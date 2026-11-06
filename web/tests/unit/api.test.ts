import { expect, test } from "@playwright/test";

import { csrfMiddleware, onAuthLost, readCookie, setCsrfToken, unwrap } from "@/lib/api/client";
import {
  ApiError,
  BudgetPausedError,
  ConflictError,
  InsufficientStorageError,
  NotAuthenticatedError,
  toApiError,
  ValidationError,
} from "@/lib/api/errors";
import { downloadHref, isApiMediaUrl, mediaSrc } from "@/lib/api/media";

type MiddlewareParams = Parameters<NonNullable<typeof csrfMiddleware.onRequest>>[0];

function params(request: Request, schemaPath = "/api/v1/users"): MiddlewareParams {
  return { request, schemaPath, params: {}, id: "t", options: {} as MiddlewareParams["options"] };
}

test.describe("ошибки API (§5.1, §7.4)", () => {
  test("статус → класс ошибки, сообщение сервера сохраняется", () => {
    const body = {
      error: { code: "job_state_conflict", message: "Уже решено в Telegram", details: { decided_via: "telegram" } },
    };
    const conflict = toApiError(409, body);
    expect(conflict).toBeInstanceOf(ConflictError);
    expect(conflict.message).toBe("Уже решено в Telegram");
    expect(conflict.code).toBe("job_state_conflict");
    expect(conflict.details).toEqual({ decided_via: "telegram" });
    expect(toApiError(401, null)).toBeInstanceOf(NotAuthenticatedError);
    expect(toApiError(423, null)).toBeInstanceOf(BudgetPausedError);
    expect(toApiError(507, null)).toBeInstanceOf(InsufficientStorageError);
    expect(toApiError(500, "Internal Server Error").message).toBe("Ошибка сервера");
  });

  test("422 → ошибки по полям без префикса body", () => {
    const error = toApiError(422, {
      error: {
        code: "validation_error",
        message: "Проверьте поля",
        details: {
          errors: [
            { loc: ["body", "login"], msg: "Недопустимый логин", type: "x" },
            { loc: ["body", "password"], msg: "Коротко", type: "y" },
          ],
        },
      },
    });
    expect(error).toBeInstanceOf(ValidationError);
    expect(error.fieldErrors).toEqual({ login: "Недопустимый логин", password: "Коротко" });
  });

  test("unwrap: данные при 2xx, класс ошибки иначе, сеть → статус 0", async () => {
    const ok = await unwrap(Promise.resolve({ data: { a: 1 }, response: new Response(null, { status: 200 }) }));
    expect(ok).toEqual({ a: 1 });
    await expect(
      unwrap(
        Promise.resolve({
          error: { error: { code: "budget_paused", message: "Пауза" } },
          response: new Response(null, { status: 423 }),
        }),
      ),
    ).rejects.toBeInstanceOf(BudgetPausedError);
    const network = await unwrap(Promise.reject(new TypeError("Failed to fetch"))).catch((e: unknown) => e);
    expect(network).toBeInstanceOf(ApiError);
    expect((network as ApiError).status).toBe(0);
  });
});

test.describe("CSRF double-submit (§5.1)", () => {
  test("мутации несут X-CSRF-Token, GET — нет", async () => {
    setCsrfToken("tok-from-me");
    const post = new Request("http://x/api/v1/users", { method: "POST" });
    await csrfMiddleware.onRequest?.(params(post));
    expect(post.headers.get("X-CSRF-Token")).toBe("tok-from-me");

    const get = new Request("http://x/api/v1/users");
    await csrfMiddleware.onRequest?.(params(get));
    expect(get.headers.get("X-CSRF-Token")).toBeNull();
    setCsrfToken(null);
  });

  test("cookie as_csrf читается по имени", () => {
    expect(readCookie("as_csrf", "a=1; as_csrf=abc%20def; b=2")).toBe("abc def");
    expect(readCookie("as_csrf", "as_csrf_x=1")).toBeNull();
  });

  test("401 на рабочем запросе сообщает о потере сессии, на /auth/me — нет", async () => {
    let lost = 0;
    const off = onAuthLost(() => {
      lost += 1;
    });
    const response = new Response(null, { status: 401 });
    await csrfMiddleware.onResponse?.({ ...params(new Request("http://x/api/v1/users")), response });
    await csrfMiddleware.onResponse?.({
      ...params(new Request("http://x/api/v1/auth/me"), "/api/v1/auth/me"),
      response,
    });
    off();
    expect(lost).toBe(1);
  });
});

test.describe("медиа только из API (§7.4, §7.7)", () => {
  test("принимаются только /api/v1/… того же origin", () => {
    expect(isApiMediaUrl("/api/v1/jobs/1/media/preview")).toBe(true);
    expect(isApiMediaUrl("https://cdn.example.com/a.mp4")).toBe(false);
    expect(isApiMediaUrl("//cdn.example.com/a.mp4")).toBe(false);
    expect(isApiMediaUrl("/api/v1/../../etc/passwd")).toBe(false);
    expect(isApiMediaUrl("/media/a.mp4")).toBe(false);
    expect(mediaSrc("blob:http://x/uuid")).toBe("blob:http://x/uuid");
    expect(mediaSrc("https://i.ytimg.com/vi/x/0.jpg")).toBeUndefined();
  });

  test("ссылка скачивания добавляет download=1", () => {
    expect(downloadHref("/api/v1/packages/77/video")).toBe("/api/v1/packages/77/video?download=1");
    expect(downloadHref("/api/v1/jobs/1/media/final?v=2")).toBe("/api/v1/jobs/1/media/final?v=2&download=1");
    expect(downloadHref("https://evil/x")).toBeUndefined();
  });
});
