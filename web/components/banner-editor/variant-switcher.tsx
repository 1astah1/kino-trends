"use client";

import { Copy, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button, StatusBadge } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { routeHref } from "@/lib/nav";
import {
  type ApiVariant,
  nextVariantCode,
  useDuplicateVariant,
  useUpdateVariant,
  useVariants,
} from "@/lib/queries/campaigns";

import { CreativeThumbBrief } from "./creative-brief";
import { PanelSection } from "./fields";
import { useEditorStore } from "./store";
import { VARIANT_STATUS_META } from "./labels";

/** Доля показа варианта: вес среди включённых в ротацию (распределение детерминировано по video_uid). */
export function variantShares(variants: readonly Pick<ApiVariant, "id" | "status" | "weight">[]): Map<number, number> {
  const active = variants.filter((v) => v.status === "active");
  const total = active.reduce((sum, v) => sum + v.weight, 0);
  const out = new Map<number, number>();
  for (const v of variants) out.set(v.id, v.status === "active" && total > 0 ? (v.weight / total) * 100 : 0);
  return out;
}

export function WeightInput({
  variant,
  disabled,
  onSaved,
}: {
  variant: Pick<ApiVariant, "id" | "weight" | "code">;
  disabled?: boolean;
  onSaved?: (variant: ApiVariant) => void;
}) {
  const update = useUpdateVariant();
  const [text, setText] = useState<string | null>(null);
  const value = text ?? String(variant.weight);
  const commit = async () => {
    if (text === null) return;
    const next = Number(text);
    setText(null);
    if (!Number.isInteger(next) || next < 0 || next > 1000 || next === variant.weight) return;
    try {
      const saved = await update.mutateAsync({ id: variant.id, body: { weight: next } });
      onSaved?.(saved);
    } catch (error) {
      toast.error(`Вес варианта ${variant.code} не сохранён`, { description: errorMessage(error) });
    }
  };
  return (
    <input
      type="text"
      inputMode="numeric"
      aria-label={`Вес варианта ${variant.code}`}
      className="tabular h-6 w-14 rounded-md border border-border bg-bg px-1.5 text-right font-mono text-xs text-fg focus-visible:border-accent disabled:opacity-50"
      value={value}
      disabled={disabled || update.isPending}
      data-testid={`variant-weight-${variant.code}`}
      onChange={(event) => setText(event.target.value.replace(/[^0-9]/g, ""))}
      onBlur={() => void commit()}
      onKeyDown={(event) => {
        if (event.key === "Enter") (event.target as HTMLInputElement).blur();
        if (event.key === "Escape") setText(null);
      }}
    />
  );
}

/**
 * Варианты A/B кампании рядом с редактором: разные креативы и/или позиции и тайминги с весами.
 * Переключение открывает вариант в редакторе; «Копия для A/B» — тот же вариант с новым кодом.
 */
export function VariantSwitcher({
  campaignId,
  currentId,
  readOnly,
  onBeforeLeave,
  saveFirst,
}: {
  campaignId: number;
  currentId: number;
  readOnly: boolean;
  onBeforeLeave?: () => boolean;
  /** Сохранить черновик редактора перед копией (true — сохранено или нечего сохранять). */
  saveFirst?: () => Promise<boolean>;
}) {
  const router = useRouter();
  const variants = useVariants(campaignId);
  const duplicate = useDuplicateVariant();
  const items = variants.data ?? [];
  const shares = variantShares(items);

  const onDuplicate = async () => {
    // Копия делается с сохранённой версии — сначала сохраняем правки редактора.
    if (saveFirst && !(await saveFirst())) {
      toast.warning("Сначала сохраните правки варианта");
      return;
    }
    const code = nextVariantCode(items.map((v) => v.code));
    try {
      const copy = await duplicate.mutateAsync({ id: currentId, code });
      toast.success(`Вариант ${copy.code} создан`, { description: "Смените креатив или позицию — это и будет A/B." });
      router.push(routeHref("/products/banner", { variant: String(copy.id) }));
    } catch (error) {
      toast.error("Копия не создана", { description: errorMessage(error) });
    }
  };

  return (
    <PanelSection
      title="Варианты A/B"
      testId="variant-switcher"
      actions={
        readOnly ? null : (
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="xs"
              onClick={() => void onDuplicate()}
              disabled={duplicate.isPending}
              title="Копия текущего варианта с новым кодом"
            >
              <Copy className="h-3 w-3" aria-hidden="true" />
              Копия
            </Button>
            <Link
              href={routeHref("/products/banner", { campaign: String(campaignId) })}
              className="inline-flex h-6 items-center gap-1 rounded-md px-2 text-xs text-fg hover:bg-surface-2"
              title="Новый вариант из другого креатива"
              onClick={(event) => {
                if (onBeforeLeave && !onBeforeLeave()) event.preventDefault();
              }}
            >
              <Plus className="h-3 w-3" aria-hidden="true" />
              Новый
            </Link>
          </div>
        )
      }
    >
      {variants.isPending ? (
        <p className="text-xs text-fg-muted">Загрузка вариантов…</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {items.map((variant) => {
            const current = variant.id === currentId;
            return (
              <li
                key={variant.id}
                className={cn(
                  "flex items-center gap-2 rounded-md border px-1.5 py-1",
                  current ? "border-accent/60 bg-accent/5" : "border-border",
                )}
                data-testid="variant-row"
                data-variant-code={variant.code}
              >
                <CreativeThumbBrief creative={variant.creatives[0]} className="h-8 w-8 shrink-0" />
                <div className="flex min-w-0 flex-1 flex-col">
                  {current ? (
                    <span className="truncate text-xs font-semibold text-fg">
                      <span className="font-mono">{variant.code}</span> · {variant.name || "без имени"}
                    </span>
                  ) : (
                    <Link
                      href={routeHref("/products/banner", { variant: String(variant.id) })}
                      className="truncate text-xs text-fg hover:text-accent"
                      onClick={(event) => {
                        if (onBeforeLeave && !onBeforeLeave()) event.preventDefault();
                      }}
                    >
                      <span className="font-mono">{variant.code}</span> · {variant.name || "без имени"}
                    </Link>
                  )}
                  <span className="flex items-center gap-1.5">
                    <StatusBadge status={variant.status} map={VARIANT_STATUS_META} className="px-1 text-[10px]" />
                    <span className="tabular font-mono text-[10px] text-fg-muted">
                      {variant.status === "active"
                        ? `${Math.round(shares.get(variant.id) ?? 0)}% роликов`
                        : "не в ротации"}
                    </span>
                  </span>
                </div>
                <WeightInput
                  variant={variant}
                  disabled={readOnly}
                  onSaved={(saved) => {
                    // Правка веса поднимает версию варианта: редактор берёт её, иначе автосохранение получит 409.
                    if (saved.id === currentId)
                      useEditorStore.getState().setDoc({ version: saved.version, weight: saved.weight });
                  }}
                />
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-2xs text-fg-muted">Вес 0–1000; доля считается среди вариантов «В ротации».</p>
    </PanelSection>
  );
}
