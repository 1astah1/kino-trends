import { expect, test } from "@playwright/test";

// @ts-expect-error — .mjs без деклараций типов, проверяем поведение
import { scanText } from "../../scripts/check-no-external.mjs";

type Finding = { url: string; category: string; loading: boolean };
const categories = (text: string, allow: string[] = []) =>
  (scanText(text, allow) as Finding[]).map((f) => `${f.category}:${f.url}`);

test.describe("check-no-external (§7.7 п. 5)", () => {
  test("шрифт и скрипт с CDN — нарушения", () => {
    expect(categories('<link href="https://fonts.googleapis.com/css2?family=Onest">')).toEqual([
      "violation:https://fonts.googleapis.com/css2?family=Onest",
    ]);
    expect(categories("@font-face{src:url(https://fonts.gstatic.com/s/onest.woff2)}")).toEqual([
      "violation:https://fonts.gstatic.com/s/onest.woff2",
    ]);
    expect(categories('<script src="//cdn.jsdelivr.net/npm/swagger-ui"></script>')).toEqual([
      "violation://cdn.jsdelivr.net/npm/swagger-ui",
    ]);
    expect(categories('fetch("https://mc.yandex.ru/watch/1")')).toEqual(["violation:https://mc.yandex.ru/watch/1"]);
  });

  test("пространства имён, ссылки из текстов ошибок, заглушки и свои домены — не нарушения", () => {
    expect(categories('<svg xmlns="http://www.w3.org/2000/svg">')).toEqual(["namespace:http://www.w3.org/2000/svg"]);
    expect(categories('"see https://react.dev/errors/418"')).toEqual(["inert-doc:https://react.dev/errors/418"]);
    expect(categories('new URL("https://a@b")')).toEqual(["placeholder:https://a@b"]);
    expect(categories("new URL(`http://${host}`)")).toEqual(["placeholder:http://$"]);
    expect(categories('<a href="https://u.umka.chat/k7m2q">', ["umka.chat"])).toEqual([
      "own:https://u.umka.chat/k7m2q",
    ]);
  });

  test("ссылка на документацию в загружающем контексте — всё равно нарушение", () => {
    expect(categories('<script src="https://nextjs.org/docs/x.js">')).toEqual([
      "violation:https://nextjs.org/docs/x.js",
    ]);
  });
});
