"use client";

import { ChevronRight, Link2, Sparkles, UserRound } from "lucide-react";

import { Badge, StatusBadge } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatAgo } from "@/lib/format";
import type { UploadPackage } from "@/lib/queries/packages";
import type { TrackingLink } from "@/lib/queries/tracking";

import { CopyButton, useCopy } from "./copy";
import { AUTOPUBLISH_STATE_META, PACKAGE_STATUS_META } from "./labels";
import type { ChannelGroupData } from "./package-utils";

function requiredProgress(pkg: UploadPackage): { done: number; total: number } {
  const required = pkg.checklist.filter((item) => item.required);
  return { done: required.filter((item) => item.done).length, total: required.length };
}

export function PackageRow({
  pkg,
  selected,
  onSelect,
}: {
  pkg: UploadPackage;
  selected: boolean;
  onSelect: (id: number) => void;
}) {
  const progress = requiredProgress(pkg);
  const awaitingUrl = pkg.status === "downloaded";
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(pkg.id)}
        aria-current={selected ? "true" : undefined}
        className={cn(
          "flex w-full items-start gap-2 rounded-md border px-2.5 py-2 text-left transition-colors duration-150",
          selected ? "border-accent bg-accent/10" : "border-transparent hover:bg-surface-2/70",
        )}
        data-testid="package-row"
        data-package-id={pkg.id}
        data-status={pkg.status}
        data-platform={pkg.channel.platform}
      >
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="truncate text-sm font-medium text-fg">{pkg.texts.title}</span>
          <span className="flex flex-wrap items-center gap-1.5">
            <StatusBadge status={pkg.status} map={PACKAGE_STATUS_META} />
            {pkg.autopublish && pkg.autopublish.state !== "cancelled" ? (
              <StatusBadge status={pkg.autopublish.state} map={AUTOPUBLISH_STATE_META} />
            ) : null}
            {pkg.kind === "clip" ? (
              <Badge variant="neutral" outline>
                нарезка
              </Badge>
            ) : null}
            {pkg.ai_disclosure_required ? (
              <Badge variant="machine" title="Нужна AI-метка при заливке">
                <Sparkles className="h-3 w-3" aria-hidden="true" />
                AI
              </Badge>
            ) : null}
            {pkg.texts.texts_mode === "template" ? (
              <Badge variant="warning" title={pkg.texts.texts_label}>
                шаблон
              </Badge>
            ) : null}
          </span>
          <span className="flex items-center gap-2 font-mono text-2xs text-fg-muted">
            <span>{pkg.video_uid}</span>
            <span>·</span>
            <span className="tabular">
              чек-лист {progress.done}/{progress.total}
            </span>
            <span>·</span>
            <span>
              {awaitingUrl && pkg.marked_uploaded_at
                ? `залит ${formatAgo(pkg.marked_uploaded_at)}`
                : formatAgo(pkg.created_at)}
            </span>
          </span>
        </span>
        <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-fg-muted" aria-hidden="true" />
      </button>
    </li>
  );
}

/**
 * Группа канала: **подсказка аккаунта** (в какой Brand Account зайти) и ссылка канала для
 * кнопки «Подписаться» / шапки канала, если она уже создана.
 */
export function ChannelGroup({
  group,
  channelLink,
  selectedId,
  onSelect,
}: {
  group: ChannelGroupData;
  channelLink: TrackingLink | null;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const { copied, copy } = useCopy();
  return (
    <section className="flex flex-col gap-1" data-testid="channel-group" data-channel-id={group.channelId}>
      <header className="flex flex-col gap-0.5 px-1">
        <div className="flex items-center gap-2">
          <h3 className="truncate text-sm font-semibold text-fg">{group.displayName}</h3>
          <Badge variant={group.platform === "youtube" ? "accent" : "machine"} outline>
            {group.platform === "youtube" ? "YouTube" : "VK"}
          </Badge>
          <span className="ml-auto tabular font-mono text-2xs text-fg-muted">{group.items.length}</span>
        </div>
        <p className="flex items-center gap-1 text-xs text-fg-muted" title="В какой аккаунт зайти перед заливкой">
          <UserRound className="h-3 w-3 shrink-0 text-human" aria-hidden="true" />
          <span className="truncate">{group.accountHint}</span>
        </p>
        {channelLink ? (
          <p className="flex items-center gap-1.5 text-xs text-fg-muted">
            <Link2 className="h-3 w-3 shrink-0" aria-hidden="true" />
            <span className="truncate font-mono">{channelLink.redirect_url}</span>
            <CopyButton
              copied={copied === "channel"}
              onCopy={() => void copy("channel", channelLink.redirect_url, "Ссылка канала")}
              label="Ссылка канала"
              className="ml-auto h-5"
            />
          </p>
        ) : null}
      </header>
      <ul className="flex flex-col gap-0.5">
        {group.items.map((pkg) => (
          <PackageRow key={pkg.id} pkg={pkg} selected={pkg.id === selectedId} onSelect={onSelect} />
        ))}
      </ul>
    </section>
  );
}
