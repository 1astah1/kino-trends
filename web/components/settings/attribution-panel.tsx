"use client";

import { ChevronDown, ChevronRight, Copy, ExternalLink, Link2, MousePointerClick, Target } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { SectionError } from "@/components/common/section-error";
import { CONVERSION_EVENT_LABELS, TRACKING_LEVEL_LABELS } from "@/components/packages/labels";
import { formatMinor, PLATFORM_LABELS, REDIRECT_MODE_LABELS } from "@/components/products/labels";
import {
  Badge,
  Button,
  DataTable,
  type DataTableColumn,
  DetailGrid,
  EmptyState,
  Input,
  KeyValue,
  Pagination,
  PillToggle,
  SectionCard,
  Select,
  Skeleton,
  SkeletonTable,
  Table,
  TableBody,
  TableHead,
  TableRow,
  Td,
  Th,
} from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { canOperate, isAdmin } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatDateTime, formatDay, formatNumber } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import {
  type AttributionLevel,
  type Click,
  type ClickRoute,
  type Conversion,
  journalItems,
  useClicksJournal,
  useConversionsJournal,
} from "@/lib/queries/journals";
import { type Product, useProducts } from "@/lib/queries/products";
import { useSettingsList } from "@/lib/queries/settings";
import {
  type TrackingLevel,
  type TrackingLink,
  useTrackingLinks,
  useTrackingLinkStats,
  useUpdateTrackingLink,
} from "@/lib/queries/tracking";

import { type SettingFieldSpec, SettingsGroupForm } from "./settings-group-form";

const CLICK_ROUTE_LABELS: Record<ClickRoute, string> = {
  landing: "Лендинг",
  to_telegram: "В Telegram",
  to_site: "На сайт",
  direct: "Прямой переход",
  unknown_code: "Неизвестный код",
};
const ATTRIBUTION_LEVEL_LABELS: Record<AttributionLevel, string> = {
  exact: "Точная (код)",
  channel: "По каналу",
  direct: "Без кода",
};
const LEVELS = Object.keys(TRACKING_LEVEL_LABELS) as TrackingLevel[];
/** `useTrackingLinks` отдаёт до 200 ссылок одним запросом — листаем и ищем по строке на клиенте. */
const LINKS_PAGE_SIZE = 25;
const SERVER_LIMIT = 200;

/**
 * Настройки → Атрибуция (§7.3, docs/ARCHITECTURE.md §6.6): продукт для неизвестного кода в редиректоре,
 * хост и режим редиректа по продуктам (ссылки на карточки), трекинг-ссылки с фильтрами и статистикой,
 * журналы кликов и конверсий. Ссылки и журналы видит любой вошедший, настройку меняет владелец.
 */
export function AttributionPanel() {
  const user = useCurrentUser();
  const admin = isAdmin(user.role);
  const operator = canOperate(user.role);
  const products = useProducts({ status: "all" });
  const productList = products.data?.items ?? [];
  const [productId, setProductId] = useState("");

  return (
    <div className="flex flex-col gap-4" data-testid="attribution-panel">
      <div>
        <h2 className="text-base font-semibold">Атрибуция</h2>
        <p className="text-xs text-fg-muted">
          Редиректор превращает короткий код из описания ролика в переход на лендинг, сайт или в Telegram и связывает
          клики и конверсии с роликом, каналом и кампанией.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <RedirectorCard admin={admin} operator={operator} products={productList} />
        <RedirectHostsCard products={productList} isPending={products.isPending} />
      </div>
      <TrackingLinksCard products={productList} productId={productId} onProductId={setProductId} operator={operator} />
      <JournalsCard productId={productId} />
    </div>
  );
}

function RedirectorCard({ admin, operator, products }: { admin: boolean; operator: boolean; products: Product[] }) {
  const settings = useSettingsList({ enabled: operator });
  const items = settings.data?.filter((item) => item.group === "attribution") ?? [];
  const current = items.find((item) => item.key === "redirect_default_product")?.value;
  const options = products.map((product) => ({ value: product.id, label: `${product.name} (${product.id})` }));
  if (typeof current === "string" && current && !products.some((product) => product.id === current))
    options.push({ value: current, label: `${current} (нет в каталоге)` });
  const fields: SettingFieldSpec[] = [
    {
      key: "redirect_default_product",
      label: "Продукт по умолчанию",
      kind: "select",
      options,
      hint: "куда вести, если код не найден или ссылка выключена",
    },
  ];

  return (
    <SectionCard title="Редиректор" actions={<Target className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}>
      {!operator ? (
        <p className="text-sm text-fg-muted">Настройки видят оператор и владелец.</p>
      ) : settings.isError ? (
        <SectionError error={settings.error} onRetry={() => void settings.refetch()} />
      ) : settings.isPending ? (
        <Skeleton className="h-24" />
      ) : (
        <SettingsGroupForm
          idPrefix="attribution"
          items={items}
          fields={fields}
          admin={admin}
          successMessage="Настройки редиректора сохранены"
        />
      )}
    </SectionCard>
  );
}

/** Хост и режим редиректа — поля продукта: подсказка со ссылками на карточки. */
function RedirectHostsCard({ products, isPending }: { products: Product[]; isPending: boolean }) {
  return (
    <SectionCard
      title="Хост и режим редиректа"
      actions={<Link2 className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}
    >
      <p className="text-xs text-fg-muted">
        Домен коротких ссылок (<span className="font-mono">redirect_host</span>) и режим — лендинг с выбором, сразу на
        сайт или сразу в Telegram (<span className="font-mono">redirect_mode</span>) — задаются на карточке продукта.
      </p>
      {isPending ? (
        <Skeleton className="h-20" />
      ) : products.length === 0 ? (
        <p className="text-sm text-fg-muted">Продуктов пока нет.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {products.map((product) => (
            <li key={product.id} className="flex items-center justify-between gap-2 py-1.5 text-sm">
              <div className="flex min-w-0 flex-col">
                <span className="truncate font-medium">
                  {product.name}
                  {product.status === "archived" ? (
                    <Badge variant="neutral" outline className="ml-1.5">
                      архив
                    </Badge>
                  ) : null}
                </span>
                <span className="truncate font-mono text-2xs text-fg-muted">
                  {product.redirect_host ?? "хост не задан"} · {REDIRECT_MODE_LABELS[product.redirect_mode]}
                </span>
              </div>
              <Link
                href={routeHref("/products/view", { id: product.id })}
                className="inline-flex shrink-0 items-center gap-1 text-xs text-accent hover:underline"
              >
                Открыть
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------- трекинг-ссылки

function TrackingLinksCard({
  products,
  productId,
  onProductId,
  operator,
}: {
  products: Product[];
  productId: string;
  onProductId: (value: string) => void;
  operator: boolean;
}) {
  const [level, setLevel] = useState<TrackingLevel | "">("");
  const [active, setActive] = useState<"" | "true" | "false">("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Смена фильтра — на первую страницу и свернуть раскрытую строку (без эффекта, прямо в рендере).
  const signature = `${productId}|${level}|${active}|${search.trim().toLowerCase()}`;
  const [tracked, setTracked] = useState(signature);
  if (tracked !== signature) {
    setTracked(signature);
    setOffset(0);
    setExpandedId(null);
  }

  const links = useTrackingLinks({
    productId: productId || null,
    levels: level ? [level] : undefined,
    isActive: active === "" ? null : active === "true",
  });
  const all = links.data?.items ?? [];
  const q = search.trim().toLowerCase();
  const filtered = q
    ? all.filter(
        (link) =>
          link.code.toLowerCase().includes(q) ||
          (link.label ?? "").toLowerCase().includes(q) ||
          (link.channel_id ?? "").toLowerCase().includes(q),
      )
    : all;
  const pageRows = filtered.slice(offset, offset + LINKS_PAGE_SIZE);
  const truncated = (links.data?.total ?? 0) > SERVER_LIMIT;
  const productName = new Map(products.map((product) => [product.id, product.name]));

  return (
    <SectionCard
      title="Трекинг-ссылки"
      actions={
        links.data ? (
          <span className="font-mono text-2xs text-fg-muted">
            {formatNumber(filtered.length)}
            {q ? ` из ${formatNumber(all.length)}` : ""}
          </span>
        ) : null
      }
    >
      <div className="flex flex-wrap items-end gap-2">
        <Select
          aria-label="Продукт"
          className="w-auto min-w-40"
          value={productId}
          onChange={(event) => onProductId(event.target.value)}
        >
          <option value="">Все продукты</option>
          {products.map((product) => (
            <option key={product.id} value={product.id}>
              {product.name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Уровень"
          className="w-auto"
          value={level}
          onChange={(event) => setLevel(event.target.value as TrackingLevel | "")}
        >
          <option value="">Все уровни</option>
          {LEVELS.map((value) => (
            <option key={value} value={value}>
              {TRACKING_LEVEL_LABELS[value]}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Активность"
          className="w-auto"
          value={active}
          onChange={(event) => setActive(event.target.value as "" | "true" | "false")}
        >
          <option value="">Активные и выключенные</option>
          <option value="true">Только активные</option>
          <option value="false">Только выключенные</option>
        </Select>
        <Input
          type="search"
          aria-label="Поиск по коду и подписи"
          placeholder="Код, подпись, канал…"
          className="w-auto min-w-48 font-mono"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {truncated ? (
        <p className="text-2xs text-warning">
          Показаны первые {SERVER_LIMIT} из {formatNumber(links.data?.total)} ссылок — уточните фильтр по продукту или
          уровню.
        </p>
      ) : null}

      {links.isError ? (
        <SectionError error={links.error} onRetry={() => void links.refetch()} />
      ) : links.isPending ? (
        <SkeletonTable rows={6} />
      ) : filtered.length === 0 ? (
        <EmptyState
          compact
          title={all.length ? "Ничего не найдено" : "Ссылок пока нет"}
          description={
            all.length
              ? "Смените фильтр или строку поиска."
              : "Ссылки создаются на пакете ролика, на канале («Подписаться») и в кампаниях."
          }
        />
      ) : (
        <Table minWidth={900}>
          <caption className="sr-only">Трекинг-ссылки</caption>
          <TableHead>
            <tr>
              <Th className="w-6" />
              <Th>Код</Th>
              <Th>Уровень</Th>
              <Th>Продукт</Th>
              <Th>Привязка</Th>
              <Th className="text-right">Клики 7д</Th>
              <Th>Активна</Th>
              <Th>Создана</Th>
            </tr>
          </TableHead>
          <TableBody>
            {pageRows.map((link) => {
              const expanded = expandedId === link.id;
              return [
                <TableRow
                  key={link.id}
                  clickable
                  aria-expanded={expanded}
                  onClick={() => setExpandedId(expanded ? null : link.id)}
                  className={cn(!link.is_active && "text-fg-muted")}
                >
                  <Td className="pr-0 text-fg-muted">
                    {expanded ? (
                      <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                    )}
                  </Td>
                  <Td>
                    <span className="flex flex-col">
                      <span className="font-mono text-xs">{link.code}</span>
                      {link.label ? <span className="truncate text-2xs text-fg-muted">{link.label}</span> : null}
                    </span>
                  </Td>
                  <Td>
                    <Badge variant={link.level === "manual" ? "human" : "neutral"} outline>
                      {TRACKING_LEVEL_LABELS[link.level]}
                    </Badge>
                  </Td>
                  <Td>
                    {productName.get(link.product_id) ?? <span className="font-mono text-xs">{link.product_id}</span>}
                  </Td>
                  <Td className="font-mono text-xs text-fg-muted">{linkBinding(link)}</Td>
                  <Td className="text-right font-mono">{formatNumber(link.clicks_7d ?? 0)}</Td>
                  <Td onClick={(event) => event.stopPropagation()}>
                    <ActiveToggle link={link} disabled={!operator} />
                  </Td>
                  <Td className="text-fg-muted">{formatDateTime(link.created_at, { short: true })}</Td>
                </TableRow>,
                expanded ? (
                  <tr key={`${link.id}-details`}>
                    <td colSpan={8} className="bg-surface-2/40 p-3">
                      <LinkDetails link={link} />
                    </td>
                  </tr>
                ) : null,
              ];
            })}
          </TableBody>
        </Table>
      )}
      {filtered.length > LINKS_PAGE_SIZE ? (
        <Pagination total={filtered.length} limit={LINKS_PAGE_SIZE} offset={offset} onOffsetChange={setOffset} />
      ) : null}
    </SectionCard>
  );
}

function linkBinding(link: TrackingLink): string {
  const parts: string[] = [];
  if (link.channel_id) parts.push(`канал ${link.channel_id}`);
  if (link.campaign_id !== null) parts.push(`кампания #${link.campaign_id}`);
  if (link.publication_id !== null) parts.push(`публикация #${link.publication_id}`);
  else if (link.job_id !== null) parts.push(`ролик #${link.job_id}`);
  if (link.banner_variant_id !== null) parts.push(`вариант #${link.banner_variant_id}`);
  if (link.platform) parts.push(PLATFORM_LABELS[link.platform]);
  return parts.length ? parts.join(" · ") : "—";
}

function ActiveToggle({ link, disabled }: { link: TrackingLink; disabled: boolean }) {
  const update = useUpdateTrackingLink(link.id);
  const toggle = async (next: boolean) => {
    try {
      await update.mutateAsync({ is_active: next });
      toast.success(next ? `Ссылка ${link.code} включена` : `Ссылка ${link.code} выключена`, {
        description: next ? undefined : "Переходы по коду пойдут на продукт по умолчанию.",
      });
    } catch (error) {
      toast.error("Не сохранено", { description: errorMessage(error) });
    }
  };
  return (
    <label className="inline-flex items-center gap-1.5 text-xs">
      <input
        type="checkbox"
        className="h-4 w-4 accent-[var(--accent)]"
        checked={link.is_active}
        disabled={disabled || update.isPending}
        aria-label={`Ссылка ${link.code} активна`}
        onChange={(event) => void toggle(event.target.checked)}
      />
      {link.is_active ? "да" : "нет"}
    </label>
  );
}

function copyText(value: string) {
  navigator.clipboard
    .writeText(value)
    .then(() => toast.success("Скопировано"))
    .catch(() => toast.error("Не удалось скопировать"));
}

function LinkUrl({ label, url }: { label: string; url: string | null }) {
  if (!url) return null;
  return (
    <div className="flex items-center gap-1.5 text-xs">
      <span className="w-28 shrink-0 text-fg-muted">{label}</span>
      <span className="truncate font-mono">{url}</span>
      <Button variant="ghost" size="icon-sm" aria-label={`Скопировать: ${label}`} onClick={() => copyText(url)}>
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
      </Button>
    </div>
  );
}

/** Раскрытая строка: адреса ссылки и статистика за 30 дней (`/tracking-links/{id}/stats`). */
function LinkDetails({ link }: { link: TrackingLink }) {
  const stats = useTrackingLinkStats(link.id);
  const data = stats.data;
  const series = data?.series ?? [];
  const maxClicks = Math.max(1, ...series.map((point) => point.clicks));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <LinkUrl label="Короткая ссылка" url={link.redirect_url} />
        <LinkUrl label="Telegram" url={link.telegram_https_url} />
        <LinkUrl label="Mini App" url={link.tma_url} />
        <LinkUrl label="Своя цель" url={link.target_override_url} />
      </div>
      {stats.isError ? (
        <SectionError error={stats.error} onRetry={() => void stats.refetch()} title="Статистика недоступна" />
      ) : !data ? (
        <Skeleton className="h-16" />
      ) : (
        <>
          <DetailGrid className="sm:grid-cols-5">
            <KeyValue label="Клики" value={<span className="font-mono">{formatNumber(data.totals.clicks)}</span>} />
            <KeyValue
              label="Уникальные"
              value={<span className="font-mono">{formatNumber(data.totals.unique_visitors)}</span>}
            />
            <KeyValue
              label="В Telegram / на сайт"
              value={
                <span className="font-mono">
                  {formatNumber(data.totals.to_telegram)} / {formatNumber(data.totals.to_site)}
                </span>
              }
            />
            <KeyValue
              label="Старты · регистрации · оплаты"
              value={
                <span className="font-mono">
                  {formatNumber(data.totals.bot_starts)} · {formatNumber(data.totals.signups)} ·{" "}
                  {formatNumber(data.totals.first_payments)}
                </span>
              }
            />
            <KeyValue
              label="Выручка"
              value={<span className="font-mono">{formatMinor(data.totals.revenue_minor, data.totals.currency)}</span>}
            />
          </DetailGrid>
          <p className="text-2xs text-fg-muted">
            Без ботов ({formatNumber(data.totals.bot_clicks)} отсечено по UA и HEAD) и тестовых событий; окно —
            последние 30 дней по дням.
          </p>
          {series.length === 0 ? (
            <p className="text-sm text-fg-muted">Кликов за период не было.</p>
          ) : (
            <div className="grid grid-cols-[auto_minmax(0,1fr)_auto_auto_auto] items-center gap-x-3 gap-y-1 text-xs">
              <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">День</span>
              <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Клики</span>
              <span className="text-right font-mono text-2xs tracking-wider text-fg-muted uppercase">Уник.</span>
              <span className="text-right font-mono text-2xs tracking-wider text-fg-muted uppercase">Старты</span>
              <span className="text-right font-mono text-2xs tracking-wider text-fg-muted uppercase">Оплаты</span>
              {series.map((point) => (
                <div key={point.t} className="contents">
                  <span className="font-mono text-fg-muted">{formatDay(point.t)}</span>
                  <div className="flex items-center gap-2">
                    <div className="relative h-2.5 flex-1 overflow-hidden rounded-sm bg-surface" aria-hidden="true">
                      <span
                        className="absolute inset-y-0 left-0 rounded-sm"
                        style={{
                          width: `${(point.clicks / maxClicks) * 100}%`,
                          backgroundColor: "var(--color-chart-1)",
                        }}
                      />
                    </div>
                    <span className="w-8 text-right font-mono">{formatNumber(point.clicks)}</span>
                  </div>
                  <span className="text-right font-mono text-fg-muted">{formatNumber(point.unique_visitors)}</span>
                  <span className="text-right font-mono text-fg-muted">{formatNumber(point.bot_starts)}</span>
                  <span className="text-right font-mono text-fg-muted">{formatNumber(point.first_payments)}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- журналы

type JournalTab = "clicks" | "conversions";
const JOURNAL_TABS: Array<{ value: JournalTab; label: string }> = [
  { value: "clicks", label: "Клики" },
  { value: "conversions", label: "Конверсии" },
];

function JournalsCard({ productId }: { productId: string }) {
  const [tab, setTab] = useState<JournalTab>("clicks");
  const [includeBots, setIncludeBots] = useState(false);
  const [includeTest, setIncludeTest] = useState(false);

  return (
    <SectionCard
      title="Журналы редиректора"
      actions={<MousePointerClick className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <PillToggle label="Журнал" options={JOURNAL_TABS} value={tab} onChange={setTab} />
        <label className="inline-flex items-center gap-1.5 text-xs text-fg-muted">
          <input
            type="checkbox"
            className="h-4 w-4 accent-[var(--accent)]"
            checked={tab === "clicks" ? includeBots : includeTest}
            onChange={(event) =>
              tab === "clicks" ? setIncludeBots(event.target.checked) : setIncludeTest(event.target.checked)
            }
          />
          {tab === "clicks" ? "показывать ботов" : "показывать тестовые"}
        </label>
      </div>
      <p className="text-2xs text-fg-muted">
        Последние записи{productId ? ` по продукту «${productId}»` : " по всем продуктам"}; дальше — «Показать ещё».
      </p>
      {tab === "clicks" ? (
        <ClicksJournal productId={productId} includeBots={includeBots} />
      ) : (
        <ConversionsJournal productId={productId} includeTest={includeTest} />
      )}
    </SectionCard>
  );
}

function LoadMore({
  hasNextPage,
  isFetchingNextPage,
  onLoadMore,
  shown,
}: {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  shown: number;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-2xs text-fg-muted">Показано {formatNumber(shown)}</span>
      {hasNextPage ? (
        <Button variant="outline" size="sm" disabled={isFetchingNextPage} onClick={onLoadMore}>
          {isFetchingNextPage ? "Загрузка…" : "Показать ещё"}
        </Button>
      ) : null}
    </div>
  );
}

function ClicksJournal({ productId, includeBots }: { productId: string; includeBots: boolean }) {
  const journal = useClicksJournal({ productId: productId || null, includeBots });
  const rows = journalItems<Click>(journal.data);
  const columns: Array<DataTableColumn<Click>> = [
    {
      key: "occurred_at",
      header: "Время",
      render: (row) => <span className="text-fg-muted">{formatDateTime(row.occurred_at, { short: true })}</span>,
    },
    { key: "code", header: "Код", render: (row) => <span className="font-mono text-xs">{row.code}</span> },
    {
      key: "route",
      header: "Маршрут",
      render: (row) => (
        <Badge variant={row.route === "unknown_code" ? "warning" : "neutral"} outline>
          {CLICK_ROUTE_LABELS[row.route]}
        </Badge>
      ),
    },
    {
      key: "product",
      header: "Продукт",
      render: (row) => <span className="font-mono text-xs">{row.product_id ?? "—"}</span>,
    },
    {
      key: "source",
      header: "Ролик / канал",
      render: (row) => (
        <span className="font-mono text-xs text-fg-muted">
          {row.publication_id !== null ? `#${row.publication_id}` : "—"}
          {row.channel_id ? ` · ${row.channel_id}` : ""}
          {row.platform ? ` · ${PLATFORM_LABELS[row.platform]}` : ""}
        </span>
      ),
    },
    {
      key: "referer",
      header: "Откуда",
      render: (row) => <span className="font-mono text-xs text-fg-muted">{row.referer_host ?? "—"}</span>,
    },
    {
      key: "bot",
      header: "",
      render: (row) => (row.is_bot ? <Badge variant="machine">бот</Badge> : null),
    },
  ];
  return (
    <>
      <DataTable
        caption="Журнал кликов"
        columns={columns}
        rows={rows}
        rowKey={(row) => String(row.id)}
        isLoading={journal.isPending}
        isError={journal.isError}
        errorContent={<SectionError error={journal.error} onRetry={() => void journal.refetch()} />}
        emptyTitle="Кликов пока нет"
        emptyDescription="Появятся после первых переходов по коротким ссылкам."
        minWidth={820}
      />
      {rows.length ? (
        <LoadMore
          shown={rows.length}
          hasNextPage={journal.hasNextPage}
          isFetchingNextPage={journal.isFetchingNextPage}
          onLoadMore={() => void journal.fetchNextPage()}
        />
      ) : null}
    </>
  );
}

function ConversionsJournal({ productId, includeTest }: { productId: string; includeTest: boolean }) {
  const journal = useConversionsJournal({ productId: productId || null, includeTest });
  const rows = journalItems<Conversion>(journal.data);
  const columns: Array<DataTableColumn<Conversion>> = [
    {
      key: "occurred_at",
      header: "Время",
      render: (row) => <span className="text-fg-muted">{formatDateTime(row.occurred_at, { short: true })}</span>,
    },
    {
      key: "event",
      header: "Событие",
      render: (row) => (
        <span className="flex items-center gap-1.5">
          {CONVERSION_EVENT_LABELS[row.event_type]}
          {row.is_test ? <Badge variant="warning">тест</Badge> : null}
        </span>
      ),
    },
    { key: "product", header: "Продукт", render: (row) => <span className="font-mono text-xs">{row.product_id}</span> },
    {
      key: "attribution",
      header: "Атрибуция",
      render: (row) => (
        <Badge variant={row.attribution_level === "exact" ? "positive" : "neutral"} outline>
          {ATTRIBUTION_LEVEL_LABELS[row.attribution_level]}
          {row.via_landing ? " · лендинг" : ""}
        </Badge>
      ),
    },
    {
      key: "source",
      header: "Ссылка / ролик",
      render: (row) => (
        <span className="font-mono text-xs text-fg-muted">
          {row.tracking_link_id !== null ? `ссылка #${row.tracking_link_id}` : "—"}
          {row.publication_id !== null ? ` · #${row.publication_id}` : ""}
          {row.channel_id ? ` · ${row.channel_id}` : ""}
        </span>
      ),
    },
    {
      key: "amount",
      header: "Сумма",
      className: "text-right",
      render: (row) => <span className="font-mono">{formatMinor(row.amount_minor, row.currency)}</span>,
    },
  ];
  return (
    <>
      <DataTable
        caption="Журнал конверсий"
        columns={columns}
        rows={rows}
        rowKey={(row) => String(row.id)}
        isLoading={journal.isPending}
        isError={journal.isError}
        errorContent={<SectionError error={journal.error} onRetry={() => void journal.refetch()} />}
        emptyTitle="Конверсий пока нет"
        emptyDescription="Продукт присылает события на вебхук; тестовое — на карточке продукта."
        minWidth={820}
      />
      {rows.length ? (
        <LoadMore
          shown={rows.length}
          hasNextPage={journal.hasNextPage}
          isFetchingNextPage={journal.isFetchingNextPage}
          onLoadMore={() => void journal.fetchNextPage()}
        />
      ) : null}
    </>
  );
}
