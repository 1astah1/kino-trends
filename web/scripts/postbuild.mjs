#!/usr/bin/env node
/**
 * После `next build` (npm запускает `postbuild` автоматически).
 * В боевом статическом экспорте не должно быть воркера MSW: Next копирует весь `public/` в `out/`,
 * а `public/mockServiceWorker.js` нужен только сборкам с моками (`NEXT_PUBLIC_API_MOCKING=1`).
 */
import { rmSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

if (process.env.NEXT_EXPORT === "1" && process.env.NEXT_PUBLIC_API_MOCKING !== "1") {
  const outDir = resolve(process.cwd(), process.env.NEXT_EXPORT_DIR || "out");
  const worker = join(outDir, "mockServiceWorker.js");
  try {
    statSync(worker);
    rmSync(worker);
    console.log(`postbuild: удалён ${worker} (моки не входят в боевой экспорт)`);
  } catch {
    // файла нет — ничего не делаем
  }
}
