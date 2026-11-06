"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Switch } from "@/components/queue/switch";
import { Badge, FormField, SectionCard, Select, StatusBadge } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber } from "@/lib/format";
import { type TrendsVideoDetail, useTrendsVideoActions } from "@/lib/queries/trends";

import {
  CLEAN_LEVEL_META,
  cleanLevel,
  licenseMeta,
  MANUAL_LEVEL_OPTIONS,
  SCORE_CHECK,
  SCORE_CLEAN,
  scoreTone,
} from "./labels";

/** Шкала балла чистоты 0–100 с засечками порогов 45 и 70. */
export function ScoreBar({ score, className }: { score: number | null | undefined; className?: string }) {
  const value = score === null || score === undefined ? 0 : Math.max(0, Math.min(100, score));
  return (
    <span
      className={cn("relative block h-2 w-full overflow-hidden rounded-full bg-surface-2", className)}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
      aria-label="Балл чистоты"
      title={`балл 0–100: от ${SCORE_CHECK} проверить, от ${SCORE_CLEAN} чистое`}
    >
      <span className={cn("absolute inset-y-0 left-0 rounded-full", scoreTone(score))} style={{ width: `${value}%` }} />
      {[SCORE_CHECK, SCORE_CLEAN].map((mark) => (
        <span
          key={mark}
          aria-hidden="true"
          className="absolute inset-y-0 w-px bg-fg-muted/60"
          style={{ left: `${mark}%` }}
        />
      ))}
    </span>
  );
}

/**
 * «Оценка чистоты»: уровень, балл со шкалой, лицензия, флаги YouTube и причины из Kino-trends; оператору —
 * решение руками (сильнее автоматической оценки) и тумблер подборки под перезалив.
 */
export function VideoVerdict({ video, operator }: { video: TrendsVideoDetail; operator: boolean }) {
  const actions = useTrendsVideoActions(video.video_id);
  const [savingLevel, setSavingLevel] = useState(false);
  const level = cleanLevel(video.level);
  const license = licenseMeta(video);
  const manual = (video.manual_level ?? "") as "" | "clean" | "check" | "no";
  const reasons = video.reasons ?? [];
  const notes = video.notes ?? [];

  async function onLevel(next: string) {
    setSavingLevel(true);
    try {
      await actions.level.mutateAsync({ level: next === "" ? null : (next as "clean" | "check" | "no") });
      toast.success("Решение сохранено", {
        description: next ? `уровень: ${next}` : "вернулась автоматическая оценка",
      });
    } catch (error) {
      toast.error("Решение не сохранено", { description: errorMessage(error) });
    } finally {
      setSavingLevel(false);
    }
  }

  async function onPick(next: boolean) {
    try {
      await actions.pick.mutateAsync({ pick: next });
      toast.success(next ? "Взято в подборку" : "Убрано из подборки");
    } catch (error) {
      toast.error("Подборка не изменена", { description: errorMessage(error) });
    }
  }

  return (
    <SectionCard title="Оценка чистоты" bodyClassName="gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={level} map={CLEAN_LEVEL_META} />
        <Badge variant={license.variant} title={license.title}>
          {license.label}
        </Badge>
        {video.made_for_kids ? <Badge variant="warning">детское</Badge> : null}
        {video.blocked_ru ? <Badge variant="warning">закрыт для РФ</Badge> : null}
        {video.pick ? <Badge variant="accent">{video.pick_auto ? "в подборке (авто)" : "в подборке"}</Badge> : null}
        {video.used_at ? <Badge variant="accent">залито</Badge> : null}
        {video.manual_level ? (
          <Badge variant="human" outline title="Решение оператора сильнее автоматической оценки">
            решение оператора
          </Badge>
        ) : null}
      </div>
      <div className="flex items-center gap-3">
        <ScoreBar score={video.score} className="flex-1" />
        <span className="tabular shrink-0 font-mono text-sm text-fg">
          балл {video.score !== null && video.score !== undefined ? formatNumber(video.score) : "—"}
        </span>
      </div>
      <p className="text-2xs text-fg-muted">
        от {SCORE_CHECK} — проверить, от {SCORE_CLEAN} — чистое
        {video.assessed_at ? ` · оценено ${formatDateTime(video.assessed_at, { short: true })}` : ""}
      </p>
      {reasons.length ? (
        <ul className="flex flex-col gap-1 text-sm text-fg">
          {reasons.map((reason) => (
            <li key={reason} className="flex gap-2">
              <span aria-hidden="true" className="mt-2 h-1 w-1 shrink-0 rounded-full bg-fg-muted" />
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-fg-muted">Причин оценки Kino-trends не прислал.</p>
      )}
      {notes.length ? <p className="text-xs text-fg-muted">{notes.join(" · ")}</p> : null}
      {operator ? (
        <div className="flex flex-wrap items-end gap-4 border-t border-border pt-3">
          <FormField
            label="Решение оператора"
            htmlFor="trends-video-manual-level"
            hint="Сильнее автоматической оценки; «не брать» убирает из подборки"
          >
            <Select
              id="trends-video-manual-level"
              value={manual}
              onChange={(event) => void onLevel(event.target.value)}
              disabled={savingLevel}
              className="w-40"
              data-testid="trends-video-level"
            >
              {MANUAL_LEVEL_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </FormField>
          <label className="flex items-center gap-2 pb-5 text-sm text-fg">
            <Switch
              checked={!!video.pick}
              onChange={(next) => void onPick(next)}
              disabled={actions.pick.isPending || level === "no"}
              label="В подборке под перезалив"
            />
            в подборке
          </label>
        </div>
      ) : null}
    </SectionCard>
  );
}
