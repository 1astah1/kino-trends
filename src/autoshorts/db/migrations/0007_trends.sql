SELECT as_migration_guard(7);

ALTER TABLE competitor_channels
  ADD COLUMN title TEXT, ADD COLUMN url TEXT, ADD COLUMN handle TEXT,
  ADD COLUMN niche_id TEXT REFERENCES niches(id),
  ADD COLUMN uploads_playlist_id TEXT,               -- UU…
  ADD COLUMN shorts_playlist_id TEXT,                -- UUSH… (недокументировано, с fallback)
  ADD COLUMN discovery_mode TEXT NOT NULL DEFAULT 'auto' CHECK (discovery_mode IN ('auto','rss','uush','uu')),
  ADD COLUMN last_discovery_at TIMESTAMPTZ, ADD COLUMN last_error TEXT,
  ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE competitor_videos (
  id BIGSERIAL PRIMARY KEY,
  competitor_id TEXT NOT NULL REFERENCES competitor_channels(id),
  platform TEXT NOT NULL, external_video_id TEXT NOT NULL,
  title TEXT, published_at TIMESTAMPTZ, duration_sec NUMERIC(8,2), is_short BOOLEAN,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  watch_until TIMESTAMPTZ,                           -- published + 7d
  next_poll_at TIMESTAMPTZ, poll_step SMALLINT NOT NULL DEFAULT 0,
  latest_views BIGINT, views_24h BIGINT, views_72h BIGINT,
  outlier_score NUMERIC(8,3), outlier_z NUMERIC(8,3),
  outlier_tier TEXT CHECK (outlier_tier IN ('noise','attention','format_works')),
  status TEXT NOT NULL DEFAULT 'watching' CHECK (status IN ('watching','done','analyzed','ignored')),
  thumb_path TEXT, analyzed_at TIMESTAMPTZ,
  UNIQUE (platform, external_video_id));
CREATE INDEX ix_cv_poll ON competitor_videos(next_poll_at) WHERE status = 'watching';
CREATE INDEX ix_cv_outlier ON competitor_videos(outlier_score DESC NULLS LAST);
CREATE INDEX ix_cv_competitor ON competitor_videos(competitor_id, published_at DESC);

CREATE TABLE competitor_baselines (                  -- медиана канала на том же возрасте
  competitor_id TEXT NOT NULL REFERENCES competitor_channels(id),
  age_bucket TEXT NOT NULL CHECK (age_bucket IN ('1h','3h','6h','12h','24h','48h','72h','7d','mature')),
  median_views NUMERIC NOT NULL, n INT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (competitor_id, age_bucket));

ALTER TABLE competitor_video_snapshots ADD COLUMN competitor_video_id BIGINT REFERENCES competitor_videos(id);
INSERT INTO competitor_videos (competitor_id, platform, external_video_id, status)
  SELECT DISTINCT s.competitor_id, c.platform, s.external_video_id, 'done'
  FROM competitor_video_snapshots s JOIN competitor_channels c ON c.id = s.competitor_id
  ON CONFLICT DO NOTHING;
UPDATE competitor_video_snapshots s SET competitor_video_id = v.id
  FROM competitor_videos v
  WHERE v.external_video_id = s.external_video_id AND v.competitor_id = s.competitor_id;
CREATE INDEX ix_cvs_video_time ON competitor_video_snapshots(competitor_video_id, captured_at);

ALTER TABLE trend_briefs
  ADD COLUMN competitor_video_id BIGINT REFERENCES competitor_videos(id),
  ADD COLUMN niche_id TEXT REFERENCES niches(id),
  ADD COLUMN status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','accepted','rejected','used')),
  ADD COLUMN summary TEXT, ADD COLUMN hook_text TEXT,
  ADD COLUMN topic_suggestions JSONB NOT NULL DEFAULT '[]',
  ADD COLUMN adaptation_ideas JSONB NOT NULL DEFAULT '[]',
  ADD COLUMN structure JSONB NOT NULL DEFAULT '{}',
  ADD COLUMN analysis_sources TEXT[] NOT NULL DEFAULT '{}',  -- metadata_llm, subtitles, scenedetect, vision
  ADD COLUMN cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
  ADD COLUMN used_count INT NOT NULL DEFAULT 0,
  ADD COLUMN notes TEXT,
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
UPDATE trend_briefs SET status = CASE WHEN accepted IS TRUE THEN 'accepted'
                                      WHEN accepted IS FALSE THEN 'rejected' ELSE 'new' END;
CREATE INDEX ix_briefs_status ON trend_briefs(status, created_at DESC);

CREATE TABLE brief_field_stats (
  computed_at TIMESTAMPTZ NOT NULL, niche_id TEXT NOT NULL DEFAULT 'all',
  field TEXT NOT NULL, bucket TEXT NOT NULL,
  n INT NOT NULL, median_outlier NUMERIC, spearman_rho NUMERIC, ci_low NUMERIC, ci_high NUMERIC,
  PRIMARY KEY (computed_at, niche_id, field, bucket));
