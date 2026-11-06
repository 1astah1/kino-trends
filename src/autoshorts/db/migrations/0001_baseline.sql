SELECT as_migration_guard(1);
-- Схема Auto Shorts. См. ТЗ §9 "Инфраструктура", §10 "Preflight-QC и приёмка",
-- §11 "Операционная устойчивость" (реестр публикаций — обязательная сущность).

CREATE TABLE IF NOT EXISTS channels (
    id              TEXT PRIMARY KEY,          -- короткий slug, напр. 'taro_main'
    niche           TEXT NOT NULL,             -- ссылка на config/niches/<niche>.yaml
    platform        TEXT NOT NULL,             -- youtube | vk
    cluster         TEXT NOT NULL,             -- "куст" изоляции — см. ТЗ §11
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes           TEXT
);

-- Очередь задач: SELECT ... FOR UPDATE SKIP LOCKED, см. ТЗ §9
CREATE TABLE IF NOT EXISTS jobs (
    id              BIGSERIAL PRIMARY KEY,
    video_uid       TEXT UNIQUE NOT NULL,      -- внутренний id ролика, до публикации на площадке
    channel_id      TEXT NOT NULL REFERENCES channels(id),
    niche           TEXT NOT NULL,
    template_version TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
                    -- queued -> script_ready -> voiced -> rendered -> qc_passed / qc_failed
                    -- -> pending_review -> approved -> rework -> rejected -> ready_to_publish -> published
    stage_payload   JSONB NOT NULL DEFAULT '{}',   -- промежуточные артефакты стадий (пути, тексты, тайминги)
    reject_reason   TEXT,                          -- фиксированный список причин, см. ТЗ §10
    attempt_count   INT NOT NULL DEFAULT 0,
    review_sent_at  TIMESTAMPTZ,                   -- когда карточка ушла в Telegram (bot/telegram_bot.py review_poll_loop)
    locked_by       TEXT,
    locked_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_channel ON jobs(channel_id);

-- Каждый вызов платного/бесплатного провайдера — для расчёта "цены ОПУБЛИКОВАННОГО ролика", ТЗ §9
CREATE TABLE IF NOT EXISTS generation_events (
    id              BIGSERIAL PRIMARY KEY,
    job_id          BIGINT NOT NULL REFERENCES jobs(id),
    step_name       TEXT NOT NULL,              -- script | tts | image | render | ...
    provider        TEXT NOT NULL,
    model           TEXT,
    units           NUMERIC,
    unit_price_usd  NUMERIC,
    cost_usd        NUMERIC NOT NULL DEFAULT 0,
    latency_ms      INT,
    http_status     INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_genev_job ON generation_events(job_id);
CREATE INDEX IF NOT EXISTS idx_genev_created ON generation_events(created_at);

-- Бюджетный предохранитель, ТЗ §9: читается перед каждым платным вызовом
CREATE TABLE IF NOT EXISTS budget_state (
    id              INT PRIMARY KEY DEFAULT 1,
    daily_limit_usd NUMERIC NOT NULL DEFAULT 15.0,
    spent_today_usd NUMERIC NOT NULL DEFAULT 0,
    day             DATE NOT NULL DEFAULT CURRENT_DATE,
    paused          BOOLEAN NOT NULL DEFAULT false
);
INSERT INTO budget_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Реестр публикаций — обязательная сущность (после терминации канала выгрузка невозможна), ТЗ §11
CREATE TABLE IF NOT EXISTS publications (
    id              BIGSERIAL PRIMARY KEY,
    job_id          BIGINT NOT NULL REFERENCES jobs(id),
    channel_id      TEXT NOT NULL REFERENCES channels(id),
    platform        TEXT NOT NULL,
    platform_video_id TEXT,                     -- вводится человеком после РУЧНОЙ заливки, см. ТЗ (правка про ручную публикацию)
    master_file_path TEXT NOT NULL,              -- мастер-файл хранится локально навсегда
    title           TEXT,
    description     TEXT,
    hashtags        TEXT[],
    published_at    TIMESTAMPTZ,
    attribution_tag TEXT,                        -- напр. yt_<platform_video_id> — deeplink-метка, ТЗ §6
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pub_channel ON publications(channel_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pub_attr_tag ON publications(attribution_tag) WHERE attribution_tag IS NOT NULL;

-- Клики по редиректору (домен + бот параллельно, ТЗ §6) — для сквозной атрибуции ролик -> переход -> регистрация
CREATE TABLE IF NOT EXISTS attribution_events (
    id              BIGSERIAL PRIMARY KEY,
    tag             TEXT NOT NULL,               -- videoId источника, без кириллицы
    source          TEXT NOT NULL,               -- yt | vk | tg
    route           TEXT NOT NULL,               -- 'domain' | 'bot'
    ip_hash         TEXT,
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_attr_tag ON attribution_events(tag);

-- Собственная калибровка виральности — своя медиана/p75, а не платформенные мифы, ТЗ §12 Этап 1
CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    publication_id  BIGINT NOT NULL REFERENCES publications(id),
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    age_hours       NUMERIC,
    views           BIGINT,
    engaged_views   BIGINT,
    retention_3s    NUMERIC,
    avg_pct_viewed  NUMERIC,
    likes           BIGINT,
    comments        BIGINT
);
CREATE INDEX IF NOT EXISTS idx_metrics_pub ON metrics_snapshots(publication_id);

-- Тренд-скаутинг: outlier-движок, ТЗ §8
CREATE TABLE IF NOT EXISTS competitor_channels (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    channel_ref     TEXT NOT NULL,   -- youtube channel_id или vk group id
    pool            TEXT NOT NULL DEFAULT 'ru',  -- 'ru' | 'donor_en' (кросс-платформенный лаг)
    active          BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS competitor_video_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    competitor_id   TEXT NOT NULL REFERENCES competitor_channels(id),
    external_video_id TEXT NOT NULL,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    age_hours       NUMERIC,
    views           BIGINT,
    outlier_score   NUMERIC          -- views / медиана последних 20-30 роликов канала
);
CREATE INDEX IF NOT EXISTS idx_cvs_competitor ON competitor_video_snapshots(competitor_id, external_video_id);

CREATE TABLE IF NOT EXISTS trend_briefs (
    id              BIGSERIAL PRIMARY KEY,
    external_video_id TEXT NOT NULL,
    hook_type       TEXT,
    hook_duration_sec NUMERIC,
    cuts_per_sec_first5 NUMERIC,
    total_duration_bucket TEXT,
    text_on_screen_density NUMERIC,
    payoff_timestamp_ratio NUMERIC,
    loop_seam       BOOLEAN,
    cta_position    TEXT,
    outlier_score   NUMERIC,
    reviewed        BOOLEAN NOT NULL DEFAULT false,
    accepted        BOOLEAN,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
