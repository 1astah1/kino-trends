"use client";

import { type ReactNode, useEffect, useState } from "react";

/**
 * MSW-моки API (§10.1 п. 5: фронт работает на моках по типам контракта параллельно с бэкендом).
 * Включаются только сборкой с `NEXT_PUBLIC_API_MOCKING=1` (`npm run dev:mock`, `npm run e2e:mock`).
 *
 * Условие записано прямо в `if (process.env.NEXT_PUBLIC_API_MOCKING === "1")`: бандлер подставляет
 * значение при сборке и выбрасывает ветку вместе с `import("@/mocks/browser")`, поэтому в боевом
 * экспорте нет ни кода MSW, ни его чанка (проверяет scripts/check-no-external.mjs).
 */
export const isMocking = process.env.NEXT_PUBLIC_API_MOCKING === "1";

export function MockGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(process.env.NEXT_PUBLIC_API_MOCKING !== "1");

  useEffect(() => {
    if (process.env.NEXT_PUBLIC_API_MOCKING === "1") {
      let cancelled = false;
      import("@/mocks/browser")
        .then(({ startMockWorker }) => startMockWorker())
        .catch((error: unknown) => {
          console.error("[msw] не удалось запустить моки", error);
        })
        .finally(() => {
          if (!cancelled) setReady(true);
        });
      return () => {
        cancelled = true;
      };
    }
  }, []);

  if (!ready) {
    return (
      <div className="flex h-dvh items-center justify-center text-sm text-fg-muted" role="status">
        Запуск моков API…
      </div>
    );
  }
  return <>{children}</>;
}
