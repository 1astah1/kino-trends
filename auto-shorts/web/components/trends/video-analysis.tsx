"use client";

import type { ReactNode } from "react";

import { Badge, SectionCard } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import type { TrendsVideoDetail } from "@/lib/queries/trends";

import { CATEGORY_RU, fitTone, formatFit, rankEntries, REGION_RU } from "./labels";

/** Пары «ключ — значение» в столбик: значения длинные (тексты нейросети), сетка DetailGrid их обрезала бы. */
export function KeyValueList({ items }: { items: Array<{ label: string; value: ReactNode } | null> }) {
  const rows = items.filter((item): item is { label: string; value: ReactNode } => !!item && item.value !== null);
  if (!rows.length) return null;
  return (
    <dl className="grid grid-cols-[minmax(0,9rem)_1fr] gap-x-3 gap-y-1.5 text-sm">
      {rows.map((row) => (
        <div key={row.label} className="contents">
          <dt className="text-fg-muted">{row.label}</dt>
          <dd className="min-w-0 text-fg">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

const text = (value: string | null | undefined) => (value && value.trim() ? value : null);
const pct = (value: number | null | undefined) =>
  value === null || value === undefined ? null : `${Math.round(value * 100)} %`;

/** «Разбор нейросети» по названию, описанию, тегам и цифрам: тема, аудитория, хук, совет по баннеру. */
export function VideoAnalysisCard({ video }: { video: TrendsVideoDetail }) {
  const a = video.analysis ?? null;
  const hasAnalysis = !!a && Object.values(a).some((value) => value !== null && value !== undefined && value !== "");
  const flags = a
    ? [
        a.is_reupload ? "уже перезалив или компиляция" : null,
        a.has_sponsor ? "спонсор в ролике" : null,
        a.music_heavy ? "музыка в основе" : null,
        a.film_title ? `кадры из «${a.film_title}»` : null,
      ].filter((flag): flag is string => !!flag)
    : [];

  return (
    <SectionCard title="Разбор нейросети" bodyClassName="gap-3">
      {!hasAnalysis || !a ? (
        <p className="text-sm text-fg-muted">
          Нейросеть ещё не разбирала ролик. «Разобрать заново» спросит модель о теме, аудитории и баннере.
        </p>
      ) : (
        <>
          <KeyValueList
            items={[
              { label: "тема", value: text(a.topic) },
              { label: "формат", value: text(a.format) },
              { label: "ниша", value: text(a.niche) },
              { label: "кто смотрит", value: text(a.audience) },
              {
                label: "наша аудитория",
                value:
                  a.audience_fit !== null && a.audience_fit !== undefined ? (
                    <span>
                      <span className={cn("tabular font-mono font-medium", fitTone(a.audience_fit))}>
                        {formatFit(a.audience_fit)}
                      </span>
                      {a.audience_why ? <span className="text-fg-muted"> · {a.audience_why}</span> : null}
                    </span>
                  ) : null,
              },
              { label: "почему зашло", value: text(a.why_popular) },
              { label: "хук", value: text(a.hook) },
              { label: "совет по баннеру", value: text(a.banner_advice) },
              { label: "пригодность под баннер", value: pct(a.reuse_fit) },
              {
                label: "флаги",
                value: flags.length ? (
                  <span className="flex flex-wrap gap-1">
                    {flags.map((flag) => (
                      <Badge key={flag} variant="warning">
                        {flag}
                      </Badge>
                    ))}
                  </span>
                ) : (
                  <span className="text-fg-muted">нет</span>
                ),
              },
            ]}
          />
          {video.analysis_at || a.model ? (
            <p className="text-2xs text-fg-muted">
              {video.analysis_at ? `разбор ${formatDateTime(video.analysis_at, { short: true })}` : null}
              {video.analysis_at && a.model ? " · " : null}
              {a.model ? `модель ${a.model}` : null}
            </p>
          ) : null}
        </>
      )}
    </SectionCard>
  );
}

/** «Где в чартах»: место в чарте «самое популярное» по странам и по категориям России. */
export function VideoChartsCard({
  video,
  regionsRu,
  categoriesRu,
}: {
  video: TrendsVideoDetail;
  regionsRu?: Record<string, string>;
  categoriesRu?: Record<string, string>;
}) {
  const regions = rankEntries(video.regions as Record<string, unknown> | null);
  const cats = rankEntries(video.cats as Record<string, unknown> | null);
  const regionName = (code: string) => regionsRu?.[code] ?? REGION_RU[code] ?? code;
  const catName = (code: string) => categoriesRu?.[code] ?? CATEGORY_RU[code] ?? code;

  return (
    <SectionCard title="Где в чартах">
      {!regions.length && !cats.length ? (
        <p className="text-sm text-fg-muted">
          {video.origin === "cc"
            ? "Найден поиском Creative Commons, в чартах стран не был."
            : "В чартах по странам сейчас не числится."}
        </p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {regions.map(([code, rank]) => (
            <Badge key={`r-${code}`} variant="accent" title={`${regionName(code)}${rank ? `, место ${rank}` : ""}`}>
              <span className="font-mono">{code}</span>
              {rank ? <span className="tabular">#{rank}</span> : null}
            </Badge>
          ))}
          {cats.map(([code, rank]) => (
            <Badge key={`c-${code}`} variant="neutral" title="чарт России по категории">
              {catName(code)}
              {rank ? <span className="tabular"> #{rank}</span> : null}
            </Badge>
          ))}
        </div>
      )}
    </SectionCard>
  );
}

/** «Карточка YouTube»: лицензия, заявки партнёра, встраивание, субтитры, качество, темы, теги, источник. */
export function VideoYoutubeCard({ video }: { video: TrendsVideoDetail }) {
  const tags = video.tags ?? [];
  const topics = video.topics ?? [];
  return (
    <SectionCard title="Карточка YouTube">
      <KeyValueList
        items={[
          { label: "лицензия", value: video.license === "creativeCommon" ? "Creative Commons" : "стандартная YouTube" },
          { label: "заявлен партнёром", value: video.licensed_content ? "да: Content ID" : "нет" },
          {
            label: "встраивание",
            value:
              video.embeddable === null || video.embeddable === undefined
                ? null
                : video.embeddable
                  ? "разрешено"
                  : "запрещено",
          },
          {
            label: "субтитры",
            value: video.caption === null || video.caption === undefined ? null : video.caption ? "есть" : "нет",
          },
          { label: "качество", value: text(video.definition)?.toUpperCase() ?? null },
          { label: "темы", value: topics.length ? topics.join(", ") : null },
          {
            label: "теги",
            value: tags.length ? (
              <span className="flex flex-wrap gap-1">
                {tags.slice(0, 16).map((tag) => (
                  <Badge key={tag} variant="neutral" outline>
                    {tag}
                  </Badge>
                ))}
                {tags.length > 16 ? <span className="text-xs text-fg-muted">+{tags.length - 16}</span> : null}
              </span>
            ) : null,
          },
          { label: "источник", value: video.origin === "cc" ? "поиск Creative Commons" : "чарт" },
          {
            label: "в чартах с",
            value: video.first_seen_at ? formatDateTime(video.first_seen_at, { short: true }) : null,
          },
          { label: "замер статистики", value: video.stats_at ? formatDateTime(video.stats_at, { short: true }) : null },
          { label: "id", value: <span className="font-mono text-xs">{video.video_id}</span> },
        ]}
      />
    </SectionCard>
  );
}
