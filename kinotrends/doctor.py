"""Доктор системы: что происходит со стендом auto-shorts, хранилищем и конвейером, где сломалось.

Раз в 5 минут (поток планировщика в api.py) и по кнопке «проверить сейчас»:
1. снимает пробы — стенд (диск, память, процессы, очередь задач, ролики, публикации, логи) одним SSH-заходом,
   хранилище (диск, раздача файлов, yt-dlp), конвейер и нейросети — из своей базы;
2. оценивает каждую проверку: ok / warn / fail, с цифрой, подробностями и подсказкой «что делать»;
3. ведёт журнал инцидентов (открыт, сколько раз видели, когда закрылся) и шлёт в Telegram новые поломки
   и починки, если задан бот;
4. само чинит то, что безопасно чинить: поднимает упавший сервис стенда, запускает аварийную уборку при
   нехватке диска, режет разросшиеся логи.

CLI для разбора: `python -m kinotrends.doctor` (текстом), `--json`, `--no-fix`.
"""
from __future__ import annotations

import json
import shlex
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import dumps, get_meta, loads, now_iso, set_meta

# адрес стенда, строка базы и панель — в настройках (stand_host, stand_db_url, stand_web; база var/, не в git)
STAND_HOST = ""
STAND_ROOT = "/home/devaccess/auto-shorts"
STAND_DB = ""
STAND_WEB = ""
STAND_SERVICES = ("api", "web", "worker-light", "worker-heavy")
RUN_EVERY_MIN = 5
KEEP_RUNS = 600  # ~2 суток по 5 минут

# пороги
DISK_FAIL_GB, DISK_WARN_GB = 4.0, 8.0  # у стенда «пауза тяжёлых задач» с 4 ГБ (disk_hard_min_gb)
STORAGE_FAIL_GB, STORAGE_WARN_GB = 3.0, 10.0
MEM_WARN_MB = 250
LOAD_WARN_PER_CORE = 3.0
DEFER_WARN_MIN = 45  # задача ждёт ресурса дольше — уже не «чуть подождать»
STUCK_JOB_HOURS = 3
REVIEW_TARGET = 10  # владелец: в приёмке всегда минимум 10 роликов
NO_OUTPUT_HOURS = 4  # конвейер включён, а новых роликов в приёмку нет столько часов
TICK_STALE_MIN = 20
LOG_ROTATE_MB = 60
LOG_KEEP_MB = 15
# строки логов, которые не поломка (перезапуск сервиса обрывает потоки видео и событий)
LOG_NOISE = ("timeout graceful shutdown exceeded", "CancelledError", "Application shutdown")

STATUS_ORDER = {"ok": 0, "info": 0, "warn": 1, "fail": 2}
GROUP_ORDER = ("Стенд", "Очередь", "Ролики", "Приёмка", "Публикация", "Конвейер", "Хранилище", "Нейросети")


@dataclass
class Check:
    id: str
    group: str
    title: str
    status: str  # ok | warn | fail | info
    value: str = ""
    detail: str = ""
    hint: str = ""
    data: Any = None


@dataclass
class Report:
    at: str
    status: str
    checks: list[Check] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # сами пробы не удались
    logs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"at": self.at, "status": self.status, "checks": [asdict(c) for c in self.checks],
                "actions": self.actions, "errors": self.errors, "logs": self.logs}


# ---------------------------------------------------------------- пробы

STAND_PROBE = r'''
import decimal, glob, json, os, shutil, subprocess, sys
root, db_url, web, storage_health = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
out = {}
du = shutil.disk_usage(root)
out["disk_free_gb"] = round(du.free / 1e9, 2)
out["disk_total_gb"] = round(du.total / 1e9, 1)
mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
out["mem_avail_mb"] = mem.get("MemAvailable", 0) // 1024
out["mem_total_mb"] = mem.get("MemTotal", 0) // 1024
out["load1"] = round(os.getloadavg()[0], 2)
out["cores"] = os.cpu_count() or 1
svcs = {}
for pf in glob.glob(root + "/var/stand/*.pid"):
    name = os.path.basename(pf)[:-4]
    try:
        pid = int(open(pf).read().strip()); os.kill(pid, 0); svcs[name] = True
    except Exception:
        svcs[name] = False
out["services"] = svcs
def http(url):
    try:
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-m", "10", "-w", "%{http_code}", url], capture_output=True, text=True, timeout=15)
        return r.stdout.strip() or "000"
    except Exception:
        return "000"
out["http"] = {"api": http("http://127.0.0.1:18300/api/v1/health/ready"), "web": http(web + "/review/"),
               "storage": http(storage_health) if storage_health else ""}
logs = {}
for f in glob.glob(root + "/var/stand/logs/*.log"):
    try:
        size = os.path.getsize(f)
        with open(f, "rb") as fh:
            fh.seek(max(0, size - 400000))
            tail = fh.read().decode("utf-8", "replace").splitlines()
        bad = [l for l in tail if ("ERROR" in l or "Traceback" in l or "CRITICAL" in l or " WARNING " in l)]
        logs[os.path.basename(f)] = {"size_mb": round(size / 1e6, 1), "lines": bad[-40:]}
    except Exception as e:
        logs[os.path.basename(f)] = {"error": str(e)}
out["logs"] = logs
try:
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(db_url, row_factory=dict_row, connect_timeout=10) as c:
        q = lambda sql: [dict(r) for r in c.execute(sql).fetchall()]
        out["deferred"] = q("select defer_reason, kind, count(*) n, round(extract(epoch from now() - min(created_at)) / 60) oldest_min from tasks where status = 'queued' and defer_reason is not null group by 1, 2 order by 3 desc")
        out["queued"] = q("select kind, resource_class, count(*) n from tasks where status = 'queued' group by 1, 2")
        out["running"] = q("select id, kind, coalesce(job_id, 0) job_id, coalesce(source_id, 0) source_id, coalesce(stage, '') stage, round(extract(epoch from now() - coalesce(heartbeat_at, started_at)) / 60) silent_min from tasks where status = 'running' order by id")
        out["failed_tasks_24h"] = q("select kind, coalesce(error_class, '') error_class, count(*) n, max(left(coalesce(last_error, ''), 240)) sample from tasks where status = 'failed' and coalesce(finished_at, updated_at) > now() - interval '24 hours' group by 1, 2 order by 3 desc limit 12")
        out["jobs"] = q("select status, count(*) n from jobs where deleted_at is null group by 1")
        out["stuck_jobs"] = q("select id, status, coalesce(stage, '') stage, channel_id, round(extract(epoch from now() - updated_at) / 3600, 1) hours from jobs where status in ('processing', 'queued') and updated_at < now() - interval '3 hours' and deleted_at is null order by id")
        out["failed_jobs_24h"] = q("select id, coalesce(stage, '') stage, channel_id, left(coalesce(error, ''), 240) error from jobs where status = 'failed' and updated_at > now() - interval '24 hours' order by id desc limit 20")
        out["qc_failed"] = q("select id, channel_id, qc_failed_checks checks, round(extract(epoch from now() - updated_at) / 60) age_min from jobs where status = 'qc_failed' and deleted_at is null order by id")
        out["review_ready"] = q("select count(*) n from jobs where status = 'pending_review' and deleted_at is null")[0]["n"]
        out["qc_done_6h"] = q("select count(*) n from artifacts where kind = 'qc_report' and created_at > now() - interval '6 hours'")[0]["n"]
        out["last_qc_min"] = q("select round(extract(epoch from now() - max(created_at)) / 60) m from artifacts where kind = 'qc_report'")[0]["m"]
        out["autopublish_errors"] = q("select p.id, p.channel_id, p.autopublish_state, left(coalesce(p.autopublish_error, ''), 240) error from upload_packages p where p.autopublish_error is not null and p.updated_at > now() - interval '24 hours' and p.status <> 'superseded' and coalesce(p.autopublish_state, '') <> 'cancelled' order by p.id desc limit 10")
        out["autopublish_next"] = q("select p.channel_id, count(*) n, min(p.autopublish_at) next_at from upload_packages p where p.autopublish_state in ('scheduled', 'queued') group by 1")
        out["oauth"] = q("select c.id channel_id, c.status, o.google_channel_title, o.last_error, (o.refresh_token_secret is not null) connected, coalesce(o.scopes::text, '') scopes, o.last_ok_at from channels c left join channel_oauth o on o.channel_id = c.id where c.status not in ('terminated', 'retired') order by c.id")
        out["cleanup_last_min"] = q("select round(extract(epoch from now() - max(finished_at)) / 60) m from tasks where kind = 'cleanup' and status = 'succeeded'")[0]["m"]
        out["published_24h"] = q("select count(*) n from publications where created_at > now() - interval '24 hours'")[0]["n"]
except Exception as e:
    out["db_error"] = str(e)[:300]
print(json.dumps(out, ensure_ascii=False, default=lambda o: float(o) if isinstance(o, decimal.Decimal) else str(o)))
'''


def _ssh(host: str, key: str, command: str, stdin: str | None = None, timeout: int = 90) -> subprocess.CompletedProcess:
    cmd = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", f"root@{host}", command]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)


def stand_cfg(settings: dict) -> dict:
    import os
    return {"host": (settings.get("stand_host") or STAND_HOST).strip(),
            "key": os.path.expanduser((settings.get("storage_key") or "~/.ssh/kt_storage").strip()),
            "root": (settings.get("stand_root") or STAND_ROOT).strip(),
            "db": (settings.get("stand_db_url") or STAND_DB).strip(),
            "web": (settings.get("stand_web") or STAND_WEB).strip().rstrip("/")}


def probe_stand(settings: dict) -> dict:
    cfg = stand_cfg(settings)
    storage_url = (settings.get("storage_url") or "").strip().rstrip("/")
    health = storage_url.rsplit("/files", 1)[0] + "/health" if storage_url else ""
    py = f"{cfg['root']}/.venv/bin/python"
    cmd = " ".join(shlex.quote(x) for x in (py, "-", cfg["root"], cfg["db"], cfg["web"], health))
    try:
        r = _ssh(cfg["host"], cfg["key"], cmd, stdin=STAND_PROBE, timeout=120)
    except subprocess.TimeoutExpired:
        return {"probe_error": "стенд не ответил за 120 с"}
    if r.returncode != 0 or not r.stdout.strip():
        return {"probe_error": (r.stderr or r.stdout or f"код {r.returncode}").strip()[-400:]}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError as e:
        return {"probe_error": f"ответ стенда не JSON: {e}"}


def probe_storage(settings: dict) -> dict:
    host = (settings.get("storage_host") or "").strip()
    if not host:
        return {"skipped": True}
    import os
    key = os.path.expanduser((settings.get("storage_key") or "~/.ssh/kt_storage").strip())
    d = (settings.get("storage_dir") or "/srv/files").strip()
    cmd = (f"df -B1 --output=avail,size {shlex.quote(d)} | tail -1; (yt-dlp --version 2>/dev/null || echo none) | head -1; "
           f"ls {shlex.quote(d)} | grep -cE '\\.(mp4|webm|mkv)$' || true; du -sb {shlex.quote(d)} | cut -f1")
    try:
        r = _ssh(host, key, cmd, timeout=60)
    except subprocess.TimeoutExpired:
        return {"probe_error": "хранилище не ответило за 60 с", "host": host}
    if r.returncode != 0:
        return {"probe_error": (r.stderr or f"код {r.returncode}").strip()[-300:], "host": host}
    lines = r.stdout.strip().splitlines()
    try:
        avail, size = (int(x) for x in lines[0].split())
        return {"host": host, "free_gb": round(avail / 1e9, 1), "size_gb": round(size / 1e9, 1), "ytdlp": lines[1].strip(),
                "files": int(lines[2] or 0), "used_gb": round(int(lines[3]) / 1e9, 1)}
    except (ValueError, IndexError) as e:
        return {"probe_error": f"не разобран ответ: {e}", "host": host}


# ---------------------------------------------------------------- оценка

def _worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: STATUS_ORDER.get(s, 0)) if statuses else "ok"


def evaluate_stand(p: dict, settings: dict | None = None) -> list[Check]:
    if p.get("probe_error"):
        return [Check("stand.reachable", "Стенд", "Стенд доступен", "fail", "нет", p["probe_error"],
                      f"проверить VPS {stand_cfg(settings or {})['host'] or '(stand_host не задан)'} и SSH-ключ ~/.ssh/kt_storage")]
    out: list[Check] = []
    free = float(p.get("disk_free_gb") or 0)
    out.append(Check("stand.disk", "Стенд", "Диск стенда", "fail" if free < DISK_FAIL_GB else "warn" if free < DISK_WARN_GB else "ok",
                     f"{free:.1f} ГБ свободно из {p.get('disk_total_gb')}",
                     "тяжёлые задачи встают при < 4 ГБ", "уборка запускается сама; если не помогает — смотреть var/media/sources и jobs"))
    mem = int(p.get("mem_avail_mb") or 0)
    out.append(Check("stand.memory", "Стенд", "Память стенда", "warn" if mem < MEM_WARN_MB else "ok", f"{mem} МБ доступно из {p.get('mem_total_mb')}"))
    load, cores = float(p.get("load1") or 0), int(p.get("cores") or 1)
    out.append(Check("stand.load", "Стенд", "Нагрузка процессора", "warn" if load / cores > LOAD_WARN_PER_CORE else "ok", f"{load} на {cores} ядра"))
    svcs = p.get("services") or {}
    down = [s for s in STAND_SERVICES if not svcs.get(s)]
    out.append(Check("stand.services", "Стенд", "Сервисы стенда", "fail" if down else "ok",
                     "не работают: " + ", ".join(down) if down else "все работают", "", "доктор перезапускает упавшие сам"))
    http = p.get("http") or {}
    bad_http = {k: v for k, v in http.items() if v and v != "200"}
    out.append(Check("stand.http", "Стенд", "API, сайт и раздача файлов", "fail" if bad_http else "ok",
                     ", ".join(f"{k}: {v}" for k, v in http.items() if v), "",
                     "000 — не отвечает; 5xx — ошибка сервиса (смотреть логи ниже)" if bad_http else ""))
    if p.get("db_error"):
        out.append(Check("stand.db", "Стенд", "База стенда", "fail", "нет связи", p["db_error"]))
        return out
    deferred = [d for d in (p.get("deferred") or []) if (d.get("oldest_min") or 0) >= DEFER_WARN_MIN]
    disk_def = [d for d in deferred if d["defer_reason"] == "disk_low"]
    out.append(Check("tasks.deferred", "Очередь", "Задачи ждут ресурсов", "fail" if disk_def else "warn" if deferred else "ok",
                     f"{sum(d['n'] for d in deferred)} дольше {DEFER_WARN_MIN} мин" if deferred else "нет",
                     "; ".join(f"{d['defer_reason']}: {d['kind']} ×{d['n']} (до {int(d['oldest_min'])} мин)" for d in deferred),
                     "disk_low — место; load_high/mem_low — стенд перегружен; quota_exhausted — лимит нейросети", deferred))
    stale = [r for r in (p.get("running") or []) if (r.get("silent_min") or 0) >= 15]
    out.append(Check("tasks.stale", "Очередь", "Зависшие задачи", "fail" if stale else "ok",
                     f"{len(stale)} молчат > 15 мин" if stale else "нет",
                     "; ".join(f"#{r['id']} {r['kind']} {r['stage']} ролик {r['job_id']} источник {r['source_id']}" for r in stale),
                     "задачу вернёт сборщик зависших; если повторяется — смотреть лог воркера", stale))
    ft = p.get("failed_tasks_24h") or []
    nf = sum(int(r["n"]) for r in ft)
    out.append(Check("tasks.failed", "Очередь", "Упавшие задачи за сутки", "fail" if nf >= 5 else "warn" if nf else "ok", str(nf),
                     "; ".join(f"{r['kind']}/{r['error_class'] or '—'} ×{r['n']}: {r['sample'][:120]}" for r in ft[:5]), "", ft))
    stuck = p.get("stuck_jobs") or []
    out.append(Check("jobs.stuck", "Ролики", "Ролики застряли в работе", "warn" if stuck else "ok",
                     f"{len(stuck)} дольше {STUCK_JOB_HOURS} ч" if stuck else "нет",
                     "; ".join(f"#{j['id']} {j['stage']} {j['hours']} ч" for j in stuck[:8]), "", stuck))
    fj = p.get("failed_jobs_24h") or []
    out.append(Check("jobs.failed", "Ролики", "Ролики с ошибкой за сутки", "warn" if fj else "ok", str(len(fj)),
                     "; ".join(f"#{j['id']} {j['stage']}: {j['error'][:100]}" for j in fj[:5]), "", fj))
    qf = p.get("qc_failed") or []
    old_qf = [j for j in qf if (j.get("age_min") or 0) > 30]
    out.append(Check("jobs.qc_failed", "Ролики", "Не прошли проверку качества", "warn" if old_qf else "ok", str(len(qf)),
                     "; ".join(f"#{j['id']}: {', '.join(j['checks'] or [])}" for j in qf[:8]),
                     "длину чинит автоисправление; остальное — смотреть в приёмке", qf))
    ready = int(p.get("review_ready") or 0)
    out.append(Check("review.ready", "Приёмка", "Роликов ждёт приёмки", "warn" if ready < REVIEW_TARGET else "ok",
                     f"{ready} (цель ≥ {REVIEW_TARGET})", f"за 6 ч готово {p.get('qc_done_6h')}"))
    enabled = (settings or {}).get("pipeline_enabled", "1") == "1"
    last_qc = p.get("last_qc_min")
    idle = enabled and (last_qc is None or float(last_qc) > NO_OUTPUT_HOURS * 60)
    out.append(Check("production.flow", "Приёмка", "Поток новых роликов", "fail" if idle else "ok",
                     "последний готов " + (f"{int(float(last_qc))} мин назад" if last_qc is not None else "никогда"),
                     f"за 6 ч: {p.get('qc_done_6h')}", "производство стоит: смотреть «Задачи ждут ресурсов» и конвейер" if idle else ""))
    ae = p.get("autopublish_errors") or []
    out.append(Check("publish.errors", "Публикация", "Ошибки автозалива за сутки", "fail" if ae else "ok", str(len(ae)),
                     "; ".join(f"пакет {a['id']} ({a['channel_id']}): {a['error'][:120]}" for a in ae[:5]), "", ae))
    oauth = p.get("oauth") or []
    broken = [o for o in oauth if o.get("connected") and o.get("last_error")]
    offline = [o for o in oauth if not o.get("connected") and o.get("status") == "active"]
    out.append(Check("publish.oauth", "Публикация", "Каналы подключены к YouTube", "fail" if broken else "warn" if offline else "ok",
                     f"{sum(1 for o in oauth if o.get('connected'))} из {len(oauth)}",
                     "; ".join([f"{o['channel_id']}: {str(o['last_error'])[:100]}" for o in broken] + [f"{o['channel_id']}: не подключён" for o in offline]),
                     "переподключить в карточке канала: «Подключить YouTube»", oauth))
    # досмотры из YouTube Analytics (auto-shorts services/analytics.py): без права канал учится только по просмотрам
    no_stats = [o["channel_id"] for o in oauth if o.get("connected") and "yt-analytics" not in str(o.get("scopes") or "")]
    out.append(Check("publish.analytics", "Публикация", "Досмотры каналов (YouTube Analytics)", "warn" if no_stats else "ok",
                     f"без доступа: {len(no_stats)} из {sum(1 for o in oauth if o.get('connected'))}" if no_stats else "есть у всех",
                     ", ".join(no_stats),
                     "в проекте Google Cloud канала включить YouTube Analytics API и переподключить канал («Подключить YouTube»)" if no_stats else ""))
    cl = p.get("cleanup_last_min")
    out.append(Check("stand.cleanup", "Стенд", "Уборка диска", "warn" if cl is None or float(cl) > 150 else "ok",
                     f"{int(float(cl))} мин назад" if cl is not None else "не запускалась"))
    big = {k: v.get("size_mb") for k, v in (p.get("logs") or {}).items() if (v.get("size_mb") or 0) > LOG_ROTATE_MB}
    out.append(Check("stand.logs", "Стенд", "Размер логов", "warn" if big else "ok",
                     ", ".join(f"{k} {v} МБ" for k, v in big.items()) or "в норме", "", "доктор обрезает логи больше 60 МБ"))
    return out


def evaluate_storage(p: dict) -> list[Check]:
    if p.get("skipped"):
        return []
    if p.get("probe_error"):
        return [Check("storage.reachable", "Хранилище", "Хранилище доступно", "fail", "нет", f"{p.get('host')}: {p['probe_error']}")]
    free = float(p.get("free_gb") or 0)
    return [
        Check("storage.disk", "Хранилище", "Диск хранилища", "fail" if free < STORAGE_FAIL_GB else "warn" if free < STORAGE_WARN_GB else "ok",
              f"{free:.1f} ГБ свободно из {p.get('size_gb')}", f"{p.get('host')}: фильмов {p.get('files')}, {p.get('used_gb')} ГБ"),
        Check("storage.ytdlp", "Хранилище", "yt-dlp на хранилище", "fail" if p.get("ytdlp") in ("none", "", None) else "ok", str(p.get("ytdlp"))),
    ]


def _ago_min(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        t = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 60


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Запрос к своей базе; таблицы, которой ещё нет (свежая установка), — пусто, а не падение доктора."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return []
        raise


def evaluate_conveyor(conn: sqlite3.Connection, settings: dict) -> list[Check]:
    out: list[Check] = []
    enabled = settings.get("pipeline_enabled", "0") == "1"
    tick_ago = _ago_min(get_meta(conn, "pipeline_tick_at", ""))
    out.append(Check("pipeline.tick", "Конвейер", "Цикл конвейера", "fail" if enabled and (tick_ago is None or tick_ago > TICK_STALE_MIN) else "ok" if enabled else "warn",
                     ("выключен" if not enabled else f"последний проход {int(tick_ago)} мин назад" if tick_ago is not None else "проходов не было")))
    counts = {r["status"]: r["n"] for r in _rows(conn, "SELECT status, COUNT(*) n FROM pipeline_items GROUP BY status")}
    waiting = sum(counts.get(s, 0) for s in ("new", "needs_rights", "file_pending", "retry_wait"))
    in_work = sum(counts.get(s, 0) for s in ("sent", "cutting"))
    out.append(Check("pipeline.queue", "Конвейер", "Очередь фильмов", "warn" if enabled and not waiting and not in_work else "ok",
                     f"ждут {waiting}, в работе {in_work}, на приёмке {counts.get('review', 0)}",
                     "очередь пуста: календарь не подкидывает фильмы" if enabled and not waiting and not in_work else ""))
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    failed = _rows(conn, "SELECT label, last_error FROM pipeline_items WHERE status = 'failed' AND updated_at >= ? ORDER BY updated_at DESC", (since,))
    out.append(Check("pipeline.failed", "Конвейер", "Фильмы отвалились за сутки", "warn" if len(failed) >= 3 else "ok", str(len(failed)),
                     "; ".join(f"«{r['label']}»: {(r['last_error'] or '')[:90]}" for r in failed[:6])))
    dl = _rows(conn, "SELECT error, COUNT(*) n FROM downloads WHERE status = 'error' AND created_at >= ? GROUP BY error ORDER BY n DESC", (since,))
    bot = sum(r["n"] for r in dl if r["error"] and "бот" in r["error"])
    out.append(Check("downloads.errors", "Конвейер", "Ошибки скачивания за сутки", "warn" if dl else "ok", str(sum(r["n"] for r in dl)),
                     "; ".join(f"{(r['error'] or '')[:80]} ×{r['n']}" for r in dl[:4]),
                     "YouTube требует вход: нужен файл cookies YouTube; конвейер переключается на Rutube" if bot else ""))
    hour = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    llm = _rows(conn, "SELECT purpose, SUM(CASE WHEN ok = 1 THEN 0 ELSE 1 END) bad, COUNT(*) n, MAX(CASE WHEN ok = 1 THEN NULL ELSE note END) note FROM llm_usage WHERE ts >= ? GROUP BY purpose", (hour,))
    bad = sum(r["bad"] or 0 for r in llm)
    total = sum(r["n"] for r in llm)
    out.append(Check("llm.errors", "Нейросети", "Ошибки нейросети за час (кино-тренды)", "warn" if total and bad / total > 0.3 else "ok",
                     f"{bad} из {total}", "; ".join(f"{r['purpose']}: {(r['note'] or '')[:80]}" for r in llm if r["bad"])))
    run = next(iter(_rows(conn, "SELECT status, started_at, finished_at, error FROM runs ORDER BY id DESC LIMIT 1")), None)
    hours = int(settings.get("schedule_hours") or 6)
    run_ago = _ago_min(run["started_at"]) if run else None
    out.append(Check("collector.run", "Конвейер", "Сбор трендов и календарь", "warn" if run is None or (run_ago or 0) > (hours * 60 + 90) or run["status"] == "error" else "ok",
                     (f"{run['status']}, {int(run_ago)} мин назад" if run else "не запускался"), (run["error"] or "") if run else ""))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ahead = next(iter(_rows(conn, "SELECT COUNT(DISTINCT day) n FROM schedule WHERE day >= ? AND title_id IS NOT NULL", (today,))), {"n": 0})["n"]
    out.append(Check("calendar.ahead", "Конвейер", "Календарь вперёд", "warn" if ahead < 3 else "ok", f"{ahead} дн."))
    return out


def evaluate_logs(p: dict) -> tuple[list[Check], list[dict]]:
    """Свежие ошибки из логов стенда без шума перезапусков."""
    rows: list[dict] = []
    for name, v in (p.get("logs") or {}).items():
        for line in v.get("lines") or []:
            if any(n in line for n in LOG_NOISE) or line.strip() == "Traceback (most recent call last):":
                continue
            rows.append({"log": name, "line": line[:300]})
    rows = rows[-40:]
    errors = [r for r in rows if "ERROR" in r["line"] or "CRITICAL" in r["line"]]
    return [Check("stand.log_errors", "Стенд", "Ошибки в логах стенда", "warn" if errors else "ok", f"{len(errors)} ошибок, {len(rows) - len(errors)} предупр.",
                  errors[-1]["line"][:200] if errors else "")], rows


# ---------------------------------------------------------------- самолечение

def remediate(checks: list[Check], probe: dict, settings: dict, log=print) -> list[str]:
    """Безопасные исправления: упавший сервис — поднять, мало диска — аварийная уборка, большие логи — обрезать."""
    actions: list[str] = []
    by = {c.id: c for c in checks}
    cfg = stand_cfg(settings)
    svcs = probe.get("services") or {}
    down = [s for s in STAND_SERVICES if svcs and not svcs.get(s)]
    if down and not probe.get("probe_error"):
        r = _ssh(cfg["host"], cfg["key"], "bash /root/as_restart.sh " + " ".join(down) + " 2>&1 | tail -5", timeout=180)
        actions.append(f"перезапущены сервисы стенда: {', '.join(down)} → {(r.stdout or r.stderr).strip()[-160:]}")
    disk = by.get("stand.disk")
    if disk and disk.status == "fail":
        # штатная аварийная уборка auto-shorts (tmp старше часа, файлы отклонённых, отработанные исходники)
        py = ("from autoshorts.db.pool import connection; from autoshorts.services.tasks import enqueue_cleanup\n"
              "with connection() as c: print(enqueue_cleanup(c, emergency=True))")
        cmd = (f"cd {shlex.quote(cfg['root'])} && DATABASE_URL={shlex.quote(cfg['db'])} .venv/bin/python -c {shlex.quote(py)} 2>&1 | tail -1")
        r = _ssh(cfg["host"], cfg["key"], cmd, timeout=90)
        actions.append("диск стенда: поставлена аварийная уборка" + (f" ({r.stdout.strip()[:120]})" if r.stdout.strip() else ""))
    for name, v in (probe.get("logs") or {}).items():
        if (v.get("size_mb") or 0) > LOG_ROTATE_MB:
            f = f"{cfg['root']}/var/stand/logs/{name}"
            keep = LOG_KEEP_MB * 1_000_000
            cmd = f"tail -c {keep} {shlex.quote(f)} > {shlex.quote(f)}.tmp && cat {shlex.quote(f)}.tmp > {shlex.quote(f)} && rm -f {shlex.quote(f)}.tmp"
            _ssh(cfg["host"], cfg["key"], cmd, timeout=60)
            actions.append(f"лог {name}: обрезан с {v.get('size_mb')} до {LOG_KEEP_MB} МБ")
    for a in actions:
        log(f"доктор: {a}")
    return actions


# ---------------------------------------------------------------- журнал и уведомления

def init(conn: sqlite3.Connection) -> None:
    conn.executescript("""
CREATE TABLE IF NOT EXISTS health_runs(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, status TEXT NOT NULL, report TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS health_incidents(
  id INTEGER PRIMARY KEY, check_id TEXT NOT NULL, status TEXT NOT NULL, title TEXT NOT NULL, value TEXT, detail TEXT,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 1, resolved_at TEXT, notified INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS health_incidents_open ON health_incidents(check_id, resolved_at);
""")
    conn.commit()


def track_incidents(conn: sqlite3.Connection, checks: list[Check], at: str) -> tuple[list[dict], list[dict]]:
    """Открывает инциденты по warn/fail, закрывает по ok. Возвращает (новые fail, закрытые fail)."""
    opened, closed = [], []
    for c in checks:
        cur = conn.execute("SELECT * FROM health_incidents WHERE check_id = ? AND resolved_at IS NULL", (c.id,)).fetchone()
        if c.status in ("warn", "fail"):
            if cur:
                worse = STATUS_ORDER[c.status] > STATUS_ORDER.get(cur["status"], 0)
                conn.execute("UPDATE health_incidents SET status = ?, value = ?, detail = ?, last_seen = ?, seen = seen + 1, notified = CASE WHEN ? THEN 0 ELSE notified END WHERE id = ?",
                             (c.status if worse else cur["status"], c.value, c.detail, at, worse, cur["id"]))
                if worse and c.status == "fail":
                    opened.append(asdict(c))
            else:
                conn.execute("INSERT INTO health_incidents(check_id, status, title, value, detail, first_seen, last_seen) VALUES (?,?,?,?,?,?,?)",
                             (c.id, c.status, c.title, c.value, c.detail, at, at))
                if c.status == "fail":
                    opened.append(asdict(c))
        elif cur:
            conn.execute("UPDATE health_incidents SET resolved_at = ?, last_seen = ? WHERE id = ?", (at, at, cur["id"]))
            if cur["status"] == "fail":
                closed.append(dict(cur))
    conn.commit()
    return opened, closed


def notify(settings: dict, opened: list[dict], closed: list[dict], actions: list[str]) -> bool:
    from . import telegram
    token, chat = settings.get("telegram_bot_token") or "", settings.get("telegram_chat_id") or ""
    if not token or not chat or not (opened or closed or actions):
        return False
    lines = ["<b>Доктор auto-shorts</b>"]
    lines += [f"🔴 {c['title']}: {c['value']} — {c['detail'][:200]}" for c in opened]
    lines += [f"🟢 починилось: {c['title']}" for c in closed]
    lines += [f"🛠 {a}" for a in actions]
    ok, _ = telegram.send(token, chat, "\n".join(lines))
    return ok


# ---------------------------------------------------------------- прогон

def run(conn: sqlite3.Connection, settings: dict, log=print, fix: bool = True) -> dict:
    init(conn)
    at = now_iso()
    stand = probe_stand(settings)
    storage = probe_storage(settings)
    checks = evaluate_stand(stand, settings) + evaluate_storage(storage) + evaluate_conveyor(conn, settings)
    log_checks, log_rows = evaluate_logs(stand)
    checks += log_checks
    guard_actions: list[str] = []
    if fix:
        # сторож правил выкладки: план 4 в день, повторы, один фильм — один канал, диск стенда, туннель через мак
        from . import guard
        try:
            g_checks, guard_actions = guard.run(conn, settings, log)
            checks += g_checks
        except Exception as e:  # noqa: BLE001 — сторож не должен ронять доктора
            checks.append(Check("guard.error", "Публикация", "Сторож правил", "warn", "упал", str(e)[:300]))
    checks.sort(key=lambda c: GROUP_ORDER.index(c.group) if c.group in GROUP_ORDER else len(GROUP_ORDER))
    actions = (remediate(checks, stand, settings, log) if fix else []) + guard_actions
    rep = Report(at=at, status=_worst(*(c.status for c in checks)), checks=checks, actions=actions,
                 errors=[x for x in (stand.get("probe_error"), storage.get("probe_error")) if x], logs=log_rows)
    opened, closed = track_incidents(conn, checks, at)
    notify(settings, opened, closed, actions)
    conn.execute("INSERT INTO health_runs(at, status, report) VALUES (?, ?, ?)", (at, rep.status, dumps(rep.to_dict())))
    conn.execute("DELETE FROM health_runs WHERE id <= (SELECT MAX(id) FROM health_runs) - ?", (KEEP_RUNS,))
    conn.commit()
    set_meta(conn, "doctor_at", at)
    return rep.to_dict()


def due(conn: sqlite3.Connection) -> bool:
    ago = _ago_min(get_meta(conn, "doctor_at", ""))
    return ago is None or ago >= RUN_EVERY_MIN


def latest(conn: sqlite3.Connection) -> dict:
    init(conn)
    r = conn.execute("SELECT report FROM health_runs ORDER BY id DESC LIMIT 1").fetchone()
    report = loads(r["report"], {}) if r else None
    open_inc = [dict(x) for x in conn.execute("SELECT * FROM health_incidents WHERE resolved_at IS NULL ORDER BY CASE status WHEN 'fail' THEN 0 ELSE 1 END, first_seen")]
    recent = [dict(x) for x in conn.execute("SELECT * FROM health_incidents WHERE resolved_at IS NOT NULL ORDER BY resolved_at DESC LIMIT 30")]
    hist = [dict(x) for x in conn.execute("SELECT at, status FROM health_runs ORDER BY id DESC LIMIT 288")]
    return {"report": report, "open": open_inc, "resolved": recent, "history": hist[::-1]}


def format_text(rep: dict) -> str:
    mark = {"ok": "✓", "info": "·", "warn": "!", "fail": "✗"}
    lines = [f"Доктор {rep['at']}: {rep['status'].upper()}"]
    group = None
    for c in rep["checks"]:
        if c["group"] != group:
            group = c["group"]
            lines.append(f"\n[{group}]")
        lines.append(f" {mark.get(c['status'], '?')} {c['title']}: {c['value']}" + (f" — {c['detail']}" if c["detail"] and c["status"] != "ok" else ""))
    if rep.get("actions"):
        lines.append("\nСамолечение:")
        lines += [f" - {a}" for a in rep["actions"]]
    if rep.get("errors"):
        lines.append("\nПробы не удались:")
        lines += [f" - {e}" for e in rep["errors"]]
    errs = [r for r in rep.get("logs", []) if "ERROR" in r["line"]][-8:]
    if errs:
        lines.append("\nПоследние ошибки в логах стенда:")
        lines += [f" {r['log']}: {r['line'][:180]}" for r in errs]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from . import db
    from .config import DB_PATH
    args = argv if argv is not None else sys.argv[1:]
    conn = db.connect(str(DB_PATH))
    rep = run(conn, db.all_settings(conn), log=lambda m, level="info": print(m, file=sys.stderr), fix="--no-fix" not in args)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if "--json" in args else format_text(rep))
    return 0 if rep["status"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
