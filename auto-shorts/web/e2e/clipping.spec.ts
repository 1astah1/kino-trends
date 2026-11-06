import { expect, type Locator, type Page, test } from "@playwright/test";

/**
 * Трек нарезки под логином (docs/ARCHITECTURE.md §10.2, критерий M5: «Playwright clipping.spec под
 * логином: in/out, „В рендер“, задача в Конвейере»).
 *
 * - На моках (`npm run e2e:mock`): учётка admin / admin-demo-password, источник «Эфир 12.09» (своё
 *   видео, эвристика без LLM) из mocks/handlers/clipping.ts; ролик создаёт мок Конвейера.
 * - На стенде: `E2E_BASE_URL`, `E2E_LOGIN`, `E2E_PASSWORD`; нужен готовый источник с моментом, по которому
 *   ещё нет ролика. Тест создаёт настоящий clip-job (рендер в тяжёлом слоте).
 * - Против `npm run dev:mock`: `E2E_BASE_URL=http://127.0.0.1:18301 E2E_MOCKS=1`.
 */
const LOGIN = process.env.E2E_LOGIN ?? "admin";
const PASSWORD = process.env.E2E_PASSWORD ?? "admin-demo-password";
const MOCKS = process.env.E2E_MOCKS === "1" || !process.env.E2E_BASE_URL;

/** Нарушения CSP, ошибки страницы и внешние запросы — провал теста (§7.7). */
function watchConsole(page: Page, baseURL: string | undefined) {
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
  // Подтверждения (снять блок, заменить текст черновиком) — принимаем.
  page.on("dialog", (dialog) => void dialog.accept());
  return problems;
}

async function login(page: Page) {
  await expect(page).toHaveURL(/\/login\//);
  await page.getByLabel("Логин").fill(LOGIN);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
}

/** Первый момент без ролика и без блока (на стенде часть моментов уже в работе). */
async function pickFreeMoment(page: Page): Promise<Locator | null> {
  const moments = page.locator('[data-testid^="moment-"][data-candidate-id]');
  await moments
    .first()
    .waitFor({ timeout: 20_000 })
    .catch(() => undefined);
  const free = page.locator(
    '[data-testid^="moment-"][data-blocked="false"]:not([data-status="used"]):not([data-status="dismissed"])',
  );
  return (await free.count()) ? free.first() : null;
}

test.describe("нарезка", () => {
  test("in/out по словам, авторский слой, «В рендер» → ролик в Конвейере", async ({ page, baseURL }) => {
    test.setTimeout(90_000);
    const problems = watchConsole(page, baseURL);
    await page.goto(MOCKS ? "/clipping/" : "/clipping/?status=ready");
    await login(page);
    await expect(page).toHaveURL(/\/clipping\//);
    await expect(page.getByRole("heading", { name: "Источники нарезки", level: 1 })).toBeVisible();

    const table = page.getByTestId("sources-table");
    const readyRow = MOCKS
      ? table.locator("tbody tr", { hasText: "Эфир 12.09" }).first()
      : table.locator("tbody tr", { hasText: "Готов к нарезке" }).first();
    const hasSource = await readyRow
      .waitFor({ timeout: 15_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!hasSource, "Нет готового источника");
    await readyRow.click();

    await expect(page).toHaveURL(/\/clipping\/source\/\?id=\d+/);
    const studio = page.getByTestId("clip-studio");
    await expect(studio).toBeVisible();
    await expect(page.getByTestId("selection-label")).toBeVisible();
    if (MOCKS) await expect(page.getByTestId("selection-label")).toContainText("Эвристика без LLM");

    const moment = await pickFreeMoment(page);
    test.skip(!moment, "Нет свободного момента без ролика");
    await (moment as Locator).click();
    const candidateId = await (moment as Locator).getAttribute("data-candidate-id");
    await expect(page).toHaveURL(new RegExp(`clip=${candidateId}`));

    // Транскрипт и дорожка на месте.
    await expect(page.getByTestId("transcript-panel").locator("[data-word]").first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("clip-timeline")).toBeVisible();

    // In/out: вход на два слова позже, выход на слово раньше → «Применить».
    const inputIn = page.getByTestId("clip-in");
    const inputOut = page.getByTestId("clip-out");
    const inBefore = (await inputIn.innerText()).trim();
    const outBefore = (await inputOut.innerText()).trim();
    await page.getByTestId("in-later").click();
    await page.getByTestId("in-later").click();
    await page.getByTestId("out-earlier").click();
    await expect(page.getByTestId("bounds-dirty")).toBeVisible();
    await expect(inputIn).not.toHaveText(inBefore);
    await expect(inputOut).not.toHaveText(outBefore);
    const patch = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        new URL(response.url()).pathname === `/api/v1/clip-candidates/${candidateId}`,
    );
    await page.getByTestId("apply-bounds").click();
    expect((await patch).status()).toBe(200);
    await expect(page.getByTestId("bounds-dirty")).toHaveCount(0);
    await expect(inputIn).not.toHaveText(inBefore);

    // Выделение в транскрипте: клик по слову и Shift+клик → панель выделения.
    const words = page.getByTestId("transcript-panel").locator("[data-word]");
    await words.nth(2).click();
    await words.nth(6).click({ modifiers: ["Shift"] });
    await expect(page.getByTestId("selection-bar")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("selection-bar")).toHaveCount(0);

    // Канал выбран, авторский слой написан своими словами → оценка оригинальности пересчитана.
    await expect(page.getByTestId("clip-channel")).not.toHaveValue("");
    await page.getByTestId("author-title").fill("Карта дня — подсказка, а не приговор");
    await page.getByTestId("author-cold-open").fill("Самая частая ошибка с картой дня — ждать от неё приговора.");
    await page.getByTestId("author-outro").fill("А вы тянете карту утром или вечером? Напишите в комментариях.");
    const report = page.getByTestId("originality-section").getByTestId("originality-report");
    await expect(report).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("author-share-value")).toContainText("%");

    // «В рендер»: при провале гейта (чужие права) кнопка просит подтвердить второй раз.
    const send = page.getByTestId("send-to-render");
    await expect(send).toBeEnabled();
    const created = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/v1/clip-candidates/${candidateId}/jobs`,
    );
    await send.click();
    if ((await send.innerText()).includes("Всё равно")) await send.click();
    const response = await created;
    expect(response.status()).toBe(201);
    const job = (await response.json()) as { id: number; video_uid: string };

    const open = page.getByTestId("send-bar").getByTestId("open-in-production").first();
    await expect(open).toBeVisible();
    await open.click();
    await expect(page).toHaveURL(new RegExp(`/production/\\?.*job=${job.id}`));
    const detail = page.getByTestId("job-detail");
    await expect(detail).toBeVisible();
    await expect(detail).toHaveAttribute("data-job-id", String(job.id));
    await expect(detail).toContainText(job.video_uid);
    expect(problems).toEqual([]);
  });

  test("добавление источника: права обязательны, файл по tus, источник в списке", async ({ page, baseURL }) => {
    test.skip(!MOCKS, "На стенде загрузка создаёт настоящий источник");
    const problems = watchConsole(page, baseURL);
    await page.goto("/clipping/");
    await login(page);
    await page.getByTestId("add-source-open").click();
    const dialog = page.getByTestId("add-source-dialog");
    await expect(dialog).toBeVisible();

    await dialog.getByTestId("add-source-file").setInputFiles({
      name: "efir_27_09.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.alloc(3 * 1024 * 1024, 7),
    });
    await expect(dialog.getByTestId("upload-progress")).toContainText("Загружено", { timeout: 20_000 });
    await expect(dialog.getByLabel("Название *")).toHaveValue("efir 27 09");

    // CC BY без атрибуции — ошибка под полем, источник не создаётся.
    await dialog.getByText("CC BY", { exact: true }).click();
    await dialog.getByLabel("Правообладатель *").fill("Иван Петров");
    await dialog.getByLabel("Чем подтверждено *").fill("Лицензия CC BY-SA 4.0 под видео");
    await page.getByTestId("add-source-submit").click();
    await expect(dialog.getByText(/атрибуция обязательна/i)).toBeVisible();
    await expect(dialog.getByText(/Лицензия не принимается/)).toBeVisible();

    // Своё видео — проходит.
    await dialog.getByText("Своё видео", { exact: true }).click();
    await dialog.getByLabel("Правообладатель *").fill("Канал «Таро — выбери карту»");
    await dialog.getByLabel("Чем подтверждено *").fill("Запись моего эфира 27.09.2026");
    await page.getByTestId("add-source-submit").click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId("sources-table").locator("tbody tr", { hasText: "efir 27 09" })).toBeVisible();
    expect(problems).toEqual([]);
  });
});
