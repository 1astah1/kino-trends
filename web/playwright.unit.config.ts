import { defineConfig } from "@playwright/test";

/**
 * Юнит-тесты чистых модулей (lib/*, scripts/check-no-external.mjs) раннером Playwright:
 * без браузера и без веб-сервера, пути `@/…` берутся из tsconfig. `npm run test:unit`.
 */
export default defineConfig({
  testDir: "tests/unit",
  testMatch: /.*\.test\.ts$/,
  timeout: 10_000,
  workers: 1,
  reporter: [["list"]],
  outputDir: "test-results/unit",
});
