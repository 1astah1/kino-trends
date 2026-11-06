SELECT as_migration_guard(3);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM channels
             WHERE id !~ '^[a-z0-9][a-z0-9_-]{1,39}$' OR niche !~ '^[a-z0-9][a-z0-9_-]{1,39}$'
                OR cluster !~ '^[a-z0-9][a-z0-9_-]{1,39}$' OR platform NOT IN ('youtube','vk')) THEN
    RAISE EXCEPTION 'channels: id/niche/cluster must be latin slugs, platform youtube|vk (fix before 0003)';
  END IF;
  IF EXISTS (SELECT 1 FROM jobs WHERE niche !~ '^[a-z0-9][a-z0-9_-]{1,39}$') THEN
    RAISE EXCEPTION 'jobs.niche must be latin slug (fix before 0003)';
  END IF;
END $$;

CREATE TABLE products (
  id TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,39}$'),       -- umka, gamekeys_tma, vpn_bot
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('web_service','telegram_bot','telegram_mini_app','other')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  landing_url TEXT,
  redirect_host TEXT,                                -- u.umka.chat
  redirect_mode TEXT NOT NULL DEFAULT 'landing_split'
    CHECK (redirect_mode IN ('landing_split','direct_site','direct_telegram')),
  telegram_bot_username TEXT CHECK (telegram_bot_username ~ '^[A-Za-z0-9_]{5,32}$'),
  tma_short_name TEXT CHECK (tma_short_name ~ '^[A-Za-z0-9_]{3,64}$'),
  telegram_link_style TEXT NOT NULL DEFAULT 'tg_scheme_with_fallback'
    CHECK (telegram_link_style IN ('tg_scheme_with_fallback','https_tme')),
  utm_defaults JSONB NOT NULL DEFAULT '{}',
  brand JSONB NOT NULL DEFAULT '{}',                 -- {display_domain, primary_color, logo_asset_id}
  landing JSONB NOT NULL DEFAULT '{}',               -- {title, subtitle, button_telegram, button_site}
  webhook_secret_name TEXT,                          -- secrets.name, напр. PRODUCT_WEBHOOK_UMKA
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
-- legacy-продукт: старый код жёстко зашивал umka.chat / umka_bot / u.umka.chat
INSERT INTO products (id, name, kind, landing_url, redirect_host, telegram_bot_username, brand, landing)
VALUES ('umka', 'Умка', 'web_service', 'https://umka.chat/', 'u.umka.chat', 'umka_bot',
        '{"display_domain":"umka.chat"}',
        '{"title":"Бесплатный расклад ждёт тебя","subtitle":"Выбери, как удобнее продолжить","button_telegram":"Открыть в Telegram","button_site":"Продолжить на сайте"}')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE clusters (                              -- «куст» изоляции (ТЗ §10)
  id TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,39}$'),
  title TEXT NOT NULL, notes TEXT,
  youtube_api_key_secret TEXT,                       -- secrets.name, свой Cloud-проект на куст
  youtube_oauth_client_secret TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
INSERT INTO clusters (id, title) SELECT DISTINCT cluster, cluster FROM channels ON CONFLICT DO NOTHING;

CREATE TABLE niches (
  id TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,39}$'),
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','needs_import')),
  current_version INT NOT NULL DEFAULT 0,
  config JSONB NOT NULL DEFAULT '{}',                -- валидируется domain/niche_schema.NicheConfig
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE niche_versions (
  niche_id TEXT NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
  version INT NOT NULL,
  config JSONB NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('ui','yaml_import','migration')),
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (niche_id, version));
INSERT INTO niches (id, label, status)
  SELECT DISTINCT niche, niche, 'needs_import' FROM (SELECT niche FROM channels UNION SELECT niche FROM jobs) s
  ON CONFLICT DO NOTHING;

ALTER TABLE channels RENAME COLUMN niche TO niche_id;
ALTER TABLE channels
  ADD COLUMN title TEXT,
  ADD COLUMN platform_channel_ref TEXT,              -- UC… для YouTube, -<group_id> для VK
  ADD COLUMN url TEXT,
  ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('warming','active','paused','sanctioned','terminated','retired')),
  ADD COLUMN replaced_by TEXT REFERENCES channels(id),
  ADD COLUMN default_product_id TEXT REFERENCES products(id),
  ADD COLUMN profile JSONB NOT NULL DEFAULT '{}',    -- {tts_provider, tts_voice, banner_template_id, visual_style_suffix, seed_family}
  ADD COLUMN publishing_plan JSONB NOT NULL DEFAULT '{}', -- {videos_per_day: 3}
  ADD COLUMN advanced_features BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN subscribers BIGINT,
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD CONSTRAINT ck_channels_id CHECK (id ~ '^[a-z0-9][a-z0-9_-]{1,39}$'),
  ADD CONSTRAINT ck_channels_platform CHECK (platform IN ('youtube','vk')),
  ADD CONSTRAINT fk_channels_niche FOREIGN KEY (niche_id) REFERENCES niches(id),
  ADD CONSTRAINT fk_channels_cluster FOREIGN KEY (cluster) REFERENCES clusters(id);
CREATE UNIQUE INDEX uq_channels_ref ON channels(platform, platform_channel_ref) WHERE platform_channel_ref IS NOT NULL;
CREATE INDEX ix_channels_cluster ON channels(cluster);

CREATE TABLE channel_incidents (                     -- журнал диагностики охвата/санкций (ТЗ §10)
  id BIGSERIAL PRIMARY KEY,
  channel_id TEXT NOT NULL REFERENCES channels(id),
  kind TEXT NOT NULL CHECK (kind IN ('reach_drop','sanction','strike','claim','unique_phrase_test','freeze','note')),
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  details JSONB NOT NULL DEFAULT '{}', note TEXT,
  created_by BIGINT REFERENCES users(id));
CREATE INDEX ix_incidents_channel ON channel_incidents(channel_id, occurred_at DESC);

CREATE TABLE assets (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('font','logo','music','image','broll','watermark_ref')),
  name TEXT NOT NULL, path TEXT NOT NULL,             -- относительно MEDIA_ROOT
  bytes BIGINT NOT NULL, mime TEXT NOT NULL,
  license_note TEXT NOT NULL,
  meta JSONB NOT NULL DEFAULT '{}',                  -- шрифт: {"cyrillic_ok": true, "missing": []}
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), deleted_at TIMESTAMPTZ);
CREATE INDEX ix_assets_kind ON assets(kind) WHERE deleted_at IS NULL;

CREATE TABLE banner_templates (                      -- геометрия и тайминг (форма config/banner.yaml)
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  layout JSONB NOT NULL,                             -- валидируется domain/banner_schema.BannerLayout
  version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE campaigns (
  id BIGSERIAL PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES products(id),
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','paused','ended')),
  banner_template_id BIGINT NOT NULL REFERENCES banner_templates(id),
  allocation TEXT NOT NULL DEFAULT 'weighted' CHECK (allocation IN ('weighted','round_robin','manual')),
  priority SMALLINT NOT NULL DEFAULT 100,
  targeting JSONB NOT NULL DEFAULT '{}',             -- {channel_ids:[], niche_ids:[], platforms:[], kinds:[]}
  starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, notes TEXT,
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ix_campaigns_active ON campaigns(priority) WHERE status = 'active';
CREATE INDEX ix_campaigns_product ON campaigns(product_id);

CREATE TABLE banner_variants (
  id BIGSERIAL PRIMARY KEY,
  campaign_id BIGINT NOT NULL REFERENCES campaigns(id),
  code TEXT NOT NULL CHECK (code ~ '^[A-Za-z0-9_-]{1,16}$'),   -- A, B, C
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft','active','paused','archived')),
  weight SMALLINT NOT NULL DEFAULT 50 CHECK (weight BETWEEN 0 AND 1000),
  micro_brand_text TEXT,                             -- слой 1
  offer_text TEXT NOT NULL,                          -- слой 2, строка 1
  offer_text_2 TEXT,                                 -- слой 2, строка 2
  voice_cta_text TEXT,                               -- произносимый CTA, уходит в сценарий
  pinned_comment_template TEXT, description_cta_template TEXT,
  style_overrides JSONB NOT NULL DEFAULT '{}',       -- {background_opacity, bg_color, text_color, corner_radius_px}
  layout_overrides JSONB NOT NULL DEFAULT '{}',      -- частичная геометрия поверх шаблона (валидируется)
  show_code_in_frame BOOLEAN NOT NULL DEFAULT false, -- «u.umka.chat/k7m2q» вместо голого домена (решает владелец)
  ad_marking JSONB NOT NULL DEFAULT '{}',            -- {enabled:false, erid, advertiser_text} — до 01.01.2027
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (campaign_id, code));
