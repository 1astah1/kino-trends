SELECT as_migration_guard(11);

-- Решение владельца от 2026-09-26 (docs/OWNER-DECISIONS.md п. 1): баннер — загруженный креатив владельца.
-- 1) banner_creatives — библиотека креативов по sha256 исходных байтов (pipeline/banner_assets.ingest_creative);
-- 2) banner_variants — вариант A/B = слои domain/banner_schema.BannerLayer (JSONB) + BannerTiming + AdMarking;
--    текстовая плашка 0003 переносится в слои (запасной fallback_plate), её колонки удаляются;
-- 3) banner_templates.layout — пресет варианта (api/schemas/banners.BannerVariantDraft), кампании он не обязателен.
-- Номер 0010 зарезервирован за уборкой legacy (M7-C), поэтому M2 начинается с 0011 (§10.1 п. 3).

CREATE TABLE banner_creatives (
  sha256 TEXT PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),   -- sha256 исходного файла = ключ в слоях варианта
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status TEXT NOT NULL DEFAULT 'processing' CHECK (status IN ('processing','ready','failed')),
  error_code TEXT CHECK (error_code IN ('empty_file','corrupt_file','unsupported_format','file_too_large',
                                        'dimensions_too_large','dimensions_too_small','animated_too_long',
                                        'too_many_frames','normalize_failed')),
  error_message TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('static','animated')),
  source_format TEXT NOT NULL
    CHECK (source_format IN ('png','jpeg','webp','gif','apng','webp_animated','webm','mp4','mov')),
  source_bytes BIGINT NOT NULL CHECK (source_bytes >= 0),
  source_width INT CHECK (source_width > 0),
  source_height INT CHECK (source_height > 0),
  upload_path TEXT,                                  -- исходник до нормализации (MEDIA_ROOT), чистится после ready/failed
  -- нормализованный файл (BannerCreative): пусто, пока status = 'processing'
  width INT CHECK (width > 0),
  height INT CHECK (height > 0),
  has_alpha BOOLEAN,
  duration_sec NUMERIC(8,3) CHECK (duration_sec >= 0),  -- один цикл анимации, 0 у статики
  fps NUMERIC(7,3) CHECK (fps >= 0),
  frame_count INT CHECK (frame_count >= 1),
  loop BOOLEAN,
  normalized_path TEXT,                              -- banners/creatives/<sha[:2]>/<sha>/creative.png|creative.webm
  normalized_mime TEXT,
  normalized_bytes BIGINT,
  thumb_path TEXT,
  first_frame_path TEXT,
  normalizer_version INT,
  meta JSONB NOT NULL DEFAULT '{}',                  -- {original_filename, notes: [...] из нормализатора}
  product_id TEXT REFERENCES products(id),           -- фильтр библиотеки, необязательно
  notes TEXT,
  task_id BIGINT REFERENCES tasks(id) ON DELETE SET NULL,  -- нормализация анимации в тяжёлой очереди
  created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ,                            -- мягкое удаление; файлы убирает cleanup, когда ссылок нет
  CONSTRAINT ck_banner_creatives_ready CHECK (status <> 'ready' OR (
    width IS NOT NULL AND height IS NOT NULL AND has_alpha IS NOT NULL AND duration_sec IS NOT NULL
    AND fps IS NOT NULL AND frame_count IS NOT NULL AND loop IS NOT NULL AND normalized_path IS NOT NULL
    AND normalized_mime IS NOT NULL AND thumb_path IS NOT NULL AND first_frame_path IS NOT NULL
    AND normalizer_version IS NOT NULL)),
  CONSTRAINT ck_banner_creatives_failed CHECK (status <> 'failed' OR error_code IS NOT NULL));
CREATE INDEX ix_banner_creatives_live ON banner_creatives(created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX ix_banner_creatives_product ON banner_creatives(product_id) WHERE deleted_at IS NULL;

ALTER TABLE banner_variants
  ADD COLUMN layers JSONB NOT NULL DEFAULT '[]',     -- [BannerLayer]: creative (creative_sha256) / text_code / text_erid / micro_brand / fallback_plate
  ADD COLUMN timing JSONB NOT NULL DEFAULT '{}',     -- BannerTiming; {} = значения по умолчанию (импульс 40–65% + финальный повтор)
  ADD COLUMN code_url_template TEXT,                 -- «u.umka.chat/{code}»; NULL = <products.redirect_host>/{code}
  ADD COLUMN notes TEXT,
  ADD COLUMN version INT NOT NULL DEFAULT 1,         -- expected_version автосохранения редактора
  ADD COLUMN created_by BIGINT REFERENCES users(id),
  ADD CONSTRAINT ck_banner_variants_layers CHECK (jsonb_typeof(layers) = 'array'),
  ADD CONSTRAINT ck_banner_variants_timing CHECK (jsonb_typeof(timing) = 'object'),
  ADD CONSTRAINT ck_banner_variants_code_url CHECK (code_url_template IS NULL OR strpos(code_url_template, '{code}') > 0);

-- Перенос текстовой плашки 0003 в слои (геометрия domain/banner_schema.default_banner_variant без креатива):
-- micro_brand_text -> слой micro_brand, offer_text + offer_text_2 -> запасная плашка fallback_plate
-- (style_overrides -> её стиль), show_code_in_frame -> слой text_code «{code_url}» под плашкой.
-- layout_overrides (частичная геометрия поверх шаблона) не переносится: форма слоёв другая.
UPDATE banner_variants v SET layers = (
  SELECT COALESCE(jsonb_agg(s.layer ORDER BY s.ord), '[]'::jsonb)
  FROM (
    SELECT 1 AS ord, jsonb_build_object(
             'id', 'micro_brand', 'kind', 'micro_brand', 'show', 'persistent', 'z', 0,
             'text', btrim(v.micro_brand_text),
             'slot', jsonb_build_object('x', 65, 'y', 200, 'w', 440, 'h', 58, 'anchor', 'top_left', 'opacity', 0.75),
             'style', jsonb_build_object('font_file', 'Montserrat-SemiBold.ttf', 'max_font_px', 44, 'min_font_px', 36,
                                         'bg_opacity', 0.35, 'padding_x', 14, 'padding_y', 6, 'align', 'left')) AS layer
     WHERE COALESCE(btrim(v.micro_brand_text), '') <> ''
    UNION ALL
    SELECT 2, jsonb_build_object(
             'id', 'fallback_plate', 'kind', 'fallback_plate', 'show', 'with_creative', 'z', 10,
             'text', left(concat_ws(' ', NULLIF(btrim(v.offer_text), ''), NULLIF(btrim(v.offer_text_2), '')), 400),
             'slot', jsonb_build_object('x', 65, 'y', 1120, 'w', 825, 'h', 190, 'anchor', 'bottom_center'),
             'style', jsonb_build_object('font_file', 'Montserrat-Bold.ttf', 'max_font_px', 66, 'min_font_px', 44,
                                         'bg_opacity', 0.7, 'padding_x', 28, 'padding_y', 18, 'corner_radius_px', 14,
                                         'max_lines', 2, 'hug_text', false)
                      || jsonb_strip_nulls(jsonb_build_object(
                           'bg_opacity', v.style_overrides->'background_opacity',
                           'bg_color', v.style_overrides->'bg_color',
                           'color', v.style_overrides->'text_color',
                           'corner_radius_px', v.style_overrides->'corner_radius_px')))
     WHERE concat_ws(' ', NULLIF(btrim(v.offer_text), ''), NULLIF(btrim(v.offer_text_2), '')) <> ''
    UNION ALL
    SELECT 3, jsonb_build_object(
             'id', 'code', 'kind', 'text_code', 'show', 'with_creative', 'z', 20,
             'text', '{code_url}', 'attach', 'below_creative',
             'slot', jsonb_build_object('x', 65, 'y', 1316, 'w', 825, 'h', 50, 'anchor', 'top_center'),
             'style', jsonb_build_object('font_file', 'Montserrat-Bold.ttf', 'max_font_px', 36, 'min_font_px', 24,
                                         'bg_opacity', 0.6))
     WHERE v.show_code_in_frame
  ) s)
WHERE jsonb_array_length(v.layers) = 0;

-- ad_marking 0003 {enabled, erid, advertiser_text} -> domain AdMarking {required, erid, advertiser_text}.
-- Только строки старой формы: ручной повтор `psql -f` без ON_ERROR_STOP (guard лишь печатает ERROR)
-- не должен сбросить required=true у уже переведённых вариантов.
UPDATE banner_variants SET ad_marking = jsonb_build_object(
  'required', COALESCE((ad_marking->>'enabled')::boolean, false),
  'erid', CASE WHEN ad_marking->>'erid' ~ '^[A-Za-z0-9_-]{4,64}$' THEN ad_marking->'erid' ELSE 'null'::jsonb END,
  'advertiser_text', CASE WHEN jsonb_typeof(ad_marking->'advertiser_text') = 'string'
                          THEN to_jsonb(left(ad_marking->>'advertiser_text', 120)) ELSE 'null'::jsonb END)
WHERE NOT (ad_marking ? 'required');

ALTER TABLE banner_variants
  DROP COLUMN micro_brand_text,
  DROP COLUMN offer_text,
  DROP COLUMN offer_text_2,
  DROP COLUMN style_overrides,
  DROP COLUMN layout_overrides,
  DROP COLUMN show_code_in_frame;
-- «на какие креативы ссылаются варианты»: layers @> '[{"creative_sha256": "<sha>"}]'
CREATE INDEX ix_banner_variants_layers ON banner_variants USING gin (layers jsonb_path_ops);

-- Кампания без шаблона: вариант самодостаточен, шаблон — только пресет для новых вариантов.
ALTER TABLE campaigns ALTER COLUMN banner_template_id DROP NOT NULL;

COMMENT ON TABLE banner_creatives IS 'Загруженные креативы баннера (решение владельца 2026-09-26), ключ sha256 исходника';
COMMENT ON COLUMN banner_variants.layers IS 'domain/banner_schema.BannerLayer[]; креатив по creative_sha256 -> banner_creatives';
COMMENT ON COLUMN banner_variants.ad_marking IS 'domain/banner_schema.AdMarking {required, erid, advertiser_text}';
COMMENT ON COLUMN banner_templates.layout IS 'Пресет варианта: api/schemas/banners.BannerVariantDraft {code, name, layers, timing, ad_marking, code_url_template}';
