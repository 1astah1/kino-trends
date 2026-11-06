"use client";

import { Lock, Play, RefreshCw, Square, StepForward, Unplug } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { Button, Callout, Dialog, EmptyState, Input, SectionCard, Select, Skeleton } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import {
  type TrendsSettingItem,
  useCancelRun,
  usePipelineTick,
  useRefreshVideos,
  useStartRun,
  useTrendsRuns,
  useTrendsSettings,
  useUpdateTrendsSettings,
} from "@/lib/queries/trends-ops";

import {
  formatElapsed,
  RIGHTS_POLICY_LABELS,
  SETTING_GROUP_LABELS,
  SETTING_GROUPS,
  type SettingGroup,
  settingValueText,
} from "./labels";
import { Switch } from "./switch";

const AUTOPUBLISH_KEY = "pipeline_autopublish";
const PIPELINE_KEY = "pipeline_enabled";

/** Целое число с сохранением по blur/Enter; пока пользователь печатает — черновик, иначе значение с сервера. */
function IntField({
  item,
  disabled,
  onSave,
}: {
  item: TrendsSettingItem;
  disabled: boolean;
  onSave: (value: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const current = item.value === null || item.value === undefined ? "" : String(item.value);
  const commit = () => {
    if (draft === null) return;
    const text = draft.trim();
    setDraft(null);
    if (text === "" || text === current) return;
    const value = Number(text);
    if (!Number.isInteger(value)) {
      toast.error("Нужно целое число");
      return;
    }
    if (item.min !== null && item.min !== undefined && value < item.min) {
      toast.error(`Не меньше ${item.min}`);
      return;
    }
    if (item.max !== null && item.max !== undefined && value > item.max) {
      toast.error(`Не больше ${item.max}`);
      return;
    }
    onSave(value);
  };
  return (
    <span className="inline-flex items-center gap-1.5">
      <Input
        type="number"
        inputMode="numeric"
        min={item.min ?? undefined}
        max={item.max ?? undefined}
        value={draft ?? current}
        disabled={disabled}
        aria-label={item.label}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            event.currentTarget.blur();
          }
        }}
        className="h-7 w-24 font-mono text-xs"
        data-testid={`queue-setting-${item.key}`}
      />
      {item.unit ? <span className="text-xs text-fg-muted">{item.unit}</span> : null}
    </span>
  );
}

function SettingRow({
  item,
  admin,
  saving,
  dimmed,
  onSave,
}: {
  item: TrendsSettingItem;
  admin: boolean;
  saving: boolean;
  dimmed: boolean;
  onSave: (item: TrendsSettingItem, value: boolean | number | string) => void;
}) {
  const disabled = !admin || saving;
  const id = `queue-setting-${item.key}`;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border py-2.5 last:border-b-0",
        dimmed && "opacity-60",
      )}
      data-testid="queue-setting-row"
    >
      <div className="min-w-0 flex-1">
        <label htmlFor={id} className="text-sm font-medium text-fg">
          {item.label}
          {item.key === AUTOPUBLISH_KEY ? <span className="ml-1 text-xs font-normal text-human">важно</span> : null}
        </label>
        <p className="text-xs text-fg-muted">{item.description}</p>
        {!admin ? (
          <p className="text-2xs text-fg-muted">
            сейчас: <b className="font-medium text-fg">{settingValueText(item.kind, item.value, item.unit)}</b>
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {saving ? <RefreshCw className="h-3.5 w-3.5 animate-spin text-fg-muted" aria-hidden="true" /> : null}
        {item.kind === "bool" ? (
          <Switch
            id={id}
            label={item.label}
            checked={item.value === true}
            disabled={disabled}
            onChange={(next) => onSave(item, next)}
          />
        ) : item.kind === "choice" ? (
          <Select
            id={id}
            value={typeof item.value === "string" ? item.value : ""}
            disabled={disabled}
            onChange={(event) => onSave(item, event.target.value)}
            className="h-7 w-auto min-w-56 text-xs"
          >
            {(item.choices ?? []).map((choice) => (
              <option key={choice} value={choice}>
                {RIGHTS_POLICY_LABELS[choice] ?? choice}
              </option>
            ))}
          </Select>
        ) : (
          <IntField item={item} disabled={disabled} onSave={(value) => onSave(item, value)} />
        )}
      </div>
    </div>
  );
}

/**
 * Вкладка «Управление»: кнопки «сделать сейчас» (собрать тренды, остановить сбор, обновить чарты и разбор,
 * прогнать конвейер) для оператора и тумблеры автоматики Kino-trends для владельца — сохранение по
 * изменению, каждое пишется в аудит. Автозалив включается только руками и с подтверждением.
 */
export function ControlPanel({
  operator,
  admin,
  trendsConfigured,
  now,
}: {
  operator: boolean;
  admin: boolean;
  trendsConfigured: boolean;
  now: number;
}) {
  const runs = useTrendsRuns({}, { enabled: trendsConfigured });
  const settings = useTrendsSettings({ enabled: trendsConfigured });
  const update = useUpdateTrendsSettings();
  const start = useStartRun();
  const cancel = useCancelRun();
  const refresh = useRefreshVideos();
  const tick = usePipelineTick();
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<TrendsSettingItem | null>(null);

  if (!trendsConfigured) {
    return (
      <EmptyState
        icon={Unplug}
        title="Мост к Kino-trends не настроен"
        description="Запуск сбора и тумблеры автоматики появятся после настройки адреса Kino-trends в «Трендоскауте». Задачами auto-shorts управляйте на вкладках «Сейчас», «Очередь» и «Сбои»."
      />
    );
  }

  const current = runs.data?.current;
  const running = current?.running ?? false;
  const popularRunning = runs.data?.jobs?.popular?.running ?? false;

  async function save(item: TrendsSettingItem, value: boolean | number | string) {
    setSavingKey(item.key);
    try {
      await update.mutateAsync({ [item.key]: value });
      toast.success(`${item.label}: ${settingValueText(item.kind, value, item.unit)}`);
    } catch (error) {
      toast.error("Не сохранено", { description: errorMessage(error) });
    } finally {
      setSavingKey(null);
    }
  }

  function onSave(item: TrendsSettingItem, value: boolean | number | string) {
    if (item.key === AUTOPUBLISH_KEY && value === true) {
      setConfirm(item);
      return;
    }
    void save(item, value);
  }

  async function onStart() {
    try {
      const result = await start.mutateAsync();
      toast.success(`Сбор трендов запущен: прогон #${result.run_id}`);
    } catch (error) {
      toast.error("Сбор не запущен", { description: errorMessage(error) });
    }
  }

  async function onStop() {
    try {
      const result = await cancel.mutateAsync();
      toast.success(result.cancelling ? "Сбору отправлен сигнал остановки" : "Сбор уже не шёл");
    } catch (error) {
      toast.error("Не удалось остановить сбор", { description: errorMessage(error) });
    }
  }

  async function onRefresh(chartsOnly: boolean) {
    try {
      await refresh.mutateAsync({ chartsOnly });
      toast.success(chartsOnly ? "Чарты обновляются в фоне" : "Чарты и разбор обновляются в фоне", {
        description: "Итог появится в подборке и на вкладке «Сейчас».",
      });
    } catch (error) {
      toast.error("Не удалось запустить обновление", { description: errorMessage(error) });
    }
  }

  async function onTick() {
    try {
      const result = await tick.mutateAsync();
      toast.success("Шаг конвейера выполнен", {
        description: result.skipped
          ? `пропущен: ${result.skipped}`
          : `обработано ${result.processed}, изменено ${result.changed}, отправлено ${result.sent}, сбоев ${result.errors}`,
      });
    } catch (error) {
      toast.error("Шаг конвейера не выполнен", { description: errorMessage(error) });
    }
  }

  const items = settings.data ?? [];
  const pipelineEnabled = items.find((item) => item.key === PIPELINE_KEY)?.value === true;
  const busy = start.isPending || cancel.isPending || refresh.isPending || tick.isPending;

  return (
    <div className="flex flex-col gap-4" data-testid="queue-control">
      <SectionCard title="Сделать сейчас">
        {operator ? (
          <div className="flex flex-wrap items-center gap-2">
            {running ? (
              <Button
                variant="destructive"
                size="sm"
                disabled={busy}
                onClick={() => void onStop()}
                data-testid="queue-run-stop"
              >
                <Square className="h-3.5 w-3.5" aria-hidden="true" />
                Остановить сбор
              </Button>
            ) : (
              <Button
                variant="default"
                size="sm"
                disabled={busy || runs.isPending}
                onClick={() => void onStart()}
                data-testid="queue-run-start"
              >
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
                Собрать тренды сейчас
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={busy || popularRunning}
              onClick={() => void onRefresh(false)}
              title="Чарты популярных видео СНГ + разбор нейросетью и по кадрам"
              data-testid="queue-videos-refresh"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", popularRunning && "animate-spin")} aria-hidden="true" />
              {popularRunning ? "Чарты обновляются…" : "Обновить чарты и разбор"}
            </Button>
            <Button variant="ghost" size="sm" disabled={busy || popularRunning} onClick={() => void onRefresh(true)}>
              только чарты
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void onTick()}
              data-testid="queue-pipeline-tick"
            >
              <StepForward className="h-3.5 w-3.5" aria-hidden="true" />
              Прогнать конвейер сейчас
            </Button>
          </div>
        ) : (
          <Callout variant="info">Запускать сбор и конвейер вручную может оператор или владелец.</Callout>
        )}
        <p className="text-xs text-fg-muted">
          {running
            ? `Сбор идёт ${formatElapsed(current?.started_at, now)}: ${current?.phase || "запуск"}.`
            : runs.data?.next_run_at
              ? `Следующий автосбор по расписанию — ${formatDateTime(runs.data.next_run_at, { short: true })}.`
              : runs.data?.schedule_hours === 0
                ? "Автосбор выключен — сбор запускается только отсюда."
                : ""}{" "}
          Сбор идёт час-полтора и не мешает воркерам auto-shorts: Kino-trends работает отдельным процессом.
        </p>
      </SectionCard>

      <SectionCard
        title="Автоматика Kino-trends"
        actions={admin ? undefined : <Lock className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}
      >
        {!admin ? (
          <Callout variant="info" title="Переключать автоматику может только владелец">
            Значения показаны для справки: что система делает сама, а что ждёт человека.
          </Callout>
        ) : null}
        {settings.isError ? (
          <SectionError
            error={settings.error}
            onRetry={() => void settings.refetch()}
            title="Настройки не загрузились"
          />
        ) : settings.isPending ? (
          <div className="flex flex-col gap-2" aria-busy="true">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {SETTING_GROUPS.map((group: SettingGroup) => {
              const rows = items.filter((item) => item.group === group);
              if (rows.length === 0) return null;
              const meta = SETTING_GROUP_LABELS[group];
              const dimmed = !pipelineEnabled && (group === "publish" || group === "pipeline");
              return (
                <section key={group} className="flex flex-col" aria-labelledby={`queue-settings-${group}`}>
                  <h3
                    id={`queue-settings-${group}`}
                    className="font-mono text-2xs tracking-wider text-fg-muted uppercase"
                  >
                    {meta.title}
                  </h3>
                  <p className="mb-1 text-xs text-fg-muted">
                    {meta.hint}
                    {dimmed && group === "publish" ? " Пока конвейер выключен, эти тумблеры не действуют." : ""}
                  </p>
                  {rows.map((item) => (
                    <SettingRow
                      key={item.key}
                      item={item}
                      admin={admin}
                      saving={savingKey === item.key}
                      dimmed={dimmed && item.key !== PIPELINE_KEY}
                      onSave={onSave}
                    />
                  ))}
                </section>
              );
            })}
          </div>
        )}
      </SectionCard>

      <Dialog
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title="Включить автозалив одобренного?"
        description="Одобренные пакеты будут сразу ставиться на автозалив — ролики уйдут на канал без вашего участия."
        footer={
          <>
            <Button variant="outline" size="sm" onClick={() => setConfirm(null)}>
              Отмена
            </Button>
            <Button
              variant="human"
              size="sm"
              onClick={() => {
                const item = confirm;
                setConfirm(null);
                if (item) void save(item, true);
              }}
              data-testid="queue-autopublish-confirm"
            >
              Включить автозалив
            </Button>
          </>
        }
      >
        <p className="text-sm text-fg-muted">
          Проверьте план канала и тумблер «Заливать сразу». Выключить автозалив можно здесь же в любой момент; каждое
          переключение попадает в журнал аудита.
        </p>
      </Dialog>
    </div>
  );
}
