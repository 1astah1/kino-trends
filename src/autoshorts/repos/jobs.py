"""SQL роликов `jobs` и справочных чтений для их создания (docs/ARCHITECTURE.md §4.2, §5.2, §6.2).

Только SQL: функции принимают открытое соединение (`conn`, строки — dict) и не управляют
транзакциями. Переходы статусов — оптимистичные: `UPDATE … WHERE id = … AND status = ANY(allowed)`;
ноль строк — сервис отвечает 409 `job_state_conflict` (веб и бот нажали одновременно, §6.2).

Чтения чужих областей (каналы, ниши, кампании, варианты, источники, кандидаты, пакеты,
публикации) здесь узкие — ровно то, что нужно ролику; полноценные репозитории этих таблиц
принадлежат своим потокам (M2-A, M3-A, M5).

Параметры внутри CASE и интервальной арифметики — только через CAST / make_interval (§1.5).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]

JOB_COLUMNS: tuple[str, ...] = (
    "id",
    "video_uid",
    "kind",
    "status",
    "stage",
    "channel_id",
    "niche_id",
    "niche_version",
    "product_id",
    "campaign_id",
    "banner_variant_id",
    "trend_brief_id",
    "topic",
    "template_version",
    "stage_payload",
    "config_snapshot",
    "reject_reason",
    "attempt_count",
    "review_sent_at",
    "duration_sec",
    "cost_usd",
    "priority",
    "scheduled_for",
    "parent_job_id",
    "qc_failed_checks",
    "qc_warnings",
    "review_comment",
    "rework_from_stage",
    "error_class",
    "error",
    "fingerprint",
    "created_by",
    "deleted_at",
    "source_id",
    "clip_candidate_id",
    "clip_start_sec",
    "clip_end_sec",
    "reframe_mode",
    "author_layer",
    "author_share",
    "created_at",
    "updated_at",
)
_J = ", ".join(f"j.{c}" for c in JOB_COLUMNS)
_SELECT_JOB = f"""
    SELECT {_J},
           c.title AS channel_title, c.platform AS channel_platform,
           bv.code AS variant_code, bv.name AS variant_name
    FROM jobs j
    JOIN channels c ON c.id = j.channel_id
    LEFT JOIN banner_variants bv ON bv.id = j.banner_variant_id
"""

# Колонки, которые пишут сервисы (insert/update). JSONB-колонки оборачиваются в Jsonb.
WRITABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "video_uid",
        "kind",
        "status",
        "stage",
        "channel_id",
        "niche_id",
        "niche_version",
        "product_id",
        "campaign_id",
        "banner_variant_id",
        "trend_brief_id",
        "topic",
        "template_version",
        "stage_payload",
        "config_snapshot",
        "reject_reason",
        "attempt_count",
        "review_sent_at",
        "duration_sec",
        "priority",
        "scheduled_for",
        "parent_job_id",
        "qc_failed_checks",
        "qc_warnings",
        "review_comment",
        "rework_from_stage",
        "error_class",
        "error",
        "fingerprint",
        "created_by",
        "deleted_at",
        "source_id",
        "clip_candidate_id",
        "clip_start_sec",
        "clip_end_sec",
        "reframe_mode",
        "author_layer",
        "author_share",
    }
)
JSONB_COLUMNS: frozenset[str] = frozenset(
    {"stage_payload", "config_snapshot", "qc_warnings", "fingerprint", "author_layer"}
)
ACTIVE_TASK_STATUSES = ("queued", "running")
REVIEW_STATUSES = ("pending_review", "qc_failed")


def _param(column: str, value: Any) -> Any:
    if column in JSONB_COLUMNS and value is not None:
        return Jsonb(value)
    if column == "qc_failed_checks" and value is not None:
        return list(value)
    return value


def _check_columns(fields: Iterable[str]) -> None:
    bad = set(fields) - WRITABLE_COLUMNS
    if bad:
        raise ValueError(f"jobs: columns not writable: {sorted(bad)}")


# ---------------------------------------------------------------- чтение одного


def get(conn: Any, job_id: int, *, for_update: bool = False, include_deleted: bool = False) -> Row | None:
    deleted = "" if include_deleted else " AND j.deleted_at IS NULL"
    lock = " FOR UPDATE OF j" if for_update else ""
    return conn.execute(f"{_SELECT_JOB} WHERE j.id = %(id)s{deleted}{lock}", {"id": job_id}).fetchone()


def get_by_video_uid(conn: Any, video_uid: str, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE OF j" if for_update else ""
    return conn.execute(
        f"{_SELECT_JOB} WHERE j.video_uid = %(uid)s AND j.deleted_at IS NULL{lock}", {"uid": video_uid}
    ).fetchone()


def get_by_package_code(conn: Any, code: str) -> Row | None:
    """Ролик по коду пакета (трекинг-ссылка уровня package, §6.6)."""
    return conn.execute(
        f"""{_SELECT_JOB}
        WHERE j.deleted_at IS NULL AND j.id = (
          SELECT tl.job_id FROM tracking_links tl
          WHERE tl.code = %(code)s AND tl.job_id IS NOT NULL
          ORDER BY tl.id DESC LIMIT 1)""",
        {"code": code},
    ).fetchone()


def video_uid_exists(conn: Any, video_uid: str) -> bool:
    row = conn.execute("SELECT 1 AS x FROM jobs WHERE video_uid = %(uid)s", {"uid": video_uid}).fetchone()
    return row is not None


# ---------------------------------------------------------------- запись


def insert(conn: Any, fields: dict[str, Any]) -> Row:
    _check_columns(fields)
    names = sorted(fields)
    query = sql.SQL("INSERT INTO jobs ({cols}) VALUES ({vals}) RETURNING id").format(
        cols=sql.SQL(", ").join(sql.Identifier(n) for n in names),
        vals=sql.SQL(", ").join(sql.Placeholder(n) for n in names),
    )
    row = conn.execute(query, {n: _param(n, fields[n]) for n in names}).fetchone()
    job = get(conn, int(row["id"]))
    assert job is not None
    return job


def update(
    conn: Any,
    job_id: int,
    fields: dict[str, Any],
    *,
    allowed_statuses: Sequence[str] | None = None,
    merge_payload: dict[str, Any] | None = None,
    drop_payload_keys: Sequence[str] = (),
) -> Row | None:
    """Обновить поля; `allowed_statuses` — оптимистичная проверка статуса (None — без проверки).

    `merge_payload` — поверхностный мерж в `stage_payload` (`||`), `drop_payload_keys` — удалить ключи.
    Ноль строк (ролика нет или статус уже другой) — None.
    """
    _check_columns(fields)
    sets = [sql.SQL("{} = {}").format(sql.Identifier(n), sql.Placeholder(n)) for n in sorted(fields)]
    params: dict[str, Any] = {n: _param(n, v) for n, v in fields.items()}
    if merge_payload or drop_payload_keys:
        expr = sql.SQL("stage_payload")
        if drop_payload_keys:
            expr = sql.SQL("({}) - CAST(%(_drop)s AS text[])").format(expr)
            params["_drop"] = list(drop_payload_keys)
        if merge_payload:
            expr = sql.SQL("({}) || %(_merge)s").format(expr)
            params["_merge"] = Jsonb(merge_payload)
        sets.append(sql.SQL("stage_payload = {}").format(expr))
    sets.append(sql.SQL("updated_at = now()"))
    where = [sql.SQL("id = %(_id)s"), sql.SQL("deleted_at IS NULL")]
    params["_id"] = job_id
    if allowed_statuses is not None:
        where.append(sql.SQL("status = ANY(%(_allowed)s)"))
        params["_allowed"] = list(allowed_statuses)
    query = sql.SQL("UPDATE jobs SET {sets} WHERE {where} RETURNING id").format(
        sets=sql.SQL(", ").join(sets), where=sql.SQL(" AND ").join(where)
    )
    row = conn.execute(query, params).fetchone()
    if row is None:
        return None
    return get(conn, job_id)


def set_review_sent(conn: Any, job_id: int, when: datetime | None) -> None:
    conn.execute(
        "UPDATE jobs SET review_sent_at = COALESCE(CAST(%(t)s AS timestamptz), now()) WHERE id = %(id)s",
        {"id": job_id, "t": when},
    )


# ---------------------------------------------------------------- списки


def list_jobs(
    conn: Any,
    *,
    kind: str | None = None,
    statuses: Sequence[str] | None = None,
    stages: Sequence[str] | None = None,
    channel_id: str | None = None,
    niche_id: str | None = None,
    product_id: str | None = None,
    campaign_id: int | None = None,
    banner_variant_id: int | None = None,
    source_id: int | None = None,
    q: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    order: sql.Composable,
    limit: int,
    offset: int,
) -> tuple[list[Row], int]:
    where: list[str] = ["j.deleted_at IS NULL"]
    params: dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
    simple = {
        "kind": kind,
        "channel_id": channel_id,
        "niche_id": niche_id,
        "product_id": product_id,
        "campaign_id": campaign_id,
        "banner_variant_id": banner_variant_id,
        "source_id": source_id,
    }
    for name, value in simple.items():
        if value is not None:
            where.append(f"j.{name} = %({name})s")
            params[name] = value
    if statuses:
        where.append("j.status = ANY(%(statuses)s)")
        params["statuses"] = list(statuses)
    if stages:
        where.append("j.stage = ANY(%(stages)s)")
        params["stages"] = list(stages)
    if q:
        where.append(
            "(j.video_uid ILIKE %(q_prefix)s OR j.topic ILIKE %(q_any)s OR bv.code = %(q_exact)s"
            " OR CAST(j.id AS text) = %(q_exact)s)"
        )
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.update({"q_prefix": f"{escaped}%", "q_any": f"%{escaped}%", "q_exact": q})
    if created_from is not None:
        where.append("j.created_at >= %(t_from)s")
        params["t_from"] = created_from
    if created_to is not None:
        where.append("j.created_at < %(t_to)s")
        params["t_to"] = created_to
    cond = " AND ".join(where)
    base = """
        FROM jobs j
        JOIN channels c ON c.id = j.channel_id
        LEFT JOIN banner_variants bv ON bv.id = j.banner_variant_id
    """
    total = conn.execute(f"SELECT count(*) AS n {base} WHERE {cond}", params).fetchone()["n"]
    # подзапрос: ORDER BY из белого списка (`pagination.order_by`) ссылается на неквалифицированные
    # имена (`created_at`, `id`), которые в JOIN с channels были бы неоднозначны
    query = sql.SQL(
        "SELECT * FROM (SELECT {cols}, c.title AS channel_title, c.platform AS channel_platform,"
        " bv.code AS variant_code, bv.name AS variant_name {base} WHERE {cond}) x {order}"
        " LIMIT %(limit)s OFFSET %(offset)s"
    ).format(
        cols=sql.SQL(_J),
        base=sql.SQL(base),
        cond=sql.SQL(cond),
        order=order,
    )
    rows = conn.execute(query, params).fetchall()
    return rows, int(total)


def stage_counts(conn: Any) -> list[Row]:
    return conn.execute(
        """
        SELECT status, stage, count(*) AS count FROM jobs
        WHERE deleted_at IS NULL
        GROUP BY status, stage
        ORDER BY status, stage NULLS FIRST
        """
    ).fetchall()


def current_progress(conn: Any, job_ids: Sequence[int]) -> dict[int, float | None]:
    """Прогресс активной задачи ролика (running, иначе queued) — для строк списка."""
    if not job_ids:
        return {}
    rows = conn.execute(
        """
        SELECT DISTINCT ON (job_id) job_id, progress
        FROM tasks
        WHERE job_id = ANY(%(ids)s) AND status IN ('queued','running')
        ORDER BY job_id, (status = 'running') DESC, id DESC
        """,
        {"ids": list(job_ids)},
    ).fetchall()
    return {int(r["job_id"]): (float(r["progress"]) if r["progress"] is not None else None) for r in rows}


# ---------------------------------------------------------------- задачи ролика


def tasks_for_job(conn: Any, job_id: int) -> list[Row]:
    """Все задачи ролика по времени + стоимость из generation_events."""
    return conn.execute(
        """
        SELECT t.id, t.kind, t.stage, t.status, t.attempts, t.max_attempts, t.progress, t.priority,
               t.resource_class, t.started_at, t.finished_at, t.created_at, t.updated_at,
               t.error_class, t.last_error, t.payload, t.cancel_requested, t.dedup_key,
               COALESCE((SELECT sum(g.cost_usd) FROM generation_events g WHERE g.task_id = t.id), 0)
                 AS cost_usd
        FROM tasks t
        WHERE t.job_id = %(id)s
        ORDER BY t.created_at, t.id
        """,
        {"id": job_id},
    ).fetchall()


def active_tasks(conn: Any, job_id: int, *, kinds: Sequence[str] | None = None) -> list[Row]:
    return conn.execute(
        """
        SELECT id, kind, stage, status, created_at FROM tasks
        WHERE job_id = %(id)s AND status IN ('queued','running')
          AND (CAST(%(kinds)s AS text[]) IS NULL OR kind = ANY(CAST(%(kinds)s AS text[])))
        ORDER BY id
        """,
        {"id": job_id, "kinds": list(kinds) if kinds is not None else None},
    ).fetchall()


def tasks_created_since(conn: Any, job_id: int, since: datetime) -> list[Row]:
    return conn.execute(
        """
        SELECT id, kind, stage, status, created_at FROM tasks
        WHERE job_id = %(id)s AND created_at >= %(since)s
        ORDER BY id
        """,
        {"id": job_id, "since": since},
    ).fetchall()


def set_task_priority(conn: Any, task_id: int, priority: int) -> None:
    conn.execute(
        "UPDATE tasks SET priority = %(p)s, updated_at = now() WHERE id = %(id)s AND status = 'queued'",
        {"id": task_id, "p": int(priority)},
    )


# ---------------------------------------------------------------- приёмка


def review_queue(conn: Any, *, channel_id: str | None = None, limit: int = 500) -> list[Row]:
    """Ролики в pending_review | qc_failed: сначала более старые (FIFO), затем приоритет."""
    return conn.execute(
        f"""
        SELECT {_J}, c.title AS channel_title, c.platform AS channel_platform,
               bv.code AS variant_code, bv.name AS variant_name,
               COALESCE((SELECT max(t.finished_at) FROM tasks t
                         WHERE t.job_id = j.id AND t.stage = 'qc' AND t.status = 'succeeded'),
                        j.updated_at) AS ready_at
        FROM jobs j
        JOIN channels c ON c.id = j.channel_id
        LEFT JOIN banner_variants bv ON bv.id = j.banner_variant_id
        WHERE j.deleted_at IS NULL AND j.status IN ('pending_review','qc_failed')
          AND (CAST(%(ch)s AS text) IS NULL OR j.channel_id = CAST(%(ch)s AS text))
        ORDER BY ready_at, j.priority, j.id
        LIMIT %(limit)s
        """,
        {"ch": channel_id, "limit": int(limit)},
    ).fetchall()


def review_queue_count(conn: Any) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE deleted_at IS NULL AND status IN ('pending_review','qc_failed')"
    ).fetchone()
    return int(row["n"])


def unsent_review_jobs(conn: Any, *, limit: int = 10) -> list[Row]:
    """Ролики для карточки в Telegram: ждут приёмки, карточка ещё не отправлена."""
    return conn.execute(
        f"""{_SELECT_JOB}
        WHERE j.deleted_at IS NULL AND j.status IN ('pending_review','qc_failed')
          AND j.review_sent_at IS NULL
        ORDER BY j.updated_at, j.id
        LIMIT %(limit)s""",
        {"limit": int(limit)},
    ).fetchall()


# ---------------------------------------------------------------- справочные чтения для создания


def get_channel(conn: Any, channel_id: str) -> Row | None:
    return conn.execute(
        """
        SELECT id, niche_id, platform, cluster, title, status, default_product_id, profile,
               platform_channel_ref
        FROM channels WHERE id = %(id)s
        """,
        {"id": channel_id},
    ).fetchone()


def get_niche(conn: Any, niche_id: str) -> Row | None:
    return conn.execute(
        "SELECT id, label, status, current_version, config FROM niches WHERE id = %(id)s", {"id": niche_id}
    ).fetchone()


def get_campaign(conn: Any, campaign_id: int) -> Row | None:
    return conn.execute(
        "SELECT id, product_id, name, status, allocation, targeting FROM campaigns WHERE id = %(id)s",
        {"id": campaign_id},
    ).fetchone()


def get_variant(conn: Any, variant_id: int) -> Row | None:
    """Вариант баннера + кампания и продукт (снимок в `config_snapshot.banner`)."""
    return conn.execute(
        """
        SELECT v.id, v.campaign_id, v.code, v.name, v.status, v.weight, v.version, v.layers, v.timing,
               v.ad_marking, v.code_url_template, v.voice_cta_text, v.pinned_comment_template,
               v.description_cta_template, c.product_id, c.status AS campaign_status
        FROM banner_variants v JOIN campaigns c ON c.id = v.campaign_id
        WHERE v.id = %(id)s
        """,
        {"id": variant_id},
    ).fetchone()


def get_trend_brief(conn: Any, brief_id: int) -> Row | None:
    return conn.execute("SELECT id FROM trend_briefs WHERE id = %(id)s", {"id": brief_id}).fetchone()


def get_source(conn: Any, source_id: int) -> Row | None:
    return conn.execute(
        """
        SELECT s.id, s.title, s.origin, s.status, s.duration_sec, s.niche_id, s.deleted_at,
               r.rights_type, r.valid_until, r.source_id AS rights_source_id
        FROM sources s LEFT JOIN source_rights r ON r.source_id = s.id
        WHERE s.id = %(id)s
        """,
        {"id": source_id},
    ).fetchone()


def get_candidate(conn: Any, candidate_id: int, *, for_update: bool = False) -> Row | None:
    lock = " FOR UPDATE OF cc" if for_update else ""
    return conn.execute(
        f"""
        SELECT cc.id, cc.source_id, cc.start_sec, cc.end_sec, cc.title, cc.hook_text, cc.status,
               cc.job_id, j.status AS job_status
        FROM clip_candidates cc LEFT JOIN jobs j ON j.id = cc.job_id
        WHERE cc.id = %(id)s{lock}
        """,
        {"id": candidate_id},
    ).fetchone()


def link_candidate(conn: Any, candidate_id: int, job_id: int) -> None:
    """Кандидат ушёл в рендер: `job_id` + статус `used` (§6.3)."""
    conn.execute(
        """
        UPDATE clip_candidates SET job_id = %(job)s, status = 'used', updated_at = now()
        WHERE id = %(id)s
        """,
        {"id": candidate_id, "job": job_id},
    )


def source_ref(conn: Any, job: Row) -> Row | None:
    if job.get("source_id") is None:
        return None
    return conn.execute(
        "SELECT id, title FROM sources WHERE id = %(id)s", {"id": job["source_id"]}
    ).fetchone()


# ---------------------------------------------------------------- пакеты и публикации ролика


def packages_for_job(conn: Any, job_id: int) -> list[Row]:
    return conn.execute(
        """
        SELECT p.id, p.status, p.downloaded_at, p.created_at, tl.code
        FROM upload_packages p LEFT JOIN tracking_links tl ON tl.id = p.tracking_link_id
        WHERE p.job_id = %(id)s
        ORDER BY p.id DESC
        """,
        {"id": job_id},
    ).fetchall()


def supersede_ready_packages(conn: Any, job_id: int, *, include_downloaded: bool = False) -> int:
    """Убрать живые пакеты ролика из «К заливке».

    По умолчанию — только нескачанный пакет (отмена одобрения: скачанный файл оператор мог уже залить
    руками). `include_downloaded` — отмена ролика: уходит и скачанный («ждёт ссылку»), иначе старую
    версию принимают за актуальную. Залитое (published) не трогаем."""
    if include_downloaded:
        cond = "status IN ('ready', 'downloaded')"
    else:
        cond = "status = 'ready' AND downloaded_at IS NULL"
    cur = conn.execute(
        f"UPDATE upload_packages SET status = 'superseded', updated_at = now()"
        f" WHERE job_id = %(id)s AND {cond}",
        {"id": job_id},
    )
    return cur.rowcount


def mark_packages_published(conn: Any, job_id: int) -> int:
    cur = conn.execute(
        """
        UPDATE upload_packages SET status = 'published', updated_at = now()
        WHERE job_id = %(id)s AND status IN ('ready','downloaded')
        """,
        {"id": job_id},
    )
    return cur.rowcount


def publications_for_job(conn: Any, job_id: int) -> list[Row]:
    return conn.execute(
        """
        SELECT id, platform, platform_video_id, url, status, published_at, created_at
        FROM publications WHERE job_id = %(id)s AND deleted_at IS NULL
        ORDER BY id
        """,
        {"id": job_id},
    ).fetchall()


def insert_publication(conn: Any, fields: dict[str, Any]) -> Row:
    """Минимальная регистрация публикации (мост `/videoid` бота до `services.publications`, M3-A)."""
    names = sorted(fields)
    query = sql.SQL("INSERT INTO publications ({cols}) VALUES ({vals}) RETURNING *").format(
        cols=sql.SQL(", ").join(sql.Identifier(n) for n in names),
        vals=sql.SQL(", ").join(sql.Placeholder(n) for n in names),
    )
    return conn.execute(query, fields).fetchone()


def find_publication(conn: Any, platform: str, platform_video_id: str) -> Row | None:
    return conn.execute(
        """
        SELECT id, job_id FROM publications
        WHERE platform = %(p)s AND platform_video_id = %(v)s AND deleted_at IS NULL
        """,
        {"p": platform, "v": platform_video_id},
    ).fetchone()


# ---------------------------------------------------------------- похожесть


def fingerprints_for_similarity(
    conn: Any, job_id: int, *, channel_id: str | None, limit: int = 200
) -> list[Row]:
    """Отпечатки последних роликов (канала, если задан) кроме самого ролика (§6.2 qc)."""
    return conn.execute(
        """
        SELECT id, video_uid, channel_id, fingerprint FROM jobs
        WHERE id <> %(id)s AND fingerprint IS NOT NULL AND deleted_at IS NULL
          AND (CAST(%(ch)s AS text) IS NULL OR channel_id = CAST(%(ch)s AS text))
        ORDER BY created_at DESC
        LIMIT %(limit)s
        """,
        {"id": job_id, "ch": channel_id, "limit": int(limit)},
    ).fetchall()


def jobs_brief(conn: Any, job_ids: Sequence[int]) -> dict[int, Row]:
    if not job_ids:
        return {}
    rows = conn.execute(
        "SELECT id, video_uid, channel_id FROM jobs WHERE id = ANY(%(ids)s)", {"ids": list(job_ids)}
    ).fetchall()
    return {int(r["id"]): r for r in rows}


# ---------------------------------------------------------------- артефакты (копия для adapt)


def copy_artifacts(conn: Any, from_job_id: int, to_job_id: int, stages: Sequence[str]) -> int:
    """Скопировать живые артефакты стадий `stages` в другой ролик (те же файлы, мастер — нет)."""
    cur = conn.execute(
        """
        INSERT INTO artifacts (job_id, kind, idx, path, bytes, sha256, input_hash, stage, is_master)
        SELECT %(to)s, kind, idx, path, bytes, sha256, input_hash, stage, false
        FROM artifacts
        WHERE job_id = %(from)s AND deleted_at IS NULL AND stage = ANY(%(stages)s)
        ON CONFLICT DO NOTHING
        """,
        {"from": from_job_id, "to": to_job_id, "stages": list(stages)},
    )
    return cur.rowcount


def restore_artifacts_deleted_at(conn: Any, job_id: int, deleted_at: datetime) -> int:
    """Undo rework: вернуть артефакты, помеченные в той же транзакции, что и решение."""
    cur = conn.execute(
        """
        UPDATE artifacts SET deleted_at = NULL
        WHERE job_id = %(id)s AND deleted_at = %(t)s
          AND NOT EXISTS (
            SELECT 1 FROM artifacts a2
            WHERE a2.job_id = artifacts.job_id AND a2.kind = artifacts.kind AND a2.idx = artifacts.idx
              AND a2.input_hash = artifacts.input_hash AND a2.deleted_at IS NULL)
        """,
        {"id": job_id, "t": deleted_at},
    )
    return cur.rowcount


# ---------------------------------------------------------------- идемпотентность POST /jobs


def idempotency_claim(conn: Any, user_id: int, key: str) -> Row | None:
    """Занять ключ: вставить заглушку. Уже есть — вернуть сохранённый ответ (вставки нет).

    Параллельный запрос с тем же ключом ждёт на уникальном индексе, пока первый не закончит
    транзакцию, и затем получает его ответ.
    """
    row = conn.execute(
        """
        INSERT INTO idempotency_keys (key, user_id, response) VALUES (%(k)s, %(u)s, '{}'::jsonb)
        ON CONFLICT (user_id, key) DO NOTHING
        RETURNING key
        """,
        {"k": key, "u": user_id},
    ).fetchone()
    if row is not None:
        return None
    return conn.execute(
        "SELECT response, created_at FROM idempotency_keys WHERE user_id = %(u)s AND key = %(k)s",
        {"k": key, "u": user_id},
    ).fetchone()


def idempotency_store(conn: Any, user_id: int, key: str, response: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE idempotency_keys SET response = %(r)s WHERE user_id = %(u)s AND key = %(k)s",
        {"k": key, "u": user_id, "r": Jsonb(response)},
    )


# ---------------------------------------------------------------- история


def audit_for_job(conn: Any, job_id: int, *, limit: int = 500) -> list[Row]:
    return conn.execute(
        """
        SELECT a.id, a.occurred_at, a.actor_kind, a.actor_user_id, a.action, a.summary, a.diff,
               u.display_name AS actor_name
        FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id
        WHERE a.entity_type = 'job' AND a.entity_id = %(id)s
        ORDER BY a.occurred_at, a.id
        LIMIT %(limit)s
        """,
        {"id": str(job_id), "limit": int(limit)},
    ).fetchall()


def generation_cost_by_task(conn: Any, task_ids: Sequence[int]) -> dict[int, float]:
    if not task_ids:
        return {}
    rows = conn.execute(
        """
        SELECT task_id, sum(cost_usd) AS cost FROM generation_events
        WHERE task_id = ANY(%(ids)s) GROUP BY task_id
        """,
        {"ids": list(task_ids)},
    ).fetchall()
    return {int(r["task_id"]): float(r["cost"] or 0) for r in rows}
