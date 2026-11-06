import { deflateSync } from "node:zlib";

import { expect, type Page, test } from "@playwright/test";

/**
 * Редактор баннера (docs/ARCHITECTURE.md §10.2, критерий M2; решение владельца от 26.09.2026:
 * баннер — загруженный креатив):
 *   креатив ниже y=1500 → предупреждение `creative_in_bottom_dead_zone`;
 *   «Кадр из рендера» возвращает PNG.
 *
 * Сценарий целиком через интерфейс: кампания в карточке продукта → «Вариант из креатива» →
 * загрузка PNG → вариант со слоями по умолчанию в редакторе → Y слота 1560 → проверка сервера →
 * «Кадр из рендера». Плюс отказ файла с кодом по-русски.
 *
 * Стенд: E2E_BASE_URL, E2E_LOGIN, E2E_PASSWORD (оператор или владелец), продукт E2E_PRODUCT (umka).
 * Без стенда — сборка на MSW-моках (`npm run e2e:mock`), admin / admin-demo-password.
 * Тест оставляет на стенде кампанию «E2E баннер …» — в конце она переводится в «Завершена».
 */
const LOGIN = process.env.E2E_LOGIN ?? "admin";
const PASSWORD = process.env.E2E_PASSWORD ?? "admin-demo-password";
const PRODUCT = process.env.E2E_PRODUCT ?? "umka";
/** Стенд: уборка кампаний нужна. На MSW-моках `page.goto` перезагружает страницу и мок-данные в памяти — убирать нечего. */
const STAND = Boolean(process.env.E2E_BASE_URL);

// ---------------------------------------------------------------- PNG без зависимостей

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buffer: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

/** Сплошной RGB PNG `w×h` (уникальный цвет — чтобы файл не совпал с уже загруженным). */
function solidPng(w: number, h: number, [r, g, b]: [number, number, number]): Buffer {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(w, 0);
  header.writeUInt32BE(h, 4);
  header[8] = 8; // глубина
  header[9] = 2; // RGB
  const row = Buffer.alloc(1 + w * 3);
  for (let x = 0; x < w; x += 1) {
    row[1 + x * 3] = r;
    row[2 + x * 3] = g;
    row[3 + x * 3] = b;
  }
  const raw = Buffer.concat(Array.from({ length: h }, () => row));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

// ---------------------------------------------------------------- помощники

function watchProblems(page: Page, baseURL: string | undefined) {
  const problems: string[] = [];
  const ownHost = new URL(baseURL ?? "http://127.0.0.1:18301").host;
  page.on("console", (message) => {
    const text = message.text();
    if (/Content Security Policy|Refused to (load|connect|execute|apply|frame)/i.test(text))
      problems.push(`CSP: ${text}`);
  });
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (/^https?:$/.test(url.protocol) && url.host !== ownHost) problems.push(`external request: ${request.url()}`);
  });
  return problems;
}

/**
 * Вход с повтором: на стенде лимит 5 попыток входа в минуту с IP (§5.1), а спеки логинятся в каждом тесте.
 * При «Слишком много попыток» ждём и пробуем снова.
 */
async function login(page: Page, path = `/products/view/?id=${PRODUCT}&tab=banners`) {
  await page.goto(path);
  await expect(page).toHaveURL(/\/login\//);
  for (let attempt = 0; attempt < 5; attempt += 1) {
    await page.getByLabel("Логин").fill(LOGIN);
    await page.getByLabel("Пароль").fill(PASSWORD);
    await page.getByRole("button", { name: "Войти" }).click();
    const outcome = await Promise.race([
      page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15_000 }).then(() => "ok" as const),
      page
        .getByTestId("login-error")
        .filter({ hasText: /Слишком много попыток/ })
        .waitFor({ timeout: 15_000 })
        .then(() => "limited" as const),
    ]).catch(() => "unknown" as const);
    if (outcome === "ok") return;
    if (outcome !== "limited") break;
    await page.waitForTimeout(15_000);
  }
  await expect(page).not.toHaveURL(/\/login\//);
}

async function createCampaign(page: Page, name: string): Promise<string> {
  await expect(page.getByTestId("campaigns-panel")).toBeVisible();
  await page.getByTestId("create-campaign").click();
  const form = page.getByTestId("campaign-form");
  await form.getByLabel("Название").fill(name);
  await form.getByRole("button", { name: "Создать" }).click();
  const card = page.getByTestId("campaign-card").filter({ hasText: name });
  await expect(card).toBeVisible();
  const id = await card.getAttribute("data-campaign-id");
  expect(id).toBeTruthy();
  return id!;
}

async function openEditorFromUpload(page: Page, campaignName: string) {
  const card = page.getByTestId("campaign-card").filter({ hasText: campaignName });
  await card.getByTestId("new-variant-link").click();
  await expect(page).toHaveURL(/\/products\/banner\/\?campaign=\d+/);
  await expect(page.getByTestId("new-variant")).toBeVisible();

  const seed = Date.now() % 200;
  const png = solidPng(800, 200, [220, 20 + seed, 30]);
  await page.getByTestId("creative-file-input").setInputFiles({
    name: `e2e-creative-${Date.now()}.png`,
    mimeType: "image/png",
    buffer: png,
  });
  // Статичный PNG нормализуется сразу → вариант со слоями по умолчанию → редактор.
  await expect(page).toHaveURL(/\/products\/banner\/\?variant=\d+/, { timeout: 30_000 });
  await expect(page.getByTestId("banner-editor")).toBeVisible();
  await expect(page.getByTestId("banner-canvas")).toBeVisible();
}

// ---------------------------------------------------------------- тесты

test.describe("редактор баннера", () => {
  test.describe.configure({ timeout: 120_000 });

  test("креатив ниже y=1500 → creative_in_bottom_dead_zone; «Кадр из рендера» — PNG", async ({ page, baseURL }) => {
    const problems = watchProblems(page, baseURL);
    await login(page);
    const campaignName = `E2E баннер ${new Date().toISOString().slice(0, 19)}`;
    await createCampaign(page, campaignName);
    await openEditorFromUpload(page, campaignName);

    // Креатив встал в зону оффера и выбран; предупреждения о нижней зоне пока нет.
    await expect(page.locator('[data-layer-visual="creative"] image')).toBeVisible();
    await expect(page.getByTestId("selection")).toHaveAttribute("data-layer-id", "creative");
    const validation = page.getByTestId("banner-validation");
    await expect(validation.getByTestId("validation-pending")).toBeHidden({ timeout: 20_000 });
    await expect(validation.locator('[data-code="creative_in_bottom_dead_zone"]')).toHaveCount(0);

    // Safe zones площадки и зона субтитров на холсте; переключение на VK меняет профиль оверлея.
    await expect(page.getByTestId("safe-zone-overlay")).toHaveAttribute("data-profile", "youtube_shorts");
    await expect(page.getByTestId("captions-zone")).toBeVisible();

    // Клавиатура: Shift+↓ сдвигает слот на 10 px.
    const selection = page.getByTestId("selection");
    const before = (await selection.getAttribute("data-slot"))!.split(",").map(Number);
    await page.getByTestId("banner-canvas").focus();
    await page.keyboard.press("Shift+ArrowDown");
    await expect(selection).toHaveAttribute("data-slot", `${before[0]},${before[1] + 10},${before[2]},${before[3]}`);

    // Креатив ниже y=1500: Y слота 1560 (низ 1750 — в зоне подписи и кнопок).
    await page.getByTestId("slot-y").fill("1560");
    await expect(selection).toHaveAttribute("data-slot", new RegExp(`^${before[0]},1560,`));
    const warning = validation
      .locator('[data-testid="banner-warning"][data-code="creative_in_bottom_dead_zone"]')
      .first();
    await expect(warning).toBeVisible({ timeout: 20_000 });
    await expect(warning).toContainText("нижней мёртвой зоне");

    // Автосохранение с expected_version.
    await expect(page.getByTestId("save-state")).toHaveAttribute("data-state", "saved", { timeout: 20_000 });

    // «Кадр из рендера»: PNG тем же кодом, что финальный рендер.
    const responsePromise = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/banner-variants/preview") && response.request().method() === "POST",
      { timeout: 60_000 },
    );
    await page.getByTestId("render-frame-button").click();
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"]).toContain("image/png");
    expect(Number(response.headers()["x-preview-t-sec"])).toBeGreaterThan(0);
    // Тело ответа, прочитанное страницей как Blob, DevTools иногда не отдаёт повторно (пустой буфер) —
    // тогда формат подтверждает декодирование картинки ниже (data-width/height из createImageBitmap).
    const body = await response.body().catch(() => Buffer.alloc(0));
    if (body.length) expect(Array.from(body.subarray(0, 8))).toEqual(PNG_SIGNATURE);
    const image = page.getByTestId("render-frame-image");
    await expect(image).toBeVisible();
    await expect(image).toHaveAttribute("data-width", "540");
    await expect(image).toHaveAttribute("data-height", "960");

    // Отмена (Ctrl+Z в холсте или кнопкой) возвращает креатив в зону оффера — предупреждение уходит.
    await page.getByRole("button", { name: "Отменить (Ctrl+Z)" }).click();
    await page.getByTestId("banner-canvas").focus();
    await page.keyboard.press("Control+z");
    await expect(selection).toHaveAttribute("data-slot", before.join(","));
    await expect(validation.locator('[data-code="creative_in_bottom_dead_zone"]')).toHaveCount(0, { timeout: 20_000 });

    // Площадка VK — другой профиль safe zone.
    await page.getByRole("group", { name: "Площадка" }).getByRole("button", { name: "VK Клипы" }).click();
    await expect(page.getByTestId("safe-zone-overlay")).toHaveAttribute("data-profile", "vk_clips");

    // Перетаскивание мышью: креатив вверх — Y слота уменьшается (Alt — без прилипания к направляющим).
    const hit = page.locator('[data-layer-hit="creative"]');
    const box = (await hit.boundingBox())!;
    await page.keyboard.down("Alt");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 40, { steps: 5 });
    await page.mouse.up();
    await page.keyboard.up("Alt");
    const dragged = (await selection.getAttribute("data-slot"))!.split(",").map(Number);
    expect(dragged[1]).toBeLessThan(before[1]);
    expect(dragged[2]).toBe(before[2]);

    // Уборка на стенде: кампания → «Завершена».
    if (STAND) {
      await page.goto(`/products/view/?id=${PRODUCT}&tab=banners`);
      const card = page.getByTestId("campaign-card").filter({ hasText: campaignName });
      await card.getByLabel(/Статус кампании/).selectOption("ended");
      await expect(card.getByLabel(/Статус кампании/)).toHaveValue("ended");
    }
    expect(problems).toEqual([]);
  });

  test("отказ файла: код сервера по-русски", async ({ page }) => {
    await login(page);
    const campaignName = `E2E отказ ${new Date().toISOString().slice(0, 19)}`;
    await createCampaign(page, campaignName);
    const card = page.getByTestId("campaign-card").filter({ hasText: campaignName });
    await card.getByTestId("new-variant-link").click();
    await expect(page.getByTestId("new-variant")).toBeVisible();

    await page.getByTestId("creative-file-input").setInputFiles({
      name: "not-an-image.png",
      mimeType: "image/png",
      buffer: Buffer.from("это не картинка, а текст ".repeat(40), "utf8"),
    });
    const error = page.getByTestId("upload-error");
    await expect(error).toBeVisible({ timeout: 20_000 });
    await expect(error).toHaveAttribute("data-code", /unsupported_format|corrupt_file/);
    await expect(error).toHaveText(/Формат не поддерживается|Файл повреждён/);
    await expect(page).toHaveURL(/\?campaign=\d+/);

    if (STAND) {
      await page.goto(`/products/view/?id=${PRODUCT}&tab=banners`);
      await page
        .getByTestId("campaign-card")
        .filter({ hasText: campaignName })
        .getByLabel(/Статус кампании/)
        .selectOption("ended");
    }
  });
});
