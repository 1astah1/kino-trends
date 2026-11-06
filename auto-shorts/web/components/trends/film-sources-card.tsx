"use client";

import { ExternalLink, Search } from "lucide-react";
import { toast } from "sonner";

import { Badge, Button, SectionCard, StatusBadge } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { formatAgo, formatNumber } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { type TrendsFilmSource, useTrendsFilmActions } from "@/lib/queries/trends";

import { FILE_STATUS_META, fileStatus, formatDuration, sourceKindLabel, sourcePlatform } from "./labels";

/** Подпись проверки исходника моделью: подтверждён с уверенностью / отклонён / не проверен. */
function VerifiedBadge({ source }: { source: TrendsFilmSource }) {
  if (source.alive === 0) return <Badge variant="negative">удалён</Badge>;
  if (source.verified) {
    return (
      <Badge variant="positive" title={source.note ?? undefined}>
        подтверждён{source.trust !== null && source.trust !== undefined ? ` ${Math.round(source.trust * 100)} %` : ""}
      </Badge>
    );
  }
  const rejected = source.note && source.note !== "не проверено моделью";
  return (
    <Badge variant="warning" title={source.note ?? undefined}>
      {rejected ? "отклонён" : "не проверен"}
    </Badge>
  );
}

/**
 * «Исходники»: полный фильм или серии на YouTube и Rutube с проверкой нейросетью. Лучший (его берёт
 * нарезчик) выделен; у сериалов видно, какая серия и сезон. Оператор может запустить поиск заново.
 */
export function FilmSourcesCard({
  titleId,
  sources,
  best,
  checkedAt,
  operator,
}: {
  titleId: string;
  sources: TrendsFilmSource[];
  best?: TrendsFilmSource | null;
  checkedAt?: string | null;
  operator: boolean;
}) {
  const actions = useTrendsFilmActions(titleId);
  const now = useNow(60_000);
  const bestKey = best ? (best.id ?? best.url) : null;
  const episodes = sources.filter((source) => source.kind === "episode").length;

  async function onFind() {
    try {
      const result = await actions.findSources.mutateAsync();
      toast.success(`Найдено исходников: ${result.items.length}`, {
        description: `${result.items.filter((item) => item.verified).length} подтверждено моделью`,
      });
    } catch (error) {
      toast.error("Поиск не удался", { description: errorMessage(error) });
    }
  }

  return (
    <SectionCard
      title="Исходники"
      bodyClassName="gap-3"
      actions={
        <span className="flex items-center gap-2">
          {checkedAt ? <span className="text-2xs text-fg-muted">проверены {formatAgo(checkedAt, now)}</span> : null}
          {operator ? (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => void onFind()}
              disabled={actions.findSources.isPending}
              title="Найти полный фильм или серии на YouTube и Rutube и проверить моделью (до минуты)"
              data-testid="trends-film-find-sources"
            >
              <Search className={cn("h-3 w-3", actions.findSources.isPending && "animate-pulse")} aria-hidden="true" />
              {actions.findSources.isPending ? "Ищу…" : sources.length ? "Искать заново" : "Найти исходники"}
            </Button>
          ) : null}
        </span>
      }
    >
      {sources.length === 0 ? (
        <p className="text-sm text-fg-muted">
          Исходников ещё нет: без полного фильма или серии резать нечего.
          {operator ? " Запустите поиск — Kino-trends обойдёт YouTube и Rutube." : ""}
        </p>
      ) : (
        <>
          {episodes ? (
            <p className="text-xs text-fg-muted">
              серий найдено {episodes} из {sources.length} исходников
            </p>
          ) : null}
          <ul className="flex flex-col divide-y divide-border">
            {sources.map((source) => {
              const isBest = bestKey !== null && (source.id ?? source.url) === bestKey;
              const status = fileStatus(source.file_status);
              return (
                <li
                  key={source.id ?? source.url ?? source.video_id ?? source.label}
                  className={cn(
                    "flex flex-col gap-1 py-2 first:pt-0 last:pb-0",
                    isBest && "rounded-md bg-accent/5 px-2",
                  )}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="neutral" outline className="font-mono">
                      {sourcePlatform(source.url)}
                    </Badge>
                    {source.url ? (
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex min-w-0 items-center gap-1 truncate text-sm font-medium text-fg hover:underline"
                        title={source.label ?? source.url}
                      >
                        <span className="truncate">{source.label ?? source.url}</span>
                        <ExternalLink className="h-3 w-3 shrink-0 text-fg-muted" aria-hidden="true" />
                      </a>
                    ) : (
                      <span className="truncate text-sm font-medium text-fg">{source.label ?? "без названия"}</span>
                    )}
                    {isBest ? <Badge variant="accent">берёт нарезчик</Badge> : null}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted">
                    <span>{sourceKindLabel(source)}</span>
                    {source.channel ? <span>{source.channel}</span> : null}
                    {source.duration_s ? <span>{formatDuration(source.duration_s)}</span> : null}
                    {source.language ? <span>{source.language}</span> : null}
                    {source.views ? <span className="tabular">{formatNumber(source.views)} просм.</span> : null}
                    <VerifiedBadge source={source} />
                    {source.file_status ? <StatusBadge status={status} map={FILE_STATUS_META} /> : null}
                    {source.file_error ? <span className="text-negative">{source.file_error}</span> : null}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </SectionCard>
  );
}
