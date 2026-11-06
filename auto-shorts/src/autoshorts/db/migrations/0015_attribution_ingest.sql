SELECT as_migration_guard(15);

-- M3-B атрибуция и приём конверсий (контракт M5M3-0).

ALTER TABLE tracking_links
  ADD COLUMN created_by BIGINT REFERENCES users(id),
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Тестовое событие из вкладки «Вебхук» идёт тем же публичным ingest, но в статистику не попадает.
ALTER TABLE conversions ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX ix_conv_product_received ON conversions(product_id, received_at DESC);

-- Ротация HMAC-секрета без простоя: прежний секрет принимается до webhook_prev_valid_until.
ALTER TABLE products
  ADD COLUMN webhook_secret_rotated_at TIMESTAMPTZ,
  ADD COLUMN webhook_prev_secret_name TEXT CHECK (webhook_prev_secret_name ~ '^[A-Z][A-Z0-9_]{2,63}$'),
  ADD COLUMN webhook_prev_valid_until TIMESTAMPTZ;

-- Журнал запросов к POST /ingest/v1/events: вкладка «Вебхук» показывает и отказы (подпись, время,
-- размер), которых нет в conversions. Тело и заголовки не храним. Уборка — 30 дней (cleanup).
CREATE TABLE ingest_requests (
  id BIGSERIAL PRIMARY KEY,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  product_id TEXT REFERENCES products(id),           -- NULL: X-AS-Product не распознан
  product_header TEXT CHECK (length(product_header) <= 64),
  outcome TEXT NOT NULL CHECK (outcome IN
    ('accepted','invalid_signature','stale_timestamp','unknown_product','payload_too_large',
     'rate_limited','invalid_body')),
  http_status SMALLINT NOT NULL,
  events_total INT NOT NULL DEFAULT 0,
  accepted INT NOT NULL DEFAULT 0, duplicates INT NOT NULL DEFAULT 0,
  unknown_tag INT NOT NULL DEFAULT 0, invalid INT NOT NULL DEFAULT 0,
  is_test BOOLEAN NOT NULL DEFAULT false,
  body_bytes INT,
  detail TEXT CHECK (length(detail) <= 500),
  request_id TEXT);
CREATE INDEX ix_ingest_product_time ON ingest_requests(product_id, received_at DESC);
CREATE INDEX ix_ingest_time ON ingest_requests(received_at);
