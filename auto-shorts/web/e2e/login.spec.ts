import { expect, type Page, test } from "@playwright/test";

/**
 * Вход → AppShell → SSE-точка live → выход (docs/ARCHITECTURE.md §10.2, критерий M1).
 * На стенде: E2E_BASE_URL, E2E_LOGIN, E2E_PASSWORD. Без них — сборка на MSW-моках (`npm run e2e:mock`),
 * учётка моков admin / admin-demo-password.
 */
const LOGIN = process.env.E2E_LOGIN ?? "admin";
const PASSWORD = process.env.E2E_PASSWORD ?? "admin-demo-password";

/** Нарушения CSP и необработанные ошибки страницы — провал теста (§7.7, M4/M7: «0 нарушений CSP»). */
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

async function login(page: Page, user = LOGIN, password = PASSWORD) {
  await page.getByLabel("Логин").fill(user);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();
}

test.describe("вход и оболочка", () => {
  test("гостя уводит на /login с next, после входа возвращает туда же", async ({ page, baseURL }) => {
    const problems = watchConsole(page, baseURL);
    await page.goto("/settings/?tab=system");
    await expect(page).toHaveURL(/\/login\/\?next=%2Fsettings%2F%3Ftab%3Dsystem$/);
    await expect(page.getByRole("heading", { name: "Auto Shorts" })).toBeVisible();

    await login(page);
    await expect(page).toHaveURL(/\/settings\/\?tab=system$/);
    await expect(page.getByTestId("system-panel")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Состояние системы" })).toBeVisible();
    expect(problems).toEqual([]);
  });

  test("неверный пароль: понятная ошибка, без перехода", async ({ page }) => {
    await page.goto("/login/");
    // Несуществующий логин: счётчик блокировки настоящих учёток на стенде не растёт.
    await login(page, `e2e-nobody-${Date.now()}`, "wrong-password-123");
    await expect(page.getByTestId("login-error")).toHaveText(/Неверный логин или пароль/);
    await expect(page).toHaveURL(/\/login\/$/);
  });

  test("вход → AppShell → SSE live → палитра → выход", async ({ page, baseURL }) => {
    const problems = watchConsole(page, baseURL);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login\//);
    await login(page);

    // AppShell: сайдбар со всеми группами, шапка с воркером, бюджетом, алертами и точкой SSE.
    const nav = page.getByRole("navigation", { name: "Основное меню" });
    await expect(nav).toBeVisible();
    for (const group of ["Пульт", "Производство", "Источники", "Продвижение", "Анализ", "Настройка"]) {
      await expect(nav.getByRole("group", { name: group })).toBeVisible();
    }
    for (const item of [
      "Обзор",
      "Конвейер",
      "Приёмка",
      "Пакеты",
      "Нарезки",
      "Трендоскаут",
      "Продукты и баннеры",
      "Аналитика",
      "Ниши",
      "Каналы",
      "Настройки",
    ]) {
      await expect(nav.getByRole("link", { name: new RegExp(item) })).toBeVisible();
    }
    await expect(page.getByTestId("worker-indicator")).toBeVisible();
    await expect(page.getByTestId("budget-meter")).toBeVisible();
    await expect(page.getByTestId("alerts-bell")).toBeVisible();
    await expect(page.getByTestId("live-dot")).toHaveAttribute("data-state", "live", { timeout: 20_000 });

    // Переход по сайдбару: Пакеты с M3 — рабочий экран; Трендоскаут с 24.10 — мост к Kino-trends.
    await nav.getByRole("link", { name: /Пакеты/ }).click();
    await expect(page).toHaveURL(/\/packages\/$/);
    await expect(page.getByRole("heading", { name: "Пакеты к заливке", level: 1 })).toBeVisible();
    await expect(page.getByTestId("packages-screen")).toBeVisible();
    await nav.getByRole("link", { name: /Трендоскаут/ }).click();
    await expect(page).toHaveURL(/\/trends\/$/);
    await expect(page.getByTestId("trends-screen")).toBeVisible();

    // Палитра Ctrl+K → Настройки → Пользователи.
    await page.keyboard.press("Control+k");
    const palette = page.getByTestId("command-palette");
    await expect(palette).toBeVisible();
    await page.getByPlaceholder(/Экран, video_uid/).fill("Пользователи");
    await palette.getByRole("option", { name: /Настройки → Пользователи/ }).click();
    await expect(page).toHaveURL(/\/settings\/\?tab=users$/);
    await expect(page.getByRole("tab", { name: /Пользователи/, selected: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: new RegExp(LOGIN) }).first()).toBeVisible();

    // Выход.
    await page.getByTestId("user-menu").click();
    await page.getByTestId("logout").click();
    await expect(page).toHaveURL(/\/login\/$/);
    await page.goto("/settings/");
    await expect(page).toHaveURL(/\/login\/\?next=%2Fsettings%2F$/);
    expect(problems).toEqual([]);
  });
});
