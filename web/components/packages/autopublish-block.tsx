"use client";

import { CalendarClock, CircleSlash, Clock, ExternalLink, LoaderCircle, Zap } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";
import { toast } from "sonner";

import { Button, Callout, FormField, Input, SectionCard, StatusBadge } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import {
  type AutopublishRequest,
  type UploadPackage,
  useCancelAutopublish,
  useScheduleAutopublish,
} from "@/lib/queries/packages";

import { AUTOPUBLISH_STATE_META, PRIVACY_LABELS } from "./labels";
import { fromMskInput, toMskInput } from "./package-utils";
import { autopublishErrorMessage, autopublishProblems } from "./publish-errors";

type Action = "slot" | "now" | "at" | "cancel";

/**
 * Автозалив пакета через YouTube API (0019): в ближайший слот плана канала, сейчас или на время; снять с
 * очереди. Состояние — бейдж, время слота, ошибка и ссылка на залитый ролик. Только для каналов YouTube.
 */
export function AutopublishBlock({ pkg, operator }: { pkg: UploadPackage; operator: boolean }) {
  const schedule = useScheduleAutopublish(pkg.id);
  const cancel = useCancelAutopublish(pkg.id);
  const fieldId = useId();
  const [picking, setPicking] = useState(false);
  const [at, setAt] = useState("");
  const [failure, setFailure] = useState<{ action: Action; error: unknown } | null>(null);
  const [running, setRunning] = useState<Action | null>(null);

  const auto = pkg.autopublish ?? null;
  const state = auto?.state ?? null;
  const live = pkg.status === "ready" || pkg.status === "downloaded";
  const canSchedule = operator && live && state !== "uploading" && state !== "done";
  const canCancel = operator && (state === "scheduled" || state === "failed");
  const atIso = fromMskInput(at);

  const run = async (action: Action, body?: AutopublishRequest) => {
    if (action === "now" && !window.confirm("Залить ролик в YouTube сейчас? Приватность — из плана канала.")) return;
    setFailure(null);
    setRunning(action);
    try {
      if (action === "cancel" || !body) {
        await cancel.mutateAsync();
        toast.success("Снят с очереди автозалива");
      } else {
        const next = await schedule.mutateAsync(body);
        const when = formatDateTime(next.autopublish?.at, { short: true });
        toast.success(action === "now" ? "Заливка началась" : "В очереди автозалива", {
          description: action === "now" ? undefined : `Слот ${when}`,
        });
        setPicking(false);
        setAt("");
      }
    } catch (error) {
      setFailure({ action, error });
    } finally {
      setRunning(null);
    }
  };

  const blockedByChannel = failure
    ? autopublishProblems(failure.error).some((problem) => problem.code === "channel_not_connected")
    : false;
  const busy = running !== null;

  return (
    <SectionCard
      title="Автозалив YouTube"
      actions={state ? <StatusBadge status={state} map={AUTOPUBLISH_STATE_META} /> : undefined}
    >
      <div className="flex flex-col gap-2" data-testid="autopublish-block" data-state={state ?? "none"}>
        {!auto ? (
          <p className="text-sm text-fg-muted">
            Сервер зальёт ролик сам через YouTube API: в ближайший слот плана канала, сразу или в заданное время.
          </p>
        ) : state === "done" ? (
          <p className="text-sm">
            Залит {formatDateTime(auto.at, { short: true })}
            {auto.video_url ? (
              <>
                {" · "}
                <a
                  href={auto.video_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-accent hover:underline"
                  data-testid="autopublish-video"
                >
                  открыть ролик
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                </a>
              </>
            ) : null}
          </p>
        ) : state === "cancelled" ? (
          <p className="text-sm text-fg-muted">Снят с очереди — можно поставить снова.</p>
        ) : (
          <p className="text-sm" data-testid="autopublish-at">
            {state === "uploading" ? "Заливается сейчас" : "Зальётся"}{" "}
            <span className="tabular font-mono">{formatDateTime(auto.at, { short: true })}</span>
            {auto.privacy ? <span className="text-fg-muted"> · {PRIVACY_LABELS[auto.privacy]}</span> : null}
            {auto.attempts ? <span className="text-fg-muted"> · попыток {auto.attempts}</span> : null}
          </p>
        )}
        {auto?.error && state !== "done" ? (
          <p className="text-xs text-negative" data-testid="autopublish-state-error">
            {auto.error}
          </p>
        ) : null}

        {failure ? (
          <Callout
            variant="negative"
            title={failure.action === "cancel" ? "Не снят с очереди" : "Автозалив не поставлен"}
            className="py-2"
          >
            <span data-testid="autopublish-error">{autopublishErrorMessage(failure.error)}</span>
            {blockedByChannel ? (
              <>
                {" "}
                <Link
                  href={routeHref("/channels", { channel: pkg.channel.id })}
                  className="text-accent hover:underline"
                >
                  Открыть карточку канала
                </Link>
              </>
            ) : null}
          </Callout>
        ) : null}

        {canSchedule || canCancel ? (
          <div className="flex flex-wrap items-center gap-2">
            {canSchedule ? (
              <>
                <Button
                  size="sm"
                  onClick={() => void run("slot", { now: false })}
                  disabled={busy}
                  data-testid="autopublish-slot"
                >
                  {running === "slot" ? (
                    <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                  ) : (
                    <CalendarClock className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                  {state === "scheduled" ? "Перенести в ближайший слот" : "В ближайший слот"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void run("now", { now: true })}
                  disabled={busy}
                  data-testid="autopublish-now"
                >
                  <Zap className="h-3.5 w-3.5" aria-hidden="true" />
                  Сейчас
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setPicking((value) => !value)}
                  disabled={busy}
                  aria-expanded={picking}
                  data-testid="autopublish-at-toggle"
                >
                  <Clock className="h-3.5 w-3.5" aria-hidden="true" />
                  На время
                </Button>
              </>
            ) : null}
            {canCancel ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void run("cancel")}
                disabled={busy}
                data-testid="autopublish-cancel"
              >
                <CircleSlash className="h-3.5 w-3.5" aria-hidden="true" />
                Снять с очереди
              </Button>
            ) : null}
          </div>
        ) : null}

        {picking && canSchedule ? (
          <div className="flex flex-wrap items-end gap-2">
            <FormField label="Когда залить (МСК)" htmlFor={`${fieldId}-at`} hint="В прошлом — зальётся сразу">
              <Input
                id={`${fieldId}-at`}
                type="datetime-local"
                value={at}
                min={toMskInput(new Date().toISOString())}
                onChange={(event) => setAt(event.target.value)}
                className="sm:max-w-60"
              />
            </FormField>
            <Button
              size="sm"
              onClick={() => (atIso ? void run("at", { now: false, at: atIso }) : undefined)}
              disabled={busy || !atIso}
              data-testid="autopublish-at-submit"
            >
              Поставить
            </Button>
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}
