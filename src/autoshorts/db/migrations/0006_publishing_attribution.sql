SELECT as_migration_guard(6);

CREATE TABLE tracking_links (
  id BIGSERIAL PRIMARY KEY,
  code TEXT NOT NULL UNIQUE CHECK (code ~ '^[A-Za-z0-9_-]{2,64}$'),  -- алфавит Telegram start-параметра
  product_id TEXT NOT NULL REFERENCES products(id),
  level TEXT NOT NULL CHECK (level IN ('package','publication','channel','campaign','manual')),
  job_id BIGINT REFERENCES jobs(id),
  publication_id BIGINT REFERENCES publications(id),
  channel_id TEXT REFERENCES channels(id),
  campaign_id BIGINT REFERENCES campaigns(id),
  banner_variant_id BIGINT REFERENCES banner_variants(id),
  platform TEXT CHECK (platform IN ('youtube','vk')),
  target_override_url TEXT, label TEXT,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ix_tl_publication ON tracking_links(publication_id);
CREATE INDEX ix_tl_job ON tracking_links(job_id);
CREATE INDEX ix_tl_channel ON tracking_links(channel_id);
CREATE INDEX ix_tl_campaign ON tracking_links(campaign_id);

CREATE TABLE upload_packages (
  id BIGSERIAL PRIMARY KEY,
  job_id BIGINT NOT NULL REFERENCES jobs(id),
  channel_id TEXT NOT NULL REFERENCES channels(id),
  platform TEXT NOT NULL CHECK (platform IN ('youtube','vk')),
  status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','downloaded','published','superseded')),
  title TEXT NOT NULL, description TEXT NOT NULL, hashtags TEXT[] NOT NULL DEFAULT '{}',
  pinned_comment TEXT, audio_note TEXT, related_video_hint TEXT,
  checklist JSONB NOT NULL DEFAULT '[]',             -- [{code, done, done_at, done_by}]
  tracking_link_id BIGINT NOT NULL REFERENCES tracking_links(id),
  final_artifact_id BIGINT NOT NULL REFERENCES artifacts(id),
  downloaded_at TIMESTAMPTZ,
  edited_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX uq_pkg_live_per_job ON upload_packages(job_id) WHERE status IN ('ready','downloaded');
CREATE INDEX ix_pkg_status_channel ON upload_packages(status, channel_id);

ALTER TABLE publications
  ADD COLUMN package_id BIGINT REFERENCES upload_packages(id),
  ADD COLUMN url TEXT, ADD COLUMN canonical_url TEXT,
  ADD COLUMN platform_kind TEXT CHECK (platform_kind IN ('short','clip','video')),
  ADD COLUMN status TEXT NOT NULL DEFAULT 'live' CHECK (status IN
     ('live','unlisted','private','removed_by_platform','deleted_by_us')),
  ADD COLUMN status_reason TEXT,
  ADD COLUMN has_claim BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN verify_status TEXT NOT NULL DEFAULT 'pending'
     CHECK (verify_status IN ('pending','ok','channel_mismatch','not_found','unverifiable')),
  ADD COLUMN verify_detail TEXT,
  ADD COLUMN product_id TEXT REFERENCES products(id),
  ADD COLUMN campaign_id BIGINT REFERENCES campaigns(id),
  ADD COLUMN banner_variant_id BIGINT REFERENCES banner_variants(id),
  ADD COLUMN registered_by BIGINT REFERENCES users(id),
  ADD COLUMN next_poll_at TIMESTAMPTZ, ADD COLUMN poll_step SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN last_polled_at TIMESTAMPTZ,
  ADD COLUMN latest_views BIGINT, ADD COLUMN latest_likes BIGINT, ADD COLUMN latest_comments BIGINT,
  ADD COLUMN latest_engaged_views BIGINT,
  ADD COLUMN retention_3s NUMERIC(5,4), ADD COLUMN avg_pct_viewed NUMERIC(6,4),
  ADD COLUMN views_24h BIGINT, ADD COLUMN views_72h BIGINT, ADD COLUMN views_7d BIGINT,
  ADD COLUMN deleted_at TIMESTAMPTZ;
CREATE UNIQUE INDEX uq_pub_platform_video ON publications(platform, platform_video_id)
  WHERE platform_video_id IS NOT NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX uq_pub_job_platform ON publications(job_id, platform) WHERE deleted_at IS NULL;  -- 1 ролик = 1 канал на площадке
CREATE INDEX ix_pub_poll ON publications(next_poll_at) WHERE status = 'live' AND deleted_at IS NULL;
CREATE INDEX ix_pub_published ON publications(published_at DESC);

CREATE TABLE clicks (
  id BIGSERIAL PRIMARY KEY,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  code TEXT NOT NULL,
  tracking_link_id BIGINT REFERENCES tracking_links(id),
  route TEXT NOT NULL CHECK (route IN ('landing','to_telegram','to_site','direct','unknown_code')),
  product_id TEXT, publication_id BIGINT, job_id BIGINT, channel_id TEXT,     -- денормализовано на вставке
  campaign_id BIGINT, banner_variant_id BIGINT, platform TEXT,
  visitor_key TEXT NOT NULL,                         -- hmac(REDIRECT_SALT, day|ip|ua)[:32]; сырой IP не храним
  user_agent TEXT, referer_host TEXT,
  is_bot BOOLEAN NOT NULL DEFAULT false);
CREATE INDEX ix_clicks_time ON clicks(occurred_at);
CREATE INDEX ix_clicks_link_time ON clicks(tracking_link_id, occurred_at);
CREATE INDEX ix_clicks_pub ON clicks(publication_id, occurred_at);

CREATE TABLE conversions (
  id BIGSERIAL PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES products(id),
  external_event_id TEXT NOT NULL CHECK (length(external_event_id) <= 128),
  event_type TEXT NOT NULL CHECK (event_type IN
     ('bot_start','tma_open','site_visit','signup','trial_start','lead','first_payment','payment','refund')),
  occurred_at TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  tag TEXT, via_landing BOOLEAN NOT NULL DEFAULT false,      -- суффикс _l у start-параметра
  tracking_link_id BIGINT REFERENCES tracking_links(id),
  publication_id BIGINT, job_id BIGINT, channel_id TEXT, campaign_id BIGINT, banner_variant_id BIGINT, platform TEXT,
  user_ref_hash TEXT,                                -- sha256(product|user_ref); PII не храним
  amount_minor BIGINT, currency CHAR(3), meta JSONB NOT NULL DEFAULT '{}',
  UNIQUE (product_id, external_event_id));
CREATE INDEX ix_conv_time ON conversions(occurred_at);
CREATE INDEX ix_conv_link_type ON conversions(tracking_link_id, event_type, occurred_at);
CREATE INDEX ix_conv_pub ON conversions(publication_id, occurred_at);

ALTER TABLE metrics_snapshots
  ADD COLUMN source TEXT NOT NULL DEFAULT 'yt_data'
    CHECK (source IN ('yt_data','yt_analytics','vk_api','ytdlp','manual')),
  ADD COLUMN shares BIGINT, ADD COLUMN avg_view_duration_sec NUMERIC(8,2),
  ADD COLUMN subscribers_gained INT, ADD COLUMN shorts_feed_share NUMERIC(5,4), ADD COLUMN raw JSONB;
CREATE INDEX ix_metrics_pub_time ON metrics_snapshots(publication_id, captured_at DESC);

CREATE TABLE retention_curves (
  id BIGSERIAL PRIMARY KEY,
  publication_id BIGINT NOT NULL REFERENCES publications(id),
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  points JSONB NOT NULL,                             -- [[ratio, audience_watch_ratio, relative_retention_performance], …] 100 точек
  retention_3s NUMERIC(5,4),
  failure_class TEXT CHECK (failure_class IN ('hook_drop','middle_sag','no_loop_lift','none')));
CREATE INDEX ix_ret_pub ON retention_curves(publication_id, captured_at DESC);

CREATE TABLE channel_snapshots (
  channel_id TEXT NOT NULL REFERENCES channels(id), captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  subscribers BIGINT, total_views BIGINT, video_count INT,
  PRIMARY KEY (channel_id, captured_at));

CREATE TABLE channel_oauth (                         -- опционально: YouTube Analytics
  channel_id TEXT PRIMARY KEY REFERENCES channels(id),
  refresh_token_secret TEXT NOT NULL,                -- secrets.name
  scopes TEXT[] NOT NULL, granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_ok_at TIMESTAMPTZ, last_error TEXT);

CREATE TABLE api_quota_usage (                       -- YouTube Data API: сброс в полночь PT
  api TEXT NOT NULL, quota_day DATE NOT NULL, cluster_id TEXT NOT NULL DEFAULT 'default',
  units INT NOT NULL DEFAULT 0,
  PRIMARY KEY (api, quota_day, cluster_id));

-- legacy: метки yt_<id> существующих публикаций -> трекинг-ссылки; attribution_events -> clicks
INSERT INTO tracking_links (code, product_id, level, publication_id, job_id, channel_id, platform)
  SELECT p.attribution_tag, 'umka', 'publication', p.id, p.job_id, p.channel_id, p.platform
  FROM publications p
  WHERE p.attribution_tag IS NOT NULL AND p.attribution_tag ~ '^[A-Za-z0-9_-]{2,64}$'
  ON CONFLICT (code) DO NOTHING;
INSERT INTO clicks (occurred_at, code, tracking_link_id, route, product_id, publication_id, job_id,
                    channel_id, platform, visitor_key, user_agent)
  SELECT a.created_at, a.tag, tl.id,
         CASE WHEN tl.id IS NULL THEN 'unknown_code' ELSE 'landing' END,
         'umka', tl.publication_id, tl.job_id, tl.channel_id, tl.platform,
         COALESCE(a.ip_hash, 'legacy'), a.user_agent
  FROM attribution_events a LEFT JOIN tracking_links tl ON tl.code = a.tag
  WHERE a.tag <> 'health';
-- attribution_events остаётся только для чтения; DROP в 0010
