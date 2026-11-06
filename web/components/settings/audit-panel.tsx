"use client";

import { ChevronDown, ChevronRight, Lock, RefreshCw, ScrollText } from "lucide-react";
import { useEffect, useState } from "react";

import { useCurrentUser } from "@/components/auth/auth-context";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  DateRangeInput,
  EmptyState,
  FormField,
  Input,
  Select,
  SkeletonTable,
  Table,
  TableBody,
  TableHead,
  TableRow,
  Td,
  Th,
} from "@/components/ui";
import { canOperate } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber } from "@/lib/format";
import { type ActorKind, type AuditEntry, auditEntries, useAuditLog } from "@/lib/queries/audit";

/** Типы объектов, которые пишет `services/audit.py` (по вызовам `audit.record`). */
const ENTITY_TYPE_LABELS: Record<string, string> = {
  user: "Пользователь",
  session: "Сессия",
  api_token: "API-токен",
  secret: "Секрет",
  settings: "Настройки",
  budget: "Бюджет",
  provider: "Провайдер",
  product: "Продукт",
  campaign: "Кампания",
  channel: "Канал",
  cluster: "Куст каналов",
  niche: "Ниша",
  source: "Источник",
  clip_candidate: "Кандидат нарезки",
  job: "Ролик",
  package: "Пакет",
  publication: "Публикация",
  tracking_link: "Трекинг-ссылка",
  banner_template: "Шаблон баннера",
  banner_variant: "Вариант баннера",
  banner_creative: "Креатив баннера",
  asset: "Файл",
  alert: "Алерт",
};
const ENTITY_TYPES = Object.keys(ENTITY_TYPE_LABELS);

/** Подсказки для поля «Действие»: точное значение или префикс со звёздочкой. */
const ACTION_PRESETS = [
  "auth.*",
  "auth.login",
  "auth.locked",
  "user.*",
  "secret.*",
  "settings.update",
  "budget.*",
  "provider.update",
  "product.*",
  "campaign.*",
  "channel.*",
  "niche.*",
  "source.*",
  "job.*",
  "package.*",
  "publication.*",
  "tracking_link.*",
  "banner_variant.*",
  "alert.ack",
];

const ACTOR_KIND_META: Record<ActorKind, { label: string; variant: "human" | "accent" | "machine" | "neutral" }> = {
  user: { label: "пользователь", variant: "human" },
  api_token: { label: "API-токен", variant: "accent" },
  bot: { label: "бот", variant: "machine" },
  system: { label: "система", variant: "neutral" },
  webhook: { label: "вебхук", variant: "machine" },
};
const ACTOR_KINDS = Object.keys(ACTOR_KIND_META) as ActorKind[];

/** Текст из поля попадает в запрос после паузы, чтобы не дёргать сервер на каждую букву. */
function useDebounced(value: string, delay = 400): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

/** Настройки → Аудит (§7.3): журнал действий с фильтрами и постраничной подгрузкой по `before_id`. Operator+. */
export function AuditPanel() {
  const user = useCurrentUser();
  if (!canOperate(user.role))
    return (
      <EmptyState
        icon={Lock}
        title="Журнал доступен оператору и владельцу"
        description="Роль «Наблюдатель» видит состояние системы, но не действия пользователей."
      />
    );
  return <AuditPanelBody />;
}

function AuditPanelBody() {
  const [entityType, setEntityType] = useState("");
  const [actorKind, setActorKind] = useState<ActorKind | "">("");
  const [actionText, setActionText] = useState("");
  const [entityIdText, setEntityIdText] = useState("");
  const [actorUserText, setActorUserText] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const action = useDebounced(actionText.trim());
  const entityId = useDebounced(entityIdText.trim());
  const actorUser = useDebounced(actorUserText.trim());
  const actorUserId = /^\d+$/.test(actorUser) ? Number(actorUser) : null;
  const actorUserError = actorUser && actorUserId === null ? "Только цифры: id пользователя" : undefined;

  const journal = useAuditLog({
    entityType: entityType || null,
    entityId: entityId || null,
    action: action || null,
    actorUserId,
    dateFrom: dateFrom || null,
    dateTo: dateTo || null,
  });
  const loaded = auditEntries(journal.data);
  // Параметра actor_kind у API нет — фильтруем загруженные страницы на клиенте.
  const entries = actorKind ? loaded.filter((entry) => entry.actor_kind === actorKind) : loaded;
  const filtered = !!entityType || !!actorKind || !!action || !!entityId || !!actorUser || !!dateFrom || !!dateTo;

  const resetFilters = () => {
    setEntityType("");
    setActorKind("");
    setActionText("");
    setEntityIdText("");
    setActorUserText("");
    setDateFrom("");
    setDateTo("");
    setExpandedId(null);
  };

  return (
    <div className="flex flex-col gap-4" data-testid="audit-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">Журнал аудита</h2>
          <p className="text-xs text-fg-muted">
            Кто, что и когда менял: вход и сессии, пользователи, секреты (значения вырезаны), настройки, бюджет,
            каталог, приёмка роликов. Новые записи сверху.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void journal.refetch()}>
          <RefreshCw className={cn("h-3.5 w-3.5", journal.isFetching && "animate-spin")} aria-hidden="true" />
          Обновить
        </Button>
      </div>

      <div className="flex flex-wrap items-end gap-2" role="search" aria-label="Фильтры журнала">
        <FormField label="Объект" htmlFor="audit-entity-type">
          <Select
            id="audit-entity-type"
            className="w-auto min-w-40"
            value={entityType}
            onChange={(event) => setEntityType(event.target.value)}
          >
            <option value="">Любой</option>
            {ENTITY_TYPES.map((value) => (
              <option key={value} value={value}>
                {ENTITY_TYPE_LABELS[value]}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Id объекта" htmlFor="audit-entity-id">
          <Input
            id="audit-entity-id"
            className="w-36 font-mono"
            placeholder="umka, 42…"
            value={entityIdText}
            onChange={(event) => setEntityIdText(event.target.value)}
          />
        </FormField>
        <FormField label="Действие" htmlFor="audit-action">
          <Input
            id="audit-action"
            list="audit-action-presets"
            className="w-44 font-mono"
            placeholder="secret.set или user.*"
            value={actionText}
            spellCheck={false}
            onChange={(event) => setActionText(event.target.value)}
          />
          <datalist id="audit-action-presets">
            {ACTION_PRESETS.map((value) => (
              <option key={value} value={value} />
            ))}
          </datalist>
        </FormField>
        <FormField label="Автор" htmlFor="audit-actor-kind">
          <Select
            id="audit-actor-kind"
            className="w-auto"
            value={actorKind}
            onChange={(event) => setActorKind(event.target.value as ActorKind | "")}
          >
            <option value="">Любой</option>
            {ACTOR_KINDS.map((value) => (
              <option key={value} value={value}>
                {ACTOR_KIND_META[value].label}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Id пользователя" htmlFor="audit-actor-user" error={actorUserError}>
          <Input
            id="audit-actor-user"
            inputMode="numeric"
            className="w-28 font-mono"
            placeholder="1"
            value={actorUserText}
            aria-invalid={!!actorUserError}
            onChange={(event) => setActorUserText(event.target.value)}
          />
        </FormField>
        <FormField label="Период" htmlFor="audit-date-from">
          <DateRangeInput from={dateFrom} to={dateTo} onFromChange={setDateFrom} onToChange={setDateTo} />
        </FormField>
        {filtered ? (
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            Сбросить фильтры
          </Button>
        ) : null}
      </div>
      {actorKind ? (
        <p className="text-2xs text-fg-muted">
          Фильтр по автору применяется к загруженным страницам: если записей мало, нажмите «Показать ещё».
        </p>
      ) : null}

      {journal.isError ? (
        <SectionError error={journal.error} onRetry={() => void journal.refetch()} title="Журнал недоступен" />
      ) : journal.isPending ? (
        <SkeletonTable rows={8} />
      ) : entries.length === 0 ? (
        <EmptyState
          icon={ScrollText}
          compact
          title={filtered ? "Ничего не найдено" : "Журнал пуст"}
          description={
            filtered
              ? journal.hasNextPage
                ? "В загруженных страницах совпадений нет — попробуйте «Показать ещё» или смените фильтр."
                : "Смените фильтр или сбросьте его."
              : "Записи появятся после первых действий в консоли."
          }
          action={
            filtered && journal.hasNextPage ? (
              <Button
                variant="outline"
                size="sm"
                disabled={journal.isFetchingNextPage}
                onClick={() => void journal.fetchNextPage()}
              >
                {journal.isFetchingNextPage ? "Загрузка…" : "Показать ещё"}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <Table minWidth={900}>
          <caption className="sr-only">Журнал аудита</caption>
          <TableHead>
            <tr>
              <Th className="w-6" />
              <Th>Время</Th>
              <Th>Автор</Th>
              <Th>Объект</Th>
              <Th>Действие</Th>
              <Th>Описание</Th>
            </tr>
          </TableHead>
          <TableBody>
            {entries.map((entry) => {
              const expanded = expandedId === entry.id;
              return [
                <TableRow
                  key={entry.id}
                  clickable
                  aria-expanded={expanded}
                  onClick={() => setExpandedId(expanded ? null : entry.id)}
                >
                  <Td className="pr-0 text-fg-muted">
                    {expanded ? (
                      <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                    )}
                  </Td>
                  <Td className="whitespace-nowrap text-fg-muted">{formatDateTime(entry.occurred_at)}</Td>
                  <Td>
                    <ActorCell entry={entry} />
                  </Td>
                  <Td>
                    <span className="flex flex-col">
                      <span>{ENTITY_TYPE_LABELS[entry.entity_type] ?? entry.entity_type}</span>
                      {entry.entity_id ? (
                        <span className="font-mono text-2xs text-fg-muted">{entry.entity_id}</span>
                      ) : null}
                    </span>
                  </Td>
                  <Td className="font-mono text-xs">{entry.action}</Td>
                  <Td className="max-w-md truncate text-fg-muted">{entry.summary ?? "—"}</Td>
                </TableRow>,
                expanded ? (
                  <tr key={`${entry.id}-details`}>
                    <td colSpan={6} className="bg-surface-2/40 p-3">
                      <EntryDetails entry={entry} />
                    </td>
                  </tr>
                ) : null,
              ];
            })}
          </TableBody>
        </Table>
      )}

      {entries.length ? (
        <div className="flex items-center justify-between gap-2">
          <span className="text-2xs text-fg-muted">
            Показано {formatNumber(entries.length)}
            {actorKind && loaded.length !== entries.length ? ` из ${formatNumber(loaded.length)} загруженных` : ""}
          </span>
          {journal.hasNextPage ? (
            <Button
              variant="outline"
              size="sm"
              disabled={journal.isFetchingNextPage}
              onClick={() => void journal.fetchNextPage()}
            >
              {journal.isFetchingNextPage ? "Загрузка…" : "Показать ещё"}
            </Button>
          ) : (
            <span className="text-2xs text-fg-muted">Это все записи</span>
          )}
        </div>
      ) : null}
    </div>
  );
}

function ActorCell({ entry }: { entry: AuditEntry }) {
  const meta = ACTOR_KIND_META[entry.actor_kind];
  return (
    <span className="flex items-center gap-1.5">
      <Badge variant={meta.variant} outline={entry.actor_kind !== "user"}>
        {meta.label}
      </Badge>
      {entry.actor_user_id !== null ? (
        <span className="font-mono text-xs text-fg-muted">#{entry.actor_user_id}</span>
      ) : null}
    </span>
  );
}

/** Раскрытая запись: diff как есть (секреты вырезаны сервером), request_id для поиска в логах. */
function EntryDetails({ entry }: { entry: AuditEntry }) {
  const hasDiff = entry.diff !== null && Object.keys(entry.diff).length > 0;
  return (
    <div className="flex flex-col gap-2">
      <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <div className="flex gap-1">
          <dt className="text-fg-muted">Запись</dt>
          <dd className="font-mono">#{entry.id}</dd>
        </div>
        <div className="flex gap-1">
          <dt className="text-fg-muted">request_id</dt>
          <dd className="font-mono">{entry.request_id ?? "—"}</dd>
        </div>
        {entry.summary ? (
          <div className="flex gap-1">
            <dt className="text-fg-muted">Описание</dt>
            <dd>{entry.summary}</dd>
          </div>
        ) : null}
      </dl>
      {hasDiff ? (
        <pre className="max-h-80 overflow-auto rounded-md border border-border bg-surface p-2 font-mono text-xs whitespace-pre-wrap break-all">
          {JSON.stringify(entry.diff, null, 2)}
        </pre>
      ) : (
        <p className="text-xs text-fg-muted">Изменений полей не записано.</p>
      )}
    </div>
  );
}
