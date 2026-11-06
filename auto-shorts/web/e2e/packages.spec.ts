import { expect, type Page, test } from "@playwright/test";

import { parseVideoUrl } from "@/lib/platform-url";

/**
 * Пакеты под логином (docs/ARCHITECTURE.md §10.2, критерий M3: `packages.spec` — вставка URL → статус
 * `published`).
 *
 * - На моках (`npm run e2e:mock`): учётка admin / admin-demo-password, пакеты из mocks/handlers/packages.ts;
 *   ссылка на Shorts генерируется случайно.
 * - На стенде: `E2E_BASE_URL`, `E2E_LOGIN`, `E2E_PASSWORD` и **`E2E_PUBLISH_URL`** — ссылка на ролик, который
 *   оператор действительно залил в канал пакета (иначе проверка принадлежности даст «чужой канал»).
 *   Необязательно `E2E_PACKAGE_ID` — какой пакет регистрировать; иначе первый живой пакет той же площадки.
 *   Без `E2E_PUBLISH_URL` на стенде регистрация пропускается: фиктивная ссылка засорила бы реестр.
 */
const LOGIN = process.env.E2E_LOGIN ?? "admin";
const PASSWORD = process.env.E2E_PASSWORD ?? "admin-demo-password";
const MOCKS = !process.env.E2E_BASE_URL;
const STAND_URL = process.env.E2E_PUBLISH_URL ?? "";
const STAND_PACKAGE = process.env.E2E_PACKAGE_ID ?? "";

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
  return problems;
}

async function login(page: Page) {
  await expect(page).toHaveURL(/\/login\//);
  await page.getByLabel("Логин").fill(LOGIN);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
}

function randomYoutubeId(): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
  let id = "";
  for (let i = 0; i < 11; i += 1) id += alphabet[Math.floor(Math.random() * alphabet.length)];
  return id;
}

function isPublicationPost(response: { url: () => string; request: () => { method: () => string } }) {
  return response.request().method() === "POST" && new URL(response.url()).pathname === "/api/v1/publications";
}

test.describe("пакеты", () => {
  test("вставка ссылки → публикация зарегистрирована, пакет «Опубликован»", async ({ page, baseURL, context }) => {
    const problems = watchConsole(page, baseURL);
    test.skip(!MOCKS && !STAND_URL, "На стенде нужен E2E_PUBLISH_URL — ссылка на реально залитый ролик");

    const publishUrl = MOCKS ? `https://youtube.com/shorts/${randomYoutubeId()}` : STAND_URL;
    const parsed = parseVideoUrl(publishUrl);
    expect(parsed.ok, "E2E_PUBLISH_URL должен быть ссылкой на ролик YouTube или VK").toBe(true);
    if (!parsed.ok) return;
    const platform = parsed.value.platform;
    await context.grantPermissions(["clipboard-read", "clipboard-write"]).catch(() => undefined);

    await page.goto("/packages/");
    await login(page);
    await expect(page).toHaveURL(/\/packages\//);
    await expect(page.getByRole("heading", { name: "Пакеты к заливке", level: 1 })).toBeVisible();

    // Живой пакет той же площадки, что и ссылка (или заданный E2E_PACKAGE_ID).
    const rows = page.locator(
      STAND_PACKAGE
        ? `[data-testid="package-row"][data-package-id="${STAND_PACKAGE}"]`
        : `[data-testid="package-row"][data-platform="${platform}"]:is([data-status="ready"], [data-status="downloaded"])`,
    );
    const hasRow = await rows
      .first()
      .waitFor({ timeout: 15_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!hasRow, `Нет живого пакета для площадки ${platform}`);
    const row = rows.first();
    const packageId = (await row.getAttribute("data-package-id")) as string;
    await row.click();
    await expect(page).toHaveURL(new RegExp(`package=${packageId}`));

    const detail = page.getByTestId("package-detail");
    await expect(detail).toHaveAttribute("data-package-id", packageId);
    await expect(detail.getByTestId("account-hint")).not.toBeEmpty();
    await expect(detail.getByTestId("package-checklist")).toBeVisible();

    // Клавиша 1 — копирование заголовка (фокус не в поле ввода).
    await detail.getByTestId("package-uid").click();
    await page.keyboard.press("1");
    await expect(page.getByText("Скопировано: Заголовок")).toBeVisible();
    const title = await detail.getByTestId("package-title").inputValue();
    const clipboard = await page.evaluate(() => navigator.clipboard.readText()).catch(() => null);
    if (clipboard !== null) expect(clipboard).toBe(title.trim());

    // Ссылка другой площадки: живой разбор предупреждает, сервер отвечает 422 platform_mismatch по-русски.
    const input = detail.getByTestId("published-url-input");
    const foreign = platform === "youtube" ? "https://vk.com/clip-1_2" : "https://youtube.com/shorts/dQw4w9WgXcQ";
    await input.fill(foreign);
    await expect(detail.getByTestId("published-url-preview")).toBeVisible();
    const mismatch = page.waitForResponse(isPublicationPost);
    await detail.getByTestId("register-publication").click();
    expect((await mismatch).status()).toBe(422);
    await expect(detail.getByTestId("publish-error")).toContainText("канал пакета");
    await expect(detail).toHaveAttribute("data-status", /ready|downloaded/);

    // Правильная ссылка: предпросмотр с алиасом, регистрация 201 с Idempotency-Key, статус published.
    await input.fill(publishUrl);
    await expect(detail.getByTestId("published-url-alias")).toHaveText(parsed.value.alias_code);
    await expect(detail.getByTestId("publish-error")).toHaveCount(0);
    const created = page.waitForResponse(isPublicationPost);
    await detail.getByTestId("register-publication").click();
    const response = await created;
    expect(response.status()).toBe(201);
    expect(response.request().headers()["idempotency-key"]).toBeTruthy();
    expect(response.request().postDataJSON()).toMatchObject({ package_id: Number(packageId), url: publishUrl });
    const body = (await response.json()) as { publication: { id: number; platform: string } };
    expect(body.publication.platform).toBe(platform);

    await expect(detail).toHaveAttribute("data-status", "published");
    await expect(detail.getByTestId("package-status")).toContainText("Опубликован");
    await expect(detail.getByTestId("publication-status")).toBeVisible();
    await expect(detail.getByTestId("publication-state")).toContainText("Опубликован");
    await expect(detail.getByTestId("published-url-form")).toHaveCount(0);
    await expect(detail.getByTestId("checklist-published")).toBeChecked();

    // Пакет ушёл из «К заливке» и виден в «Опубликованы».
    await page.getByRole("tab", { name: /Опубликованы/ }).click();
    await expect(page).toHaveURL(/status=published/);
    await expect(
      page.locator(`[data-testid="package-row"][data-package-id="${packageId}"][data-status="published"]`),
    ).toBeVisible();

    expect(problems, problems.join("\n")).toEqual([]);
  });

  test("вкладка «Проблемы»: публикации с непройденной проверкой канала", async ({ page, baseURL }) => {
    const problems = watchConsole(page, baseURL);
    await page.goto("/packages/?status=problems");
    await login(page);
    await expect(page).toHaveURL(/status=problems/);
    const panel = page.getByTestId("problems-panel");
    await expect(panel).toBeVisible();
    if (MOCKS) {
      const item = panel.locator('[data-testid="problem-publication"]').first();
      await expect(item).toBeVisible();
      await expect(item.getByTestId("publication-status")).toHaveAttribute("data-verify", "channel_mismatch");
      await expect(item.getByRole("button", { name: "Проверить снова" })).toBeVisible();
    }
    expect(problems, problems.join("\n")).toEqual([]);
  });
});

/**
 * Автозалив пакета через YouTube API (0019) — только на моках: 501 (taro_main подключён) ставится в слот,
 * снимается, ставится на время и заливается «сейчас»; 502 (astro_main не подключён) — ошибка и 409
 * `autopublish_blocked` человеческим текстом.
 */
test.describe("пакеты: автозалив", () => {
  test.skip(!MOCKS, "Автозалив меняет очередь — только на моках");

  test("ближайший слот, снять, на время, сейчас → залит", async ({ page, baseURL }) => {
    const problems = watchConsole(page, baseURL);
    await page.goto("/packages/?package=501");
    await login(page);
    const detail = page.getByTestId("package-detail");
    await expect(detail).toHaveAttribute("data-package-id", "501");
    const block = detail.getByTestId("autopublish-block");
    await expect(block).toHaveAttribute("data-state", "none");

    // В ближайший слот: POST с пустым планом времени → «В очереди», время слота.
    const slot = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().endsWith("/api/v1/packages/501/autopublish"),
    );
    await block.getByTestId("autopublish-slot").click();
    expect((await slot).postDataJSON()).toEqual({ now: false });
    await expect(block).toHaveAttribute("data-state", "scheduled");
    await expect(block.getByTestId("autopublish-at")).toContainText("МСК");
    await expect(page.getByText("В очереди автозалива")).toBeVisible();
    await expect(
      page.locator('[data-testid="package-row"][data-package-id="501"]').getByText("В очереди"),
    ).toBeVisible();

    // Снять с очереди.
    await block.getByTestId("autopublish-cancel").click();
    await expect(block).toHaveAttribute("data-state", "cancelled");

    // На время: поле МСК → ISO со смещением +03:00.
    const tomorrow = new Date(Date.now() + 24 * 3600_000 + 3 * 3600_000).toISOString().slice(0, 10);
    await block.getByTestId("autopublish-at-toggle").click();
    await block.getByLabel("Когда залить (МСК)").fill(`${tomorrow}T10:30`);
    const timed = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().endsWith("/api/v1/packages/501/autopublish"),
    );
    await block.getByTestId("autopublish-at-submit").click();
    expect((await timed).postDataJSON()).toEqual({ now: false, at: `${tomorrow}T10:30:00+03:00` });
    await expect(block).toHaveAttribute("data-state", "scheduled");
    await expect(block.getByTestId("autopublish-at")).toContainText("10:30");

    // Сейчас (с подтверждением): заливается → залит, ссылка на ролик, пакет опубликован.
    page.once("dialog", (dialog) => void dialog.accept());
    await block.getByTestId("autopublish-now").click();
    await expect(block).toHaveAttribute("data-state", /uploading|done/);
    await expect(block).toHaveAttribute("data-state", "done", { timeout: 20_000 });
    await expect(block.getByTestId("autopublish-video")).toHaveAttribute(
      "href",
      /^https:\/\/www\.youtube\.com\/shorts\//,
    );
    await expect(detail).toHaveAttribute("data-status", "published");
    expect(problems, problems.join("\n")).toEqual([]);
  });

  test("канал не подключён: ошибка автозалива и 409 по-русски, снять с очереди", async ({ page }) => {
    await page.goto("/packages/?package=502");
    await login(page);
    const block = page.getByTestId("package-detail").getByTestId("autopublish-block");
    await expect(block).toHaveAttribute("data-state", "failed");
    await expect(block.getByTestId("autopublish-state-error")).toContainText("Канал не подключён");

    const blocked = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" && response.url().endsWith("/api/v1/packages/502/autopublish"),
    );
    await block.getByTestId("autopublish-slot").click();
    expect((await blocked).status()).toBe(409);
    await expect(block.getByTestId("autopublish-error")).toContainText(
      "Автозалив невозможен: Канал не подключён к YouTube",
    );
    await expect(block.getByRole("link", { name: "Открыть карточку канала" })).toHaveAttribute(
      "href",
      "/channels/?channel=astro_main",
    );

    await block.getByTestId("autopublish-cancel").click();
    await expect(block).toHaveAttribute("data-state", "cancelled");
  });
});

/** Живой разбор ссылки повторяет `domain/url_parse.py` (выборка из tests/unit/test_url_parse.py). */
test.describe("platform-url", () => {
  const YT = "dQw4w9WgXcQ";
  const ok: Array<[string, string, string]> = [
    [`https://www.youtube.com/shorts/${YT}`, `yt_${YT}`, "short"],
    [`youtube.com/shorts/${YT}?si=AbCdEf123`, `yt_${YT}`, "short"],
    [`https://youtu.be/${YT}?si=xyz&t=5`, `yt_${YT}`, "video"],
    [`https://www.youtube.com/watch?feature=share&v=${YT}`, `yt_${YT}`, "video"],
    [`https://studio.youtube.com/video/${YT}/edit`, `yt_${YT}`, "video"],
    [`https://www.youtube.com/attribution_link?a=x&u=/watch%3Fv%3D${YT}%26feature%3Dshare`, `yt_${YT}`, "video"],
    ["https://vk.com/clip-123456_456239017", "vk_-123456_456239017", "clip"],
    ["https://vk.com/clips-123456?z=clip-123456_456239017", "vk_-123456_456239017", "clip"],
    ["https://vk.com/video?z=video-123_456%2Fpl_cat_trends", "vk_-123_456", "video"],
    ["https://vk.com/video_ext.php?oid=-123&id=456&hash=abcdef", "vk_-123_456", "video"],
  ];
  for (const [url, alias, kind] of ok) {
    test(`разбирает ${url.slice(0, 60)}`, () => {
      const result = parseVideoUrl(url);
      expect(result.ok).toBe(true);
      if (result.ok) {
        expect(result.value.alias_code).toBe(alias);
        expect(result.value.platform_kind).toBe(kind);
      }
    });
  }
  const rejected: Array<[string, string]> = [
    ["", "empty"],
    ["https://www.youtube.com/shorts/привет12345", "non_ascii"],
    ["javascript:alert(1)//youtube.com/shorts/dQw4w9WgXcQ", "bad_scheme"],
    ["https://youtube.com.evil.example/shorts/dQw4w9WgXcQ", "unsupported_host"],
    ["https://www.youtube.com/playlist?list=PL123", "not_a_video"],
    ["https://youtu.be/<script>", "bad_video_id"],
    ["https://vk.com/clip-0_1", "not_a_video"],
  ];
  for (const [url, reason] of rejected) {
    test(`отклоняет «${url.slice(0, 50)}» → ${reason}`, () => {
      const result = parseVideoUrl(url);
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.reason).toBe(reason);
    });
  }
});
