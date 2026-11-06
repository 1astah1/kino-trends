"""Сторож правил выкладки (01.11.2026, владелец: «сделай систему, чтобы сама всё делала без твоего ведома,
проверяла все правила и фиксила»). Идёт внутри прохода доктора (doctor.run), тяжёлая часть — раз в RUN_EVERY_MIN.

Правила и что сторож делает сам:
1. План — 4 ролика в день на каждом подключённом канале. Считает запас на сегодня и завтра (стоящее в плане +
   одобренное, ждущее пробы); голодным каналам отдаются следующие фильмы: settings.pipeline_hungry →
   pipeline._film_channel.
2. Повторы в плане снимаются до выхода (вышедшее не трогаем):
   - та же сцена того же файла (перекрытие ≥ половины клипа) — 01.11 «Такси» и «Викингов» резали по 4 раза;
   - почти тот же заголовок на канале (≥ 0,9) — «Такси» трижды с одним фактом;
   - одно произведение на двух каналах — YouTube считает это сетью перезаливов; остаётся канал произведения
     (закреплённый за заданием конвейера), с остальных снимается;
   - ролик длиннее 59,5 с — с заявкой Content ID шортс дольше минуты блокируется во всём мире.
3. Запрещённое (правообладатель блокирует, РФ-ТВ, советское) снимает outcomes.purge — здесь не дублируем.
4. Диск стенда < DISK_MIN_GB — удаляются исходники, из которых уже ничего не режется (готовые ролики лежат отдельно).
5. Туннель YouTube через мак упал — поднимается заново.

Снимать за проход не больше MAX_CANCEL роликов: если повторов больше, это поломка, а не повторы — сторож только
сигналит и ждёт человека.
"""

from __future__ import annotations

import difflib
import json
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .db import dumps, get_meta, loads, now_iso, set_meta

RUN_EVERY_MIN = 15
PER_DAY = 4
OVERLAP_MIN = 0.5
TWIN_RATIO = 0.9  # как pipeline.TWIN_RATIO: 0,96 — тот же факт другими словами, 0,83 — шаблон викторины с другой сценой
MAX_CANCEL = 12
MAX_CLIP_S = 59.5  # SHORTS_RULES: с 15.10.2024 шортс > 60 с с любой заявкой Content ID блокируется везде
DISK_MIN_GB = 12.0
HUNGRY_TTL_MIN = 90  # устаревшая оценка голода не управляет выбором канала
ROOT = Path(__file__).resolve().parent.parent

ROWS_SQL = """select coalesce(json_agg(t), '[]'::json) from (
  select up.id pid, j.id jid, j.kind, up.channel_id ch, up.autopublish_state st, j.status jst,
         to_char(up.autopublish_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') at_utc,
         to_char(p.published_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') pub_utc,
         coalesce(up.title, '') title, s.sha256 sha, j.clip_start_sec::float a, j.clip_end_sec::float b,
         j.duration_sec::float dur
  from upload_packages up join jobs j on j.id = up.job_id
  left join sources s on s.id = j.source_id
  left join publications p on p.package_id = up.id
  where up.autopublish_state in ('scheduled', 'publishing')
     or (up.autopublish_state is null and j.status = 'approved' and p.id is null)
     or p.published_at > now() - interval '30 days') t"""
CHANNELS_SQL = """select coalesce(json_agg(t), '[]'::json) from (
  select c.id, c.publishing_plan plan, exists(select 1 from channel_oauth o where o.channel_id = c.id) connected
  from channels c where c.status = 'active') t"""
SOURCES_SQL = """select coalesce(json_agg(t), '[]'::json) from (
  select s.id, s.status, coalesce(s.file_bytes, 0) bytes,
         (select count(*) from jobs j where j.source_id = s.id and j.status in ('queued', 'processing')) active
  from sources s where s.deleted_at is null and s.status in ('ready', 'failed', 'archived')) t"""


# ---------------------------------------------------------------- чистая логика (тесты)

def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (t or "").lower().replace("ё", "е")).split())


def _overlap(r: dict, k: dict) -> float:
    if r.get("a") is None or r.get("b") is None or k.get("a") is None or k.get("b") is None:
        return 0.0
    inter = min(r["b"], k["b"]) - max(r["a"], k["a"])
    return max(0.0, inter) / max(1e-6, min(r["b"] - r["a"], k["b"] - k["a"]))


def _order(r: dict) -> tuple:
    """Вышедшее — первым (его не трогаем), потом заливка, потом план по времени, потом одобренное без времени."""
    if r.get("pub_utc"):
        return (0, r["pub_utc"], r["pid"])
    if r.get("st") == "publishing":
        return (1, r.get("at_utc") or "", r["pid"])
    if r.get("st") == "scheduled":
        return (2, r.get("at_utc") or "", r["pid"])
    return (3, "", r["pid"])


def _cancellable(r: dict) -> bool:
    return not r.get("pub_utc") and r.get("st") in ("scheduled", None) and r.get("jst") == "approved"


def plan_dedupe(rows: list[dict], film_of: dict[int, str], home: dict[str, str]) -> list[tuple[dict, str]]:
    """Что снять: [(строка, причина)]. film_of: job id → произведение; home: произведение → его канал."""
    out: list[tuple[dict, str]] = []
    kept: list[dict] = []
    for r in sorted(rows, key=_order):
        if r.get("kind") != "clip":
            kept.append(r)
            continue
        why = None
        if (r.get("dur") or 0) > MAX_CLIP_S:
            why = f"длина {r['dur']:.0f} с — шортс дольше 60 с с заявкой Content ID блокируется во всём мире"
        film = film_of.get(int(r["jid"]))
        if not why and film and home.get(film) and r["ch"] != home[film]:
            why = f"произведение идёт на канале {home[film]}, а не на {r['ch']}"
        if not why and r.get("sha"):
            same = next((k for k in kept if k.get("sha") == r["sha"] and _overlap(r, k) >= OVERLAP_MIN), None)
            if same:
                why = f"та же сцена уже {'вышла' if same.get('pub_utc') else 'стоит'} на {same['ch']} («{same['title'][:40]}»)"
        if not why and r.get("title"):
            me = _norm(r["title"])
            twin = next((k for k in kept if k["ch"] == r["ch"] and k.get("title")
                         and difflib.SequenceMatcher(None, me, _norm(k["title"])).ratio() >= TWIN_RATIO), None)
            if twin:
                why = f"почти тот же заголовок на канале: «{twin['title'][:40]}»"
        if why and _cancellable(r):
            out.append((r, why))
        else:
            kept.append(r)
    return out


def film_homes(rows: list[dict], film_of: dict[int, str], pinned: dict[str, str]) -> dict[str, str]:
    """Канал произведения: закреплённый за заданием конвейера, иначе где у него больше всего роликов (вышедших и в плане)."""
    count: dict[str, dict[str, int]] = {}
    for r in rows:
        film = film_of.get(int(r["jid"]))
        if film:
            count.setdefault(film, {}).setdefault(r["ch"], 0)
            count[film][r["ch"]] += 1
    out = {}
    for film, by in count.items():
        if len(by) < 2 and film not in pinned:
            continue
        out[film] = pinned.get(film) or max(sorted(by), key=lambda ch: by[ch])
    return out


def fill(rows: list[dict], channels: list[dict], now: datetime) -> dict[str, dict]:
    """Запас по каналам: сколько стоит сегодня (с вышедшими) и завтра, сколько одобрено без времени, дефицит."""
    out: dict[str, dict] = {}
    for ch in channels:
        if not ch.get("connected"):
            continue
        plan = ch.get("plan") or {}
        per_day = int(plan.get("videos_per_day") or PER_DAY)
        tz = ZoneInfo(plan.get("timezone") or "Europe/Moscow")
        local = now.astimezone(tz)
        today, tomorrow = local.date(), local.date() + timedelta(days=1)
        slots = [s for s in plan.get("slots") or [] if re.fullmatch(r"\d\d:\d\d", s)]
        left_slots = sum(1 for s in slots if s > local.strftime("%H:%M")) if slots else per_day
        n_today = n_tomorrow = idle = pub_today = 0
        for r in rows:
            if r["ch"] != ch["id"]:
                continue
            when = r.get("pub_utc") or (r.get("at_utc") if r.get("st") in ("scheduled", "publishing") else None)
            if when:
                d = datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(tz).date()
                n_today += d == today
                n_tomorrow += d == tomorrow
                pub_today += d == today and bool(r.get("pub_utc"))
            elif r.get("st") is None:
                idle += 1
        # больше, чем вышло + осталось слотов, сегодня уже не выйдет (стоящее в плане эти слоты и занимает)
        need_today = min(per_day, pub_today + left_slots)
        short = max(0, need_today - n_today) + max(0, per_day - n_tomorrow)
        out[ch["id"]] = {"today": n_today, "need_today": need_today, "tomorrow": n_tomorrow, "per_day": per_day,
                         "idle": idle, "deficit": max(0, short - idle)}
    return out



def orphans(rows: list[dict], items: list[dict]) -> dict[int, int]:
    """Готовые ролики без времени у произведения, которое конвейер уже считает «опубликованным» (01.11: проба вышла,
    остальные ролики ждали час — задание закрылось и бросило их). id задания → сколько роликов брошено.
    Снятое владельцем или итогами (пометка «снят…» в ошибке) не возвращаем."""
    idle = {int(r["pid"]) for r in rows if r.get("st") is None and not r.get("pub_utc") and r.get("jst") == "approved"}
    out: dict[int, int] = {}
    for it in items:
        if it.get("status") != "published" or "снят" in (it.get("last_error") or "").lower():
            continue
        n = sum(1 for pid in it.get("as_packages") or [] if int(pid) in idle)
        if n:
            out[int(it["id"])] = n
    return out


# ---------------------------------------------------------------- стенд

def _psql(settings: dict, sql: str) -> Any:
    from .doctor import _ssh, stand_cfg
    cfg = stand_cfg(settings)
    r = _ssh(cfg["host"], cfg["key"], f"psql {shlex.quote(cfg['db'])} -At -c {shlex.quote(sql)}", timeout=90)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "psql").strip()[-300:])
    return json.loads(r.stdout.strip() or "[]")


def _stand_free_gb(settings: dict) -> float | None:
    from .doctor import _ssh, stand_cfg
    cfg = stand_cfg(settings)
    r = _ssh(cfg["host"], cfg["key"], "df -BG --output=avail / | tail -1", timeout=60)
    m = re.search(r"(\d+)", r.stdout or "")
    return float(m.group(1)) if m else None


def _film_map(conn) -> tuple[dict[int, str], dict[str, str], dict[int, dict]]:
    """job id → произведение, произведение → закреплённый канал (последнее живое задание), source id → задание."""
    film_of: dict[int, str] = {}
    pinned: dict[str, str] = {}
    by_source: dict[int, dict] = {}
    for it in conn.execute("SELECT id, ref_id, status, as_jobs, as_source_id, metrics FROM pipeline_items WHERE kind = 'film' ORDER BY id"):
        for jid in loads(it["as_jobs"], []) or []:
            film_of[int(jid)] = it["ref_id"]
        ch = (loads(it["metrics"], {}) or {}).get("channel")
        if ch and it["status"] not in ("cancelled", "failed", "rejected", "qc_failed"):
            pinned[it["ref_id"]] = ch
        if it["as_source_id"]:
            by_source[int(it["as_source_id"])] = dict(it)
    return film_of, pinned, by_source


def _cancel(asc, r: dict) -> None:
    if r.get("st") == "scheduled":
        asc._req("DELETE", f"/api/v1/packages/{int(r['pid'])}/autopublish")
    asc.post(f"/api/v1/jobs/{int(r['jid'])}/cancel", {})


def _proxy(settings: dict) -> tuple[str, str | None]:
    """Туннель через мак для YouTube: жив — ok; упал — поднять."""
    if not (settings.get("storage_proxy") or "").strip():
        return "info", None
    script = ROOT / "scripts" / "mac-proxy.sh"
    st = subprocess.run([str(script), "status"], capture_output=True, text=True, timeout=60).stdout
    if "жив" in st:
        return "ok", None
    r = subprocess.run([str(script), "start"], capture_output=True, text=True, timeout=60)
    return "warn", f"туннель YouTube через мак упал — поднят заново ({(r.stdout or r.stderr).strip()[-80:]})"


# ---------------------------------------------------------------- проход

def due(conn) -> bool:
    last = get_meta(conn, "guard_at", "")
    return not last or last <= (datetime.now(timezone.utc) - timedelta(minutes=RUN_EVERY_MIN)).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(conn, settings: dict, log=print, asc=None, force: bool = False) -> tuple[list, list[str]]:
    """(проверки для отчёта доктора, сделанные действия). Между тяжёлыми проходами отдаёт прошлые проверки."""
    from .doctor import Check
    if not force and not due(conn):
        prev = loads(get_meta(conn, "guard_checks", "[]"), []) or []
        return [Check(**c) for c in prev], []
    set_meta(conn, "guard_at", now_iso())
    checks: list[Check] = []
    actions: list[str] = []
    if (settings.get("guard_enabled") or "1") == "0":
        return [Check("guard.off", "Публикация", "Сторож правил", "info", "выключен", "guard_enabled=0")], []
    try:
        if asc is None:
            from .pipeline import client_from_settings
            asc = client_from_settings(settings)
        rows = _psql(settings, ROWS_SQL)
        channels = _psql(settings, CHANNELS_SQL)
    except Exception as e:  # noqa: BLE001 — стенд недоступен: доктор это и так покажет
        checks.append(Check("guard.data", "Публикация", "Сторож правил", "warn", "нет данных стенда", str(e)[:300]))
        set_meta(conn, "guard_checks", dumps([c.__dict__ for c in checks]))
        return checks, actions

    # 2. повторы
    film_of, pinned, by_source = _film_map(conn)
    plan = plan_dedupe(rows, film_of, film_homes(rows, film_of, pinned))
    done = 0
    if len(plan) > MAX_CANCEL:
        checks.append(Check("guard.dupes", "Публикация", "Повторы в плане", "fail", f"{len(plan)} к снятию",
                            "больше предела за проход — похоже на поломку, сторож ничего не снял",
                            "посмотреть план каналов, причины: " + "; ".join(w for _, w in plan[:3])))
    else:
        for r, why in plan:
            try:
                _cancel(asc, r)
                done += 1
                actions.append(f"снят ролик {r['ch']} «{r['title'][:50]}»: {why}")
            except Exception as e:  # noqa: BLE001
                log(f"сторож: ролик {r['pid']} не снят: {e}", "warn")
        checks.append(Check("guard.dupes", "Публикация", "Повторы в плане", "ok" if not plan else "warn",
                            f"снято {done}" if plan else "нет", "; ".join(w for _, w in plan[:3])))
        rows = [r for r in rows if not any(r is x for x, _ in plan)]

    # 3. брошенные ролики: задание закрыто, а готовые ролики так и не встали в план — вернуть в работу
    items = [dict(it, as_packages=loads(it["as_packages"], []) or []) for it in
             conn.execute("SELECT id, label, status, as_packages, last_error FROM pipeline_items WHERE status = 'published'")]
    lost = orphans(rows, items)
    for iid, n in lost.items():
        conn.execute("UPDATE pipeline_items SET status = 'publishing', updated_at = ? WHERE id = ?", (now_iso(), iid))
        label = next((it["label"] for it in items if it["id"] == iid), str(iid))
        actions.append(f"«{label}»: {n} готовых роликов без времени — задание снова в работе, конвейер поставит их в план")
    if lost:
        conn.commit()
    checks.append(Check("guard.orphans", "Публикация", "Брошенные ролики", "warn" if lost else "ok",
                        f"возвращено {sum(lost.values())}" if lost else "нет"))

    # 1. план 4 в день
    f = fill(rows, channels, datetime.now(timezone.utc))
    hungry = {ch: v["deficit"] for ch, v in f.items() if v["deficit"] > 0}
    set_meta(conn, "pipeline_hungry", dumps({"at": now_iso(), "deficit": hungry}))
    bad = [ch for ch, v in f.items() if v["tomorrow"] + v["idle"] < v["per_day"]]
    empty = [ch for ch, v in f.items() if v["tomorrow"] + v["idle"] == 0]
    value = " · ".join(f"{ch} {v['today']}/{v['need_today']}, завтра {v['tomorrow']}/{v['per_day']}" + (f" +{v['idle']} ждут" if v["idle"] else "")
                       for ch, v in sorted(f.items()))
    checks.append(Check("guard.fill", "Публикация", f"План {PER_DAY} ролика в день", "fail" if empty else "warn" if bad else "ok", value,
                        ("не хватает на завтра: " + ", ".join(bad) + " — следующие фильмы конвейер отдаёт им") if bad else "",
                        data=f))

    # 4. диск стенда
    try:
        free = _stand_free_gb(settings)
        if free is not None and free < DISK_MIN_GB:
            gone = 0
            for s in sorted(_psql(settings, SOURCES_SQL), key=lambda x: -int(x["bytes"])):
                it = by_source.get(int(s["id"]))
                busy = it and it["status"] in ("new", "file_pending", "sent", "cutting", "review", "retry_wait")
                if s["active"] or busy:
                    continue
                try:
                    asc._req("DELETE", f"/api/v1/sources/{int(s['id'])}", params={"purge_files": "true"})
                    gone += 1
                except Exception as e:  # noqa: BLE001
                    log(f"сторож: исходник {s['id']} не удалён: {e}", "warn")
            if gone:
                actions.append(f"диск стенда {free:.0f} ГБ: удалено отработанных исходников {gone}")
    except Exception as e:  # noqa: BLE001
        log(f"сторож: диск стенда: {e}", "warn")

    # 5. туннель через мак
    try:
        st, act = _proxy(settings)
        if act:
            actions.append(act)
        if st != "info":
            checks.append(Check("guard.proxy", "Хранилище", "Туннель YouTube через мак", st, "жив" if st == "ok" else "поднят заново"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("guard.proxy", "Хранилище", "Туннель YouTube через мак", "warn", "не проверен", str(e)[:200]))

    for a in actions:
        log(f"сторож: {a}")
    set_meta(conn, "guard_checks", dumps([c.__dict__ for c in checks]))
    return checks, actions


def hungry(conn) -> dict[str, int]:
    """Дефицит роликов по каналам из последнего прохода сторожа (свежее HUNGRY_TTL_MIN), для выбора канала фильму."""
    h = loads(get_meta(conn, "pipeline_hungry", ""), {}) or {}
    at = h.get("at") or ""
    if not at or at < (datetime.now(timezone.utc) - timedelta(minutes=HUNGRY_TTL_MIN)).strftime("%Y-%m-%dT%H:%M:%SZ"):
        return {}
    return {k: int(v) for k, v in (h.get("deficit") or {}).items()}
