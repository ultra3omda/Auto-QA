"""Visual QA — Claude vision verifies the discount applied at checkout."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
_PROMPT_TPL = (
    "Tu es un agent QA. L'offre attendue est : {expected_offer}.\n\n"
    "Regarde ce panier de paiement et juge :\n"
    "  1. Le code promo a-t-il bien appliqué la réduction attendue ?\n"
    "  2. Y a-t-il un message d'erreur rouge ou la mention "
    "'code invalide / expiré / non valable' ?\n"
    "  3. Le total correspond-il à l'offre attendue ?\n\n"
    "Réponds UNIQUEMENT en JSON strict, sans markdown, exactement :\n"
    '{{"status": "VALIDE" | "INVALIDE_CODE_EXPIRE", "reason": "explication courte"}}'
)
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def analyze_checkout_screenshot(
    screenshot_base64: str,
    expected_offer: str,
) -> dict[str, str]:
    """Return a verdict dict: {'status': ..., 'reason': ...}."""
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=_MODEL,
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": screenshot_base64,
                    },
                },
                {
                    "type": "text",
                    "text": _PROMPT_TPL.format(
                        expected_offer=expected_offer or "(non spécifiée)"
                    ),
                },
            ],
        }],
    )

    text = "".join(
        block.text for block in response.content
        if getattr(block, "type", None) == "text"
    ).strip()

    parsed = _safe_parse(text)
    status = parsed.get("status", "INVALIDE_CODE_EXPIRE")
    if status not in {"VALIDE", "INVALIDE_CODE_EXPIRE"}:
        status = "INVALIDE_CODE_EXPIRE"
    return {"status": status, "reason": str(parsed.get("reason", ""))[:1000]}


def _safe_parse(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        match = _JSON_BLOCK_RE.search(text)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    log.warning("Vision QA returned non-JSON: %r", text[:200])
    return {"status": "INVALIDE_CODE_EXPIRE", "reason": "Vision response unparsable"}
