import { existsSync } from "node:fs";

import { chromium, defineConfig, devices } from "@playwright/test";

/**
 * E2E (docs/ARCHITECTURE.md §1.5, §10.2 M1: «на стенде под логином»).
 *
 * - Стенд: `E2E_BASE_URL=https://admin.<стенд> E2E_LOGIN=… E2E_PASSWORD=… npx playwright test e2e/login.spec.ts`
 *   — сервер не поднимается, проверяется настоящий API под логином.
 * - Локально на моках: `npm run e2e:mock` собирает экспорт с MSW в `out-mock/` и раздаёт его
 *   scripts/serve-static.mjs на 127.0.0.1:18301 с тем же CSP, что nginx в проде.
 * - Против локального API без nginx: `NEXT_EXPORT=1 npm run build`, затем `E2E_STATIC_DIR=out`
 *   и API на 127.0.0.1:18300 (serve-static проксирует `/api/*`).
 *
 * Браузеры не скачиваются: если нет бандла Playwright нужной ревизии, берётся системный Chrome
 * (`channel: "chrome"`) или путь из `E2E_CHROMIUM_PATH`.
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:18301";
const external = Boolean(process.env.E2E_BASE_URL);
const staticDir = process.env.E2E_STATIC_DIR ?? "out-mock";

function browserOptions() {
  if (process.env.E2E_CHROMIUM_PATH) return { launchOptions: { executablePath: process.env.E2E_CHROMIUM_PATH } };
  if (process.env.E2E_BROWSER_CHANNEL) return { channel: process.env.E2E_BROWSER_CHANNEL };
  let bundled = "";
  try {
    bundled = chromium.executablePath();
  } catch {
    bundled = "";
  }
  if (bundled && existsSync(bundled)) return {};
  if (existsSync("/usr/bin/google-chrome") || existsSync("/opt/google/chrome/chrome")) return { channel: "chrome" };
  return {};
}

export default defineConfig({
  testDir: "e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results",
  use: {
    baseURL,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1360, height: 860 }, ...browserOptions() },
    },
  ],
  webServer: external
    ? undefined
    : {
        command: `node scripts/serve-static.mjs --dir ${staticDir} --port 18301`,
        url: `${baseURL}/login/`,
        reuseExistingServer: false,
        timeout: 30_000,
        stdout: "ignore",
        stderr: "pipe",
      },
});
