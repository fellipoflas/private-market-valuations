"""Dashboard tests: pure helpers always run; page smoke tests need the local database."""

import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

import ui  # noqa: E402


def _db_available() -> bool:
    try:
        from pcd.db import ping

        ping()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(
    not _db_available(), reason="needs DATABASE_URL pointing at a loaded db"
)


# --- pure helpers ----------------------------------------------------------
@pytest.mark.parametrize(
    "value, text",
    [(965e9, "$965B"), (61e9, "$61.0B"), (2.75e12, "$2.75T"), (450e6, "$450M"), (None, "—")],
)
def test_money(value, text):
    assert ui.money(value) == text


@pytest.mark.parametrize(
    "value, text", [(0.43, "+43%"), (-0.2, "-20%"), (4.3, "5.3×"), (1.0, "2.0×")]
)
def test_change_switches_to_multiples_once_doubled(value, text):
    assert ui.change(value) == text


def test_spread_labels_keeps_order_and_min_gap():
    placed = ui.spread_labels([2.98, 2.93, 2.27, 2.20, 2.06], min_gap=0.1)
    ordered = sorted(placed)
    assert all(b - a >= 0.1 - 1e-9 for a, b in zip(ordered, ordered[1:], strict=False))
    # relative order of the original values is preserved
    assert sorted(range(5), key=lambda i: placed[i]) == sorted(
        range(5), key=lambda i: [2.98, 2.93, 2.27, 2.20, 2.06][i]
    )


# --- database-backed -------------------------------------------------------
@needs_db
def test_one_year_change_matches_point_in_time_lookup():
    """leaderboard.change_1y must equal (current mark / mark valid 365 days ago) - 1."""
    from datetime import date, timedelta

    from pcd.db import fetch_analysis, fetch_one

    lb = fetch_analysis("leaderboard").set_index("company_id")
    target = date.today() - timedelta(days=365)
    checked = 0
    for company_id, row in lb.dropna(subset=["valuation_1y_ago"]).iterrows():
        then = fetch_one(
            """SELECT latest_valuation_usd AS v FROM dim_company
               WHERE company_id = %(c)s AND valid_from <= %(d)s AND valid_to >= %(d)s""",
            {"c": company_id, "d": target},
        )
        assert then["v"] == row.valuation_1y_ago
        assert float(row.change_1y) == pytest.approx(
            float(row.latest_valuation_usd) / float(then["v"]) - 1
        )
        checked += 1
    assert checked > 0


@needs_db
@pytest.mark.parametrize(
    "page", ["views/overview.py", "views/company.py", "views/rounds.py", "views/pipeline.py"]
)
def test_page_renders_without_exceptions(page):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=90)
    at.switch_page(page)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) >= 4


def test_spread_labels_never_pushes_past_the_top():
    """Ten labels bunched near the top must be stacked downward, not off the chart."""
    placed = ui.spread_labels([2.99, 2.98, 2.97, 2.96, 2.95], min_gap=0.1, hi=3.0)
    assert max(placed) <= 3.0 + 1e-9
    ordered = sorted(placed)
    assert all(b - a >= 0.1 - 1e-9 for a, b in zip(ordered, ordered[1:], strict=False))
