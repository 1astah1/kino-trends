"use client";

import { CircleAlert, Megaphone, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { type UploadPackage, useSetChecklistItem } from "@/lib/queries/packages";

import { CHECKLIST_HINTS, checklistLabel } from "./labels";
import { eridOf } from "./package-utils";

/**
 * Серверный чек-лист пакета (§7.3): пункты и обязательность решает `package_build`
 * (например, AI-метка обязательна при синтетическом голосе). Отметка — `PATCH /checklist`
 * с оптимистичным обновлением; `published` сервер ставит сам при регистрации ссылки.
 */
export function PackageChecklist({ pkg, editable }: { pkg: UploadPackage; editable: boolean }) {
  const toggle = useSetChecklistItem(pkg.id);
  const required = pkg.checklist.filter((item) => item.required);
  const doneRequired = required.filter((item) => item.done).length;
  const erid = eridOf(pkg.texts.description);
  const hasAdItem = pkg.checklist.some((item) => (item.code as string) === "ad_marking");

  return (
    <div className="flex flex-col gap-2" data-testid="package-checklist">
      <div className="flex items-center justify-between gap-2 text-xs text-fg-muted">
        <span>
          Обязательных отмечено{" "}
          <span className="tabular font-mono text-fg">
            {doneRequired}/{required.length}
          </span>
        </span>
        {pkg.ai_disclosure_required ? (
          <Badge variant="machine">
            <Sparkles className="h-3 w-3" aria-hidden="true" />
            нужна AI-метка
          </Badge>
        ) : null}
      </div>
      <ul className="flex flex-col gap-0.5">
        {pkg.checklist.map((item) => {
          const hint = CHECKLIST_HINTS[item.code]?.[pkg.channel.platform];
          const id = `check-${pkg.id}-${item.code}`;
          const serverOnly = item.code === "published";
          const important = item.code === "ai_disclosure" && item.required;
          return (
            <li
              key={item.code}
              className={cn(
                "flex items-start gap-2 rounded-md px-2 py-1.5",
                important && !item.done && "bg-machine/10",
                !item.done && item.required && !important && "hover:bg-surface-2/60",
              )}
            >
              <input
                id={id}
                type="checkbox"
                className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
                checked={item.done}
                disabled={!editable || serverOnly || toggle.isPending}
                onChange={(event) => toggle.mutate({ code: item.code, done: event.target.checked })}
                data-testid={`checklist-${item.code}`}
              />
              <label htmlFor={id} className="flex min-w-0 flex-col gap-0.5 text-sm">
                <span className={cn(item.done ? "text-fg-muted line-through decoration-fg-muted/50" : "text-fg")}>
                  {checklistLabel(item.code)}
                  {!item.required ? <span className="ml-1 text-xs text-fg-muted">(по желанию)</span> : null}
                  {serverOnly ? (
                    <span className="ml-1 text-xs text-fg-muted">— отметится при регистрации ссылки</span>
                  ) : null}
                </span>
                {hint && !item.done ? <span className="text-xs text-fg-muted">{hint}</span> : null}
                {item.done && item.done_at ? (
                  <span className="text-2xs text-fg-muted">{formatDateTime(item.done_at, { short: true })}</span>
                ) : null}
              </label>
            </li>
          );
        })}
      </ul>
      {erid && !hasAdItem ? (
        <div className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 px-2 py-1.5 text-xs">
          <Megaphone className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />
          <span className="text-fg">
            Реклама с маркировкой: проверьте, что в кадре и в описании есть «Реклама · erid»{" "}
            <span className="font-mono">{erid}</span>. Описание копируйте целиком — строку с erid не удаляйте.
          </span>
        </div>
      ) : null}
      {pkg.checklist.length === 0 ? (
        <p className="flex items-center gap-1 text-xs text-fg-muted">
          <CircleAlert className="h-3.5 w-3.5" aria-hidden="true" />
          Чек-лист пуст — пересоберите пакет.
        </p>
      ) : null}
    </div>
  );
}
