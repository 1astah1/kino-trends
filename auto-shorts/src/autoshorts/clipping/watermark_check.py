"""Foreign watermark check on a source proxy (ARCHITECTURE.md §6.3.1 `watermark_check`, §1.3 п. 3).

Foreign watermarks cost ~90% of reach on every platform, and YouTube treats re-uploads with somebody
else's logo as reused content. Sources with a foreign logo are **not masked** — they are flagged so the
operator drops them or gets a clean master from the rights holder.

    report = check_watermark("proxy_540p.mp4")                 # logos from config/logos/*.png
    report.suspect, report.to_dict()                           # -> sources.watermark_suspect / _detail

Two independent signals:

1. **Known logos** (the §6.3.1 check). 12 frames spread over the video are compared with every image in
   `config/logos/` (PNG with alpha = masked match) by `cv2.matchTemplate(TM_CCOEFF_NORMED)`, logo
   height 3-20% of the frame height, coarse-to-fine over scales. Score >= 0.8 in at least `min_frames`
   frames -> `suspect` (severity "warning").
2. **Static corner overlay** (logo-agnostic hint). Edges that stay in place in >= 90% of the sampled
   frames while the picture itself changes, concentrated in a corner, look like a burned-in logo or
   channel bug. This only yields severity "hint" (it can be the owner's own bug); letterbox lines and
   UI frames are ignored.

OpenCV runs single-threaded (`cv2.setNumThreads(1)`, §8.1). No frames are written to disk.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOGOS_DIR = PROJECT_ROOT / "config" / "logos"
DEFAULT_THRESHOLD = 0.8
DEFAULT_FRAMES = 12
HEIGHT_RANGE = (0.03, 0.20)  # logo height as a fraction of the frame height
COARSE_STEP = 1.08  # geometric scale step of the coarse pass (on a half-size frame)
FINE_STEPS = (0.96, 0.98, 1.0, 1.02, 1.04)
COARSE_KEEP = 0.55  # coarse score that earns a full-resolution refinement
ANALYSIS_MAX_H = 540
LOGO_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")
CORNER_W, CORNER_H = 0.30, 0.22


def _cv2() -> Any:
    import cv2

    cv2.setNumThreads(1)
    return cv2


@dataclass(slots=True)
class Logo:
    name: str
    image: np.ndarray  # gray uint8, cropped to the alpha bbox
    mask: np.ndarray | None  # uint8 0/255 or None
    height_range: tuple[float, float] = HEIGHT_RANGE


@dataclass(slots=True)
class WatermarkHit:
    logo: str
    t: float
    score: float
    x: int
    y: int
    w: int
    h: int
    height_fraction: float


@dataclass(slots=True)
class WatermarkReport:
    suspect: bool
    severity: str  # "ok" | "hint" | "warning"
    threshold: float
    frames_checked: int
    logos_checked: int
    hits: list[WatermarkHit] = field(default_factory=list)
    best: dict[str, float] = field(default_factory=dict)
    frames_hit: dict[str, int] = field(default_factory=dict)
    static_overlay: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["hits"] = [asdict(h) for h in sorted(self.hits, key=lambda h: -h.score)[:20]]
        return d


# ---------------------------------------------------------------------- logos


def load_logos(logos_dir: str | Path | None = None) -> list[Logo]:
    """All logo images in `logos_dir` (default config/logos). Sidecar `<name>.json` may set
    {"name": "...", "height_range": [0.03, 0.2]}. Files starting with "_" or "." are ignored."""
    cv2 = _cv2()
    root = Path(logos_dir) if logos_dir else LOGOS_DIR
    if not root.is_dir():
        return []
    logos: list[Logo] = []
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in LOGO_SUFFIXES or path.name.startswith(("_", ".")):
            continue
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        mask = None
        if img.ndim == 3 and img.shape[2] == 4:
            alpha = img[:, :, 3]
            ys, xs = np.nonzero(alpha > 32)
            if ys.size == 0:
                continue
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            img = img[y0:y1, x0:x1]
            alpha = alpha[y0:y1, x0:x1]
            mask = np.where(alpha > 32, 255, 0).astype(np.uint8)
            if float((mask > 0).mean()) >= 0.9:
                mask = None  # (almost) opaque: the plain FFT-based match is ~4x faster and as good
            gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
        elif img.ndim == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img
        meta: dict[str, Any] = {}
        side = path.with_suffix(".json")
        if side.is_file():
            try:
                meta = json.loads(side.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        lo, hi = (float(x) for x in meta.get("height_range", HEIGHT_RANGE))
        logos.append(Logo(str(meta.get("name", path.stem)), gray.astype(np.uint8), mask, (lo, hi)))
    return logos


# ---------------------------------------------------------------------- frames


def sample_frames(
    video: str | Path, n: int = DEFAULT_FRAMES, duration: float | None = None
) -> list[tuple[float, np.ndarray]]:
    """`n` BGR frames at (i + 0.5) / n of the duration, downscaled to <= 540 px height."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video {video}")
    try:
        if duration is None:
            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
            duration = count / fps if fps > 0 else 0.0
        out: list[tuple[float, np.ndarray]] = []
        for i in range(n):
            t = (duration or 0.0) * (i + 0.5) / n
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            if h > ANALYSIS_MAX_H:
                frame = cv2.resize(
                    frame, (int(round(w * ANALYSIS_MAX_H / h)), ANALYSIS_MAX_H), interpolation=cv2.INTER_AREA
                )
            out.append((round(t, 3), frame))
        return out
    finally:
        cap.release()


# ---------------------------------------------------------------------- matching


def _scales(lo: float, hi: float, step: float = COARSE_STEP) -> list[float]:
    out, f = [], lo
    while f <= hi * 1.0001:
        out.append(round(f, 4))
        f *= step
    return out


def _match(img: np.ndarray, logo: Logo, th: int) -> tuple[float, int, int, int, int] | None:
    """(score, x, y, w, h) of the best TM_CCOEFF_NORMED match of `logo` scaled to height `th`."""
    cv2 = _cv2()
    lh, lw = logo.image.shape[:2]
    tw = int(round(lw * th / max(lh, 1)))
    ih, iw = img.shape[:2]
    if th < 8 or tw < 8 or th >= ih or tw >= iw:
        return None
    interp = cv2.INTER_AREA if th < lh else cv2.INTER_LINEAR
    tpl = cv2.resize(logo.image, (tw, th), interpolation=interp)
    if float(tpl.std()) < 4.0:
        return None  # flat template: correlation is meaningless
    if logo.mask is not None:
        m = cv2.resize(logo.mask, (tw, th), interpolation=cv2.INTER_NEAREST)
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED, mask=m)
    else:
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    _, score, _, loc = cv2.minMaxLoc(res)
    return float(score), int(loc[0]), int(loc[1]), tw, th


def match_logo(gray: np.ndarray, logo: Logo) -> WatermarkHit | None:
    """Best match of `logo` in one gray frame (t is filled by the caller).

    Coarse pass: half-size frame, logo heights over `height_range` in geometric steps of 8%.
    Fine pass: full-size frame, +-4% around the best coarse scale, only in a window around the coarse
    location. Cost is ~0.1 s per logo and frame at 540p.
    """
    cv2 = _cv2()
    fh, fw = gray.shape[:2]
    small = cv2.resize(gray, (fw // 2, fh // 2), interpolation=cv2.INTER_AREA)
    coarse: tuple[float, float, int, int] | None = None  # (score, frac, x, y) in full-res coords
    for frac in _scales(*logo.height_range):
        r = _match(small, logo, int(round(fh * frac / 2)))
        if r and (coarse is None or r[0] > coarse[0]):
            coarse = (r[0], frac, r[1] * 2, r[2] * 2)
    if coarse is None:
        return None
    c_score, c_frac, cx, cy = coarse
    best = WatermarkHit(logo.name, 0.0, round(c_score, 4), cx, cy, 0, int(round(fh * c_frac)), c_frac)
    if c_score < COARSE_KEEP:
        return best
    lh, lw = logo.image.shape[:2]
    for k in FINE_STEPS:
        frac = c_frac * k
        th = int(round(fh * frac))
        tw = int(round(lw * th / max(lh, 1)))
        pad = max(8, th // 3)
        x0, y0 = max(0, cx - pad), max(0, cy - pad)
        x1, y1 = min(fw, cx + tw + pad), min(fh, cy + th + pad)
        r = _match(gray[y0:y1, x0:x1], logo, th)
        if r and r[0] > best.score:
            best = WatermarkHit(
                logo.name, 0.0, round(r[0], 4), x0 + r[1], y0 + r[2], r[3], r[4], round(frac, 4)
            )
    if best.w == 0:  # no fine match improved on the coarse one
        best.w = int(round(lw * best.h / max(lh, 1)))
    return best


def detect_static_overlay(grays: list[np.ndarray], *, min_motion: float = 6.0) -> dict[str, Any] | None:
    """Logo-agnostic hint: persistent edges concentrated in a corner of a moving picture."""
    cv2 = _cv2()
    if len(grays) < 6:
        return None
    h0, w0 = grays[0].shape[:2]
    W = 480
    H = max(2, int(round(h0 * W / w0)))
    small = [cv2.resize(g, (W, H), interpolation=cv2.INTER_AREA) for g in grays]
    diffs = [
        float(np.mean(np.abs(small[i].astype(np.int16) - small[i - 1].astype(np.int16))))
        for i in range(1, len(small))
    ]
    motion = float(np.median(diffs))
    if motion < min_motion:
        return {"checked": False, "reason": "low_motion", "motion": round(motion, 2)}
    edges = np.stack([cv2.Canny(g, 60, 160) > 0 for g in small])
    stable = edges.mean(axis=0) >= 0.9
    stable[stable.mean(axis=1) > 0.4, :] = False  # horizontal lines (letterbox, UI bars)
    stable[:, stable.mean(axis=0) > 0.4] = False  # vertical lines (pillarbox)
    ch, cw = int(H * CORNER_H), int(W * CORNER_W)
    corners = {
        "top_left": stable[:ch, :cw],
        "top_right": stable[:ch, W - cw :],
        "bottom_left": stable[H - ch :, :cw],
        "bottom_right": stable[H - ch :, W - cw :],
    }
    center = stable[int(H * 0.3) : int(H * 0.7), int(W * 0.3) : int(W * 0.7)]
    base = float(center.mean()) if center.size else 0.0
    dens = {k: float(v.mean()) for k, v in corners.items()}
    corner, d = max(dens.items(), key=lambda kv: kv[1])
    flagged = d >= 0.015 and d >= 4.0 * base + 0.005
    return {
        "checked": True,
        "flagged": bool(flagged),
        "corner": corner if flagged else None,
        "density": round(d, 4),
        "baseline": round(base, 4),
        "motion": round(motion, 2),
        "densities": {k: round(v, 4) for k, v in dens.items()},
    }


def check_frames(
    frames: list[tuple[float, np.ndarray]],
    logos: list[Logo],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_frames: int = 1,
    static_hint: bool = True,
) -> WatermarkReport:
    cv2 = _cv2()
    grays = [(t, cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f) for t, f in frames]
    hits: list[WatermarkHit] = []
    best: dict[str, float] = {}
    frames_hit: dict[str, int] = {}
    for logo in logos:
        for t, g in grays:
            hit = match_logo(g, logo)
            if hit is None:
                continue
            hit.t = t
            best[logo.name] = max(best.get(logo.name, -1.0), hit.score)
            if hit.score >= threshold:
                hits.append(hit)
                frames_hit[logo.name] = frames_hit.get(logo.name, 0) + 1
    suspect = any(v >= min_frames for v in frames_hit.values())
    notes: list[str] = []
    if not logos:
        notes.append("config/logos is empty: only the static-overlay hint was checked")
    overlay = detect_static_overlay([g for _, g in grays]) if static_hint else None
    severity = "warning" if suspect else ("hint" if overlay and overlay.get("flagged") else "ok")
    if suspect:
        names = ", ".join(sorted(k for k, v in frames_hit.items() if v >= min_frames))
        notes.append(f"foreign logo detected ({names}): do not use this source; get a clean master")
    elif severity == "hint":
        notes.append(
            f"persistent overlay in the {overlay['corner']} corner: check for a burned-in logo "  # type: ignore[index]
            "(add known foreign logos to config/logos)"
        )
    return WatermarkReport(
        suspect=suspect,
        severity=severity,
        threshold=threshold,
        frames_checked=len(grays),
        logos_checked=len(logos),
        hits=hits,
        best={k: round(v, 4) for k, v in best.items()},
        frames_hit=frames_hit,
        static_overlay=overlay,
        notes=notes,
    )


def check_watermark(
    video: str | Path,
    *,
    logos_dir: str | Path | None = None,
    logos: list[Logo] | None = None,
    n_frames: int = DEFAULT_FRAMES,
    threshold: float = DEFAULT_THRESHOLD,
    min_frames: int = 1,
    duration: float | None = None,
) -> WatermarkReport:
    frames = sample_frames(video, n_frames, duration)
    return check_frames(
        frames,
        logos if logos is not None else load_logos(logos_dir),
        threshold=threshold,
        min_frames=min_frames,
    )


__all__ = [
    "DEFAULT_THRESHOLD",
    "LOGOS_DIR",
    "Logo",
    "WatermarkHit",
    "WatermarkReport",
    "check_frames",
    "check_watermark",
    "detect_static_overlay",
    "load_logos",
    "match_logo",
    "sample_frames",
]
