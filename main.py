"""Auto-QA — orchestrateur Cron.

Usage :
    python main.py            # une passe (cible Cron)
    python main.py --loop     # boucle continue (debug)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from agent_runner import AgentResult, run_qa_for_deal
from data_manager import SupabaseManager, next_test_at
from mail_manager import MailManager
from vision_qa import analyze_checkout_screenshot

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("auto-qa")


async def process_deal(
    deal: dict,
    db: SupabaseManager,
    mail: MailManager,
) -> None:
    deal_id = deal["id"]
    name = deal.get("nom_partenaire") or "?"
    target_url = deal.get("url_partenaire_1") or deal.get("url_partenaire_2")
    if not target_url:
        log.warning("[%s] no target URL — A_VERIFIER_MANUELLEMENT", name)
        db.update_deal_result(
            deal_id, "A_VERIFIER_MANUELLEMENT", False,
            "URL partenaire absente",
            next_test_at(deal.get("tier")),
        )
        return

    promo = deal.get("code_promo_1") or deal.get("code_promo_2") or ""
    expected = deal.get("offre_detectee") or deal.get("titre_offre") or ""
    alias = mail.generate_alias(name)
    log.info("[%s] alias=%s tier=%s promo=%r", name, alias, deal.get("tier"), promo)

    result: AgentResult = await run_qa_for_deal(
        target_url=target_url,
        email_alias=alias,
        promo_code=promo,
        expected_offer=expected,
    )

    if result.needs_otp:
        log.info("[%s] OTP requested — fetching from IMAP", name)
        otp = await mail.wait_for_verification(alias, timeout=90)
        if not otp:
            db.update_deal_result(
                deal_id, "A_VERIFIER_MANUELLEMENT", False,
                "Email de vérification jamais reçu",
                next_test_at(deal.get("tier")),
            )
            return
        result = await run_qa_for_deal(
            target_url=target_url,
            email_alias=alias,
            promo_code=promo,
            expected_offer=expected,
            prefilled_otp=otp,
            reuse_session_url=result.session_url,
        )

    next_test = next_test_at(deal.get("tier"))

    if result.untestable:
        db.update_deal_result(
            deal_id, "A_VERIFIER_MANUELLEMENT", False,
            f"[UNTESTABLE_WALL] {result.raw_output[:500]}",
            next_test,
        )
        return
    if result.requires_paid:
        db.update_deal_result(
            deal_id, "INVALIDE_CODE_EXPIRE", False,
            "REQUIRE_PAID_SUBSCRIPTION — pas de free trial à 1 USD",
            next_test,
        )
        return
    if not result.success or not result.screenshot_b64:
        db.update_deal_result(
            deal_id, "INVALIDE_LIEN_CASSE", False,
            f"Agent n'a pas atteint le checkout : {result.raw_output[:300]}",
            next_test,
        )
        return

    verdict = analyze_checkout_screenshot(result.screenshot_b64, expected)
    statut = verdict.get("status", "INVALIDE_CODE_EXPIRE")
    is_valid = statut == "VALIDE"
    db.update_deal_result(
        deal_id,
        statut,
        is_valid,
        verdict.get("reason", ""),
        next_test,
    )
    log.info("[%s] => %s (%s)", name, statut, verdict.get("reason", "")[:120])


async def run_batch() -> int:
    db = SupabaseManager()
    mail = MailManager()
    batch_size = int(os.getenv("CRON_BATCH_SIZE", "20"))
    concurrency = max(1, int(os.getenv("CRON_CONCURRENCY", "1")))

    deals = db.fetch_due_deals(limit=batch_size)
    if not deals:
        log.info("Aucun deal à tester pour cette passe.")
        return 0

    log.info("%d deals à tester (concurrence=%d)", len(deals), concurrency)
    sem = asyncio.Semaphore(concurrency)

    async def guarded(deal: dict) -> None:
        async with sem:
            try:
                await process_deal(deal, db, mail)
            except Exception:
                log.exception(
                    "Crash sur deal %s — on continue",
                    deal.get("nom_partenaire"),
                )

    await asyncio.gather(*(guarded(d) for d in deals))
    return len(deals)


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-QA Web Agent — cron")
    parser.add_argument("--loop", action="store_true",
                        help="Boucle indéfiniment (debug)")
    parser.add_argument("--sleep", type=int, default=60,
                        help="Secondes entre deux passes en mode --loop")
    args = parser.parse_args()

    if not args.loop:
        asyncio.run(run_batch())
        return 0

    async def loop_forever() -> None:
        while True:
            try:
                await run_batch()
            except Exception:
                log.exception("Batch crashé")
            await asyncio.sleep(args.sleep)

    asyncio.run(loop_forever())
    return 0


if __name__ == "__main__":
    sys.exit(main())
