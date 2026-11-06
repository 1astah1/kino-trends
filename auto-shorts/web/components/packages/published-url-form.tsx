"use client";

import { CloudUpload, Info, Link2, TriangleAlert } from "lucide-react";
import { type FormEvent, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Callout, FormField, Input } from "@/components/ui";
import { isApiError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format";
import { normalizeVkOwner, parseVideoUrl, PLATFORM_LABELS, VIDEO_KIND_LABELS } from "@/lib/platform-url";
import type { UploadPackage } from "@/lib/queries/packages";
import { useRegisterPublication } from "@/lib/queries/publications";

import { fromMskInput, toMskInput } from "./package-utils";
import { publishErrorMessage } from "./publish-errors";

function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return `pub-${crypto.randomUUID()}`;
  return `pub-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/**
 * Поле «ссылка на опубликованный ролик» (§7.3): живой разбор `lib/platform-url.ts` (площадка, вид,
 * ID, алиас атрибуции) и подсказки до отправки; регистрацию решает сервер — его ошибки показываем
 * по-русски. Повтор той же ссылки идёт с тем же `Idempotency-Key`.
 */
export function PublishedUrlForm({ pkg, disabled }: { pkg: UploadPackage; disabled?: boolean }) {
  const [url, setUrl] = useState("");
  const [publishedAt, setPublishedAt] = useState(() => toMskInput(pkg.marked_uploaded_at));
  const [touched, setTouched] = useState(false);
  const attempt = useRef<{ url: string; key: string } | null>(null);
  const register = useRegisterPublication();

  const trimmed = url.trim();
  const parsed = useMemo(() => (trimmed ? parseVideoUrl(trimmed) : null), [trimmed]);
  const channelPlatform = pkg.channel.platform;

  const warnings: string[] = [];
  if (parsed?.ok) {
    const value = parsed.value;
    if (value.platform !== channelPlatform) {
      warnings.push(
        `Ссылка на ${PLATFORM_LABELS[value.platform]}, а канал «${pkg.channel.display_name}» — на ${PLATFORM_LABELS[channelPlatform]}. Сервер такую регистрацию отклонит.`,
      );
    } else if (value.platform === "vk") {
      const expected = normalizeVkOwner(pkg.channel.platform_channel_ref);
      if (expected && value.owner_ref && expected !== value.owner_ref) {
        warnings.push(
          `Клип принадлежит владельцу ${value.owner_ref}, а у канала в карточке — ${expected}. Проверьте, в какое сообщество залили.`,
        );
      }
    } else if (value.platform_kind !== "short") {
      warnings.push(
        "Это ссылка на обычное видео, а не на Shorts. Если ролик вертикальный и до 3 минут — всё в порядке.",
      );
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (!trimmed || register.isPending) return;
    if (!attempt.current || attempt.current.url !== trimmed) {
      attempt.current = { url: trimmed, key: newIdempotencyKey() };
    }
    register.mutate(
      {
        packageId: pkg.id,
        url: trimmed,
        publishedAt: fromMskInput(publishedAt),
        idempotencyKey: attempt.current.key,
      },
      {
        onSuccess: (created) => {
          const verify = created.publication.verify_status;
          toast.success("Публикация зарегистрирована", {
            description:
              verify === "ok"
                ? "Канал подтверждён по ссылке."
                : verify === "pending"
                  ? "Принадлежность каналу проверяется в фоне."
                  : "Проверьте вкладку «Проблемы».",
          });
          setUrl("");
          attempt.current = null;
        },
      },
    );
  }

  const error = register.error;
  const fieldId = `published-url-${pkg.id}`;
  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-3 rounded-lg border border-human/40 bg-human/5 p-3"
      data-testid="published-url-form"
      noValidate
    >
      <div className="flex items-center gap-2 text-sm font-medium text-fg">
        <Link2 className="h-4 w-4 text-human" aria-hidden="true" />
        Ссылка на опубликованный ролик
        {pkg.marked_uploaded_at ? (
          <Badge variant="human" outline className="ml-auto">
            залит {formatDateTime(pkg.marked_uploaded_at, { short: true })}
          </Badge>
        ) : null}
      </div>
      <FormField
        label={`Вставьте ссылку из ${channelPlatform === "youtube" ? "YouTube Studio или «Поделиться»" : "VK Видео или «Поделиться»"}`}
        htmlFor={fieldId}
      >
        <Input
          id={fieldId}
          type="url"
          inputMode="url"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder={channelPlatform === "youtube" ? "youtube.com/shorts/…" : "vk.com/clip-…"}
          value={url}
          onChange={(event) => {
            setUrl(event.target.value);
            if (register.error) register.reset();
          }}
          onBlur={() => setTouched(true)}
          disabled={disabled}
          aria-invalid={parsed !== null && !parsed.ok && touched ? true : undefined}
          aria-describedby={`${fieldId}-preview`}
          data-testid="published-url-input"
        />
      </FormField>

      <div id={`${fieldId}-preview`} aria-live="polite" className="flex min-h-5 flex-col gap-1.5 text-xs">
        {parsed?.ok ? (
          <div className="flex flex-wrap items-center gap-1.5" data-testid="published-url-preview">
            <Badge variant={parsed.value.platform === channelPlatform ? "positive" : "negative"}>
              {PLATFORM_LABELS[parsed.value.platform]}
            </Badge>
            <Badge variant="neutral" outline>
              {VIDEO_KIND_LABELS[parsed.value.platform_kind]}
            </Badge>
            <span className="text-fg-muted">ID</span>
            <span className="font-mono text-fg">{parsed.value.platform_video_id}</span>
            <span className="text-fg-muted">· алиас</span>
            <span className="font-mono text-fg" data-testid="published-url-alias">
              {parsed.value.alias_code}
            </span>
          </div>
        ) : parsed && !parsed.ok ? (
          <p className={touched ? "text-warning" : "text-fg-muted"} data-testid="published-url-parse-error">
            {parsed.message}. Сервер проверит ссылку при регистрации.
          </p>
        ) : (
          <p className="text-fg-muted">Площадка, ID ролика и алиас атрибуции появятся здесь сразу после вставки.</p>
        )}
        {warnings.map((text) => (
          <p key={text} className="flex items-start gap-1 text-warning">
            <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
            {text}
          </p>
        ))}
      </div>

      <FormField
        label="Когда опубликован (МСК)"
        htmlFor={`${fieldId}-at`}
        hint={
          pkg.marked_uploaded_at
            ? "Подставлено время отметки «залил». Пусто — сейчас."
            : "Необязательно: пусто — сейчас. Важно для метрик по возрасту ролика."
        }
      >
        <Input
          id={`${fieldId}-at`}
          type="datetime-local"
          value={publishedAt}
          max={toMskInput(new Date().toISOString())}
          onChange={(event) => setPublishedAt(event.target.value)}
          disabled={disabled}
          className="sm:max-w-60"
        />
      </FormField>

      {error ? (
        <Callout variant="negative" title="Не зарегистрировано" className="py-2">
          <span data-testid="publish-error">{publishErrorMessage(error)}</span>
          {isApiError(error) ? <span className="ml-1 font-mono text-2xs">({error.code})</span> : null}
        </Callout>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="submit"
          variant="human"
          disabled={disabled || !trimmed || register.isPending}
          data-testid="register-publication"
        >
          <CloudUpload className="h-4 w-4" aria-hidden="true" />
          {register.isPending ? "Регистрирую…" : "Зарегистрировать публикацию"}
        </Button>
        <span className="flex items-center gap-1 text-2xs text-fg-muted">
          <Info className="h-3 w-3" aria-hidden="true" />
          Ролик и пакет станут «Опубликован», код получит алиас площадки.
        </span>
      </div>
    </form>
  );
}
