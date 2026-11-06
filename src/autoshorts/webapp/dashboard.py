"""
Веб-дашборд конвейера — "инструмент на сайте, чтобы следить за всем и видеть, как что работает".

Отдельное FastAPI-приложение от webapp/redirect.py (тот публичный, этот — операционные данные:
расходы, причины отказов, статусы задач — закрыт HTTP Basic Auth). Разворачивается отдельным
процессом на своём порту/поддомене (см. README: nginx-конфиг под admin.<домен> или /dashboard).

Server-rendered HTML без фронтенд-фреймворка — это внутренний инструмент на 5-10 роликов/день,
а не публичная страница; JS не нужен, автообновление через <meta refresh>.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from autoshorts import dashboard_queries as q
from autoshorts.models import JobStatus

app = FastAPI(title="Auto Shorts — Dashboard")
security = HTTPBasic()

_DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
_DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not _DASHBOARD_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="DASHBOARD_PASSWORD не задан в .env — дашборд отключён, пока пароль не установлен",
        )
    user_ok = secrets.compare_digest(credentials.username, _DASHBOARD_USER)
    pass_ok = secrets.compare_digest(credentials.password, _DASHBOARD_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Неверные логин/пароль",
                             headers={"WWW-Authenticate": "Basic"})
    return credentials.username


STATUS_LABELS = {
    "queued": "В очереди", "script_ready": "Сценарий готов", "voiced": "Озвучено",
    "rendered": "Отрендерено", "qc_passed": "QC пройден", "qc_failed": "QC провален",
    "pending_review": "Ждёт приёмки", "approved": "Одобрено", "rework": "На переделку",
    "rejected": "Отклонено", "ready_to_publish": "Готово к заливке", "published": "Опубликовано",
}
STATUS_COLORS = {
    "queued": "#8a8aa0", "script_ready": "#8a8aa0", "voiced": "#8a8aa0", "rendered": "#4b3fa8",
    "qc_passed": "#2e6f52", "qc_failed": "#a32b21", "pending_review": "#8a6410",
    "approved": "#2e6f52", "rework": "#8a6410", "rejected": "#a32b21",
    "ready_to_publish": "#2e6f52", "published": "#2e6f52",
}

_PAGE_HEAD = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Auto Shorts — Дашборд</title>
<style>
  :root {{ --bg:#0f0f17; --surface:#181824; --surface2:#20202e; --ink:#e8e6f2; --ink2:#a29fbd;
           --line:#2c2c3d; --accent:#7c6fe8; }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font-family:system-ui,-apple-system,sans-serif;
          font-size:14px; line-height:1.5; padding-bottom:60px; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:24px 20px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:var(--ink2); font-size:12.5px; margin-bottom:24px; }}
  nav {{ display:flex; gap:4px; margin-bottom:24px; border-bottom:1px solid var(--line); padding-bottom:0; }}
  nav a {{ color:var(--ink2); text-decoration:none; padding:8px 14px; font-size:13px; border-bottom:2px solid transparent; }}
  nav a.active, nav a:hover {{ color:var(--ink); border-bottom-color:var(--accent); }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:24px; }}
  .card {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:16px; }}
  .card .v {{ font-size:24px; font-weight:700; font-variant-numeric:tabular-nums; }}
  .card .k {{ color:var(--ink2); font-size:12px; margin-top:2px; }}
  .card.warn {{ border-color:#8a6410; background:#241d0f; }}
  .card.crit {{ border-color:#a32b21; background:#26130f; }}
  table {{ width:100%; border-collapse:collapse; background:var(--surface); border-radius:8px; overflow:hidden; }}
  th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--ink2);
        padding:10px 14px; border-bottom:1px solid var(--line); background:var(--surface2); }}
  td {{ padding:10px 14px; border-bottom:1px solid var(--line); font-size:13px; }}
  tr:last-child td {{ border-bottom:none; }}
  .pill {{ display:inline-block; padding:2px 9px; border-radius:10px; font-size:11px; font-weight:600; color:#fff; }}
  .mono {{ font-family:ui-monospace,monospace; font-size:12.5px; color:var(--ink2); }}
  a.link {{ color:var(--accent); text-decoration:none; }}
  a.link:hover {{ text-decoration:underline; }}
  .section-title {{ font-size:14px; font-weight:600; margin:28px 0 10px; }}
  .empty {{ color:var(--ink2); padding:20px; text-align:center; }}
  .tbl-wrap {{ overflow-x:auto; }}
</style></head><body><div class="wrap">
<h1>🎬 Auto Shorts</h1>
<div class="sub">Обновляется каждые 60 сек · {now}</div>
<nav>
  <a href="/" class="{nav_overview}">Обзор</a>
  <a href="/jobs" class="{nav_jobs}">Задачи</a>
  <a href="/publications" class="{nav_pubs}">Публикации</a>
  <a href="/channels" class="{nav_channels}">Каналы</a>
</nav>
"""
_PAGE_FOOT = "</div></body></html>"


def _pill(status_val: str) -> str:
    label = STATUS_LABELS.get(status_val, status_val)
    color = STATUS_COLORS.get(status_val, "#8a8aa0")
    return f'<span class="pill" style="background:{color}">{label}</span>'


def _nav(active: str) -> dict:
    keys = ["overview", "jobs", "pubs", "channels"]
    return {f"nav_{k}": ("active" if k == active else "") for k in keys}


@app.get("/", response_class=HTMLResponse)
async def overview(_: str = Depends(require_auth)) -> str:
    queue = q.queue_by_status()
    budget = q.budget_status()
    qc = q.qc_pass_rate()
    rejects = q.reject_reason_breakdown()
    jobs = q.recent_jobs(limit=15)

    queue_total = sum(r["count"] for r in queue)
    budget_pct = round(100 * float(budget.get("spent_today_usd", 0)) / float(budget.get("daily_limit_usd", 1) or 1), 1)
    budget_card_class = "crit" if budget.get("paused") else ("warn" if budget_pct >= 80 else "")

    queue_rows = "".join(
        f'<tr><td>{_pill(r["status"])}</td><td class="mono">{r["count"]}</td></tr>' for r in queue
    ) or '<tr><td colspan="2" class="empty">Очередь пуста</td></tr>'

    reject_rows = "".join(
        f'<tr><td>{r["reject_reason"]}</td><td class="mono">{r["count"]}</td></tr>' for r in rejects
    ) or '<tr><td colspan="2" class="empty">Отказов нет</td></tr>'

    jobs_rows = "".join(
        f'<tr><td><a class="link" href="/jobs/{j["id"]}">{j["video_uid"]}</a></td>'
        f'<td class="mono">{j["niche"]}</td><td class="mono">{j["channel_id"]}</td>'
        f'<td>{_pill(j["status"])}</td>'
        f'<td class="mono">${float(j["cost_usd"]):.3f}</td>'
        f'<td class="mono">{j["updated_at"].strftime("%d.%m %H:%M")}</td></tr>'
        for j in jobs
    ) or '<tr><td colspan="6" class="empty">Задач ещё не было</td></tr>'

    body = f"""
    <div class="grid">
      <div class="card"><div class="v">{queue_total}</div><div class="k">задач в системе</div></div>
      <div class="card {budget_card_class}">
        <div class="v">${float(budget.get('spent_today_usd', 0)):.2f} / ${float(budget.get('daily_limit_usd', 0)):.0f}</div>
        <div class="k">расход сегодня {'· ПРИОСТАНОВЛЕНО' if budget.get('paused') else f'({budget_pct}%)'}</div>
      </div>
      <div class="card"><div class="v">{qc['pass_rate_pct'] if qc['pass_rate_pct'] is not None else '—'}%</div>
        <div class="k">QC pass rate (посл. {qc['total']})</div></div>
      <div class="card"><div class="v">{qc['failed']}</div><div class="k">провалов QC</div></div>
    </div>

    <div class="section-title">Очередь по стадиям</div>
    <div class="tbl-wrap"><table><thead><tr><th>Стадия</th><th>Задач</th></tr></thead>
    <tbody>{queue_rows}</tbody></table></div>

    <div class="section-title">Причины отказов на приёмке (посл. 100)</div>
    <div class="tbl-wrap"><table><thead><tr><th>Причина</th><th>Раз</th></tr></thead>
    <tbody>{reject_rows}</tbody></table></div>

    <div class="section-title">Последние задачи</div>
    <div class="tbl-wrap"><table>
    <thead><tr><th>ID</th><th>Ниша</th><th>Канал</th><th>Статус</th><th>Стоимость</th><th>Обновлено</th></tr></thead>
    <tbody>{jobs_rows}</tbody></table></div>
    """
    return _PAGE_HEAD.format(now=datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"), **_nav("overview")) + body + _PAGE_FOOT


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_list(_: str = Depends(require_auth)) -> str:
    jobs = q.recent_jobs(limit=100)
    rows = "".join(
        f'<tr><td><a class="link" href="/jobs/{j["id"]}">{j["video_uid"]}</a></td>'
        f'<td class="mono">{j["niche"]}</td><td class="mono">{j["channel_id"]}</td>'
        f'<td>{_pill(j["status"])}</td>'
        f'<td class="mono">{j["reject_reason"] or "—"}</td>'
        f'<td class="mono">${float(j["cost_usd"]):.3f}</td>'
        f'<td class="mono">{j["created_at"].strftime("%d.%m %H:%M")}</td></tr>'
        for j in jobs
    ) or '<tr><td colspan="7" class="empty">Задач нет</td></tr>'

    body = f"""
    <div class="section-title">Все задачи (последние 100)</div>
    <div class="tbl-wrap"><table>
    <thead><tr><th>ID</th><th>Ниша</th><th>Канал</th><th>Статус</th><th>Причина отказа</th><th>Стоимость</th><th>Создана</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
    """
    return _PAGE_HEAD.format(now=datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"), **_nav("jobs")) + body + _PAGE_FOOT


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(job_id: int, _: str = Depends(require_auth)) -> str:
    job = q.job_detail(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    events_rows = "".join(
        f'<tr><td class="mono">{e["step_name"]}</td><td class="mono">{e["provider"]}</td>'
        f'<td class="mono">${float(e["cost_usd"]):.4f}</td>'
        f'<td class="mono">{e["latency_ms"] or "—"} мс</td>'
        f'<td class="mono">{e["created_at"].strftime("%d.%m %H:%M:%S")}</td></tr>'
        for e in job["generation_events"]
    ) or '<tr><td colspan="5" class="empty">Событий нет</td></tr>'

    payload = job["stage_payload"] or {}
    qc_checks = payload.get("qc_checks", {})
    qc_html = "".join(
        f'<span class="pill" style="background:{"#2e6f52" if ok else "#a32b21"}">{k}</span> '
        for k, ok in qc_checks.items()
    ) or "—"

    total_cost = sum(float(e["cost_usd"]) for e in job["generation_events"])

    body = f"""
    <div class="section-title">Задача {job['video_uid']} {_pill(job['status'])}</div>
    <div class="grid">
      <div class="card"><div class="v">${total_cost:.3f}</div><div class="k">суммарная стоимость</div></div>
      <div class="card"><div class="v">{payload.get('duration_sec', '—')}</div><div class="k">длительность, сек</div></div>
      <div class="card"><div class="v">{len(job['generation_events'])}</div><div class="k">событий генерации</div></div>
    </div>

    <div class="section-title">Preflight-QC</div>
    <p>{qc_html}</p>

    <div class="section-title">Тема / сценарий</div>
    <p class="mono" style="white-space:pre-wrap;">{payload.get('voiceover_text', '—')}</p>

    <div class="section-title">Хэштеги</div>
    <p class="mono">{" ".join(payload.get('hashtags', []) or ['—'])}</p>

    <div class="section-title">Файлы</div>
    <p class="mono">final: {payload.get('final_path', '—')}<br>preview: {payload.get('preview_path', '—')}</p>

    <div class="section-title">События генерации</div>
    <div class="tbl-wrap"><table>
    <thead><tr><th>Шаг</th><th>Провайдер</th><th>Стоимость</th><th>Latency</th><th>Когда</th></tr></thead>
    <tbody>{events_rows}</tbody></table></div>
    """
    return _PAGE_HEAD.format(now=datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"), **_nav("jobs")) + body + _PAGE_FOOT


@app.get("/publications", response_class=HTMLResponse)
async def publications_page(_: str = Depends(require_auth)) -> str:
    pubs = q.recent_publications()
    rows = "".join(
        f'<tr><td class="mono">{p["video_uid"]}</td><td class="mono">{p["niche"]}</td>'
        f'<td class="mono">{p["platform"]}</td><td class="mono">{p["platform_video_id"] or "—"}</td>'
        f'<td class="mono">{p["attribution_tag"] or "—"}</td>'
        f'<td class="mono">{p["published_at"].strftime("%d.%m %H:%M") if p["published_at"] else "—"}</td></tr>'
        for p in pubs
    ) or '<tr><td colspan="6" class="empty">Публикаций ещё не было — заливка ручная, см. /videoid в Telegram-боте</td></tr>'

    body = f"""
    <div class="section-title">Опубликованные ролики</div>
    <div class="tbl-wrap"><table>
    <thead><tr><th>Job</th><th>Ниша</th><th>Площадка</th><th>ID на площадке</th><th>Метка атрибуции</th><th>Опубликовано</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
    """
    return _PAGE_HEAD.format(now=datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"), **_nav("pubs")) + body + _PAGE_FOOT


@app.get("/channels", response_class=HTMLResponse)
async def channels_page(_: str = Depends(require_auth)) -> str:
    channels = q.channels_overview()
    rows = "".join(
        f'<tr><td class="mono">{c["id"]}</td><td class="mono">{c["niche"]}</td>'
        f'<td class="mono">{c["platform"]}</td><td class="mono">{c["cluster"]}</td>'
        f'<td class="mono">{c["total_jobs"]}</td><td class="mono">{c["published_count"]}</td></tr>'
        for c in channels
    ) or '<tr><td colspan="6" class="empty">Каналы ещё не заведены</td></tr>'

    body = f"""
    <div class="section-title">Каналы (кусты изоляции, ТЗ §11)</div>
    <div class="tbl-wrap"><table>
    <thead><tr><th>ID</th><th>Ниша</th><th>Площадка</th><th>Куст</th><th>Задач</th><th>Опубликовано</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
    """
    return _PAGE_HEAD.format(now=datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"), **_nav("channels")) + body + _PAGE_FOOT


@app.get("/api/stats")
async def api_stats(_: str = Depends(require_auth)) -> dict:
    """JSON-эндпоинт для программного доступа — если понадобится встроить виджет на сам umka.chat."""
    return {
        "queue": q.queue_by_status(),
        "budget": q.budget_status(),
        "qc": q.qc_pass_rate(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
