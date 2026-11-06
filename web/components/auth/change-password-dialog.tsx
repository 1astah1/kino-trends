"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import * as z from "zod/mini";

import { Button, Dialog, FormField, Input } from "@/components/ui";
import { errorMessage, ValidationError } from "@/lib/api/errors";
import { useChangePassword } from "@/lib/queries/auth";

const MIN_PASSWORD = 12;

const schema = z
  .object({
    current_password: z.string().check(z.minLength(1, "Введите текущий пароль")),
    new_password: z
      .string()
      .check(
        z.minLength(MIN_PASSWORD, `Не короче ${MIN_PASSWORD} символов`),
        z.maxLength(1024, "Слишком длинный пароль"),
      ),
    confirm: z.string(),
  })
  .check(
    z.refine((value) => value.new_password === value.confirm, { path: ["confirm"], error: "Пароли не совпадают" }),
    z.refine((value) => value.new_password !== value.current_password, {
      path: ["new_password"],
      error: "Новый пароль совпадает с текущим",
    }),
  );
type Values = z.infer<typeof schema>;

/** Смена своего пароля (`POST /auth/change-password`). Остальные сессии сервер завершает. */
export function ChangePasswordDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const change = useChangePassword();
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: "", new_password: "", confirm: "" },
  });
  const { errors, isSubmitting } = form.formState;

  const close = () => {
    form.reset();
    onClose();
  };

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await change.mutateAsync({ current_password: values.current_password, new_password: values.new_password });
      toast.success("Пароль изменён", { description: "Остальные сессии завершены." });
      close();
    } catch (error) {
      if (error instanceof ValidationError) {
        const fields = error.fieldErrors;
        if (fields.current_password)
          form.setError("current_password", { type: "server", message: fields.current_password });
        if (fields.new_password) form.setError("new_password", { type: "server", message: fields.new_password });
        if (fields.current_password || fields.new_password) return;
      }
      form.setError("root.server", { type: "server", message: errorMessage(error) });
    }
  });

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Смена пароля"
      description="Не короче 12 символов. Другие устройства придётся войти заново."
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-3" noValidate>
        <FormField label="Текущий пароль" htmlFor="cp-current" error={errors.current_password?.message}>
          <Input
            id="cp-current"
            autoFocus
            type="password"
            autoComplete="current-password"
            aria-invalid={!!errors.current_password}
            {...form.register("current_password")}
          />
        </FormField>
        <FormField label="Новый пароль" htmlFor="cp-new" error={errors.new_password?.message}>
          <Input
            id="cp-new"
            type="password"
            autoComplete="new-password"
            aria-invalid={!!errors.new_password}
            {...form.register("new_password")}
          />
        </FormField>
        <FormField label="Новый пароль ещё раз" htmlFor="cp-confirm" error={errors.confirm?.message}>
          <Input
            id="cp-confirm"
            type="password"
            autoComplete="new-password"
            aria-invalid={!!errors.confirm}
            {...form.register("confirm")}
          />
        </FormField>
        {errors.root?.server?.message ? (
          <p
            role="alert"
            className="rounded-md border border-negative/40 bg-negative/10 px-2 py-1.5 text-xs text-negative"
          >
            {errors.root.server.message}
          </p>
        ) : null}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" onClick={close}>
            Отмена
          </Button>
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Сохраняю…" : "Сменить пароль"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
