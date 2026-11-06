"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CloudUpload,
  Download,
  FileArchive,
  FileText,
  Info,
  RefreshCw,
  Share2,
  ShieldAlert,
  UserRound,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { Badge, Button, Callout, SectionCard, Skeleton, StatusBadge } from "@/components/ui";
import { isApiMediaUrl } from "@/lib/api/media";
import { formatDateTime, formatMb, formatPct } from "@/lib/format";
import {
  refreshAfterDownload,
  type UploadPackage,
  useMarkUploaded,
  usePackage,
  useRebuildPackage,
} from "@/lib/queries/packages";

import { AutopublishBlock } from "./autopublish-block";
import { CopyRow, useCopy } from "./copy";
import { ORIGINALITY_VERDICT_META, PACKAGE_STATUS_META, TEXTS_MODE_META } from "./labels";
import { PackageChecklist } from "./package-checklist";
import { PublicationStatusBlock } from "./publication-status";
import { PublishedUrlForm } from "./published-url-form";
import { publishErrorMessage } from "./publish-errors";
import { canShareFiles, canWebShare, fetchPackageFile, shareNow } from "./share";
import { COPY_LABELS, COPY_ORDER, type CopyKey, SaveIndicator, TextsEditor } from "./texts-editor";
import { usePackageDraft } from "./use-package-draft";

const KIND_LABELS: Record<UploadPackage["kind"], string> = { generate: "Генерация", clip: "Нарезка" };

function formatDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return "—";
  const total = Math.round(sec);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/**
 * Карточка пакета (§7.3, §7.5 PackageCard): файл, тексты с копированием (клавиши 1–6),
 * серверный чек-лист, атрибуция и итог оригинальности для нарезки, ссылка на опубликованный ролик.
 * Пакет опубликован или заменён — только чтение.
 */
export function PackageDetail({
  packageId,
  initial,
  operator,
  admin,
  onBack,
  onOpenPackage,
}: {
  packageId: number;
  initial?: UploadPackage;
  operator: boolean;
  admin: boolean;
  onBack: () => void;
  onOpenPackage: (id: number) => void;
}) {
  const query = usePackage(packageId, initial);
  const [revision, setRevision] = useState(0);
  const pkg = query.data;

  if (query.isError && !pkg) {
    return (
      <div className="flex flex-col gap-3">
        <BackButton onBack={onBack} />
        <SectionError error={query.error} onRetry={() => void query.refetch()} title="Пакет не загрузился" />
      </div>
    );
  }
  if (!pkg) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-16" />
        <Skeleton className="h-72" />
      </div>
    );
  }
  return (
    <PackageCard
      key={`${pkg.id}:${revision}`}
      pkg={pkg}
      operator={operator}
      admin={admin}
      onBack={onBack}
      onRebuilt={(next) => {
        if (next.id !== pkg.id) onOpenPackage(next.id);
        else setRevision((value) => value + 1);
      }}
    />
  );
}

function BackButton({ onBack }: { onBack: () => void }) {
  return (
    <button
      type="button"
      onClick={onBack}
      className="inline-flex items-center gap-1 self-start text-xs text-fg-muted hover:text-fg md:hidden"
    >
      <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />К списку пакетов
    </button>
  );
}

function PackageCard({
  pkg,
  operator,
  admin,
  onBack,
  onRebuilt,
}: {
  pkg: UploadPackage;
  operator: boolean;
  admin: boolean;
  onBack: () => void;
  onRebuilt: (next: UploadPackage) => void;
}) {
  const queryClient = useQueryClient();
  const live = pkg.status === "ready" || pkg.status === "downloaded";
  const editable = operator && live;
  const draft = usePackageDraft(pkg, editable);
  const { copied, copy } = useCopy();
  const markUploaded = useMarkUploaded(pkg.id);
  const rebuild = useRebuildPackage(pkg.job_id);

  const { normalized } = draft;
  const values = useMemo<Record<CopyKey, string>>(() => {
    const hashtags = normalized.hashtags.join(" ");
    return {
      title: normalized.title,
      // Хэштеги — в конец описания, если оператор ещё не вписал их туда сам.
      description: normalized.hashtags.every((tag) => normalized.description.includes(tag))
        ? normalized.description.trimEnd()
        : [normalized.description.trimEnd(), hashtags].filter(Boolean).join("\n\n"),
      hashtags,
      pinned: (normalized.pinned ?? "").trim(),
      link: pkg.tracking.redirect_url,
      code: pkg.tracking.code,
    };
  }, [normalized, pkg.tracking.redirect_url, pkg.tracking.code]);
  const copyKey = useCallback((key: CopyKey) => void copy(key, values[key], COPY_LABELS[key]), [copy, values]);

  useHotkeys(
    COPY_ORDER.map((_, index) => String(index + 1)),
    (event) => {
      const key = COPY_ORDER[Number(event.key) - 1];
      if (key) copyKey(key);
    },
    { preventDefault: true },
    [copyKey],
  );

  const videoHref = isApiMediaUrl(pkg.file.download_url) ? pkg.file.download_url : undefined;
  const textHref = isApiMediaUrl(pkg.file.text_url) ? pkg.file.text_url : undefined;
  const zipHref = isApiMediaUrl(pkg.file.zip_url) ? pkg.file.zip_url : undefined;
  const onDownloaded = () => refreshAfterDownload(queryClient, pkg.id);
  const blocked = Object.keys(draft.errors).length > 0;

  return (
    <div className="flex flex-col gap-4" data-testid="package-detail" data-package-id={pkg.id} data-status={pkg.status}>
      <BackButton onBack={onBack} />

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-mono text-base font-semibold text-fg" data-testid="package-uid">
            {pkg.video_uid}
          </h2>
          <span data-testid="package-status">
            <StatusBadge status={pkg.status} map={PACKAGE_STATUS_META} />
          </span>
          <Badge variant="neutral" outline>
            {KIND_LABELS[pkg.kind] ?? pkg.kind}
          </Badge>
          <span className="font-mono text-xs text-fg-muted">код {pkg.tracking.code}</span>
        </div>
        <div className="flex items-start gap-2 rounded-md border border-human/40 bg-human/10 px-3 py-2 text-sm">
          <UserRound className="mt-0.5 h-4 w-4 shrink-0 text-human" aria-hidden="true" />
          <div className="flex min-w-0 flex-col">
            <span className="text-xs text-fg-muted">Зайдите в аккаунт · {pkg.channel.display_name}</span>
            <span className="font-medium text-fg" data-testid="account-hint">
              {pkg.channel.account_hint}
            </span>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2" data-testid="package-actions">
        {videoHref ? (
          <a
            href={videoHref}
            download={pkg.file.filename}
            onClick={onDownloaded}
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-accent px-3 text-sm font-medium text-accent-fg hover:bg-accent/90"
            data-testid="download-video"
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            Скачать видео
            <span className="tabular font-mono text-xs opacity-80">
              {formatMb(pkg.file.size_bytes / 1048576)} · {formatDuration(pkg.file.duration_sec)}
            </span>
          </a>
        ) : null}
        {textHref ? (
          <a
            href={textHref}
            download
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-sm hover:bg-surface-2"
          >
            <FileText className="h-4 w-4" aria-hidden="true" />
            Тексты .txt
          </a>
        ) : null}
        {zipHref ? (
          <a
            href={zipHref}
            download
            onClick={onDownloaded}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-sm hover:bg-surface-2"
          >
            <FileArchive className="h-4 w-4" aria-hidden="true" />
            ZIP
          </a>
        ) : null}
        <ShareControl pkg={pkg} title={values.title} text={values.description} onFetched={onDownloaded} />
        {operator && live && !pkg.marked_uploaded_at ? (
          <Button
            variant="outline"
            disabled={markUploaded.isPending}
            onClick={() =>
              markUploaded.mutate(undefined, { onSuccess: () => toast.success("Отмечено: залит, ссылка позже") })
            }
            data-testid="mark-uploaded"
          >
            <CloudUpload className="h-4 w-4" aria-hidden="true" />
            Залил, ссылку вставлю позже
          </Button>
        ) : null}
        {operator && live ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={rebuild.isPending}
            onClick={() => {
              if (
                !window.confirm(
                  "Пересобрать тексты заново (LLM или шаблон)? Ваши правки заголовка, описания и хэштегов пропадут.",
                )
              )
                return;
              rebuild.mutate(
                { regenerate_texts: true },
                {
                  onSuccess: (next) => {
                    toast.success("Тексты пересобраны", { description: next.texts.texts_label });
                    onRebuilt(next);
                  },
                  onError: (error) => toast.error("Пакет не пересобран", { description: publishErrorMessage(error) }),
                },
              );
            }}
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            {rebuild.isPending ? "Пересобираю…" : "Пересобрать тексты"}
          </Button>
        ) : null}
      </div>

      {pkg.texts.texts_mode === "template" ? (
        <Callout variant="warning" title={pkg.texts.texts_label}>
          LLM не использовался (нет ключа, сбой или пауза бюджета) — тексты собраны шаблоном ниши. Прочитайте их перед
          заливкой.
        </Callout>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(320px,400px)]">
        <div className="flex min-w-0 flex-col gap-4">
          <SectionCard
            title="Тексты"
            actions={
              <span className="flex items-center gap-2">
                <Badge variant={TEXTS_MODE_META[pkg.texts.texts_mode].variant} data-testid="texts-label">
                  {pkg.texts.texts_label}
                </Badge>
                {editable ? <SaveIndicator state={draft.state} blocked={blocked} /> : null}
              </span>
            }
          >
            <TextsEditor
              pkg={pkg}
              draft={draft.draft}
              errors={draft.errors}
              editable={editable}
              onField={draft.setField}
              onBlur={draft.flush}
              copied={copied}
              onCopy={copyKey}
            />
            {draft.state.kind === "error" ? <p className="text-xs text-negative">{draft.state.message}</p> : null}
          </SectionCard>

          {pkg.texts.attribution_text || pkg.originality ? <SourceCard pkg={pkg} copied={copied} copy={copy} /> : null}

          <SectionCard title="Ссылки и код">
            <CopyRow
              label="Ссылка из ролика"
              value={values.link}
              hotkey="5"
              copied={copied === "link"}
              onCopy={() => copyKey("link")}
              testId="copy-link"
            />
            <CopyRow
              label="Код ролика"
              value={values.code}
              hotkey="6"
              copied={copied === "code"}
              onCopy={() => copyKey("code")}
              testId="copy-code"
            />
            <CopyRow
              label="Telegram"
              value={pkg.tracking.telegram_https_url}
              copied={copied === "tg"}
              onCopy={() => void copy("tg", pkg.tracking.telegram_https_url ?? "", "Ссылка в Telegram")}
            />
            <CopyRow
              label="Сайт (UTM)"
              value={pkg.tracking.site_url}
              copied={copied === "site"}
              onCopy={() => void copy("site", pkg.tracking.site_url ?? "", "Ссылка на сайт")}
            />
            <p className="text-2xs text-fg-muted">
              В закрепе и описании — «ссылка в шапке канала». В VK ссылку можно ставить в кликабельные места сообщества.
            </p>
          </SectionCard>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          {pkg.channel.platform === "youtube" && (pkg.autopublish || live) ? (
            <AutopublishBlock pkg={pkg} operator={operator} />
          ) : null}

          <SectionCard title="Публикация">
            {pkg.publication ? (
              <PublicationStatusBlock publication={pkg.publication} operator={operator} admin={admin} />
            ) : live && operator ? (
              <PublishedUrlForm pkg={pkg} />
            ) : live ? (
              <p className="text-sm text-fg-muted">Ссылку вставляет оператор или владелец.</p>
            ) : (
              <p className="text-sm text-fg-muted">Пакет заменён новым — публикуйте актуальный пакет ролика.</p>
            )}
          </SectionCard>

          <SectionCard title="Чек-лист">
            <PackageChecklist pkg={pkg} editable={operator && pkg.status !== "superseded"} />
          </SectionCard>

          {pkg.texts.related_video_hint || pkg.texts.audio_note ? (
            <SectionCard title="Подсказки к заливке">
              {pkg.texts.related_video_hint ? <Note text={pkg.texts.related_video_hint} /> : null}
              {pkg.texts.audio_note ? <Note text={pkg.texts.audio_note} /> : null}
            </SectionCard>
          ) : null}

          <p className="text-2xs text-fg-muted">
            Собран {formatDateTime(pkg.created_at, { short: true })}
            {pkg.downloaded_at ? ` · скачан ${formatDateTime(pkg.downloaded_at, { short: true })}` : ""}
            {pkg.marked_uploaded_at ? ` · залит ${formatDateTime(pkg.marked_uploaded_at, { short: true })}` : ""}
          </p>
        </div>
      </div>
    </div>
  );
}

function Note({ text }: { text: string }) {
  return (
    <p className="flex items-start gap-1.5 text-sm text-fg">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
      {text}
    </p>
  );
}

/** Нарезка: атрибуция чужого источника и итог отчёта об оригинальности на момент сборки. */
function SourceCard({
  pkg,
  copied,
  copy,
}: {
  pkg: UploadPackage;
  copied: string | null;
  copy: (key: string, text: string, label: string) => Promise<void>;
}) {
  const originality = pkg.originality;
  return (
    <SectionCard title="Источник и оригинальность">
      {pkg.texts.attribution_text ? (
        <div className="flex flex-col gap-1" data-testid="attribution-block">
          <CopyRow
            label="Атрибуция"
            value={pkg.texts.attribution_text}
            mono={false}
            copied={copied === "attribution"}
            onCopy={() => void copy("attribution", pkg.texts.attribution_text ?? "", "Атрибуция")}
          />
          <p className="text-xs text-fg-muted">
            Чужие права (CC BY, лицензия): строка уже вставлена в описание и должна остаться там — без неё сервер не
            сохранит правку.
          </p>
        </div>
      ) : null}
      {originality ? (
        <div className="flex flex-col gap-1.5" data-testid="originality-block">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={originality.verdict} map={ORIGINALITY_VERDICT_META} />
            <span className="text-xs text-fg-muted">
              доля автора <span className="tabular font-mono text-fg">{formatPct(originality.author_share * 100)}</span>
            </span>
            {originality.gate_applies ? (
              <Badge variant={originality.gate_passed ? "positive" : "negative"} outline>
                {originality.gate_passed ? "гейт пройден" : "гейт не пройден"}
              </Badge>
            ) : (
              <Badge variant="neutral" outline>
                свой источник — гейт не применяется
              </Badge>
            )}
          </div>
          {originality.gate_applies && !originality.gate_passed ? (
            <p className="flex items-start gap-1 text-xs text-negative">
              <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Чужой источник с проваленным отчётом не заливаем: доработайте авторский слой.
            </p>
          ) : null}
          {originality.suggestions.length ? (
            <ul className="list-disc pl-5 text-xs text-fg-muted">
              {originality.suggestions.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </SectionCard>
  );
}

type ShareState =
  { kind: "idle" } | { kind: "loading" } | { kind: "ready"; file: File } | { kind: "error"; message: string };

/**
 * «Поделиться» на телефоне: файл ролика в приложение площадки (если браузер умеет файлы),
 * иначе — заголовок и описание текстом. Кнопки нет, где Web Share недоступен (десктоп).
 */
function ShareControl({
  pkg,
  title,
  text,
  onFetched,
}: {
  pkg: UploadPackage;
  title: string;
  text: string;
  onFetched: () => void;
}) {
  const [supported] = useState(() =>
    typeof window === "undefined" ? { share: false, files: false } : { share: canWebShare(), files: canShareFiles() },
  );
  const [state, setState] = useState<ShareState>({ kind: "idle" });
  if (!supported.share) return null;

  async function prepare() {
    setState({ kind: "loading" });
    try {
      const file = await fetchPackageFile(pkg.file.download_url, pkg.file.filename);
      onFetched();
      setState({ kind: "ready", file });
    } catch (error) {
      setState({ kind: "error", message: error instanceof Error ? error.message : "Файл не скачался" });
    }
  }

  async function share(file: File | null) {
    try {
      const sent = await shareNow({ title, text, file });
      if (sent) toast.success(file ? "Файл отправлен в приложение" : "Тексты отправлены");
    } catch (error) {
      toast.error("Не удалось поделиться", { description: error instanceof Error ? error.message : undefined });
    }
  }

  if (!supported.files) {
    return (
      <Button variant="outline" onClick={() => void share(null)}>
        <Share2 className="h-4 w-4" aria-hidden="true" />
        Поделиться текстом
      </Button>
    );
  }
  if (state.kind === "ready") {
    return (
      <Button variant="human" onClick={() => void share(state.file)} data-testid="share-file">
        <Share2 className="h-4 w-4" aria-hidden="true" />
        Поделиться файлом
      </Button>
    );
  }
  return (
    <Button
      variant="outline"
      disabled={state.kind === "loading"}
      onClick={() => void prepare()}
      title={state.kind === "error" ? state.message : "Скачать файл в память телефона, затем отправить в приложение"}
    >
      <Share2 className="h-4 w-4" aria-hidden="true" />
      {state.kind === "loading" ? "Готовлю файл…" : state.kind === "error" ? "Повторить" : "Поделиться (файл)"}
    </Button>
  );
}
