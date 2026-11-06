"""Полный цикл серии: сценарист → начальник (сценарий) → голос → монтажёр → РЕНДЕР И СБОРКА НА МАКЕ → начальник (видео).

Владелец 06.11.2026: «для таких видосов юзай мак», «всё через мак». Мак: адрес и ключ — var/mac-proxy.env (MAC_HOST, MAC_KEY;
не в git), рабочая папка ~/asmr-render/toon (на маке — только там). Код рендера синхронизируется перед каждой серией.
Запуск: python -m toon.produce <сериал> [формат] [идея]."""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import sys

from kinotrends import db

from . import agents, montage, review, series, store, voice

ROOT = pathlib.Path(__file__).resolve().parent.parent
JOBS = ROOT / "var" / "toon" / "jobs"
MAC_DIR = "asmr-render/toon"


def _mac() -> tuple[str, str]:
    env = {}
    f = ROOT / "var" / "mac-proxy.env"
    if f.exists():
        for ln in f.read_text().splitlines():
            if "=" in ln and not ln.strip().startswith("#"):
                k, v = ln.split("=", 1)
                env[k.strip()] = v.strip()
    host = os.environ.get("MAC_HOST") or env.get("MAC_HOST")
    if not host:
        raise RuntimeError("нет MAC_HOST в var/mac-proxy.env")
    return host, os.path.expanduser(os.environ.get("MAC_KEY") or env.get("MAC_KEY") or "~/.ssh/bpn_ios")


def _ssh(cmd: str, timeout: int = 900) -> str:
    host, key = _mac()
    r = subprocess.run(["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", host, cmd], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"мак: {(r.stderr or r.stdout)[-400:]}")
    return r.stdout


def _rsync(src: str, dst: str) -> None:
    host, key = _mac()
    subprocess.run(["rsync", "-a", "-e", f"ssh -i {key} -o BatchMode=yes", src.replace("MAC:", f"{host}:"), dst.replace("MAC:", f"{host}:")], check=True, timeout=600)


def mix(job: pathlib.Path, starts: list[float], wavs: list[pathlib.Path], duration: float) -> pathlib.Path:
    """Реплики по своим местам на дорожке, громкость под Shorts."""
    out = job / "audio.wav"
    args = ["ffmpeg", "-v", "error", "-y"]
    for w in wavs:
        args += ["-i", str(w)]
    fl = "".join(f"[{i}]adelay={int(s * 1000)}|{int(s * 1000)}[a{i}];" for i, s in enumerate(starts))
    fl += "".join(f"[a{i}]" for i in range(len(wavs))) + f"amix=inputs={len(wavs)}:normalize=0,apad,atrim=0:{duration:.3f},loudnorm=I=-14:TP=-1.5:LRA=11[out]"
    subprocess.run(args + ["-filter_complex", fl, "-map", "[out]", "-ar", "48000", "-ac", "2", str(out)], check=True)
    return out


def render_on_mac(ep: int, job: pathlib.Path, w: int = 1080, h: int = 1920, log=print) -> pathlib.Path:
    _ssh(f"mkdir -p {MAC_DIR}/jobs/{ep} {MAC_DIR}/render")
    _rsync(str(ROOT / "toon" / "render") + "/", f"MAC:{MAC_DIR}/render/")
    _rsync(str(job / "timeline.json"), f"MAC:{MAC_DIR}/jobs/{ep}/")
    _rsync(str(job / "audio.wav"), f"MAC:{MAC_DIR}/jobs/{ep}/")
    j = f"{MAC_DIR}/jobs/{ep}"
    out = _ssh(f"export PATH=/opt/homebrew/bin:$PATH; cd {MAC_DIR}/render && nice -n 10 node render.mjs ../jobs/{ep}/timeline.json ../jobs/{ep}/video.mp4 --w {w} --h {h} 2>&1 | tail -1 && "
               f"cd ~/{j} && nice -n 10 ffmpeg -hide_banner -loglevel error -y -i video.mp4 -i audio.wav -map 0:v -map 1:a -c:v copy -c:a aac -b:a 160k -shortest -movflags +faststart final.mp4 && echo ok")
    log(f"мак: {out.strip()[-200:]}")
    _rsync(f"MAC:{j}/final.mp4", str(job) + "/")
    return job / "final.mp4"


def _finish(conn, settings: dict, key: str, ep: int, script: dict, log=print) -> int:
    """Голос → монтажёр → сборка звука → рендер на маке → начальник смотрит видео. Сценарий уже принят."""
    s = series.S[key]
    job = JOBS / f"{ep:04d}"
    (job / "lines").mkdir(parents=True, exist_ok=True)
    durs, envs, wavs = [], [], []
    for i, ln in enumerate(script.get("lines") or []):
        c = s["cast"].get(ln.get("who")) or next(iter(s["cast"].values()))
        w = job / "lines" / f"{i:02d}.wav"
        durs.append(voice.synth(settings, ln.get("text") or "", c["voice"], c["how"], ln.get("emotion") or "спокойно", w))
        envs.append(voice.envelope(w))
        wavs.append(w)
    store.event(conn, ep, "голос", "готово", f"{len(wavs)} реплик, {sum(durs):.1f} с")
    tl = montage.plan(conn, settings, key, script, durs, envs, log=log)
    (job / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
    starts, t = [], montage.LEAD
    for i, d in enumerate(durs):
        starts.append(t)
        t += d + (montage.GAP if i < len(durs) - 1 else montage.TAIL)
    store.event(conn, ep, "монтажёр", "план", f"{len(tl['segments'])} планов, {tl['duration']} с, локация «{tl['set']}»")
    mix(job, starts, wavs, tl["duration"])
    final = render_on_mac(ep, job, log=log)
    store.save(conn, ep, status="rendered", video=str(final))
    store.event(conn, ep, "мак", "рендер", str(final))
    rv = review.watch(conn, settings, key, final.read_bytes(), script.get("logline") or "")
    store.save(conn, ep, status="approved" if rv.get("verdict") == "publish" else "needs_fix")
    store.event(conn, ep, "начальник", f"видео: {rv.get('verdict')} ({rv.get('score')})", "; ".join(rv.get("problems") or [])[:500], rv)
    log(f"серия {ep} «{script.get('title')}»: начальник {rv.get('verdict')} {rv.get('score')}/10 — {'; '.join(rv.get('problems') or [])[:200]}")
    return ep


def redo(ep: int, log=print) -> int:
    """Заново озвучить и собрать серию по принятому сценарию (смена голоса, правки монтажа)."""
    conn = db.connect()
    e = store.episode(conn, ep)
    return _finish(conn, db.all_settings(conn), e.get("series") or "katscena", ep, e["script"], log)


def produce(key: str, fmt: str | None = None, seed: str | None = None, log=print) -> int:
    conn = db.connect()
    settings = db.all_settings(conn)
    store.init(conn)
    s = series.S[key]
    fmt = fmt or next(iter(s["formats"]))
    seed = seed or "выбери сам самую сильную тему для пилота сериала — так, чтобы досмотрели и пересмотрели"
    ep = agents.produce_script(conn, settings, key, fmt, seed, log=log)
    e = store.episode(conn, ep)
    if e["status"] != "script_ok":
        log(f"серия {ep}: сценарий не принят — {((e.get('review') or {}).get('summary') or '')[:200]}")
        return ep
    return _finish(conn, settings, key, ep, e["script"], log)


def rerender(ep: int, log=print) -> dict:
    """Перерендер готовой серии после правок сцены: тот же сценарий, голос и монтаж; заново мак и начальник."""
    conn = db.connect()
    settings = db.all_settings(conn)
    e = store.episode(conn, ep)
    key = e.get("series") or "katscena"
    job = JOBS / f"{ep:04d}"
    tl = json.loads((job / "timeline.json").read_text(encoding="utf-8"))
    tl["caption_y"] = series.S[key].get("caption_y", 0.69)
    (job / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
    final = render_on_mac(ep, job, log=log)
    rv = review.watch(conn, settings, key, final.read_bytes(), (e.get("script") or {}).get("logline") or "")
    store.save(conn, ep, status="approved" if rv.get("verdict") == "publish" else "needs_fix", video=str(final))
    store.event(conn, ep, "начальник", f"видео после правок: {rv.get('verdict')} ({rv.get('score')})", "; ".join(rv.get("problems") or [])[:500], rv)
    log(f"серия {ep}: начальник {rv.get('verdict')} {rv.get('score')}/10 — {'; '.join(rv.get('problems') or [])[:200]}")
    return rv


if __name__ == "__main__":
    if sys.argv[1:2] == ["rerender"]:
        rerender(int(sys.argv[2]))
        sys.exit(0)
    if sys.argv[1:2] == ["redo"]:
        redo(int(sys.argv[2]))
        sys.exit(0)
    a = sys.argv[1:]
    produce(a[0], a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else None)
