"""Collapse messy round labels into three stage buckets.

Round names are inconsistent across sources ("Series E-2", "Series F Extension",
"Growth", "Tender Offer"), and 20+ distinct labels is useless as a chart colour.
Three ordered buckets answer the question a buyer actually asks: how far along is
this company?

Rule (shown on the dashboard's methodology card too):
  Seed, Series A-B       -> Early
  Series C-D             -> Growth
  Series E and later     -> Late
  Unlettered late labels -> Late   ("Growth", "Tender Offer", "Secondary" - in
                                    this dataset those only appear at late stage)
"""

import re

STAGE_ORDER = ["Early", "Growth", "Late"]

_SERIES = re.compile(r"^series\s+([a-z])\b", re.IGNORECASE)
_LATE_LABELS = {"growth", "tender offer", "secondary", "pre-ipo"}


def stage_bucket(round_stage: str | None) -> str | None:
    """Map a raw round label to Early / Growth / Late, or None if we can't tell."""
    if not round_stage:
        return None
    label = round_stage.strip()
    lowered = label.lower()

    if lowered.startswith(("seed", "pre-seed", "angel")):
        return "Early"
    if lowered in _LATE_LABELS:
        return "Late"

    match = _SERIES.match(label)
    if not match:
        return None
    letter = match.group(1).upper()
    if letter in "AB":
        return "Early"
    if letter in "CD":
        return "Growth"
    return "Late"
