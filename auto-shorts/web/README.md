# web — операторская консоль Auto Shorts

Next.js 16 + React 19 + Tailwind 4, статический экспорт на том же origin, что и API.
Архитектура: `docs/ARCHITECTURE.md` §7. Ноль внешних ресурсов в рантайме (§7.7).

## Команды

| Команда                       | Что делает                                                                                            |
| ----------------------------- | ----------------------------------------------------------------------------------------------------- |
| `npm run dev`                 | `next dev` на 127.0.0.1:18301, `/api/*` проксируется на API 127.0.0.1:18300 (`AS_API_ORIGIN`)         |
| `npm run dev:mock`            | то же, но API подменяют MSW-моки (`mocks/`): admin / admin-demo-password                              |
| `npm run typecheck`           | `next typegen` + `tsc --noEmit`                                                                       |
| `npm run lint`                | ESLint (в т. ч. запрет `next/font/google`)                                                            |
| `npm run test:unit`           | юнит-тесты чистых модулей (`tests/unit`, раннер Playwright без браузера)                              |
| `NEXT_EXPORT=1 npm run build` | статический экспорт в `out/` (воркер MSW из экспорта удаляется в `postbuild`)                         |
| `npm run check:external`      | проверка `out/` на внешние URL; ненулевой код — сборку не выкладывать                                 |
| `npm run build:export`        | экспорт + проверка одной командой                                                                     |
| `npm run e2e:mock`            | экспорт с моками в `out-mock/` + Playwright `e2e/` через `scripts/serve-static.mjs` (CSP как в nginx) |
| `npm run e2e`                 | Playwright против стенда: `E2E_BASE_URL`, `E2E_LOGIN`, `E2E_PASSWORD`                                 |
| `npm run gen:api`             | перегенерация `lib/api/schema.d.ts` из OpenAPI (делает владелец контрактов, руками не править)        |

Сборка на боксе с живыми сервисами: `nice -n 10`, `NODE_OPTIONS=--max-old-space-size=1536`,
перед запуском MemAvailable > 1,2 ГБ. Лучше собирать в CI и отвозить `out/` (≈3 МБ).

Playwright браузеры не скачивает: если нет бандла нужной ревизии, используется системный Chrome
(`channel: "chrome"`) или `E2E_CHROMIUM_PATH`.

## Как устроено

- `app/(console)/*` — экраны за `AuthGate` → `AppShell`; `app/login` — вход.
  Детали адресуются query-параметрами (`?job=`, `?tab=`), динамических сегментов нет.
- `lib/nav.ts` — все маршруты v1; ещё не готовые показывают заглушку «Скоро» с этапом.
  Поток этапа меняет `status` своего маршрута и кладёт страницу, не трогая сайдбар и палитру.
- `lib/api/client.ts` — openapi-fetch по `schema.d.ts`, cookie-сессия, CSRF double-submit
  (`X-CSRF-Token` = cookie `as_csrf`), классы ошибок 401/409/422/423/507 в `lib/api/errors.ts`.
- `lib/api/sse.ts` — один `EventSource('/api/v1/events/stream')`; нет событий 45 с → «опрос»,
  экраны переходят на `refetchInterval` 10 с (`lib/hooks/use-live-refetch.ts`).
- `lib/api/media.ts` — медиа только по URL из ответов API.
- `components/ui/*` — UI-кит, скопирован из retentionos (источник указан в каждом файле).
- Токены «Аппаратная» — `app/globals.css` (тёмная тема по умолчанию), шрифты — `@fontsource-variable/*`.
