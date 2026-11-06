"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import * as z from "zod/mini";

import { Button, FormField, Input, Select } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { ConflictError } from "@/lib/api/errors";
import { type Product, useCreateProduct, useUpdateProduct } from "@/lib/queries/products";

import { applyServerErrors, HOST_RE, orNull, SLUG_RE, TG_BOT_RE, TMA_RE } from "./form-utils";
import { PRODUCT_KIND_LABELS, REDIRECT_MODE_LABELS, TELEGRAM_LINK_STYLE_LABELS } from "./labels";

const KINDS = Object.keys(PRODUCT_KIND_LABELS) as [Schemas["ProductKind"], ...Schemas["ProductKind"][]];
const MODES = Object.keys(REDIRECT_MODE_LABELS) as [Schemas["RedirectMode"], ...Schemas["RedirectMode"][]];
const LINK_STYLES = Object.keys(TELEGRAM_LINK_STYLE_LABELS) as [
  Schemas["TelegramLinkStyle"],
  ...Schemas["TelegramLinkStyle"][],
];

const optional = (re: RegExp, message: string) =>
  z.string().check(
    z.trim(),
    z.refine((v) => !v || re.test(v), message),
  );

const schema = z.object({
  id: z.string().check(z.trim()),
  name: z.string().check(z.trim(), z.minLength(1, "Укажите название"), z.maxLength(100, "Не длиннее 100 символов")),
  kind: z.enum(KINDS),
  landing_url: z.string().check(
    z.trim(),
    z.refine((v) => !v || /^https?:\/\/\S+$/.test(v), "Полный адрес лендинга, с https"),
  ),
  redirect_host: optional(HOST_RE, "Только домен: u.umka.chat"),
  redirect_mode: z.enum(MODES),
  telegram_bot_username: optional(TG_BOT_RE, "5–32 символа: латиница, цифры, _ (без @)"),
  tma_short_name: optional(TMA_RE, "3–64 символа: латиница, цифры, _"),
  telegram_link_style: z.enum(LINK_STYLES),
  display_domain: optional(HOST_RE, "Домен в кадре: umka.chat"),
  primary_color: optional(/^#[0-9A-Fa-f]{6}$/, "Цвет #RRGGBB"),
  landing_title: z.string().check(z.maxLength(120, "Не длиннее 120")),
  landing_subtitle: z.string().check(z.maxLength(200, "Не длиннее 200")),
  button_telegram: z.string().check(z.maxLength(60, "Не длиннее 60")),
  button_site: z.string().check(z.maxLength(60, "Не длиннее 60")),
});
type Values = z.infer<typeof schema>;

const FIELDS = [
  "id",
  "name",
  "kind",
  "landing_url",
  "redirect_host",
  "redirect_mode",
  "telegram_bot_username",
  "tma_short_name",
  "telegram_link_style",
] as const;

function valuesOf(product: Product | null): Values {
  return {
    id: product?.id ?? "",
    name: product?.name ?? "",
    kind: product?.kind ?? "web_service",
    landing_url: product?.landing_url ?? "",
    redirect_host: product?.redirect_host ?? "",
    redirect_mode: product?.redirect_mode ?? "landing_split",
    telegram_bot_username: product?.telegram_bot_username ?? "",
    tma_short_name: product?.tma_short_name ?? "",
    telegram_link_style: product?.telegram_link_style ?? "tg_scheme_with_fallback",
    display_domain: product?.brand.display_domain ?? "",
    primary_color: product?.brand.primary_color ?? "",
    landing_title: product?.landing.title ?? "",
    landing_subtitle: product?.landing.subtitle ?? "",
    button_telegram: product?.landing.button_telegram ?? "",
    button_site: product?.landing.button_site ?? "",
  };
}

/** Форма продукта: создание (с id-слагом) или правка карточки. */
export function ProductForm({
  product,
  onDone,
  onCancel,
  readOnly,
}: {
  product: Product | null;
  onDone: (product: Product) => void;
  onCancel?: () => void;
  readOnly?: boolean;
}) {
  const create = useCreateProduct();
  const update = useUpdateProduct(product?.id ?? "");
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: valuesOf(product) });
  const { errors, isSubmitting, isDirty } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    if (!product && !SLUG_RE.test(values.id)) {
      form.setError("id", { type: "client", message: "2–40 символов: a–z, 0–9, «-», «_»; с буквы или цифры" });
      return;
    }
    const common = {
      name: values.name,
      kind: values.kind,
      landing_url: orNull(values.landing_url),
      redirect_host: orNull(values.redirect_host),
      redirect_mode: values.redirect_mode,
      telegram_bot_username: orNull(values.telegram_bot_username),
      tma_short_name: orNull(values.tma_short_name),
      telegram_link_style: values.telegram_link_style,
      brand: {
        display_domain: orNull(values.display_domain),
        primary_color: orNull(values.primary_color),
        logo_asset_id: product?.brand.logo_asset_id ?? null,
      },
      landing: {
        title: orNull(values.landing_title),
        subtitle: orNull(values.landing_subtitle),
        button_telegram: orNull(values.button_telegram),
        button_site: orNull(values.button_site),
      },
    };
    try {
      const saved = product
        ? await update.mutateAsync(common)
        : await create.mutateAsync({ id: values.id, utm_defaults: {}, ...common });
      toast.success(product ? "Продукт сохранён" : `Продукт ${saved.name} создан`);
      form.reset(valuesOf(saved));
      onDone(saved);
    } catch (error) {
      if (error instanceof ConflictError) {
        form.setError("id", { type: "server", message: error.message || "Такой id уже есть" });
        return;
      }
      applyServerErrors(error, form.setError, [...FIELDS, "brand.display_domain", "brand.primary_color"]);
    }
  });

  const disabled = readOnly || isSubmitting;

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate data-testid="product-form">
      <fieldset className="grid gap-3 sm:grid-cols-2" disabled={disabled}>
        <legend className="sr-only">Основное</legend>
        {product ? null : (
          <FormField
            label="id (латиница, навсегда)"
            htmlFor="product-id"
            error={errors.id?.message}
            hint="Например: umka, vpn_bot"
          >
            <Input
              id="product-id"
              className="font-mono"
              autoComplete="off"
              spellCheck={false}
              aria-invalid={!!errors.id}
              {...form.register("id")}
            />
          </FormField>
        )}
        <FormField label="Название" htmlFor="product-name" error={errors.name?.message}>
          <Input id="product-name" aria-invalid={!!errors.name} {...form.register("name")} />
        </FormField>
        <FormField label="Тип" htmlFor="product-kind" error={errors.kind?.message}>
          <Select id="product-kind" {...form.register("kind")}>
            {KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {PRODUCT_KIND_LABELS[kind]}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Лендинг продукта" htmlFor="product-landing" error={errors.landing_url?.message}>
          <Input
            id="product-landing"
            className="font-mono"
            placeholder="полный адрес, с https"
            aria-invalid={!!errors.landing_url}
            {...form.register("landing_url")}
          />
        </FormField>
      </fieldset>

      <fieldset className="grid gap-3 sm:grid-cols-2" disabled={disabled}>
        <legend className="mb-1 font-mono text-2xs tracking-wider text-fg-muted uppercase">
          Редиректор и Telegram
        </legend>
        <FormField
          label="Домен редиректора"
          htmlFor="product-host"
          error={errors.redirect_host?.message}
          hint="Короткие ссылки u.<домен>/<код>; из него же код в кадре"
        >
          <Input
            id="product-host"
            className="font-mono"
            placeholder="u.umka.chat"
            aria-invalid={!!errors.redirect_host}
            {...form.register("redirect_host")}
          />
        </FormField>
        <FormField label="Куда ведёт ссылка" htmlFor="product-mode" error={errors.redirect_mode?.message}>
          <Select id="product-mode" {...form.register("redirect_mode")}>
            {MODES.map((mode) => (
              <option key={mode} value={mode}>
                {REDIRECT_MODE_LABELS[mode]}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Бот (без @)" htmlFor="product-bot" error={errors.telegram_bot_username?.message}>
          <Input
            id="product-bot"
            className="font-mono"
            placeholder="umka_bot"
            aria-invalid={!!errors.telegram_bot_username}
            {...form.register("telegram_bot_username")}
          />
        </FormField>
        <FormField label="Mini App (short name)" htmlFor="product-tma" error={errors.tma_short_name?.message}>
          <Input
            id="product-tma"
            className="font-mono"
            aria-invalid={!!errors.tma_short_name}
            {...form.register("tma_short_name")}
          />
        </FormField>
        <FormField
          label="Ссылка в Telegram"
          htmlFor="product-tg-style"
          error={errors.telegram_link_style?.message}
          hint="t.me в РФ ненадёжен: tg:// с запасной ссылкой"
        >
          <Select id="product-tg-style" {...form.register("telegram_link_style")}>
            {LINK_STYLES.map((style) => (
              <option key={style} value={style}>
                {TELEGRAM_LINK_STYLE_LABELS[style]}
              </option>
            ))}
          </Select>
        </FormField>
      </fieldset>

      <fieldset className="grid gap-3 sm:grid-cols-2" disabled={disabled}>
        <legend className="mb-1 font-mono text-2xs tracking-wider text-fg-muted uppercase">
          Бренд в кадре и лендинг
        </legend>
        <FormField label="Домен в кадре (микро-бренд)" htmlFor="product-domain" error={errors.display_domain?.message}>
          <Input
            id="product-domain"
            className="font-mono"
            placeholder="umka.chat"
            aria-invalid={!!errors.display_domain}
            {...form.register("display_domain")}
          />
        </FormField>
        <FormField label="Цвет бренда" htmlFor="product-color" error={errors.primary_color?.message}>
          <Input
            id="product-color"
            className="font-mono"
            placeholder="#7C5CFF"
            aria-invalid={!!errors.primary_color}
            {...form.register("primary_color")}
          />
        </FormField>
        <FormField label="Заголовок лендинга" htmlFor="product-l-title" error={errors.landing_title?.message}>
          <Input id="product-l-title" {...form.register("landing_title")} />
        </FormField>
        <FormField label="Подзаголовок" htmlFor="product-l-sub" error={errors.landing_subtitle?.message}>
          <Input id="product-l-sub" {...form.register("landing_subtitle")} />
        </FormField>
        <FormField label="Кнопка Telegram" htmlFor="product-l-tg" error={errors.button_telegram?.message}>
          <Input id="product-l-tg" {...form.register("button_telegram")} />
        </FormField>
        <FormField label="Кнопка сайта" htmlFor="product-l-site" error={errors.button_site?.message}>
          <Input id="product-l-site" {...form.register("button_site")} />
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
          <Button type="submit" disabled={isSubmitting || (!!product && !isDirty)}>
            {isSubmitting ? "Сохраняю…" : product ? "Сохранить" : "Создать"}
          </Button>
        </div>
      )}
    </form>
  );
}
