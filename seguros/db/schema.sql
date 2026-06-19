-- Schema da régua de cobrança. Tudo IF NOT EXISTS -> init idempotente.
-- Convenções: datetime = TEXT ISO-8601 UTC ("...Z"); bool = INTEGER 0/1;
-- dinheiro = INTEGER centavos; CPF = TEXT 11 dígitos.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS clientes_regua (
    cpf                      TEXT NOT NULL,
    corretor_id              TEXT NOT NULL DEFAULT 'local',
    nome                     TEXT NOT NULL,
    telefone                 TEXT,
    email                    TEXT,
    valor_inadimplente_cents INTEGER,
    valor_texto              TEXT,
    vencimento_mais_antigo   TEXT,
    competencia              TEXT,
    work_status              TEXT,
    link_pagamento           TEXT,
    link_gerado_em           TEXT,
    autoriza_whatsapp        INTEGER NOT NULL DEFAULT 0 CHECK (autoriza_whatsapp IN (0, 1)),
    autoriza_email           INTEGER NOT NULL DEFAULT 0 CHECK (autoriza_email IN (0, 1)),
    whatsapp_enviado_em      TEXT,
    email_enviado_em         TEXT,
    enrolled_em              TEXT NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'em_regua'
                               CHECK (status IN ('em_regua', 'resolvido', 'opt_out')),
    atualizado_em            TEXT NOT NULL,
    PRIMARY KEY (corretor_id, cpf)
);
CREATE INDEX IF NOT EXISTS idx_regua_status   ON clientes_regua(corretor_id, status);
CREATE INDEX IF NOT EXISTS idx_regua_enrolled ON clientes_regua(enrolled_em);

CREATE TABLE IF NOT EXISTS opt_out (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    corretor_id TEXT NOT NULL DEFAULT 'local',
    cpf         TEXT,
    telefone    TEXT,
    origem      TEXT NOT NULL CHECK (origem IN ('sair_whatsapp', 'manual')),
    data        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_optout_cpf
    ON opt_out(corretor_id, cpf) WHERE cpf IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_optout_tel ON opt_out(corretor_id, telefone);

CREATE TABLE IF NOT EXISTS log_disparos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    corretor_id    TEXT NOT NULL DEFAULT 'local',
    cpf            TEXT NOT NULL,
    canal          TEXT NOT NULL CHECK (canal IN ('whatsapp', 'email', 'sistema')),
    link           TEXT,
    resultado      TEXT NOT NULL,
    payload_resumo TEXT,
    modo           TEXT NOT NULL CHECK (modo IN ('dry_run', 'live')),
    data           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_cpf  ON log_disparos(corretor_id, cpf);
CREATE INDEX IF NOT EXISTS idx_log_data ON log_disparos(data);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '1');
