import { setupWorker } from "msw/browser";

import { handlers } from "./handlers";

/**
 * Запуск MSW в браузере. Подключается только из components/common/mock-gate.tsx при
 * `NEXT_PUBLIC_API_MOCKING=1`; в обычной сборке этот модуль в бандл не попадает.
 * Воркер `public/mockServiceWorker.js` генерирует msw (`msw.workerDirectory` в package.json).
 */
export async function startMockWorker() {
  const worker = setupWorker(...handlers);
  await worker.start({
    onUnhandledRequest: "bypass",
    quiet: true,
    serviceWorker: { url: "/mockServiceWorker.js" },
  });
  return worker;
}
