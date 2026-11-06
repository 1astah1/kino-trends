"use client";

import { Calculator, Cpu, HardDrive, Lock, MemoryStick, RefreshCw, Server, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  Callout,
  DataTable,
  type DataTableColumn,
  Dialog,
  EmptyState,
  SectionCard,
  Skeleton,
  SkeletonStatGrid,
  StatTile,
  StatTileGrid,
} from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { isAdmin } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatGb, formatMb, formatNumber, formatTime } from "@/lib/format";
import {
  type CleanupPreviewItem,
  cleanupCategoryLabel,
  isNotImplemented,
  useCleanupPreview,
  useRunCleanup,
} from "@/lib/queries/maintenance";
import { type SettingItem, useSettingsList } from "@/lib/queries/settings";
import { type SystemStatus, useSystemStatus } from "@/lib/queries/system";
import { stageLabel } from "@/lib/status-meta";

import { type SettingFieldSpec, SettingsGroupForm } from "./settings-group-form";

const RESOURCE_PROFILE_OPTIONS = [
  { value: "shared_dev", label: "Общий бокс (shared_dev)" },
  { value: "dedicated_small", label: "Отдельный малый сервер (dedicated_small)" },
] as const;

/** Группа `worker` (src/autoshorts/domain/settings_schema.py): порядок как в UI-схеме. */
const WORKER_FIELDS: readonly SettingFieldSpec[] = [
  { key: "resource_profile", label: "Профиль ресурсов", kind: "select", options: RESOURCE_PROFILE_OPTIONS },
  { key: "disk_soft_min_gb", label: "Мягкий порог диска", kind: "number", unit: "ГБ", positive: true },
  { key: "disk_hard_min_gb", label: "Жёсткий порог диска", kind: "number", unit: "ГБ", positive: true },
  { key: "disk_emergency_gb", label: "Аварийный порог диска", kind: "number", unit: "ГБ", positive: true },
  {
    key: "media_max_gb",
    label: "Квота медиа",
    kind: "number",
    unit: "ГБ",
    positive: true,
    nullable: true,
    placeholder: "без квоты",
  },
  { key: "heavy_max_load1", label: "Максимальный load1", kind: "number", positive: true },
  { key: "heavy_min_mem_available_mb", label: "Минимум свободной памяти", kind: "integer", unit: "МБ", min: 0 },
  { key: "heavy_max_psi_mem10", label: "Максимум PSI memory avg10", kind: "number", min: 0 },
  { key: "heavy_max_psi_cpu10", label: "Максимум PSI cpu avg10", kind: "number", min: 0 },
];

/** Группа `retention`: сроки хранения, по которым работает уборка. */
const RETENTION_FIELDS: readonly SettingFieldSpec[] = [
  { key: "proxy_retain_days", label: "Прокси источников", kind: "integer", unit: "дн", min: 1 },
  { key: "original_retain_days", label: "Оригиналы загрузок", kind: "integer", unit: "дн", min: 1 },
  { key: "work_artifacts_retain_days", label: "Рабочие файлы ролика", kind: "integer", unit: "дн", min: 1 },
  { key: "rejected_jobs_retain_days", label: "Медиа отклонённых роликов", kind: "integer", unit: "дн", min: 1 },
];

function settingNumber(items: SettingItem[] | undefined, key: string): number | null {
  const value = items?.find((item) => item.key === key)?.value;
  return typeof value === "number" ? value : null;
}

/** Байты → «12 МБ» / «1,2 ГБ»; меньше мегабайта — в КБ. */
function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${formatNumber(bytes / 1024)} КБ`;
  return formatMb(bytes / (1024 * 1024));
}

function LimitBadge({ ok, label }: { ok: boolean | null; label: string }) {
  if (ok === null) return null;
  return <Badge variant={ok ? "positive" : "warning"}>{ok ? label : `выше порога ${label}`}</Badge>;
}

/** Настройки → Воркер (§7.3): текущее состояние против порогов, профиль, диск, хранение, уборка. Только admin. */
export function WorkerPanel() {
  const user = useCurrentUser();
  if (!isAdmin(user.role))
    return (
      <EmptyState
        icon={Lock}
        title="Раздел доступен владельцу"
        description="Пороги допуска тяжёлых задач, профиль ресурсов и очистку медиа меняет только роль «Владелец». Текущее состояние системы — на вкладке «Система»."
      />
    );
  return <WorkerPanelBody />;
}

function WorkerPanelBody() {
  const status = useSystemStatus({ slowWhenLive: 15_000 });
  const settings = useSettingsList();
  const items = settings.data;
  const workerItems = items?.filter((item) => item.group === "worker") ?? [];
  const retentionItems = items?.filter((item) => item.group === "retention") ?? [];

  return (
    <div className="flex flex-col gap-4" data-testid="worker-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">Воркер</h2>
          <p className="text-xs text-fg-muted">
            Тяжёлый слот всегда один: ffmpeg и локальный ASR не делят CPU бокса. Тяжёлая задача стартует, только когда
            нагрузка, память, PSI и диск ниже порогов ниже.
            {status.dataUpdatedAt ? ` Обновлено ${formatTime(status.dataUpdatedAt)}.` : ""}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void status.refetch()}>
          <RefreshCw className={cn("h-3.5 w-3.5", status.isFetching && "animate-spin")} aria-hidden="true" />
          Обновить
        </Button>
      </div>

      {status.isError ? (
        <SectionError error={status.error} onRetry={() => void status.refetch()} title="Нет ответа /system/status" />
      ) : status.data ? (
        <StateTiles status={status.data} settings={items} />
      ) : (
        <SkeletonStatGrid count={8} className="grid-cols-2 sm:grid-cols-4" />
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <SectionCard
          title="Допуск тяжёлых задач и диск"
          actions={<Cpu className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}
        >
          {settings.isError ? (
            <SectionError error={settings.error} onRetry={() => void settings.refetch()} />
          ) : settings.isPending ? (
            <Skeleton className="h-64" />
          ) : (
            <SettingsGroupForm
              idPrefix="worker"
              items={workerItems}
              fields={WORKER_FIELDS}
              admin
              successMessage="Пороги воркера сохранены"
            />
          )}
        </SectionCard>

        <div className="flex flex-col gap-4">
          <SectionCard
            title="Хранение"
            actions={<HardDrive className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}
          >
            <p className="text-xs text-fg-muted">
              Сроки, после которых уборка удаляет файлы. Финальные ролики и постеры опубликованных не трогаются.
            </p>
            {settings.isError ? (
              <SectionError error={settings.error} onRetry={() => void settings.refetch()} />
            ) : settings.isPending ? (
              <Skeleton className="h-40" />
            ) : (
              <SettingsGroupForm
                idPrefix="retention"
                items={retentionItems}
                fields={RETENTION_FIELDS}
                admin
                successMessage="Сроки хранения сохранены"
              />
            )}
          </SectionCard>
          <CleanupCard />
        </div>
      </div>
    </div>
  );
}

function StateTiles({ status, settings }: { status: SystemStatus; settings: SettingItem[] | undefined }) {
  const slot = status.heavy_slot;
  const maxLoad = settingNumber(settings, "heavy_max_load1");
  const minMem = settingNumber(settings, "heavy_min_mem_available_mb");
  const maxPsiCpu = settingNumber(settings, "heavy_max_psi_cpu10");
  const maxPsiMem = settingNumber(settings, "heavy_max_psi_mem10");
  const { free_gb: free, thresholds } = status.disk;
  const diskLevel =
    free <= thresholds.emergency_gb ? 3 : free <= thresholds.hard_gb ? 2 : free <= thresholds.soft_gb ? 1 : 0;
  const diskBadge =
    diskLevel === 0 ? (
      <Badge variant="positive">в норме</Badge>
    ) : (
      <Badge variant={diskLevel === 1 ? "warning" : "negative"}>
        {diskLevel === 1 ? "ниже мягкого" : diskLevel === 2 ? "ниже жёсткого" : "аварийно"}
      </Badge>
    );

  return (
    <StatTileGrid className="grid-cols-2 sm:grid-cols-4">
      <StatTile
        label="Тяжёлый слот"
        icon={Cpu}
        value={slot.task_id !== null ? <span className="font-mono">#{slot.task_id}</span> : "свободен"}
        badge={slot.task_id !== null ? <Badge variant="machine">занят</Badge> : <Badge variant="positive">готов</Badge>}
        hint={
          slot.task_id !== null
            ? `${stageLabel(slot.stage)} · ${formatNumber(slot.pct, 0)}% · ${slot.owner ?? "—"}`
            : "ждёт тяжёлую задачу"
        }
      />
      <StatTile
        label="Очередь тяжёлых"
        icon={Server}
        value={formatNumber(status.queue.heavy.queued)}
        badge={
          status.queue.heavy.deferred ? (
            <Badge variant="warning">отложено {formatNumber(status.queue.heavy.deferred)}</Badge>
          ) : null
        }
        hint={`выполняется ${formatNumber(status.queue.heavy.running)}`}
      />
      <StatTile
        label="Очередь лёгких"
        icon={Server}
        value={formatNumber(status.queue.light.queued)}
        badge={
          status.queue.light.deferred ? (
            <Badge variant="warning">отложено {formatNumber(status.queue.light.deferred)}</Badge>
          ) : null
        }
        hint={`выполняется ${formatNumber(status.queue.light.running)}`}
      />
      <StatTile
        label="Диск свободно"
        icon={HardDrive}
        value={formatGb(free)}
        badge={diskBadge}
        hint={`пороги ${formatGb(thresholds.soft_gb)} · ${formatGb(thresholds.hard_gb)} · ${formatGb(thresholds.emergency_gb)}`}
      />
      <StatTile
        label="load1"
        value={formatNumber(status.load1, 2)}
        badge={
          <LimitBadge ok={maxLoad === null ? null : status.load1 <= maxLoad} label={`≤ ${formatNumber(maxLoad, 1)}`} />
        }
        hint="средняя нагрузка за минуту"
      />
      <StatTile
        label="MemAvailable"
        icon={MemoryStick}
        value={formatMb(status.mem_available_mb)}
        badge={
          minMem === null ? null : status.mem_available_mb >= minMem ? (
            <Badge variant="positive">≥ {formatMb(minMem)}</Badge>
          ) : (
            <Badge variant="warning">ниже порога {formatMb(minMem)}</Badge>
          )
        }
        hint="доступно без свопа"
      />
      <StatTile
        label="PSI cpu avg10"
        value={formatNumber(status.psi.cpu10, 2)}
        badge={
          <LimitBadge
            ok={maxPsiCpu === null || status.psi.cpu10 === null ? null : status.psi.cpu10 <= maxPsiCpu}
            label={`≤ ${formatNumber(maxPsiCpu, 1)}`}
          />
        }
        hint="доля времени в ожидании CPU"
      />
      <StatTile
        label="PSI memory avg10"
        value={formatNumber(status.psi.mem10, 2)}
        badge={
          <LimitBadge
            ok={maxPsiMem === null || status.psi.mem10 === null ? null : status.psi.mem10 <= maxPsiMem}
            label={`≤ ${formatNumber(maxPsiMem, 1)}`}
          />
        }
        hint="доля времени в ожидании памяти"
      />
    </StatTileGrid>
  );
}

function CleanupCard() {
  const preview = useCleanupPreview();
  const run = useRunCleanup();
  const [unsupported, setUnsupported] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const rows = preview.data?.items ?? [];
  const totalFiles = rows.reduce((sum, item) => sum + item.files, 0);
  const totalBytes = rows.reduce((sum, item) => sum + item.bytes, 0);

  const count = async () => {
    try {
      await preview.mutateAsync();
      setUnsupported(false);
    } catch (error) {
      if (isNotImplemented(error)) setUnsupported(true);
      else toast.error("Не удалось оценить уборку", { description: errorMessage(error) });
    }
  };

  const launch = async () => {
    try {
      const accepted = await run.mutateAsync();
      setConfirmOpen(false);
      preview.reset();
      toast.success(`Уборка запущена · задача #${accepted.task_id}`, {
        description: "Ход виден в задачах и на вкладке «Система».",
      });
    } catch (error) {
      setConfirmOpen(false);
      if (isNotImplemented(error)) setUnsupported(true);
      else toast.error("Уборка не запущена", { description: errorMessage(error) });
    }
  };

  const columns: Array<DataTableColumn<CleanupPreviewItem>> = [
    {
      key: "category",
      header: "Категория",
      render: (item) => (
        <span className="flex flex-col">
          <span>{cleanupCategoryLabel(item.category)}</span>
          <span className="font-mono text-2xs text-fg-muted">{item.category}</span>
        </span>
      ),
    },
    {
      key: "files",
      header: "Файлов",
      className: "text-right",
      render: (item) => <span className="font-mono">{formatNumber(item.files)}</span>,
    },
    {
      key: "bytes",
      header: "Объём",
      className: "text-right",
      render: (item) => <span className="font-mono">{formatBytes(item.bytes)}</span>,
    },
  ];

  return (
    <SectionCard title="Очистка медиа" actions={<Trash2 className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}>
      <p className="text-xs text-fg-muted">
        Удаляет по срокам хранения: рабочие файлы роликов, медиа отклонённых, прокси, старые оригиналы, недокачанные
        загрузки и временные файлы. Сначала посчитайте объём — оценка ничего не удаляет.
      </p>
      {unsupported ? (
        <Callout variant="warning" title="Сервер пока не поддерживает очистку">
          Эндпоинты обслуживания отвечают 501: задача уборки ещё не реализована. Сроки хранения при этом сохраняются и
          начнут действовать, как только уборка появится.
        </Callout>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" disabled={preview.isPending || unsupported} onClick={() => void count()}>
          <Calculator className={cn("h-3.5 w-3.5", preview.isPending && "animate-pulse")} aria-hidden="true" />
          Посчитать
        </Button>
        <Button
          variant="destructive"
          size="sm"
          disabled={run.isPending || unsupported}
          onClick={() => setConfirmOpen(true)}
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          Очистить…
        </Button>
        {preview.data ? (
          <span className="text-xs text-fg-muted">
            К удалению: <span className="font-mono text-fg">{formatNumber(totalFiles)}</span> файлов ·{" "}
            <span className="font-mono text-fg">{formatBytes(totalBytes)}</span>
          </span>
        ) : null}
      </div>
      {preview.data ? (
        <DataTable
          caption="Оценка уборки по категориям"
          columns={columns}
          rows={rows}
          rowKey={(item) => item.category}
          emptyTitle="Удалять нечего"
          emptyDescription="Все файлы моложе сроков хранения."
          minWidth={420}
        />
      ) : null}
      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Запустить уборку?"
        description="Файлы удаляются безвозвратно по текущим срокам хранения. Опубликованные финалы и постеры не затрагиваются."
        footer={
          <>
            <Button variant="outline" size="sm" onClick={() => setConfirmOpen(false)} disabled={run.isPending}>
              Отмена
            </Button>
            <Button variant="destructive" size="sm" onClick={() => void launch()} disabled={run.isPending}>
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              Очистить
            </Button>
          </>
        }
      >
        {preview.data ? (
          <p className="text-sm">
            По оценке уйдёт <span className="font-mono">{formatNumber(totalFiles)}</span> файлов,{" "}
            <span className="font-mono">{formatBytes(totalBytes)}</span>.
          </p>
        ) : (
          <p className="text-sm text-fg-muted">Объём не посчитан — уборка пройдёт по срокам хранения как есть.</p>
        )}
      </Dialog>
    </SectionCard>
  );
}
