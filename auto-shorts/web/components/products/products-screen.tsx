"use client";

import { Megaphone, PanelsTopLeft, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { useState } from "react";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import { Button, DataTable, type DataTableColumn, Dialog, PillToggle, StatusBadge } from "@/components/ui";
import { canOperate } from "@/lib/auth";
import { formatNumber } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import { useCampaigns } from "@/lib/queries/campaigns";
import { type Product, useProducts } from "@/lib/queries/products";

import { formatMinor, PRODUCT_KIND_LABELS, PRODUCT_STATUS_META } from "./labels";
import { ProductForm } from "./product-form";

const STATUS_FILTERS = ["active", "archived", "all"] as const;

/** Продукты и баннеры (§7.3 `/products`): что рекламируем, куда ведём и чем в кадре. */
export function ProductsScreen() {
  const user = useCurrentUser();
  const router = useRouter();
  const operator = canOperate(user.role);
  const [status, setStatus] = useQueryState(
    "status",
    parseAsStringLiteral(STATUS_FILTERS).withDefault("active").withOptions({ history: "replace" }),
  );
  const products = useProducts({ status });
  const campaigns = useCampaigns();
  const [creating, setCreating] = useState(false);

  const campaignCount = new Map<string, { total: number; active: number }>();
  for (const campaign of campaigns.data?.items ?? []) {
    const entry = campaignCount.get(campaign.product_id) ?? { total: 0, active: 0 };
    entry.total += 1;
    if (campaign.status === "active") entry.active += 1;
    campaignCount.set(campaign.product_id, entry);
  }

  const columns: Array<DataTableColumn<Product>> = [
    {
      key: "name",
      header: "Продукт",
      render: (p) => (
        <Link
          href={routeHref("/products/view", { id: p.id })}
          className="flex flex-col hover:text-accent"
          onClick={(e) => e.stopPropagation()}
        >
          <span className="font-medium">{p.name}</span>
          <span className="font-mono text-2xs text-fg-muted">{p.id}</span>
        </Link>
      ),
    },
    { key: "kind", header: "Тип", render: (p) => <span className="text-fg-muted">{PRODUCT_KIND_LABELS[p.kind]}</span> },
    {
      key: "host",
      header: "Редиректор",
      render: (p) => <span className="font-mono text-xs">{p.redirect_host ?? "—"}</span>,
    },
    {
      key: "domain",
      header: "В кадре",
      render: (p) => <span className="font-mono text-xs">{p.brand.display_domain ?? "—"}</span>,
    },
    {
      key: "campaigns",
      header: "Кампании",
      className: "text-right",
      render: (p) => {
        const entry = campaignCount.get(p.id);
        return entry ? (
          <span className="tabular font-mono text-xs">
            {entry.active}
            <span className="text-fg-muted"> / {entry.total}</span>
          </span>
        ) : (
          <span className="text-fg-muted">—</span>
        );
      },
    },
    {
      key: "clicks",
      header: "Переходы 7д",
      className: "text-right",
      render: (p) => <span className="tabular font-mono text-xs">{formatNumber(p.stats_7d?.clicks)}</span>,
    },
    {
      key: "visitors",
      header: "Посетители",
      className: "text-right",
      render: (p) => <span className="tabular font-mono text-xs">{formatNumber(p.stats_7d?.unique_visitors)}</span>,
    },
    {
      key: "starts",
      header: "Старты бота",
      className: "text-right",
      render: (p) => <span className="tabular font-mono text-xs">{formatNumber(p.stats_7d?.bot_starts)}</span>,
    },
    {
      key: "revenue",
      header: "Выручка 7д",
      className: "text-right",
      render: (p) => (
        <span className="tabular font-mono text-xs">
          {formatMinor(p.stats_7d?.revenue_minor, p.stats_7d?.currency)}
        </span>
      ),
    },
    { key: "status", header: "Статус", render: (p) => <StatusBadge status={p.status} map={PRODUCT_STATUS_META} /> },
  ];

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="products-screen">
      <PageHeader
        eyebrow="Продвижение"
        title="Продукты и баннеры"
        description="Что рекламируем, куда ведёт ссылка из ролика и какой баннер в кадре. Баннер — ваш загруженный креатив, варианты A/B живут в кампаниях продукта."
        actions={
          <>
            <Link
              href={routeHref("/products/banner")}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-sm hover:bg-surface-2"
            >
              <PanelsTopLeft className="h-4 w-4" aria-hidden="true" />
              Редактор баннера
            </Link>
            {operator ? (
              <Button onClick={() => setCreating(true)} data-testid="create-product">
                <Plus className="h-4 w-4" aria-hidden="true" />
                Продукт
              </Button>
            ) : null}
          </>
        }
      />
      <PillToggle
        label="Статус продуктов"
        options={[
          { value: "active", label: "Активные" },
          { value: "archived", label: "Архив" },
          { value: "all", label: "Все" },
        ]}
        value={status}
        onChange={(value) => void setStatus(value)}
        className="self-start"
      />
      {products.isError ? (
        <SectionError error={products.error} onRetry={() => void products.refetch()} />
      ) : (
        <DataTable
          caption="Продукты"
          columns={columns}
          rows={products.data?.items ?? []}
          rowKey={(p) => p.id}
          onRowClick={(p) => router.push(routeHref("/products/view", { id: p.id }))}
          isLoading={products.isPending}
          emptyTitle="Продуктов нет"
          emptyDescription="Добавьте продукт — например, umka.chat или VPN-бот."
          minWidth={980}
        />
      )}
      <p className="flex items-center gap-1.5 text-2xs text-fg-muted">
        <Megaphone className="h-3 w-3" aria-hidden="true" />
        Переходы и конверсии за 7 дней считает редиректор и вебхук продукта (M3); пока данных нет — прочерк.
      </p>
      <Dialog
        open={creating}
        onClose={() => setCreating(false)}
        title="Новый продукт"
        description="id — латиницей, менять нельзя: по нему связаны ссылки и конверсии."
        className="max-w-2xl"
      >
        {creating ? (
          <ProductForm
            product={null}
            onCancel={() => setCreating(false)}
            onDone={(product) => {
              setCreating(false);
              router.push(routeHref("/products/view", { id: product.id }));
            }}
          />
        ) : null}
      </Dialog>
    </div>
  );
}
