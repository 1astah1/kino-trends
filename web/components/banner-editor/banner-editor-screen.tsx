"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CircleCheck, Film, LoaderCircle, PanelsTopLeft, PauseCircle, PlayCircle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { parseAsInteger, useQueryState } from "nuqs";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import { Badge, Button, Card, EmptyState, Skeleton, StatusBadge } from "@/components/ui";
import { ApiError, errorMessage, ValidationError } from "@/lib/api/errors";
import { canOperate } from "@/lib/auth";
import { routeHref } from "@/lib/nav";
import { bannerKeys, useBannerTemplate, useBannerTemplates, useUpdateBannerTemplate } from "@/lib/queries/banners";
import {
  type ApiVariant,
  nextVariantCode,
  useCampaign,
  useCampaigns,
  useCreateVariant,
  useSetVariantStatus,
  useUpdateVariant,
  useVariant,
  useVariants,
} from "@/lib/queries/campaigns";
import { type BannerCreative, useCreative } from "@/lib/queries/creatives";
import { useProduct, useProducts } from "@/lib/queries/products";

import { CreativeLibrary, CreativeUploader } from "./creative-library";
import { docFromVariant, draftFrom, type EditorDoc, type EditorDraft } from "./draft";
import { EditorWorkspace, type SaveState } from "./editor-workspace";
import { rejectCodeLabel, validationCodeLabel, VARIANT_STATUS_META } from "./labels";
import { useEditorStore } from "./store";
import { VariantSwitcher } from "./variant-switcher";

/** Редактор баннера (§7.3 `/products/banner`, `?variant=` | `?template=` | `?campaign=`). */
export function BannerEditorScreen() {
  const [variantId] = useQueryState("variant", parseAsInteger);
  const [templateId] = useQueryState("template", parseAsInteger);
  const [campaignId] = useQueryState("campaign", parseAsInteger);

  useEffect(() => () => useEditorStore.getState().reset(), []);

  if (variantId) return <VariantEditor key={`v${variantId}`} id={variantId} />;
  if (templateId) return <TemplateEditor key={`t${templateId}`} id={templateId} />;
  if (campaignId) return <NewVariantFromCreative key={`c${campaignId}`} campaignId={campaignId} />;
  return <EditorChooser />;
}

function EditorSkeleton() {
  return (
    <div className="flex h-full flex-col gap-3 p-4" aria-busy="true" aria-label="Загрузка редактора">
      <Skeleton className="h-8 w-80" />
      <div className="grid flex-1 gap-3 lg:grid-cols-[264px_1fr_328px]">
        <Skeleton className="h-96" />
        <Skeleton className="mx-auto aspect-[9/16] h-[70vh]" />
        <Skeleton className="h-96" />
      </div>
    </div>
  );
}

function SaveStateHint({ state }: { state: SaveState }) {
  return state === "conflict" ? <Badge variant="warning">конфликт</Badge> : null;
}

// ---------------------------------------------------------------- вариант кампании

function VariantEditor({ id }: { id: number }) {
  const user = useCurrentUser();
  const readOnly = !canOperate(user.role);
  const variant = useVariant(id);
  const campaign = useCampaign(variant.data?.campaign_id);
  const product = useProduct(variant.data?.product_id);
  const updateVariant = useUpdateVariant();
  const setStatus = useSetVariantStatus();
  const docId = useEditorStore((state) => (state.doc?.kind === "variant" ? state.doc.id : null));
  const doc = useEditorStore((state) => state.doc);
  const name = useEditorStore((state) => state.draft?.name ?? "");
  const { load, update, setDoc } = useEditorStore.getState();

  useEffect(() => {
    if (variant.data && docId !== variant.data.id) load(docFromVariant(variant.data), draftFrom(variant.data));
  }, [variant.data, docId, load]);

  const save = useCallback(
    async (draft: EditorDraft, current: EditorDoc) => {
      const saved = await updateVariant.mutateAsync({
        id: current.id,
        body: {
          name: draft.name,
          layers: draft.layers,
          timing: draft.timing,
          ad_marking: draft.ad_marking,
          code_url_template: draft.code_url_template,
          expected_version: current.version,
        },
      });
      return docFromVariant(saved);
    },
    [updateVariant],
  );

  const refetchVariant = variant.refetch;
  const reload = useCallback(async () => {
    const result = await refetchVariant();
    const fresh = result.data;
    return fresh ? { doc: docFromVariant(fresh), draft: draftFrom(fresh) } : null;
  }, [refetchVariant]);

  if (variant.isError) {
    return (
      <div className="p-4 md:p-6">
        <SectionError error={variant.error} onRetry={() => void variant.refetch()} title="Вариант не загрузился" />
      </div>
    );
  }
  if (!variant.data || docId !== variant.data.id) return <EditorSkeleton />;
  const v = variant.data;

  const changeStatus = async (status: "active" | "paused" | "archived", saveNow: () => Promise<boolean>) => {
    if (!(await saveNow())) {
      toast.warning("Сначала сохраните правки");
      return;
    }
    try {
      const next = await setStatus.mutateAsync({ id, status });
      setDoc({ status: next.status, version: next.version });
      toast.success(
        status === "active" ? `Вариант ${next.code} в ротации` : `Статус: ${VARIANT_STATUS_META[next.status].label}`,
      );
    } catch (error) {
      if (error instanceof ValidationError && Array.isArray(error.details.warnings)) {
        const codes = (error.details.warnings as Array<{ code: string }>).map((w) => validationCodeLabel(w.code));
        toast.error("Вариант не включён: есть ошибки проверки", { description: codes.slice(0, 3).join("; ") });
        return;
      }
      toast.error("Статус не изменён", { description: errorMessage(error) });
    }
  };

  const status = (doc?.status ?? v.status) as ApiVariant["status"];

  return (
    <EditorWorkspace
      save={save}
      reload={reload}
      readOnly={readOnly}
      microBrandText={product.data?.brand.display_domain ?? undefined}
      header={({ saveState, saveNow }) => (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <Link
            href={routeHref("/products/view", { id: v.product_id, tab: "banners" })}
            className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            {product.data?.name ?? v.product_id}
            {campaign.data ? ` · ${campaign.data.name}` : ""}
          </Link>
          <h1 className="flex items-center gap-2 text-base font-semibold text-fg">
            <PanelsTopLeft className="h-4 w-4 text-accent" aria-hidden="true" />
            <span className="font-mono">{v.code}</span>
          </h1>
          <input
            aria-label="Название варианта"
            className="h-7 w-56 min-w-0 rounded-md border border-transparent bg-transparent px-1.5 text-sm text-fg hover:border-border focus-visible:border-accent disabled:opacity-80"
            value={name}
            maxLength={200}
            disabled={readOnly}
            placeholder="Название варианта"
            onChange={(event) => update((d) => ({ ...d, name: event.target.value }), { coalesce: "name" })}
          />
          <StatusBadge status={status} map={VARIANT_STATUS_META} />
          <SaveStateHint state={saveState} />
          {readOnly ? null : (
            <div className="flex items-center gap-1">
              {status !== "active" ? (
                <Button
                  size="xs"
                  variant="outline"
                  onClick={() => void changeStatus("active", saveNow)}
                  disabled={setStatus.isPending}
                  data-testid="activate-variant"
                >
                  <PlayCircle className="h-3.5 w-3.5" aria-hidden="true" />В ротацию
                </Button>
              ) : (
                <Button
                  size="xs"
                  variant="outline"
                  onClick={() => void changeStatus("paused", saveNow)}
                  disabled={setStatus.isPending}
                >
                  <PauseCircle className="h-3.5 w-3.5" aria-hidden="true" />
                  Пауза
                </Button>
              )}
            </div>
          )}
        </div>
      )}
      variantsPanel={({ confirmLeave, saveNow }) => (
        <VariantSwitcher
          campaignId={v.campaign_id}
          currentId={v.id}
          readOnly={readOnly}
          onBeforeLeave={confirmLeave}
          saveFirst={saveNow}
        />
      )}
    />
  );
}

// ---------------------------------------------------------------- шаблон (пресет варианта)

function TemplateEditor({ id }: { id: number }) {
  const user = useCurrentUser();
  const readOnly = !canOperate(user.role);
  const template = useBannerTemplate(id);
  const updateTemplate = useUpdateBannerTemplate();
  const queryClient = useQueryClient();
  const docId = useEditorStore((state) => (state.doc?.kind === "template" ? state.doc.id : null));
  const { load } = useEditorStore.getState();

  const toDoc = (tpl: NonNullable<typeof template.data>): EditorDoc => ({
    kind: "template",
    id: tpl.id,
    version: tpl.version,
    campaignId: null,
    productId: null,
    status: tpl.status,
    weight: null,
    effectiveCodeUrlTemplate: tpl.layout.code_url_template || "u.umka.chat/{code}",
  });

  useEffect(() => {
    if (template.data && docId !== template.data.id) load(toDoc(template.data), draftFrom(template.data.layout));
    // toDoc — чистая функция от данных
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template.data, docId, load]);

  const save = useCallback(
    async (draft: EditorDraft, current: EditorDoc) => {
      const saved = await updateTemplate.mutateAsync({
        id: current.id,
        body: {
          layout: {
            code: draft.code,
            name: draft.name,
            layers: draft.layers,
            timing: draft.timing,
            ad_marking: draft.ad_marking,
            code_url_template: draft.code_url_template,
          },
          expected_version: current.version,
        },
      });
      return { ...current, version: saved.version, status: saved.status };
    },
    [updateTemplate],
  );

  const reload = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: bannerKeys.template(id) });
    const result = await template.refetch();
    const fresh = result.data;
    return fresh ? { doc: toDoc(fresh), draft: draftFrom(fresh.layout) } : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, queryClient, template]);

  if (template.isError) {
    return (
      <div className="p-4 md:p-6">
        <SectionError error={template.error} onRetry={() => void template.refetch()} title="Шаблон не загрузился" />
      </div>
    );
  }
  if (!template.data || docId !== template.data.id) return <EditorSkeleton />;

  return (
    <EditorWorkspace
      save={save}
      reload={reload}
      readOnly={readOnly}
      header={() => (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <Link
            href={routeHref("/products/banner")}
            className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            Все варианты
          </Link>
          <h1 className="text-base font-semibold text-fg">Шаблон «{template.data.name}»</h1>
          <Badge variant="machine" outline>
            пресет новых вариантов
          </Badge>
        </div>
      )}
    />
  );
}

// ---------------------------------------------------------------- новый вариант из креатива

/**
 * Главный сценарий владельца: загрузил креатив → он встал в кадр 1080×1920. Сервер строит вариант со
 * слоями по умолчанию (микро-бренд, креатив в зоне оффера, код ролика под ним), дальше — редактор.
 */
function NewVariantFromCreative({ campaignId }: { campaignId: number }) {
  const router = useRouter();
  const user = useCurrentUser();
  const readOnly = !canOperate(user.role);
  const campaign = useCampaign(campaignId);
  const variants = useVariants(campaignId);
  const createVariant = useCreateVariant();
  const [pendingSha, setPendingSha] = useState<string | null>(null);
  const pending = useCreative(pendingSha);
  const creatingRef = useRef(false);

  const create = useCallback(
    async (creative: BannerCreative) => {
      if (creatingRef.current) return;
      creatingRef.current = true;
      try {
        const code = nextVariantCode((variants.data ?? []).map((v) => v.code));
        const variant = await createVariant.mutateAsync({
          campaignId,
          body: {
            code,
            name: creative.name.slice(0, 200),
            status: "draft",
            weight: 50,
            creative_sha256: creative.sha256,
          },
        });
        toast.success(`Вариант ${variant.code} создан`, {
          description: "Креатив в зоне оффера — поправьте позицию и тайминг.",
        });
        router.replace(routeHref("/products/banner", { variant: String(variant.id) }));
      } catch (error) {
        creatingRef.current = false;
        setPendingSha(null);
        const message =
          error instanceof ApiError &&
          error.status === 422 &&
          Object.keys((error as ValidationError).fieldErrors ?? {}).length
            ? Object.values((error as ValidationError).fieldErrors).join("; ")
            : errorMessage(error);
        toast.error("Вариант не создан", { description: message });
      }
    },
    [campaignId, createVariant, router, variants.data],
  );

  const onCreative = (creative: BannerCreative) => {
    if (creative.status === "ready") void create(creative);
    else if (creative.status === "processing") setPendingSha(creative.sha256);
    else toast.error("Креатив не принят", { description: creative.error_message ?? undefined });
  };

  // Анимация дообработалась (опрос каждые 3 с) — создаём вариант; вызов вне тела эффекта.
  const readyPending = pending.data?.status === "ready" ? pending.data : null;
  useEffect(() => {
    if (!readyPending) return;
    const id = setTimeout(() => void create(readyPending), 0);
    return () => clearTimeout(id);
  }, [readyPending, create]);
  const pendingFailed = pending.data?.status === "failed" ? pending.data : null;

  if (campaign.isError) {
    return (
      <div className="p-4 md:p-6">
        <SectionError error={campaign.error} onRetry={() => void campaign.refetch()} title="Кампания не найдена" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="new-variant">
      <PageHeader
        eyebrow={
          campaign.data
            ? `Кампания «${campaign.data.name}» · вариант ${nextVariantCode((variants.data ?? []).map((v) => v.code))}`
            : "Новый вариант"
        }
        title="Новый вариант баннера"
        description="Загрузите готовый креатив — он встанет в зону оффера кадра 1080×1920. Дальше двигайте, масштабируйте и проверяйте по safe zones площадок."
        actions={
          campaign.data ? (
            <Link
              href={routeHref("/products/view", { id: campaign.data.product_id, tab: "banners" })}
              className="inline-flex items-center gap-1 text-sm text-fg-muted hover:text-fg"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />К продукту
            </Link>
          ) : null
        }
      />
      {readOnly ? (
        <EmptyState title="Варианты создают оператор и владелец" description="У вас роль наблюдателя." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
          <Card className="flex flex-col gap-3 p-4">
            <CreativeUploader
              productId={campaign.data?.product_id}
              onUploaded={onCreative}
              disabled={createVariant.isPending || (!!pendingSha && !pendingFailed)}
            />
            {pendingFailed ? (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-md border border-negative/40 bg-negative/10 p-2 text-sm text-negative"
              >
                <span className="flex-1">
                  Анимация не прошла нормализацию:{" "}
                  {rejectCodeLabel(pendingFailed.error_code) ?? pendingFailed.error_message ?? "ошибка"}
                </span>
                <Button size="xs" variant="outline" onClick={() => setPendingSha(null)}>
                  Понятно
                </Button>
              </div>
            ) : pendingSha ? (
              <p className="flex items-center gap-2 text-sm text-machine" role="status">
                <Film className="h-4 w-4" aria-hidden="true" />
                <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
                Перекодирую анимацию в WebM с альфой; вариант создастся, как только файл будет готов. Нормализацию
                делает тяжёлый воркер — если ждёте дольше минуты, проверьте его в «Настройки → Система».
              </p>
            ) : null}
            {createVariant.isPending ? (
              <p className="flex items-center gap-2 text-sm text-fg-muted" role="status">
                <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
                Создаю вариант…
              </p>
            ) : null}
          </Card>
          <Card className="flex flex-col gap-2 p-3">
            <h2 className="text-xs font-semibold tracking-wide text-fg-muted uppercase">Или из библиотеки</h2>
            <CreativeLibrary
              productId={campaign.data?.product_id}
              currentShas={[]}
              onPick={onCreative}
              readOnly={createVariant.isPending}
            />
          </Card>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- выбор, что править

function EditorChooser() {
  const products = useProducts({ status: "active" });
  const campaigns = useCampaigns();
  const templates = useBannerTemplates();
  const productName = new Map((products.data?.items ?? []).map((p) => [p.id, p.name]));
  const items = campaigns.data?.items ?? [];
  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      <PageHeader
        title="Редактор баннера"
        description="Выберите вариант кампании или шаблон. Новый вариант начинается с загрузки креатива."
      />
      {campaigns.isError ? <SectionError error={campaigns.error} onRetry={() => void campaigns.refetch()} /> : null}
      {campaigns.isPending ? (
        <Skeleton className="h-40" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={PanelsTopLeft}
          title="Кампаний пока нет"
          description="Создайте кампанию в карточке продукта — варианты баннера живут внутри кампаний."
          action={
            <Link href={routeHref("/products")} className="text-sm text-accent hover:underline">
              К продуктам
            </Link>
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {items.map((campaign) => (
            <CampaignVariantsCard
              key={campaign.id}
              campaignId={campaign.id}
              title={campaign.name}
              product={productName.get(campaign.product_id) ?? campaign.product_id}
            />
          ))}
        </div>
      )}
      {templates.data?.length ? (
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold">Шаблоны (пресеты новых вариантов)</h2>
          <ul className="flex flex-wrap gap-2">
            {templates.data.map((tpl) => (
              <li key={tpl.id}>
                <Link
                  href={routeHref("/products/banner", { template: String(tpl.id) })}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-sm hover:border-accent/60"
                >
                  {tpl.name}
                  {tpl.status === "archived" ? <Badge>архив</Badge> : null}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function CampaignVariantsCard({ campaignId, title, product }: { campaignId: number; title: string; product: string }) {
  const variants = useVariants(campaignId);
  return (
    <Card className="flex flex-col gap-2 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-fg">{title}</p>
          <p className="truncate text-xs text-fg-muted">{product}</p>
        </div>
        <Link
          href={routeHref("/products/banner", { campaign: String(campaignId) })}
          className="shrink-0 text-xs text-accent hover:underline"
        >
          + вариант
        </Link>
      </div>
      <ul className="flex flex-col gap-1">
        {(variants.data ?? []).map((variant) => (
          <li key={variant.id}>
            <Link
              href={routeHref("/products/banner", { variant: String(variant.id) })}
              className="flex items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-surface-2"
            >
              <span className="font-mono text-xs">{variant.code}</span>
              <span className="min-w-0 flex-1 truncate">{variant.name || "без имени"}</span>
              {variant.status === "active" ? (
                <CircleCheck className="h-3.5 w-3.5 text-positive" aria-label="в ротации" />
              ) : (
                <span className="text-2xs text-fg-muted">{VARIANT_STATUS_META[variant.status].label}</span>
              )}
            </Link>
          </li>
        ))}
        {variants.data && variants.data.length === 0 ? <li className="text-xs text-fg-muted">Вариантов нет</li> : null}
      </ul>
    </Card>
  );
}
