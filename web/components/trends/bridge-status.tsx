"use client";

import { useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { CircleCheck, CircleX, Plug, RotateCcw, Settings2, Unplug } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Callout, FormField, Input, Skeleton } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { formatAgo, formatNumber } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { useSettingsList, useUpdateSettings } from "@/lib/queries/settings";
import { trendsKeys, type TrendsStatus } from "@/lib/queries/trends";

const URL_SETTING = "kino_trends_url";
const PUBLIC_URL_SETTING = "kino_trends_public_url";

/**
 * Шапка моста: настроен ли адрес Kino-trends, отвечает ли сервис, счётчики дня и очереди.
 * Для администратора — поле адреса прямо здесь (те же настройки группы `trends`, что и в
 * «Настройки → Провайдеры/Система»); остальным — подсказка, кого просить.
 */
export function BridgeStatus({ status, admin }: { status: UseQueryResult<TrendsStatus>; admin: boolean }) {
  const [editing, setEditing] = useState(false);
  const now = useNow(30_000);
  const data = status.data;

  if (status.isPending) {
    return <Skeleton className="h-9 w-full max-w-xl" data-testid="trends-bridge-loading" />;
  }

  const configured = data?.configured ?? false;
  const reachable = data?.reachable ?? false;
  const showForm = admin && (editing || !configured);

  return (
    <div className="flex flex-col gap-3" data-testid="trends-bridge-status">
      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
        {configured ? (
          <Badge variant="accent">
            <Plug className="h-3 w-3" aria-hidden="true" />
            мост настроен
          </Badge>
        ) : (
          <Badge variant="warning">
            <Unplug className="h-3 w-3" aria-hidden="true" />
            мост не настроен
          </Badge>
        )}
        {configured ? (
          status.isError ? (
            <Badge variant="negative">
              <CircleX className="h-3 w-3" aria-hidden="true" />
              статус не загрузился
            </Badge>
          ) : reachable ? (
            <Badge variant="positive">
              <CircleCheck className="h-3 w-3" aria-hidden="true" />
              Kino-trends отвечает
            </Badge>
          ) : (
            <Badge variant="negative">
              <CircleX className="h-3 w-3" aria-hidden="true" />
              Kino-trends не отвечает
            </Badge>
          )
        ) : null}
        {data?.today ? (
          <span className="tabular">
            кино сегодня {formatNumber(data.today.films_done)} из {formatNumber(data.today.films)} · чистых роликов{" "}
            {formatNumber(data.today.clean)} · на проверку {formatNumber(data.today.check)}
          </span>
        ) : null}
        {data?.pipeline ? (
          <span className="tabular">
            конвейер: в работе {formatNumber(data.pipeline.active)} · ждут человека {formatNumber(data.pipeline.review)}{" "}
            · ошибок {formatNumber(data.pipeline.failed)}
            {data.pipeline.linked ? null : (
              <span
                className="text-warning"
                title="Kino-trends не видит auto-shorts: проверьте autoshorts_url в его «Сборе»"
              >
                {" "}
                · связь с auto-shorts не подтверждена
              </span>
            )}
          </span>
        ) : null}
        {data?.checked_at ? <span>проверено {formatAgo(data.checked_at, now)}</span> : null}
        {admin && configured && !editing ? (
          <Button variant="ghost" size="xs" onClick={() => setEditing(true)} data-testid="trends-bridge-edit">
            <Settings2 className="h-3 w-3" aria-hidden="true" />
            Изменить адрес
          </Button>
        ) : null}
      </div>

      {!configured ? (
        <Callout variant="warning" title="Мост к Kino-trends не настроен">
          {data?.error ? <p>{data.error}.</p> : null}
          <p>
            Укажите адрес сервиса в настройке <code className="font-mono text-xs">kino_trends_url</code> (Настройки →
            Провайдеры/Система, группа «Трендоскаут»), например{" "}
            <code className="font-mono text-xs">http://host:18320</code> (адрес сервиса с портом, обычно локальный)
            {admin ? " — или прямо здесь." : ". Это может сделать владелец."}
          </p>
        </Callout>
      ) : status.isError ? (
        <Callout
          variant="negative"
          title="Статус моста не загрузился"
          action={
            <Button variant="outline" size="sm" onClick={() => void status.refetch()}>
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              Повторить
            </Button>
          }
        >
          {errorMessage(status.error)}
        </Callout>
      ) : !reachable ? (
        <Callout
          variant="negative"
          title="Kino-trends не отвечает"
          action={
            <Button variant="outline" size="sm" onClick={() => void status.refetch()}>
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              Проверить снова
            </Button>
          }
        >
          {data?.error ?? "Нет ответа от сервиса."} Проверьте, что сервис запущен и адрес в{" "}
          <code className="font-mono text-xs">kino_trends_url</code> верный (Настройки → Провайдеры/Система).
        </Callout>
      ) : null}

      {showForm ? <BridgeSetupForm onDone={() => setEditing(false)} /> : null}
    </div>
  );
}

/** Форма администратора: адрес API Kino-trends и публичный адрес для кнопки «Открыть Kino-trends». */
function BridgeSetupForm({ onDone }: { onDone: () => void }) {
  const settings = useSettingsList();
  if (settings.isPending) return <Skeleton className="h-20 w-full max-w-2xl" />;
  if (settings.isError) {
    return (
      <Callout variant="negative" title="Настройки не загрузились">
        {errorMessage(settings.error)}
      </Callout>
    );
  }
  const current = (key: string) => {
    const item = settings.data.find((s) => s.key === key);
    return typeof item?.value === "string" ? item.value : "";
  };
  return (
    <BridgeSetupFields
      initialUrl={current(URL_SETTING)}
      initialPublicUrl={current(PUBLIC_URL_SETTING)}
      onDone={onDone}
    />
  );
}

function BridgeSetupFields({
  initialUrl,
  initialPublicUrl,
  onDone,
}: {
  initialUrl: string;
  initialPublicUrl: string;
  onDone: () => void;
}) {
  const [url, setUrl] = useState(initialUrl);
  const [publicUrl, setPublicUrl] = useState(initialPublicUrl);
  const update = useUpdateSettings();
  const queryClient = useQueryClient();
  const trimmed = url.trim();
  const urlError = trimmed && !/^https?:\/\/\S+$/i.test(trimmed) ? "Нужен адрес вида http://host:port" : undefined;

  async function onSave() {
    if (urlError) return;
    try {
      await update.mutateAsync({
        [URL_SETTING]: trimmed || null,
        [PUBLIC_URL_SETTING]: publicUrl.trim() || null,
      });
      toast.success(trimmed ? "Адрес Kino-trends сохранён" : "Адрес Kino-trends сброшен");
      void queryClient.invalidateQueries({ queryKey: trendsKeys.all });
      onDone();
    } catch (error) {
      toast.error("Не сохранено", { description: errorMessage(error) });
    }
  }

  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-3 md:flex-row md:items-end"
      onSubmit={(event) => {
        event.preventDefault();
        void onSave();
      }}
      data-testid="trends-bridge-form"
    >
      <FormField
        label="Адрес Kino-trends (API)"
        htmlFor="trends-bridge-url"
        hint="Куда ходит сервер auto-shorts; обычно локальный адрес"
        error={urlError}
        className="flex-1"
      >
        <Input
          id="trends-bridge-url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="http://host:18320"
          autoComplete="off"
          spellCheck={false}
          aria-invalid={urlError ? true : undefined}
        />
      </FormField>
      <FormField
        label="Публичный адрес"
        htmlFor="trends-bridge-public-url"
        hint="Для кнопки «Открыть Kino-trends» в браузере; пусто — кнопки нет"
        className="flex-1"
      >
        <Input
          id="trends-bridge-public-url"
          value={publicUrl}
          onChange={(event) => setPublicUrl(event.target.value)}
          placeholder="https://site/kino-trends/"
          autoComplete="off"
          spellCheck={false}
        />
      </FormField>
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={update.isPending || Boolean(urlError)}>
          {update.isPending ? "Сохраняю…" : "Сохранить"}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onDone} disabled={update.isPending}>
          Отмена
        </Button>
      </div>
    </form>
  );
}
