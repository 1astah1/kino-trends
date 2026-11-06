SELECT as_migration_guard(16);

-- Постоянные резервы бюджета (контракт M4-0; реализация — services/budget.py и reaper, M1-C).
-- До 0016 резерв жил в памяти гейта задачи (TaskBudgetGate) и суммой в budget_state.reserved_usd:
-- воркер, убитый SIGKILL/OOM, оставлял резерв до смены бизнес-дня. Теперь каждый резерв — строка,
-- и reaper снимает открытые резервы, чей хозяин пропал (v_budget_orphan_reservations).
--
-- Правила для кода (одна транзакция, порядок блокировок: budget_state FOR UPDATE, затем строки резервов):
--   * reserve: budget_state.reserved_usd += amount и INSERT строки (status='open', business_day = budget_state.day,
--     task_id / task_attempt = tasks.attempts / worker_id = tasks.locked_by, если резерв из задачи);
--   * settle / release / reaper закрывают строку только так:
--       UPDATE budget_reservations SET status = …, closed_at = now(), … WHERE id = … AND status = 'open' RETURNING …
--     и вычитают amount_usd из reserved_usd, только если строка вернулась и её business_day = budget_state.day
--     (резерв прошлого дня уже обнулён сменой дня). Гонка воркер/reaper не снимает резерв дважды;
--   * settle после того, как reaper уже снял резерв, всё равно добавляет фактический расход в spent_today_usd
--     и может дописать actual_usd / generation_event_id в закрытую строку (status не меняется).
-- Инвариант: budget_state.reserved_usd = SUM(amount_usd) открытых резервов с business_day = budget_state.day,
-- кроме резервов, выданных до 0016 (без строки): они по-прежнему уходят сменой дня.
CREATE TABLE budget_reservations (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT REFERENCES tasks(id) ON DELETE SET NULL,
  task_attempt INT,                                  -- tasks.attempts на момент резерва: новая попытка = сирота
  worker_id TEXT,                                    -- workers.id (= tasks.locked_by) того, кто резервировал
  job_id BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
  source_id BIGINT REFERENCES sources(id) ON DELETE SET NULL,
  trend_brief_id BIGINT REFERENCES trend_briefs(id) ON DELETE SET NULL,
  provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 64),
  amount_usd NUMERIC(12,6) NOT NULL CHECK (amount_usd >= 0),
  business_day DATE NOT NULL,                        -- budget_state.day в момент резерва
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','settled','released')),
  actual_usd NUMERIC(12,6) CHECK (actual_usd >= 0),  -- фактический расход (settle)
  generation_event_id BIGINT REFERENCES generation_events(id) ON DELETE SET NULL,
  release_reason TEXT CHECK (release_reason IN
    ('call_failed','task_ended','worker_lost','expired','manual')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at TIMESTAMPTZ,
  CONSTRAINT ck_budget_res_closed CHECK ((status = 'open') = (closed_at IS NULL)),
  CONSTRAINT ck_budget_res_settled CHECK (status <> 'settled' OR actual_usd IS NOT NULL),
  CONSTRAINT ck_budget_res_released CHECK ((status = 'released') = (release_reason IS NOT NULL)));
CREATE INDEX ix_budget_res_open_task ON budget_reservations(task_id) WHERE status = 'open';
CREATE INDEX ix_budget_res_open_day ON budget_reservations(business_day) WHERE status = 'open';
CREATE INDEX ix_budget_res_created ON budget_reservations(created_at);   -- уборка закрытых (cleanup)

-- Открытые резервы без живого хозяина — их снимает reaper (release_reason='worker_lost'):
--   * task_not_running — задача уже не running (завершилась, упала, возвращена reaper-ом в очередь);
--   * task_repicked    — задачу взял другой воркер или это уже другая попытка;
--   * no_task_stale    — резерв без задачи старше часа: синхронный вызов API (сухой прогон ниши и т. п.)
--                        или задача удалена уборкой (task_id обнулён ON DELETE SET NULL).
-- Протухший heartbeat сам по себе не повод: сначала reaper задач возвращает задачу в очередь,
-- после этого её резервы попадают сюда как task_not_running.
CREATE VIEW v_budget_orphan_reservations AS
  SELECT r.id, r.task_id, r.worker_id, r.provider, r.amount_usd, r.business_day, r.created_at,
         CASE
           WHEN r.task_id IS NULL THEN 'no_task_stale'
           WHEN t.status IS DISTINCT FROM 'running' THEN 'task_not_running'
           ELSE 'task_repicked'
         END AS orphan_reason
  FROM budget_reservations r
  LEFT JOIN tasks t ON t.id = r.task_id
  WHERE r.status = 'open'
    AND CASE
          WHEN r.task_id IS NULL THEN r.created_at < now() - interval '1 hour'
          ELSE t.status IS DISTINCT FROM 'running'
               OR (r.worker_id IS NOT NULL AND t.locked_by IS DISTINCT FROM r.worker_id)
               OR (r.task_attempt IS NOT NULL AND t.attempts IS DISTINCT FROM r.task_attempt)
        END;
