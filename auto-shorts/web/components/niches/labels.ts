/** Подписи ниш (повторяют `LABELS` domain/enums.py) и шпаргалка по секциям конфига (domain/niche_schema.py). */
import { Archive, CircleCheck, FileWarning } from "lucide-react";

import type { StatusMetaMap } from "@/components/ui/status-badge";
import type { NicheStatus, NicheVersionSource } from "@/lib/queries/niches";

export const NICHE_STATUS_META: StatusMetaMap<NicheStatus> = {
  active: { label: "Активна", variant: "positive", icon: CircleCheck },
  archived: { label: "В архиве", variant: "neutral", icon: Archive, outline: true },
  needs_import: { label: "Нужен импорт", variant: "warning", icon: FileWarning },
};

export const NICHE_STATUS_OPTIONS: ReadonlyArray<{ value: NicheStatus; label: string }> = [
  { value: "active", label: "Активные" },
  { value: "archived", label: "Архив" },
  { value: "needs_import", label: "Нужен импорт" },
];

export const VERSION_SOURCE_LABELS: Record<NicheVersionSource, string> = {
  ui: "Редактор",
  yaml_import: "Импорт YAML",
  migration: "Миграция",
};

export interface ConfigFieldHint {
  name: string;
  hint: string;
}

export interface ConfigSectionHint {
  key: string;
  title: string;
  summary: string;
  fields: ConfigFieldHint[];
}

/**
 * Шпаргалка по `NicheConfig` (src/autoshorts/domain/niche_schema.py). Ключи — латиница snake_case,
 * тексты — по-русски; неизвестный ключ — ошибка (опечатка не теряется молча). Любая секция
 * необязательна: пропущенная берёт значения по умолчанию.
 */
export const CONFIG_SECTIONS: ConfigSectionHint[] = [
  {
    key: "script",
    title: "Сценарий",
    summary: "Формат, тон, темп, объём и заготовки хуков, CTA и словарный фильтр.",
    fields: [
      { name: "format", hint: "код формата: pick_a_card | card_of_day | horoscope_12 | generic …" },
      { name: "tone", hint: "тон текста, до 300 символов" },
      { name: "pacing", hint: "темп: slow | medium | fast (латиницей)" },
      { name: "word_count", hint: "[мин, макс] слов, 5 ≤ мин ≤ макс ≤ 1000; по умолчанию [55, 80]" },
      { name: "hooks", hint: "список {pattern, template}: pattern латиницей, template до 400 символов, можно {theme}" },
      { name: "cta_variants", hint: "варианты призыва к действию, до 50, без пустых строк" },
      {
        name: "forbidden_phrases",
        hint: "словарный фильтр, до 500 фраз; хук или CTA с запрещённой фразой — ошибка конфига",
      },
    ],
  },
  {
    key: "visuals",
    title: "Визуал",
    summary: "Стиль картинок, настроение, что показывать и чего избегать, движение и число сцен.",
    fields: [
      { name: "style", hint: "описание стиля для генерации, до 600 символов" },
      { name: "mood", hint: "настроение, до 300 символов" },
      { name: "subjects", hint: "список объектов в кадре, до 50" },
      { name: "avoid", hint: "чего не рисовать, до 50" },
      { name: "motion", hint: "crop_pan (кадрирование и панорама) | prerendered_loop" },
      { name: "scene_count", hint: "сцен на ролик, 1–20; по умолчанию 6" },
    ],
  },
  {
    key: "voice",
    title: "Голос",
    summary: "Темп и энергия озвучки, рекомендованные провайдеры.",
    fields: [
      { name: "pace", hint: "темп речи, до 100 символов" },
      { name: "energy", hint: "энергия подачи, до 300 символов" },
      {
        name: "suggested_providers",
        hint: 'словарь роль → провайдер, ключи латиницей: primary: "silero:v5_cis_base"',
      },
    ],
  },
  {
    key: "captions",
    title: "Субтитры",
    summary: "Стиль караоке-субтитров, цвета, жирность и положение.",
    fields: [
      { name: "style", hint: "код стиля; по умолчанию karaoke_word_highlight" },
      { name: "highlight_color", hint: "цвет подсветки слова #RRGGBB; по умолчанию #F2C14E" },
      { name: "base_color", hint: "основной цвет #RRGGBB; по умолчанию #FFFFFF" },
      { name: "font_weight", hint: "код жирности; по умолчанию bold" },
      { name: "position", hint: "положение: lower_third | center | top (латиницей)" },
    ],
  },
  {
    key: "music",
    title: "Музыка",
    summary: "Настроение и энергия фоновой музыки.",
    fields: [
      { name: "mood", hint: "настроение, до 300 символов" },
      { name: "energy", hint: "энергия: low | medium | high, до 100 символов" },
    ],
  },
  {
    key: "duration",
    title: "Длительность",
    summary: "Целевой диапазон ролика и бесшовный цикл.",
    fields: [
      { name: "target_sec", hint: "[мин, макс] секунд, 3 ≤ мин ≤ макс ≤ 180; по умолчанию [15, 25]" },
      { name: "loop_seam", hint: "true — конец ролика сшивается с началом (зацикливание)" },
    ],
  },
  {
    key: "thumbnail",
    title: "Обложка",
    summary: "Стиль обложки и место текста.",
    fields: [
      { name: "style", hint: "описание стиля, до 600 символов" },
      { name: "text_position", hint: "положение текста: top | center | bottom (латиницей)" },
    ],
  },
  {
    key: "hashtags",
    title: "Хештеги",
    summary: "Пул хештегов: в пакет заливки берутся per_video случайных плюс все fixed.",
    fields: [
      { name: "pool", hint: "список вида #слово без пробелов, до 100" },
      { name: "per_video", hint: "сколько брать из пула на ролик, 0–15; по умолчанию 3" },
      { name: "fixed", hint: "хештеги в каждом ролике, до 10" },
    ],
  },
  {
    key: "prompts",
    title: "Промпты LLM",
    summary: "Промпты сценария для ниши. Пусто — общий промпт стадии generate_script.",
    fields: [
      { name: "system", hint: "системный промпт, до 8000 символов" },
      { name: "body_hint", hint: "подсказка для тела сценария, до 4000 символов" },
      { name: "title_hint", hint: "подсказка для заголовка, до 2000 символов" },
    ],
  },
];
