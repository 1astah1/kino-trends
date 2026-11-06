SELECT as_migration_guard(2);

CREATE TABLE users (
  id BIGSERIAL PRIMARY KEY,
  login TEXT NOT NULL UNIQUE CHECK (login ~ '^[a-z0-9][a-z0-9_.-]{2,31}$'),
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,                       -- argon2id
  role TEXT NOT NULL CHECK (role IN ('admin','operator','viewer')),
  tg_user_id BIGINT UNIQUE,                          -- кто может жать кнопки приёмки в боте
  is_active BOOLEAN NOT NULL DEFAULT true,
  failed_logins INT NOT NULL DEFAULT 0,
  locked_until TIMESTAMPTZ,
  last_login_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE sessions (
  id BIGSERIAL PRIMARY KEY,
  token_hash BYTEA NOT NULL UNIQUE,                  -- sha256(token); сам токен только в cookie
  csrf_token TEXT NOT NULL,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  ip_hash TEXT, user_agent TEXT);
CREATE INDEX ix_sessions_user_active ON sessions(user_id) WHERE revoked_at IS NULL;

CREATE TABLE api_tokens (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL, prefix TEXT NOT NULL,
  token_hash BYTEA NOT NULL UNIQUE,
  scopes TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ, expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ);
CREATE INDEX ix_api_tokens_user ON api_tokens(user_id);

CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor_kind TEXT NOT NULL CHECK (actor_kind IN ('user','api_token','bot','system','webhook')),
  actor_user_id BIGINT REFERENCES users(id),
  action TEXT NOT NULL,                              -- 'job.review', 'secret.set', 'product.update'
  entity_type TEXT NOT NULL, entity_id TEXT,
  summary TEXT, diff JSONB,                          -- значения секретов вырезаются до записи
  request_id TEXT, ip_hash TEXT);
CREATE INDEX ix_audit_time ON audit_log(occurred_at DESC);
CREATE INDEX ix_audit_entity ON audit_log(entity_type, entity_id);

CREATE TABLE settings (                              -- только переопределения; дефолты в domain/settings_schema.py
  key TEXT PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{2,63}$'),
  value JSONB NOT NULL,
  updated_by BIGINT REFERENCES users(id),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE secrets (
  name TEXT PRIMARY KEY CHECK (name ~ '^[A-Z][A-Z0-9_]{2,63}$'),
  ciphertext BYTEA NOT NULL, key_id TEXT NOT NULL, last4 TEXT,
  updated_by BIGINT REFERENCES users(id),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE provider_configs (
  key TEXT PRIMARY KEY CHECK (key ~ '^[a-z0-9_]{2,63}$'),
  kind TEXT NOT NULL CHECK (kind IN ('llm','tts','image','asr','vision','faces')),
  enabled BOOLEAN NOT NULL DEFAULT true,
  is_default BOOLEAN NOT NULL DEFAULT false,
  params JSONB NOT NULL DEFAULT '{}',
  secret_name TEXT,
  updated_by BIGINT REFERENCES users(id),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX uq_provider_default_per_kind ON provider_configs(kind) WHERE is_default;

CREATE TABLE alerts (
  id BIGSERIAL PRIMARY KEY,
  code TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','acked','resolved')),
  dedup_key TEXT NOT NULL, title TEXT NOT NULL, details JSONB NOT NULL DEFAULT '{}',
  opened_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ, acked_by BIGINT REFERENCES users(id), notified_at TIMESTAMPTZ);
CREATE UNIQUE INDEX uq_alerts_open ON alerts(dedup_key) WHERE status <> 'resolved';
CREATE INDEX ix_alerts_status_time ON alerts(status, opened_at DESC);

CREATE TABLE idempotency_keys (
  key TEXT NOT NULL, user_id BIGINT NOT NULL REFERENCES users(id),
  response JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key));
