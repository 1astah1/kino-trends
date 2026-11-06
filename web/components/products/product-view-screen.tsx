"use client";

import { Archive, ArrowLeft, ExternalLink, Hourglass } from "lucide-react";
import Link from "next/link";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  DetailGrid,
  EmptyState,
  KeyValue,
  SectionCard,
  Skeleton,
  StatusBadge,
  tabPanelId,
  Tabs,
} from "@/components/ui";
import { canOperate, isAdmin } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { MILESTONE_HINT, routeHref } from "@/lib/nav";
import { useArchiveProduct, useProduct } from "@/lib/queries/products";

import { CampaignsPanel } from "./campaigns-panel";
import { PRODUCT_KIND_LABELS, PRODUCT_STATUS_META, REDIRECT_MODE_LABELS } from "./labels";
import { ProductForm } from "./product-form";
import { WebhookTab } from "./webhook-tab";

const TABS = ["overview", "banners", "stats", "webhook"] as const;
type TabId = (typeof TABS)[number];
const TAB_LABELS: Record<TabId, string> = {
  overview: "Карточка",
  banners: "Кампании и баннеры",
  stats: "A/B и статистика",
  webhook: "Вебхук конверсий",
};
const ID_PREFIX = "product";

/** Карточка продукта (§7.3 `/products/view?id=&tab=`): лендинг, редиректор, бренд; кампании и варианты. */
export function ProductViewScreen() {
  const [id] = useQueryState("id");
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TABS).withDefault("overview").withOptions({ history: "replace" }),
  );
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const product = useProduct(id);
  const archive = useArchiveProduct(id ?? "");

  if (!id) {
    return (
      <div className="p-4 md:p-6">
        <EmptyState
          title="Продукт не выбран"
          action={
            <Link href={routeHref("/products")} className="text-sm text-accent hover:underline">
              К списку продуктов
            </Link>
          }
        />
      </div>
    );
  }

  const p = product.data;
  const sample = p?.redirect_host ? `https://${p.redirect_host}/k7m2q` : null;

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="product-view">
      <Link
        href={routeHref("/products")}
        className="inline-flex items-center gap-1 self-start text-xs text-fg-muted hover:text-fg"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        Все продукты
      </Link>
      {product.isError ? (
        <SectionError error={product.error} onRetry={() => void product.refetch()} title="Продукт не загрузился" />
      ) : !p ? (
        <Skeleton className="h-16 w-full max-w-xl" />
      ) : (
        <PageHeader
          eyebrow={<span className="font-mono normal-case">{p.id}</span>}
          title={p.name}
          description={`${PRODUCT_KIND_LABELS[p.kind]} · ${REDIRECT_MODE_LABELS[p.redirect_mode]}${p.brand.display_domain ? ` · в кадре ${p.brand.display_domain}` : ""}`}
          actions={
            <>
              <StatusBadge status={p.status} map={PRODUCT_STATUS_META} />
              {operator && p.status === "active" ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={archive.isPending}
                  onClick={() => {
                    if (!window.confirm(`Отправить «${p.name}» в архив? Новые ролики перестанут его рекламировать.`))
                      return;
                    archive.mutate(undefined, { onSuccess: () => toast.success("Продукт в архиве") });
                  }}
                >
                  <Archive className="h-3.5 w-3.5" aria-hidden="true" />В архив
                </Button>
              ) : null}
            </>
          }
        />
      )}

      <Tabs
        idPrefix={ID_PREFIX}
        label="Разделы продукта"
        value={tab}
        onChange={(value) => void setTab(value as TabId)}
        items={TABS.map((value) => ({
          value,
          label: TAB_LABELS[value],
          badge:
            value === "stats" ? (
              <span className="font-mono text-2xs text-fg-muted/70 uppercase" aria-label="скоро">
                M4
              </span>
            ) : undefined,
        }))}
      />

      <section id={tabPanelId(ID_PREFIX, tab)} role="tabpanel" aria-labelledby={`${ID_PREFIX}-tab-${tab}`}>
        {tab === "banners" ? (
          <CampaignsPanel productId={id} operator={operator} />
        ) : tab === "webhook" ? (
          <WebhookTab productId={id} operator={operator} admin={isAdmin(user.role)} />
        ) : tab === "overview" ? (
          p ? (
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
              <SectionCard title="Карточка продукта">
                <ProductForm key={p.updated_at} product={p} readOnly={!operator} onDone={() => undefined} />
              </SectionCard>
              <div className="flex flex-col gap-4">
                <SectionCard title="Куда ведёт ролик">
                  <DetailGrid className="sm:grid-cols-1">
                    <KeyValue
                      label="Ссылка из ролика"
                      value={
                        sample ? <span className="font-mono text-xs">{sample}</span> : "не задан домен редиректора"
                      }
                    />
                    <KeyValue label="Режим" value={REDIRECT_MODE_LABELS[p.redirect_mode]} />
                    <KeyValue
                      label="Лендинг продукта"
                      value={
                        p.landing_url ? (
                          <span className="inline-flex items-center gap-1 font-mono text-xs">
                            {p.landing_url}
                            <ExternalLink className="h-3 w-3 text-fg-muted" aria-hidden="true" />
                          </span>
                        ) : (
                          "—"
                        )
                      }
                    />
                    <KeyValue
                      label="Telegram"
                      value={
                        p.telegram_bot_username ? (
                          <span className="font-mono text-xs">
                            tg://resolve?domain={p.telegram_bot_username}&start=k7m2q_l
                          </span>
                        ) : (
                          "—"
                        )
                      }
                    />
                    <KeyValue
                      label="UTM"
                      value={
                        <span className="font-mono text-xs">
                          utm_source=yt|vk · utm_medium=shorts · utm_campaign=&lt;код&gt;
                        </span>
                      }
                    />
                  </DetailGrid>
                </SectionCard>
                <SectionCard title="Вебхук">
                  <p className="text-sm text-fg-muted">
                    Секрет{" "}
                    {p.webhook_secret_set ? (
                      <Badge variant="positive">задан</Badge>
                    ) : (
                      <Badge variant="warning">не задан</Badge>
                    )}
                    .{" "}
                    <Link
                      href={routeHref("/products/view", { id, tab: "webhook" })}
                      className="text-accent hover:underline"
                    >
                      Инструкция интеграции и тестовое событие
                    </Link>
                  </p>
                </SectionCard>
                <p className="text-2xs text-fg-muted">
                  Создан {formatDateTime(p.created_at, { short: true })} · изменён{" "}
                  {formatDateTime(p.updated_at, { short: true })}
                </p>
              </div>
            </div>
          ) : (
            <Skeleton className="h-96" />
          )
        ) : (
          <EmptyState
            icon={Hourglass}
            title="A/B-статистика: скоро"
            description={
              <>
                Strip-plot вариантов по переходам на 1000 просмотров и «рано судить: нужно ещё N» — когда появятся
                метрики публикаций.
                <br />
                <Badge variant="machine" outline className="mt-2">
                  {MILESTONE_HINT.M4}
                </Badge>
              </>
            }
          />
        )}
      </section>
    </div>
  );
}
