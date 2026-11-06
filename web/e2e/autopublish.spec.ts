import { expect, type Page, test } from "@playwright/test";

/**
 * «Запланировано» (`/autopublish`): очередь автозалива через YouTube API (0019).
 *
 * - На моках: сиды mocks/handlers/packages.ts — 502 с ошибкой, 504 залит; постановка пакета 501 в слот
 *   видна в очереди после перехода по сайдбару (мок-данные живут в памяти страницы).
 * - На стенде (E2E_BASE_URL): только чтение — экран и фильтры.
 */
const LOGIN = process.env.E2E_LOGIN ?? "admin";
const PASSWORD = process.env.E2E_PASSWORD ?? "admin-demo-password";
const MOCKS = !process.env.E2E_BASE_URL;

async function login(page: Page, path: string) {
  await page.goto(path);
  await expect(page).toHaveURL(/\/login\//);
  await page.getByLabel("Логин").fill(LOGIN);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).not.toHaveURL(/\/login\//);
}

test.describe("запланировано", () => {
  test("очередь: состояния, фильтр в URL, ссылка на ролик, переход в пакет", async ({ page }) => {
    await login(page, "/autopublish/");
    await expect(page.getByRole("heading", { name: "Запланировано", level: 1 })).toBeVisible();
    const screen = page.getByTestId("autopublish-screen");
    await expect(screen.locator("tbody tr").first().or(screen.getByText("Очередь пуста"))).toBeVisible();

    await page.getByRole("group", { name: "Состояние" }).getByRole("button", { name: "Ошибки" }).click();
    await expect(page).toHaveURL(/state=failed/);
    test.skip(!MOCKS, "Дальше — сиды моков");

    const failed = screen.locator("tbody tr");
    await expect(failed).toHaveCount(1);
    await expect(failed.first()).toContainText("Ошибка автозалива");
    await expect(failed.first()).toContainText("Канал не подключён");

    await page.getByRole("group", { name: "Состояние" }).getByRole("button", { name: "Залитые" }).click();
    await expect(page).toHaveURL(/state=done/);
    await expect(screen.getByTestId("queue-video-link")).toHaveAttribute(
      "href",
      "https://www.youtube.com/shorts/Qm3hX9aK2pL",
    );

    await page.getByRole("group", { name: "Состояние" }).getByRole("button", { name: "Все" }).click();
    await screen.locator("tbody tr").filter({ hasText: "cl-0927-b4" }).click();
    await expect(page).toHaveURL(/\/packages\/\?package=502/);
    await expect(page.getByTestId("package-detail")).toHaveAttribute("data-package-id", "502");
  });

  test("пакет в слоте появляется в очереди", async ({ page }) => {
    test.skip(!MOCKS, "Постановка в очередь — только на моках");
    await login(page, "/packages/?package=501");
    const block = page.getByTestId("package-detail").getByTestId("autopublish-block");
    await block.getByTestId("autopublish-slot").click();
    await expect(block).toHaveAttribute("data-state", "scheduled");

    await page.getByRole("link", { name: "Запланировано" }).click();
    await expect(page).toHaveURL(/\/autopublish\/$/);
    const row = page.getByTestId("autopublish-screen").locator("tbody tr").filter({ hasText: "tr-0927-a1" });
    await expect(row).toContainText("В очереди");
    await expect(row).toContainText("Таро — выбери карту");
  });
});
