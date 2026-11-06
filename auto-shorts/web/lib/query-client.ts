import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError, BudgetPausedError, ConflictError, errorMessage, InsufficientStorageError } from "@/lib/api/errors";

/**
 * Общие правила кеша (§7.4): staleTime 15 с; 4xx не повторяем; мутации показывают тост ошибки,
 * если сами не обработали (meta.silentError). 409 → тост и перечитывание затронутых запросов.
 */
export function makeQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({}),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.silentError) return;
        if (error instanceof ApiError && error.status === 401) return; // AuthGate уведёт на вход
        if (error instanceof ConflictError) {
          toast.warning(error.message, { description: "Данные перечитаны." });
          void client.invalidateQueries();
          return;
        }
        if (error instanceof BudgetPausedError) {
          toast.warning("Платные шаги на паузе", { description: error.message });
          return;
        }
        if (error instanceof InsufficientStorageError) {
          toast.error("Нет места на диске", { description: error.message });
          return;
        }
        toast.error(errorMessage(error));
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
          return failureCount < 2;
        },
      },
      mutations: {
        retry: false,
      },
    },
  });
  return client;
}

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: { silentError?: boolean };
  }
}
