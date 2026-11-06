"use client";

import { Activity, NotebookPen, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  Callout,
  DetailGrid,
  KeyValue,
  SectionCard,
  Select,
  Skeleton,
  StatusBadge,
  Textarea,
} from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import { formatDateTime, formatNumber, formatPct } from "@/lib/format";
import {
  type Channel,
  useChannel,
  useChannelHealth,
  useChannelIncidents,
  useCreateIncident,
} from "@/lib/queries/channels";

import { ChannelForm } from "./channel-form";
import { CHANNEL_STATUS_META, INCIDENT_KIND_LABELS, PLATFORM_SHORT, SANCTION_SIGNAL_LABELS } from "./labels";
import { SideSheet } from "./sheet";
import { YoutubeCard } from "./youtube-card";

const INCIDENT_KINDS = Object.keys(INCIDENT_KIND_LABELS) as Schemas["IncidentKind"][];

/**
 * Карточка канала (§7.3 drawer): форма, здоровье, YouTube-автозалив, журнал инцидентов, предупреждение
 * о совпадающем профиле.
 */
export function ChannelDrawer({
  channelId,
  operator,
  admin,
  onClose,
  onOpenChannel,
}: {
  channelId: string | null;
  operator: boolean;
  admin: boolean;
  onClose: () => void;
  onOpenChannel: (id: string) => void;
}) {
  const channel = useChannel(channelId);
  const c = channel.data;
  return (
    <SideSheet
      open={!!channelId}
      onClose={onClose}
      testId="channel-drawer"
      title={
        c ? (
          <span className="flex items-center gap-2">
            {c.title || c.id}
            <Badge variant="neutral" outline>
              {PLATFORM_SHORT[c.platform]}
            </Badge>
          </span>
        ) : (
          (channelId ?? "Канал")
        )
      }
      description={c ? <span className="font-mono">{c.id}</span> : undefined}
    >
      {channel.isError ? (
        <div className="p-4">
          <SectionError error={channel.error} onRetry={() => void channel.refetch()} />
        </div>
      ) : !c ? (
        <div className="flex flex-col gap-3 p-4">
          <Skeleton className="h-24" />
          <Skeleton className="h-64" />
        </div>
      ) : (
        <div className="flex flex-col gap-4 p-4">
          {c.profile_duplicate_of.length ? (
            <Callout variant="warning" title="Профиль совпадает с другими каналами">
              <span data-testid="profile-duplicate">
                Тот же голос, шаблон и стиль, что у{" "}
                {c.profile_duplicate_of.map((id, index) => (
                  <span key={id}>
                    {index ? ", " : ""}
                    <button
                      type="button"
                      className="font-mono text-accent hover:underline"
                      onClick={() => onOpenChannel(id)}
                    >
                      {id}
                    </button>
                  </span>
                ))}
                . Площадки связывают такие каналы в сетку — сделайте профиль своим.
              </span>
            </Callout>
          ) : null}
          <HealthCard channel={c} />
          <SectionCard title="Настройки канала">
            <ChannelForm key={c.updated_at} channel={c} readOnly={!operator} onDone={() => undefined} />
          </SectionCard>
          {c.platform === "youtube" ? (
            <YoutubeCard channelId={c.id} channelTitle={c.title || c.id} operator={operator} admin={admin} />
          ) : null}
          <IncidentsCard channelId={c.id} operator={operator} />
        </div>
      )}
    </SideSheet>
  );
}

function HealthCard({ channel }: { channel: Channel }) {
  const health = useChannelHealth(channel.id);
  const h = health.data;
  return (
    <SectionCard title="Здоровье" actions={<Activity className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}>
      {health.isError ? (
        <SectionError error={health.error} onRetry={() => void health.refetch()} />
      ) : !h ? (
        <Skeleton className="h-16" />
      ) : (
        <>
          <DetailGrid>
            <KeyValue label="Статус" value={<StatusBadge status={h.status} map={CHANNEL_STATUS_META} />} />
            <KeyValue
              label="Снято за 30 дней"
              value={<span className="tabular font-mono">{formatNumber(h.removed_count_30d)}</span>}
            />
            <KeyValue
              label="Просмотры 7д к прошлым 7д"
              value={
                <span className="tabular font-mono">
                  {h.views_trend_7d === null ? "—" : `×${h.views_trend_7d.toFixed(2).replace(".", ",")}`}
                </span>
              }
            />
            <KeyValue label="Ниже p25 подряд" value={<span className="tabular font-mono">{h.below_p25_streak}</span>} />
            <KeyValue
              label="Доля ленты Shorts"
              value={
                <span className="tabular font-mono">
                  {h.shorts_feed_share === null ? "—" : formatPct(h.shorts_feed_share * 100)}
                </span>
              }
            />
            <KeyValue
              label="Подписчики"
              value={<span className="tabular font-mono">{formatNumber(channel.subscribers)}</span>}
            />
            <KeyValue
              label="План в день"
              value={<span className="tabular font-mono">{channel.publishing_plan.videos_per_day ?? "—"}</span>}
            />
            <KeyValue
              label="Публикаций (последние)"
              value={<span className="tabular font-mono">{h.last_publications.length}</span>}
            />
          </DetailGrid>
          {h.sanction_signals.length ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <TriangleAlert className="h-3.5 w-3.5 text-negative" aria-hidden="true" />
              {h.sanction_signals.map((signal) => (
                <Badge key={signal} variant="negative">
                  {SANCTION_SIGNAL_LABELS[signal] ?? signal}
                </Badge>
              ))}
            </div>
          ) : null}
          {h.last_publications.length === 0 ? (
            <p className="text-2xs text-fg-muted">
              Публикаций ещё нет: тренды и серии заполнятся с реестром публикаций (M3–M4).
            </p>
          ) : null}
        </>
      )}
    </SectionCard>
  );
}

function IncidentsCard({ channelId, operator }: { channelId: string; operator: boolean }) {
  const incidents = useChannelIncidents(channelId);
  const create = useCreateIncident(channelId);
  const [kind, setKind] = useState<Schemas["IncidentKind"]>("note");
  const [note, setNote] = useState("");

  const submit = async () => {
    try {
      await create.mutateAsync({ kind, note: note.trim() || null, details: {} });
      setNote("");
      toast.success("Запись добавлена в журнал");
    } catch (error) {
      toast.error("Не записано", { description: errorMessage(error) });
    }
  };

  return (
    <SectionCard
      title="Журнал инцидентов"
      actions={<NotebookPen className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}
    >
      {operator ? (
        <div className="flex flex-col gap-2 rounded-md border border-border p-2">
          <div className="flex gap-2">
            <Select
              aria-label="Тип записи"
              value={kind}
              onChange={(event) => setKind(event.target.value as Schemas["IncidentKind"])}
              className="w-56"
            >
              {INCIDENT_KINDS.map((value) => (
                <option key={value} value={value}>
                  {INCIDENT_KIND_LABELS[value]}
                </option>
              ))}
            </Select>
            <Button
              size="sm"
              onClick={() => void submit()}
              disabled={create.isPending || (kind === "note" && !note.trim())}
            >
              Записать
            </Button>
          </div>
          <Textarea
            aria-label="Что случилось"
            rows={2}
            placeholder="Что случилось, что сделали"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </div>
      ) : null}
      {incidents.isError ? (
        <SectionError error={incidents.error} onRetry={() => void incidents.refetch()} />
      ) : incidents.isPending ? (
        <Skeleton className="h-12" />
      ) : (incidents.data ?? []).length === 0 ? (
        <p className="text-xs text-fg-muted">Записей нет.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {(incidents.data ?? []).map((incident) => (
            <li key={incident.id} className="flex flex-col gap-0.5 py-1.5">
              <span className="flex items-center gap-2 text-xs">
                <Badge
                  variant={
                    incident.kind === "note" ? "neutral" : incident.kind === "reach_drop" ? "warning" : "negative"
                  }
                >
                  {INCIDENT_KIND_LABELS[incident.kind]}
                </Badge>
                <span className="text-fg-muted">{formatDateTime(incident.occurred_at, { short: true })}</span>
              </span>
              {incident.note ? <span className="text-sm">{incident.note}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
