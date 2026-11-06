import { expect, type Page, test } from "@playwright/test";

/**
 * Каналы (§7.3 `/channels`): таблица, фильтры в URL, группировка по кустам, drawer с формой, здоровьем
 * и журналом инцидентов, предупреждение о совпадающем профиле.
 *
 * На стенде (E2E_BASE_URL) тест только читает; создание канала и запись в журнал — на моках или
 * при E2E_ALLOW_WRITES=1 (каналы через API не удаляются).
 *
 * «YouTube · автозалив» (0019) — только на моках (mocks/handlers/autopublish.ts): taro_main подключён,
 * taro_second — клиент Google есть, не подключён, astro_main — клиента нет. Экран согласия Google
 * подменяется `page.route`, колбэк — переходом на `/channels/?oauth=…`.
 */
const LOGIN = process.env.E2E_LOGIN ?? "admin";
const PASSWORD = process.env.E2E_PASSWORD ?? "admin-demo-password";
const WRITES = !process.env.E2E_BASE_URL || process.env.E2E_ALLOW_WRITES === "1";
const MOCKS = !process.env.E2E_BASE_URL;
const GOOGLE_AUTH = /^https:\/\/accounts\.google\.com\//;

/** Дождаться, пока таблица каналов загрузится (строки или пустое состояние). */
async function waitForTable(page: Page) {
  const screen = page.getByTestId("channels-screen");
  await expect(screen.locator("tbody tr").first().or(screen.getByText("Каналов нет"))).toBeVisible();
  return screen;
}

/** Открыть карточку канала из таблицы (drawer — модальный: прежнюю закрываем Escape). */
async function openChannel(page: Page, id: string) {
  const drawer = page.getByTestId("channel-drawer");
  if (await drawer.isVisible()) {
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
  }
  const screen = await waitForTable(page);
  await screen.locator("tbody tr").filter({ hasText: id }).first().click();
  await expect(page).toHaveURL(new RegExp(`channel=${id}`));
  await expect(drawer).toBeVisible();
  return drawer.getByTestId("youtube-card");
}

/** Вход с повтором при лимите попыток (5 в минуту с IP на стенде, §5.1). */
async function login(page: Page, path: string) {
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

test.describe("каналы", () => {
  test.describe.configure({ timeout: 120_000 });

  test("список, фильтр площадки, кусты и карточка канала", async ({ page }) => {
    await login(page, "/channels/");
    await expect(page).toHaveURL(/\/channels\/$/);
    await expect(page.getByRole("heading", { name: "Каналы", level: 1 })).toBeVisible();
    const screen = page.getByTestId("channels-screen");
    await expect(screen).toBeVisible();

    // Фильтр площадки попадает в URL.
    await page.getByRole("group", { name: "Площадка" }).getByRole("button", { name: "YouTube" }).click();
    await expect(page).toHaveURL(/platform=youtube/);
    await page.getByRole("group", { name: "Площадка" }).getByRole("button", { name: "Все" }).click();

    await waitForTable(page);
    const rows = screen.locator("tbody tr");
    const count = await rows.count();
    test.skip(count === 0, "На стенде нет каналов — карточку проверить не на чем");

    // Карточка: ?channel=, здоровье, настройки, журнал; Escape закрывает.
    await rows.first().click();
    await expect(page).toHaveURL(/channel=/);
    const drawer = page.getByTestId("channel-drawer");
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText("Здоровье", { exact: true })).toBeVisible();
    await expect(drawer.getByText("Журнал инцидентов", { exact: true })).toBeVisible();
    await expect(drawer.getByTestId("channel-form")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(page).not.toHaveURL(/channel=/);

    // Группировка по кустам.
    await page.getByRole("group", { name: "Вид" }).getByRole("button", { name: "По кустам" }).click();
    await expect(page).toHaveURL(/view=clusters/);
  });

  test("совпадающий профиль виден в таблице и в карточке", async ({ page }) => {
    await login(page, "/channels/");
    const screen = await waitForTable(page);
    const duplicate = screen.locator("tbody tr").filter({ hasText: "профиль-дубль" });
    const count = await duplicate.count();
    test.skip(count === 0, "Нет каналов с одинаковым профилем");
    await duplicate.first().click();
    await expect(page.getByTestId("profile-duplicate")).toBeVisible();
  });

  test("новый канал и запись в журнал", async ({ page }) => {
    test.skip(!WRITES, "На стенде без E2E_ALLOW_WRITES=1 каналы не создаём");
    await login(page, "/channels/");
    await page.getByTestId("create-channel").click();
    const form = page.getByTestId("channel-form");
    const id = `e2e_${Date.now().toString(36)}`;
    await form.getByLabel("id (латиница)").fill(id);
    await form.getByLabel("Название").fill(`E2E ${id}`);
    const niche = form.getByLabel("Ниша");
    const firstNiche = await niche.locator("option").nth(1).getAttribute("value");
    expect(firstNiche).toBeTruthy();
    await niche.selectOption(firstNiche!);
    await form.getByLabel("Куст").fill(firstNiche!);
    await form.getByLabel("Голос").fill(`ru-RU-E2E-${id}`);
    await form.getByTestId("channel-save").click();

    await expect(page).toHaveURL(new RegExp(`channel=${id}`));
    const drawer = page.getByTestId("channel-drawer");
    await expect(drawer).toBeVisible();
    await drawer.getByLabel("Тип записи").selectOption("note");
    await drawer.getByLabel("Что случилось").fill("E2E: проверка журнала");
    await drawer.getByRole("button", { name: "Записать" }).click();
    await expect(drawer.getByText("E2E: проверка журнала")).toBeVisible();
  });
  test("YouTube · автозалив: подключён, клиент Google, кнопка подключения", async ({ page }) => {
    test.skip(!MOCKS, "Состояния подключения — из моков");
    // Экран согласия Google не трогаем: навигацию туда перехватываем.
    await page.route(GOOGLE_AUTH, (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<title>Google</title><p>consent</p>" }),
    );
    await login(page, "/channels/?channel=taro_main");
    const drawer = page.getByTestId("channel-drawer");
    await expect(drawer).toBeVisible();

    // Подключён: канал Google ссылкой, право загрузки, план, действия.
    let card = drawer.getByTestId("youtube-card");
    await expect(card).toBeVisible();
    await expect(card.getByText("подключён", { exact: true })).toBeVisible();
    await expect(card.getByTestId("youtube-channel-link")).toHaveAttribute(
      "href",
      /^https:\/\/www\.youtube\.com\/channel\/UC/,
    );
    await expect(card.getByText("youtube.upload", { exact: true })).toBeVisible();
    await expect(card.getByTestId("youtube-plan").getByText("13:00", { exact: true })).toBeVisible();
    await expect(card.getByRole("button", { name: "Переподключить" })).toBeVisible();
    await expect(card.getByRole("button", { name: "Отключить" })).toBeVisible();
    await expect(card.getByText(/больше не блокируются как приватные/)).toBeVisible();

    // Клиент есть, канал не подключён: только кнопка подключения.
    card = await openChannel(page, "taro_second");
    await expect(card.getByText("не подключён", { exact: true })).toBeVisible();
    await expect(card.getByTestId("youtube-connect")).toBeVisible();
    await expect(card.getByTestId("youtube-client-setup")).toHaveCount(0);

    // Клиента нет: инструкция, redirect URI, форма JSON (admin) — значение не остаётся после сохранения.
    card = await openChannel(page, "astro_main");
    const setup = card.getByTestId("youtube-client-setup");
    await expect(setup).toBeVisible();
    await expect(setup.getByText("YT_OAUTH_CLIENT_ASTRO").first()).toBeVisible();
    await expect(setup.getByText(/\/api\/v1\/oauth\/youtube\/callback$/)).toBeVisible();
    await expect(card.getByTestId("youtube-connect")).toHaveCount(0);
    const json = setup.getByLabel(/JSON клиента/);
    await json.fill("{oops");
    await expect(setup.getByText(/Это не JSON/)).toBeVisible();
    await expect(setup.getByRole("button", { name: "Сохранить в секрет" })).toBeDisabled();
    const origin = new URL(page.url()).origin;
    await json.fill(
      JSON.stringify({
        web: {
          client_id: "1-e2e.apps.googleusercontent.com",
          client_secret: "e2e-secret",
          redirect_uris: [`${origin}/api/v1/oauth/youtube/callback`],
        },
      }),
    );
    const saved = page.waitForRequest(
      (request) => request.method() === "PUT" && request.url().endsWith("/api/v1/secrets/YT_OAUTH_CLIENT_ASTRO"),
    );
    await setup.getByRole("button", { name: "Сохранить в секрет" }).click();
    expect((await saved).postDataJSON()).toMatchObject({ value: expect.stringContaining("e2e-secret") });
    await expect(page.getByText("Секрет YT_OAUTH_CLIENT_ASTRO записан")).toBeVisible();
    await expect(setup).toBeHidden();

    // Подключение: GET …/start → window.location.assign(authorize_url).
    await card.getByTestId("youtube-connect").click();
    await page.waitForURL(GOOGLE_AUTH);
    const authorize = new URL(page.url());
    expect(authorize.searchParams.get("redirect_uri")).toBe(`${origin}/api/v1/oauth/youtube/callback`);
    expect(authorize.searchParams.get("scope")).toContain("youtube.upload");
    expect(authorize.searchParams.get("access_type")).toBe("offline");
    expect(authorize.searchParams.get("state")).toContain("astro_main");
  });

  test("план автозалива: правка режима, слотов, категории и языка", async ({ page }) => {
    test.skip(!MOCKS, "Правка плана меняет канал — только на моках");
    await login(page, "/channels/?channel=taro_main");
    const plan = page.getByTestId("channel-drawer").getByTestId("youtube-plan");
    await expect(plan.getByText("24 · Развлечения")).toBeVisible();
    await plan.getByTestId("plan-edit").click();
    const form = plan.getByTestId("plan-form");
    await expect(form.getByLabel("Режим")).toHaveValue("auto");

    // Неверный пояс — ошибка и «Сохранить» недоступна.
    await form.getByLabel("Часовой пояс").fill("Mars/Base");
    await expect(form.getByText(/Неизвестный пояс/)).toBeVisible();
    await expect(form.getByTestId("plan-save")).toBeDisabled();
    await form.getByLabel("Часовой пояс").fill("Asia/Yekaterinburg");

    await form.getByRole("button", { name: "Убрать слот 09:00" }).click();
    await form.getByRole("button", { name: "Слот", exact: true }).click();
    await form.getByLabel("Слот 3").fill("21:15");
    await form.getByLabel("Приватность").selectOption("unlisted");
    await form.getByLabel("Роликов в день").fill("2");
    await form.getByLabel("Категория YouTube").selectOption("1");
    await form.getByLabel("Язык ролика").selectOption("");

    const patch = page.waitForRequest(
      (request) => request.method() === "PATCH" && request.url().endsWith("/api/v1/channels/taro_main"),
    );
    await form.getByTestId("plan-save").click();
    expect((await patch).postDataJSON()).toEqual({
      publishing_plan: {
        mode: "auto",
        slots: ["13:00", "19:30", "21:15"],
        timezone: "Asia/Yekaterinburg",
        privacy: "unlisted",
        videos_per_day: 2,
        category_id: "1",
        language: null,
      },
    });
    await expect(page.getByText("План автозалива сохранён")).toBeVisible();
    await expect(plan.getByTestId("plan-form")).toHaveCount(0);
    await expect(plan.getByText("1 · Фильмы и анимация")).toBeVisible();
    await expect(plan.getByText("по ссылке")).toBeVisible();
    await expect(plan.getByText("21:15", { exact: true })).toBeVisible();
    await expect(plan.getByText("Asia/Yekaterinburg")).toBeVisible();
    await expect(plan.getByText("не указан")).toBeVisible();
  });

  test("возврат с Google: ?oauth=ok открывает карточку и тост, ошибки — тостом", async ({ page }) => {
    test.skip(!MOCKS, "Возврат проверяется на сидах моков");
    await login(page, "/channels/?platform=youtube&oauth=ok&channel=taro_main");
    await expect(page.getByText("YouTube подключён")).toBeVisible();
    await expect(page.getByTestId("channel-drawer").getByTestId("youtube-card")).toBeVisible();
    // oauth убран из URL, остальное (канал, фильтр) — на месте.
    await expect(page).not.toHaveURL(/oauth=/);
    await expect(page).toHaveURL(/channel=taro_main/);
    await expect(page).toHaveURL(/platform=youtube/);

    await page.goto(
      `/channels/?oauth=error&reason=oauth_exchange_failed&message=${encodeURIComponent("Google отклонил код")}`,
    );
    await expect(page.getByText("YouTube не подключён")).toBeVisible();
    await expect(page.getByText("Google отклонил код")).toBeVisible();
    await expect(page).not.toHaveURL(/oauth=|reason=|message=/);

    await page.goto("/channels/?oauth=denied&reason=access_denied");
    await expect(page.getByText("Google не выдал доступ")).toBeVisible();
    await expect(page.getByText(/нажали «Отмена»/)).toBeVisible();
    await expect(page).not.toHaveURL(/oauth=|reason=/);
    await expect(page.getByTestId("channel-drawer")).toHaveCount(0);
  });
});
