"""Сквозной прогон `scripts/clip_local.py` и URL-источник со звуком отдельно (ffmpeg, nice 10, 2 потока).

* clip_local: крошечный testsrc2 + «речь» из тональных слогов, свой фрагмент, сценарий оператора,
  mock-голос, демо-баннер → clip_01.mp4, отчёты json/md, candidates.json, run.log, история источника;
  второй прогон того же момента (без рендера) ловит повтор уже нарезанного момента.
* ingest_url с поддельным yt-dlp (видео без звука + отдельный звук, как у YouTube) → клип, в
  котором звук исходника действительно есть (раньше рендер брал звук из видеофайла без звука).

Run: pytest -m media tests/media/test_clipping_local.py
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.media

if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
    pytest.skip("ffmpeg/ffprobe not installed", allow_module_level=True)

from autoshorts.clipping.author.voice import synthesize_mock  # noqa: E402
from autoshorts.clipping.produce import ClipJobConfig, ClipSource, produce_clip  # noqa: E402
from autoshorts.clipping.source_ingest import PreparedSource, ingest_url  # noqa: E402
from autoshorts.clipping.transcript import Transcript, Word  # noqa: E402
from autoshorts.pipeline.proxy import probe_media  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TEXT = (
    "Почему одни ролики досматривают, а другие нет? Всё решают первые три секунды. "
    "Если зритель не понял, о чём ролик, он листает дальше. Поэтому хук должен быть конкретным. "
    "Второе правило касается темпа. Паузы длиннее секунды убивают внимание. "
    "Третье правило про финал. Закончите мыслью, а не прощанием."
)
SCRIPT = {
    "cold_open": {"vo_text": "Одно правило решает, досмотрят ли ролик.", "duck_source": True},
    "inserts": [
        {
            "after_sid": 3,
            "mode": "freeze_zoom",
            "vo_text": "Заметьте: речь о ясности, а не о громкости. Это разные вещи.",
            "on_screen": "Ясность важнее",
        }
    ],
    "outro": {"vo_text": "А вы на какой секунде листаете?", "cta_style": "question"},
    "on_screen_title": "Три секунды решают всё",
}


def ffmpeg(*args: str) -> None:
    subprocess.run(
        ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2", *args],
        check=True,
    )


@pytest.fixture(scope="module")
def src(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Transcript]:
    d = tmp_path_factory.mktemp("local_src")
    take = synthesize_mock("speech", TEXT, d, base_hz=140.0)
    lead, tail = 5.0, 3.0
    dur = lead + take.duration + tail
    video = d / "talk.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={dur:.3f}",
        "-i", str(take.path), "-af", f"adelay={int(lead * 1000)}:all=1,apad", "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
    )  # fmt: skip
    words = [Word(w.w, round(w.s + lead, 3), round(w.e + lead, 3)) for w in take.words]
    tr = Transcript.from_words(words, language="ru", duration=dur, provider="mock")
    tpath = tr.save(d / "t.json.gz")
    return video, tpath, tr


@pytest.fixture(scope="module")
def clip_local():  # noqa: ANN201
    spec = importlib.util.spec_from_file_location("clip_local_media", ROOT / "scripts" / "clip_local.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["clip_local_media"] = mod
    spec.loader.exec_module(mod)
    return mod


def _creative(path: Path) -> Path:
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (600, 150), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, 599, 149), radius=30, fill=(90, 40, 200, 235))
    im.save(path)
    return path


def test_clip_local_end_to_end(clip_local, src, tmp_path: Path) -> None:  # noqa: ANN001
    video, tpath, tr = src
    script = tmp_path / "script.json"
    script.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "run1"
    sources = tmp_path / "sources"
    a, b = tr.sentences[0].s - 0.2, tr.sentences[-1].e + 0.3
    common = [
        "--video", str(video), "--rights", "licensed_written", "--owner", "Test Owner",
        "--proof", "test licence", "--attribution", "Test Owner, youtube.com/@test",
        "--allowlist", "allowlisted", "--transcript", str(tpath), "--sources-root", str(sources),
        "--clip", f"{a:.2f}:{b:.2f}", "--llm", "none", "--tts", "mock",
    ]  # fmt: skip
    rc = clip_local.main(
        [*common, "--author-script", str(script), "--banner", str(_creative(tmp_path / "b.png")),
         "--banner-code", "t1", "--out", str(out)]
    )  # fmt: skip
    assert rc == 0
    for name in ("clip_01.mp4", "clip_01.originality.json", "clip_01.originality.md", "candidates.json",
                 "run.log", "summary.json"):  # fmt: skip
        assert (out / name).is_file(), name
    pr = probe_media(out / "clip_01.mp4")
    assert (pr.width, pr.height) == (1080, 1920)
    assert 20.0 <= pr.duration <= 58.0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    clip = summary["clips"][0]
    assert clip["banner"] is True and clip["render"]["banner"] is True
    assert clip["author"]["mode"] == "operator"
    rep = json.loads((out / "clip_01.originality.json").read_text(encoding="utf-8"))
    assert rep["verdict"] in ("pass", "warn")
    assert rep["clip"]["id"] == "run1/clip_01"
    md = (out / "clip_01.originality.md").read_text(encoding="utf-8")
    assert md.startswith("# Отчёт об оригинальности — clip_01") and "## Что сделать до заливки" in md
    log = (out / "run.log").read_text(encoding="utf-8")
    assert "Тайминги" in log and "clip.render" in log
    edl = json.loads((out / "work" / "clip_01" / "edl.json").read_text(encoding="utf-8"))
    assert edl["banner"]["windows"]
    # история источника: момент записан, второй прогон того же момента — повтор
    src_dir = Path(summary["source_dir"])
    hist = json.loads((src_dir / "clipped.json").read_text(encoding="utf-8"))
    assert [h["id"] for h in hist["clips"]] == ["run1/clip_01"]
    assert hist["clips"][0]["fingerprint"] is not None
    out2 = tmp_path / "run2"
    assert clip_local.main([*common, "--no-render", "--out", str(out2)]) == 0
    rep2 = json.loads((out2 / "clip_01.originality.json").read_text(encoding="utf-8"))
    rm = next(c for c in rep2["checks"] if c["code"] == "repeat_moment")
    assert rm["status"] == "fail" and rm["value"] >= 0.5
    # --supersede: переснятый дубль того же момента не считается повтором
    out3 = tmp_path / "run3"
    assert clip_local.main([*common, "--no-render", "--supersede", "run1/clip_01", "--out", str(out3)]) == 0
    rep3 = json.loads((out3 / "clip_01.originality.json").read_text(encoding="utf-8"))
    assert next(c for c in rep3["checks"] if c["code"] == "repeat_moment")["status"] != "fail"


class DiskYDL:
    """Поддельный yt-dlp: «скачивает» готовые файлы — видео без звука и звук отдельно."""

    def __init__(self, video_only: Path, audio: Path, duration: float) -> None:
        self.video_only, self.audio, self.duration = video_only, audio, duration
        self.opts: dict = {}

    def __call__(self, opts: dict) -> DiskYDL:
        self.opts = opts
        return self

    def __enter__(self) -> DiskYDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def _target(self) -> Path:
        tmpl = self.opts["outtmpl"]
        ext = "m4a" if "original_audio" in tmpl else "mp4"
        return Path(tmpl.replace("%(ext)s", ext))

    def extract_info(self, url: str, download: bool = False) -> dict:
        info = {"id": "abc123", "title": "Talk", "duration": self.duration, "license": "", "ext": "mp4"}
        if download:
            dst = self._target()
            shutil.copyfile(self.audio if dst.suffix == ".m4a" else self.video_only, dst)
        return info

    def prepare_filename(self, info: dict) -> str:
        return str(self._target())


def test_url_source_renders_with_source_audio(src, tmp_path: Path) -> None:  # noqa: ANN001
    video, _, tr = src
    vonly, audio = tmp_path / "v.mp4", tmp_path / "a.m4a"
    ffmpeg("-i", str(video), "-an", "-c:v", "copy", str(vonly))
    ffmpeg("-i", str(video), "-vn", "-c:a", "copy", str(audio))
    rights = {"rights_type": "own", "owner_name": "Umka", "proof_note": "own footage"}
    prep = ingest_url(
        "https://example.org/watch?v=abc123",
        rights,
        out_root=tmp_path / "sources",
        ydl_factory=DiskYDL(vonly, audio, tr.duration),
        min_mem_mb=0,
    )
    prep = PreparedSource.load(prep.dir)
    cs = ClipSource.from_prepared(prep)
    assert cs.render_path.name.startswith("original_video")
    assert cs.audio_path is not None and cs.has_audio
    res = produce_clip(
        cs,
        tr,
        start=tr.sentences[0].s - 0.2,
        end=tr.sentences[-1].e + 0.3,
        workdir=tmp_path / "w",
        author_script=SCRIPT,
        operator_edited=True,
        config=ClipJobConfig(tts="mock", detect_faces=False),
    )
    assert res.final_path is not None and res.final_path.is_file()
    # звук исходника измерен и не тишина (раньше: видео без звука → источник молчал)
    assert res.mix["levels"].get("src") is not None and res.mix["levels"]["src"] > -40.0
    assert res.qc is not None and res.qc.passed, res.qc.failed_checks
