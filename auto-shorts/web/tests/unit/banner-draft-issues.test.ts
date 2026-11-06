import { expect, test } from "@playwright/test";

import { ValidationError } from "@/lib/api/errors";

import {
  DEFAULT_AD_MARKING,
  DEFAULT_TIMING,
  type EditorDraft,
  makeCodeLayer,
  makeCreativeLayer,
  normalizeSlot,
} from "@/components/banner-editor/draft";
import { describeValidationError, draftIssues, issueMessage } from "@/components/banner-editor/draft-issues";

function draft(patch: Partial<EditorDraft> = {}): EditorDraft {
  return {
    code: "A",
    name: "A",
    layers: [makeCreativeLayer("a".repeat(64)), makeCodeLayer()],
    timing: structuredClone(DEFAULT_TIMING),
    ad_marking: { ...DEFAULT_AD_MARKING },
    code_url_template: null,
    ...patch,
  };
}

test("чистый черновик — без замечаний", () => {
  expect(draftIssues(draft())).toEqual([]);
});

test("поля вне схемы — замечания по-русски с привязкой к слою", () => {
  const base = draft();
  const code = base.layers[1]!;
  const broken = draft({
    layers: [
      base.layers[0]!,
      { ...code, text: "  ", style: { ...code.style!, max_font_px: 7, min_font_px: 30, padding_x: 150 } },
    ],
    timing: { ...DEFAULT_TIMING, impulse_min_sec: 5, impulse_duration_sec: 4, fade_in_ms: 12.5 },
    ad_marking: { required: true, erid: "ё", advertiser_text: null },
  });
  const messages = draftIssues(broken).map((issue) => issue.message);
  expect(messages).toContain("Слой 2 · Код ролика: пустой текст");
  expect(messages).toContain("Слой 2 · Код ролика, кегль макс.: от 8 до 200");
  expect(messages).toContain("Слой 2 · Код ролика: кегль мин. больше кегля макс.");
  expect(messages).toContain("Слой 2 · Код ролика, поле X: от 0 до 120");
  expect(messages).toContain("«Не короче» больше длины импульса — уменьшите его или удлините импульс");
  expect(messages).toContain("Появление: нужно целое число");
  expect(messages.some((m) => m.startsWith("erid:"))).toBe(true);
  expect(draftIssues(broken).find((issue) => issue.message.includes("пустой текст"))?.layerId).toBe("code");
});

test("масштаб слота ограничен схемой (×4)", () => {
  const slot = normalizeSlot({ ...makeCreativeLayer("a".repeat(64)).slot, scale: 5.7, corner_radius_px: 12.4 });
  expect(slot.scale).toBe(4);
  expect(slot.corner_radius_px).toBe(12);
});

test("422 сервера переводится на русский с именем поля", () => {
  const error = new ValidationError({
    code: "validation_error",
    message: "Ошибка валидации запроса",
    details: {
      errors: [
        {
          loc: ["body", "draft", "layers", 1],
          msg: "Value error, layer code: kind text_code needs text",
          type: "value_error",
        },
        {
          loc: ["body", "draft", "ad_marking", "erid"],
          msg: "String should match pattern '^[A-Za-z0-9_-]{4,64}$'",
          type: "string_pattern_mismatch",
        },
        {
          loc: ["body", "draft", "layers", 0, "slot", "scale"],
          msg: "Input should be less than or equal to 4",
          type: "less_than_equal",
        },
      ],
    },
  });
  const text = describeValidationError(error, draft());
  expect(text).toBe(
    "Слой 2 · Код ролика: пустой текст; erid: недопустимые символы или формат; Слой 1 · Креатив, масштаб: не больше 4",
  );
  expect(text).not.toMatch(/[A-Za-z]{4,}.*should/);
  expect(issueMessage({ loc: ["body"], msg: "Сервер сказал по-русски", type: "x" })).toBe("Сервер сказал по-русски");
  expect(describeValidationError(new Error("x"))).toBeNull();
});
