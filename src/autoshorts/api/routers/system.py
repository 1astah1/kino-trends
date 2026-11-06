"""Здоровье, статус системы и обслуживание. Владелец — M1-B.

- `/health` — живость без БД и без авторизации (healthcheck compose, nginx).
- `/health/ready` — готовность без авторизации (сторож, deploy.sh): БД, миграции, запись в
  MEDIA_ROOT, свободное место (порог `disk_hard_min_gb`). 503 с тем же телом, если что-то не так.
- `/system/status` — воркеры, тяжёлый слот, очередь, ресурсы хоста, бюджет (viewer+).
- `/maintenance/*` — admin; уборку делает задача `cleanup` потока M1-C (`tasks/system.py`),
  пока её нет — 501.

`/system/quota` — контракт M4-A (`routers/quota.py`), `/overview` — M4-B.
"""

from __future__ import annotations

import os
import shutil
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from autoshorts.api.deps import AdminDep, ViewerDep
from autoshorts.api.routers import not_implemented
from autoshorts.api.schemas.common import TaskAccepted
from autoshorts.api.schemas.system import CleanupPreview, HealthResponse, ReadyResponse, SystemStatus
from autoshorts.domain.enums import JobStage, ResourceClass

# теги на эндпоинтах: system и maintenance живут в одном файле
router = APIRouter()

GIB = 1024**3
WORKER_VISIBLE_HOURS = 24  # воркеры с heartbeat старше суток в статусе не показываются
_JOB_STAGES = {s.value for s in JobStage}
_RESOURCE_CLASSES = {c.value for c in ResourceClass}


# ---------------------------------------------------------------- ресурсы хоста


def _existing_ancestor(path: Path) -> Path:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def disk_free_gb(path: Path) -> float:
    return round(shutil.disk_usage(_existing_ancestor(path)).free / GIB, 2)


def mem_available_mb() -> int:
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def psi_some_avg10(resource: str) -> float | None:
    """`/proc/pressure/<cpu|memory>`, строка `some avg10=…`; нет PSI — None."""
    try:
        with open(f"/proc/pressure/{resource}", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("some"):
                    for part in line.split():
                        if part.startswith("avg10="):
                            return float(part.split("=", 1)[1])
    except (OSError, ValueError):
        return None
    return None


def load1() -> float:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return 0.0


def media_writable(media_root: Path) -> tuple[bool, str | None]:
    """Записать и удалить пробный файл в `MEDIA_ROOT/tmp`."""
    probe_dir = media_root / "tmp"
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = probe_dir / f".ready-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True, None
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc.strerror or exc}"


def _app_version() -> str:
    try:
        return version("autoshorts")
    except PackageNotFoundError:
        return "0.0.0"


# ---------------------------------------------------------------- здоровье


@router.get(
    "/health",
    name="health",
    tags=["system"],
    response_model=HealthResponse,
    summary="Живость (без БД, без авторизации)",
)
def health():
    return {"status": "ok"}


@router.get(
    "/health/ready",
    name="ready",
    tags=["system"],
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse, "description": "Не готов (например, migrations_pending)"}},
    summary="Готовность: БД, миграции, запись в MEDIA_ROOT, диск",
)
def ready(request: Request):
    from autoshorts.core.config import get_env
    from autoshorts.db.migrate import schema_state
    from autoshorts.db.pool import connection
    from autoshorts.services.settings import get_runtime_settings

    env = get_env()
    checks: dict[str, dict[str, Any]] = {}
    rs = None
    try:
        with connection(timeout=3) as conn:
            conn.execute("SELECT 1")
            checks["db"] = {"ok": True, "detail": None}
            state = schema_state(conn)
            rs = get_runtime_settings(conn, fallback_to_defaults=True)
        gate = getattr(request.app.state, "schema_gate", None)
        if gate is not None:
            gate.state = state
        checks["migrations"] = (
            {"ok": True, "detail": f"version {state.current}"}
            if state.ok
            else {
                "ok": False,
                "detail": f"migrations_pending: current {state.current}, latest {state.latest}, "
                f"pending {list(state.pending)}",
            }
        )
    except Exception as exc:  # noqa: BLE001 - любой сбой БД = не готов
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        checks["db"] = {"ok": False, "detail": f"db_unavailable: {reason[:200]}"}
        checks["migrations"] = {"ok": False, "detail": "unknown: database unavailable"}
    if rs is None:
        rs = get_runtime_settings(fallback_to_defaults=True, max_age=3600)

    ok, detail = media_writable(env.media_root)
    checks["media_writable"] = {"ok": ok, "detail": detail}
    free = disk_free_gb(env.media_root)
    checks["disk"] = {
        "ok": free >= rs.disk_hard_min_gb,
        "detail": f"free {free} GB, hard_min {rs.disk_hard_min_gb} GB",
    }
    all_ok = all(c["ok"] for c in checks.values())
    body = ReadyResponse.model_validate({"status": "ok" if all_ok else "not_ready", "checks": checks})
    return JSONResponse(status_code=200 if all_ok else 503, content=body.model_dump(mode="json"))


# ---------------------------------------------------------------- статус системы


def _queue(conn: Any) -> dict[str, dict[str, int]]:
    out = {c: {"queued": 0, "running": 0, "deferred": 0} for c in ("heavy", "light")}
    rows = conn.execute(
        """
        SELECT resource_class,
               count(*) FILTER (WHERE status = 'queued' AND defer_reason IS NULL) AS queued,
               count(*) FILTER (WHERE status = 'running') AS running,
               count(*) FILTER (WHERE status = 'queued' AND defer_reason IS NOT NULL) AS deferred
        FROM tasks WHERE status IN ('queued', 'running')
        GROUP BY resource_class
        """
    ).fetchall()
    for r in rows:
        if r["resource_class"] in out:
            out[r["resource_class"]] = {k: int(r[k]) for k in ("queued", "running", "deferred")}
    return out


def _workers(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, resource_class, hostname, pid, version, started_at, heartbeat_at, current_task_id, state
        FROM workers
        WHERE heartbeat_at > now() - make_interval(hours => CAST(%(hours)s AS int))
        ORDER BY resource_class, id
        """,
        {"hours": WORKER_VISIBLE_HOURS},
    ).fetchall()
    return [r for r in rows if r["resource_class"] in _RESOURCE_CLASSES]


def _heavy_slot(conn: Any) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id AS task_id, locked_by AS owner, stage, progress
        FROM tasks WHERE resource_class = 'heavy' AND status = 'running'
        ORDER BY started_at DESC NULLS LAST, id DESC LIMIT 1
        """
    ).fetchone()
    if row is None:
        return {"owner": None, "task_id": None, "stage": None, "pct": None}
    return {
        "owner": row["owner"],
        "task_id": row["task_id"],
        "stage": row["stage"] if row["stage"] in _JOB_STAGES else None,
        "pct": float(row["progress"]) if row["progress"] is not None else None,
    }


def _db_schema_version(conn: Any) -> int | None:
    row = conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()
    return int(row["v"]) if row and row["v"] is not None else None


@router.get(
    "/system/status",
    name="status",
    tags=["system"],
    response_model=SystemStatus,
    summary="Воркеры, ресурсы, очередь, бюджет",
)
def system_status(principal: ViewerDep):
    from autoshorts.core.config import get_env
    from autoshorts.db.pool import connection
    from autoshorts.services.budget import get_budget
    from autoshorts.services.settings import get_runtime_settings

    env = get_env()
    with connection() as conn:
        rs = get_runtime_settings(conn)
        workers = _workers(conn)
        heavy_slot = _heavy_slot(conn)
        queue = _queue(conn)
        budget = get_budget(conn)
        db_schema = _db_schema_version(conn)
    return {
        "workers": workers,
        "heavy_slot": heavy_slot,
        "disk": {
            "free_gb": disk_free_gb(env.media_root),
            "thresholds": {
                "soft_gb": rs.disk_soft_min_gb,
                "hard_gb": rs.disk_hard_min_gb,
                "emergency_gb": rs.disk_emergency_gb,
            },
        },
        "load1": load1(),
        "psi": {"cpu10": psi_some_avg10("cpu"), "mem10": psi_some_avg10("memory")},
        "mem_available_mb": mem_available_mb(),
        "queue": queue,
        "budget": budget,
        "versions": {"app": _app_version(), "db_schema": db_schema},
    }


# ---------------------------------------------------------------- обслуживание


@router.post(
    "/maintenance/cleanup-preview",
    name="cleanup_preview",
    tags=["maintenance"],
    response_model=CleanupPreview,
    summary="Оценить уборку файлов",
)
def cleanup_preview(principal: AdminDep):
    # политика хранения (§8.2) и обход MEDIA_ROOT — задача cleanup потока M1-C (tasks/system.py)
    return not_implemented()


@router.post(
    "/maintenance/cleanup",
    name="cleanup",
    tags=["maintenance"],
    status_code=202,
    response_model=TaskAccepted,
    summary="Запустить уборку (admin)",
)
def cleanup(principal: AdminDep):
    # постановка задачи cleanup — через services/tasks.py потока M1-C, когда он появится
    return not_implemented()
