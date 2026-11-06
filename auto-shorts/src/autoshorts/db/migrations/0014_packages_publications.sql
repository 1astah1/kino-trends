SELECT as_migration_guard(14);

-- M3-A пакеты и публикации (контракт M5M3-0). Публикация ручная: инструмент готовит пакет,
-- оператор заливает сам и вставляет ссылку.

ALTER TABLE upload_packages
  -- откуда тексты: llm (OpenAI-совместимый провайдер), template (без ключа LLM — подписывается в UI),
  -- operator (правил вручную)
  ADD COLUMN texts_mode TEXT NOT NULL DEFAULT 'template' CHECK (texts_mode IN ('llm','template','operator')),
  -- чужие права (CC BY и лицензии): атрибуция обязана попасть в описание
  ADD COLUMN attribution_text TEXT,
  -- синтетический голос / картинки: пункт ai_disclosure чек-листа обязателен
  ADD COLUMN ai_disclosure_required BOOLEAN NOT NULL DEFAULT true,
  -- «залил, ссылку вставлю позже» (POST /packages/{id}/mark-uploaded)
  ADD COLUMN marked_uploaded_at TIMESTAMPTZ,
  ADD COLUMN marked_uploaded_by BIGINT REFERENCES users(id);

ALTER TABLE publications
  ADD COLUMN verified_at TIMESTAMPTZ;                -- последняя проверка принадлежности каналу
