SELECT as_migration_guard(19);

-- Автозалив своих роликов на свои каналы через YouTube Data API (решение владельца 2026-09-27).
-- Ручной путь (пакет → оператор заливает → вставляет ссылку) остаётся: автозалив включается на канале
-- (`publishing_plan.mode = auto`) или кнопкой у пакета. Регистрация публикации — тот же
-- `services.publications.register`, что и у ручной ссылки.

ALTER TABLE upload_packages
  ADD COLUMN autopublish_state TEXT
    CHECK (autopublish_state IN ('scheduled','uploading','done','failed','cancelled')),
  ADD COLUMN autopublish_at TIMESTAMPTZ,              -- слот заливки (UTC)
  ADD COLUMN autopublish_privacy TEXT CHECK (autopublish_privacy IN ('public','unlisted','private')),
  ADD COLUMN autopublish_attempts SMALLINT NOT NULL DEFAULT 0 CHECK (autopublish_attempts >= 0),
  ADD COLUMN autopublish_error TEXT CHECK (length(autopublish_error) <= 2000),
  -- resumable-сессия YouTube: после обрыва заливка продолжается с места, а не с нуля
  ADD COLUMN autopublish_session TEXT,
  -- id ролика на площадке сразу после заливки: повтор не зальёт второй раз, только дорегистрирует
  ADD COLUMN autopublish_video_id TEXT,
  ADD COLUMN autopublish_by BIGINT REFERENCES users(id);
CREATE INDEX ix_pkg_autopublish_due ON upload_packages(autopublish_at)
  WHERE autopublish_state = 'scheduled';
CREATE INDEX ix_pkg_autopublish_channel ON upload_packages(channel_id, autopublish_at)
  WHERE autopublish_state IS NOT NULL;

-- OAuth канала теперь и для заливки: чей это канал по ответу Google и когда токен последний раз обновлялся.
ALTER TABLE channel_oauth
  ADD COLUMN google_channel_id TEXT,
  ADD COLUMN google_channel_title TEXT,
  ADD COLUMN granted_by BIGINT REFERENCES users(id);

INSERT INTO schedules (name, task_kind, resource_class, interval_sec, anchor_local_time, payload) VALUES
 ('autopublish_tick','autopublish_tick','light',60,NULL,'{}')   -- слоты автозалива и созревшие заливки
ON CONFLICT (name) DO NOTHING;
