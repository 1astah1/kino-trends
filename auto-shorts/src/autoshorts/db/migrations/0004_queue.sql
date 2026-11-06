SELECT as_migration_guard(4);

ALTER TABLE jobs RENAME COLUMN niche TO niche_id;
ALTER TABLE jobs
  ADD COLUMN kind TEXT NOT NULL DEFAULT 'generate' CHECK (kind IN ('generate','clip')),
  ADD COLUMN stage TEXT,
  ADD COLUMN topic TEXT,
  ADD COLUMN product_id TEXT REFERENCES products(id),
  ADD COLUMN campaign_id BIGINT REFERENCES campaigns(id),
  ADD COLUMN banner_variant_id BIGINT REFERENCES banner_variants(id),
  ADD COLUMN trend_brief_id BIGINT REFERENCES trend_briefs(id),
  ADD COLUMN niche_version INT,
  ADD COLUMN config_snapshot JSONB NOT NULL DEFAULT '{}', -- ниша+шаблон+вариант+провайдеры+пресет на момент старта
  ADD COLUMN duration_sec NUMERIC(7,2),
  ADD COLUMN cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,   -- кеш суммы generation_events
  ADD COLUMN priority SMALLINT NOT NULL DEFAULT 100,
  ADD COLUMN scheduled_for TIMESTAMPTZ,
  ADD COLUMN parent_job_id BIGINT REFERENCES jobs(id),    -- VK-перерендер, клон с правками
  ADD COLUMN qc_failed_checks TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN qc_warnings JSONB NOT NULL DEFAULT '[]',
  ADD COLUMN review_comment TEXT,
  ADD COLUMN rework_from_stage TEXT,
  ADD COLUMN error_class TEXT, ADD COLUMN error TEXT,
  ADD COLUMN fingerprint JSONB,                           -- dHash ключевых кадров
  ADD COLUMN created_by BIGINT REFERENCES users(id),
  ADD COLUMN deleted_at TIMESTAMPTZ,
  ADD CONSTRAINT fk_jobs_niche FOREIGN KEY (niche_id) REFERENCES niches(id);

-- нормализация смешанного reject_reason (QC-склейки, 'rework_requested', 'tehnicheskiy_sboy')
UPDATE jobs SET qc_failed_checks = ARRAY[reject_reason], reject_reason = NULL
  WHERE status = 'qc_failed' AND reject_reason IS NOT NULL;
UPDATE jobs SET reject_reason = NULL WHERE reject_reason = 'rework_requested';
UPDATE jobs SET status = 'failed', error_class = 'legacy_max_attempts', reject_reason = NULL
  WHERE status = 'rejected' AND reject_reason = 'tehnicheskiy_sboy';
-- старые статусы -> (status, stage)
UPDATE jobs SET
  stage = CASE status WHEN 'queued' THEN 'script' WHEN 'script_ready' THEN 'voice'
                      WHEN 'voiced' THEN 'visuals' WHEN 'rendered' THEN 'qc' ELSE NULL END,
  status = CASE status WHEN 'script_ready' THEN 'processing' WHEN 'voiced' THEN 'processing'
                       WHEN 'rendered' THEN 'processing' WHEN 'qc_passed' THEN 'pending_review'
                       WHEN 'ready_to_publish' THEN 'approved' ELSE status END;
ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status CHECK (status IN
  ('draft','queued','processing','qc_failed','pending_review','rework','approved',
   'published','rejected','failed','cancelled'));
CREATE INDEX ix_jobs_status_updated ON jobs(status, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX ix_jobs_campaign ON jobs(campaign_id);
CREATE INDEX ix_jobs_variant ON jobs(banner_variant_id);
CREATE INDEX ix_jobs_product ON jobs(product_id);
CREATE INDEX ix_jobs_created ON jobs(created_at DESC);

CREATE TABLE tasks (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,                                -- job_stage, source_prepare, metrics_poll, …
  resource_class TEXT NOT NULL CHECK (resource_class IN ('heavy','light')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  priority SMALLINT NOT NULL DEFAULT 100,            -- меньше = раньше
  run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
  job_id BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
  source_id BIGINT,                                  -- FK в 0005
  stage TEXT,
  payload JSONB NOT NULL DEFAULT '{}',
  dedup_key TEXT,
  attempts INT NOT NULL DEFAULT 0, max_attempts INT NOT NULL DEFAULT 3,
  locked_by TEXT, locked_at TIMESTAMPTZ, heartbeat_at TIMESTAMPTZ,
  cancel_requested BOOLEAN NOT NULL DEFAULT false,
  progress NUMERIC(5,2),
  started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
  error_class TEXT, last_error TEXT,                  -- хвост 4 КБ; полный лог MEDIA_ROOT/logs/tasks/<id>.log
  defer_reason TEXT,                                  -- disk_low | load_high | mem_low | budget_exhausted | quota_exhausted
  result JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX uq_tasks_dedup_active ON tasks(dedup_key) WHERE status IN ('queued','running');
CREATE INDEX ix_tasks_pick ON tasks(resource_class, priority, run_after, id) WHERE status = 'queued';
CREATE INDEX ix_tasks_running ON tasks(heartbeat_at) WHERE status = 'running';
CREATE INDEX ix_tasks_job ON tasks(job_id, created_at);
CREATE INDEX ix_tasks_finished ON tasks(finished_at) WHERE status IN ('succeeded','failed','cancelled');

CREATE TABLE schedules (
  name TEXT PRIMARY KEY CHECK (name ~ '^[a-z0-9_]{2,63}$'),
  task_kind TEXT NOT NULL,
  resource_class TEXT NOT NULL CHECK (resource_class IN ('heavy','light')),
  payload JSONB NOT NULL DEFAULT '{}',
  interval_sec INT NOT NULL CHECK (interval_sec >= 30),
  anchor_local_time TIME,                            -- для суточных: время в business_timezone
  next_run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  enabled BOOLEAN NOT NULL DEFAULT true,
  last_task_id BIGINT);

CREATE TABLE workers (
  id TEXT PRIMARY KEY,                               -- <host>-<pid>-<class>
  resource_class TEXT NOT NULL, hostname TEXT NOT NULL, pid INT NOT NULL, version TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(), heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  current_task_id BIGINT,
  state TEXT NOT NULL DEFAULT 'idle');               -- idle | busy | deferred:<reason> | stopping

CREATE TABLE artifacts (
  id BIGSERIAL PRIMARY KEY,
  job_id BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
  source_id BIGINT,                                  -- FK в 0005
  kind TEXT NOT NULL,  -- script_json words_json voice scene_image scenes_video banner_png subtitles_ass
                       -- final preview poster author_voice edl_json crop_cmd qc_report
  idx SMALLINT NOT NULL DEFAULT 0,
  path TEXT NOT NULL,                                -- относительно MEDIA_ROOT
  bytes BIGINT, sha256 TEXT,
  input_hash TEXT NOT NULL,                          -- hash(video_uid, stage, входы, template_version)
  stage TEXT, is_master BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), deleted_at TIMESTAMPTZ,
  CHECK (job_id IS NOT NULL OR source_id IS NOT NULL));
CREATE UNIQUE INDEX uq_artifacts_live ON artifacts(job_id, kind, idx, input_hash)
  WHERE deleted_at IS NULL AND job_id IS NOT NULL;
CREATE INDEX ix_artifacts_job_kind ON artifacts(job_id, kind) WHERE deleted_at IS NULL;

CREATE TABLE review_decisions (
  id BIGSERIAL PRIMARY KEY,
  job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  decision TEXT NOT NULL CHECK (decision IN ('approve','rework','reject','override_qc','undo')),
  reject_reason TEXT, rework_from_stage TEXT, comment TEXT,
  undoes_id BIGINT REFERENCES review_decisions(id),
  decided_by BIGINT REFERENCES users(id),
  decided_via TEXT NOT NULL CHECK (decided_via IN ('web','telegram','api')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ix_review_job ON review_decisions(job_id, created_at);

-- незавершённые задачи старого воркера -> новая очередь
INSERT INTO tasks (kind, resource_class, job_id, stage, dedup_key, priority)
  SELECT 'job_stage', CASE WHEN stage IN ('render','qc') THEN 'heavy' ELSE 'light' END, id, stage,
         'job:' || id || ':' || stage || ':0', 50
  FROM jobs WHERE status IN ('queued','processing') AND stage IS NOT NULL;

ALTER TABLE budget_state
  ADD COLUMN reserved_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
  ADD COLUMN alert_threshold_pct SMALLINT NOT NULL DEFAULT 80,
  ADD COLUMN pause_reason TEXT, ADD COLUMN alerted_at TIMESTAMPTZ;

ALTER TABLE generation_events
  ALTER COLUMN job_id DROP NOT NULL,
  ADD COLUMN task_id BIGINT REFERENCES tasks(id),
  ADD COLUMN source_id BIGINT,                       -- FK в 0005
  ADD COLUMN trend_brief_id BIGINT REFERENCES trend_briefs(id),
  ADD COLUMN status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error')),
  ADD COLUMN error TEXT,
  ADD COLUMN cpu_ms INT,
  ADD CONSTRAINT ck_genev_subject CHECK (job_id IS NOT NULL OR task_id IS NOT NULL
                                         OR source_id IS NOT NULL OR trend_brief_id IS NOT NULL);
CREATE INDEX ix_genev_provider_time ON generation_events(provider, created_at);
