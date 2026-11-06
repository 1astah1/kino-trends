-- Не миграция: идемпотентный пролог, который раннер (db/migrate.py) выполняет до файлов 0001…
-- Источник: docs/ARCHITECTURE.md §4.1. Имя начинается с «_», поэтому раннер не принимает файл
-- за версию (версии: только файлы вида NNNN_<name>.sql).
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now(), duration_ms INT,
  adopted BOOLEAN NOT NULL DEFAULT false);
CREATE OR REPLACE FUNCTION as_migration_guard(v int) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM schema_migrations WHERE version = v) THEN
    RAISE EXCEPTION 'migration % already applied', v;
  END IF;
END $$;
