"use client";

import { CircleCheck, CircleX, Cpu, KeyRound, LoaderCircle, PlugZap, Star } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { Badge, Button, Callout, Card, Input, Skeleton, Textarea } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { formatAgo, formatDateTime } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import {
  type Provider,
  type ProviderKind,
  type SecretItem,
  useDeleteSecret,
  useProviders,
  useSecrets,
  useTestProvider,
  useUpdateProvider,
  useWriteSecret,
} from "@/lib/queries/providers";

const KIND_LABELS: Record<ProviderKind, string> = {
  llm: "LLM",
  tts: "Озвучка",
  image: "Картинки",
  asr: "Распознавание речи",
  vision: "Зрение",
  faces: "Лица",
};
const KIND_ORDER: ProviderKind[] = ["llm", "tts", "asr", "image", "vision", "faces"];
const PRICE_UNITS: Record<string, string> = {
  call: "вызов",
  image: "картинка",
  "1k_tokens": "1000 токенов",
  audio_minute: "минута аудио",
  char: "символ",
};
const SECRET_NAME_RE = /^[A-Z][A-Z0-9_]{2,63}$/;

/**
 * Настройки → Провайдеры (§7.3): включение, провайдер по умолчанию на вид, параметры (JSON), секрет только
 * на запись, «Проверить связь», пометка ресурсоёмкости. По решению владельца LLM по умолчанию — Umka
 * (OpenAI-совместимый, https://umka.chat/v1), ASR — локальный.
 */
export function ProvidersPanel() {
  const providers = useProviders();
  const secrets = useSecrets();
  const now = useNow(60_000);
  const secretByName = new Map((secrets.data ?? []).map((item) => [item.name, item]));
  const grouped = KIND_ORDER.map((kind) => ({
    kind,
    items: (providers.data ?? []).filter((provider) => provider.kind === kind),
  })).filter((group) => group.items.length);

  return (
    <div className="flex flex-col gap-4" data-testid="providers-panel">
      <div>
        <h2 className="text-base font-semibold">Провайдеры</h2>
        <p className="text-xs text-fg-muted">
          Значения секретов не показываются никогда — только «задан» и последние 4 символа. «Проверить связь» делает
          настоящий вызов: у платных провайдеров это копейки из бюджета.
        </p>
      </div>
      {providers.isError ? (
        <SectionError error={providers.error} onRetry={() => void providers.refetch()} />
      ) : providers.isPending ? (
        <Skeleton className="h-64" />
      ) : grouped.length === 0 ? (
        <Callout variant="info" title="Реестр пуст">
          Провайдеры появятся после миграций и регистрации адаптеров.
        </Callout>
      ) : (
        grouped.map((group) => (
          <section key={group.kind} className="flex flex-col gap-2" aria-labelledby={`providers-${group.kind}`}>
            <h3
              id={`providers-${group.kind}`}
              className="font-mono text-2xs font-semibold tracking-wider text-fg-muted uppercase"
            >
              {KIND_LABELS[group.kind]}
            </h3>
            <div className="grid gap-3 xl:grid-cols-2">
              {group.items.map((provider) => (
                <ProviderCard
                  key={provider.key}
                  provider={provider}
                  secret={provider.secret_name ? secretByName.get(provider.secret_name) : undefined}
                  now={now}
                />
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}

function ProviderCard({ provider, secret, now }: { provider: Provider; secret: SecretItem | undefined; now: number }) {
  const update = useUpdateProvider();
  const test = useTestProvider();
  const [paramsText, setParamsText] = useState<string | null>(null);
  const [secretName, setSecretName] = useState<string | null>(null);
  const [lastTest, setLastTest] = useState<Schemas["ProviderTestResult"] | null>(null);
  const paramsValue = paramsText ?? JSON.stringify(provider.params ?? {}, null, 2);
  const secretNameValue = secretName ?? provider.secret_name ?? "";

  let paramsError: string | null = null;
  let parsedParams: Record<string, unknown> | null = null;
  try {
    const parsed = JSON.parse(paramsValue) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      parsedParams = parsed as Record<string, unknown>;
    else paramsError = "Нужен JSON-объект {…}";
  } catch {
    paramsError = "Некорректный JSON";
  }
  const secretNameError = secretNameValue && !SECRET_NAME_RE.test(secretNameValue) ? "A–Z, 0–9, _; с буквы" : null;
  const dirty = paramsText !== null || secretName !== null;

  const put = async (patch: Partial<Schemas["ProviderUpdate"]>, message: string) => {
    try {
      await update.mutateAsync({
        key: provider.key,
        body: {
          enabled: provider.enabled,
          is_default: provider.is_default,
          params: provider.params,
          secret_name: provider.secret_name,
          ...patch,
        },
      });
      toast.success(message);
      return true;
    } catch (error) {
      toast.error("Не сохранено", { description: errorMessage(error) });
      return false;
    }
  };

  const saveConfig = async () => {
    if (!parsedParams || secretNameError) return;
    const ok = await put(
      { params: parsedParams, secret_name: secretNameValue.trim() || null },
      `Настройки ${provider.label} сохранены`,
    );
    if (ok) {
      setParamsText(null);
      setSecretName(null);
    }
  };

  const runTest = async () => {
    try {
      const result = await test.mutateAsync(provider.key);
      setLastTest(result);
    } catch (error) {
      setLastTest({ ok: false, latency_ms: null, detail: errorMessage(error) });
    }
  };

  return (
    <Card className="flex flex-col gap-3 p-3" data-testid="provider-card" data-provider={provider.key}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <span className="flex items-center gap-1.5 font-medium">
            {provider.label}
            {provider.is_default ? (
              <Badge variant="accent">
                <Star className="h-3 w-3" aria-hidden="true" />
                по умолчанию
              </Badge>
            ) : null}
          </span>
          <span className="font-mono text-2xs text-fg-muted">
            {provider.key} · {provider.adapter}
          </span>
        </div>
        <label className="flex shrink-0 items-center gap-1.5 text-xs">
          <input
            type="checkbox"
            className="h-4 w-4 accent-[var(--accent)]"
            checked={provider.enabled}
            disabled={update.isPending}
            onChange={(event) =>
              void put(
                { enabled: event.target.checked },
                event.target.checked ? `${provider.label} включён` : `${provider.label} выключен`,
              )
            }
          />
          включён
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 text-2xs">
        <Badge variant={provider.resource_class === "heavy" ? "machine" : "neutral"}>
          <Cpu className="h-3 w-3" aria-hidden="true" />
          {provider.resource_class === "heavy" ? "тяжёлая очередь (CPU бокса)" : "лёгкая очередь"}
        </Badge>
        {provider.price ? (
          <Badge variant="neutral" outline>
            ${provider.price.usd} / {PRICE_UNITS[provider.price.unit] ?? provider.price.unit}
          </Badge>
        ) : (
          <Badge variant="positive" outline>
            бесплатно
          </Badge>
        )}
        {provider.last_ok_at ? (
          <span className="text-fg-muted" title={formatDateTime(provider.last_ok_at)}>
            связь была {formatAgo(provider.last_ok_at, now)}
          </span>
        ) : null}
      </div>
      {provider.license_note ? <p className="text-2xs text-warning">{provider.license_note}</p> : null}
      {provider.last_error ? (
        <p className="text-2xs text-negative">
          Последняя ошибка: <span className="font-mono">{provider.last_error}</span>
        </p>
      ) : null}

      <div className="flex flex-col gap-1">
        <label htmlFor={`params-${provider.key}`} className="text-xs text-fg-muted">
          Параметры (JSON)
        </label>
        <Textarea
          id={`params-${provider.key}`}
          rows={Math.min(8, paramsValue.split("\n").length + 1)}
          className="font-mono text-xs"
          spellCheck={false}
          value={paramsValue}
          aria-invalid={!!paramsError}
          onChange={(event) => setParamsText(event.target.value)}
        />
        {paramsError ? <p className="text-2xs text-negative">{paramsError}</p> : null}
      </div>

      <SecretBlock
        providerKey={provider.key}
        secretName={secretNameValue}
        secretNameError={secretNameError}
        onSecretName={setSecretName}
        secret={secret}
        providerSecretSet={provider.secret_set}
      />

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-2">
        <Button
          size="sm"
          onClick={() => void saveConfig()}
          disabled={!dirty || !!paramsError || !!secretNameError || update.isPending}
        >
          Сохранить
        </Button>
        {!provider.is_default ? (
          <Button
            size="sm"
            variant="outline"
            disabled={update.isPending || !provider.enabled}
            onClick={() =>
              void put({ is_default: true }, `${provider.label} — по умолчанию для «${KIND_LABELS[provider.kind]}»`)
            }
          >
            <Star className="h-3.5 w-3.5" aria-hidden="true" />
            Сделать основным
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="outline"
          onClick={() => void runTest()}
          disabled={test.isPending}
          data-testid="provider-test"
        >
          {test.isPending ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <PlugZap className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          Проверить связь
        </Button>
        {lastTest ? (
          <span
            className={cn("inline-flex items-center gap-1 text-xs", lastTest.ok ? "text-positive" : "text-negative")}
            role="status"
          >
            {lastTest.ok ? (
              <CircleCheck className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <CircleX className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {lastTest.ok
              ? `связь есть${lastTest.latency_ms !== null ? `, ${lastTest.latency_ms} мс` : ""}`
              : (lastTest.detail ?? "нет связи")}
          </span>
        ) : null}
      </div>
    </Card>
  );
}

function SecretBlock({
  providerKey,
  secretName,
  secretNameError,
  onSecretName,
  secret,
  providerSecretSet,
}: {
  providerKey: string;
  secretName: string;
  secretNameError: string | null;
  onSecretName: (value: string) => void;
  secret: SecretItem | undefined;
  providerSecretSet: boolean;
}) {
  const write = useWriteSecret();
  const remove = useDeleteSecret();
  const [value, setValue] = useState("");
  const isSet = secret?.is_set ?? providerSecretSet;

  const save = async () => {
    if (!secretName || secretNameError || !value) return;
    try {
      await write.mutateAsync({ name: secretName, value });
      setValue("");
      toast.success(`Секрет ${secretName} записан`, { description: "Значение больше не показывается." });
    } catch (error) {
      toast.error("Секрет не записан", { description: errorMessage(error) });
    }
  };

  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-border p-2">
      <div className="flex items-center gap-2 text-xs">
        <KeyRound className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />
        <span className="text-fg-muted">Секрет</span>
        {secretName ? (
          isSet ? (
            <Badge variant="positive">
              задан{secret?.last4 ? ` · …${secret.last4}` : ""}
              {secret?.source === "env" ? " · из env" : ""}
            </Badge>
          ) : (
            <Badge variant="warning">не задан</Badge>
          )
        ) : (
          <Badge variant="neutral">не нужен</Badge>
        )}
      </div>
      <div className="grid gap-1.5 sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)_auto]">
        <Input
          aria-label="Имя секрета"
          className="h-7 font-mono text-xs"
          placeholder="LLM_API_KEY"
          value={secretName}
          aria-invalid={!!secretNameError}
          onChange={(event) => onSecretName(event.target.value.toUpperCase())}
        />
        <Input
          aria-label={`Новое значение секрета для ${providerKey}`}
          type="password"
          autoComplete="off"
          className="h-7 font-mono text-xs"
          placeholder={isSet ? "Заменить значение…" : "Значение (только запись)"}
          value={value}
          disabled={!secretName || !!secretNameError}
          onChange={(event) => setValue(event.target.value)}
        />
        <div className="flex gap-1">
          <Button
            size="sm"
            variant="outline"
            disabled={!value || write.isPending || !!secretNameError}
            onClick={() => void save()}
          >
            Записать
          </Button>
          {secret?.is_set && secret.source === "db" ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={remove.isPending}
              onClick={() => {
                if (window.confirm(`Удалить секрет ${secret.name}? Провайдер перестанет работать.`))
                  remove.mutate(secret.name);
              }}
            >
              Удалить
            </Button>
          ) : null}
        </div>
      </div>
      {secretNameError ? <p className="text-2xs text-negative">Имя секрета: {secretNameError}</p> : null}
    </div>
  );
}
