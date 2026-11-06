"use client";

import { ExternalLink, LoaderCircle, PlugZap, RefreshCw, Save, Unplug } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { CopyRow, useCopy } from "@/components/packages/copy";
import { Badge, Button, Callout, DetailGrid, KeyValue, SectionCard, Skeleton, Textarea } from "@/components/ui";
import { errorMessage, isApiError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format";
import {
  type YoutubeConnection,
  useDisconnectYoutube,
  useStartYoutubeOAuth,
  useWriteYoutubeClient,
  useYoutubeConnection,
} from "@/lib/queries/channels";

import { PlanSection } from "./youtube-plan";

/** Куда смотреть настройку клиента: из ответа 409 `oauth_client_missing` или из статуса подключения. */
interface ClientTarget {
  secretName: string;
  redirectUri: string;
}

/**
 * YouTube · автозалив (0019): OAuth-подключение канала, право youtube.upload, план автозалива (правка —
 * operator). Без OAuth-клиента Google — инструкция и, для admin, запись JSON клиента в секрет: секреты
 * пишет только admin (§5.1), operator подключает каналы.
 */
export function YoutubeCard({
  channelId,
  channelTitle,
  operator,
  admin,
}: {
  channelId: string;
  channelTitle: string;
  operator: boolean;
  admin: boolean;
}) {
  const connection = useYoutubeConnection(channelId);
  const start = useStartYoutubeOAuth(channelId);
  const disconnect = useDisconnectYoutube(channelId);
  const [missing, setMissing] = useState<ClientTarget | null>(null);
  const [leaving, setLeaving] = useState(false);
  const c = connection.data;

  const connect = async () => {
    setMissing(null);
    try {
      const { authorize_url } = await start.mutateAsync();
      // Уходим на экран согласия Google; назад колбэк вернёт на /channels/?oauth=…
      setLeaving(true);
      window.location.assign(authorize_url);
    } catch (error) {
      if (isApiError(error) && error.code === "oauth_client_missing") {
        setMissing({
          secretName: String(error.details.secret_name ?? c?.client_secret_name ?? ""),
          redirectUri: String(error.details.redirect_uri ?? c?.redirect_uri ?? ""),
        });
        void connection.refetch();
        return;
      }
      toast.error("Подключение не началось", { description: errorMessage(error) });
    }
  };

  const unlink = async () => {
    if (
      !window.confirm(
        `Отключить «${channelTitle}» от YouTube? Токен удалится, автозалив канала встанет до нового подключения.`,
      )
    )
      return;
    try {
      await disconnect.mutateAsync();
      toast.success("YouTube отключён", { description: channelTitle });
    } catch (error) {
      toast.error("Не отключено", { description: errorMessage(error) });
    }
  };

  const client: ClientTarget | null =
    missing ?? (c && !c.client_configured ? { secretName: c.client_secret_name, redirectUri: c.redirect_uri } : null);
  const busy = start.isPending || leaving;

  return (
    <div data-testid="youtube-card">
      <SectionCard
        title="YouTube · автозалив"
        actions={
          c ? (
            c.connected ? (
              <Badge variant={c.can_upload ? "positive" : "warning"}>
                {c.can_upload ? "подключён" : "без права загрузки"}
              </Badge>
            ) : (
              <Badge variant="neutral">не подключён</Badge>
            )
          ) : undefined
        }
      >
        {connection.isError ? (
          <SectionError error={connection.error} onRetry={() => void connection.refetch()} />
        ) : !c ? (
          <Skeleton className="h-24" />
        ) : (
          <>
            {c.connected ? (
              <ConnectedInfo connection={c} />
            ) : !client ? (
              <p className="text-sm text-fg-muted">
                Канал не подключён — ролики заливаются вручную. Подключение: вход в Google-аккаунт владельца канала и
                разрешение на загрузку.
              </p>
            ) : null}

            {client ? (
              operator ? (
                <ClientSetup target={client} admin={admin} onSaved={() => setMissing(null)} />
              ) : (
                <p className="text-sm text-fg-muted">OAuth-клиент Google не настроен.</p>
              )
            ) : null}

            <PlanSection connection={c} channelId={channelId} operator={operator} />

            {operator && !(client && !c.connected) ? (
              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-2">
                {c.connected ? (
                  <>
                    <Button size="sm" variant="outline" onClick={() => void connect()} disabled={busy}>
                      {busy ? (
                        <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                      )}
                      Переподключить
                    </Button>
                    {admin ? (
                      <Button
                        size="sm"
                        variant="destructive"
                        onClick={() => void unlink()}
                        disabled={disconnect.isPending || busy}
                      >
                        <Unplug className="h-3.5 w-3.5" aria-hidden="true" />
                        Отключить
                      </Button>
                    ) : null}
                  </>
                ) : (
                  <Button size="sm" onClick={() => void connect()} disabled={busy} data-testid="youtube-connect">
                    {busy ? (
                      <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    ) : (
                      <PlugZap className="h-3.5 w-3.5" aria-hidden="true" />
                    )}
                    Подключить YouTube
                  </Button>
                )}
              </div>
            ) : null}
          </>
        )}
        <p className="text-2xs text-fg-muted">
          С 15.10.2026 документация YouTube API пишет, что ролики из непроверенных проектов больше не блокируются как
          приватные. После первой заливки всё равно проверьте статус ролика в Studio. Приложение Google переведите в In
          production, иначе вход в канал слетает через 7 дней.
        </p>
      </SectionCard>
    </div>
  );
}

function ConnectedInfo({ connection: c }: { connection: YoutubeConnection }) {
  return (
    <>
      <DetailGrid>
        <KeyValue
          label="Канал Google"
          className="col-span-2"
          value={
            c.google_channel_id ? (
              <span className="flex min-w-0 flex-col">
                <a
                  href={`https://www.youtube.com/channel/${encodeURIComponent(c.google_channel_id)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-w-0 items-center gap-1 text-accent hover:underline"
                  data-testid="youtube-channel-link"
                >
                  <span className="truncate">{c.google_channel_title || "без названия"}</span>
                  <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
                </a>
                <span className="truncate font-mono text-2xs text-fg-muted">{c.google_channel_id}</span>
              </span>
            ) : (
              <span className="text-fg-muted">не определён</span>
            )
          }
        />
        <KeyValue
          label="Право загрузки"
          value={
            c.can_upload ? <Badge variant="positive">youtube.upload</Badge> : <Badge variant="negative">нет</Badge>
          }
        />
        <KeyValue label="Подключён" value={formatDateTime(c.granted_at, { short: true })} />
        <KeyValue label="Последний успех" value={formatDateTime(c.last_ok_at, { short: true })} />
      </DetailGrid>
      {!c.can_upload ? (
        <Callout variant="warning" title="Нет права загрузки">
          Токен без youtube.upload — переподключите с правом загрузки: в окне Google отметьте все запрошенные права.
        </Callout>
      ) : null}
      {c.last_error ? (
        <p className="text-2xs text-negative">
          Последняя ошибка: <span className="font-mono">{c.last_error}</span>
        </p>
      ) : null}
    </>
  );
}

function ClientSetup({ target, admin, onSaved }: { target: ClientTarget; admin: boolean; onSaved: () => void }) {
  const { copied, copy } = useCopy();
  return (
    <div className="flex flex-col gap-2" data-testid="youtube-client-setup">
      <Callout variant="warning" title="Нет OAuth-клиента Google">
        <ol className="list-decimal pl-4">
          <li>В Google Cloud Console создайте OAuth client ID типа «Web application».</li>
          <li>В Authorized redirect URIs клиента впишите адрес ниже.</li>
          <li>
            JSON клиента запишите в секрет <span className="font-mono text-fg">{target.secretName}</span>
            {admin ? " — формой ниже." : " — это делает владелец (роль admin)."}
          </li>
        </ol>
      </Callout>
      <CopyRow
        label="Redirect URI"
        value={target.redirectUri}
        copied={copied === "redirect"}
        onCopy={() => void copy("redirect", target.redirectUri, "Redirect URI")}
        testId="youtube-redirect-copy"
      />
      {admin ? (
        <ClientJsonForm secretName={target.secretName} redirectUri={target.redirectUri} onSaved={onSaved} />
      ) : null}
    </div>
  );
}

/** Проверка JSON клиента до отправки — те же правила, что `oauth_google.parse_client` на сервере. */
function checkClientJson(raw: string, redirectUri: string): { error?: string; warning?: string } {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return { error: "Это не JSON — вставьте файл клиента из Google Cloud Console целиком" };
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return { error: "Нужен JSON-объект клиента {…}" };
  const root = data as Record<string, unknown>;
  const kind = ["web", "installed"].find((key) => root[key] && typeof root[key] === "object");
  const body = (kind ? root[kind] : root) as Record<string, unknown>;
  if (
    typeof body.client_id !== "string" ||
    !body.client_id ||
    typeof body.client_secret !== "string" ||
    !body.client_secret
  )
    return { error: "В JSON нет client_id и client_secret" };
  if (kind === "installed")
    return { warning: "Это клиент типа Desktop: вход из браузера требует тип «Web application»" };
  const uris = body.redirect_uris;
  if (Array.isArray(uris) && !uris.includes(redirectUri))
    return {
      warning: `В JSON клиента нет ${redirectUri} — впишите его в Authorized redirect URIs, иначе Google ответит redirect_uri_mismatch`,
    };
  return {};
}

function ClientJsonForm({
  secretName,
  redirectUri,
  onSaved,
}: {
  secretName: string;
  redirectUri: string;
  onSaved: () => void;
}) {
  const write = useWriteYoutubeClient();
  const [value, setValue] = useState("");
  const check = value.trim() ? checkClientJson(value.trim(), redirectUri) : null;

  const save = async () => {
    if (!check || check.error) return;
    try {
      await write.mutateAsync({ name: secretName, value: value.trim() });
      setValue("");
      onSaved();
      toast.success(`Секрет ${secretName} записан`, { description: "Значение больше не показывается." });
    } catch (error) {
      toast.error("Секрет не записан", { description: errorMessage(error) });
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor="youtube-client-json" className="text-xs text-fg-muted">
        JSON клиента → секрет <span className="font-mono">{secretName}</span> (только запись)
      </label>
      <Textarea
        id="youtube-client-json"
        rows={4}
        className="font-mono text-xs"
        spellCheck={false}
        autoComplete="off"
        placeholder='{"web":{"client_id":"…","client_secret":"…","redirect_uris":["…"]}}'
        value={value}
        aria-invalid={!!check?.error}
        onChange={(event) => setValue(event.target.value)}
      />
      {check?.error ? (
        <p className="text-2xs text-negative">{check.error}</p>
      ) : check?.warning ? (
        <p className="text-2xs text-warning">{check.warning}</p>
      ) : null}
      <div>
        <Button size="sm" onClick={() => void save()} disabled={!check || !!check.error || write.isPending}>
          {write.isPending ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Save className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          Сохранить в секрет
        </Button>
      </div>
    </div>
  );
}
