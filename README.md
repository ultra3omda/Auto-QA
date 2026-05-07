# Auto-QA Web Agent

Agent web autonome qui teste, à intervalles réguliers, les ~1 800 deals SaaS
du catalogue **Club Privilèges** (codes promo, free trials, liens cassés)
puis met à jour `est_actif` dans Supabase. Les offres invalides sont masquées
automatiquement de la PWA front-end.

## Architecture

```
┌──────────────┐   cron / boucle   ┌──────────────┐
│  main.py     │ ────────────────► │ Supabase     │
│ orchestrator │ ◄──────────────── │ deals_saas   │
└──────┬───────┘                   └──────────────┘
       │ pour chaque deal :
       ▼
┌─────────────────┐    OTP / lien     ┌──────────────┐
│ agent_runner.py │ ◄───────────────  │ mail_manager │
│ (browser-use    │                   │ (IMAP catch- │
│  + Browserbase) │                   │  all)        │
└──────┬──────────┘                   └──────────────┘
       │ screenshot final
       ▼
┌─────────────┐
│ vision_qa   │  (Claude Sonnet — vision)
└─────────────┘
       │ verdict JSON
       ▼
   Supabase update
```

## Stack

| Couche | Choix |
|---|---|
| Backend | Python 3.11+ |
| Agent web | `browser-use` + Playwright |
| Infra navigateur | Browserbase (proxy résidentiel + bypass captcha) |
| LLM cognitif + vision | Claude Sonnet (`ANTHROPIC_MODEL`) |
| Base de données | Supabase (PostgreSQL) |
| Mail | IMAP catch-all `assistant@clubprivileges.app` |
| Paiement | VCC Revolut (plafond ≈ 1 USD) |

## Setup

```bash
git clone https://github.com/ultra3omda/Auto-QA.git
cd Auto-QA
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env       # éditer les secrets
psql "$SUPABASE_DB_URL" -f schema.sql
```

## Variables d'environnement

Toutes définies dans `.env` (cf. `.env.example`). Les plus sensibles :

| Variable | Usage |
|---|---|
| `SUPABASE_URL` / `SUPABASE_KEY` | Connexion service-role. |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | LLM cognitif + vision. |
| `BROWSERBASE_API_KEY` / `BROWSERBASE_PROJECT_ID` | Sessions navigateur. |
| `IMAP_SERVER` / `IMAP_USER` / `IMAP_PASSWORD` | Catch-all mailbox. |
| `QA_CC_NUMBER` / `QA_CC_EXP_*` / `QA_CC_CVC` | VCC plafond 1 USD. |
| `CRON_BATCH_SIZE` | Deals par passe (défaut 20). |
| `CRON_CONCURRENCY` | Sessions navigateur en parallèle (défaut 1). |
| `AGENT_MAX_STEPS` | Pas max par mission (défaut 30). |

## Flow

1. **Import** — `python scripts/import_csv.py data/deals.csv`
   parse le CSV Freelance Stack, calcule un `popularite_score` /
   `tier` (Smart QA), insère dans `deals_saas`.
2. **Cron** — `python main.py` lit les deals dont
   `date_prochain_test <= NOW()` ou `statut_test = EN_ATTENTE`.
3. **Agent** — chaque deal reçoit un alias unique
   (`assistant+<slug>@clubprivileges.app`), l'agent simule Fabien Tougai
   jusqu'au checkout et applique le code promo.
4. **Vérification email** — si l'agent émet
   `WAITING_FOR_EMAIL_CODE: <alias>`, l'orchestrateur récupère l'OTP /
   lien d'activation par IMAP et relance l'agent.
5. **Vision QA** — le screenshot final est envoyé à Claude (vision).
   Verdict strict JSON :
   `{"status": "VALIDE" | "INVALIDE_CODE_EXPIRE", "reason": "..."}`.
6. **Mise à jour** — `est_actif`, `statut_test`, `raison_echec`,
   `date_prochain_test` selon le tier (TIER_1 +48 h, TIER_2 +15 j,
   TIER_3 +30 j).

## Sentinelles renvoyées par l'agent

| Sentinelle | Effet orchestrateur |
|---|---|
| `WAITING_FOR_EMAIL_CODE: <alias>` | Lit l'IMAP, relance avec l'OTP. |
| `REQUIRE_PAID_SUBSCRIPTION` | `INVALIDE_CODE_EXPIRE`, `est_actif=false`. |
| `[UNTESTABLE_WALL] <raison>` | `A_VERIFIER_MANUELLEMENT`, `est_actif=false`. |

## Cadence Smart QA

| Tier | Score | Cadence retest |
|---|---|---|
| TIER_1 | ≥ 80 (Google, Stripe, AWS, Notion, …) | toutes les 48 h |
| TIER_2 | ≥ 40 | tous les 15 jours |
| TIER_3 | < 40 | tous les 30 jours |

## Sécurité

- Aucune valeur sensible hardcodée — tout passe par `.env`.
- VCC plafonnée à ~1 USD : si `Due Today > 1.00`, l'agent abandonne.
- Persona unique réutilisé partout (Fabien Tougai / Purpi).
- Captchas + détection bot délégués à Browserbase.

## Layout

```
.
├── agent_runner.py     # browser-use + Browserbase
├── data_manager.py     # Supabase + Smart QA + CSV importer
├── mail_manager.py     # IMAP async catch-all
├── main.py             # orchestrateur Cron
├── vision_qa.py        # Claude vision verdict
├── schema.sql          # ENUMs + table deals_saas
├── scripts/
│   └── import_csv.py   # CLI d'import CSV
├── data/               # CSV bruts (gitignored, sauf .gitkeep)
├── requirements.txt
├── .env.example
└── .gitignore
```

## Branches

- `main` — production, branche stable.
- `develop` — intégration continue (default).

## Licence

Propriétaire — Club Privilèges.
