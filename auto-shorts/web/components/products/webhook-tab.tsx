"use client";

import { FlaskConical, KeyRound, Send, ShieldAlert, TriangleAlert, Webhook } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { CopyButton, CopyRow, useCopy } from "@/components/packages/copy";
import { CONVERSION_EVENT_LABELS, INGEST_OUTCOME_META } from "@/components/packages/labels";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  Callout,
  Dialog,
  EmptyState,
  FormField,
  Input,
  PillToggle,
  SectionCard,
  Select,
  Skeleton,
  StatusBadge,
} from "@/components/ui";
import { errorMessage, isApiError } from "@/lib/api/errors";
import { formatDateTime, formatNumber } from "@/lib/format";
import {
  type ConversionEventType,
  type ConversionTestResult,
  type ProductIntegration,
  useProductIntegration,
  useRotateWebhookSecret,
  useSendTestConversion,
  type WebhookSecretRotated,
} from "@/lib/queries/tracking";

const GRACE_OPTIONS = [
  { value: 0, label: "сразу (прежний перестаёт работать)" },
  { value: 1, label: "1 час" },
  { value: 24, label: "24 часа" },
  { value: 72, label: "3 дня" },
  { value: 168, label: "7 дней" },
];

const TEST_ERROR_LABELS: Record<string, string> = {
  invalid_signature: "Подпись не сошлась — секрет не задан или продукт шлёт старым",
  stale_timestamp: "Время запроса вне окна ±5 минут — проверьте часы сервера продукта",
  rate_limited: "Превышен лимит запросов в секунду",
  payload_too_large: "Тело больше лимита",
  invalid_body: "Тело не прошло проверку",
  unknown_product: "Продукт неизвестен приёму",
};

const TAG_RE = /^[A-Za-z0-9_-]{1,64}$/;

/**
 * Вкладка «Вебхук конверсий» карточки продукта (§7.3 `/products/view?tab=webhook`, §5.3 приём):
 * секрет HMAC только на запись (показывается один раз, ротация — владелец), инструкция интеграции
 * со сниппетами, тестовое событие через тот же код приёма (`is_test`, в статистику не идёт),
 * последние 20 запросов приёма, включая отказы.
 */
export function WebhookTab({ productId, operator, admin }: { productId: string; operator: boolean; admin: boolean }) {
  const integration = useProductIntegration(productId);
  const data = integration.data;

  if (integration.isError) {
    return (
      <SectionError
        error={integration.error}
        onRetry={() => void integration.refetch()}
        title="Инструкция интеграции не загрузилась"
      />
    );
  }
  if (!data) {
    return (
      <div className="grid gap-4 xl:grid-cols-2">
        <Skeleton className="h-48" />
        <Skeleton className="h-48" />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4" data-testid="webhook-tab">
      {!data.secret_set ? (
        <Callout variant="warning" title="Секрет вебхука не задан">
          Продукт не сможет подписать события — приём ответит 401 invalid_signature.{" "}
          {admin
            ? "Сгенерируйте секрет ниже и передайте его в конфиг продукта."
            : "Попросите владельца сгенерировать секрет."}
        </Callout>
      ) : null}
      <div className="grid gap-4 xl:grid-cols-2">
        <SecretCard data={data} productId={productId} admin={admin} />
        <TestConversionCard data={data} productId={productId} operator={operator} />
      </div>
      <IntegrationCard data={data} />
      <RecentRequestsCard data={data} />
    </div>
  );
}

function SecretCard({ data, productId, admin }: { data: ProductIntegration; productId: string; admin: boolean }) {
  const rotate = useRotateWebhookSecret(productId);
  const [grace, setGrace] = useState(24);
  const [revealed, setRevealed] = useState<WebhookSecretRotated | null>(null);
  const { copied, copy } = useCopy();

  function doRotate() {
    const prompt = data.secret_set
      ? `Сгенерировать новый секрет для «${productId}»? Прежний будет приниматься ещё ${grace ? `${grace} ч` : "0 ч — то есть перестанет сразу"}.`
      : `Сгенерировать секрет для «${productId}»?`;
    if (!window.confirm(prompt)) return;
    rotate.mutate(grace, {
      onSuccess: (result) => setRevealed(result),
      onError: (error) => toast.error("Секрет не создан", { description: errorMessage(error) }),
    });
  }

  return (
    <SectionCard
      title="Секрет подписи"
      actions={data.secret_set ? <Badge variant="positive">задан</Badge> : <Badge variant="warning">не задан</Badge>}
    >
      <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
        <div className="flex flex-col gap-0.5">
          <dt className="text-xs text-fg-muted">Сменён</dt>
          <dd>{formatDateTime(data.secret_rotated_at, { short: true })}</dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="text-xs text-fg-muted">Прежний принимается до</dt>
          <dd>
            {data.previous_secret_valid_until ? formatDateTime(data.previous_secret_valid_until, { short: true }) : "—"}
          </dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="text-xs text-fg-muted">Последнее событие (не тестовое)</dt>
          <dd>{formatDateTime(data.last_event_at, { short: true })}</dd>
        </div>
      </dl>
      <p className="text-xs text-fg-muted">
        Секрет хранится только на сервере и показывается один раз — при создании. Прочитать его потом нельзя, только
        заменить.
      </p>
      {admin ? (
        <div className="flex flex-wrap items-end gap-2">
          <FormField label="Прежний секрет ещё принимать" htmlFor={`grace-${productId}`}>
            <Select
              id={`grace-${productId}`}
              value={grace}
              onChange={(event) => setGrace(Number(event.target.value))}
              className="w-64"
              disabled={!data.secret_set}
            >
              {GRACE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </FormField>
          <Button onClick={doRotate} disabled={rotate.isPending} data-testid="rotate-webhook-secret">
            <KeyRound className="h-4 w-4" aria-hidden="true" />
            {data.secret_set ? "Сменить секрет" : "Сгенерировать секрет"}
          </Button>
        </div>
      ) : (
        <p className="flex items-center gap-1 text-xs text-fg-muted">
          <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
          Менять секрет может только владелец.
        </p>
      )}

      <Dialog
        open={revealed !== null}
        onClose={() => setRevealed(null)}
        title="Новый секрет вебхука"
        description="Скопируйте сейчас: после закрытия окна значение больше не покажется."
        footer={
          <Button onClick={() => setRevealed(null)} data-testid="secret-dialog-close">
            Скопировал, закрыть
          </Button>
        }
      >
        {revealed ? (
          <div className="flex flex-col gap-3" data-testid="secret-dialog">
            <div className="flex items-center gap-2 rounded-md border border-border bg-bg p-2">
              <code className="min-w-0 flex-1 font-mono text-xs break-all text-fg" data-testid="secret-value">
                {revealed.secret}
              </code>
              <CopyButton
                copied={copied === "secret"}
                onCopy={() => void copy("secret", revealed.secret, "Секрет")}
                label="Секрет"
              />
            </div>
            <p className="text-xs text-fg-muted">
              Имя в хранилище секретов: <span className="font-mono">{revealed.secret_name}</span>. В продукте положите
              значение в его конфиг (переменная окружения), не в код.
            </p>
            {revealed.previous_valid_until ? (
              <p className="text-xs text-fg-muted">
                Прежний секрет принимается до {formatDateTime(revealed.previous_valid_until, { short: true })} — успейте
                обновить продукт.
              </p>
            ) : (
              <p className="flex items-center gap-1 text-xs text-warning">
                <TriangleAlert className="h-3.5 w-3.5" aria-hidden="true" />
                Прежний секрет больше не принимается.
              </p>
            )}
          </div>
        ) : null}
      </Dialog>
    </SectionCard>
  );
}

function TestConversionCard({
  data,
  productId,
  operator,
}: {
  data: ProductIntegration;
  productId: string;
  operator: boolean;
}) {
  const send = useSendTestConversion(productId);
  const types = data.event_types.length ? data.event_types : (["bot_start"] as ConversionEventType[]);
  const [eventType, setEventType] = useState<ConversionEventType>(types.includes("bot_start") ? "bot_start" : types[0]);
  const [tag, setTag] = useState("");
  const [result, setResult] = useState<ConversionTestResult | null>(null);
  const tagInvalid = tag.trim() !== "" && !TAG_RE.test(tag.trim());

  function submit(event: FormEvent) {
    event.preventDefault();
    if (tagInvalid) return;
    setResult(null);
    send.mutate({ event_type: eventType, tag: tag.trim() || null }, { onSuccess: (next) => setResult(next) });
  }

  return (
    <SectionCard title="Тестовое событие">
      <p className="text-xs text-fg-muted">
        Идёт через тот же код приёма, подписано настоящим секретом, помечено как тестовое и в статистику не попадает.
      </p>
      {operator ? (
        <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
          <FormField label="Событие" htmlFor={`test-type-${productId}`}>
            <Select
              id={`test-type-${productId}`}
              value={eventType}
              onChange={(event) => setEventType(event.target.value as ConversionEventType)}
              className="w-48"
            >
              {types.map((type) => (
                <option key={type} value={type}>
                  {CONVERSION_EVENT_LABELS[type] ?? type}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField
            label="Метка (tag)"
            htmlFor={`test-tag-${productId}`}
            error={tagInvalid ? "Только A–Z, a–z, 0–9, _ и -, до 64" : undefined}
          >
            <Input
              id={`test-tag-${productId}`}
              value={tag}
              placeholder="пусто — канальный или тестовый код"
              onChange={(event) => setTag(event.target.value)}
              className="w-60 font-mono text-xs"
              aria-invalid={tagInvalid ? true : undefined}
            />
          </FormField>
          <Button type="submit" disabled={send.isPending || tagInvalid} data-testid="send-test-conversion">
            <Send className="h-4 w-4" aria-hidden="true" />
            {send.isPending ? "Отправляю…" : "Отправить"}
          </Button>
        </form>
      ) : (
        <p className="text-xs text-fg-muted">Отправлять тестовые события могут оператор и владелец.</p>
      )}
      {send.error ? (
        <Callout variant="negative" title="Запрос не выполнен">
          {errorMessage(send.error)}
          {isApiError(send.error) ? <span className="ml-1 font-mono text-2xs">({send.error.code})</span> : null}
        </Callout>
      ) : null}
      {result ? <TestResult result={result} /> : null}
    </SectionCard>
  );
}

function TestResult({ result }: { result: ConversionTestResult }) {
  const status = result.response?.results[0]?.status;
  return (
    <div
      className={
        result.ok
          ? "flex flex-col gap-1.5 rounded-md border border-positive/40 bg-positive/10 p-2.5 text-sm"
          : "flex flex-col gap-1.5 rounded-md border border-negative/40 bg-negative/10 p-2.5 text-sm"
      }
      data-testid="test-conversion-result"
      data-ok={result.ok ? "true" : "false"}
    >
      <div className="flex flex-wrap items-center gap-2">
        <FlaskConical className={result.ok ? "h-4 w-4 text-positive" : "h-4 w-4 text-negative"} aria-hidden="true" />
        <span className="font-medium text-fg">{result.ok ? "Приём ответил" : "Приём отказал"}</span>
        <Badge variant={result.ok ? "positive" : "negative"} className="font-mono">
          HTTP {result.http_status}
        </Badge>
        {status ? <Badge variant="neutral">{status}</Badge> : null}
      </div>
      {result.error_code ? (
        <p className="text-xs text-fg">
          {TEST_ERROR_LABELS[result.error_code] ?? result.error_code}{" "}
          <span className="font-mono text-fg-muted">({result.error_code})</span>
        </p>
      ) : null}
      {result.response ? (
        <p className="tabular font-mono text-xs text-fg-muted">
          принято {result.response.accepted} · повторов {result.response.duplicates} · неизвестный код{" "}
          {result.response.unknown_tag} · ошибок {result.response.invalid}
        </p>
      ) : null}
      <p className="font-mono text-2xs text-fg-muted">
        event_id {result.event_id}
        {result.request_id ? ` · request ${result.request_id}` : ""}
      </p>
    </div>
  );
}

function IntegrationCard({ data }: { data: ProductIntegration }) {
  const { copied, copy } = useCopy();
  const [lang, setLang] = useState<"python" | "node">("python");
  const snippet = data.emitter_snippets[lang];
  const example = JSON.stringify(data.example_payload, null, 2);

  return (
    <SectionCard title="Как подключить продукт">
      <CopyRow
        label="Адрес приёма"
        value={data.ingest_url}
        copied={copied === "url"}
        onCopy={() => void copy("url", data.ingest_url, "Адрес приёма")}
      />
      <div className="flex flex-col gap-1">
        <span className="text-xs text-fg-muted">Заголовки</span>
        <ul className="flex flex-col gap-1 text-sm">
          {data.headers.map((header) => (
            <li key={header.name} className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2">
              <span className="w-40 shrink-0 font-mono text-xs text-fg">{header.name}</span>
              <span className="font-mono text-xs text-accent">{header.value}</span>
              <span className="text-xs text-fg-muted">{header.description}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-fg-muted">Подпись</span>
        <code className="rounded-md border border-border bg-bg px-2 py-1.5 font-mono text-xs break-all text-fg">
          {data.signature_spec}
        </code>
      </div>
      <p className="text-xs text-fg-muted">
        Окно времени ±{formatNumber(data.max_skew_sec)} с · до {formatNumber(data.max_events)} событий в запросе · тело
        до {formatNumber(Math.round(data.max_body_bytes / 1024))} КБ · до {formatNumber(data.rate_limit_per_sec)}{" "}
        запросов/с. Повтор того же <span className="font-mono">event_id</span> не задвоит конверсию.
      </p>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-fg-muted">Типы событий</span>
        <div className="flex flex-wrap gap-1">
          {data.event_types.map((type) => (
            <Badge key={type} variant="neutral" outline>
              <span className="font-mono">{type}</span> · {CONVERSION_EVENT_LABELS[type] ?? type}
            </Badge>
          ))}
        </div>
      </div>

      {data.deeplink_examples.length ? (
        <div className="flex flex-col gap-1">
          <span className="text-xs text-fg-muted">Откуда продукт берёт код (tag)</span>
          <ul className="flex flex-col gap-1">
            {data.deeplink_examples.map((example) => (
              <li key={example.kind} className="flex flex-col gap-0.5">
                <span className="font-mono text-xs break-all text-fg">{example.url}</span>
                <span className="text-xs text-fg-muted">{example.description}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-fg-muted">Пример тела запроса</span>
          <CopyButton
            copied={copied === "payload"}
            onCopy={() => void copy("payload", example, "Пример тела")}
            label="Пример тела"
          />
        </div>
        <pre className="max-h-64 overflow-auto rounded-md border border-border bg-bg p-2 font-mono text-xs text-fg">
          {example}
        </pre>
      </div>

      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between gap-2">
          <PillToggle
            label="Язык сниппета"
            value={lang}
            onChange={setLang}
            options={[
              { value: "python", label: "Python (stdlib)" },
              { value: "node", label: "Node.js" },
            ]}
          />
          <CopyButton
            copied={copied === `snippet-${lang}`}
            onCopy={() => void copy(`snippet-${lang}`, snippet, `Сниппет ${lang}`)}
            label="Сниппет"
          />
        </div>
        <pre
          className="max-h-96 overflow-auto rounded-md border border-border bg-bg p-2 font-mono text-xs text-fg"
          data-testid="emitter-snippet"
        >
          {snippet}
        </pre>
      </div>
    </SectionCard>
  );
}

function RecentRequestsCard({ data }: { data: ProductIntegration }) {
  const items = data.recent_requests;
  return (
    <SectionCard title="Последние запросы приёма">
      {items.length === 0 ? (
        <EmptyState
          icon={Webhook}
          title="Запросов ещё не было"
          description="Отправьте тестовое событие — оно появится здесь вместе с исходом проверки."
          compact
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm" data-testid="ingest-requests">
            <thead>
              <tr className="border-b border-border text-left text-xs text-fg-muted">
                <th className="py-1.5 pr-2 font-medium">Время</th>
                <th className="py-1.5 pr-2 font-medium">Исход</th>
                <th className="py-1.5 pr-2 text-right font-medium">HTTP</th>
                <th className="py-1.5 pr-2 text-right font-medium">Событий</th>
                <th className="py-1.5 pr-2 font-medium">Принято / повторы / без кода / ошибки</th>
                <th className="py-1.5 font-medium">Детали</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="h-8 border-b border-border/60 last:border-0">
                  <td className="py-1 pr-2 text-xs whitespace-nowrap">
                    {formatDateTime(item.received_at, { short: true })}
                  </td>
                  <td className="py-1 pr-2">
                    <span className="flex items-center gap-1">
                      <StatusBadge status={item.outcome} map={INGEST_OUTCOME_META} />
                      {item.is_test ? (
                        <Badge variant="neutral" outline>
                          тест
                        </Badge>
                      ) : null}
                    </span>
                  </td>
                  <td className="tabular py-1 pr-2 text-right font-mono text-xs">{item.http_status}</td>
                  <td className="tabular py-1 pr-2 text-right font-mono text-xs">{item.events_total}</td>
                  <td className="tabular py-1 pr-2 font-mono text-xs text-fg-muted">
                    {item.accepted} / {item.duplicates} / {item.unknown_tag} / {item.invalid}
                  </td>
                  <td className="max-w-64 truncate py-1 text-xs text-fg-muted" title={item.detail ?? undefined}>
                    {item.detail ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </SectionCard>
  );
}
