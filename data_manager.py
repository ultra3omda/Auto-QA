"""Supabase client + CSV importer with Smart QA scoring."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Smart QA — popularity scoring
# ---------------------------------------------------------------------------

KNOWN_BRANDS_TIER_1: dict[str, int] = {
    "google": 100, "stripe": 100, "aws": 100, "amazon web services": 100,
    "microsoft": 95, "azure": 95, "github": 95, "gitlab": 90,
    "zoom": 95, "slack": 95, "notion": 90, "figma": 90, "atlassian": 90,
    "openai": 100, "anthropic": 100, "claude": 95,
    "hubspot": 85, "salesforce": 90, "shopify": 90, "intercom": 80,
    "mailchimp": 80, "sendgrid": 80, "twilio": 85, "cloudflare": 90,
    "datadog": 85, "asana": 80, "trello": 80, "linear": 80, "monday": 80,
    "calendly": 80, "typeform": 80, "airtable": 85, "zapier": 85,
    "canva": 85, "miro": 80, "loom": 80, "dropbox": 85, "adobe": 85,
}

KNOWN_BRANDS_TIER_2: dict[str, int] = {
    "freshworks": 60, "pipedrive": 60, "freshdesk": 55, "zoho": 55,
    "clickup": 60, "wrike": 50, "basecamp": 55, "deel": 65, "remote": 60,
    "lemlist": 55, "apollo": 55, "phantombuster": 50, "sendinblue": 55,
    "brevo": 55, "ahrefs": 60, "semrush": 60, "buffer": 55, "hootsuite": 55,
    "tally": 50, "softr": 50, "webflow": 65, "framer": 60, "wix": 55,
    "squarespace": 55, "wordpress": 60, "elementor": 50, "make": 60,
    "n8n": 55, "retool": 55, "supabase": 70, "vercel": 70, "netlify": 65,
    "render": 50, "fly.io": 50, "linode": 50, "digitalocean": 60,
    "mongodb": 65, "redis": 60, "snowflake": 65, "bigquery": 65,
    "tableau": 65, "metabase": 50, "looker": 60, "mixpanel": 55,
    "amplitude": 55, "segment": 60, "posthog": 55, "sentry": 60,
    "rollbar": 50, "papertrail": 45, "grafana": 50, "newrelic": 60,
    "okta": 65, "auth0": 65, "1password": 65, "lastpass": 60, "bitwarden": 55,
    "nordvpn": 55, "expressvpn": 55, "surfshark": 50,
}


def smart_qa_score(nom_partenaire: str) -> tuple[int, str]:
    """Return (popularite_score, tier) used by the Smart QA cadence.

    Tier maps to retest frequency in `next_test_at`:
        TIER_1 (>=80) -> +48h, TIER_2 (>=40) -> +15j, TIER_3 -> +30j.
    """
    slug = (nom_partenaire or "").strip().lower()
    if not slug:
        return 0, "TIER_3"

    for brand, score in KNOWN_BRANDS_TIER_1.items():
        if brand in slug:
            return score, "TIER_1"
    for brand, score in KNOWN_BRANDS_TIER_2.items():
        if brand in slug:
            return score, "TIER_2"

    return (20 if len(slug) <= 12 else 10), "TIER_3"


# ---------------------------------------------------------------------------
# CSV -> DB column mapping
# ---------------------------------------------------------------------------

# Mapping aligné sur les en-têtes du fichier Freelance Stack réel
# (data/Liste des deals Freelance Stack .xlsx — feuille "Deals", 1 791 lignes).
DEALS_COLUMN_MAP: dict[str, str] = {
    "Nom partenaire":              "nom_partenaire",
    "URL fiche Freelance Stack":   "url_fiche_freelance",
    "Titre / Offre":               "titre_offre",
    "Offre détectée (texte deal)": "offre_detectee",
    "Mécanisme":                   "mecanisme",
    "Code promo #1":               "code_promo_1",
    "Code promo #2":               "code_promo_2",
    "URL partenaire #1":           "url_partenaire_1",
    "URL partenaire #2":           "url_partenaire_2",
    "URL partenaire #3":           "url_partenaire_3",
}
# Backward-compat alias (le nom historique du spec).
CSV_COLUMN_MAP = DEALS_COLUMN_MAP


# ---------------------------------------------------------------------------
# Smart-QA cadence
# ---------------------------------------------------------------------------

TIER_TO_NEXT_TEST: dict[str, timedelta] = {
    "TIER_1": timedelta(hours=48),
    "TIER_2": timedelta(days=15),
    "TIER_3": timedelta(days=30),
}


def next_test_at(tier: str | None) -> datetime:
    delta = TIER_TO_NEXT_TEST.get(tier or "TIER_3", TIER_TO_NEXT_TEST["TIER_3"])
    return datetime.now(timezone.utc) + delta


# ---------------------------------------------------------------------------
# Supabase manager
# ---------------------------------------------------------------------------

class SupabaseManager:
    """Thin wrapper around supabase-py with QA-specific helpers."""

    TABLE_NAME = "deals_saas"

    def __init__(self) -> None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        self.client: Client = create_client(url, key)

    @property
    def table(self):
        return self.client.table(self.TABLE_NAME)

    def fetch_due_deals(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return deals that need testing now, highest popularity first."""
        now = datetime.now(timezone.utc).isoformat()
        return (
            self.table
                .select("*")
                .or_(f"statut_test.eq.EN_ATTENTE,date_prochain_test.lte.{now}")
                .order("popularite_score", desc=True)
                .limit(limit)
                .execute()
                .data
        ) or []

    def update_deal_result(
        self,
        deal_id: str,
        statut: str,
        est_actif: bool,
        raison: str | None,
        next_test: datetime,
    ) -> None:
        self.table.update({
            "statut_test": statut,
            "est_actif": est_actif,
            "raison_echec": raison,
            "date_dernier_test": datetime.now(timezone.utc).isoformat(),
            "date_prochain_test": next_test.isoformat(),
        }).eq("id", deal_id).execute()

    def insert_deals(self, rows: list[dict[str, Any]], chunk: int = 500) -> int:
        n = 0
        for i in range(0, len(rows), chunk):
            batch = rows[i : i + chunk]
            self.table.insert(batch).execute()
            n += len(batch)
        return n


# ---------------------------------------------------------------------------
# CSV importer
# ---------------------------------------------------------------------------

def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _read_deals_file(path: Path) -> "pd.DataFrame":
    """Read .xlsx / .xls / .csv into a string-typed DataFrame."""
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def build_deal_rows(filepath: str | Path) -> tuple[list[dict[str, Any]], int]:
    """Parse the deals file and return (rows_to_insert, skipped_count).

    No DB call. Used both by `import_deals_file_to_supabase` and by tests /
    dry-run scripts that need to validate the column mapping.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(path)

    df = _read_deals_file(path)
    log.info("Loaded %d rows from %s", len(df), path)

    rows: list[dict[str, Any]] = []
    skipped = 0
    for _, raw in df.iterrows():
        record: dict[str, Any] = {}
        for src_col, db_col in DEALS_COLUMN_MAP.items():
            if src_col in raw.index:
                record[db_col] = _normalize(raw[src_col])

        nom = record.get("nom_partenaire")
        if not nom or not record.get("url_partenaire_1"):
            skipped += 1
            continue

        score, tier = smart_qa_score(nom)
        record.update({
            "popularite_score": score,
            "tier": tier,
            "statut_test": "EN_ATTENTE",
            "est_actif": False,
            "date_prochain_test": datetime.now(timezone.utc).isoformat(),
        })
        rows.append(record)
    return rows, skipped


def import_deals_file_to_supabase(filepath: str | Path) -> int:
    """Read partner deals file (.xlsx / .csv), compute Smart-QA, insert in Supabase.

    Returns the number of rows inserted.
    """
    rows, skipped = build_deal_rows(filepath)
    log.info("Prepared %d rows (%d skipped — missing name or URL)", len(rows), skipped)
    return SupabaseManager().insert_deals(rows)


# Backward-compat alias (le nom historique du spec).
import_csv_to_supabase = import_deals_file_to_supabase
