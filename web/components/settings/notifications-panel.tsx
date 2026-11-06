"use client";

import { Bot, KeyRound, Lock, MessageSquare } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { SectionError } from "@/components/common/section-error";
import { Badge, Button, EmptyState, FormField, Input, SectionCard, Skeleton } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { isAdmin } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { useDeleteSecret, useSecrets, useWriteSecret } from "@/lib/queries/providers";
import { useSettingsList } from "@/lib/queries/settings";

import { type SettingFieldSpec, SettingsGroupForm } from "./settings-group-form";

/** Имя секрета с токеном бота (docs/ARCHITECTURE.md §6.7). */
const BOT_TOKEN_SECRET = "TELEGRAM_BOT_TOKEN";
/** Форма токена BotFather: `<id бота>:<35 символов>`; проверка мягкая, вставку не блокирует. */
const TOKEN_RE = /^\d{6,12}:[A-Za-z0-9_-]{30,}$/;
/** Числовой id чата (супергруппы начинаются с -100…) либо @username публичного канала. */
const CHAT_ID_RE = /^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$/;
const PROXY_RE = /^(https?|socks5h?|socks4a?):\/\/\S+$/i;

/** Группа `notifications` (src/autoshorts/domain/settings_schema.py). */
const NOTIFICATION_FIELDS: readonly SettingFieldSpec[] = [
  {
    key: "review_chat_id",
    label: "Чат приёмки",
    kind: "text",
    nullable: true,
    placeholder: "-1001234567890",
    pattern: CHAT_ID_RE,
    patternHint: "Числовой id чата (у супергрупп начинается с -100) или @username канала",
    hint: "сюда приходят ролики на согласование",
  },
  {
    key: "alert_chat_id",
    label: "Чат алертов",
    kind: "text",
    nullable: true,
    placeholder: "-1001234567890",
    pattern: CHAT_ID_RE,
    patternHint: "Числовой id чата (у супергрупп начинается с -100) или @username канала",
    hint: "диск, бюджет, сбои публикации, воркер без связи",
  },
  {
    key: "telegram_proxy_url",
    label: "Прокси для Telegram",
    kind: "text",
    nullable: true,
    placeholder: "socks5://user:pass@host:1080",
    pattern: PROXY_RE,
    patternHint: "Адрес прокси со схемой: socks5://host:1080 или http-прокси",
    hint: "если Bot API с бокса недоступен напрямую",
  },
];

/** Настройки → Уведомления (§7.3): чаты приёмки и алертов, прокси, токен бота. Только admin. */
export function NotificationsPanel() {
  const user = useCurrentUser();
  if (!isAdmin(user.role))
    return (
      <EmptyState
        icon={Lock}
        title="Раздел доступен владельцу"
        description="Чаты Telegram и токен бота меняет только роль «Владелец»."
      />
    );
  return <NotificationsPanelBody />;
}

function NotificationsPanelBody() {
  const settings = useSettingsList();
  const items = settings.data?.filter((item) => item.group === "notifications") ?? [];

  return (
    <div className="flex flex-col gap-4" data-testid="notifications-panel">
      <div>
        <h2 className="text-base font-semibold">Уведомления</h2>
        <p className="text-xs text-fg-muted">
          Один бот Telegram шлёт ролики на приёмку и алерты. Токен хранится как секрет и обратно не показывается; чаты —
          обычные настройки.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex flex-col gap-4">
          <SectionCard
            title="Чаты Telegram"
            actions={<MessageSquare className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}
          >
            {settings.isError ? (
              <SectionError error={settings.error} onRetry={() => void settings.refetch()} />
            ) : settings.isPending ? (
              <Skeleton className="h-48" />
            ) : (
              <SettingsGroupForm
                idPrefix="notifications"
                items={items}
                fields={NOTIFICATION_FIELDS}
                admin
                successMessage="Настройки уведомлений сохранены"
              />
            )}
          </SectionCard>
          <BotTokenCard />
        </div>
        <SectionCard title="Как подключить" actions={<Bot className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}>
          <ol className="flex list-decimal flex-col gap-2 pl-4 text-sm">
            <li>
              В Telegram откройте <span className="font-mono">@BotFather</span>, команда{" "}
              <span className="font-mono">/newbot</span>: имя и username бота. Полученный токен вставьте в поле «Токен
              бота» ниже и сохраните в секрет.
            </li>
            <li>
              Создайте два чата (или один общий): для приёмки роликов и для алертов. Добавьте бота участником; в канале
              — администратором с правом писать сообщения.
            </li>
            <li>
              Узнайте <span className="font-mono">chat_id</span>: напишите в чате любое сообщение, затем в чате с ботом
              вызовите <span className="font-mono">@userinfobot</span> или <span className="font-mono">@getidsbot</span>
              , переслав туда это сообщение, — он покажет id чата. У групп id отрицательный, у супергрупп начинается с{" "}
              <span className="font-mono">-100</span>. Для публичного канала достаточно{" "}
              <span className="font-mono">@username</span>.
            </li>
            <li>
              Впишите id в «Чат приёмки» и «Чат алертов», сохраните. Если Bot API с бокса не отвечает — укажите прокси.
            </li>
          </ol>
          <p className="text-2xs text-fg-muted">
            Проверка: следующий ролик со статусом «на приёмке» придёт в чат приёмки карточкой с кнопками.
          </p>
        </SectionCard>
      </div>
    </div>
  );
}

/** Токен бота: статус секрета, запись нового значения, удаление из БД (хуки — lib/queries/providers.ts). */
function BotTokenCard() {
  const secrets = useSecrets();
  const write = useWriteSecret();
  const remove = useDeleteSecret();
  const [value, setValue] = useState("");
  const secret = secrets.data?.find((item) => item.name === BOT_TOKEN_SECRET);
  const trimmed = value.trim();
  const looksWrong = trimmed !== "" && !TOKEN_RE.test(trimmed);

  const save = async () => {
    if (!trimmed) return;
    try {
      await write.mutateAsync({ name: BOT_TOKEN_SECRET, value: trimmed });
      setValue("");
      toast.success("Токен бота записан", { description: "Значение больше не показывается." });
    } catch (error) {
      toast.error("Токен не записан", { description: errorMessage(error) });
    }
  };

  return (
    <SectionCard title="Токен бота" actions={<KeyRound className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />}>
      {secrets.isError ? (
        <SectionError error={secrets.error} onRetry={() => void secrets.refetch()} title="Секреты недоступны" />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-mono text-fg-muted">{BOT_TOKEN_SECRET}</span>
            {secrets.isPending ? (
              <Skeleton className="h-5 w-24" />
            ) : secret?.is_set ? (
              <Badge variant="positive">
                задан{secret.last4 ? ` · …${secret.last4}` : ""}
                {secret.source === "env" ? " · из env" : ""}
              </Badge>
            ) : (
              <Badge variant="warning">не задан</Badge>
            )}
            {secret?.updated_at ? (
              <span className="text-fg-muted">обновлён {formatDateTime(secret.updated_at, { short: true })}</span>
            ) : null}
            {secret?.used_by.length ? (
              <span className="text-fg-muted">используют: {secret.used_by.join(", ")}</span>
            ) : null}
          </div>
          <FormField
            label={secret?.is_set ? "Новое значение токена" : "Токен от BotFather"}
            htmlFor="telegram-bot-token"
            hint={
              secret?.source === "env"
                ? "Сейчас токен берётся из переменной окружения; запись в БД перекроет его."
                : "Только запись: сервер вернёт лишь последние 4 символа."
            }
            error={
              looksWrong
                ? "Не похоже на токен BotFather (вид 123456789:AAH…). Можно сохранить, если уверены."
                : undefined
            }
          >
            <Input
              id="telegram-bot-token"
              type="password"
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
              placeholder="123456789:AAH…"
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </FormField>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={!trimmed || write.isPending} onClick={() => void save()}>
              <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
              Сохранить в секрет
            </Button>
            {secret?.is_set && secret.source === "db" ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={remove.isPending}
                onClick={() => {
                  if (window.confirm("Удалить токен бота из БД? Приёмка и алерты в Telegram остановятся."))
                    remove.mutate(BOT_TOKEN_SECRET, { onSuccess: () => toast.success("Токен удалён") });
                }}
              >
                Удалить
              </Button>
            ) : null}
          </div>
        </>
      )}
    </SectionCard>
  );
}
