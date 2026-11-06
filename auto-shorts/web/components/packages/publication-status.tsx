"use client";

import { CloudOff, ExternalLink, RefreshCw, Trash } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button, Dialog, StatusBadge } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import type { Schemas } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { useDeletePublication, useRemoveRemotePublication, useReverifyPublication } from "@/lib/queries/publications";

import { PUBLICATION_STATUS_META, VERIFY_HINTS, VERIFY_STATUS_META } from "./labels";

export interface PublicationLike {
  id: number;
  url: string | null;
  status: Schemas["PublicationStatus"];
  verify_status: Schemas["VerifyStatus"];
  verify_detail?: string | null;
  published_at: string | null;
}

/** Внешняя ссылка на ролик площадки: только http(s), открывается в новой вкладке без referrer. */
export function safeExternalHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  return /^https?:\/\/[^\s/]+\//i.test(url) ? url : undefined;
}

/**
 * Состояние публикации: статус, проверка принадлежности каналу и что с ней делать. Повторная
 * проверка — оператор; удаление ошибочной регистрации (ролик → approved, пакет → ready) — владелец.
 */
export function PublicationStatusBlock({
  publication,
  operator,
  admin,
  compact = false,
}: {
  publication: PublicationLike;
  operator: boolean;
  admin: boolean;
  compact?: boolean;
}) {
  const reverify = useReverifyPublication();
  const remove = useDeletePublication();
  const removeRemote = useRemoveRemotePublication();
  const [confirmRemote, setConfirmRemote] = useState(false);
  const onPlatform =
    publication.status === "live" || publication.status === "unlisted" || publication.status === "private";
  const href = safeExternalHref(publication.url);
  const hint = VERIFY_HINTS[publication.verify_status];
  const problem = publication.verify_status !== "ok" && publication.verify_status !== "pending";

  return (
    <div className="flex flex-col gap-2" data-testid="publication-status" data-verify={publication.verify_status}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span data-testid="publication-state">
          <StatusBadge status={publication.status} map={PUBLICATION_STATUS_META} />
        </span>
        <StatusBadge status={publication.verify_status} map={VERIFY_STATUS_META} />
        {publication.published_at ? (
          <span className="text-xs text-fg-muted">{formatDateTime(publication.published_at, { short: true })}</span>
        ) : null}
      </div>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex min-w-0 items-center gap-1 self-start font-mono text-xs text-accent hover:underline"
        >
          <span className="truncate">{publication.url}</span>
          <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
        </a>
      ) : null}
      {publication.verify_detail ? <p className="text-xs text-fg-muted">{publication.verify_detail}</p> : null}
      {!compact && hint && publication.verify_status !== "ok" ? (
        <p className={problem ? "text-xs text-warning" : "text-xs text-fg-muted"}>{hint}</p>
      ) : null}
      {(operator && publication.verify_status !== "ok") || admin ? (
        <div className="flex flex-wrap gap-2">
          {operator && publication.verify_status !== "ok" ? (
            <Button
              variant="outline"
              size="sm"
              disabled={reverify.isPending}
              onClick={() =>
                reverify.mutate(publication.id, { onSuccess: () => toast.success("Проверка принадлежности запущена") })
              }
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              Проверить снова
            </Button>
          ) : null}
          {operator && onPlatform ? (
            <Button
              type="button"
              size="xs"
              variant="outline"
              disabled={removeRemote.isPending}
              onClick={() => setConfirmRemote(true)}
              data-testid="publication-remove-remote"
            >
              <CloudOff className="size-3.5" aria-hidden="true" />
              Снять с YouTube
            </Button>
          ) : null}
          {admin ? (
            <Button
              variant="destructive"
              size="sm"
              disabled={remove.isPending}
              onClick={() => {
                if (
                  !window.confirm(
                    "Удалить ошибочную регистрацию? Ролик вернётся в «Одобрен», пакет — в «Готов к заливке», алиас площадки выключится. Сам ролик на площадке не трогаем.",
                  )
                )
                  return;
                remove.mutate(publication.id, {
                  onSuccess: () => toast.success("Регистрация удалена — можно вставить правильную ссылку"),
                });
              }}
            >
              <Trash className="h-3.5 w-3.5" aria-hidden="true" />
              Удалить регистрацию
            </Button>
          ) : null}
        </div>
      ) : null}
      <Dialog
        open={confirmRemote}
        onClose={() => setConfirmRemote(false)}
        title="Снять ролик с YouTube?"
        description="Ролик будет удалён на площадке без возможности восстановить, просмотры и комментарии пропадут. Запись о публикации останется со статусом «удалён нами»."
        footer={
          <>
            <Button type="button" variant="ghost" onClick={() => setConfirmRemote(false)}>
              Отмена
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={removeRemote.isPending}
              onClick={() =>
                removeRemote.mutate(publication.id, {
                  onSuccess: () => {
                    setConfirmRemote(false);
                    toast.success("Ролик удалён с YouTube");
                  },
                  onError: (e) => toast.error("Не снято", { description: errorMessage(e) }),
                })
              }
            >
              Удалить на YouTube
            </Button>
          </>
        }
      >
        <p className="text-sm text-fg-muted">
          Нужно подключение канала с правом управлять роликами (youtube.force-ssl). Если подключение старое, сервер
          ответит подсказкой переподключить канал.
        </p>
      </Dialog>
    </div>
  );
}
