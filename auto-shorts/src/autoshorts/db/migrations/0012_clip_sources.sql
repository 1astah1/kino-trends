SELECT as_migration_guard(12);

-- M5 (нарезка, контракт M5M3-0): то, что движок clipping/** производит сверх таблиц 0005.
-- Всё аддитивно: новые колонки с умолчаниями, существующие строки не теряют смысла.

-- tus 1.0.0 (M5-A): Upload-Metadata целиком (кроме filename/filetype/purpose, у них свои колонки)
-- и момент, когда получен последний байт (status -> complete).
ALTER TABLE uploads
  ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}',
  ADD COLUMN completed_at TIMESTAMPTZ;

-- Источник: каталог в MEDIA_ROOT (движок пишет туда source.json, прокси, peaks, раскадровку,
-- transcript.json.gz), полный ffprobe, сводка площадки для URL (extractor, uploader, license,
-- upload_date — сверка лицензии) и итог последнего отбора (режим llm|mock|heuristic с подписью,
-- параметры, число кандидатов, стоимость). NULL в media_dir означает sources/<id>.
ALTER TABLE sources
  ADD COLUMN media_dir TEXT CHECK (media_dir ~ '^sources/[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$'),
  ADD COLUMN probe JSONB NOT NULL DEFAULT '{}',
  ADD COLUMN platform_meta JSONB NOT NULL DEFAULT '{}',
  ADD COLUMN selection JSONB;
CREATE UNIQUE INDEX uq_sources_media_dir ON sources(media_dir) WHERE media_dir IS NOT NULL;
CREATE INDEX ix_sources_sha256 ON sources(sha256) WHERE sha256 IS NOT NULL AND deleted_at IS NULL;

-- Права: поля RightsRecord движка (source_ingest.validate_rights), которых не было в 0005,
-- и предупреждения проверки (allowlist не подтверждён, версия лицензии неизвестна, …).
-- CC BY-SA, NC и ND не принимаются (§0.1 п. 14): основная проверка — validate_rights в API,
-- здесь страховка на явные маркеры в названии и URL лицензии.
ALTER TABLE source_rights
  ADD COLUMN license TEXT CHECK (length(license) <= 200),
  ADD COLUMN license_url TEXT CHECK (length(license_url) <= 2000),
  ADD COLUMN warnings TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN updated_at TIMESTAMPTZ,
  ADD CONSTRAINT ck_source_rights_license_compatible CHECK (
    (license IS NULL OR license !~* '(by-nc|by-sa|by-nd|share-?alike|non-?commercial|no-?deriv)')
    AND (license_url IS NULL OR license_url !~* 'licenses/by-(nc|sa|nd)'));

-- Кандидаты: откуда взялся (llm | mock | heuristic | manual — UI подписывает эвристику и заглушку),
-- разблокированные оператором флаги, границы в индексах слов после снаппинга (включительно)
-- и сам результат снаппинга, заметки оператора. Повторный отбор не удаляет прежних кандидатов,
-- а ставит им superseded_at.
ALTER TABLE clip_candidates
  ADD COLUMN origin TEXT NOT NULL DEFAULT 'llm' CHECK (origin IN ('llm','mock','heuristic','manual')),
  ADD COLUMN unblocked_flags TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN start_word_idx INT CHECK (start_word_idx >= 0),
  ADD COLUMN end_word_idx INT CHECK (end_word_idx >= 0),
  ADD COLUMN snap JSONB NOT NULL DEFAULT '{}',        -- {w0, w1, lead_sec, tail_sec, raw_start_sec, raw_end_sec}
  ADD COLUMN notes TEXT CHECK (length(notes) <= 2000),
  ADD COLUMN superseded_at TIMESTAMPTZ,
  ADD COLUMN updated_by BIGINT REFERENCES users(id),
  ADD CONSTRAINT ck_clipcand_word_range CHECK (
    start_word_idx IS NULL OR end_word_idx IS NULL OR end_word_idx >= start_word_idx);
UPDATE clip_candidates SET origin = CASE
    WHEN created_by_kind = 'manual' THEN 'manual'
    WHEN features->>'origin' IN ('llm','mock','heuristic') THEN features->>'origin'
    ELSE 'llm' END;
CREATE INDEX ix_clipcand_current ON clip_candidates(source_id, final_score DESC NULLS LAST)
  WHERE superseded_at IS NULL;
