"""Config in one place, so nothing else has to know about env vars or file paths."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

# Paths are derived from this file's location so they work no matter where
# you run the code from (local shell, GitHub Actions, Streamlit Cloud).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = PROJECT_ROOT / "sql"
DATA_DIR = PROJECT_ROOT / "data"
SEED_CSV = DATA_DIR / "seed" / "valuations_seed.csv"
RAW_DIR = DATA_DIR / "raw"
COMPANIES_YML = PROJECT_ROOT / "config" / "companies.yml"


def get_database_url() -> str:
    """Postgres connection string. Fail loudly and helpfully if it's missing."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill in your "
            "Neon connection string (https://console.neon.tech -> Connection Details)."
        )
    return url


@dataclass(frozen=True)
class Company:
    """One tracked company, as defined in config/companies.yml."""

    id: str
    display_name: str
    sector: str | None = None      # coarse, for grouping charts (~8 values)
    subsector: str | None = None   # detailed, for labels and tooltips
    hq_country: str | None = None
    aliases: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    fp_risk: str = "low"
    status: str = "private"        # private | public | acquired
    status_note: str | None = None
    status_source_url: str | None = None
    companies_house_number: str | None = None

    @property
    def is_private(self) -> bool:
        return self.status == "private"

    @property
    def search_names(self) -> list[str]:
        """Every spelling we want to search for. Display name first."""
        return [self.display_name, *self.aliases]


def load_companies() -> list[Company]:
    """Read config/companies.yml into Company objects."""
    with COMPANIES_YML.open() as f:
        raw = yaml.safe_load(f)

    return [
        Company(
            id=entry["id"],
            display_name=entry["display_name"],
            sector=entry.get("sector"),
            subsector=entry.get("subsector"),
            hq_country=entry.get("hq_country"),
            aliases=tuple(entry.get("aliases") or ()),
            exclude_terms=tuple(entry.get("exclude_terms") or ()),
            fp_risk=entry.get("fp_risk", "low"),
            status=entry.get("status", "private"),
            status_note=entry.get("status_note"),
            status_source_url=entry.get("status_source_url"),
            companies_house_number=entry.get("companies_house_number"),
        )
        for entry in raw["companies"]
    ]


def get_company(company_id: str) -> Company:
    for company in load_companies():
        if company.id == company_id:
            return company
    raise KeyError(f"No company with id {company_id!r} in {COMPANIES_YML}")
