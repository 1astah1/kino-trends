"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import * as z from "zod/mini";

import { applyServerErrors, orNull, SLUG_RE } from "@/components/products/form-utils";
import { Button, FormField, Input, Select, Skeleton, Textarea } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { ConflictError } from "@/lib/api/errors";
import { useBannerTemplates } from "@/lib/queries/banners";
import {
  type Channel,
  useClusters,
  useCreateChannel,
  useCreateCluster,
  useNicheOptions,
  useUpdateChannel,
} from "@/lib/queries/channels";
import { useProducts } from "@/lib/queries/products";
import { useProviders } from "@/lib/queries/providers";

import { CHANNEL_STATUS_META, PLATFORM_SHORT } from "./labels";

const STATUSES = Object.keys(CHANNEL_STATUS_META) as [Schemas["ChannelStatus"], ...Schemas["ChannelStatus"][]];
const PLATFORMS = ["youtube", "vk"] as const;
const REF_RE = /^(UC[A-Za-z0-9_-]{22}|-?[0-9]{1,20})$/;

const schema = z.object({
  id: z.string().check(z.trim()),
  platform: z.enum(PLATFORMS),
  niche_id: z.string().check(z.minLength(1, "Выберите нишу")),
  cluster: z.string().check(z.trim(), z.regex(SLUG_RE, "Куст: 2–40 символов a–z, 0–9, «-», «_»")),
  title: z.string().check(z.maxLength(200, "Не длиннее 200")),
  platform_channel_ref: z.string().check(
    z.trim(),
    z.refine((v) => !v || REF_RE.test(v), "YouTube: UC… (24 символа); VK: -<id группы>"),
  ),
  url: z.string().check(
    z.trim(),
    z.refine((v) => !v || /^https?:\/\/\S+$/.test(v), "Полный адрес канала, с https"),
  ),
  status: z.enum(STATUSES),
  default_product_id: z.string(),
  tts_provider: z.string(),
  tts_voice: z.string().check(z.maxLength(100, "Слишком длинно")),
  banner_template_id: z.string(),
  visual_style_suffix: z.string().check(z.maxLength(300, "Не длиннее 300")),
  seed_family: z.string().check(z.maxLength(40, "Не длиннее 40")),
  videos_per_day: z.string().check(z.refine((v) => !v || (/^\d+$/.test(v) && Number(v) <= 50), "0–50")),
  subscribers: z.string().check(z.refine((v) => !v || /^\d+$/.test(v), "Целое число")),
  advanced_features: z.boolean(),
  notes: z.string().check(z.maxLength(2000, "Слишком длинно")),
});
type Values = z.infer<typeof schema>;

const SERVER_FIELDS = [
  "id",
  "platform",
  "niche_id",
  "cluster",
  "title",
  "platform_channel_ref",
  "url",
  "status",
  "default_product_id",
  "notes",
  "subscribers",
] as const;

function valuesOf(channel: Channel | null): Values {
  return {
    id: channel?.id ?? "",
    platform: channel?.platform ?? "youtube",
    niche_id: channel?.niche_id ?? "",
    cluster: channel?.cluster ?? "",
    title: channel?.title ?? "",
    platform_channel_ref: channel?.platform_channel_ref ?? "",
    url: channel?.url ?? "",
    status: channel?.status ?? "warming",
    default_product_id: channel?.default_product_id ?? "",
    tts_provider: channel?.profile.tts_provider ?? "",
    tts_voice: channel?.profile.tts_voice ?? "",
    banner_template_id: channel?.profile.banner_template_id ? String(channel.profile.banner_template_id) : "",
    visual_style_suffix: channel?.profile.visual_style_suffix ?? "",
    seed_family: channel?.profile.seed_family ?? "",
    videos_per_day:
      channel?.publishing_plan.videos_per_day !== null && channel?.publishing_plan.videos_per_day !== undefined
        ? String(channel.publishing_plan.videos_per_day)
        : "",
    subscribers: channel?.subscribers !== null && channel?.subscribers !== undefined ? String(channel.subscribers) : "",
    advanced_features: channel?.advanced_features ?? false,
    notes: channel?.notes ?? "",
  };
}

/**
 * Форма канала: площадка, ниша, куст, профиль (голос, шаблон баннера, стиль визуала, семья seed —
 * разные профили против детекта сетки, §1.3), план публикаций. Создание и правка.
 */
export function ChannelForm({
  channel,
  onDone,
  onCancel,
  readOnly,
}: {
  channel: Channel | null;
  onDone: (channel: Channel) => void;
  onCancel?: () => void;
  readOnly?: boolean;
}) {
  const create = useCreateChannel();
  const update = useUpdateChannel(channel?.id ?? "");
  const createCluster = useCreateCluster();
  const niches = useNicheOptions();
  const clusters = useClusters();
  const products = useProducts({ status: "active" });
  const providers = useProviders();
  const templates = useBannerTemplates();
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: valuesOf(channel) });
  const { errors, isSubmitting, isDirty } = form.formState;
  const ttsProviders = (providers.data ?? []).filter((provider) => provider.kind === "tts");

  const onSubmit = form.handleSubmit(async (values) => {
    if (!channel && !SLUG_RE.test(values.id)) {
      form.setError("id", { type: "client", message: "2–40 символов: a–z, 0–9, «-», «_»" });
      return;
    }
    const profile = {
      tts_provider: orNull(values.tts_provider),
      tts_voice: orNull(values.tts_voice),
      banner_template_id: values.banner_template_id ? Number(values.banner_template_id) : null,
      visual_style_suffix: orNull(values.visual_style_suffix),
      seed_family: orNull(values.seed_family),
    };
    const common = {
      niche_id: values.niche_id,
      cluster: values.cluster,
      title: orNull(values.title),
      platform_channel_ref: orNull(values.platform_channel_ref),
      url: orNull(values.url),
      status: values.status,
      default_product_id: orNull(values.default_product_id),
      profile,
      publishing_plan: { videos_per_day: values.videos_per_day ? Number(values.videos_per_day) : null },
      advanced_features: values.advanced_features,
      notes: orNull(values.notes),
    };
    try {
      if (!(clusters.data ?? []).some((cluster) => cluster.id === values.cluster)) {
        await createCluster.mutateAsync({ id: values.cluster, title: values.cluster });
      }
      const saved = channel
        ? await update.mutateAsync({ ...common, subscribers: values.subscribers ? Number(values.subscribers) : null })
        : await create.mutateAsync({ id: values.id, platform: values.platform, ...common });
      if (saved.profile_duplicate_of.length) {
        toast.warning("Профиль совпадает с другими каналами", {
          description: `${saved.profile_duplicate_of.join(", ")} — сделайте голос, шаблон или стиль разными.`,
        });
      } else {
        toast.success(channel ? "Канал сохранён" : `Канал ${saved.id} добавлен`);
      }
      form.reset(valuesOf(saved));
      onDone(saved);
    } catch (error) {
      if (error instanceof ConflictError) {
        form.setError("id", { type: "server", message: error.message || "Такой канал уже есть" });
        return;
      }
      applyServerErrors(error, form.setError, [...SERVER_FIELDS]);
    }
  });

  const disabled = readOnly || isSubmitting;
  // Поля-списки неуправляемые (register): форма монтируется, когда варианты уже есть, иначе
  // <select> не примет значение по умолчанию (ниша, продукт, озвучка, шаблон).
  const optionsReady = [niches, clusters, products, providers, templates].every((query) => !query.isPending);
  if (!optionsReady) return <Skeleton className="h-96" aria-label="Загрузка справочников" />;
  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate data-testid="channel-form">
      <fieldset className="grid gap-3 sm:grid-cols-2" disabled={disabled}>
        <legend className="sr-only">Канал</legend>
        {channel ? null : (
          <>
            <FormField label="id (латиница)" htmlFor="channel-id" error={errors.id?.message} hint="Например: taro_main">
              <Input
                id="channel-id"
                className="font-mono"
                autoComplete="off"
                spellCheck={false}
                aria-invalid={!!errors.id}
                {...form.register("id")}
              />
            </FormField>
            <FormField label="Площадка" htmlFor="channel-platform" error={errors.platform?.message}>
              <Select id="channel-platform" {...form.register("platform")}>
                {PLATFORMS.map((platform) => (
                  <option key={platform} value={platform}>
                    {platform === "youtube" ? "YouTube Shorts" : "VK Клипы"}
                  </option>
                ))}
              </Select>
            </FormField>
          </>
        )}
        <FormField label="Название" htmlFor="channel-title" error={errors.title?.message}>
          <Input id="channel-title" {...form.register("title")} />
        </FormField>
        <FormField label="Статус" htmlFor="channel-status" error={errors.status?.message}>
          <Select id="channel-status" {...form.register("status")}>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {CHANNEL_STATUS_META[status].label}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Ниша" htmlFor="channel-niche" error={errors.niche_id?.message}>
          <Select id="channel-niche" aria-invalid={!!errors.niche_id} {...form.register("niche_id")}>
            <option value="">— выберите —</option>
            {(niches.data ?? []).map((niche) => (
              <option key={niche.id} value={niche.id}>
                {niche.label} ({niche.id})
              </option>
            ))}
          </Select>
        </FormField>
        <FormField
          label="Куст"
          htmlFor="channel-cluster"
          error={errors.cluster?.message}
          hint="Свой Cloud-проект и прокси на куст; нового куста создастся запись"
        >
          <Input
            id="channel-cluster"
            className="font-mono"
            list="channel-clusters"
            aria-invalid={!!errors.cluster}
            {...form.register("cluster")}
          />
          <datalist id="channel-clusters">
            {(clusters.data ?? []).map((cluster) => (
              <option key={cluster.id} value={cluster.id}>
                {cluster.title}
              </option>
            ))}
          </datalist>
        </FormField>
        <FormField
          label={channel ? `ID канала на площадке (${PLATFORM_SHORT[channel.platform]})` : "ID канала на площадке"}
          htmlFor="channel-ref"
          error={errors.platform_channel_ref?.message}
          hint="YouTube: UC…; VK: -<id группы>"
        >
          <Input
            id="channel-ref"
            className="font-mono"
            aria-invalid={!!errors.platform_channel_ref}
            {...form.register("platform_channel_ref")}
          />
        </FormField>
        <FormField label="Ссылка на канал" htmlFor="channel-url" error={errors.url?.message}>
          <Input id="channel-url" className="font-mono" aria-invalid={!!errors.url} {...form.register("url")} />
        </FormField>
        <FormField label="Продукт по умолчанию" htmlFor="channel-product" error={errors.default_product_id?.message}>
          <Select id="channel-product" {...form.register("default_product_id")}>
            <option value="">— по кампаниям —</option>
            {(products.data?.items ?? []).map((product) => (
              <option key={product.id} value={product.id}>
                {product.name}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Роликов в день (план)" htmlFor="channel-plan" error={errors.videos_per_day?.message}>
          <Input id="channel-plan" inputMode="numeric" className="font-mono" {...form.register("videos_per_day")} />
        </FormField>
        {channel ? (
          <FormField label="Подписчики (вручную)" htmlFor="channel-subs" error={errors.subscribers?.message}>
            <Input id="channel-subs" inputMode="numeric" className="font-mono" {...form.register("subscribers")} />
          </FormField>
        ) : null}
        <label className="flex items-center gap-2 self-end pb-1.5 text-sm">
          <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" {...form.register("advanced_features")} />
          Advanced features включены (ссылки в описании)
        </label>
      </fieldset>

      <fieldset className="grid gap-3 sm:grid-cols-2" disabled={disabled}>
        <legend className="mb-1 font-mono text-2xs tracking-wider text-fg-muted uppercase">
          Профиль: свой голос, шаблон и стиль у каждого канала
        </legend>
        <FormField label="Озвучка" htmlFor="channel-tts" error={errors.tts_provider?.message}>
          <Select id="channel-tts" {...form.register("tts_provider")}>
            <option value="">— по умолчанию —</option>
            {ttsProviders.map((provider) => (
              <option key={provider.key} value={provider.key}>
                {provider.label}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Голос" htmlFor="channel-voice" error={errors.tts_voice?.message}>
          <Input
            id="channel-voice"
            className="font-mono"
            placeholder="ru-RU-SvetlanaNeural"
            {...form.register("tts_voice")}
          />
        </FormField>
        <FormField label="Шаблон баннера" htmlFor="channel-template" error={errors.banner_template_id?.message}>
          <Select id="channel-template" {...form.register("banner_template_id")}>
            <option value="">— по кампании —</option>
            {(templates.data ?? []).map((tpl) => (
              <option key={tpl.id} value={String(tpl.id)}>
                {tpl.name}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Семья seed" htmlFor="channel-seed" error={errors.seed_family?.message}>
          <Input id="channel-seed" className="font-mono" {...form.register("seed_family")} />
        </FormField>
        <FormField
          label="Суффикс стиля визуала"
          htmlFor="channel-style"
          error={errors.visual_style_suffix?.message}
          className="sm:col-span-2"
        >
          <Input id="channel-style" {...form.register("visual_style_suffix")} />
        </FormField>
        <FormField label="Заметки" htmlFor="channel-notes" error={errors.notes?.message} className="sm:col-span-2">
          <Textarea id="channel-notes" rows={2} {...form.register("notes")} />
        </FormField>
      </fieldset>

      {errors.root?.server?.message ? (
        <p
          role="alert"
          className="rounded-md border border-negative/40 bg-negative/10 px-2 py-1.5 text-xs text-negative"
        >
          {errors.root.server.message}
        </p>
      ) : null}
      {readOnly ? null : (
        <div className="flex justify-end gap-2">
          {onCancel ? (
            <Button variant="outline" onClick={onCancel}>
              Отмена
            </Button>
          ) : null}
          <Button type="submit" disabled={isSubmitting || (!!channel && !isDirty)} data-testid="channel-save">
            {isSubmitting ? "Сохраняю…" : channel ? "Сохранить" : "Добавить канал"}
          </Button>
        </div>
      )}
    </form>
  );
}
