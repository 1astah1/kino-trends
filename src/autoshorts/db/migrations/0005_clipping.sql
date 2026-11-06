SELECT as_migration_guard(5);

CREATE TABLE uploads (                               -- tus-сессии
  id TEXT PRIMARY KEY CHECK (id ~ '^[a-f0-9]{32}$'),
  user_id BIGINT NOT NULL REFERENCES users(id),
  purpose TEXT NOT NULL CHECK (purpose IN ('source','asset','rights_doc')),
  filename TEXT NOT NULL, filetype TEXT,
  length_bytes BIGINT NOT NULL, received_bytes BIGINT NOT NULL DEFAULT 0,
  path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'uploading'
    CHECK (status IN ('uploading','complete','consumed','expired','aborted')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL);
CREATE INDEX ix_uploads_expiry ON uploads(expires_at) WHERE status IN ('uploading','complete');

CREATE TABLE sources (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('upload','url')),
  origin_url TEXT, upload_id TEXT REFERENCES uploads(id),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
    ('queued','downloading','preparing','transcribing','selecting','ready','failed','archived')),
  error TEXT,
  language TEXT NOT NULL DEFAULT 'ru' CHECK (language ~ '^[a-z]{2}$'),
  niche_id TEXT REFERENCES niches(id),
  duration_sec NUMERIC(9,2), width INT, height INT, fps NUMERIC(6,3), video_codec TEXT,
  has_audio BOOLEAN, file_bytes BIGINT, sha256 TEXT,
  original_path TEXT, proxy_path TEXT, audio_path TEXT, peaks_path TEXT,
  storyboard JSONB,                                  -- {sheets, cols, rows, tile_w, tile_h, interval_sec}
  scenes JSONB,                                      -- [[start,end],...]; по окнам кандидатов или целиком по запросу
  watermark_suspect BOOLEAN NOT NULL DEFAULT false, watermark_detail JSONB,
  options JSONB NOT NULL DEFAULT '{}',               -- {asr_provider, target_clip_sec, max_candidates}
  original_retain_until DATE, proxy_retain_until DATE,
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ,
  CHECK ((origin = 'url' AND origin_url IS NOT NULL) OR (origin = 'upload' AND upload_id IS NOT NULL)));
CREATE INDEX ix_sources_status ON sources(status) WHERE deleted_at IS NULL;

CREATE TABLE source_rights (                         -- гейт: без записи источник не создаётся
  source_id BIGINT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
  rights_type TEXT NOT NULL CHECK (rights_type IN
    ('own','licensed_written','licensed_platform','cc_by','public_domain')),
  owner_name TEXT NOT NULL,
  owner_channel_url TEXT,
  proof_note TEXT NOT NULL CHECK (length(proof_note) >= 3),   -- что именно разрешено, ссылка на переписку
  proof_file_path TEXT,                              -- MEDIA_ROOT/rights/<source_id>/…
  allowlist_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (allowlist_status IN ('not_needed','requested','allowlisted','unknown')),
  attribution_text TEXT,                             -- обязательна при rights_type <> 'own'
  valid_until DATE,
  recorded_by BIGINT REFERENCES users(id),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (rights_type = 'own' OR attribution_text IS NOT NULL));

ALTER TABLE tasks ADD CONSTRAINT fk_tasks_source FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE;
ALTER TABLE artifacts ADD CONSTRAINT fk_artifacts_source FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE;
ALTER TABLE generation_events ADD CONSTRAINT fk_genev_source FOREIGN KEY (source_id) REFERENCES sources(id);
CREATE INDEX ix_tasks_source ON tasks(source_id, created_at) WHERE source_id IS NOT NULL;

CREATE TABLE transcripts (
  id BIGSERIAL PRIMARY KEY,
  source_id BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  provider_key TEXT NOT NULL, model TEXT, language TEXT NOT NULL,
  path TEXT NOT NULL,                                -- transcript.json.gz: {words:[{w,s,e,sid}], sentences:[{sid,s,e,text}], vad:[[s,e]]}
  words_count INT NOT NULL, sentences_count INT NOT NULL,
  duration_sec NUMERIC(9,2), rtfx NUMERIC(8,2),
  cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
  is_current BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX uq_transcripts_current ON transcripts(source_id) WHERE is_current;

CREATE TABLE clip_candidates (
  id BIGSERIAL PRIMARY KEY,
  source_id BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  start_sid INT, end_sid INT, hook_sid INT,          -- ID предложений от LLM (NULL для ручных)
  start_sec NUMERIC(9,2) NOT NULL, end_sec NUMERIC(9,2) NOT NULL CHECK (end_sec > start_sec),
  title TEXT, hook_text TEXT, hook_type TEXT, reason TEXT, transcript_excerpt TEXT,
  llm_scores JSONB NOT NULL DEFAULT '{}',            -- {hook, standalone, payoff, emotion, niche_fit, novelty}
  features JSONB NOT NULL DEFAULT '{}',              -- {speech_rate_wps, dead_air_ratio, first_word_latency, …}
  trend_prior NUMERIC(4,3), final_score NUMERIC(4,3),
  content_flags TEXT[] NOT NULL DEFAULT '{}',        -- medical_claim, guarantee_claim, profanity, …
  needs_context BOOLEAN NOT NULL DEFAULT false,
  status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','dismissed','used')),
  created_by_kind TEXT NOT NULL DEFAULT 'auto' CHECK (created_by_kind IN ('auto','manual')),
  operator_edits JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ix_clipcand_source ON clip_candidates(source_id, final_score DESC NULLS LAST);

ALTER TABLE jobs
  ADD COLUMN source_id BIGINT REFERENCES sources(id),
  ADD COLUMN clip_candidate_id BIGINT REFERENCES clip_candidates(id),
  ADD COLUMN clip_start_sec NUMERIC(9,2), ADD COLUMN clip_end_sec NUMERIC(9,2),
  ADD COLUMN reframe_mode TEXT CHECK (reframe_mode IN ('auto','track','center','fit_blur')),
  ADD COLUMN author_layer JSONB,                     -- ввод оператора или черновик LLM (§6.3.8)
  ADD COLUMN author_share NUMERIC(4,3);
ALTER TABLE clip_candidates ADD COLUMN job_id BIGINT REFERENCES jobs(id);
CREATE INDEX ix_jobs_source ON jobs(source_id) WHERE source_id IS NOT NULL;
