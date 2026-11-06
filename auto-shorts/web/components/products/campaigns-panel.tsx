"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Copy, PanelsTopLeft, Pencil, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import * as z from "zod/mini";

import { CreativeThumbBrief } from "@/components/banner-editor/creative-brief";
import { validationCodeLabel, VARIANT_STATUS_META } from "@/components/banner-editor/labels";
import { variantShares, WeightInput } from "@/components/banner-editor/variant-switcher";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  Card,
  DataTable,
  type DataTableColumn,
  Dialog,
  EmptyState,
  FormField,
  Input,
  Select,
  StatusBadge,
  Textarea,
} from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { errorMessage, ValidationError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import { useBannerTemplates } from "@/lib/queries/banners";
import {
  type ApiVariant,
  type Campaign,
  nextVariantCode,
  useCampaigns,
  useCreateCampaign,
  useDuplicateVariant,
  useSetCampaignStatus,
  useSetVariantStatus,
  useUpdateCampaign,
  useVariants,
} from "@/lib/queries/campaigns";

import { applyServerErrors } from "./form-utils";
import { CAMPAIGN_ALLOCATION_LABELS, CAMPAIGN_STATUS_META, JOB_KIND_LABELS, PLATFORM_LABELS } from "./labels";

const ALLOCATIONS = Object.keys(CAMPAIGN_ALLOCATION_LABELS) as [
  Schemas["CampaignAllocation"],
  ...Schemas["CampaignAllocation"][],
];
const PLATFORMS = Object.keys(PLATFORM_LABELS) as Schemas["Platform"][];
const KINDS = Object.keys(JOB_KIND_LABELS) as Schemas["JobKind"][];
const CAMPAIGN_STATUSES = Object.keys(CAMPAIGN_STATUS_META) as Schemas["CampaignStatus"][];

/** Кампании продукта и их варианты A/B (вес, статус, креатив), вход в редактор баннера. */
export function CampaignsPanel({ productId, operator }: { productId: string; operator: boolean }) {
  const campaigns = useCampaigns({ productId });
  const [editing, setEditing] = useState<Campaign | "new" | null>(null);
  const items = campaigns.data?.items ?? [];

  return (
    <div className="flex flex-col gap-3" data-testid="campaigns-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">Кампании и варианты баннера</h2>
          <p className="text-xs text-fg-muted">
            Вариант — загруженный креатив с позицией и таймингом. Ролик получает вариант по весам, детерминированно по
            video_uid.
          </p>
        </div>
        {operator ? (
          <Button onClick={() => setEditing("new")} data-testid="create-campaign">
            <Plus className="h-4 w-4" aria-hidden="true" />
            Кампания
          </Button>
        ) : null}
      </div>
      {campaigns.isError ? (
        <SectionError error={campaigns.error} onRetry={() => void campaigns.refetch()} />
      ) : campaigns.isPending ? (
        <Card className="h-32 animate-pulse" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={PanelsTopLeft}
          title="Кампаний нет"
          description="Создайте кампанию, затем загрузите креатив — он станет вариантом A."
          action={
            operator ? (
              <Button onClick={() => setEditing("new")}>
                <Plus className="h-4 w-4" aria-hidden="true" />
                Кампания
              </Button>
            ) : undefined
          }
        />
      ) : (
        items.map((campaign) => (
          <CampaignCard key={campaign.id} campaign={campaign} operator={operator} onEdit={() => setEditing(campaign)} />
        ))
      )}
      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Новая кампания" : "Кампания"}
        description="Таргетинг: пустой список — без ограничения. Меньший приоритет выигрывает, если подходят несколько кампаний."
        className="max-w-xl"
      >
        {editing ? (
          <CampaignForm
            productId={productId}
            campaign={editing === "new" ? null : editing}
            onDone={() => setEditing(null)}
            onCancel={() => setEditing(null)}
          />
        ) : null}
      </Dialog>
    </div>
  );
}

function TargetingChips({ targeting }: { targeting: Schemas["CampaignTargeting"] }) {
  const chips: string[] = [];
  for (const p of targeting.platforms ?? []) chips.push(PLATFORM_LABELS[p]);
  for (const k of targeting.kinds ?? []) chips.push(JOB_KIND_LABELS[k]);
  for (const n of targeting.niche_ids ?? []) chips.push(`ниша ${n}`);
  for (const c of targeting.channel_ids ?? []) chips.push(`канал ${c}`);
  if (!chips.length) return <span className="text-2xs text-fg-muted">все ролики продукта</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {chips.map((chip) => (
        <Badge key={chip} variant="neutral" outline>
          {chip}
        </Badge>
      ))}
    </span>
  );
}

function CampaignCard({ campaign, operator, onEdit }: { campaign: Campaign; operator: boolean; onEdit: () => void }) {
  const router = useRouter();
  const variants = useVariants(campaign.id);
  const setCampaignStatus = useSetCampaignStatus();
  const setVariantStatus = useSetVariantStatus();
  const duplicate = useDuplicateVariant();
  const items = variants.data ?? [];
  const shares = variantShares(items);

  const changeVariantStatus = async (variant: ApiVariant, status: Schemas["VariantStatus"]) => {
    try {
      await setVariantStatus.mutateAsync({ id: variant.id, status });
    } catch (error) {
      if (error instanceof ValidationError && Array.isArray(error.details.warnings)) {
        const codes = (error.details.warnings as Array<{ code: string }>).map((w) => validationCodeLabel(w.code));
        toast.error(`Вариант ${variant.code} не включён`, { description: codes.slice(0, 3).join("; ") });
        return;
      }
      toast.error("Статус не изменён", { description: errorMessage(error) });
    }
  };

  const onDuplicate = async (variant: ApiVariant) => {
    try {
      const copy = await duplicate.mutateAsync({ id: variant.id, code: nextVariantCode(items.map((v) => v.code)) });
      router.push(routeHref("/products/banner", { variant: String(copy.id) }));
    } catch (error) {
      toast.error("Копия не создана", { description: errorMessage(error) });
    }
  };

  const columns: Array<DataTableColumn<ApiVariant>> = [
    {
      key: "creative",
      header: <span className="sr-only">Креатив</span>,
      className: "w-12",
      render: (v) => <CreativeThumbBrief creative={v.creatives[0]} className="h-9 w-9" />,
    },
    {
      key: "code",
      header: "Вариант",
      render: (v) => (
        <Link
          href={routeHref("/products/banner", { variant: String(v.id) })}
          className="flex flex-col hover:text-accent"
          data-testid={`variant-link-${v.code}`}
        >
          <span>
            <span className="font-mono font-semibold">{v.code}</span> · {v.name || "без имени"}
          </span>
          <span className="text-2xs text-fg-muted">{v.creatives[0]?.name ?? "запасная плашка"}</span>
        </Link>
      ),
    },
    {
      key: "status",
      header: "Статус",
      render: (v) =>
        operator ? (
          <select
            aria-label={`Статус варианта ${v.code}`}
            className="h-7 rounded-md border border-border bg-bg px-1.5 text-xs"
            value={v.status}
            disabled={setVariantStatus.isPending}
            onChange={(event) => void changeVariantStatus(v, event.target.value as Schemas["VariantStatus"])}
          >
            {(Object.keys(VARIANT_STATUS_META) as Schemas["VariantStatus"][]).map((status) => (
              <option key={status} value={status}>
                {VARIANT_STATUS_META[status].label}
              </option>
            ))}
          </select>
        ) : (
          <StatusBadge status={v.status} map={VARIANT_STATUS_META} />
        ),
    },
    {
      key: "weight",
      header: "Вес",
      className: "text-right",
      render: (v) =>
        operator && campaign.allocation === "weighted" ? (
          <WeightInput variant={v} />
        ) : (
          <span className="tabular font-mono text-xs">{v.weight}</span>
        ),
    },
    {
      key: "share",
      header: "Доля",
      className: "text-right",
      render: (v) => (
        <span className="tabular font-mono text-xs text-fg-muted">
          {campaign.allocation === "weighted" && v.status === "active" ? `${Math.round(shares.get(v.id) ?? 0)}%` : "—"}
        </span>
      ),
    },
    {
      key: "updated",
      header: "Изменён",
      render: (v) => <span className="text-xs text-fg-muted">{formatDateTime(v.updated_at, { short: true })}</span>,
    },
    {
      key: "actions",
      header: <span className="sr-only">Действия</span>,
      className: "w-px text-right",
      render: (v) => (
        <div className="flex justify-end gap-1">
          <Link
            href={routeHref("/products/banner", { variant: String(v.id) })}
            className="inline-flex h-6 items-center gap-1 rounded-md px-2 text-xs hover:bg-surface-2"
          >
            <PanelsTopLeft className="h-3.5 w-3.5" aria-hidden="true" />
            Редактор
          </Link>
          {operator ? (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => void onDuplicate(v)}
              disabled={duplicate.isPending}
              title="Копия для A/B"
            >
              <Copy className="h-3.5 w-3.5" aria-hidden="true" />
              Копия
            </Button>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <Card className="flex flex-col" data-testid="campaign-card" data-campaign-id={campaign.id}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-3 py-2">
        <h3 className="text-sm font-semibold">{campaign.name}</h3>
        {operator ? (
          <select
            aria-label={`Статус кампании ${campaign.name}`}
            className="h-7 rounded-md border border-border bg-bg px-1.5 text-xs"
            value={campaign.status}
            disabled={setCampaignStatus.isPending}
            onChange={(event) =>
              setCampaignStatus.mutate({ id: campaign.id, status: event.target.value as Schemas["CampaignStatus"] })
            }
          >
            {CAMPAIGN_STATUSES.map((status) => (
              <option key={status} value={status}>
                {CAMPAIGN_STATUS_META[status].label}
              </option>
            ))}
          </select>
        ) : (
          <StatusBadge status={campaign.status} map={CAMPAIGN_STATUS_META} />
        )}
        <span className="text-xs text-fg-muted">{CAMPAIGN_ALLOCATION_LABELS[campaign.allocation]}</span>
        <span className="tabular font-mono text-2xs text-fg-muted">приоритет {campaign.priority}</span>
        <TargetingChips targeting={campaign.targeting} />
        <div className="ml-auto flex items-center gap-1">
          {operator ? (
            <>
              <Button variant="ghost" size="xs" onClick={onEdit}>
                <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                Изменить
              </Button>
              <Link
                href={routeHref("/products/banner", { campaign: String(campaign.id) })}
                className="inline-flex h-6 items-center gap-1 rounded-md bg-accent px-2 text-xs font-medium text-accent-fg hover:bg-accent/90"
                data-testid="new-variant-link"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                Вариант из креатива
              </Link>
            </>
          ) : null}
        </div>
      </div>
      <div className="p-3">
        {variants.isError ? (
          <SectionError error={variants.error} onRetry={() => void variants.refetch()} />
        ) : (
          <DataTable
            caption={`Варианты кампании ${campaign.name}`}
            columns={columns}
            rows={items}
            rowKey={(v) => String(v.id)}
            isLoading={variants.isPending}
            emptyTitle="Вариантов нет"
            emptyDescription="Загрузите креатив — сервер соберёт вариант со слоями по умолчанию."
            skeletonRows={2}
            minWidth={760}
          />
        )}
      </div>
    </Card>
  );
}

const campaignSchema = z.object({
  name: z.string().check(z.trim(), z.minLength(1, "Укажите название"), z.maxLength(200, "Не длиннее 200")),
  allocation: z.enum(ALLOCATIONS),
  priority: z.string().check(z.regex(/^\d{1,5}$/, "Целое число")),
  banner_template_id: z.string(),
  platforms: z.array(z.string()),
  kinds: z.array(z.string()),
  niche_ids: z.string(),
  notes: z.string().check(z.maxLength(2000, "Слишком длинно")),
});
type CampaignValues = z.infer<typeof campaignSchema>;

function splitList(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function CampaignForm({
  productId,
  campaign,
  onDone,
  onCancel,
}: {
  productId: string;
  campaign: Campaign | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const create = useCreateCampaign();
  const update = useUpdateCampaign();
  const templates = useBannerTemplates();
  const form = useForm<CampaignValues>({
    resolver: zodResolver(campaignSchema),
    defaultValues: {
      name: campaign?.name ?? "",
      allocation: campaign?.allocation ?? "weighted",
      priority: String(campaign?.priority ?? 100),
      banner_template_id: campaign?.banner_template_id ? String(campaign.banner_template_id) : "",
      platforms: campaign?.targeting.platforms ?? [],
      kinds: campaign?.targeting.kinds ?? [],
      niche_ids: (campaign?.targeting.niche_ids ?? []).join(", "),
      notes: campaign?.notes ?? "",
    },
  });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    const body = {
      name: values.name,
      allocation: values.allocation,
      priority: Number(values.priority),
      banner_template_id: values.banner_template_id ? Number(values.banner_template_id) : null,
      targeting: {
        platforms: values.platforms as Schemas["Platform"][],
        kinds: values.kinds as Schemas["JobKind"][],
        niche_ids: splitList(values.niche_ids),
        channel_ids: campaign?.targeting.channel_ids ?? [],
      },
      notes: values.notes.trim() || null,
    };
    try {
      if (campaign) await update.mutateAsync({ id: campaign.id, body });
      else await create.mutateAsync({ product_id: productId, ...body });
      toast.success(campaign ? "Кампания сохранена" : `Кампания «${values.name}» создана`);
      onDone();
    } catch (error) {
      applyServerErrors(error, form.setError, ["name", "allocation", "priority", "banner_template_id", "notes"]);
    }
  });

  // Список шаблонов приходит асинхронно: неуправляемый <select> монтируем, когда варианты уже есть.
  if (templates.isPending) return <Card className="h-64 animate-pulse" aria-label="Загрузка" />;
  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3" noValidate data-testid="campaign-form">
      <FormField label="Название" htmlFor="campaign-name" error={errors.name?.message}>
        <Input id="campaign-name" autoFocus aria-invalid={!!errors.name} {...form.register("name")} />
      </FormField>
      <div className="grid gap-3 sm:grid-cols-2">
        <FormField label="Распределение вариантов" htmlFor="campaign-allocation" error={errors.allocation?.message}>
          <Select id="campaign-allocation" {...form.register("allocation")}>
            {ALLOCATIONS.map((value) => (
              <option key={value} value={value}>
                {CAMPAIGN_ALLOCATION_LABELS[value]}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField
          label="Приоритет"
          htmlFor="campaign-priority"
          error={errors.priority?.message}
          hint="Меньше — важнее"
        >
          <Input id="campaign-priority" inputMode="numeric" className="font-mono" {...form.register("priority")} />
        </FormField>
      </div>
      <FormField
        label="Шаблон для новых вариантов (необязательно)"
        htmlFor="campaign-template"
        error={errors.banner_template_id?.message}
        hint="Креатив нового варианта встанет в зону оффера шаблона"
      >
        <Select id="campaign-template" {...form.register("banner_template_id")}>
          <option value="">Без шаблона — раскладка по умолчанию</option>
          {(templates.data ?? []).map((tpl) => (
            <option key={tpl.id} value={String(tpl.id)}>
              {tpl.name}
            </option>
          ))}
        </Select>
      </FormField>
      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-xs font-medium text-fg-muted">Таргетинг</legend>
        <div className="flex flex-wrap gap-3 text-sm">
          {PLATFORMS.map((platform) => (
            <label key={platform} className="flex items-center gap-1.5">
              <input
                type="checkbox"
                className="h-4 w-4 accent-[var(--accent)]"
                value={platform}
                {...form.register("platforms")}
              />
              {PLATFORM_LABELS[platform]}
            </label>
          ))}
          {KINDS.map((kind) => (
            <label key={kind} className="flex items-center gap-1.5">
              <input
                type="checkbox"
                className="h-4 w-4 accent-[var(--accent)]"
                value={kind}
                {...form.register("kinds")}
              />
              {JOB_KIND_LABELS[kind]}
            </label>
          ))}
        </div>
        <Input
          aria-label="Ниши через запятую"
          placeholder="Ниши через запятую (пусто — все)"
          className="font-mono"
          {...form.register("niche_ids")}
        />
      </fieldset>
      <FormField label="Заметки" htmlFor="campaign-notes" error={errors.notes?.message}>
        <Textarea id="campaign-notes" rows={2} {...form.register("notes")} />
      </FormField>
      {errors.root?.server?.message ? (
        <p
          role="alert"
          className="rounded-md border border-negative/40 bg-negative/10 px-2 py-1.5 text-xs text-negative"
        >
          {errors.root.server.message}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Отмена
        </Button>
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Сохраняю…" : campaign ? "Сохранить" : "Создать"}
        </Button>
      </div>
    </form>
  );
}
