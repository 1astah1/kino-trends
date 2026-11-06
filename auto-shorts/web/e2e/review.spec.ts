import { expect, type Page, test } from "@playwright/test";

/**
 * Приёмка под логином (docs/ARCHITECTURE.md §10.2, критерий M2: `review.spec` — A, затем U, затем X+3).
 *
 * - На моках (`npm run e2e:mock`): учётка admin / admin-demo-password, очередь из mocks/handlers/production.ts.
 * - На стенде: `E2E_BASE_URL`, `E2E_LOGIN`, `E2E_PASSWORD`; в очереди нужен хотя бы один ролик в
 *   `pending_review`. Тест меняет его судьбу: одобряет, отменяет одобрение и отклоняет с причиной «3».
 */
const LOGIN = process.env.E2E_LOGIN ?? "admin";
const PASSWORD = process.env.E2E_PASSWORD ?? "admin-demo-password";
const MOCKS = !process.env.E2E_BASE_URL;
/** Ролик моков, решённый «в Telegram» раньше оператора (REVIEW_CONFLICT_JOB_ID). */
const MOCK_CONFLICT_JOB_ID = "1205";

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

function isReviewPost(jobId: string, suffix: "review" | "review/undo") {
  return (response: { url: () => string; request: () => { method: () => string } }) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === `/api/v1/jobs/${jobId}/${suffix}`;
}

test.describe("приёмка", () => {
  test("A → U → X+3: одобрить, отменить решение, отклонить с причиной 3", async ({ page, baseURL }) => {
    const problems = watchConsole(page, baseURL);
    await page.goto("/review/");
    await login(page);
    await expect(page).toHaveURL(/\/review\//);
    await expect(page.getByRole("heading", { name: "Приёмка", level: 1 })).toBeVisible();

    const queue = page.getByTestId("review-queue");
    const pending = queue.locator('[data-testid="review-queue-item"][data-status="pending_review"]');
    const hasPending = await pending
      .first()
      .waitFor({ timeout: 15_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!hasPending, "В очереди нет ролика в pending_review");

    // Берём первый ролик, ждущий приёмки (у qc_failed клавиша A открывает «вопреки QC»).
    const target = pending.first();
    const jobId = (await target.getAttribute("data-job-id")) as string;
    const uid = (await target.getAttribute("data-uid")) as string;
    await target.click();
    await expect(page.getByTestId("review-current-uid")).toHaveText(uid);
    await expect(page.getByTestId("vertical-player")).toBeVisible();
    await expect(page.getByTestId("review-overlay")).toBeVisible();
    const itemOf = queue.locator(`[data-job-id="${jobId}"]`);
    const counter = page.getByTestId("session-counter");

    // A — одобрить: ролик уходит из очереди, открывается следующий, тост с «Отменить».
    const approved = page.waitForResponse(isReviewPost(jobId, "review"));
    await page.keyboard.press("a");
    const approveResponse = await approved;
    expect(approveResponse.status()).toBe(200);
    expect(approveResponse.request().postDataJSON()).toMatchObject({
      decision: "approve",
      expected_status: "pending_review",
    });
    await expect(itemOf).toHaveCount(0);
    await expect(page.getByText(`${uid}: одобрен`)).toBeVisible();
    await expect(counter).toHaveAttribute("data-decided", "1");

    // U — отменить последнее решение: ролик возвращается в очередь и снова открыт.
    const undone = page.waitForResponse(isReviewPost(jobId, "review/undo"));
    await page.keyboard.press("u");
    expect((await undone).status()).toBe(200);
    await expect(itemOf).toHaveCount(1);
    await expect(page.getByTestId("review-current-uid")).toHaveText(uid);
    await expect(page.getByTestId("review-facts")).toContainText("Ждёт приёмки");
    await expect(page.getByText(`${uid}: решение отменено`)).toBeVisible();
    await expect(counter).toHaveAttribute("data-decided", "0");

    // X, затем 3 — отклонить с причиной под цифрой 3 («Рассинхрон субтитров»).
    await page.keyboard.press("x");
    const reasons = page.getByTestId("reject-reasons");
    await expect(reasons).toBeVisible();
    await expect(reasons.locator('[data-reason="subtitry_rassinhron"]')).toContainText("3");
    const rejected = page.waitForResponse(isReviewPost(jobId, "review"));
    await page.keyboard.press("3");
    const rejectResponse = await rejected;
    expect(rejectResponse.status()).toBe(200);
    expect(rejectResponse.request().postDataJSON()).toMatchObject({
      decision: "reject",
      reject_reason: "subtitry_rassinhron",
      expected_status: "pending_review",
    });
    await expect(reasons).toHaveCount(0);
    await expect(itemOf).toHaveCount(0);
    await expect(page.getByText(`${uid}: отклонён`)).toBeVisible();
    await expect(page.getByText("Причина: Рассинхрон субтитров")).toBeVisible();
    await expect(counter).toHaveAttribute("data-decided", "1");

    expect(problems).toEqual([]);
  });

  test("409: ролик уже решён в Telegram — понятное сообщение, ролик уходит из очереди", async ({ page, baseURL }) => {
    test.skip(!MOCKS, "Нужен мок-ролик, решённый в Telegram раньше веба");
    const problems = watchConsole(page, baseURL);
    await page.goto(`/review/?job=${MOCK_CONFLICT_JOB_ID}`);
    await login(page);
    await expect(page).toHaveURL(new RegExp(`/review/\\?job=${MOCK_CONFLICT_JOB_ID}`));
    const queue = page.getByTestId("review-queue");
    const item = queue.locator(`[data-job-id="${MOCK_CONFLICT_JOB_ID}"]`);
    await expect(item).toHaveCount(1);
    const uid = (await item.getAttribute("data-uid")) as string;
    await expect(page.getByTestId("review-current-uid")).toHaveText(uid);

    const conflict = page.waitForResponse(isReviewPost(MOCK_CONFLICT_JOB_ID, "review"));
    await page.keyboard.press("a");
    expect((await conflict).status()).toBe(409);

    const alert = page.getByTestId("review-alert");
    await expect(alert).toBeVisible();
    await expect(alert).toHaveAttribute("data-kind", "conflict");
    await expect(alert).toContainText(`Ролик ${uid} уже решён в Telegram`);
    await expect(alert).toContainText("Оператор смены");
    await expect(item).toHaveCount(0);
    await expect(page.getByTestId("session-counter")).toHaveAttribute("data-decided", "0");
    expect(problems).toEqual([]);
  });

  test("клавиши: X открывает причины, Esc закрывает, ? — справка", async ({ page }) => {
    await page.goto("/review/");
    await login(page);
    const firstItem = page.getByTestId("review-queue-item").first();
    const hasItems = await firstItem
      .waitFor({ timeout: 15_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!hasItems, "Очередь приёмки пуста");
    await expect(page.getByTestId("review-current-uid")).toBeVisible();

    await page.keyboard.press("x");
    await expect(page.getByTestId("reject-reasons")).toBeVisible();
    // A в режиме выбора причины не одобряет ролик.
    await page.keyboard.press("a");
    await expect(page.getByTestId("reject-reasons")).toBeVisible();
    await expect(page.getByTestId("session-counter")).toHaveAttribute("data-decided", "0");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("reject-reasons")).toHaveCount(0);

    await page.keyboard.press("Shift+Slash");
    await expect(page.getByTestId("hotkeys-help")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("hotkeys-help")).toHaveCount(0);

    // Пока курсор в поле комментария, клавиши молчат.
    const comment = page.getByLabel("Комментарий к решению");
    await comment.click();
    await comment.pressSequentially("xa");
    await expect(comment).toHaveValue("xa");
    await expect(page.getByTestId("reject-reasons")).toHaveCount(0);
    await expect(page.getByTestId("session-counter")).toHaveAttribute("data-decided", "0");
  });
});
