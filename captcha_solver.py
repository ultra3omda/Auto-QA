"""CapSolver integration — solve hCaptcha / reCAPTCHA v2 transparently.

Pattern d'usage : enregistrer `solve_captcha_on_page` comme step-callback de
l'Agent browser-use. À chaque step, on inspecte la page courante; si un
hCaptcha ou reCAPTCHA est détecté, on récupère sa site-key, on appelle
l'API CapSolver, on injecte le token dans la page, et on déclenche les
événements DOM nécessaires pour que le bouton submit se débloque.

Variables d'environnement :
    CAPSOLVER_API_KEY    obligatoire pour solver les captchas
    CAPSOLVER_TIMEOUT    optionnel, secondes max d'attente (défaut 120)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)

CAPSOLVER_API = "https://api.capsolver.com"
DEFAULT_TIMEOUT = int(os.getenv("CAPSOLVER_TIMEOUT", "120"))


# JS d'inspection de la page : retourne (site_key, type) ou (None, None).
# Couvre les patterns canoniques pour hCaptcha et reCAPTCHA v2.
_DETECT_JS = """
() => {
  // hCaptcha — plusieurs façons d'incarner le widget.
  const hc1 = document.querySelector('[data-sitekey].h-captcha');
  if (hc1) return [hc1.dataset.sitekey, 'hcaptcha'];

  const hc2 = document.querySelector('iframe[src*="hcaptcha.com"][src*="sitekey="]');
  if (hc2) {
    const m = hc2.src.match(/sitekey=([^&]+)/);
    if (m) return [m[1], 'hcaptcha'];
  }

  // reCAPTCHA v2 — div .g-recaptcha[data-sitekey] ou iframe k=...
  const rc1 = document.querySelector('.g-recaptcha[data-sitekey]');
  if (rc1) return [rc1.dataset.sitekey, 'recaptcha'];

  const rc2 = document.querySelector('iframe[src*="recaptcha"][src*="k="]');
  if (rc2) {
    const m = rc2.src.match(/[?&]k=([^&]+)/);
    if (m) return [m[1], 'recaptcha'];
  }

  return [null, null];
}
"""


async def solve_captcha_on_page(page) -> bool:
    """Detect a captcha on `page` and solve it via CapSolver.

    Returns True if a captcha was found AND solved.
    Returns False if no captcha was found, or solving failed.
    """
    api_key = os.environ.get("CAPSOLVER_API_KEY", "").strip()
    if not api_key or api_key.startswith("A_REMPLIR"):
        return False  # silencieux : pas de clé, on ne fait rien

    try:
        site_key, kind = await page.evaluate(_DETECT_JS)
    except Exception as exc:
        log.debug("Captcha detect failed: %s", exc)
        return False

    if not site_key:
        return False

    page_url = page.url
    log.info("Captcha détecté sur %s : %s site_key=%s", page_url, kind, site_key[:12])

    token = await _solve_via_capsolver(api_key, kind, page_url, site_key)
    if not token:
        log.warning("CapSolver n'a pas pu résoudre le captcha %s", kind)
        return False

    try:
        await _inject_token(page, kind, token)
    except Exception as exc:
        log.warning("Token reçu mais injection échouée: %s", exc)
        return False

    log.info("Captcha %s résolu et injecté", kind)
    return True


# ---------------------------------------------------------------------------
# CapSolver API — createTask + getTaskResult polling
# ---------------------------------------------------------------------------

async def _solve_via_capsolver(
    api_key: str,
    kind: str,
    page_url: str,
    site_key: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[str]:
    task_type = (
        "HCaptchaTaskProxyless"
        if kind == "hcaptcha"
        else "ReCaptchaV2TaskProxyless"
    )
    payload_create = {
        "clientKey": api_key,
        "task": {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(f"{CAPSOLVER_API}/createTask", json=payload_create)
            data = resp.json()
        except Exception as exc:
            log.warning("CapSolver createTask network error: %s", exc)
            return None

        if data.get("errorId"):
            log.warning("CapSolver createTask: %s — %s",
                        data.get("errorCode"), data.get("errorDescription"))
            return None

        task_id = data.get("taskId")
        if not task_id:
            log.warning("CapSolver: pas de taskId dans la réponse: %s", data)
            return None

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(3)
            try:
                r = await client.post(
                    f"{CAPSOLVER_API}/getTaskResult",
                    json={"clientKey": api_key, "taskId": task_id},
                )
                d = r.json()
            except Exception as exc:
                log.debug("CapSolver poll network error: %s", exc)
                continue

            status = d.get("status")
            if status == "ready":
                solution = d.get("solution") or {}
                return (
                    solution.get("gRecaptchaResponse")
                    or solution.get("token")
                    or solution.get("captchaResponse")
                )
            if d.get("errorId"):
                log.warning("CapSolver poll error: %s", d.get("errorDescription"))
                return None
        log.warning("CapSolver timeout après %ds", timeout)
        return None


# ---------------------------------------------------------------------------
# Token injection — set the response field + dispatch events + invoke callback
# ---------------------------------------------------------------------------

_INJECT_JS = """
(payload) => {
  const { token, fieldName } = payload;
  const set = (el) => {
    if (!el) return;
    el.value = token;
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  document.querySelectorAll(`[name="${fieldName}"], textarea#${fieldName}`)
    .forEach(set);
  // hCaptcha callback hook si présent
  if (typeof window.hcaptcha === 'object' && window.hcaptcha) {
    try { window.hcaptcha.execute && window.hcaptcha.execute(); } catch (_) {}
  }
  if (typeof window.onloadCallback === 'function') {
    try { window.onloadCallback(token); } catch (_) {}
  }
  return true;
}
"""


async def _inject_token(page, kind: str, token: str) -> None:
    field_name = "h-captcha-response" if kind == "hcaptcha" else "g-recaptcha-response"
    await page.evaluate(_INJECT_JS, {"token": token, "fieldName": field_name})
