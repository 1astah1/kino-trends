"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Archive, ArchiveRestore, FilePenLine, Layers, Plus, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { type MouseEvent, useId, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import * as z from "zod/mini";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import { applyServerErrors, SLUG_RE } from "@/components/products/form-utils";
import {
  Button,
  Callout,
  DataTable,
  type DataTableColumn,
  Dialog,
  FormField,
  Input,
  PillToggle,
  Select,
  StatusBadge,
} from "@/components/ui";
import { errorMessage, isApiError } from "@/lib/api/errors";
import { canOperate } from "@/lib/auth";
import { formatDateTime, formatAgo, formatNumber } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { routeHref } from "@/lib/nav";
import {
  configErrorsOf,
  type NicheImportMode,
  type NicheRow,
  type NicheStatus,
  useCreateNiche,
  useImportNicheYaml,
  useNiches,
  useUpdateNiche,
} from "@/lib/queries/niches";

import { NICHE_STATUS_META, NICHE_STATUS_OPTIONS } from "./labels";

const STATUS_FILTERS = ["all", ...NICHE_STATUS_OPTIONS.map((o) => o.value)] as ["all", ...NicheStatus[]];

/** Ниши (§7.3 `/niches?status=`): список, версии конфигов, создание, импорт YAML, архив. */
export function NichesScreen() {
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const router = useRouter();
  const now = useNow(60_000);
  const [status, setStatus] = useQueryState(
    "status",
    parseAsStringLiteral(STATUS_FILTERS).withDefault("all").withOptions({ history: "replace" }),
  );
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);

  const niches = useNiches({ status: status === "all" ? null : status });
  const items = niches.data?.items ?? [];
  const openEditor = (id: string) => router.push(routeHref("/niches/edit", { id }));

  const columns: Array<DataTableColumn<NicheRow>> = [
    {
      key: "niche",
      header: "Ниша",
      render: (n) => (
        <span className="flex flex-col">
          <span className="font-medium">{n.label || n.id}</span>
          <span className="font-mono text-2xs text-fg-muted">{n.id}</span>
        </span>
      ),
    },
    { key: "status", header: "Статус", render: (n) => <StatusBadge status={n.status} map={NICHE_STATUS_META} /> },
    {
      key: "version",
      header: "Версия",
      className: "text-right",
      render: (n) => (
        <span className="tabular font-mono text-xs">{n.current_version > 0 ? `v${n.current_version}` : "—"}</span>
      ),
    },
    {
      key: "updated",
      header: "Обновлено",
      render: (n) => (
        <span className="text-xs text-fg-muted" title={formatDateTime(n.updated_at)}>
          {formatAgo(n.updated_at, now)}
        </span>
      ),
    },
    {
      key: "channels",
      header: "Каналы",
      className: "text-right",
      render: (n) =>
        n.used_by_channels.length ? (
          <span className="tabular font-mono text-xs" title={n.used_by_channels.join(", ")}>
            {formatNumber(n.used_by_channels.length)}
          </span>
        ) : (
          <span className="text-fg-muted">—</span>
        ),
    },
    {
      key: "actions",
      header: <span className="sr-only">Действия</span>,
      className: "text-right",
      render: (n) => (
        <NicheRowActions
          niche={n}
          operator={operator}
          onEdit={() => openEditor(n.id)}
          onImport={() => setImporting(true)}
        />
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="niches-screen">
      <PageHeader
        eyebrow="Настройка"
        title="Ниши"
        description="Конфиг ниши прошивает все стадии ролика: сценарий, визуал, голос, субтитры, музыку, длительность, обложку и хештеги. Каждое сохранение — новая версия, старые доступны для отката."
        actions={
          operator ? (
            <>
              <Button variant="outline" onClick={() => setImporting(true)} data-testid="import-niche">
                <Upload className="h-4 w-4" aria-hidden="true" />
                Импорт YAML
              </Button>
              <Button onClick={() => setCreating(true)} data-testid="create-niche">
                <Plus className="h-4 w-4" aria-hidden="true" />
                Ниша
              </Button>
            </>
          ) : null
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <PillToggle
          label="Статус ниш"
          options={[{ value: "all", label: "Все" }, ...NICHE_STATUS_OPTIONS]}
          value={status}
          onChange={(value) => void setStatus(value)}
        />
        <span className="ml-auto text-xs text-fg-muted">
          {niches.data ? `${formatNumber(niches.data.total)} ниш` : null}
        </span>
      </div>

      {niches.isError ? (
        <SectionError error={niches.error} onRetry={() => void niches.refetch()} />
      ) : (
        <DataTable
          caption="Ниши"
          columns={columns}
          rows={items}
          rowKey={(n) => n.id}
          onRowClick={(n) => openEditor(n.id)}
          isLoading={niches.isPending}
          emptyTitle={status === "all" ? "Ниш нет" : "В этом статусе ниш нет"}
          emptyDescription="Создайте нишу с пустым конфигом или клоном существующей либо импортируйте YAML из config/niches."
          minWidth={760}
        />
      )}
      <p className="flex items-center gap-1.5 text-2xs text-fg-muted">
        <Layers className="h-3 w-3" aria-hidden="true" />
        «Нужен импорт» — ниша появилась из старых каналов без конфига: импортируйте YAML или заполните конфиг в
        редакторе.
      </p>

      <Dialog
        open={creating}
        onClose={() => setCreating(false)}
        title="Новая ниша"
        description="id — латиницей, навсегда: по нему связаны каналы и задания. Конфиг можно скопировать с существующей ниши."
      >
        {creating ? (
          <CreateNicheForm
            niches={items}
            onCancel={() => setCreating(false)}
            onDone={(id) => {
              setCreating(false);
              openEditor(id);
            }}
          />
        ) : null}
      </Dialog>
      <Dialog
        open={importing}
        onClose={() => setImporting(false)}
        title="Импорт YAML"
        description="Файл формы config/niches/*.yaml, UTF-8, до 256 КБ. id берётся из файла."
      >
        {importing ? (
          <ImportYamlForm
            onCancel={() => setImporting(false)}
            onDone={(id) => {
              setImporting(false);
              openEditor(id);
            }}
          />
        ) : null}
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------- действия строки

function NicheRowActions({
  niche,
  operator,
  onEdit,
  onImport,
}: {
  niche: NicheRow;
  operator: boolean;
  onEdit: () => void;
  onImport: () => void;
}) {
  const update = useUpdateNiche(niche.id);
  const archived = niche.status === "archived";

  const toggleStatus = async (event: MouseEvent) => {
    event.stopPropagation();
    const next: NicheStatus = archived ? "active" : "archived";
    try {
      await update.mutateAsync({ status: next });
      toast.success(next === "archived" ? `Ниша ${niche.id} в архиве` : `Ниша ${niche.id} возвращена`);
    } catch (error) {
      toast.error("Не сохранено", { description: errorMessage(error) });
    }
  };

  return (
    <span className="inline-flex items-center justify-end gap-1">
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={`Открыть редактор ${niche.id}`}
        title="Редактор"
        onClick={(event) => {
          event.stopPropagation();
          onEdit();
        }}
      >
        <FilePenLine className="h-3.5 w-3.5" aria-hidden="true" />
      </Button>
      {operator && niche.status === "needs_import" ? (
        <Button
          variant="outline"
          size="xs"
          onClick={(event) => {
            event.stopPropagation();
            onImport();
          }}
        >
          <Upload className="h-3 w-3" aria-hidden="true" />
          Импорт
        </Button>
      ) : null}
      {operator ? (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={archived ? `Вернуть нишу ${niche.id}` : `Архивировать нишу ${niche.id}`}
          title={archived ? "Вернуть из архива" : "В архив"}
          disabled={update.isPending}
          onClick={(event) => void toggleStatus(event)}
        >
          {archived ? (
            <ArchiveRestore className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <Archive className="h-3.5 w-3.5" aria-hidden="true" />
          )}
        </Button>
      ) : null}
    </span>
  );
}

// ---------------------------------------------------------------- создание

const createSchema = z.object({
  id: z.string().check(z.trim()),
  label: z.string().check(z.trim(), z.minLength(1, "Укажите подпись"), z.maxLength(100, "Не длиннее 100 символов")),
  clone_from: z.string(),
});
type CreateValues = z.infer<typeof createSchema>;
const CREATE_FIELDS = ["id", "label", "clone_from"] as const;

function CreateNicheForm({
  niches,
  onDone,
  onCancel,
}: {
  niches: NicheRow[];
  onDone: (id: string) => void;
  onCancel: () => void;
}) {
  const create = useCreateNiche();
  const form = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: { id: "", label: "", clone_from: "" },
  });
  const { errors, isSubmitting } = form.formState;
  const sources = niches.filter((n) => n.status !== "needs_import");

  const onSubmit = form.handleSubmit(async (values) => {
    if (!SLUG_RE.test(values.id)) {
      form.setError("id", { type: "client", message: "2–40 символов: a–z, 0–9, «-», «_»; с буквы или цифры" });
      return;
    }
    try {
      const niche = await create.mutateAsync({
        id: values.id,
        label: values.label,
        clone_from: values.clone_from || null,
        ...(values.clone_from ? {} : { config: {} }),
      });
      toast.success(`Ниша ${niche.id} создана`, { description: `Версия ${niche.current_version}` });
      onDone(niche.id);
    } catch (error) {
      applyServerErrors(error, form.setError, CREATE_FIELDS);
    }
  });

  return (
    <form className="flex flex-col gap-3" onSubmit={(event) => void onSubmit(event)} noValidate>
      <FormField label="id" htmlFor="niche-id" error={errors.id?.message} hint="Латиницей: taro, astro, kino_trends">
        <Input
          id="niche-id"
          autoFocus
          autoComplete="off"
          spellCheck={false}
          className="font-mono"
          {...form.register("id")}
        />
      </FormField>
      <FormField label="Подпись" htmlFor="niche-label" error={errors.label?.message}>
        <Input id="niche-label" placeholder="Таро — расклад дня" {...form.register("label")} />
      </FormField>
      <FormField
        label="Конфиг"
        htmlFor="niche-clone"
        error={errors.clone_from?.message}
        hint="Пустой конфиг — значения по умолчанию из схемы; клон копирует текущую версию образца."
      >
        <Select id="niche-clone" {...form.register("clone_from")}>
          <option value="">Пустой конфиг</option>
          {sources.map((n) => (
            <option key={n.id} value={n.id}>
              Клон: {n.label} ({n.id})
            </option>
          ))}
        </Select>
      </FormField>
      {errors.root?.server ? (
        <p role="alert" className="text-xs text-negative">
          {errors.root.server.message}
        </p>
      ) : null}
      <div className="flex items-center justify-end gap-2 pt-1">
        <Button type="button" variant="ghost" onClick={onCancel}>
          Отмена
        </Button>
        <Button type="submit" disabled={isSubmitting} data-testid="create-niche-submit">
          Создать
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- импорт YAML

function ImportYamlForm({ onDone, onCancel }: { onDone: (id: string) => void; onCancel: () => void }) {
  const importYaml = useImportNicheYaml();
  const fileId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<NicheImportMode>("create");
  const [error, setError] = useState<{ message: string; issues: Array<{ path: string; msg: string }> } | null>(null);

  const submit = async () => {
    if (!file) {
      setError({ message: "Выберите файл .yaml", issues: [] });
      return;
    }
    setError(null);
    try {
      const niche = await importYaml.mutateAsync({ file, mode });
      toast.success(`Ниша ${niche.id} импортирована`, { description: `Версия ${niche.current_version}` });
      onDone(niche.id);
    } catch (cause) {
      const fieldError = isApiError(cause) ? cause.fieldErrors.file : undefined;
      setError({ message: fieldError ?? errorMessage(cause), issues: configErrorsOf(cause) });
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <FormField
        label="Файл YAML"
        htmlFor={fileId}
        hint={file ? `${file.name} · ${Math.ceil(file.size / 1024)} КБ` : undefined}
      >
        <input
          id={fileId}
          type="file"
          accept=".yaml,.yml,text/yaml,application/x-yaml,application/yaml"
          className="block w-full text-sm text-fg file:mr-3 file:h-7 file:cursor-pointer file:rounded-md file:border file:border-border file:bg-surface-2 file:px-2.5 file:text-xs file:font-medium file:text-fg hover:file:bg-surface"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setError(null);
          }}
        />
      </FormField>
      <FormField
        label="Режим"
        hint={
          mode === "create"
            ? "Только новая ниша (или заготовка «нужен импорт»). Если такой id уже есть — ошибка."
            : "Новая версия конфига у существующей ниши; история сохраняется."
        }
      >
        <PillToggle
          label="Режим импорта"
          options={[
            { value: "create", label: "Создать" },
            { value: "replace", label: "Заменить" },
          ]}
          value={mode}
          onChange={setMode}
        />
      </FormField>
      {error ? (
        <Callout variant="negative" title="Импорт не прошёл">
          <p>{error.message}</p>
          {error.issues.length ? (
            <ul className="mt-1 flex flex-col gap-0.5">
              {error.issues.map((issue, index) => (
                <li key={`${issue.path}-${index}`} className="flex gap-2">
                  <code className="font-mono text-xs text-fg">{issue.path}</code>
                  <span>{issue.msg}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </Callout>
      ) : null}
      <div className="flex items-center justify-end gap-2 pt-1">
        <Button type="button" variant="ghost" onClick={onCancel}>
          Отмена
        </Button>
        <Button
          type="button"
          disabled={importYaml.isPending || !file}
          onClick={() => void submit()}
          data-testid="import-niche-submit"
        >
          <Upload className="h-4 w-4" aria-hidden="true" />
          Импортировать
        </Button>
      </div>
    </div>
  );
}
