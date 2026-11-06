"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw, Unplug } from "lucide-react";
import { parseAsStringLiteral, useQueryState } from "nuqs";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { Badge, Button, buttonVariants, EmptyState, Skeleton, tabPanelId, Tabs } from "@/components/ui";
import { canOperate, isAdmin } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { trendsKeys, useTrendsAttentionBadge } from "@/lib/queries/trends";

import { BridgeStatus } from "./bridge-status";
import { FilmsPanel } from "./films-panel";
import { PipelinePanel } from "./pipeline-panel";
import { TodayPanel } from "./today-panel";
import { VideosPanel } from "./videos-panel";

const TABS = ["today", "films", "videos", "pipeline"] as const;
type TabId = (typeof TABS)[number];
const TAB_LABELS: Record<TabId, string> = {
  today: "Сегодня",
  films: "Кино и сериалы",
  videos: "Видео",
  pipeline: "Конвейер",
};
const ID_PREFIX = "trends";

/**
 * Трендоскаут (`/trends?tab=today|films|videos|pipeline`): мост к отдельному сервису Kino-trends, который
 * парсит популярные видео СНГ и фильмы-тренды, проверяет авторские права, отбирает «чистые» ролики
 * и ставит их в конвейер auto-shorts. Здесь — его подборка дня, чарты и очередь внутри консоли плюс
 * ссылка на сам Kino-trends; настройка моста (`kino_trends_url`) — для администратора прямо в шапке.
 */
export function TrendsScreen() {
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const admin = isAdmin(user.role);
  const queryClient = useQueryClient();
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TABS).withDefault("today").withOptions({ history: "replace" }),
  );
  const status = useTrendsAttentionBadge();
  const configured = status.data?.configured ?? false;
  const publicUrl = status.data?.public_url ?? null;
  const clean = status.data?.today?.clean ?? 0;
  const review = status.data?.pipeline?.review ?? 0;
  const failed = status.data?.pipeline?.failed ?? 0;

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="trends-screen">
      <PageHeader
        eyebrow="Тренды"
        title="Трендоскаут"
        description="Что сейчас смотрят в СНГ и что из этого можно взять: фильмы-тренды с проверкой прав и чистые ролики под нашу аудиторию. Данные и проверки — из Kino-trends, нарезка и залив — здесь, в auto-shorts."
        actions={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void queryClient.invalidateQueries({ queryKey: trendsKeys.all })}
              disabled={status.isFetching}
              title="Перечитать статус, подборку и очередь"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", status.isFetching && "animate-spin")} aria-hidden="true" />
              Обновить
            </Button>
            {publicUrl ? (
              <a
                href={publicUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={buttonVariants({ variant: "outline", size: "sm" })}
                data-testid="trends-open-kino"
              >
                Открыть Kino-trends
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              </a>
            ) : null}
          </>
        }
      />

      <BridgeStatus status={status} admin={admin} />

      {status.isPending ? (
        <div className="flex flex-col gap-4" aria-busy="true" aria-label="Загрузка">
          <Skeleton className="h-8 w-80" />
          <Skeleton className="h-64" />
        </div>
      ) : !configured ? (
        <EmptyState
          icon={Unplug}
          title="Мост не настроен"
          description={
            admin
              ? "Заполните адрес Kino-trends выше — подборка дня, чарты и очередь конвейера появятся здесь."
              : "Подборка дня, чарты и очередь конвейера появятся после того, как владелец укажет адрес Kino-trends в настройках."
          }
        />
      ) : (
        <>
          <Tabs
            idPrefix={ID_PREFIX}
            label="Разделы трендоскаута"
            value={tab}
            onChange={(value) => void setTab(value as TabId)}
            items={TABS.map((value) => ({
              value,
              label: TAB_LABELS[value],
              badge:
                value === "today" && clean ? (
                  <Badge variant="machine" className="font-mono" title="Чистых роликов в чартах">
                    {clean}
                  </Badge>
                ) : value === "pipeline" && (review || failed) ? (
                  <Badge variant={failed ? "negative" : "human"} className="font-mono" title="Ждут человека / ошибок">
                    {failed ? `${review}+${failed}` : review}
                  </Badge>
                ) : undefined,
            }))}
          />
          <section id={tabPanelId(ID_PREFIX, tab)} role="tabpanel" aria-labelledby={`${ID_PREFIX}-tab-${tab}`}>
            {tab === "videos" ? (
              <VideosPanel operator={operator} enabled={configured} />
            ) : tab === "films" ? (
              <FilmsPanel operator={operator} enabled={configured} />
            ) : tab === "pipeline" ? (
              <PipelinePanel operator={operator} enabled={configured} />
            ) : (
              <TodayPanel operator={operator} enabled={configured} />
            )}
          </section>
        </>
      )}
    </div>
  );
}
