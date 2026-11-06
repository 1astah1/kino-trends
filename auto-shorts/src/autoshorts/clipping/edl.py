"""EDL клипа: монтажный лист для рендера одним проходом (docs/ARCHITECTURE.md §6.3.7, §6.3.8).

```json
{"canvas":{"w":1080,"h":1920,"fps":30},
 "video":[{"type":"src","in":84.12,"out":96.40,"reframe":"track","crop_cmd":"crop_000.cmd"},
          {"type":"freeze","at_src":96.40,"dur":2.6,"pan":"in"},
          {"type":"src","in":96.40,"out":121.90,"reframe":"fit_blur"}],
 "audio":{"src_duck":[[0.0,2.4,-16],[12.28,14.88,-60]],
          "vo":[{"file":"vo_open.wav","at":0.0},{"file":"vo_ins1.wav","at":12.28},{"file":"vo_out.wav","at":38.1}]},
 "captions":[{"style":"Source","words_ref":"src_words.json"},{"style":"Author","words_ref":"vo_words.json"}],
 "banner":{"variant_id":12,"windows":[[16.0,20.5],[38.5,41.0]]},
 "expected_static":[[12.28,14.88]],
 "meta":{"ai_disclosure":true}}
```

Сверх примера §6.3.7 у элементов есть `t` (начало на выходной шкале), `frames`, `role`
(body | cold_open | insert | outro) и `crop` (окно кропа в пикселях файла рендера), у EDL —
`texts` (свой заголовок и тезисы на экране), `source`, `clip`, `warnings`.

Раскладка (`layout_clip`) по сценарию автора:

* **cold open** — `preroll`: голос автора поверх кадров ПЕРЕД началом клипа (исходник приглушён
  на 16 дБ), так хук исходника звучит полностью; `duck`: голос поверх начала клипа (как в
  примере §6.3.7); `freeze`: первый кадр замирает с медленным наездом. `auto` берёт preroll,
  если перед клипом хватает материала, иначе duck; `duck_source=false` в сценарии → freeze;
* **вставка** после предложения `after_sid`: `freeze_zoom` — кадр конца предложения замирает с
  наездом (`expected_static`), звук исходника убран; `over_ducked` — голос поверх идущего
  исходника, приглушённого на 16 дБ;
* **outro** — замирает первый кадр ролика (петля: конец смыкается с началом), голос автора;
* длительности всех кусков кратны кадру (1/fps), поэтому звук и видео не расходятся;
* если ролик с авторским слоем длиннее `max_total_sec` (58 с), конец исходника
  подрезается по ближайшему концу предложения. Время резервируется только под вставки, которые
  после подрезки остаются в клипе: вставка, которая всё равно выпала бы за новый конец,
  снимается (`insert_N_dropped_to_fit`), и подрезка пересчитывается без неё — иначе ролик
  терял бы и вставку, и зарезервированные под неё секунды исходника;
* свой заголовок и тезисы засчитываются в author_share (`counts_as_author`), только если в них
  есть свои слова и это не цитата транскрипта (`textutil.author_text_status`); причина отказа —
  в `TextItem.note`.

Никаких преобразований «для неузнаваемости» (зеркало, сдвиг тона и скорости, шум, дрожание
кропа) здесь нет и не будет: оригинальность = авторский вклад (§1.3).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from autoshorts.clipping import intervals as iv
from autoshorts.clipping import lag_ad
from autoshorts.clipping.author.share import AuthorShare, TextEvent, VoiceEvent, compute_author_share
from autoshorts.clipping.author.textutil import author_text_status
from autoshorts.clipping.transcript import Transcript, Word

EDL_VERSION = 1
CANVAS_W, CANVAS_H, FPS = 1080, 1920, 30.0
_TERMINAL = (".", "?", "!", "…")
_CLOSERS = "\"'»”’)]}"

Role = Literal["body", "cold_open", "insert", "outro"]


# ---------------------------------------------------------------------- модель


@dataclass(slots=True)
class VideoItem:
    type: Literal["src", "freeze"]
    t: float  # начало на выходной шкале
    dur: float
    frames: int
    in_: float | None = None  # src: отрезок источника
    out: float | None = None
    at_src: float | None = None  # freeze: момент источника
    pan: str = "in"
    reframe: str = "center"
    crop_cmd: str | None = None
    crop: dict[str, int] | None = None
    role: str = "body"

    @property
    def end(self) -> float:
        return self.t + self.dur

    @property
    def src_time(self) -> float:
        return float(self.in_ if self.type == "src" else self.at_src)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.type == "src":
            d.update({"in": round(self.in_ or 0.0, 4), "out": round(self.out or 0.0, 4)})
        else:
            d.update({"at_src": round(self.at_src or 0.0, 4), "dur": round(self.dur, 4), "pan": self.pan})
        d.update({"reframe": self.reframe, "t": round(self.t, 4), "frames": self.frames, "role": self.role})
        if self.type == "src":
            d["dur"] = round(self.dur, 4)
        if self.crop_cmd:
            d["crop_cmd"] = self.crop_cmd
        if self.crop:
            d["crop"] = dict(self.crop)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], fps: float = FPS) -> VideoItem:
        frames = int(d.get("frames", 0))
        dur = (
            frames / fps
            if frames > 0
            else float(d.get("dur") or (float(d["out"]) - float(d["in"]) if d["type"] == "src" else 0.0))
        )
        return cls(
            type=d["type"],
            t=float(d.get("t", 0.0)),
            dur=dur,
            frames=frames,
            in_=float(d["in"]) if "in" in d else None,
            out=float(d["out"]) if "out" in d else None,
            at_src=float(d["at_src"]) if "at_src" in d else None,
            pan=str(d.get("pan", "in")),
            reframe=str(d.get("reframe", "center")),
            crop_cmd=d.get("crop_cmd"),
            crop=dict(d["crop"]) if d.get("crop") else None,
            role=str(d.get("role", "body")),
        )


@dataclass(slots=True)
class DuckWindow:
    start: float
    end: float
    gain_db: float  # отрицательное: −16 = исходник тише на 16 дБ

    def to_list(self) -> list[float]:
        return [round(self.start, 3), round(self.end, 3), round(self.gain_db, 1)]


@dataclass(slots=True)
class VoItem:
    file: str
    at: float
    dur: float
    role: str
    kind: str = "tts"  # tts | operator | mock
    text: str = ""
    words: list[Word] = field(default_factory=list)  # от начала файла

    @property
    def end(self) -> float:
        return self.at + self.dur

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "at": round(self.at, 3),
            "dur": round(self.dur, 3),
            "role": self.role,
            "kind": self.kind,
            "text": self.text,
            "words": [{"w": w.w, "s": round(w.s, 3), "e": round(w.e, 3)} for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> VoItem:
        return cls(
            file=str(d["file"]),
            at=float(d["at"]),
            dur=float(d.get("dur", 0.0)),
            role=str(d.get("role", "insert")),
            kind=str(d.get("kind", "tts")),
            text=str(d.get("text", "")),
            words=[Word(str(w["w"]), float(w["s"]), float(w["e"])) for w in d.get("words", [])],
        )


@dataclass(slots=True)
class TextItem:
    kind: Literal["title", "on_screen"]
    text: str
    start: float
    end: float
    role: str = "body"
    counts_as_author: bool = True
    note: str = ""  # почему не засчитан: no_words | duplicates_transcript

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind,
            "text": self.text,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "role": self.role,
            "counts_as_author": self.counts_as_author,
        }
        if self.note:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> TextItem:
        return cls(
            d["kind"],
            d["text"],
            float(d["start"]),
            float(d["end"]),
            d.get("role", "body"),
            bool(d.get("counts_as_author", True)),
            str(d.get("note") or ""),
        )


@dataclass(slots=True)
class EDL:
    canvas: dict[str, Any] = field(default_factory=lambda: {"w": CANVAS_W, "h": CANVAS_H, "fps": FPS})
    source: dict[str, Any] = field(default_factory=dict)
    clip: dict[str, Any] = field(default_factory=dict)
    video: list[VideoItem] = field(default_factory=list)
    src_duck: list[DuckWindow] = field(default_factory=list)
    vo: list[VoItem] = field(default_factory=list)
    captions: list[dict[str, Any]] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)
    banner: dict[str, Any] | None = None
    expected_static: list[tuple[float, float]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    src_gain_db: float = 0.0
    vo_gain_db: float = 0.0
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ шкала

    @property
    def fps(self) -> float:
        return float(self.canvas.get("fps", FPS))

    @property
    def duration(self) -> float:
        return sum(v.dur for v in self.video)

    @property
    def total_frames(self) -> int:
        return sum(v.frames for v in self.video)

    def freeze_windows(self) -> list[tuple[float, float]]:
        return [(v.t, v.end) for v in self.video if v.type == "freeze"]

    def src_items(self) -> list[VideoItem]:
        return [v for v in self.video if v.type == "src"]

    def src_to_out(self, t: float) -> float | None:
        for v in self.video:
            if v.type == "src" and (v.in_ or 0) - 1e-6 <= t <= (v.out or 0) + 1e-6:
                return v.t + (t - (v.in_ or 0))
        return None

    def vo_windows(self) -> list[tuple[float, float]]:
        return [(v.at, v.end) for v in self.vo]

    # ------------------------------------------------------------ слова для субтитров

    def source_words(self, words: Sequence[Word], *, hide_under_vo: bool = True) -> list[Word]:
        """Слова транскрипта на выходной шкале (только то, что звучит в src-кусках)."""
        hide = iv.normalize(self.vo_windows()) if hide_under_vo else []
        out: list[Word] = []
        for v in self.video:
            if v.type != "src":
                continue
            a, b = float(v.in_ or 0), float(v.out or 0)
            for w in words:
                mid = (w.s + w.e) / 2
                if w.e <= a or w.s >= b or not (a - 0.05 <= mid <= b + 0.05):
                    continue
                s = v.t + max(0.0, w.s - a)
                e = min(v.end, v.t + (w.e - a))
                if e - s <= 0.01:
                    continue
                m = (s + e) / 2
                if any(x <= m <= y for x, y in hide):
                    continue
                out.append(Word(w.w, round(s, 3), round(e, 3), w.p))
        return out

    def author_words(self) -> list[Word]:
        out: list[Word] = []
        for v in self.vo:
            if v.role == "ad":  # слоган рекламной вставки: на экране плашка, субтитры его закрыли бы
                continue
            for w in v.words:
                s = v.at + w.s
                e = min(v.end, v.at + w.e)
                if e > s:
                    out.append(Word(w.w, round(s, 3), round(e, 3)))
        return sorted(out, key=lambda w: w.s)

    # ------------------------------------------------------------ author_share

    def duck_at(self, a: float, b: float) -> float:
        """Сколько дБ приглушения у исходника на отрезке (по окну с наибольшим перекрытием)."""
        best, best_ov = 0.0, 0.0
        for d in self.src_duck:
            ov = min(b, d.end) - max(a, d.start)
            if ov > best_ov:
                best, best_ov = -d.gain_db, ov
        return best if best_ov >= 0.5 * (b - a) else 0.0

    def author_share(self) -> AuthorShare:
        # рекламная вставка (lag_ad) — не вклад автора: её стоп-кадры и слоган в долю не идут
        frz = iv.normalize([(v.t, v.end) for v in self.video if v.type == "freeze" and not v.role.startswith("lag_")])
        voice: list[VoiceEvent] = []
        for v in self.vo:
            if v.role == "ad":
                continue
            inside = iv.total(iv.intersect([(v.at, v.end)], frz)) >= (v.end - v.at) - 1e-3
            role = "insert" if v.role.startswith("insert") else v.role
            voice.append(VoiceEvent(v.at, v.end, role, self.duck_at(v.at, v.end), inside))
        texts = [TextEvent(t.start, t.end, t.kind, t.counts_as_author) for t in self.texts]
        return compute_author_share(self.duration, freeze_windows=frz, voice=voice, texts=texts)

    # ------------------------------------------------------------ JSON

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": EDL_VERSION,
            "canvas": dict(self.canvas),
            "source": dict(self.source),
            "clip": dict(self.clip),
            "duration": round(self.duration, 4),
            "video": [v.to_dict() for v in self.video],
            "audio": {
                "src_duck": [d.to_list() for d in self.src_duck],
                "vo": [v.to_dict() for v in self.vo],
                "src_gain_db": round(self.src_gain_db, 2),
                "vo_gain_db": round(self.vo_gain_db, 2),
            },
            "captions": list(self.captions),
            "texts": [t.to_dict() for t in self.texts],
            "banner": self.banner,
            "expected_static": [[round(a, 3), round(b, 3)] for a, b in self.expected_static],
            "meta": dict(self.meta),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> EDL:
        if int(d.get("version", 1)) > EDL_VERSION:
            raise ValueError("EDL is newer than this code")
        audio = d.get("audio") or {}
        canvas = dict(d.get("canvas") or {"w": CANVAS_W, "h": CANVAS_H, "fps": FPS})
        fps = float(canvas.get("fps", FPS))
        return cls(
            canvas=canvas,
            source=dict(d.get("source") or {}),
            clip=dict(d.get("clip") or {}),
            video=[VideoItem.from_dict(v, fps) for v in d.get("video", [])],
            src_duck=[DuckWindow(float(a), float(b), float(g)) for a, b, g in audio.get("src_duck", [])],
            vo=[VoItem.from_dict(v) for v in audio.get("vo", [])],
            captions=list(d.get("captions") or []),
            texts=[TextItem.from_dict(t) for t in d.get("texts", [])],
            banner=d.get("banner"),
            expected_static=[(float(a), float(b)) for a, b in d.get("expected_static", [])],
            meta=dict(d.get("meta") or {}),
            src_gain_db=float(audio.get("src_gain_db", 0.0)),
            vo_gain_db=float(audio.get("vo_gain_db", 0.0)),
            warnings=list(d.get("warnings") or []),
        )

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)
        return p

    @classmethod
    def load(cls, path: str | Path) -> EDL:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# ---------------------------------------------------------------------- раскладка


@dataclass(slots=True)
class LayoutConfig:
    fps: float = FPS
    canvas_w: int = CANVAS_W
    canvas_h: int = CANVAS_H
    cold_open_mode: Literal["auto", "preroll", "duck", "freeze"] = "auto"
    cold_open: bool = True  # False — без вступления автора (стоп-кадр с голосом в начале ролика)
    author_voice: bool = True  # False — без голоса автора вообще: ни вступления, ни вставок, ни концовки
    # без голоса — реплики сценария текстом: комментарии вставок в паузах сцены и вопрос-вывод в конце
    # (YouTube с 01.10.2026 режет показы перезаливам без своего вклада; шаблон кадра вкладом не считается)
    author_text: bool = True
    lag_ad: bool = False  # рекламная вставка «видео тормозит → BPN» (clipping/lag_ad.py), нужна реплика ad_slogan
    comment_cps: float = 14.0  # скорость чтения, символов в секунду
    comment_min_sec: float = 2.5
    comment_max_sec: float = 5.0
    comment_max_chars: int = 90
    cold_open_duck_db: float = 16.0  # ≥ 12 дБ → author-only по §6.3.8
    insert_duck_db: float = 16.0
    freeze_duck_db: float = 60.0
    vo_lead: float = 0.12  # голос начинается чуть позже окна
    vo_tail: float = 0.3  # и окно держится чуть дольше голоса
    outro_tail: float = 0.5
    over_duck_pre: float = 0.15
    over_duck_post: float = 0.25
    freeze_pan: str = "in"
    cold_freeze_pan: str = "in"
    outro_pan: str = "out"
    title_mode: Literal["persistent", "intro"] = "persistent"
    title_intro_sec: float = 4.0
    on_screen_min_sec: float = 2.0
    min_total_sec: float = 20.0
    max_total_sec: float = 58.0
    min_source_sec: float = 8.0
    insert_edge_gap: float = 2.0  # секунд исходника от начала/конца клипа до вставки
    text_dup_threshold: float = 0.7


def _is_terminal(word: str) -> bool:
    return word.strip().rstrip(_CLOSERS).rstrip().endswith(_TERMINAL)


def boundary_after(transcript: Transcript, sid: int) -> float:
    """Момент разреза после предложения `sid`: конец последнего слова + до 0,25 с.

    Не дальше середины паузы, чтобы не зацепить следующее слово.
    """
    st = transcript.sentences[sid]
    last = transcript.words[st.w1]
    nxt = transcript.words[st.w1 + 1] if st.w1 + 1 < len(transcript.words) else None
    gap = (nxt.s - last.e) if nxt else 0.5
    return last.e + max(0.0, min(0.25, gap / 2))


def _trim_point(transcript: Transcript, lo: float, hi: float) -> float:
    """Последний конец предложения (или слова с точкой, или слова) в `[lo, hi]` плюс хвост в тишину."""
    words = [w for w in transcript.words if lo < w.e <= hi]
    if not words:
        return hi
    ends = {transcript.words[s.w1].e for s in transcript.sentences}
    best = next((w for w in reversed(words) if w.e in ends), None)
    best = best or next((w for w in reversed(words) if _is_terminal(w.w)), None) or words[-1]
    idx = transcript.words.index(best)
    nxt = transcript.words[idx + 1] if idx + 1 < len(transcript.words) else None
    tail = min(0.35, (nxt.s - best.e) / 2) if nxt else 0.35
    return min(hi, best.e + max(0.0, tail))


class _Timeline:
    def __init__(self, fps: float) -> None:
        self.fps = fps
        self.frames = 0
        self.items: list[VideoItem] = []

    @property
    def t(self) -> float:
        return self.frames / self.fps

    def add_src(self, a: float, b: float, role: str) -> VideoItem | None:
        n = int(round((b - a) * self.fps))
        if n <= 0:
            return None
        item = VideoItem("src", self.t, n / self.fps, n, in_=a, out=a + n / self.fps, role=role)
        self.items.append(item)
        self.frames += n
        return item

    def add_freeze(self, at_src: float, dur: float, role: str, pan: str) -> VideoItem:
        n = max(1, int(round(dur * self.fps)))
        item = VideoItem("freeze", self.t, n / self.fps, n, at_src=at_src, pan=pan, role=role)
        self.items.append(item)
        self.frames += n
        return item


def _comment_text(cands: Sequence[str], limit: int) -> str:
    """Первая реплика, что влезает в `limit` символов; иначе первая непустая, обрезанная по слову."""
    clean = [str(c or "").strip() for c in cands if str(c or "").strip()]
    for c in clean:
        if len(c) <= limit:
            return c
    if not clean:
        return ""
    return clean[0][:limit].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


def _out_time(items: Sequence[VideoItem], src_t: float) -> float | None:
    """Момент источника → время на выходной шкале (по кускам исходника)."""
    for it in items:
        if it.type == "src" and it.in_ is not None and it.out is not None and it.in_ - 1e-3 <= src_t <= it.out + 1e-3:
            return it.t + (src_t - it.in_)
    return None


def text_comments(
    script: Any,
    transcript: Transcript,
    items: Sequence[VideoItem],
    total: float,
    cfg: LayoutConfig,
    clip_text: str,
    start_sid: int | None,
    end_sid: int | None,
) -> list[TextItem]:
    """Ролик без голоса автора: реплики сценария текстом. Вставка — после своей фразы (`after_sid`), не
    раньше конца заголовка и не внахлёст; концовка — последние секунды ролика. Пересказ сцены
    (`author_text_status` не засчитывает) не выводится: такой текст вкладом не считается."""

    def dur_of(text: str) -> float:
        return min(cfg.comment_max_sec, max(cfg.comment_min_sec, len(text) / cfg.comment_cps))

    out: list[TextItem] = []
    outro_text = _comment_text([script.outro.vo_text], cfg.comment_max_chars) if script.outro.vo_text.strip() else ""
    # вопрос зрителю короткий («А вы бы смогли?») — от концовки требуем только не пересказывать сцену
    outro_ok = bool(outro_text) and author_text_status(outro_text, clip_text, threshold=cfg.text_dup_threshold)[1] != "duplicates_transcript"
    outro_dur = dur_of(outro_text) if outro_ok else 0.0
    limit = total - outro_dur - (0.4 if outro_ok else 0.2)
    cursor = cfg.title_intro_sec + 0.3
    for i, ins in enumerate(script.inserts):
        sid = int(ins.after_sid)
        if start_sid is not None and end_sid is not None and not (start_sid <= sid < end_sid):
            continue
        if not 0 <= sid < len(transcript.sentences):
            continue
        at = _out_time(items, boundary_after(transcript, sid))
        if at is None:
            continue
        text = _comment_text([ins.vo_text, ins.on_screen], cfg.comment_max_chars)
        counts, note = author_text_status(text, clip_text, threshold=cfg.text_dup_threshold)
        if not text or not counts:
            continue
        a = max(at, cursor)
        b = min(a + dur_of(text), limit)
        if b - a < cfg.comment_min_sec:
            continue
        out.append(TextItem("title", text, a, b, f"insert_{i}", counts, note))
        cursor = b + 0.5
    if outro_ok:
        a = max(cursor, total - outro_dur)
        if total - a >= cfg.comment_min_sec:
            out.append(TextItem("title", outro_text, a, total, "outro", True, ""))
    return out


LAG_AD_IDX = 9  # индекс рекламной вставки среди вставок сценария (их ≤ 2)


@dataclass(slots=True)
class _LagAdInsert:
    after_sid: int
    mode: str = "lag_ad"
    on_screen: str = ""
    vo_text: str = ""


def _take_attr(take: Any, name: str, default: Any = None) -> Any:
    if isinstance(take, Mapping):
        return take.get(name, default)
    return getattr(take, name, default)


def layout_clip(
    transcript: Transcript,
    *,
    start: float,
    end: float,
    start_sid: int | None = None,
    end_sid: int | None = None,
    script: Any = None,
    takes: Mapping[str, Any] | None = None,
    source: Mapping[str, Any] | None = None,
    keep_segments: Sequence[Sequence[float]] | None = None,
    media_range: tuple[float, float] | None = None,
    config: LayoutConfig | None = None,
    meta: Mapping[str, Any] | None = None,
) -> EDL:
    """Раскладка клипа `[start, end]` (секунды источника) с авторским слоем по сценарию.

    `takes` — озвученные реплики по ролям (`cold_open`, `insert_0`, `insert_1`, `outro`):
    объекты `VoiceTake` или словари с `path`, `duration`, `words`, `kind`, `text`.
    `media_range` — какой отрезок источника реально доступен рендеру (для URL-секций ±3 с).
    Реплика без озвучки пропускается с предупреждением.
    """
    cfg = config or LayoutConfig()
    orig_start, orig_end = float(start), float(end)
    takes = dict(takes or {})
    src = dict(source or {})
    src_dur = float(src.get("duration") or transcript.duration or end)
    lo_media, hi_media = media_range or (0.0, src_dur)
    warnings: list[str] = []

    pieces = [(max(start, float(a)), min(end, float(b))) for a, b in (keep_segments or [(start, end)])]
    pieces = [(a, b) for a, b in pieces if b - a > 0.05]
    if not pieces:
        raise ValueError("empty clip range")

    sids = [s for s in transcript.sentences if s.e > start + 0.05 and s.s < end - 0.05]
    if start_sid is None:
        start_sid = sids[0].sid if sids else 0
    if end_sid is None:
        end_sid = sids[-1].sid if sids else start_sid
    sids = [s for s in sids if start_sid <= s.sid <= end_sid] or sids
    clip_text = " ".join(s.text for s in sids)

    co = script.cold_open if script is not None and cfg.cold_open and cfg.author_voice else None
    co_take = takes.get("cold_open") if co is not None and co.vo_text.strip() else None
    if co is not None and co.vo_text.strip() and co_take is None:
        warnings.append("cold_open_not_voiced")
    co_dur = float(_take_attr(co_take, "duration", 0.0)) if co_take is not None else 0.0
    co_window = cfg.vo_lead + co_dur + cfg.vo_tail if co_take is not None else 0.0

    co_mode = cfg.cold_open_mode
    if co_take is not None:
        if not co.duck_source:
            co_mode = "freeze"
        elif co_mode == "auto":
            co_mode = "preroll" if pieces[0][0] - co_window >= lo_media + 0.05 else "duck"
        elif co_mode == "preroll" and pieces[0][0] - co_window < lo_media + 0.05:
            warnings.append("preroll_unavailable_using_duck")
            co_mode = "duck"

    ins_list = []
    for i, ins in enumerate(list(script.inserts) if script is not None and cfg.author_voice else []):
        take = takes.get(f"insert_{i}")
        if take is None:
            warnings.append(f"insert_{i}_not_voiced")
            continue
        ins_list.append((i, ins, take))
    out_take = None
    if script is not None and cfg.author_voice and script.outro.vo_text.strip():
        out_take = takes.get("outro")
        if out_take is None:
            warnings.append("outro_not_voiced")
    ad_take = takes.get("ad_slogan") if cfg.lag_ad else None
    if cfg.lag_ad and ad_take is None:
        warnings.append("lag_ad_not_voiced")
    ad_extra = 0.0  # время под рекламную вставку: резервируется при подгонке, если сцене хватает длины

    # --- длительность: если не влезает в max_total_sec, подрезаем конец исходника
    def extra_sec(active: Sequence[tuple[int, Any, Any]]) -> float:
        x = ad_extra + (co_window if co_mode in ("freeze", "preroll") and co_take is not None else 0.0)
        for _, ins, take in active:
            if ins.mode == "freeze_zoom":
                x += cfg.vo_lead + float(_take_attr(take, "duration", 0.0)) + cfg.vo_tail

        if out_take is not None:
            x += cfg.vo_lead + float(_take_attr(out_take, "duration", 0.0)) + cfg.outro_tail
        return x

    def fit_pieces(
        active: Sequence[tuple[int, Any, Any]],
    ) -> tuple[list[tuple[float, float]], float | None]:
        """Куски исходника, подрезанные под max_total_sec с резервом под `active` вставки."""
        src_len = sum(b - a for a, b in pieces)
        allowed = cfg.max_total_sec - extra_sec(active) - 0.1
        if src_len <= allowed:
            return list(pieces), None
        allowed = max(cfg.min_source_sec, allowed)
        acc = 0.0
        cut_at = pieces[-1][1]
        for a, b in pieces:
            if acc + (b - a) >= allowed:
                cut_at = a + (allowed - acc)
                break
            acc += b - a
        new_end = _trim_point(transcript, pieces[0][0] + 1.0, cut_at)
        return [(a, min(b, new_end)) for a, b in pieces if a < new_end - 0.05], new_end

    duck_region_end_src = pieces[0][0] + (co_window if co_mode == "duck" else 0.0)

    def place_inserts(
        pcs: Sequence[tuple[float, float]], active: Sequence[tuple[int, Any, Any]]
    ) -> tuple[list[tuple[float, int, Any, Any]], list[str]]:
        """Точки вставок в кусках `pcs` и предупреждения о пропущенных."""
        out: list[tuple[float, int, Any, Any]] = []
        skipped: list[str] = []
        for i, ins, take in active:
            if not (0 <= ins.after_sid < len(transcript.sentences)):
                skipped.append(f"insert_{i}_bad_sid:{ins.after_sid}")
                continue
            c = boundary_after(transcript, ins.after_sid)
            if not any(a <= c <= b for a, b in pcs):
                # джамп-кат вырезал паузу, где стоит граница: вставка — в конце предыдущего куска
                prev = [b for a, b in pcs if b <= c]
                if prev:
                    c = prev[-1]
            in_piece = any(a + 0.2 < c <= b for a, b in pcs)
            lo_ok = max(pcs[0][0] + cfg.insert_edge_gap, duck_region_end_src + 0.5)
            hi_ok = pcs[-1][1] - cfg.insert_edge_gap
            if ins.mode == "over_ducked":
                hi_ok = min(hi_ok, pcs[-1][1] - float(_take_attr(take, "duration", 0.0)) - 0.5)
            if not in_piece or not (lo_ok <= c <= hi_ok):
                skipped.append(f"insert_{i}_skipped_out_of_range:sid{ins.after_sid}")
                continue
            if out and c - out[-1][0] < 1.0:
                skipped.append(f"insert_{i}_skipped_too_close")
                continue
            out.append((c, i, ins, take))
        return out, skipped

    # Вставки, которые встали бы без подрезки. Если подрезка выталкивает такую вставку за новый
    # конец, её резерв возвращается исходнику: самая поздняя из выпавших снимается, подрезка
    # считается заново (вставок ≤ 2, итераций ≤ 3). Раньше резерв оставался, и ролик терял и
    # вставку, и секунды исходника.
    if ad_take is not None:
        # вставка только в длинных роликах: сцена после неё (в пределах max_total_sec) не короче MIN_SOURCE_SEC;
        # ради рекламы сцену не режем
        need = cfg.vo_lead + lag_ad.total_sec(float(_take_attr(ad_take, "duration", 0.0)))
        scene = min(sum(b - a for a, b in pieces), cfg.max_total_sec - extra_sec(ins_list) - need - 0.1)
        if scene < lag_ad.MIN_SOURCE_SEC:
            ad_take = None
            warnings.append("lag_ad_clip_short")
        else:
            ad_extra = need
    fits_untrimmed = {i for _, i, _, _ in place_inserts(pieces, ins_list)[0]}
    active = list(ins_list)
    dropped_to_fit: list[tuple[int, Any, Any]] = []
    while True:
        fitted, new_end = fit_pieces(active)
        cuts, skipped = place_inserts(fitted, active)
        placed_ids = {i for _, i, _, _ in cuts}
        lost = [x for x in active if x[0] in fits_untrimmed and x[0] not in placed_ids]
        if new_end is None or not lost:
            break
        victim = max(lost, key=lambda x: (boundary_after(transcript, x[1].after_sid), x[0]))
        active = [x for x in active if x[0] != victim[0]]
        dropped_to_fit.append(victim)
    for i, ins, _ in sorted(dropped_to_fit, key=lambda x: x[0]):
        warnings.append(f"insert_{i}_dropped_to_fit:sid{ins.after_sid}")
    warnings += skipped
    if new_end is not None:
        warnings.append(f"clip_trimmed_to_fit:{end:.2f}->{new_end:.2f}")
        pieces = fitted
        end = new_end
        sids = [s for s in sids if s.s < end - 0.05]
        if sids:
            end_sid = sids[-1].sid
    if ad_take is not None:
        # граница фразы ближе к середине клипа, вдали от краёв и подсказок; время уже зарезервировано
        bounds = [(x.sid, boundary_after(transcript, x.sid)) for x in sids[:-1]]
        spot = lag_ad.pick_spot(bounds, pieces[0][0], pieces[-1][1], [c for c, _, _, _ in cuts])
        if spot is not None and any(a + 0.2 < spot[1] <= b for a, b in pieces):
            cuts = sorted([*cuts, (spot[1], LAG_AD_IDX, _LagAdInsert(after_sid=spot[0]), ad_take)], key=lambda x: x[0])
        else:
            warnings.append("lag_ad_no_spot")

    placed_inserts = [
        {"after_sid": ins.after_sid, "mode": ins.mode, "role": "lag_ad" if i == LAG_AD_IDX else f"insert_{i}"}
        for _, i, ins, _ in cuts
    ]
    lag_meta: dict[str, Any] | None = None
    tl = _Timeline(cfg.fps)
    edl = EDL(canvas={"w": cfg.canvas_w, "h": cfg.canvas_h, "fps": cfg.fps})
    vos: list[VoItem] = []
    ducks: list[DuckWindow] = []
    texts: list[TextItem] = []

    def vo_item(take: Any, role: str, at: float) -> VoItem:
        item = VoItem(
            file=str(_take_attr(take, "path")),
            at=round(at, 3),
            dur=float(_take_attr(take, "duration", 0.0)),
            role=role,
            kind=str(_take_attr(take, "kind", "tts")),
            text=str(_take_attr(take, "text", "")),
            words=list(_take_attr(take, "words", []) or []),
        )
        vos.append(item)
        return item

    def on_screen(text: str, a: float, b: float, role: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        counts, note = author_text_status(text, clip_text, threshold=cfg.text_dup_threshold)
        texts.append(TextItem("on_screen", text, a, max(b, a + cfg.on_screen_min_sec), role, counts, note))

    # --- cold open
    first = pieces[0][0]
    if co_take is not None:
        if co_mode == "freeze":
            fr = tl.add_freeze(first, co_window, "cold_open", cfg.cold_freeze_pan)
            vo_item(co_take, "cold_open", fr.t + cfg.vo_lead)
            ducks.append(DuckWindow(fr.t, fr.end, -cfg.freeze_duck_db))
            on_screen(co.on_screen, fr.t, fr.end, "cold_open")
        elif co_mode == "preroll":
            pr = tl.add_src(first - co_window, first, "cold_open")
            assert pr is not None
            first_in = pr.in_ or 0.0
            vo_item(co_take, "cold_open", pr.t + cfg.vo_lead)
            ducks.append(DuckWindow(pr.t, pr.end, -cfg.cold_open_duck_db))
            on_screen(co.on_screen, pr.t, pr.end, "cold_open")
            # следующий кусок продолжает ровно с кадра, где закончился preroll
            pieces[0] = (pr.out or first, pieces[0][1])
            first = first_in
        else:  # duck: поверх начала клипа
            vo_item(co_take, "cold_open", cfg.vo_lead)
            ducks.append(DuckWindow(0.0, co_window, -cfg.cold_open_duck_db))
            on_screen(co.on_screen, 0.0, co_window, "cold_open")

    # --- тело клипа со вставками
    body_items: list[VideoItem] = []
    for a, b in pieces:
        cursor = a
        for c, i, ins, take in cuts:
            if not (a < c <= b + 1e-9) or c <= cursor:
                continue
            dur = float(_take_attr(take, "duration", 0.0))
            role = f"insert_{i}"
            if ins.mode == "lag_ad":
                # дёрганье (стоп-кадры последних долей секунды), зависание с крутилкой, плашка и слоган
                it = tl.add_src(cursor, c, "body")
                if it is not None:
                    body_items.append(it)
                    cursor = it.out or c
                lo = it.in_ if it is not None and it.in_ is not None else cursor
                st0 = tl.t
                for back, hold in lag_ad.STUTTER:
                    tl.add_freeze(max(lo, cursor - back), hold, "lag_stutter", "none")
                fr = tl.add_freeze(cursor, lag_ad.BUFFER_SEC + cfg.vo_lead + dur + lag_ad.TAIL_SEC, "lag_ad", "none")
                vo_item(take, "ad", fr.t + lag_ad.BUFFER_SEC + cfg.vo_lead)
                ducks.append(DuckWindow(st0, fr.end, -cfg.freeze_duck_db))
                lag_meta = {"start": round(fr.t, 3), "dur": round(fr.dur, 3), "buffer": lag_ad.BUFFER_SEC,
                            "stutter_from": round(st0, 3), "sid": ins.after_sid, "src_at": round(cursor, 3)}
                continue
            if ins.mode == "freeze_zoom":
                # кусок до точки вставки, затем стоп-кадр последнего кадра этого куска
                it = tl.add_src(cursor, c, "body")
                if it is not None:
                    body_items.append(it)
                    cursor = it.out or c
                fr = tl.add_freeze(cursor, cfg.vo_lead + dur + cfg.vo_tail, "insert", cfg.freeze_pan)
                vo_item(take, role, fr.t + cfg.vo_lead)
                ducks.append(DuckWindow(fr.t, fr.end, -cfg.freeze_duck_db))
                on_screen(ins.on_screen, fr.t, fr.end, "insert")
            else:
                # голос поверх идущего исходника: видео не режется
                at = tl.t + (c - cursor) + 0.1
                v = vo_item(take, role, at)
                ducks.append(
                    DuckWindow(
                        max(0.0, at - cfg.over_duck_pre), v.end + cfg.over_duck_post, -cfg.insert_duck_db
                    )
                )
                on_screen(ins.on_screen, at, v.end, "insert")
        it = tl.add_src(cursor, b, "body")
        if it is not None:
            body_items.append(it)

    body_end = tl.t
    # --- outro: первый кадр ролика замирает (петля)
    if out_take is not None:
        first_frame = tl.items[0].src_time if tl.items else first
        dur = float(_take_attr(out_take, "duration", 0.0))
        fr = tl.add_freeze(first_frame, cfg.vo_lead + dur + cfg.outro_tail, "outro", cfg.outro_pan)
        vo_item(out_take, "outro", fr.t + cfg.vo_lead)
        ducks.append(DuckWindow(fr.t, fr.end, -cfg.freeze_duck_db))

    total = tl.t
    # голос поверх исходника не должен вылезать за конец исходника
    for v in vos:
        if (
            v.role.startswith("insert")
            and v.end > body_end + 1e-3
            and not any(f.t - 1e-3 <= v.at and v.end <= f.end + 1e-3 for f in tl.items if f.type == "freeze")
        ):
            warnings.append(f"{v.role}_runs_past_source_end")
    for x, y in zip(sorted(vos, key=lambda v: v.at), sorted(vos, key=lambda v: v.at)[1:], strict=False):
        if y.at < x.end - 0.05:
            warnings.append(f"vo_overlap:{x.role}/{y.role}")

    comments = text_comments(script, transcript, tl.items, total, cfg, clip_text, start_sid, end_sid) if (
        script is not None and not cfg.author_voice and cfg.author_text
    ) else []
    if comments:
        cfg = replace(cfg, title_mode="intro")  # заголовок уходит после вступления, его место — комментариям
        texts += comments

    # --- свой заголовок
    if script is not None and script.on_screen_title.strip():
        title = script.on_screen_title.strip()
        # В author_share заголовок засчитывается только первые TITLE_CREDIT_SEC каждого показа
        # (author/share.py), сколько бы он ни висел на экране.
        counts, note = author_text_status(title, clip_text, threshold=cfg.text_dup_threshold)
        if cfg.title_mode == "persistent":
            texts.insert(0, TextItem("title", title, 0.0, total, "body", counts, note))
        else:
            texts.insert(
                0, TextItem("title", title, 0.0, min(total, cfg.title_intro_sec), "body", counts, note)
            )
            if out_take is not None:
                texts.append(TextItem("title", title, body_end, total, "outro", counts, note))
    for t in texts:
        t.end = min(t.end, total)

    edl.video = tl.items
    edl.vo = vos
    edl.src_duck = [DuckWindow(max(0.0, d.start), min(total, d.end), d.gain_db) for d in ducks]
    edl.texts = [t for t in texts if t.end - t.start > 0.05]
    edl.expected_static = [(v.t, v.end) for v in tl.items if v.type == "freeze"]
    edl.captions = [
        {"style": "Source", "words_ref": "src_words.json"},
        {"style": "Author", "words_ref": "vo_words.json"},
    ]
    kinds = sorted({v.kind for v in vos})
    edl.source = {
        "path": str(src.get("path", "")),
        "width": int(src.get("width") or 0),
        "height": int(src.get("height") or 0),
        "duration": src_dur,
        "fps": float(src.get("fps") or 0.0),
        "has_audio": bool(src.get("has_audio", True)),
        **({"source_id": src["source_id"]} if src.get("source_id") else {}),
    }
    edl.clip = {
        "start": round(orig_start, 3),
        "end": round(end, 3),
        "requested_end": round(orig_end, 3),
        "preroll_start": round(first, 3) if co_mode == "preroll" and co_take is not None else None,
        "body_start": round(body_items[0].in_ or 0.0, 3) if body_items else None,
        "start_sid": start_sid,
        "end_sid": end_sid,
        "cold_open_mode": co_mode if co_take is not None else None,
        "sids": [s.sid for s in sids],
    }
    synthetic = any(k != "operator" for k in kinds)
    edl.meta = {
        "ai_disclosure": synthetic,
        "synthetic_voice": synthetic,
        "voice_kinds": kinds,
        "generator": "autoshorts.clipping",
        "inserts": placed_inserts,
        **({"lag_ad": lag_meta} if lag_meta else {}),
        **dict(meta or {}),
    }
    if total < cfg.min_total_sec:
        warnings.append(f"too_short:{total:.1f}s<{cfg.min_total_sec:.0f}s")
    if total > cfg.max_total_sec + 0.05:
        warnings.append(f"too_long:{total:.1f}s>{cfg.max_total_sec:.0f}s")
    edl.warnings = warnings
    return edl


def plain_layout(
    transcript: Transcript,
    *,
    start: float,
    end: float,
    source: Mapping[str, Any] | None = None,
    config: LayoutConfig | None = None,
) -> EDL:
    """EDL без авторского слоя (для своих источников, где автор — сам спикер)."""
    return layout_clip(transcript, start=start, end=end, source=source, config=config)


# ---------------------------------------------------------------------- рефрейм в EDL


def apply_reframe(edl: EDL, plan: Any, *, workdir: str | Path | None = None) -> list[Path]:
    """Разбить src-куски по шотам плана, назначить стратегию и окно кропа; записать crop_NNN.cmd.

    `plan` — `clipping.reframe.ReframePlan` в пикселях файла рендера. Возвращает пути файлов команд.
    """
    fps = edl.fps
    g = plan.geometry
    items: list[VideoItem] = []
    cmd_files: list[Path] = []
    wd = Path(workdir) if workdir else None
    crop_idx = 0
    t_frames = 0
    for v in edl.video:
        if v.type == "freeze":
            at = float(v.at_src or 0.0)
            mode = plan.mode_at(at)
            v2 = VideoItem(**{**_fields(v), "t": t_frames / fps})
            v2.reframe = "fit_blur" if mode == "fit_blur" else "center"
            v2.crop = {"w": g.crop_w, "h": g.crop_h, "x": plan.x_at(at), "y": g.y}
            items.append(v2)
            t_frames += v2.frames
            continue
        a, b = float(v.in_ or 0.0), float(v.out or 0.0)
        pieces = plan.pieces(a, b)
        remaining = v.frames
        for k, (pa, pb, shot) in enumerate(pieces):
            n = remaining if k == len(pieces) - 1 else max(1, int(round((pb - pa) * fps)))
            n = min(n, remaining - (len(pieces) - 1 - k))  # у каждого следующего куска хотя бы кадр
            if n <= 0:
                continue
            remaining -= n
            piece_in = pa if k == 0 else items[-1].out or pa
            it = VideoItem(
                "src",
                t_frames / fps,
                n / fps,
                n,
                in_=piece_in,
                out=piece_in + n / fps,
                role=v.role,
                reframe=shot.mode,
            )
            it.crop = {"w": g.crop_w, "h": g.crop_h, "x": shot.x_at(piece_in), "y": g.y}
            if shot.mode == "track":
                name = f"crop_{crop_idx:03d}.cmd"
                it.crop_cmd = name
                if wd is not None:
                    lines = plan.sendcmd_for(piece_in, piece_in + n / fps, target=f"crop@c{crop_idx}")
                    p = wd / name
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("\n".join(lines) + "\n", encoding="ascii")
                    cmd_files.append(p)
                crop_idx += 1
            items.append(it)
            t_frames += n
    edl.video = items
    edl.meta["reframe"] = {
        "modes": sorted({i.reframe for i in items}),
        "scene_cuts": [round(c, 3) for c in getattr(plan, "scene_cuts", [])],
        "geometry": g.to_dict(),
    }
    return cmd_files


def _fields(v: VideoItem) -> dict[str, Any]:
    return {
        "type": v.type,
        "t": v.t,
        "dur": v.dur,
        "frames": v.frames,
        "in_": v.in_,
        "out": v.out,
        "at_src": v.at_src,
        "pan": v.pan,
        "reframe": v.reframe,
        "crop_cmd": v.crop_cmd,
        "crop": dict(v.crop) if v.crop else None,
        "role": v.role,
    }


def source_range(edl: EDL, pad: float = 0.0) -> tuple[float, float]:
    """Отрезок источника, который нужен рендеру и анализу рефрейма."""
    ts: list[float] = []
    for v in edl.video:
        if v.type == "src":
            ts += [float(v.in_ or 0.0), float(v.out or 0.0)]
        else:
            ts.append(float(v.at_src or 0.0))
    if not ts:
        return (0.0, 0.0)
    return (max(0.0, min(ts) - pad), max(ts) + pad)


def words_json(words: Iterable[Word]) -> list[dict[str, Any]]:
    return [{"w": w.w, "s": round(w.s, 3), "e": round(w.e, 3)} for w in words]


__all__ = [
    "EDL",
    "EDL_VERSION",
    "DuckWindow",
    "LayoutConfig",
    "TextItem",
    "VideoItem",
    "VoItem",
    "apply_reframe",
    "boundary_after",
    "layout_clip",
    "plain_layout",
    "source_range",
    "words_json",
]
