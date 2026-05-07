"""Tableau de bord temps-réel pour le cron Auto-QA — rafraîchit toutes les 5 s.

Usage :
    python scripts/monitor.py            # plein écran (alt-screen)
    python scripts/monitor.py --plain    # défilement standard, scriptable
    python scripts/monitor.py --once     # une seule passe puis quitte
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console, Group  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.table import Table  # noqa: E402

from data_manager import SupabaseManager  # noqa: E402

REFRESH_SECONDS = 5
QUERY_LIMIT = 2500


def fetch_state(db: SupabaseManager) -> list[dict]:
    return (
        db.table
        .select(
            "statut_test, tier, est_actif, raison_echec, "
            "date_dernier_test, date_prochain_test, nom_partenaire"
        )
        .limit(QUERY_LIMIT)
        .execute()
        .data
    ) or []


def render(rows: list[dict]) -> Group:
    total = len(rows)
    actifs = sum(1 for r in rows if r.get("est_actif"))
    by_statut: Counter[str] = Counter(r.get("statut_test") or "(null)" for r in rows)
    by_tier: Counter[str] = Counter(r.get("tier") or "(null)" for r in rows)

    header = Table(
        title=f"Auto-QA  ·  {total} deals  ·  {actifs} actifs  ·  "
              f"{datetime.now().strftime('%H:%M:%S')}",
        title_style="bold cyan",
        show_header=False,
        expand=True,
    )
    header.add_column()

    statut_t = Table(title="Statut", expand=True)
    statut_t.add_column("statut", style="cyan")
    statut_t.add_column("compte", justify="right")
    for s, c in by_statut.most_common():
        statut_t.add_row(s, str(c))

    tier_t = Table(title="Tier", expand=True)
    tier_t.add_column("tier", style="green")
    tier_t.add_column("compte", justify="right")
    for s, c in sorted(by_tier.items()):
        tier_t.add_row(s, str(c))

    tested = [r for r in rows if r.get("date_dernier_test")]
    tested.sort(key=lambda r: r["date_dernier_test"] or "", reverse=True)
    recent_t = Table(title="20 derniers tests", expand=True)
    recent_t.add_column("date_dernier_test", style="dim", width=20)
    recent_t.add_column("partenaire", style="bold", max_width=28)
    recent_t.add_column("tier", width=7)
    recent_t.add_column("statut", width=22)
    recent_t.add_column("raison", max_width=80)
    for r in tested[:20]:
        statut = r.get("statut_test") or "?"
        style = {
            "VALIDE": "green",
            "INVALIDE_CODE_EXPIRE": "red",
            "INVALIDE_LIEN_CASSE": "red",
            "A_VERIFIER_MANUELLEMENT": "yellow",
            "EN_ATTENTE": "dim",
        }.get(statut, "")
        recent_t.add_row(
            (r.get("date_dernier_test") or "")[:19].replace("T", " "),
            r.get("nom_partenaire") or "?",
            r.get("tier") or "?",
            f"[{style}]{statut}[/{style}]" if style else statut,
            (r.get("raison_echec") or "")[:80],
        )
    return Group(header, statut_t, tier_t, recent_t)


def main() -> int:
    p = argparse.ArgumentParser(description="Auto-QA real-time monitor")
    p.add_argument("--plain", action="store_true",
                   help="désactive le mode plein écran")
    p.add_argument("--once", action="store_true",
                   help="affiche une fois et quitte")
    args = p.parse_args()

    console = Console()
    db = SupabaseManager()

    if args.once:
        console.print(render(fetch_state(db)))
        return 0

    with Live(
        refresh_per_second=0.5,
        console=console,
        screen=not args.plain,
    ) as live:
        try:
            while True:
                live.update(render(fetch_state(db)))
                time.sleep(REFRESH_SECONDS)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
