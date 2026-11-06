"use client";

import { Link2, Pencil, Plus, Power, PowerOff } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { Badge, Button, EmptyState, FormField, Input, SectionCard, Select, Skeleton } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import { type Channel, useChannels } from "@/lib/queries/channels";
import { useProducts } from "@/lib/queries/products";
import {
  type TrackingLink,
  useCreateChannelLink,
  useTrackingLinks,
  useUpdateTrackingLink,
} from "@/lib/queries/tracking";

import { CopyRow, useCopy } from "./copy";
import { publishErrorMessage } from "./publish-errors";

/**
 * Ссылки каналов (§6.6 «код канала»): для кнопки «Подписаться», шапки канала и описания профиля.
 * Уровень атрибуции `channel` — переход считается каналу и продукту, но не конкретному ролику.
 * Повторное создание отдаёт существующую активную ссылку.
 */
export function ChannelLinksPanel({ operator, channelId }: { operator: boolean; channelId: string | null }) {
  const channels = useChannels();
  const links = useTrackingLinks({ levels: ["channel"] });
  const items = (channels.data?.items ?? []).filter(
    (channel) =>
      (!channelId || channel.id === channelId) && channel.status !== "retired" && channel.status !== "terminated",
  );

  if (channels.isError) return <SectionError error={channels.error} onRetry={() => void channels.refetch()} />;
  if (links.isError) return <SectionError error={links.error} onRetry={() => void links.refetch()} />;
  if (channels.isPending || links.isPending) return <Skeleton className="h-48" />;
  if (!items.length) {
    return <EmptyState icon={Link2} title="Каналов нет" description="Добавьте канал в «Каналах»." compact />;
  }
  const byChannel = new Map<string, TrackingLink[]>();
  for (const link of links.data?.items ?? []) {
    if (!link.channel_id) continue;
    byChannel.set(link.channel_id, [...(byChannel.get(link.channel_id) ?? []), link]);
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="channel-links-panel">
      {items.map((channel) => (
        <ChannelLinksCard
          key={channel.id}
          channel={channel}
          links={byChannel.get(channel.id) ?? []}
          operator={operator}
        />
      ))}
    </div>
  );
}

/**
 * Ссылки одного канала — можно встроить в карточку канала (drawer «Каналов»):
 * `<ChannelTrackingLinks channelId={c.id} operator={operator} />`.
 */
export function ChannelTrackingLinks({ channelId, operator }: { channelId: string; operator: boolean }) {
  const channels = useChannels();
  const links = useTrackingLinks({ levels: ["channel"], channelId });
  const channel = channels.data?.items.find((item) => item.id === channelId);
  if (links.isError) return <SectionError error={links.error} onRetry={() => void links.refetch()} />;
  if (!channel || links.isPending) return <Skeleton className="h-24" />;
  return <ChannelLinksCard channel={channel} links={links.data?.items ?? []} operator={operator} />;
}

function ChannelLinksCard({
  channel,
  links,
  operator,
}: {
  channel: Channel;
  links: TrackingLink[];
  operator: boolean;
}) {
  const [creating, setCreating] = useState(false);
  const active = links.filter((link) => link.is_active);
  return (
    <SectionCard
      title={channel.title || channel.id}
      actions={
        <span className="flex items-center gap-1.5">
          <Badge variant={channel.platform === "youtube" ? "accent" : "machine"} outline>
            {channel.platform === "youtube" ? "YouTube" : "VK"}
          </Badge>
          {operator && !creating ? (
            <Button variant="ghost" size="xs" onClick={() => setCreating(true)} data-testid="create-channel-link">
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              Ссылка
            </Button>
          ) : null}
        </span>
      }
    >
      <div className="flex flex-col gap-3" data-testid="channel-links" data-channel-id={channel.id}>
        {links.length === 0 && !creating ? (
          <p className="text-sm text-fg-muted">
            Ссылки канала ещё нет. Она нужна для кнопки «Подписаться», шапки и описания профиля: переходы по ней
            считаются каналу, а не ролику.
          </p>
        ) : null}
        {links.map((link) => (
          <LinkRow key={link.id} link={link} operator={operator} />
        ))}
        {creating ? (
          <CreateChannelLinkForm channel={channel} onDone={() => setCreating(false)} hasActive={active.length > 0} />
        ) : null}
      </div>
    </SectionCard>
  );
}

function LinkRow({ link, operator }: { link: TrackingLink; operator: boolean }) {
  const { copied, copy } = useCopy();
  const update = useUpdateTrackingLink(link.id);
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(link.label ?? "");

  function saveLabel(event: FormEvent) {
    event.preventDefault();
    update.mutate(
      { label: label.trim() || null },
      {
        onSuccess: () => {
          setEditing(false);
          toast.success("Подпись ссылки сохранена");
        },
        onError: (error) => toast.error(publishErrorMessage(error)),
      },
    );
  }

  return (
    <div
      className={link.is_active ? "flex flex-col gap-1.5" : "flex flex-col gap-1.5 opacity-60"}
      data-testid="channel-link"
      data-code={link.code}
    >
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="font-mono text-fg">{link.code}</span>
        <Badge variant="neutral" outline>
          {link.product_id}
        </Badge>
        {link.is_active ? null : <Badge variant="neutral">выключена</Badge>}
        {link.label && !editing ? <span className="text-fg-muted">{link.label}</span> : null}
        <span className="ml-auto tabular font-mono text-fg-muted" title="Переходы за 7 дней без ботов">
          {formatNumber(link.clicks_7d ?? 0)} за 7 дн
        </span>
      </div>
      <CopyRow
        label="Ссылка"
        value={link.redirect_url}
        copied={copied === "redirect"}
        onCopy={() => void copy("redirect", link.redirect_url, "Ссылка канала")}
      />
      {link.telegram_https_url ? (
        <CopyRow
          label="Telegram"
          value={link.telegram_https_url}
          copied={copied === "tg"}
          onCopy={() => void copy("tg", link.telegram_https_url ?? "", "Ссылка в Telegram")}
        />
      ) : null}
      {editing ? (
        <form onSubmit={saveLabel} className="flex items-end gap-2">
          <FormField label="Подпись (для себя)" htmlFor={`link-label-${link.id}`} className="flex-1">
            <Input
              id={`link-label-${link.id}`}
              value={label}
              maxLength={200}
              onChange={(event) => setLabel(event.target.value)}
            />
          </FormField>
          <Button type="submit" size="sm" disabled={update.isPending}>
            Сохранить
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
            Отмена
          </Button>
        </form>
      ) : operator ? (
        <div className="flex gap-2">
          <Button variant="ghost" size="xs" onClick={() => setEditing(true)}>
            <Pencil className="h-3 w-3" aria-hidden="true" />
            Подпись
          </Button>
          <Button
            variant="ghost"
            size="xs"
            disabled={update.isPending}
            onClick={() => {
              if (
                link.is_active &&
                !window.confirm(`Выключить ${link.code}? Переходы по ней перестанут считаться каналу.`)
              )
                return;
              update.mutate(
                { is_active: !link.is_active },
                { onError: (error) => toast.error(publishErrorMessage(error)) },
              );
            }}
          >
            {link.is_active ? (
              <PowerOff className="h-3 w-3" aria-hidden="true" />
            ) : (
              <Power className="h-3 w-3" aria-hidden="true" />
            )}
            {link.is_active ? "Выключить" : "Включить"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function CreateChannelLinkForm({
  channel,
  hasActive,
  onDone,
}: {
  channel: Channel;
  hasActive: boolean;
  onDone: () => void;
}) {
  const products = useProducts();
  const create = useCreateChannelLink(channel.id);
  const [productId, setProductId] = useState(channel.default_product_id ?? "");
  const [label, setLabel] = useState("Кнопка «Подписаться»");

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(
      { product_id: productId || null, label: label.trim() || null },
      {
        onSuccess: (link) => {
          toast.success(`Ссылка канала: ${link.code}`);
          onDone();
        },
      },
    );
  }

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-2 rounded-md border border-border p-2.5"
      data-testid="channel-link-form"
    >
      <FormField label="Продукт" htmlFor={`link-product-${channel.id}`} hint="Пусто — продукт канала по умолчанию">
        <Select
          id={`link-product-${channel.id}`}
          value={productId}
          onChange={(event) => setProductId(event.target.value)}
        >
          <option value="">по умолчанию{channel.default_product_id ? ` (${channel.default_product_id})` : ""}</option>
          {(products.data?.items ?? []).map((product) => (
            <option key={product.id} value={product.id}>
              {product.name} · {product.id}
            </option>
          ))}
        </Select>
      </FormField>
      <FormField label="Подпись" htmlFor={`link-new-label-${channel.id}`}>
        <Input
          id={`link-new-label-${channel.id}`}
          value={label}
          maxLength={200}
          onChange={(event) => setLabel(event.target.value)}
        />
      </FormField>
      {hasActive ? (
        <p className="text-xs text-fg-muted">
          У канала уже есть активная ссылка — для того же продукта сервер вернёт её, а не создаст новую.
        </p>
      ) : null}
      {create.error ? <p className="text-xs text-negative">{publishErrorMessage(create.error)}</p> : null}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={create.isPending}>
          <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
          {create.isPending ? "Создаю…" : "Получить ссылку"}
        </Button>
        <Button variant="ghost" size="sm" onClick={onDone}>
          Отмена
        </Button>
      </div>
    </form>
  );
}
