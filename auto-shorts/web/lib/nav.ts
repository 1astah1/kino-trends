/**
 * Карта маршрутов консоли v1 (docs/ARCHITECTURE.md §7.3, §10.1 п. 2).
 *
 * Здесь перечислены **все** экраны v1. С 24.10.2026 все экраны рабочие (`status: "ready"`);
 * механизм заглушки «Скоро» (`status: "soon"` + ComingSoon) оставлен для будущих экранов:
 * новый маршрут добавляется сюда и кладёт страницу в `app/(console)/<path>/page.tsx`,
 * не трогая сайдбар, шапку и палитру.
 *
 * Все сегменты и параметры латиницей; кириллица — только в подписях.
 */
import {
  Box,
  CalendarClock,
  ChartColumn,
  ChartLine,
  Clapperboard,
  ClipboardCheck,
  FilePenLine,
  Film,
  Layers,
  LayoutDashboard,
  ListTodo,
  type LucideIcon,
  Megaphone,
  Package,
  PanelsTopLeft,
  Radar,
  Scissors,
  Settings,
  Tv,
  Workflow,
} from "lucide-react";

export type NavGroupId = "desk" | "production" | "sources" | "promotion" | "analysis" | "setup";

/** Группы по вопросу оператора (§7.3). */
export const NAV_GROUPS: ReadonlyArray<{ id: NavGroupId; label: string }> = [
  { id: "desk", label: "Пульт" },
  { id: "production", label: "Производство" },
  { id: "sources", label: "Источники" },
  { id: "promotion", label: "Продвижение" },
  { id: "analysis", label: "Анализ" },
  { id: "setup", label: "Настройка" },
];

export type Milestone = "M1" | "M2" | "M3" | "M4" | "M5" | "M6" | "M7";
export type RouteStatus = "ready" | "partial" | "soon";
/** Ключ бейджа в сайдбаре: значение приходит из кеша запросов, который обновляют SSE-события. */
export type NavBadgeKey = "review" | "packages" | "trends";

export interface NavRoute {
  /** Путь без завершающего слэша (`/production`); ссылка строится через `routeHref`. */
  path: string;
  /** Подпись в сайдбаре. */
  label: string;
  /** Название экрана (заголовок страницы, крошки). */
  title: string;
  group: NavGroupId;
  icon: LucideIcon;
  /** Этап, в котором экран становится рабочим (§10.2). */
  milestone: Milestone;
  status: RouteStatus;
  /** Для вложенных экранов — путь родителя; такие экраны не показываются в сайдбаре. */
  parent?: string;
  description: string;
  /** Состояние экрана в URL (§7.3). */
  urlState: string[];
  /** Эндпоинты `/api/v1`, из которых экран берёт данные (§7.3). */
  endpoints: string[];
  badge?: NavBadgeKey;
  /** Полноценно работает на телефоне (§7.3: Приёмка и Пакеты). */
  mobile?: boolean;
  /** Слова для поиска в палитре Ctrl+K. */
  keywords?: string[];
}

export const NAV_ROUTES: readonly NavRoute[] = [
  {
    path: "/",
    label: "Обзор",
    title: "Обзор",
    group: "desk",
    icon: LayoutDashboard,
    milestone: "M4",
    status: "ready",
    description:
      "KPI со спарклайнами и определениями, колонка «Требует тебя», малые мультипликаторы, мини-воронка домен/бот, алерты, топ роликов, здоровье конвейера; онбординг при пустой базе.",
    urlState: ["range", "channel", "basis"],
    endpoints: [
      "/overview",
      "/analytics/timeseries",
      "/alerts",
      "/analytics/videos?limit=10",
      "/jobs/stage-counts",
      "/budget",
    ],
    keywords: ["пульт", "kpi", "dashboard", "главная"],
  },
  {
    path: "/queue",
    label: "Очередь",
    title: "Очередь",
    group: "desk",
    icon: ListTodo,
    milestone: "M4",
    status: "ready",
    description:
      "Что сейчас делает система: задачи auto-shorts (воркеры, тяжёлый слот, отложенные, сбои) и фон Kino-trends (сбор, конвейер); отменить, повторить, запустить сейчас, включить и выключить автоматику.",
    urlState: ["tab"],
    endpoints: ["/tasks", "/workers", "/system/status", "/trends/runs", "/trends/pipeline", "/trends/settings"],
    keywords: ["очередь", "queue", "tasks", "задачи", "воркер", "фон", "сбор", "автозалив"],
  },
  {
    path: "/production",
    label: "Конвейер",
    title: "Конвейер",
    group: "production",
    icon: Workflow,
    milestone: "M2",
    status: "ready",
    description:
      "Лента стадий-фильтров, плотная таблица роликов, панель задачи: таймлайн стадий, мини-плеер, сценарий, сцены, QC, стоимость, логи, файлы, история; повтор, отмена, клон, приоритет, перегенерация сцены.",
    urlState: ["stage", "status", "kind", "channel", "q", "job", "tab", "page", "sort"],
    endpoints: [
      "/jobs",
      "/jobs/stage-counts",
      "/jobs/{job_id}",
      "/jobs/{job_id}/tasks",
      "/jobs/{job_id}/history",
      "/tasks/{task_id}/log/stream",
      "/system/status",
    ],
    keywords: ["production", "jobs", "ролики", "задачи", "очередь"],
  },
  {
    path: "/review",
    label: "Приёмка",
    title: "Приёмка",
    group: "production",
    icon: ClipboardCheck,
    milestone: "M2",
    status: "ready",
    badge: "review",
    mobile: true,
    description:
      "Очередь, плеер 9:16 с оверлеем мёртвых зон, баннера и субтитров, дорожка склеек и окон баннера, факты (караоке-текст, QC, флаги, похожесть). Клавиши A / R / X+1–9 / U, отмена решения 6 с.",
    urlState: ["job", "platform"],
    endpoints: [
      "/review-queue",
      "/jobs/{job_id}/review-context",
      "/jobs/{job_id}/review",
      "/jobs/{job_id}/review/undo",
      "/meta/reject-reasons",
      "/meta/safe-zones",
    ],
    keywords: ["review", "одобрить", "отклонить", "проверка"],
  },
  {
    path: "/packages",
    label: "Пакеты",
    title: "Пакеты к заливке",
    group: "production",
    icon: Package,
    milestone: "M3",
    status: "ready",
    badge: "packages",
    mobile: true,
    description:
      "Пакеты по каналам с подсказкой аккаунта, копирование полей в один клик, серверный чек-лист, поле URL с живым разбором и регистрацией публикации.",
    urlState: ["status", "channel", "package"],
    endpoints: [
      "/packages",
      "/packages/{package_id}",
      "/packages/{package_id}/checklist",
      "/packages/{package_id}/mark-uploaded",
      "/packages/{package_id}/video",
      "/publications/parse-url",
      "/publications",
    ],
    keywords: ["packages", "заливка", "публикация", "upload"],
  },
  {
    path: "/autopublish",
    label: "Запланировано",
    title: "Запланировано",
    group: "production",
    icon: CalendarClock,
    milestone: "M3",
    status: "ready",
    description:
      "Очередь автозалива через YouTube API (0019): ролики в слотах плана канала, идущие заливки, ошибки и залитые со ссылками.",
    urlState: ["state", "channel"],
    endpoints: ["/autopublish/queue", "/packages/{package_id}/autopublish", "/channels"],
    keywords: ["autopublish", "автозалив", "очередь", "слоты", "расписание"],
  },
  {
    path: "/clipping",
    label: "Нарезки",
    title: "Источники нарезки",
    group: "sources",
    icon: Scissors,
    milestone: "M5",
    status: "ready",
    description:
      "Свои и лицензированные длинные видео: прогресс обработки и место в тяжёлой очереди; добавление файлом или ссылкой с обязательной формой прав и оценкой диска.",
    urlState: ["status", "q", "page"],
    endpoints: ["/sources", "/uploads/tus", "/sources/from-upload", "/sources/from-url", "/system/status"],
    keywords: ["clipping", "sources", "источники", "видео"],
  },
  {
    path: "/clipping/source",
    label: "Студия нарезки",
    title: "Студия нарезки",
    group: "sources",
    icon: Clapperboard,
    parent: "/clipping",
    milestone: "M5",
    status: "ready",
    description:
      "Плеер прокси с рамкой 9:16, моменты со score, дорожка волны со словами и склейками, монтаж по тексту, авторский слой (хук, вставки, вывод, свой голос) и оценка оригинальности до рендера. Только десктоп.",
    urlState: ["id", "clip", "t"],
    endpoints: [
      "/sources/{source_id}",
      "/sources/{source_id}/transcript",
      "/sources/{source_id}/media/proxy",
      "/sources/{source_id}/peaks",
      "/sources/{source_id}/storyboard",
      "/sources/{source_id}/scenes",
      "/sources/{source_id}/candidates",
      "/clip-candidates/{candidate_id}",
      "/clip-candidates/{candidate_id}/jobs",
      "/clip-candidates/{candidate_id}/originality-estimate",
      "/jobs/{job_id}/author-script",
      "/jobs/{job_id}/author-voice",
    ],
    keywords: ["studio", "студия", "клип", "wavesurfer"],
  },
  {
    path: "/trends",
    label: "Трендоскаут",
    title: "Трендоскаут",
    group: "sources",
    icon: Radar,
    milestone: "M6",
    status: "ready",
    badge: "trends",
    description:
      "Мост к Kino-trends: подборка дня (фильмы-тренды и чистые видео без чужой рекламы), популярные видео СНГ с проверкой прав, очередь конвейера, «В конвейер» одной кнопкой.",
    urlState: ["tab", "level", "fit", "short", "sort", "q"],
    endpoints: ["/trends/status", "/trends/today", "/trends/videos", "/trends/pipeline", "/trends/pipeline/enqueue"],
    keywords: ["trends", "kino", "тренды", "подборка", "конвейер", "кино"],
  },
  {
    path: "/trends/video",
    label: "Разбор ролика",
    title: "Разбор ролика",
    group: "sources",
    icon: Film,
    parent: "/trends",
    milestone: "M6",
    status: "ready",
    description:
      "Большой разбор ролика из Kino-trends: кадры и свободные зоны под баннер, разбор нейросети, оценка чистоты и прав, чарты по странам, замеры просмотров; решения оператора и «В конвейер».",
    urlState: ["id"],
    endpoints: ["/trends/videos/{video_id}", "/trends/videos/{video_id}/frames", "/trends/pipeline/enqueue"],
    keywords: ["ролик", "разбор", "кадры", "чистота", "права"],
  },
  {
    path: "/trends/film",
    label: "Разбор фильма",
    title: "Разбор фильма",
    group: "sources",
    icon: Clapperboard,
    parent: "/trends",
    milestone: "M6",
    status: "ready",
    description:
      "Бриф по фильму или сериалу из Kino-trends: почему заходит в Shorts, лучшие нарезки-референсы, исходник и права, что и как резать, «В конвейер».",
    urlState: ["id", "period"],
    endpoints: ["/trends/films/{title_id}", "/trends/films/{title_id}/rights", "/trends/pipeline/enqueue"],
    keywords: ["фильм", "сериал", "кино", "бриф", "права", "нарезка"],
  },
  {
    path: "/products",
    label: "Продукты и баннеры",
    title: "Продукты и баннеры",
    group: "promotion",
    icon: Megaphone,
    milestone: "M2",
    status: "ready",
    description: "Продукты (umka.chat, магазин ключей, VPN-бот), кампании и варианты баннера с весами.",
    urlState: [],
    endpoints: ["/products", "/campaigns"],
    keywords: ["products", "campaigns", "баннер", "кампании"],
  },
  {
    path: "/products/view",
    label: "Продукт",
    title: "Карточка продукта",
    group: "promotion",
    icon: Box,
    parent: "/products",
    milestone: "M2",
    status: "ready",
    description:
      "Лендинг, редиректор, deeplink, UTM; кампании и варианты баннера, вебхук конверсий. A/B-статистика — M4.",
    urlState: ["id", "tab"],
    endpoints: [
      "/products/{product_id}",
      "/campaigns",
      "/campaigns/{campaign_id}/variants",
      "/products/{product_id}/integration",
      "/campaigns/{campaign_id}/experiment",
    ],
    keywords: ["product", "webhook", "a/b"],
  },
  {
    path: "/products/banner",
    label: "Редактор баннера",
    title: "Редактор баннера",
    group: "promotion",
    icon: PanelsTopLeft,
    parent: "/products",
    milestone: "M2",
    status: "ready",
    description:
      "Загруженный креатив владельца на кадре 1080×1920: перетаскивание и масштаб в safe zones площадок, тайминг, служебные слои (код ролика, «Реклама · erid»), живая проверка и «Кадр из рендера» тем же кодом, что и ролик.",
    urlState: ["variant", "template", "campaign", "platform", "t"],
    endpoints: [
      "/banner-variants/{variant_id}",
      "/banner-creatives",
      "/banner-safe-zones",
      "/banner-fonts",
      "/banner-variants/validate",
      "/banner-variants/preview",
      "/banner-templates/{template_id}",
    ],
    keywords: ["banner", "creative", "safe zones", "креатив", "плашка"],
  },
  {
    path: "/analytics",
    label: "Аналитика",
    title: "Аналитика",
    group: "analysis",
    icon: ChartColumn,
    milestone: "M4",
    status: "ready",
    description:
      "Воронка двух веток, таблица роликов с CSV, разрезы (медиана и p75), когорты, удержание, стоимость опубликованного ролика; плашка свежести данных.",
    urlState: ["tab", "date_from", "date_to", "platform", "channel_id", "niche_id", "product_id", "basis", "mode"],
    endpoints: [
      "/analytics/summary",
      "/analytics/timeseries",
      "/analytics/breakdown",
      "/analytics/videos",
      "/analytics/cohorts",
      "/analytics/funnel",
      "/analytics/costs",
    ],
    keywords: ["analytics", "воронка", "когорты", "csv"],
  },
  {
    path: "/analytics/video",
    label: "Карточка ролика",
    title: "Карточка ролика",
    group: "analysis",
    icon: ChartLine,
    parent: "/analytics",
    milestone: "M4",
    status: "ready",
    description:
      "Просмотры от возраста на фоне коридора p25–p75 канала, удержание с классом провала, переходы по часам, конверсии, стоимость.",
    urlState: ["id"],
    endpoints: [
      "/publications/{publication_id}",
      "/publications/{publication_id}/metrics",
      "/publications/{publication_id}/retention",
      "/publications/{publication_id}/traffic",
    ],
    keywords: ["video", "publication", "удержание"],
  },
  {
    path: "/niches",
    label: "Ниши",
    title: "Ниши",
    group: "setup",
    icon: Layers,
    milestone: "M6",
    status: "ready",
    description: "Список ниш, версии конфигов, импорт и экспорт YAML.",
    urlState: [],
    endpoints: ["/niches", "/niches/import-yaml"],
    keywords: ["niches", "yaml", "конфиг"],
  },
  {
    path: "/niches/edit",
    label: "Редактор ниши",
    title: "Редактор ниши",
    group: "setup",
    icon: FilePenLine,
    parent: "/niches",
    milestone: "M6",
    status: "ready",
    description:
      "Форма-зеркало YAML (сценарий, хуки, запрещённые фразы с тестером, голос, визуал, субтитры, длительность, хэштеги), сухой прогон, сырой YAML, история версий с diff.",
    urlState: ["id"],
    endpoints: [
      "/niches/{niche_id}",
      "/niches/{niche_id}/versions",
      "/niches/{niche_id}/validate",
      "/niches/{niche_id}/test-script",
      "/niches/{niche_id}/voice-sample",
      "/text/check-forbidden",
      "/providers",
    ],
    keywords: ["niche", "prompts", "forbidden"],
  },
  {
    path: "/channels",
    label: "Каналы",
    title: "Каналы",
    group: "setup",
    icon: Tv,
    milestone: "M2",
    status: "ready",
    description:
      "Таблица каналов (куст, статус, подписчики, загрузки против плана, охват), карточка с формой, журналом инцидентов и предупреждением о совпадающем профиле.",
    urlState: ["channel", "view"],
    endpoints: [
      "/channels",
      "/channels/{channel_id}/health",
      "/channels/{channel_id}/incidents",
      "/channels/{channel_id}/tracking-link",
      "/channels/{channel_id}/oauth/youtube/start",
    ],
    keywords: ["channels", "youtube", "vk", "кусты"],
  },
  {
    path: "/settings",
    label: "Настройки",
    title: "Настройки",
    group: "setup",
    icon: Settings,
    milestone: "M1",
    status: "ready",
    description: "Провайдеры, бюджет, воркер, атрибуция, уведомления, пользователи и роли, аудит, система.",
    urlState: ["tab"],
    endpoints: [
      "/providers",
      "/secrets",
      "/budget",
      "/settings",
      "/users",
      "/audit-log",
      "/system/status",
      "/maintenance/*",
    ],
    keywords: ["settings", "users", "system", "пользователи", "система"],
  },
];

export type SettingsTabId =
  "providers" | "budget" | "worker" | "attribution" | "notifications" | "users" | "audit" | "system";

export interface SettingsTab {
  id: SettingsTabId;
  label: string;
  milestone: Milestone;
  status: RouteStatus;
  adminOnly?: boolean;
  description: string;
}

/** Вкладки «Настроек» (§7.3 `tab=providers|budget|worker|attribution|notifications|users|audit|system`). */
export const SETTINGS_TABS: readonly SettingsTab[] = [
  {
    id: "providers",
    label: "Провайдеры",
    milestone: "M2",
    status: "ready",
    adminOnly: true,
    description: "Секрет только на запись, «Проверить связь», пометка ресурсоёмкости.",
  },
  {
    id: "budget",
    label: "Бюджет",
    milestone: "M2",
    status: "ready",
    description: "Дневной лимит, порог предупреждения, пауза платных шагов, курс.",
  },
  {
    id: "worker",
    label: "Воркер",
    milestone: "M2",
    status: "ready",
    description: "Слот тяжёлой очереди = 1 (только чтение), пороги допуска, очистка с оценкой.",
  },
  {
    id: "attribution",
    label: "Атрибуция",
    milestone: "M3",
    status: "ready",
    description: "Редиректор, продукт по умолчанию, трекинг-ссылки.",
  },
  {
    id: "notifications",
    label: "Уведомления",
    milestone: "M7",
    status: "ready",
    description: "Чаты Telegram для приёмки и алертов.",
  },
  {
    id: "users",
    label: "Пользователи",
    milestone: "M1",
    status: "ready",
    adminOnly: true,
    description: "Пользователи и роли.",
  },
  { id: "audit", label: "Аудит", milestone: "M2", status: "ready", description: "Журнал действий с фильтрами." },
  {
    id: "system",
    label: "Система",
    milestone: "M1",
    status: "ready",
    description: "Готовность, воркеры, очередь, диск, память, PSI, версии.",
  },
];

export const DEFAULT_SETTINGS_TAB: SettingsTabId = "system";

export function isSettingsTab(value: string | null | undefined): value is SettingsTabId {
  return SETTINGS_TABS.some((tab) => tab.id === value);
}

/** Маршруты верхнего уровня (в сайдбаре). */
export const SIDEBAR_ROUTES = NAV_ROUTES.filter((route) => !route.parent);

/** Ссылка на экран: со слэшем на конце (`trailingSlash: true`, статический экспорт `out/<path>/index.html`). */
export function routeHref(path: string, query?: Record<string, string | undefined | null>): string {
  const base = path === "/" ? "/" : `${path.replace(/\/+$/, "")}/`;
  if (!query) return base;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, value);
  }
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}

export function normalizePath(pathname: string | null | undefined): string {
  if (!pathname) return "/";
  const trimmed = pathname.replace(/\/+$/, "");
  return trimmed === "" ? "/" : trimmed;
}

/** Самый длинный совпавший путь: `/clipping/source` не подсвечивает заодно `/clipping`. */
export function resolveNavRoute(pathname: string | null | undefined): NavRoute | undefined {
  const path = normalizePath(pathname);
  let best: NavRoute | undefined;
  for (const route of NAV_ROUTES) {
    const matches = route.path === "/" ? path === "/" : path === route.path || path.startsWith(`${route.path}/`);
    if (matches && (!best || route.path.length > best.path.length)) best = route;
  }
  return best;
}

/** Пункт сайдбара, который подсвечивается для текущего пути (для вложенных — родитель). */
export function activeSidebarPath(pathname: string | null | undefined): string | undefined {
  const route = resolveNavRoute(pathname);
  return route?.parent ?? route?.path;
}

export function findRoute(path: string): NavRoute | undefined {
  return NAV_ROUTES.find((route) => route.path === path);
}

/** Крошки: группа → родитель → экран. */
export function breadcrumbsFor(pathname: string | null | undefined): Array<{ label: string; href?: string }> {
  const route = resolveNavRoute(pathname);
  if (!route) return [];
  const group = NAV_GROUPS.find((g) => g.id === route.group);
  const crumbs: Array<{ label: string; href?: string }> = [];
  if (group) crumbs.push({ label: group.label });
  if (route.parent) {
    const parent = findRoute(route.parent);
    if (parent) crumbs.push({ label: parent.label, href: routeHref(parent.path) });
  }
  crumbs.push({ label: route.title });
  return crumbs;
}

export const MILESTONE_HINT: Record<Milestone, string> = {
  M1: "M1 · фундамент",
  M2: "M2 · каталог, конвейер, приёмка",
  M3: "M3 · пакеты, публикация, атрибуция",
  M4: "M4 · метрики и аналитика",
  M5: "M5 · трек нарезки",
  M6: "M6 · трендоскаут и редактор ниш",
  M7: "M7 · прод и эксплуатация",
};
