"""Private Market Valuations - entry point.

Built for a secondary-market buyer: which private companies are worth the most,
how fast are they compounding, and which marks are stale or nearing an exit?
Each page lives in app/views/; this file only sets up the shell and navigation.
"""

import sys
from pathlib import Path

# Make the pcd package importable without PYTHONPATH=src. Locally that env var
# works, but Streamlit Community Cloud just runs this file, so set it here.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st  # noqa: E402
import ui  # noqa: E402

st.set_page_config(
    page_title="Private Market Valuations", layout="wide", page_icon=":material/monitoring:"
)
ui.inject_css()

pages = [
    st.Page(
        "views/overview.py", title="Market overview", icon=":material/monitoring:", default=True
    ),
    st.Page(
        "views/company.py",
        title="Company lineage",
        icon=":material/account_tree:",
        url_path="company",
    ),
    st.Page(
        "views/rounds.py",
        title="Rounds & repricing",
        icon=":material/swap_vert:",
        url_path="rounds",
    ),
    st.Page(
        "views/pipeline.py",
        title="Pipeline & data quality",
        icon=":material/fact_check:",
        url_path="pipeline",
    ),
]
st.navigation(pages, position="top").run()
