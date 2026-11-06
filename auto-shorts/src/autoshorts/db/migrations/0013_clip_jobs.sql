SELECT as_migration_guard(13);

-- M5 clip-job (контракт M5M3-0): авторский слой, голос автора и отчёт об оригинальности.
-- jobs.author_layer (0005) остаётся вводом оператора {mode, voice, captions, hook_text, title_overlay,
-- script}; здесь — то, что получилось на стадиях author_script / author_voice / qc.

ALTER TABLE jobs
  -- предложения транскрипта клипа: повтор момента в отчёте об оригинальности (Jaccard по sid)
  ADD COLUMN clip_start_sid INT CHECK (clip_start_sid >= 0),
  ADD COLUMN clip_end_sid INT CHECK (clip_end_sid >= 0),
  -- AuthorScriptResult.to_dict() + {version, updated_at, updated_by}: сценарий, режим
  -- llm | mock | heuristic | operator и подпись, needs_operator_edit, blocked, issues, warnings
  ADD COLUMN author_script JSONB,
  ADD COLUMN author_script_version INT NOT NULL DEFAULT 0,   -- expected_version для PUT
  -- по репликам: {segments: {cold_open: {source, artifact_id, duration_sec, timing, words_count,
  --   uploaded_at, uploaded_by, mime, status}}}; файлы — artifacts kind=author_voice, idx 0..3
  ADD COLUMN author_voice JSONB NOT NULL DEFAULT '{}',
  -- OriginalityReport.to_dict() финального рендера (стадия qc) + {computed_at}
  ADD COLUMN originality JSONB,
  ADD COLUMN originality_verdict TEXT CHECK (originality_verdict IN ('pass','warn','fail')),
  ADD CONSTRAINT ck_jobs_clip_sid_range CHECK (
    clip_start_sid IS NULL OR clip_end_sid IS NULL OR clip_end_sid >= clip_start_sid);
CREATE INDEX ix_jobs_originality ON jobs(originality_verdict)
  WHERE originality_verdict IS NOT NULL AND deleted_at IS NULL;
