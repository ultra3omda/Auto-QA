-- Auto-QA Web Agent — schéma Supabase / PostgreSQL
-- Idempotent : peut être réexécuté sans erreur.
--   psql "$SUPABASE_DB_URL" -f schema.sql

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$ BEGIN
    CREATE TYPE qa_status AS ENUM (
        'VALIDE',
        'INVALIDE_CODE_EXPIRE',
        'INVALIDE_LIEN_CASSE',
        'A_VERIFIER_MANUELLEMENT',
        'EN_ATTENTE'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE popularite_tier AS ENUM ('TIER_1', 'TIER_2', 'TIER_3');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS deals_saas (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    nom_partenaire VARCHAR(255) NOT NULL,
    url_fiche_freelance TEXT,
    titre_offre TEXT,
    offre_detectee TEXT,
    mecanisme VARCHAR(100),
    code_promo_1 VARCHAR(100),
    code_promo_2 VARCHAR(100),
    url_partenaire_1 TEXT NOT NULL,
    url_partenaire_2 TEXT,
    url_partenaire_3 TEXT,

    popularite_score INTEGER DEFAULT 0,
    tier popularite_tier DEFAULT 'TIER_3',
    statut_test qa_status DEFAULT 'EN_ATTENTE',
    raison_echec TEXT,
    date_dernier_test TIMESTAMP WITH TIME ZONE,
    date_prochain_test TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    est_actif BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_smart_qa
    ON deals_saas(date_prochain_test)
    WHERE est_actif = TRUE;

CREATE INDEX IF NOT EXISTS idx_deals_saas_tier
    ON deals_saas(tier);
