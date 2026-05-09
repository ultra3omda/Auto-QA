"""Rapport final QA — pagine TOUS les deals (au-delà du cap 1 000 REST)
et exporte en Markdown + CSV.

Usage :
    python scripts/report.py                       # écrit auto-qa-report.md + .csv
    python scripts/report.py --md report.md        # markdown vers fichier custom
    python scripts/report.py --csv report.csv      # csv vers fichier custom
    python scripts/report.py --md - --csv -        # tout sur stdout
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_manager import SupabaseManager  # noqa: E402

PAGE_SIZE = 1000

REPORT_FIELDS = [
    "nom_partenaire", "tier", "statut_test", "est_actif",
    "popularite_score", "code_promo_1", "offre_detectee",
    "raison_echec", "date_dernier_test", "date_prochain_test",
    "url_partenaire_1",
]


def fetch_all_deals(db: SupabaseManager) -> list[dict[str, Any]]:
    """Pagination .range() pour récupérer les ~1 044 deals (default REST cap = 1000)."""
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        end = start + PAGE_SIZE - 1
        page = (
            db.table
            .select(",".join(REPORT_FIELDS))
            .order("popularite_score", desc=True)
            .range(start, end)
            .execute()
            .data
        ) or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_statut: Counter[str] = Counter(r.get("statut_test") or "(null)" for r in rows)
    by_tier: Counter[str] = Counter(r.get("tier") or "(null)" for r in rows)
    actifs = sum(1 for r in rows if r.get("est_actif"))
    tested = [r for r in rows if r.get("date_dernier_test")]
    return {
        "total": len(rows),
        "tested": len(tested),
        "untested": len(rows) - len(tested),
        "actifs": actifs,
        "by_statut": dict(by_statut),
        "by_tier": dict(by_tier),
    }


def render_markdown(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    out: list[str] = []
    out.append("# Auto-QA · Rapport final\n")
    out.append(
        f"**Total** : {summary['total']} deals · "
        f"**Testés** : {summary['tested']} · "
        f"**Actifs (promo OK)** : {summary['actifs']} · "
        f"**À tester** : {summary['untested']}\n"
    )

    out.append("\n## Répartition par statut\n")
    out.append("| Statut | Nombre |\n|---|---|")
    for s, n in sorted(summary["by_statut"].items(), key=lambda kv: -kv[1]):
        out.append(f"| `{s}` | {n} |")

    out.append("\n## Répartition par tier\n")
    out.append("| Tier | Nombre |\n|---|---|")
    for s, n in sorted(summary["by_tier"].items()):
        out.append(f"| `{s}` | {n} |")

    by_status_groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("date_dernier_test"):
            by_status_groups.setdefault(r.get("statut_test") or "?", []).append(r)

    status_order = (
        "VALIDE",
        "INVALIDE_CODE_EXPIRE",
        "INVALIDE_LIEN_CASSE",
        "A_VERIFIER_MANUELLEMENT",
    )
    for st in status_order:
        deals = by_status_groups.get(st, [])
        if not deals:
            continue
        out.append(f"\n## {st} — {len(deals)} deal(s)\n")
        out.append("| Partenaire | Tier | Promo | Offre | Raison | Testé le |")
        out.append("|---|---|---|---|---|---|")
        deals.sort(key=lambda r: r.get("date_dernier_test") or "", reverse=True)
        for r in deals:
            out.append(
                "| {nom} | {tier} | `{promo}` | {offre} | {raison} | {date} |".format(
                    nom=(r.get("nom_partenaire") or "?")[:30],
                    tier=r.get("tier") or "?",
                    promo=(r.get("code_promo_1") or "")[:25] or "—",
                    offre=(r.get("offre_detectee") or "")[:60].replace("|", "\\|"),
                    raison=(r.get("raison_echec") or "")[:80].replace("|", "\\|").replace("\n", " "),
                    date=(r.get("date_dernier_test") or "")[:19].replace("T", " "),
                )
            )

    untested = [r for r in rows if not r.get("date_dernier_test")]
    if untested:
        out.append(f"\n## EN_ATTENTE — {len(untested)} deal(s) à tester\n")
        out.append("Top 30 par popularité :\n")
        out.append("| Partenaire | Tier | Score | Promo |")
        out.append("|---|---|---|---|")
        for r in untested[:30]:
            out.append(
                "| {nom} | {tier} | {score} | `{promo}` |".format(
                    nom=(r.get("nom_partenaire") or "?")[:30],
                    tier=r.get("tier") or "?",
                    score=r.get("popularite_score") or 0,
                    promo=(r.get("code_promo_1") or "")[:25] or "—",
                )
            )

    return "\n".join(out) + "\n"


def render_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=REPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        clean = {k: (r.get(k) if r.get(k) is not None else "") for k in REPORT_FIELDS}
        writer.writerow(clean)
    return buf.getvalue()


def write_or_print(content: str, target: str | None) -> None:
    if not target:
        return
    if target == "-":
        sys.stdout.write(content)
        return
    Path(target).write_text(content, encoding="utf-8")
    print(f"  → {target} ({len(content)} chars)", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description="Auto-QA — rapport final paginé")
    p.add_argument("--md", help="chemin du rapport Markdown ('-' = stdout)")
    p.add_argument("--csv", help="chemin du rapport CSV ('-' = stdout)")
    args = p.parse_args()

    db = SupabaseManager()
    rows = fetch_all_deals(db)
    summary = build_summary(rows)

    print(
        f"Rapport : {summary['total']} deals · "
        f"{summary['tested']} testés · "
        f"{summary['actifs']} actifs · "
        f"{summary['untested']} à tester",
        file=sys.stderr,
    )
    print("Statut :", summary["by_statut"], file=sys.stderr)
    print("Tier   :", summary["by_tier"], file=sys.stderr)

    md_target = args.md or "auto-qa-report.md"
    csv_target = args.csv or "auto-qa-report.csv"

    write_or_print(render_markdown(rows, summary), md_target)
    write_or_print(render_csv(rows), csv_target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
