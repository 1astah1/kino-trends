import type { NextConfig } from "next";

/**
 * Доставка (docs/ARCHITECTURE.md §7.1):
 * - `NEXT_EXPORT=1` → статический экспорт (`output: 'export'`) в `out/`; Node-сервера в проде нет,
 *   `out/` раздаёт nginx (`try_files $uri $uri/ $uri.html =404`).
 *   `NEXT_EXPORT_DIR=out-mock` кладёт экспорт в другой каталог (сборка с MSW-моками для e2e не
 *   должна затирать боевой `out/`).
 * - иначе `next dev` с rewrites `/api/:path*` → API на 127.0.0.1:18300 (тот же origin для cookie).
 */
const isExport = process.env.NEXT_EXPORT === "1";
const exportDir = process.env.NEXT_EXPORT_DIR;
const apiOrigin = process.env.AS_API_ORIGIN ?? "http://127.0.0.1:18300";

const nextConfig: NextConfig = {
  // Всегда подставляется при сборке (иначе незаданная переменная остаётся рантайм-обращением
  // к process.env и бандлер не может выбросить ветку с MSW из боевого экспорта).
  env: {
    NEXT_PUBLIC_API_MOCKING: process.env.NEXT_PUBLIC_API_MOCKING === "1" ? "1" : "0",
  },
  reactStrictMode: true,
  poweredByHeader: false,
  trailingSlash: true,
  // `/api/v1/...` живёт без слэша на конце; редирект trailingSlash превратил бы
  // `/api/v1/auth/me` в `/api/v1/auth/me/` ещё до rewrite, и FastAPI ответил бы 404.
  skipTrailingSlashRedirect: true,
  images: { unoptimized: true },
  productionBrowserSourceMaps: false,
  // Доступ к `next dev` не с localhost (например, по IP стенда): иначе HMR и гидрация молча не работают.
  allowedDevOrigins: (process.env.NEXT_ALLOWED_DEV_ORIGINS ?? "")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean),
  ...(isExport
    ? { output: "export" as const, ...(exportDir ? { distDir: exportDir } : {}) }
    : {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
        },
      }),
};

export default nextConfig;
