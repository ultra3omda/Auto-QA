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

## 🚀 Démarrer (procédure complète)

```bash
# 0. environnement (une seule fois)
source .venv/bin/activate

# 1. créer le schéma Supabase
psql "$SUPABASE_DB_URL" -f schema.sql

# 2. importer les deals (.xlsx ou .csv) — ~1 044 deals utilisables
python scripts/import_csv.py "data/Liste des deals Freelance Stack .xlsx"

# 3. lancer le cron QA — une seule passe, intended-for-cron
python main.py

# ou en boucle continue (debug) — repasse toutes les 60 s
python main.py --loop --sleep 60
```

Pour planifier sur une vraie crontab (toutes les heures par exemple) :

```cron
0 * * * * cd /home/imed/Bureau/Auto-QA && /home/imed/Bureau/Auto-QA/.venv/bin/python main.py >> auto-qa.log 2>&1
```

## 📊 Suivi temps-réel

Trois façons de regarder ce qui se passe pendant que le cron tourne :

```bash
# 1. Tableau de bord rich (plein écran, refresh 5 s) — capé à 1 000 lignes
python scripts/monitor.py

# 2. Variante scriptable (pas d'alt-screen, défilement standard)
python scripts/monitor.py --plain

# 3. Snapshot unique
python scripts/monitor.py --once
```

Le moniteur affiche : total deals · `est_actif` count · répartition par
`statut_test` · répartition par `tier` · les 20 derniers tests avec
`raison_echec`.

## 📑 Rapport final (au-delà du cap 1 000 REST)

```bash
# génère auto-qa-report.md + auto-qa-report.csv au repo root
python scripts/report.py

# vers stdout pour piper
python scripts/report.py --md - | less

# chemins custom
python scripts/report.py --md ~/Desktop/qa.md --csv ~/Desktop/qa.csv
```

Le rapport pagine via `.range()` pour récupérer les **1 044 deals**
(au-delà du cap 1 000 par défaut de PostgREST). Sections produites :

- Total / testés / actifs / à tester
- Répartition par statut + par tier
- Tableau détaillé pour `VALIDE`, `INVALIDE_CODE_EXPIRE`,
  `INVALIDE_LIEN_CASSE`, `A_VERIFIER_MANUELLEMENT`
- Top 30 deals `EN_ATTENTE` (les plus populaires à tester en priorité)

Le CSV s'ouvre directement dans Excel / Google Sheets.

## ⏱️ Tester l'ensemble du catalogue

```bash
# Mode boucle continue — laisse tourner en tmux pour ne pas perdre la session
tmux new -s auto-qa
source .venv/bin/activate
python main.py --loop --sleep 30
# Ctrl+B puis D pour détacher

# Reprendre la session
tmux attach -t auto-qa
```

**Estimation pour 1 044 deals** :

- **Temps** : 1 044 / `CRON_BATCH_SIZE` × ~30 min/batch (séquentiel)
  ≈ 1 jour continu si `CRON_CONCURRENCY=1`. Augmenter `CRON_CONCURRENCY=3`
  divise par 3 (~8 h) mais multiplie le risque captcha-burn.
- **Coût Anthropic Sonnet 4.6** : ~1-2 USD par deal (30 steps × image
  vision) → **~1 500 USD pour le catalogue entier**. Préférer une
  stratégie progressive :

```bash
# TIER_1 d'abord (80 deals = ~150 USD, ~3 h) — gros noms, plus de valeur
python -c "
from data_manager import SupabaseManager
db = SupabaseManager()
db.client.table('deals_saas').update({'date_prochain_test':'2099-01-01'})\
    .neq('tier','TIER_1').execute()
"
python main.py --loop --sleep 30
# laisser tourner ~3 h, puis :
python scripts/report.py
```

- **Coût CapSolver** : ~1 USD / 1 000 captchas → négligeable à l'échelle.
- **Coût Browserbase** : 0 — on tourne en local Playwright (`AGENT_LOCAL_BROWSER=1`).
- **Coût Supabase** : 0 (free tier suffit largement pour 1 044 lignes + writes).

Lance `python scripts/report.py` régulièrement pour suivre la progression
sans toucher au cron.

Suivi côté logs (à lancer dans un autre terminal) :

```bash
tail -f auto-qa.log
```

Suivi SQL ad-hoc dans Supabase Studio :

```sql
-- Tests réalisés dans la dernière heure
SELECT date_dernier_test, nom_partenaire, tier, statut_test, raison_echec
FROM deals_saas
WHERE date_dernier_test > NOW() - INTERVAL '1 hour'
ORDER BY date_dernier_test DESC;

-- Deals à retester maintenant
SELECT COUNT(*) FROM deals_saas
WHERE date_prochain_test <= NOW() OR statut_test = 'EN_ATTENTE';
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
