SELECT as_migration_guard(18);

-- M4-B ролл-апы и разрезы аналитики (контракт M4-0, docs/ARCHITECTURE.md §6.6, §6.7).
-- Бизнес-день — business_timezone через БД: (ts AT TIME ZONE %(tz)s)::date; параметры в CASE и интервалах —
-- CAST(%(x)s AS int). Боты (clicks.is_bot) и тестовые события (conversions.is_test) в ролл-апы не идут.

-- Остальные типы событий продуктов (§5.3) — чтобы разрезы не читали conversions напрямую.
ALTER TABLE stats_daily_traffic
  ADD COLUMN tma_opens BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN site_visits BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN trial_starts BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN refunds BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN refund_minor BIGINT NOT NULL DEFAULT 0;
CREATE INDEX ix_sdt_link_day ON stats_daily_traffic(tracking_link_id, day);

-- Прямой трафик продукта (уровень атрибуции direct, §6.6): неизвестный код в редиректоре, события без тега
-- или с неразрешённым тегом. В разрез роликов не попадает, в сводке и воронке виден отдельно.
CREATE TABLE stats_daily_unattributed (
  day DATE NOT NULL,                                 -- бизнес-день (Europe/Moscow)
  product_id TEXT NOT NULL REFERENCES products(id),
  clicks_unknown_code BIGINT NOT NULL DEFAULT 0,     -- clicks.route = 'unknown_code', без ботов
  unique_visitors BIGINT NOT NULL DEFAULT 0,
  bot_starts BIGINT NOT NULL DEFAULT 0, unique_bot_starts BIGINT NOT NULL DEFAULT 0,
  tma_opens BIGINT NOT NULL DEFAULT 0, site_visits BIGINT NOT NULL DEFAULT 0,
  signups BIGINT NOT NULL DEFAULT 0, trial_starts BIGINT NOT NULL DEFAULT 0, leads BIGINT NOT NULL DEFAULT 0,
  first_payments BIGINT NOT NULL DEFAULT 0, payments BIGINT NOT NULL DEFAULT 0,
  revenue_minor BIGINT NOT NULL DEFAULT 0,
  refunds BIGINT NOT NULL DEFAULT 0, refund_minor BIGINT NOT NULL DEFAULT 0,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (day, product_id));

-- Индексы под ролл-ап прямого трафика и под разрезы.
CREATE INDEX ix_clicks_unattributed ON clicks(product_id, occurred_at) WHERE tracking_link_id IS NULL;
CREATE INDEX ix_conv_unattributed ON conversions(product_id, occurred_at) WHERE tracking_link_id IS NULL;
CREATE INDEX ix_pub_channel_published ON publications(channel_id, published_at) WHERE deleted_at IS NULL;
CREATE INDEX ix_genev_job_time ON generation_events(job_id, created_at) WHERE job_id IS NOT NULL;
CREATE INDEX ix_genev_overhead_time ON generation_events(created_at) WHERE job_id IS NULL;

-- Измерения публикации: к 0008 добавлены (только в конец — правило CREATE OR REPLACE VIEW)
-- video_uid, статус публикации и трек под именем разреза (track = jobs.kind: generate | clip).
CREATE OR REPLACE VIEW v_publication_dims AS
  SELECT p.id AS publication_id, p.platform, p.channel_id, j.niche_id, j.kind,
         COALESCE(p.product_id, j.product_id) AS product_id,
         COALESCE(p.campaign_id, j.campaign_id) AS campaign_id,
         COALESCE(p.banner_variant_id, j.banner_variant_id) AS banner_variant_id,
         j.source_id, j.trend_brief_id,
         p.job_id, p.published_at, j.cost_usd AS job_cost_usd,
         j.video_uid, p.status AS publication_status, j.kind AS track
  FROM publications p JOIN jobs j ON j.id = p.job_id
  WHERE p.deleted_at IS NULL;
