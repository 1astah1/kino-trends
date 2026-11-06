"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { LogIn } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import * as z from "zod/mini";

import { isMocking } from "@/components/common/mock-gate";
import { BrandMark } from "@/components/layout/brand";
import { Button, FormField, Input } from "@/components/ui";
import { ApiError, errorMessage } from "@/lib/api/errors";
import { safeNextPath } from "@/lib/auth";
import { useLogin, useMe } from "@/lib/queries/auth";

const schema = z.object({
  login: z.string().check(z.trim(), z.minLength(1, "Введите логин"), z.maxLength(64, "Слишком длинный логин")),
  password: z.string().check(z.minLength(1, "Введите пароль"), z.maxLength(1024, "Слишком длинный пароль")),
});
type Values = z.infer<typeof schema>;

function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Неверный логин или пароль";
    if (error.status === 429) return "Слишком много попыток. Подождите минуту";
    if (error.status === 0) return "Нет связи с сервером";
  }
  return errorMessage(error);
}

/** Вход (§7.3 `/login?next=`): `POST /auth/login` → cookie сессии и CSRF → переход на `next`. */
export function LoginScreen() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = safeNextPath(searchParams.get("next"));
  const me = useMe();
  const login = useLogin();
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { login: "", password: "" } });
  const { errors, isSubmitting } = form.formState;

  // Уже вошёл (открыл /login в новой вкладке) — сразу туда, куда шёл.
  useEffect(() => {
    if (me.data) router.replace(next);
  }, [me.data, next, router]);

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await login.mutateAsync(values);
      router.replace(next);
    } catch (error) {
      form.setError("root.server", { type: "server", message: loginErrorMessage(error) });
      form.setValue("password", "");
      form.setFocus("password");
    }
  });

  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg p-4">
      <div className="w-full max-w-sm">
        <div className="safe-frame rounded-lg border border-border bg-surface p-6">
          <div className="mb-6 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-md border border-border bg-video">
              <BrandMark className="h-6 w-6" />
            </div>
            <div>
              <p className="font-mono text-2xs tracking-[0.2em] text-fg-muted uppercase">Пульт оператора</p>
              <h1 className="text-lg font-semibold tracking-tight">Auto Shorts</h1>
            </div>
          </div>

          <form onSubmit={onSubmit} className="flex flex-col gap-3" noValidate aria-label="Вход в консоль">
            <FormField label="Логин" htmlFor="login" error={errors.login?.message}>
              <Input
                id="login"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                autoFocus
                aria-invalid={!!errors.login}
                aria-describedby={errors.login ? "login-message" : undefined}
                {...form.register("login")}
              />
            </FormField>
            <FormField label="Пароль" htmlFor="password" error={errors.password?.message}>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                aria-invalid={!!errors.password}
                aria-describedby={errors.password ? "password-message" : undefined}
                {...form.register("password")}
              />
            </FormField>
            {errors.root?.server?.message ? (
              <p
                role="alert"
                className="rounded-md border border-negative/40 bg-negative/10 px-2 py-1.5 text-xs text-negative"
                data-testid="login-error"
              >
                {errors.root.server.message}
              </p>
            ) : null}
            <Button type="submit" className="mt-1 h-9" disabled={isSubmitting}>
              <LogIn className="h-4 w-4" aria-hidden="true" />
              {isSubmitting ? "Вход…" : "Войти"}
            </Button>
          </form>
        </div>
        <p className="mt-3 text-center text-2xs text-fg-muted">
          После 10 неудачных попыток за 15 минут вход блокируется. Время на экранах — МСК.
        </p>
        {isMocking ? (
          <p className="mt-2 rounded-md border border-machine/40 bg-machine/10 px-2 py-1.5 text-center font-mono text-2xs text-machine">
            Моки API: admin / admin-demo-password · operator / operator-demo-pass
          </p>
        ) : null}
      </div>
    </main>
  );
}
