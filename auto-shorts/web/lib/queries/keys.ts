/**
 * Ключи запросов (§7.4): `['jobs', filters]`, `['job', id]`, … Один файл, чтобы SSE-мост
 * (lib/hooks/use-live-events.ts) инвалидировал ровно то, что показывают экраны.
 */
export const queryKeys = {
  me: ["auth", "me"] as const,
  systemStatus: ["system", "status"] as const,
  ready: ["system", "ready"] as const,
  budget: ["budget"] as const,
  alerts: (status: string = "open") => ["alerts", status] as const,
  alertsAll: ["alerts"] as const,
  users: ["users"] as const,
  metaEnums: ["meta", "enums"] as const,
  // Бейджи сайдбара: заполняются событиями SSE `review_queue` и `packages` (экраны M2/M3
  // кладут сюда же ответы своих списков).
  reviewQueueCount: ["review-queue", "count"] as const,
  packagesCounts: ["packages", "counts"] as const,
  trendsAttention: ["trends", "attention"] as const,
  jobs: ["jobs"] as const,
  job: (id: number) => ["job", id] as const,
  tasks: ["tasks"] as const,
  task: (id: number) => ["task", id] as const,
  sources: ["sources"] as const,
  source: (id: number) => ["source", id] as const,
  publication: (id: number) => ["publication", id] as const,
};
