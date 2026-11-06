SELECT as_migration_guard(17);

-- M4-A коллекторы метрик своих публикаций (контракт M4-0, docs/ARCHITECTURE.md §6.5).
-- Решения владельца: YouTube — Data API по ключу куста (clusters.youtube_api_key_secret, иначе секрет
-- YOUTUBE_API_KEY), без ключа коллектор выключен с понятным статусом; VK — yt-dlp, затем ручной ввод.
-- YouTube Analytics (OAuth, M4-D) в этот прогон не входит: yt_analytics в CHECK — задел.

-- Состояние коллекторов: строка на (коллектор, куст). Пишет M4-A, читают /quota, /analytics/freshness и Обзор.
CREATE TABLE collector_state (
  collector TEXT NOT NULL CHECK (collector IN ('yt_data','yt_analytics','vk_api','ytdlp','manual')),
  cluster_id TEXT NOT NULL DEFAULT 'default' CHECK (cluster_id ~ '^[a-z0-9][a-z0-9_-]{1,39}$'),
  state TEXT NOT NULL DEFAULT 'idle' CHECK (state IN ('ok','idle','disabled','degraded','paused','failing')),
  reason TEXT CHECK (reason IN ('no_api_key','quota_exhausted','quota_soft_limit','network_error',
                                'http_error','auth_error','tool_missing','not_configured','manual_only')),
  message TEXT CHECK (length(message) <= 500),       -- для человека, по-русски
  last_run_at TIMESTAMPTZ, last_ok_at TIMESTAMPTZ,
  last_error_at TIMESTAMPTZ, last_error TEXT CHECK (length(last_error) <= 2000),
  consecutive_failures INT NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  paused_until TIMESTAMPTZ,                          -- квота исчерпана: до сброса суток квоты
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (collector, cluster_id),
  CONSTRAINT ck_collector_state_reason CHECK (reason IS NOT NULL OR state IN ('ok','idle')));

-- Публикация: какой коллектор её ведёт, ошибки опроса и ожидание ручного ввода.
ALTER TABLE publications
  ADD COLUMN metrics_collector TEXT CHECK (metrics_collector IN ('yt_data','yt_analytics','vk_api','ytdlp','manual')),
  ADD COLUMN poll_failures SMALLINT NOT NULL DEFAULT 0 CHECK (poll_failures >= 0),
  ADD COLUMN last_poll_error TEXT CHECK (length(last_poll_error) <= 2000),
  ADD COLUMN manual_due_at TIMESTAMPTZ,              -- с этого момента ждём ручной ввод (GET /metrics/manual-due)
  ADD COLUMN views_14d BIGINT, ADD COLUMN views_30d BIGINT;   -- интерполяция на ровный возраст (когорты)
UPDATE publications SET metrics_collector = CASE platform WHEN 'youtube' THEN 'yt_data' ELSE 'ytdlp' END
  WHERE metrics_collector IS NULL AND platform IN ('youtube','vk');
CREATE INDEX ix_pub_manual_due ON publications(manual_due_at)
  WHERE manual_due_at IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX ix_pub_collector_poll ON publications(metrics_collector, next_poll_at)
  WHERE status = 'live' AND deleted_at IS NULL;

-- Снимки: кто ввёл руками и зачем. Неотрицательность — для новых строк (legacy не перепроверяется).
ALTER TABLE metrics_snapshots
  ADD COLUMN created_by BIGINT REFERENCES users(id),
  ADD COLUMN note TEXT CHECK (length(note) <= 500);
ALTER TABLE metrics_snapshots
  ADD CONSTRAINT ck_metrics_nonneg CHECK (
    (views IS NULL OR views >= 0) AND (likes IS NULL OR likes >= 0) AND (comments IS NULL OR comments >= 0)
    AND (shares IS NULL OR shares >= 0) AND (engaged_views IS NULL OR engaged_views >= 0)) NOT VALID;
CREATE INDEX ix_metrics_manual ON metrics_snapshots(publication_id, captured_at DESC) WHERE source = 'manual';

-- Квоты: сутки квоты (YouTube — America/Los_Angeles), вызовы и ошибки для /quota.
ALTER TABLE api_quota_usage
  ADD COLUMN calls INT NOT NULL DEFAULT 0 CHECK (calls >= 0),
  ADD COLUMN errors INT NOT NULL DEFAULT 0 CHECK (errors >= 0),
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE api_quota_usage
  ADD CONSTRAINT ck_quota_api CHECK (api IN ('youtube_data','youtube_analytics','vk_api')) NOT VALID;

-- Снимки канала (задача channel_stats): откуда цифры.
ALTER TABLE channel_snapshots
  ADD COLUMN source TEXT NOT NULL DEFAULT 'yt_data'
    CHECK (source IN ('yt_data','yt_analytics','vk_api','ytdlp','manual'));
UPDATE channel_snapshots cs SET source = 'ytdlp' FROM channels c WHERE c.id = cs.channel_id AND c.platform = 'vk';
