"use client";

import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  BookOpen,
  CircleCheck,
  Download,
  History,
  Layers,
  LoaderCircle,
  RotateCcw,
  Save,
  ShieldCheck,
  TriangleAlert,
  Undo2,
} from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useQueryState } from "nuqs";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { parse, stringify } from "yaml";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  buttonVariants,
  Callout,
  Card,
  Dialog,
  EmptyState,
  FormField,
  Input,
  SectionCard,
  Skeleton,
  StatusBadge,
} from "@/components/ui";
import { ConflictError, errorMessage } from "@/lib/api/errors";
import { canOperate } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatAgo, formatDateTime } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { routeHref } from "@/lib/nav";
import {
  type ConfigIssue,
  configErrorsOf,
  type Niche,
  type NicheConfig,
  nicheExportYamlUrl,
  useNiche,
  useNicheVersion,
  useNicheVersions,
  usePutNicheConfig,
  useUpdateNiche,
  useValidateNiche,
} from "@/lib/queries/niches";

import { CONFIG_SECTIONS, NICHE_STATUS_META, VERSION_SOURCE_LABELS } from "./labels";

// CodeMirror — отдельный чанк, только в браузере (бюджет оболочки, §7.1).
const YamlEditor = dynamic(() => import("./yaml-editor").then((module) => module.YamlEditor), {
  ssr: false,
  loading: () => <Skeleton className="h-[520px] w-full rounded-none" />,
});

/** Несохранённые правки: текст и версия, от которой они начались (она же `expected_version` в PUT). */
interface Draft {
  fromVersion: number;
  text: string;
}

/** Текст, который оператор сохранил сам: оставляем его (с комментариями и порядком) как «чистый» для этой версии. */
interface SavedText {
  version: number;
  text: string;
}

interface ValidationState {
  ok: boolean;
  errors: ConfigIssue[];
  warnings: ConfigIssue[];
  /** Откуда результат: «Проверить» или отказ сервера при сохранении (422). */
  source: "validate" | "save";
}

type ParsedConfig = { ok: true; config: NicheConfig } | { ok: false; message: string };

/** Пустой конфиг → подсказка комментариями (парсится в `null` → `{}`), иначе YAML без id/label (они в строке ниши). */
function configToYaml(config: NicheConfig): string {
  const body = Object.fromEntries(Object.entries(config).filter(([key]) => key !== "id" && key !== "label"));
  if (Object.keys(body).length === 0) {
    return [
      "# Конфиг пуст: все секции берут значения по умолчанию из схемы.",
      "# Раскомментируйте нужное и заполните — подсказки по полям справа.",
      ...CONFIG_SECTIONS.map((section) => `# ${section.key}:   # ${section.title.toLowerCase()}`),
      "",
    ].join("\n");
  }
  return stringify(body, { lineWidth: 100 });
}

/** YAML редактора → объект для API. Комментарии и пустой документ — `{}`; не словарь — ошибка. */
function parseConfig(text: string): ParsedConfig {
  let data: unknown;
  try {
    data = parse(text);
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : "YAML не разбирается" };
  }
  if (data === null || data === undefined) return { ok: true, config: {} };
  if (typeof data !== "object" || Array.isArray(data)) {
    return { ok: false, message: "YAML ниши должен быть словарём верхнего уровня (секция: поля)" };
  }
  return { ok: true, config: data as NicheConfig };
}

/** Редактор ниши (§7.3 `/niches/edit?id=`): YAML слева, проверка, сохранение и история версий справа. */
export function NicheEditorScreen() {
  const [id] = useQueryState("id");
  const niche = useNiche(id);

  if (!id) {
    return (
      <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="niche-editor-screen">
        <PageHeader eyebrow="Настройка · Ниши" title="Редактор ниши" />
        <EmptyState
          icon={Layers}
          title="Ниша не выбрана"
          description="Откройте нишу из списка — редактор работает с конфигом одной ниши."
          action={
            <Link href={routeHref("/niches")} className={buttonVariants({ variant: "outline", size: "sm" })}>
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />К списку ниш
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="niche-editor-screen">
      {niche.isError ? (
        <>
          <PageHeader eyebrow="Настройка · Ниши" title={id} />
          <SectionError error={niche.error} onRetry={() => void niche.refetch()} title="Ниша не загрузилась" />
        </>
      ) : niche.data ? (
        <NicheEditor key={niche.data.id} niche={niche.data} refetch={() => niche.refetch()} />
      ) : (
        <>
          <PageHeader eyebrow="Настройка · Ниши" title={id} />
          <Skeleton className="h-[560px] w-full" />
        </>
      )}
    </div>
  );
}

function NicheEditor({ niche, refetch }: { niche: Niche; refetch: () => Promise<{ data?: Niche }> }) {
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const now = useNow(60_000);
  const put = usePutNicheConfig(niche.id);
  const validate = useValidateNiche(niche.id);
  const update = useUpdateNiche(niche.id);
  const versions = useNicheVersions(niche.id);

  const [draft, setDraft] = useState<Draft | null>(null);
  const [savedText, setSavedText] = useState<SavedText | null>(null);
  const [saving, setSaving] = useState(false);
  const [validation, setValidation] = useState<ValidationState | null>(null);
  const [conflict, setConflict] = useState<{ currentVersion: number | null; message: string } | null>(null);
  const [previewVersion, setPreviewVersion] = useState<number | null>(null);

  // «Чистый» текст: свой сохранённый для текущей версии, иначе — конфиг с сервера. Без правок редактор
  // показывает его и сам подхватывает новую версию, когда она появляется на сервере.
  const serverText = useMemo(() => configToYaml(niche.config), [niche.config]);
  const cleanText = savedText && savedText.version === niche.current_version ? savedText.text : serverText;
  const text = draft ? draft.text : cleanText;
  const dirty = draft !== null && draft.text !== cleanText;
  const baseVersion = draft?.fromVersion ?? niche.current_version;
  const parsed = useMemo(() => parseConfig(text), [text]);
  // Сервер уже отдал другую версию (сохранил кто-то другой), а у нас правки: сами не перезаписываем.
  const remoteChanged = !saving && dirty && baseVersion !== niche.current_version;

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  const onChange = useCallback(
    (value: string) =>
      setDraft((current) => ({ fromVersion: current?.fromVersion ?? niche.current_version, text: value })),
    [niche.current_version],
  );

  const reset = () => {
    setDraft(null);
    setValidation(null);
    setConflict(null);
  };

  const reload = async () => {
    await refetch();
    setDraft(null);
    setSavedText(null);
    setValidation(null);
    setConflict(null);
  };

  const runValidate = async () => {
    if (!parsed.ok) return;
    try {
      const result = await validate.mutateAsync(parsed.config);
      setValidation({ ...result, source: "validate" });
      if (result.ok && result.warnings.length === 0) toast.success("Конфиг в порядке");
    } catch (error) {
      toast.error("Проверка не удалась", { description: errorMessage(error) });
    }
  };

  const canSave = operator && dirty && parsed.ok && !saving && !remoteChanged;

  const save = useCallback(async () => {
    if (!parsed.ok || !draft) return;
    setSaving(true);
    try {
      const saved = await put.mutateAsync({ config: parsed.config, expected_version: draft.fromVersion });
      setSavedText({ version: saved.current_version, text: draft.text });
      setDraft(null);
      setConflict(null);
      setValidation(null);
      toast.success(`Сохранено: версия ${saved.current_version}`);
    } catch (error) {
      if (error instanceof ConflictError) {
        const current = error.details.current_version;
        setConflict({ currentVersion: typeof current === "number" ? current : null, message: error.message });
        void refetch();
        return;
      }
      const issues = configErrorsOf(error);
      if (issues.length) {
        setValidation({ ok: false, errors: issues, warnings: [], source: "save" });
        return;
      }
      toast.error("Не сохранено", { description: errorMessage(error) });
    } finally {
      setSaving(false);
    }
  }, [parsed, draft, put, refetch]);

  // Ctrl/Cmd+S — сохранить (CodeMirror эту комбинацию не перехватывает).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "s") return;
      event.preventDefault();
      if (canSave) void save();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canSave, save]);

  const toggleStatus = async () => {
    const next = niche.status === "archived" ? "active" : "archived";
    try {
      await update.mutateAsync({ status: next });
      toast.success(next === "archived" ? "Ниша в архиве" : "Ниша возвращена из архива");
    } catch (error) {
      toast.error("Не сохранено", { description: errorMessage(error) });
    }
  };

  return (
    <>
      <PageHeader
        eyebrow={
          <Link href={routeHref("/niches")} className="hover:text-fg">
            Настройка · Ниши
          </Link>
        }
        title={niche.label || niche.id}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <code className="font-mono text-xs">{niche.id}</code>
            <StatusBadge status={niche.status} map={NICHE_STATUS_META} />
            <span>
              v{niche.current_version} · обновлено{" "}
              <span title={formatDateTime(niche.updated_at)}>{formatAgo(niche.updated_at, now)}</span>
            </span>
            {niche.used_by_channels.length ? (
              <span title={niche.used_by_channels.join(", ")}>каналов: {niche.used_by_channels.length}</span>
            ) : (
              <span>каналов нет</span>
            )}
          </span>
        }
        actions={
          <>
            <a
              href={nicheExportYamlUrl(niche.id)}
              download={`${niche.id}.yaml`}
              className={buttonVariants({ variant: "outline", size: "sm" })}
              data-testid="niche-export-yaml"
            >
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              Скачать YAML
            </a>
            {operator ? (
              <Button variant="outline" size="sm" disabled={update.isPending} onClick={() => void toggleStatus()}>
                {niche.status === "archived" ? (
                  <ArchiveRestore className="h-3.5 w-3.5" aria-hidden="true" />
                ) : (
                  <Archive className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                {niche.status === "archived" ? "Вернуть" : "В архив"}
              </Button>
            ) : null}
          </>
        }
      />

      {niche.status === "needs_import" ? (
        <Callout variant="warning" title="Конфига ещё нет">
          Ниша появилась из старых каналов без конфига. Импортируйте YAML на экране «Ниши» или заполните конфиг здесь и
          сохраните — он станет версией 1.
        </Callout>
      ) : null}
      {!operator ? (
        <Callout variant="info" title="Только просмотр">
          Проверять и сохранять конфиг может оператор. Скачать YAML можно.
        </Callout>
      ) : null}
      {conflict || remoteChanged ? (
        <Callout
          variant="negative"
          title="Ниша изменена кем-то другим"
          action={
            <Button variant="outline" size="sm" onClick={() => void reload()}>
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              Перезагрузить
            </Button>
          }
        >
          На сервере версия {conflict?.currentVersion ?? niche.current_version}, в редакторе — v{baseVersion}.
          «Перезагрузить» заменит текст серверной версией; свои правки перед этим скопируйте.
        </Callout>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="flex min-w-0 flex-col overflow-hidden">
          <div className="flex h-9 items-center justify-between gap-2 border-b border-border px-3">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-fg-muted">{niche.id}.yaml</span>
              {dirty ? (
                <Badge variant="human">изменено</Badge>
              ) : (
                <Badge variant="neutral" outline>
                  v{baseVersion}
                </Badge>
              )}
            </div>
            <Button variant="ghost" size="xs" disabled={!dirty} onClick={reset} data-testid="niche-reset">
              <Undo2 className="h-3 w-3" aria-hidden="true" />
              Сбросить
            </Button>
          </div>
          <YamlEditor
            value={text}
            onChange={onChange}
            readOnly={!operator}
            height="calc(100vh - 20rem)"
            minHeight="420px"
            ariaLabel={`Конфиг ниши ${niche.id} в YAML`}
          />
          <p className="border-t border-border px-3 py-1.5 text-2xs text-fg-muted">
            Сервер хранит структуру, а не текст: комментарии и порядок ключей после перезагрузки страницы не
            сохраняются. Неизвестный ключ — ошибка проверки.
          </p>
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          <SectionCard
            title="Проверка и сохранение"
            actions={
              operator ? (
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={!parsed.ok || validate.isPending}
                    onClick={() => void runValidate()}
                    data-testid="niche-validate"
                  >
                    {validate.isPending ? (
                      <LoaderCircle className="h-3 w-3 animate-spin" aria-hidden="true" />
                    ) : (
                      <ShieldCheck className="h-3 w-3" aria-hidden="true" />
                    )}
                    Проверить
                  </Button>
                  <Button size="xs" disabled={!canSave} onClick={() => void save()} data-testid="niche-save">
                    {saving ? (
                      <LoaderCircle className="h-3 w-3 animate-spin" aria-hidden="true" />
                    ) : (
                      <Save className="h-3 w-3" aria-hidden="true" />
                    )}
                    Сохранить
                  </Button>
                </div>
              ) : null
            }
          >
            {!parsed.ok ? (
              <Callout variant="negative" title="YAML не разбирается">
                <pre className="whitespace-pre-wrap font-mono text-xs">{parsed.message}</pre>
              </Callout>
            ) : validation ? (
              <ValidationResult result={validation} />
            ) : (
              <p className="text-xs text-fg-muted">
                «Проверить» прогоняет конфиг через схему без сохранения. «Сохранить» создаёт версию{" "}
                {niche.current_version + 1}
                {dirty ? "" : " — пока правок нет"}. Ctrl+S тоже сохраняет.
              </p>
            )}
          </SectionCard>

          {operator ? <RenameForm niche={niche} /> : null}

          <SectionCard title="История версий">
            {versions.isError ? (
              <SectionError error={versions.error} onRetry={() => void versions.refetch()} />
            ) : versions.isPending ? (
              <Skeleton className="h-20" />
            ) : versions.data && versions.data.length ? (
              <ul className="flex flex-col divide-y divide-border">
                {[...versions.data]
                  .sort((a, b) => b.version - a.version)
                  .map((item) => (
                    <li key={item.version}>
                      <button
                        type="button"
                        className="flex w-full items-center gap-2 py-1.5 text-left text-sm hover:text-accent"
                        onClick={() => setPreviewVersion(item.version)}
                        aria-label={`Версия ${item.version}: показать конфиг`}
                      >
                        <History className="h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
                        <span className="font-mono text-xs">v{item.version}</span>
                        <span className="min-w-0 flex-1 truncate text-xs text-fg-muted">
                          {VERSION_SOURCE_LABELS[item.source] ?? item.source}
                          {item.created_by != null ? ` · №${item.created_by}` : ""}
                        </span>
                        <span className="text-2xs text-fg-muted" title={formatDateTime(item.created_at)}>
                          {formatAgo(item.created_at, now)}
                        </span>
                        {item.version === niche.current_version ? <Badge variant="accent">текущая</Badge> : null}
                      </button>
                    </li>
                  ))}
              </ul>
            ) : (
              <p className="text-xs text-fg-muted">Версий ещё нет — появятся после первого сохранения.</p>
            )}
          </SectionCard>

          <SectionCard title="Шпаргалка по конфигу" bodyClassName="gap-1 p-2">
            <p className="px-1 pb-1 text-2xs text-fg-muted">
              Ключи — латиницей, тексты — по-русски. Любая секция необязательна.
            </p>
            {CONFIG_SECTIONS.map((section) => (
              <details key={section.key} className="group rounded-md border border-transparent open:border-border">
                <summary className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-surface-2">
                  <BookOpen className="h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
                  <code className="font-mono text-xs text-accent">{section.key}</code>
                  <span className="min-w-0 flex-1 truncate text-fg-muted">{section.title}</span>
                </summary>
                <div className="flex flex-col gap-1.5 px-2 pt-1 pb-2">
                  <p className="text-xs text-fg-muted">{section.summary}</p>
                  <dl className="flex flex-col gap-1">
                    {section.fields.map((field) => (
                      <div key={field.name} className="flex flex-col">
                        <dt className="font-mono text-xs text-fg">{field.name}</dt>
                        <dd className="text-2xs text-fg-muted">{field.hint}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </details>
            ))}
          </SectionCard>
        </div>
      </div>

      <VersionPreview
        nicheId={niche.id}
        version={previewVersion}
        currentVersion={niche.current_version}
        canRestore={operator}
        onClose={() => setPreviewVersion(null)}
        onRestore={(config) => {
          onChange(configToYaml(config));
          setValidation(null);
          setPreviewVersion(null);
          toast.info("Версия в редакторе", { description: "Проверьте и сохраните — получится новая версия." });
        }}
      />
    </>
  );
}

// ---------------------------------------------------------------- результат проверки

function ValidationResult({ result }: { result: ValidationState }) {
  if (result.ok && result.warnings.length === 0) {
    return (
      <p className="flex items-center gap-1.5 text-sm text-positive">
        <CircleCheck className="h-4 w-4" aria-hidden="true" />
        Конфиг в порядке
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {result.errors.length ? (
        <div className="flex flex-col gap-1">
          <p className="flex items-center gap-1.5 text-sm font-medium text-negative">
            <TriangleAlert className="h-4 w-4" aria-hidden="true" />
            {result.source === "save" ? "Сервер отклонил сохранение" : "Ошибки"}: {result.errors.length}
          </p>
          <IssueList issues={result.errors} tone="negative" />
        </div>
      ) : null}
      {result.warnings.length ? (
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-warning">Предупреждения: {result.warnings.length}</p>
          <IssueList issues={result.warnings} tone="warning" />
        </div>
      ) : null}
      {result.ok && result.errors.length === 0 ? (
        <p className="text-xs text-fg-muted">Схема принимает конфиг; предупреждения сохранению не мешают.</p>
      ) : null}
    </div>
  );
}

function IssueList({ issues, tone }: { issues: ConfigIssue[]; tone: "negative" | "warning" }) {
  return (
    <ul className="flex flex-col gap-1">
      {issues.map((issue, index) => (
        <li
          key={`${issue.path}-${index}`}
          className={cn(
            "flex flex-col rounded-md border px-2 py-1",
            tone === "negative" ? "border-negative/40 bg-negative/10" : "border-warning/40 bg-warning/10",
          )}
        >
          <code className="font-mono text-xs text-fg">{issue.path}</code>
          <span className="text-xs text-fg-muted">{issue.msg}</span>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- подпись ниши

function RenameForm({ niche }: { niche: Niche }) {
  const update = useUpdateNiche(niche.id);
  // null — поле показывает подпись с сервера (и само подхватывает её смену), строка — свои правки.
  const [draftLabel, setDraftLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const label = draftLabel ?? niche.label;
  const changed = label.trim() !== niche.label && label.trim().length > 0;

  const submit = async () => {
    if (!changed) return;
    setError(null);
    try {
      await update.mutateAsync({ label: label.trim() });
      setDraftLabel(null);
      toast.success("Подпись обновлена");
    } catch (cause) {
      setError(errorMessage(cause));
    }
  };

  return (
    <SectionCard title="Ниша">
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <FormField
          label="Подпись"
          htmlFor="niche-label"
          error={error ?? undefined}
          hint="Отображается в списках и формах канала; в конфиг не входит."
        >
          <div className="flex gap-2">
            <Input
              id="niche-label"
              value={label}
              maxLength={100}
              onChange={(event) => setDraftLabel(event.target.value)}
              className="min-w-0 flex-1"
            />
            <Button type="submit" variant="outline" disabled={!changed || update.isPending}>
              Переименовать
            </Button>
          </div>
        </FormField>
      </form>
    </SectionCard>
  );
}

// ---------------------------------------------------------------- просмотр версии

function VersionPreview({
  nicheId,
  version,
  currentVersion,
  canRestore,
  onClose,
  onRestore,
}: {
  nicheId: string;
  version: number | null;
  currentVersion: number;
  canRestore: boolean;
  onClose: () => void;
  onRestore: (config: NicheConfig) => void;
}) {
  const query = useNicheVersion(nicheId, version);
  const text = useMemo(() => (query.data ? configToYaml(query.data.config) : ""), [query.data]);
  return (
    <Dialog
      open={version !== null}
      onClose={onClose}
      title={version !== null ? `Версия ${version}${version === currentVersion ? " (текущая)" : ""}` : "Версия"}
      description="Конфиг версии только для чтения. «Откатить в редактор» подставит его в редактор; сохранение создаст новую версию."
      className="max-w-3xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Закрыть
          </Button>
          {canRestore && query.data ? (
            <Button variant="human" onClick={() => onRestore(query.data.config)} data-testid="niche-restore-version">
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              Откатить в редактор
            </Button>
          ) : null}
        </>
      }
    >
      {query.isError ? (
        <SectionError error={query.error} onRetry={() => void query.refetch()} title="Версия не загрузилась" />
      ) : query.isPending ? (
        <Skeleton className="h-80 w-full" />
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <YamlEditor value={text} readOnly height="60vh" minHeight="240px" ariaLabel={`Конфиг версии ${version}`} />
        </div>
      )}
    </Dialog>
  );
}
