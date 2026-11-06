SELECT as_migration_guard(8);

CREATE TABLE stats_daily_views (
  day DATE NOT NULL,                                 -- бизнес-день (Europe/Moscow)
  publication_id BIGINT NOT NULL REFERENCES publications(id),
  views_delta BIGINT NOT NULL DEFAULT 0, engaged_views_delta BIGINT,
  likes_delta BIGINT NOT NULL DEFAULT 0, comments_delta BIGINT NOT NULL DEFAULT 0,
  views_eod BIGINT NOT NULL DEFAULT 0,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (day, publication_id));
CREATE INDEX ix_sdv_pub ON stats_daily_views(publication_id, day);

CREATE TABLE stats_daily_traffic (
  day DATE NOT NULL,
  tracking_link_id BIGINT NOT NULL REFERENCES tracking_links(id),
  landing_views BIGINT NOT NULL DEFAULT 0, unique_visitors BIGINT NOT NULL DEFAULT 0,
  to_telegram BIGINT NOT NULL DEFAULT 0, to_site BIGINT NOT NULL DEFAULT 0, direct BIGINT NOT NULL DEFAULT 0,
  bot_starts BIGINT NOT NULL DEFAULT 0, unique_bot_starts BIGINT NOT NULL DEFAULT 0,
  direct_bot_starts BIGINT NOT NULL DEFAULT 0,       -- старты без _l (не через лендинг)
  signups BIGINT NOT NULL DEFAULT 0, leads BIGINT NOT NULL DEFAULT 0,
  first_payments BIGINT NOT NULL DEFAULT 0, payments BIGINT NOT NULL DEFAULT 0,
  revenue_minor BIGINT NOT NULL DEFAULT 0,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (day, tracking_link_id));

CREATE VIEW v_publication_dims AS
  SELECT p.id AS publication_id, p.platform, p.channel_id, j.niche_id, j.kind,
         COALESCE(p.product_id, j.product_id) AS product_id,
         COALESCE(p.campaign_id, j.campaign_id) AS campaign_id,
         COALESCE(p.banner_variant_id, j.banner_variant_id) AS banner_variant_id,
         j.source_id, j.trend_brief_id,
         p.job_id, p.published_at, j.cost_usd AS job_cost_usd
  FROM publications p JOIN jobs j ON j.id = p.job_id
  WHERE p.deleted_at IS NULL;
