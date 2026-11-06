"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { SectionError } from "@/components/common/section-error";
import { ShellSkeleton } from "@/components/layout/shell-skeleton";
import { onAuthLost } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { buildNextParam, loginHref } from "@/lib/auth";
import { useMe } from "@/lib/queries/auth";
import { queryKeys } from "@/lib/queries/keys";

import { AuthProvider } from "./auth-context";

/**
 * Клиентская защита маршрутов (§7.1): скелетон до ответа `GET /auth/me`, при 401 — переход на
 * `/login?next=<текущий путь>`. Любой 401 от API во время работы (сессия истекла, отозвана)
 * сбрасывает кеш `/auth/me` → тот же переход.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;

  useEffect(
    () =>
      onAuthLost(() => {
        void queryClient.resetQueries({ queryKey: queryKeys.me });
      }),
    [queryClient],
  );

  useEffect(() => {
    if (!unauthenticated) return;
    const search = searchParams.toString();
    router.replace(loginHref(buildNextParam(pathname ?? "/", search ? `?${search}` : "")));
  }, [unauthenticated, pathname, searchParams, router]);

  if (me.data) return <AuthProvider user={me.data.user}>{children}</AuthProvider>;
  if (me.isPending || unauthenticated) return <ShellSkeleton />;

  return (
    <div className="flex h-dvh items-center justify-center p-4">
      <SectionError
        className="w-full max-w-md"
        title="Консоль не может проверить сессию"
        error={me.error}
        onRetry={() => void me.refetch()}
      />
    </div>
  );
}
