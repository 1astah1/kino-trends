"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Keyboard, PackageOpen } from "lucide-react";
import { parseAsInteger, parseAsStringLiteral, useQueryState } from "nuqs";
import { useEffect, useMemo, useRef } from "react";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import { Badge, EmptyState, Select, Skeleton, tabPanelId, Tabs } from "@/components/ui";
import { canOperate, isAdmin } from "@/lib/auth";
import { useChannels } from "@/lib/queries/channels";
import { queryKeys } from "@/lib/queries/keys";
import { type PackageStatus, packageKeys, usePackages, usePackageTotals } from "@/lib/queries/packages";
import { PROBLEM_VERIFY_STATUSES, usePublications } from "@/lib/queries/publications";
import { type TrackingLink, useTrackingLinks } from "@/lib/queries/tracking";

import { ChannelLinksPanel } from "./channel-links";
import { PackageDetail } from "./package-detail";
import { ChannelGroup } from "./package-list";
import { groupByChannel } from "./package-utils";
import { ProblemsPanel, useBlockedPackages } from "./problems-panel";

const TABS = ["live", "published", "problems", "links"] as const;
type TabId = (typeof TABS)[number];
const TAB_LABELS: Record<TabId, string> = {
  live: "К заливке",
  published: "Опубликованы",
  problems: "Проблемы",
  links: "Ссылки каналов",
};
const TAB_STATUSES: Partial<Record<TabId, PackageStatus[]>> = {
  live: ["ready", "downloaded"],
  published: ["published"],
};
const ID_PREFIX = "packages";

/**
 * Пакеты к заливке (§7.3 `/packages?status=&channel=&package=`). Публикация ручная: оператор
 * скачивает файл (или «Поделиться» на телефоне), копирует тексты в один клик (клавиши 1–6),
 * отмечает серверный чек-лист, заливает сам и вставляет ссылку — сервер регистрирует публикацию.
 * На телефоне работает полноценно: список и карточка сменяют друг друга.
 */
export function PackagesScreen() {
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const admin = isAdmin(user.role);
  const queryClient = useQueryClient();
  const [tab, setTab] = useQueryState(
    "status",
    parseAsStringLiteral(TABS).withDefault("live").withOptions({ history: "replace" }),
  );
  const [channelId, setChannelId] = useQueryState("channel", { history: "replace" });
  const [packageId, setPackageId] = useQueryState("package", parseAsInteger.withOptions({ history: "push" }));

  const statuses = TAB_STATUSES[tab];
  const packages = usePackages({ statuses: statuses ?? ["ready", "downloaded"], channelId });
  const totals = usePackageTotals(channelId);
  const problems = usePublications({ verifyStatuses: PROBLEM_VERIFY_STATUSES, channelId, limit: 1 });
  const channels = useChannels();
  const channelLinks = useTrackingLinks({ levels: ["channel"], isActive: true });

  // SSE `packages` обновляет счётчики сайдбара; при их изменении перечитываем и список.
  const counts = useQuery<{ ready: number; downloaded: number } | null>({
    queryKey: queryKeys.packagesCounts,
    queryFn: () => Promise.resolve(null),
    enabled: false,
    staleTime: Infinity,
  });
  const lastCounts = useRef<string | null>(null);
  useEffect(() => {
    const snapshot = counts.data ? `${counts.data.ready}:${counts.data.downloaded}` : null;
    if (snapshot && lastCounts.current && snapshot !== lastCounts.current) {
      void queryClient.invalidateQueries({ queryKey: packageKeys.all });
    }
    lastCounts.current = snapshot;
  }, [counts.data, queryClient]);

  const items = useMemo(() => (statuses ? (packages.data?.items ?? []) : []), [packages.data, statuses]);
  const groups = useMemo(() => groupByChannel(items), [items]);
  const linkItems = channelLinks.data?.items;
  const linkByChannel = useMemo(() => {
    const map = new Map<string, TrackingLink>();
    for (const link of linkItems ?? []) {
      if (link.channel_id && !map.has(link.channel_id)) map.set(link.channel_id, link);
    }
    return map;
  }, [linkItems]);

  const selectedInitial = items.find((item) => item.id === packageId);
  const liveCount = totals.data ? totals.data.ready + totals.data.downloaded : null;
  const blockedPackages = useBlockedPackages();
  const problemCount = (problems.data?.total ?? 0) + blockedPackages.length;
  const showDetail = packageId !== null && (tab === "live" || tab === "published");

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="packages-screen">
      <div className={showDetail ? "hidden md:block" : undefined}>
        <PageHeader
          eyebrow="Производство"
          title="Пакеты к заливке"
          description="Скачайте файл, скопируйте тексты, отметьте чек-лист, залейте ролик сами и вставьте ссылку — публикация зарегистрируется, код получит алиас площадки."
          actions={
            <Select
              aria-label="Канал"
              value={channelId ?? ""}
              onChange={(event) => void setChannelId(event.target.value || null)}
              className="w-56"
              data-testid="packages-channel-filter"
            >
              <option value="">Все каналы</option>
              {(channels.data?.items ?? []).map((channel) => (
                <option key={channel.id} value={channel.id}>
                  {channel.title || channel.id} · {channel.platform === "youtube" ? "YouTube" : "VK"}
                </option>
              ))}
            </Select>
          }
        />
      </div>

      <div className={showDetail ? "hidden md:block" : undefined}>
        <Tabs
          idPrefix={ID_PREFIX}
          label="Разделы пакетов"
          value={tab}
          onChange={(value) => {
            void setTab(value as TabId);
            void setPackageId(null);
          }}
          items={TABS.map((value) => ({
            value,
            label: TAB_LABELS[value],
            badge:
              value === "live" && liveCount ? (
                <Badge variant="human" className="font-mono">
                  {liveCount}
                </Badge>
              ) : value === "problems" && problemCount ? (
                <Badge variant="negative" className="font-mono">
                  {problemCount}
                </Badge>
              ) : undefined,
          }))}
        />
      </div>

      <section id={tabPanelId(ID_PREFIX, tab)} role="tabpanel" aria-labelledby={`${ID_PREFIX}-tab-${tab}`}>
        {tab === "problems" ? (
          <ProblemsPanel channelId={channelId} operator={operator} admin={admin} />
        ) : tab === "links" ? (
          <ChannelLinksPanel channelId={channelId} operator={operator} />
        ) : (
          <div className="grid gap-4 md:grid-cols-[300px_minmax(0,1fr)] lg:grid-cols-[340px_minmax(0,1fr)]">
            <aside className={showDetail ? "hidden md:flex md:flex-col md:gap-4" : "flex flex-col gap-4"}>
              {packages.isError ? (
                <SectionError
                  error={packages.error}
                  onRetry={() => void packages.refetch()}
                  title="Пакеты не загрузились"
                />
              ) : packages.isPending ? (
                <Skeleton className="h-64" />
              ) : groups.length === 0 ? (
                <EmptyState
                  icon={PackageOpen}
                  title={tab === "live" ? "Заливать нечего" : "Опубликованных пакетов нет"}
                  description={
                    tab === "live"
                      ? "Пакет собирается сам после одобрения ролика в Приёмке."
                      : "Здесь появятся пакеты, по которым вставлена ссылка."
                  }
                  compact
                />
              ) : (
                groups.map((group) => (
                  <ChannelGroup
                    key={group.channelId}
                    group={group}
                    channelLink={linkByChannel.get(group.channelId) ?? null}
                    selectedId={packageId}
                    onSelect={(id) => void setPackageId(id)}
                  />
                ))
              )}
              {packages.data && packages.data.total > packages.data.items.length ? (
                <p className="px-1 text-xs text-fg-muted">
                  Показаны {packages.data.items.length} из {packages.data.total}. Сузьте список фильтром канала.
                </p>
              ) : null}
            </aside>
            <div className={showDetail ? "min-w-0" : "hidden min-w-0 md:block"}>
              {packageId !== null ? (
                <PackageDetail
                  key={packageId}
                  packageId={packageId}
                  initial={selectedInitial}
                  operator={operator}
                  admin={admin}
                  onBack={() => void setPackageId(null)}
                  onOpenPackage={(id) => void setPackageId(id)}
                />
              ) : (
                <EmptyState
                  icon={Keyboard}
                  title="Выберите пакет слева"
                  description="В карточке: скачать файл, копировать поля клавишами 1–6 (заголовок, описание с хэштегами, хэштеги, закреп, ссылка, код), чек-лист и поле для ссылки на опубликованный ролик."
                />
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
