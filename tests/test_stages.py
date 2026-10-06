import pytest

from pcd.transform.stages import stage_bucket


@pytest.mark.parametrize(
    "label, bucket",
    [
        ("Seed", "Early"),
        ("Series A", "Early"),
        ("Series A Extension", "Early"),
        ("Series B", "Early"),
        ("Series C", "Growth"),
        ("Series D-2", "Growth"),
        ("Series E", "Late"),
        ("Series F Extension", "Late"),
        ("Series L", "Late"),
        ("Growth", "Late"),
        ("Tender Offer", "Late"),
        ("Secondary", "Late"),
        ("series c", "Growth"),
    ],
)
def test_known_labels(label, bucket):
    assert stage_bucket(label) == bucket


@pytest.mark.parametrize("label", [None, "", "Convertible note", "Seriously big round"])
def test_unknown_labels_are_none_not_guessed(label):
    assert stage_bucket(label) is None


def test_every_seed_label_has_a_bucket():
    """If someone adds a round with a new label, this catches it before the chart drops it."""
    import csv

    from pcd.settings import SEED_CSV

    with SEED_CSV.open() as f:
        labels = {row["round_stage"] for row in csv.DictReader(f)}
    assert {lbl for lbl in labels if stage_bucket(lbl) is None} == set()
