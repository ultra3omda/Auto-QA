"""Browser-use agent on Browserbase with persona, VCC rule, edge-case bail.

Architecture
------------
* A Browserbase session is provisioned (residential proxy + captcha bypass).
* `browser-use` drives a Playwright browser through the Browserbase WSS endpoint.
* Claude Sonnet (configurable via `ANTHROPIC_MODEL`) is the cognitive LLM.
* A hard system-prompt encodes the Fabien Tougai persona, VCC rule and
  the edge-case bail-out signals consumed by the orchestrator.

Orchestrator contract (see `main.py`)
-------------------------------------
The agent's final text output is inspected for sentinels :

* ``[UNTESTABLE_WALL] ...``     -> A_VERIFIER_MANUELLEMENT
* ``REQUIRE_PAID_SUBSCRIPTION`` -> INVALIDE_CODE_EXPIRE (no free trial)
* ``WAITING_FOR_EMAIL_CODE``    -> orchestrator fetches OTP and reruns
                                   with `prefilled_otp` + `reuse_session_url`.

Otherwise the last screenshot recorded in the agent's history is returned.
"""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from browser_use import Agent, BrowserSession, ChatAnthropic
from browserbase import Browserbase
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hard system prompt — persona + payment rule + edge cases
# ---------------------------------------------------------------------------

PERSONA = """\
PERSONA OBLIGATOIRE — utilise ces données pour TOUS les formulaires
(inscription, lead-gen, onboarding B2B). Ne sors jamais de ce profil.

  Prénom / Nom : Fabien Tougai
  Email        : (l'alias unique fourni dans la mission)
  Téléphone    : +33465840081
  Adresse      : 69 rue du rouet, 13008 Marseille, France
  Entreprise   : Purpi  (https://purpi.app)  — taille 1-10
  Secteur      : Technology / Software / Internet
  Poste        : Business developer  — département Sales
  Use case     : Testing / Internal Use
  Mot de passe universel : ClubPrivil3ges2026!+
"""

PAYMENT_RULE_TPL = """\
RÈGLE CARTE BANCAIRE — STRICTE :
  Numéro     : {number}
  Expiration : {month}/{year}
  CVC        : {cvc}

Cette carte virtuelle (plafond ≈ 1 USD) sert UNIQUEMENT à débloquer un Free Trial.
Si le montant "Due Today" / "À payer aujourd'hui" est strictement supérieur à
1.00 (EUR ou USD), ABANDONNE et retourne EXACTEMENT le texte :
"REQUIRE_PAID_SUBSCRIPTION"
sans rien d'autre.
"""

EDGE_RULES = """\
EDGE CASES — déclenche un abandon immédiat avec le préfixe
"[UNTESTABLE_WALL]" suivi d'une explication courte si tu rencontres :
  - un appel commercial obligatoire (Book a demo / Talk to sales)
  - un upload obligatoire d'une pièce d'identité officielle
  - une application disponible UNIQUEMENT sur mobile (iOS / Android)
  - un blocage géographique strict
  - aucun free-trial visible (paywall sec)
"""

OBJECTIVE_TPL = """\
MISSION QA — un seul partenaire :

  1. Va sur l'URL : {target_url}
  2. Crée un compte avec l'email exact : {email_alias}
  3. Si une vérification email est requise (OTP ou lien d'activation),
     interromps-toi en émettant EXACTEMENT cette ligne :
       WAITING_FOR_EMAIL_CODE: {email_alias}
     L'orchestrateur te répondra avec le code ou le lien.
  4. Continue jusqu'à la page Billing / Checkout / Paiement.
  5. Saisis le code promo : "{promo_code}".
     Offre attendue : {expected_offer}
  6. Si un Free Trial est proposé et le total dû est ≤ 1.00, saisis la carte
     bancaire selon la RÈGLE CARTE BANCAIRE.
  7. Reste sur la page de checkout finale. Prends un screenshot lisible
     du panier total + ligne de réduction.

Ne fais pas autre chose. Une seule mission, un seul partenaire.
"""

RESUME_TPL = (
    "Le code reçu par email pour {email_alias} est : {value}. "
    "Reprends la mission immédiatement à l'étape de vérification, "
    "puis poursuis jusqu'au checkout."
)


@dataclass
class AgentResult:
    success: bool
    screenshot_b64: Optional[str]
    raw_output: str
    untestable: bool = False
    requires_paid: bool = False
    needs_otp: bool = False
    session_url: Optional[str] = None


def _build_system_prompt() -> str:
    cc_rule = PAYMENT_RULE_TPL.format(
        number=os.environ.get("QA_CC_NUMBER", ""),
        month=os.environ.get("QA_CC_EXP_MONTH", ""),
        year=os.environ.get("QA_CC_EXP_YEAR", ""),
        cvc=os.environ.get("QA_CC_CVC", ""),
    )
    return "\n\n".join([PERSONA, cc_rule, EDGE_RULES])


def _create_browserbase_session() -> str:
    """Provision a Browserbase session and return the WSS connect URL."""
    bb = Browserbase(api_key=os.environ["BROWSERBASE_API_KEY"])
    session = bb.sessions.create(
        project_id=os.environ["BROWSERBASE_PROJECT_ID"],
    )
    log.info("Browserbase session opened: %s", getattr(session, "id", "?"))
    return session.connect_url


async def run_qa_for_deal(
    target_url: str,
    email_alias: str,
    promo_code: str,
    expected_offer: str,
    prefilled_otp: Optional[str] = None,
    reuse_session_url: Optional[str] = None,
    max_steps: Optional[int] = None,
) -> AgentResult:
    """Run a full QA pass for a single deal.

    Pass `prefilled_otp` + `reuse_session_url` to resume after a
    `WAITING_FOR_EMAIL_CODE` checkpoint.
    """
    connect_url = reuse_session_url or _create_browserbase_session()
    session = BrowserSession(cdp_url=connect_url)

    llm = ChatAnthropic(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        api_key=os.environ["ANTHROPIC_API_KEY"],
        temperature=0,
    )

    objective = OBJECTIVE_TPL.format(
        target_url=target_url,
        email_alias=email_alias,
        promo_code=promo_code or "(aucun code, vérifie l'application automatique)",
        expected_offer=expected_offer or "(non spécifiée — déduis-la du site)",
    )
    if prefilled_otp:
        objective += "\n\n" + RESUME_TPL.format(
            email_alias=email_alias,
            value=prefilled_otp,
        )

    agent = Agent(
        task=objective,
        llm=llm,
        browser_session=session,
        extend_system_message=_build_system_prompt(),
    )

    try:
        history = await agent.run(
            max_steps=max_steps or int(os.getenv("AGENT_MAX_STEPS", "30"))
        )
    except Exception as exc:
        log.exception("Agent crashed for %s", target_url)
        return AgentResult(
            success=False,
            screenshot_b64=None,
            raw_output=f"AGENT_CRASH: {exc}",
            session_url=connect_url,
        )
    finally:
        try:
            await session.stop()
        except Exception:
            pass

    text = _coerce_to_text(history)
    if "[UNTESTABLE_WALL]" in text:
        return AgentResult(False, None, text, untestable=True, session_url=connect_url)
    if "REQUIRE_PAID_SUBSCRIPTION" in text:
        return AgentResult(False, None, text, requires_paid=True, session_url=connect_url)
    if "WAITING_FOR_EMAIL_CODE" in text and not prefilled_otp:
        return AgentResult(False, None, text, needs_otp=True, session_url=connect_url)

    screenshot = _extract_screenshot(history)
    return AgentResult(
        success=bool(screenshot),
        screenshot_b64=screenshot,
        raw_output=text,
        session_url=connect_url,
    )


def _coerce_to_text(history: Any) -> str:
    try:
        out = history.final_result()
        if out:
            return out
    except Exception:
        pass
    return str(history)


def _extract_screenshot(history: Any) -> Optional[str]:
    """Return the most recent screenshot as base64 PNG, or None."""
    try:
        items = getattr(history, "history", None) or []
        for item in reversed(items):
            shot = getattr(item, "screenshot", None)
            if not shot:
                continue
            if isinstance(shot, bytes):
                return base64.b64encode(shot).decode("ascii")
            return str(shot)
    except Exception:
        log.debug("Could not extract screenshot from history", exc_info=True)
    return None
