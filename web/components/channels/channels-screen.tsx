"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Copy, Plus, Tv } from "lucide-react";
import { parseAsString, parseAsStringLiteral, useQueryState, useQueryStates } from "nuqs";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  Card,
  DataTable,
  type DataTableColumn,
  Dialog,
  PillToggle,
  Select,
  StatusBadge,
} from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { canOperate, isAdmin } from "@/lib/auth";
import { formatNumber } from "@/lib/format";
import { type Channel, channelKeys, useChannels, useClusters, useNicheOptions } from "@/lib/queries/channels";

import { ChannelDrawer } from "./channel-drawer";
import { ChannelForm } from "./channel-form";
import { CHANNEL_STATUS_META, PLATFORM_SHORT } from "./labels";

const VIEWS = ["list", "clusters"] as const;
const PLATFORM_FILTERS = ["all", "youtube", "vk"] as const;
const STATUS_FILTERS = ["all", ...(Object.keys(CHANNEL_STATUS_META) as Schemas["ChannelStatus"][])] as const;
/** Возврат с колбэка Google OAuth (`/oauth/youtube/callback` → 302 `/channels/?oauth=ok|error|denied&…`). */
const OAUTH_RETURN = { oauth: parseAsString, reason: parseAsString, message: parseAsString };
const OAUTH_DENIED_REASONS: Record<string, string> = {
  access_denied: "В окне Google нажали «Отмена» или не выдали доступ.",
  no_code: "Google вернул без кода — начните подключение заново.",
};

/** Каналы (§7.3 `/channels?channel=&view=`): таблица, группировка по кустам, карточка в drawer. */
export function ChannelsScreen() {
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const admin = isAdmin(user.role);
  const queryClient = useQueryClient();
  const [channelId, setChannelId] = useQueryState("channel");
  const [oauthReturn, setOauthReturn] = useQueryStates(OAUTH_RETURN, { history: "replace" });
  const [view, setView] = useQueryState(
    "view",
    parseAsStringLiteral(VIEWS).withDefault("list").withOptions({ history: "replace" }),
  );
  const [platform, setPlatform] = useQueryState(
    "platform",
    parseAsStringLiteral(PLATFORM_FILTERS).withDefault("all").withOptions({ history: "replace" }),
  );
  const [status, setStatus] = useQueryState(
    "status",
    parseAsStringLiteral(STATUS_FILTERS).withDefault("all").withOptions({ history: "replace" }),
  );
  const [niche, setNiche] = useQueryState("niche", { history: "replace" });
  const [creating, setCreating] = useState(false);

  // ?oauth=ok&channel=X — карточка X открывается сама (`channel` уже в URL); тост и чистка oauth/reason/message.
  // id тоста — от двойного эффекта в StrictMode.
  const { oauth, reason: oauthReason, message: oauthMessage } = oauthReturn;
  useEffect(() => {
    if (!oauth) return;
    if (oauth === "ok") {
      toast.success("YouTube подключён", {
        id: "youtube-oauth-return",
        description: channelId ? `Канал ${channelId}` : undefined,
      });
      if (channelId) void queryClient.invalidateQueries({ queryKey: channelKeys.youtube(channelId) });
    } else {
      toast.error(oauth === "denied" ? "Google не выдал доступ" : "YouTube не подключён", {
        id: "youtube-oauth-return",
        description: oauthMessage || (oauthReason ? (OAUTH_DENIED_REASONS[oauthReason] ?? oauthReason) : undefined),
      });
    }
    void setOauthReturn(null);
  }, [oauth, oauthReason, oauthMessage, channelId, queryClient, setOauthReturn]);

  const channels = useChannels({
    platform: platform === "all" ? null : platform,
    status: status === "all" ? null : [status],
    nicheId: niche,
  });
  const niches = useNicheOptions();
  const clusters = useClusters();
  const items = channels.data?.items ?? [];
  const nicheLabel = new Map((niches.data ?? []).map((n) => [n.id, n.label]));

  const columns: Array<DataTableColumn<Channel>> = [
    {
      key: "title",
      header: "Канал",
      render: (c) => (
        <span className="flex flex-col">
          <span className="flex items-center gap-1.5 font-medium">
            {c.title || c.id}
            {c.profile_duplicate_of.length ? (
              <Badge variant="warning" title={`Профиль совпадает: ${c.profile_duplicate_of.join(", ")}`}>
                <Copy className="h-3 w-3" aria-hidden="true" />
                профиль-дубль
              </Badge>
            ) : null}
          </span>
          <span className="font-mono text-2xs text-fg-muted">{c.id}</span>
        </span>
      ),
    },
    {
      key: "platform",
      header: "Площадка",
      render: (c) => (
        <Badge variant={c.platform === "youtube" ? "accent" : "machine"} outline>
          {PLATFORM_SHORT[c.platform]}
        </Badge>
      ),
    },
    {
      key: "niche",
      header: "Ниша",
      render: (c) => <span className="text-fg-muted">{nicheLabel.get(c.niche_id) ?? c.niche_id}</span>,
    },
    { key: "cluster", header: "Куст", render: (c) => <span className="font-mono text-xs">{c.cluster}</span> },
    { key: "status", header: "Статус", render: (c) => <StatusBadge status={c.status} map={CHANNEL_STATUS_META} /> },
    {
      key: "advanced",
      header: "Advanced",
      render: (c) =>
        c.advanced_features ? <Badge variant="positive">вкл</Badge> : <span className="text-fg-muted">—</span>,
    },
    {
      key: "subs",
      header: "Подписчики",
      className: "text-right",
      render: (c) => <span className="tabular font-mono text-xs">{formatNumber(c.subscribers)}</span>,
    },
    {
      key: "plan",
      header: "План/день",
      className: "text-right",
      render: (c) => <span className="tabular font-mono text-xs">{c.publishing_plan.videos_per_day ?? "—"}</span>,
    },
    {
      key: "product",
      header: "Продукт",
      render: (c) => <span className="font-mono text-xs text-fg-muted">{c.default_product_id ?? "—"}</span>,
    },
  ];

  const grouped = new Map<string, Channel[]>();
  for (const channel of items) {
    const list = grouped.get(channel.cluster) ?? [];
    list.push(channel);
    grouped.set(channel.cluster, list);
  }

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="channels-screen">
      <PageHeader
        eyebrow="Настройка"
        title="Каналы"
        description="YouTube Shorts — основная площадка, VK Клипы — второй контур. У каждого канала свой профиль: голос, шаблон баннера, стиль — иначе площадки видят сетку."
        actions={
          operator ? (
            <Button onClick={() => setCreating(true)} data-testid="create-channel">
              <Plus className="h-4 w-4" aria-hidden="true" />
              Канал
            </Button>
          ) : null
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <PillToggle
          label="Вид"
          options={[
            { value: "list", label: "Список" },
            { value: "clusters", label: "По кустам" },
          ]}
          value={view}
          onChange={(value) => void setView(value)}
        />
        <PillToggle
          label="Площадка"
          options={[
            { value: "all", label: "Все" },
            { value: "youtube", label: "YouTube" },
            { value: "vk", label: "VK" },
          ]}
          value={platform}
          onChange={(value) => void setPlatform(value)}
        />
        <Select
          aria-label="Статус канала"
          className="h-7 w-44 text-xs"
          value={status}
          onChange={(event) => void setStatus(event.target.value as (typeof STATUS_FILTERS)[number])}
        >
          <option value="all">Все статусы</option>
          {(Object.keys(CHANNEL_STATUS_META) as Schemas["ChannelStatus"][]).map((value) => (
            <option key={value} value={value}>
              {CHANNEL_STATUS_META[value].label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Ниша"
          className="h-7 w-48 text-xs"
          value={niche ?? ""}
          onChange={(event) => void setNiche(event.target.value || null)}
        >
          <option value="">Все ниши</option>
          {(niches.data ?? []).map((n) => (
            <option key={n.id} value={n.id}>
              {n.label}
            </option>
          ))}
        </Select>
        <span className="ml-auto text-xs text-fg-muted">
          {channels.data ? `${formatNumber(channels.data.total)} каналов` : null}
        </span>
      </div>

      {channels.isError ? (
        <SectionError error={channels.error} onRetry={() => void channels.refetch()} />
      ) : view === "list" ? (
        <DataTable
          caption="Каналы"
          columns={columns}
          rows={items}
          rowKey={(c) => c.id}
          onRowClick={(c) => void setChannelId(c.id)}
          isLoading={channels.isPending}
          emptyTitle="Каналов нет"
          emptyDescription="Добавьте канал: площадка, ниша, куст и свой профиль."
          minWidth={960}
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from(grouped.entries()).map(([cluster, list]) => {
            const info = clusters.data?.find((c) => c.id === cluster);
            return (
              <Card key={cluster} className="flex flex-col gap-2 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold">{info?.title ?? cluster}</span>
                  <span className="font-mono text-2xs text-fg-muted">{cluster}</span>
                </div>
                <ul className="flex flex-col gap-0.5">
                  {list.map((c) => (
                    <li key={c.id}>
                      <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-sm hover:bg-surface-2"
                        onClick={() => void setChannelId(c.id)}
                      >
                        <Tv className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />
                        <span className="min-w-0 flex-1 truncate">{c.title || c.id}</span>
                        <span className="text-2xs text-fg-muted">{PLATFORM_SHORT[c.platform]}</span>
                        <StatusBadge status={c.status} map={CHANNEL_STATUS_META} />
                      </button>
                    </li>
                  ))}
                </ul>
                {info && !info.youtube_api_key_secret ? (
                  <p className="text-2xs text-warning">Не задан ключ YouTube Data API куста</p>
                ) : null}
              </Card>
            );
          })}
          {items.length === 0 && !channels.isPending ? <p className="text-sm text-fg-muted">Каналов нет.</p> : null}
        </div>
      )}

      <ChannelDrawer
        channelId={channelId}
        operator={operator}
        admin={admin}
        onClose={() => void setChannelId(null)}
        onOpenChannel={(id) => void setChannelId(id)}
      />
      <Dialog
        open={creating}
        onClose={() => setCreating(false)}
        title="Новый канал"
        description="id — латиницей, навсегда. Профиль (голос, шаблон, стиль) лучше сразу сделать отличным от других каналов ниши."
        className="max-w-2xl"
      >
        {creating ? (
          <ChannelForm
            channel={null}
            onCancel={() => setCreating(false)}
            onDone={(channel) => {
              setCreating(false);
              void setChannelId(channel.id);
            }}
          />
        ) : null}
      </Dialog>
    </div>
  );
}
