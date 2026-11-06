SELECT as_migration_guard(20);

-- avg_pct_viewed хранит averageViewPercentage из YouTube Analytics в процентах; у Shorts он бывает больше 100
-- (ролик пересматривают). NUMERIC(6,4) вмещал только < 100 — 06.11.2026 сбор досмотров «Кадра дня» падал
-- «numeric field overflow». Расширяем без потери данных.
ALTER TABLE publications ALTER COLUMN avg_pct_viewed TYPE NUMERIC(7,2);
