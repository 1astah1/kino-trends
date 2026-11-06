import { expect, type Page, test } from "@playwright/test";

/**
 * Конвейер под логином (docs/ARCHITECTURE.md §7.3 «Конвейер», §10.2 M2).
 * На моках — `npm run e2e:mock`; на стенде — `E2E_BASE_URL`, `E2E_LOGIN`, `E2E_PASSWORD`
 * (на стенде тест создания роликов пропускается: он тратит бюджет провайдеров).
 */
const LOGIN = process.env.E2E_LOGIN ?? "admin";
const PASSWORD = process.env.E2E_PASSWORD ?? "admin-demo-password";
const MOCKS = !process.env.E2E_BASE_URL;

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
  return problems;
}

async function login(page: Page) {
  await expect(page).toHaveURL(/\/login\//);
  await page.getByLabel("Логин").fill(LOGIN);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
}

test.describe("конвейер", () => {
  test("лента стадий, таблица, панель ролика с таймлайном и логом", async ({ page, baseURL }) => {
    const problems = watchConsole(page, baseURL);
    await page.goto("/production/");
    await login(page);
    await expect(page).toHaveURL(/\/production\//);
    await expect(page.getByRole("heading", { name: "Конвейер", level: 1 })).toBeVisible();

    const rail = page.getByTestId("stage-rail");
    await expect(rail.getByRole("button", { name: /Все/ })).toHaveAttribute("aria-pressed", "true");

    const table = page.getByTestId("jobs-table");
    const rows = table.locator("tbody tr");
    const hasRows = await rows
      .first()
      .waitFor({ timeout: 15_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!hasRows, "Роликов нет");

    // Фильтр ленты: «Ждёт приёмки» попадает в URL.
    await rail.getByRole("button", { name: /Ждёт приёмки/ }).click();
    await expect(page).toHaveURL(/status=pending_review/);
    await expect(rail.getByRole("button", { name: /Ждёт приёмки/ })).toHaveAttribute("aria-pressed", "true");

    // Ждём, пока таблица покажет отфильтрованные строки (старые держатся на экране до ответа).
    const firstRow = table.locator("tbody tr", { hasText: "Ждёт приёмки" }).first();
    const hasPending = await firstRow
      .waitFor({ timeout: 10_000 })
      .then(() => true)
      .catch(() => false);
    if (hasPending) {
      const jobId = (await firstRow.getAttribute("data-job-id")) as string;
      await firstRow.click();
      await expect(page).toHaveURL(new RegExp(`job=${jobId}`));
      const detail = page.getByTestId("job-detail");
      await expect(detail).toBeVisible();
      await expect(detail).toHaveAttribute("data-job-id", jobId);
      await expect(page.getByTestId("stage-timeline")).toBeVisible();
      await expect(detail.getByRole("link", { name: /В приёмку|Открыть в приёмке/ })).toBeVisible();

      await detail.getByRole("tab", { name: "QC" }).click();
      await expect(page).toHaveURL(/tab=qc/);
      await detail.getByRole("tab", { name: "Логи" }).click();
      await expect(page.getByTestId("task-log")).toBeVisible();
      await detail.getByRole("tab", { name: "История" }).click();
      await expect(page.getByTestId("job-history")).toBeVisible();

      // Esc закрывает панель.
      await page.keyboard.press("Escape");
      await expect(detail).toHaveCount(0);
    }

    // Сброс фильтров.
    await page.getByRole("button", { name: "Сбросить" }).click();
    await expect(page).not.toHaveURL(/status=/);
    expect(problems).toEqual([]);
  });

  test("новый ролик: две темы → пачка, панель первого ролика", async ({ page, baseURL }) => {
    test.skip(!MOCKS, "На стенде создание роликов тратит бюджет провайдеров");
    const problems = watchConsole(page, baseURL);
    await page.goto("/production/");
    await login(page);
    await page.getByTestId("new-job-open").click();
    const sheet = page.getByTestId("new-job-sheet");
    await expect(sheet).toBeVisible();
    await expect(sheet.getByLabel("Канал")).not.toHaveValue("");
    // по умолчанию — клип (трек нарезки в приоритете); генерация без стадий M2-B — только черновиком
    await sheet.getByRole("button", { name: /^Генерация/ }).click();
    await expect(page.getByTestId("generate-unavailable")).toBeVisible();
    await sheet.getByLabel("Темы — по одной в строке").fill("карта на завтра\nденьги в октябре");
    await expect(page.getByTestId("new-job-estimate")).toContainText("Будет создано: 2");
    await page.getByTestId("new-job-submit").click();

    await expect(sheet).toHaveCount(0);
    await expect(page.getByText("Создано роликов: 2")).toBeVisible();
    await expect(page).toHaveURL(/job=\d+/);
    await expect(page.getByTestId("job-detail")).toBeVisible();
    expect(problems).toEqual([]);
  });
});
