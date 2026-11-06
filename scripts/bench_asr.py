#!/usr/bin/env python3
"""ASR benchmark: RTFx, peak RSS, WER and word-timing error (docs/ARCHITECTURE.md §10.2 M5 "Гейт ASR").

    # 1) build the 10-minute Russian sample with known text (edge-tts, word timings from WordBoundary)
    python scripts/bench_asr.py --make-sample    # -> var/bench/10min_ru.{wav,txt,words.json}
    # 2) run the gate
    python scripts/bench_asr.py --sample var/bench/10min_ru.wav --provider gigaam_v3_onnx --threads 2

Gate (M5): RTFx >= 5 and peak RSS <= 800 MB. RSS is `ru_maxrss` of this process (the same number
`/usr/bin/time -v` prints as "Maximum resident set size"), so it includes Python, numpy, onnxruntime
and the model. RTFx = audio seconds / transcription wall seconds (model load reported separately).

WER: word-level Levenshtein on normalized text (lower case, ё -> е, punctuation dropped). If
`<sample>.words.json` (edge-tts WordBoundary truth) exists, the mean absolute error of word starts and
ends over matched words is reported too — this is what `providers/asr/gigaam_onnx.WORD_TIMING` is
calibrated on. edge-tts offsets are early by the MP3 priming, so the truth is shifted by the delay
measured from audio onsets (`timing.truth_offset`; `timing_raw` is the unshifted comparison).

Shared-box rules: waits for MemAvailable >= 1500 MB (up to 10 min), runs itself with nice 10,
ORT intra_op = --threads, inter_op = 1, OMP/BLAS threads limited.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BENCH_DIR = ROOT / "var" / "bench"
SAMPLE_STEM = "10min_ru"
VOICES = ("ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural")
PARAGRAPH_PAUSES = (0.7, 1.6, 0.9, 2.2, 0.6, 1.3, 1.9, 0.8)

# Original text written for this benchmark (no numerals: e2e ASR writes digits, TTS reads words).
TEXT_RU = """
Доброе утро! Сегодня мы поговорим о том, как из длинного видео получаются короткие ролики, которые люди
досматривают до конца. Кажется, что достаточно просто вырезать интересный кусок. На самом деле всё
сложнее. Нужно найти момент, который понятен без контекста, начинается с сильной фразы и заканчивается
законченной мыслью. Если зритель не понял, о чём речь, в первые секунды, он пролистает дальше. Поэтому
хороший монтажёр сначала слушает запись целиком, а уже потом берёт в руки ножницы.

Представьте обычный подкаст. Два человека сидят в студии и спокойно обсуждают книги, путешествия и
работу. Большая часть разговора интересна только тем, кто слушает с самого начала. Но иногда звучит
история, которая цепляет сразу. Например, ведущий рассказывает, как опоздал на поезд и из-за этого
познакомился со своей будущей женой. Такой фрагмент можно смотреть отдельно, и именно его стоит
превращать в короткое видео.

Теперь немного о технике. Сначала звук превращают в текст. Программа распознавания речи слушает запись и
записывает каждое слово вместе со временем, когда оно прозвучало. Потом текст делят на предложения по
точкам, вопросительным и восклицательным знакам. Если пауза между словами слишком длинная, предложение
тоже заканчивается. Так получается удобная карта всего разговора, по которой легко искать нужные места.

Почему это важно? Потому что языковая модель плохо считает миллисекунды, зато хорошо понимает смысл. Ей
показывают пронумерованные предложения и просят выбрать отрезки, которые подходят для коротких роликов.
Модель возвращает номера первого и последнего предложения, а программа сама переводит их во время. Затем
границы аккуратно сдвигаются в тишину, чтобы ролик не начинался на полуслове.

Однако простой нарезки мало. Площадки всё строже относятся к повторному использованию чужого контента.
Если вы просто перезалили кусок чужого эфира, алгоритм вряд ли станет его продвигать. Зрителю нужен ваш
вклад: комментарий, объяснение, собственная история или вопрос, который заставляет задуматься. Поэтому в
каждом ролике должен звучать голос автора, а на экране появляться его собственный текст.

Хорошая структура выглядит так. В самом начале автор своим голосом задаёт интригу, пока исходное видео
звучит тише. Затем идёт главный фрагмент. В середине можно остановить кадр, слегка приблизить его и
объяснить, что здесь происходит на самом деле. В конце автор задаёт вопрос зрителям или предлагает
досмотреть ролик ещё раз, потому что финал связан с началом.

Давайте отвлечёмся и поговорим о погоде. Осень в этом году выдалась тёплой и сухой. Листья долго
держались на деревьях, а по утрам над рекой стоял густой туман. Рыбаки говорят, что в такую погоду клюёт
особенно хорошо. Вечерами в парках много людей, дети катаются на велосипедах, а пожилые пары неспешно
гуляют по аллеям и кормят уток.

А вы любите готовить? Настоящий борщ требует терпения. Сначала варят бульон на говяжьей косточке, потом
отдельно тушат свёклу с морковью и луком. Капусту добавляют ближе к концу, чтобы она осталась хрустящей.
Некоторые кладут немного фасоли или чернослива, и каждая хозяйка уверена, что именно её рецепт
правильный. Подавать борщ лучше со сметаной, чесноком и свежим чёрным хлебом.

Теперь о звёздах. В ясную ночь за городом можно увидеть Млечный Путь, светлую полосу, которая тянется
через всё небо. Это миллиарды звёзд нашей галактики, настолько далёких, что они сливаются в одно сияние.
Если взять обычный бинокль, в созвездии Ориона можно разглядеть туманность, где прямо сейчас рождаются
новые звёзды. Удивительно, что свет от неё летел к нам больше тысячи лет.

Расскажу короткую историю про собаку. Наш сосед завёл щенка, рыжего и очень шумного. Первую неделю он
лаял на всё подряд: на пылесос, на голубей и даже на собственное отражение в луже. Потом хозяин начал
каждый день гулять с ним в парке и учить простым командам. Через месяц щенок спокойно сидел у подъезда и
терпеливо ждал, пока ему откроют дверь.

Многие боятся выступать перед публикой. Руки дрожат, голос садится, а все заготовленные слова куда-то
исчезают. Есть несколько простых приёмов. Во-первых, репетируйте вслух, а не про себя. Во-вторых,
начинайте с истории или вопроса, а не с сухого приветствия. В-третьих, найдите в зале несколько
доброжелательных лиц и обращайтесь к ним. И помните, что слушатели хотят, чтобы у вас всё получилось.

Байкал зимой похож на другую планету. Лёд такой прозрачный, что сквозь него видно камни на дне. Он
трескается с громким гулом, и кажется, будто под ногами кто-то стреляет из пушки. Туристы приезжают,
чтобы прокатиться на коньках по бесконечному зеркалу и сфотографировать пузыри метана, застывшие в толще
льда. Главное правило простое: не выходить на лёд без местного проводника.

Программисты часто шутят, что самая сложная задача в их работе не написать код, а придумать хорошее
название для переменной. В этой шутке много правды. Понятное название экономит часы тем, кто будет
читать программу потом. Хороший код похож на хорошую прозу: в нём нет лишних слов, а каждая строчка на
своём месте. И конечно, его обязательно проверяют тестами.

Сон влияет на нас сильнее, чем кажется. Если человек несколько ночей подряд спит мало, у него хуже
работает память, портится настроение и растёт аппетит. Врачи советуют ложиться примерно в одно и то же
время, не смотреть в телефон перед сном и проветривать спальню. Даже короткая прогулка вечером помогает
быстрее заснуть и лучше выспаться.

Шахматы учат думать на несколько ходов вперёд. Новички обычно смотрят только на свои фигуры, а опытные
игроки сначала спрашивают себя, что задумал соперник. Интересно, что многие великие партии были
проиграны не из-за плохой стратегии, а из-за одной случайной ошибки в цейтноте. Поэтому тренеры советуют
не только решать задачи, но и учиться спокойно распределять время.

Поговорим о путешествиях на поезде. Многие считают, что это слишком долго, и выбирают самолёт. Но в
поезде есть своя магия. За окном меняются леса, поля и маленькие станции, где продают горячие пирожки и
солёные огурцы. Попутчики рассказывают удивительные истории, а чай в стеклянном стакане с подстаканником
кажется вкуснее, чем дома. Иногда ради такой дороги стоит потратить лишний день.

Теперь несколько слов о саде. Весной главное не торопиться с посадкой. Земля должна прогреться, иначе
семена просто сгниют. Томаты любят солнце и редкий, но обильный полив. Огурцам нужна влага и защита от
холодного ветра. А вот укроп и петрушка растут почти сами по себе. Опытные садоводы ведут дневник, куда
записывают, что и где посадили, чтобы на следующий год не повторять ошибок.

Отдельная тема — музыка в коротких роликах. Популярный трек может помочь видео набрать просмотры, но
может и привести к блокировке звука, если права на него принадлежат крупному лейблу. Безопаснее
использовать собственную музыку или библиотеки с понятной лицензией. И не забывайте, что голос автора
всегда должен быть громче фоновой мелодии, иначе зритель просто не разберёт слов.

Как понять, что ролик получился? Смотрите не только на просмотры, но и на удержание. Если половина
зрителей уходит в первые три секунды, значит, начало слабое. Если люди досматривают, но не переходят по
ссылке, возможно, призыв к действию звучит неубедительно. Каждый ролик — это маленький эксперимент.
Меняйте одну вещь за раз, записывайте результаты и через месяц вы начнёте видеть закономерности.

Немного истории. Бумагу придумали в Китае очень давно, и долгое время секрет её изготовления тщательно
охраняли. Мастера размачивали кору шелковицы, старые сети и тряпки, превращали всё это в кашицу и тонким
слоем выкладывали на сито. Когда масса высыхала, получался лёгкий и прочный лист. Постепенно ремесло
распространилось по всему миру, и без него трудно представить современную культуру.

И последнее наблюдение. Людям нравится чувствовать, что с ними разговаривает живой человек, а не машина.
Поэтому не бойтесь оговорок, пауз и собственной интонации. Расскажите, почему вам самим интересна эта
тема, что удивило вас в разговоре и с чем вы не согласны. Именно такой личный взгляд отличает авторский
канал от бесконечной ленты одинаковых нарезок.

Напоследок маленькая басня. Жил-был муравей, который всё лето таскал зёрна в муравейник. Рядом прыгала
стрекоза и смеялась над ним. Зачем работать, если солнце светит и цветы пахнут? Осенью пошли дожди, и
стрекоза постучалась к муравью. Муравей не стал её упрекать. Он просто налил ей чаю и сказал: давай
весной работать вместе. Вот и всё на сегодня. Спасибо, что досмотрели до конца!
"""


# ---------------------------------------------------------------------- text utils

_TOKEN = re.compile(r"\w+", re.UNICODE)


def norm_words(text: str) -> list[str]:
    return _TOKEN.findall(text.lower().replace("ё", "е"))


def wer(ref: list[str], hyp: list[str]) -> tuple[float, int, int, int]:
    """(WER, substitutions, deletions, insertions) by word-level Levenshtein."""
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    ops_prev = [(0, 0, j) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        ops_cur = [(0, i, 0)] + [(0, 0, 0)] * m
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                cur[j], ops_cur[j] = prev[j - 1], ops_prev[j - 1]
                continue
            sub, dele, ins = prev[j - 1] + 1, prev[j] + 1, cur[j - 1] + 1
            best = min(sub, dele, ins)
            cur[j] = best
            if best == sub:
                s, d, a = ops_prev[j - 1]
                ops_cur[j] = (s + 1, d, a)
            elif best == dele:
                s, d, a = ops_prev[j]
                ops_cur[j] = (s, d + 1, a)
            else:
                s, d, a = ops_cur[j - 1]
                ops_cur[j] = (s, d, a + 1)
        prev, ops_prev = cur, ops_cur
    s, d, a = ops_prev[m]
    return (prev[m] / max(n, 1), s, d, a)


def timing_errors(truth: list[dict], words: list) -> dict:
    """Mean abs / mean signed error of starts and ends over words matched by normalized text."""
    t_norm = [" ".join(norm_words(t["w"])) for t in truth]
    h_norm = [" ".join(norm_words(w.w)) for w in words]
    sm = difflib.SequenceMatcher(a=t_norm, b=h_norm, autojunk=False)
    ds, de = [], []
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            t, h = truth[blk.a + k], words[blk.b + k]
            ds.append(h.s - t["s"])
            de.append(h.e - t["e"])
    if not ds:
        return {"matched": 0}

    def mae(x: list[float]) -> float:
        return round(sum(abs(v) for v in x) / len(x), 3)

    def bias(x: list[float]) -> float:
        return round(sum(x) / len(x), 3)

    ds_sorted = sorted(abs(v) for v in ds)
    return {
        "matched": len(ds),
        "start_mae": mae(ds),
        "start_bias": bias(ds),
        "start_p90": round(ds_sorted[int(0.9 * (len(ds_sorted) - 1))], 3),
        "end_mae": mae(de),
        "end_bias": bias(de),
    }


def measure_truth_offset(wav: Path, truth: list[dict], min_gap: float = 0.5) -> float | None:
    """Median delay between edge-tts WordBoundary starts and the real energy onset in the WAV.

    edge-tts offsets ignore the MP3 priming/leading silence, so the audible word starts ~0.13-0.16 s later
    than the offsets say. Measured on words that follow a pause (clean onsets), 10%-of-peak threshold.
    """
    import numpy as np
    import soundfile as sf

    x, sr = sf.read(str(wav), dtype="float32")
    firsts = [truth[0]] + [
        truth[i] for i in range(1, len(truth)) if truth[i]["s"] - truth[i - 1]["e"] > min_gap
    ]
    frame = int(0.005 * sr)
    delays = []
    for t in firsts:
        a, b = int(max(0.0, t["s"] - 0.8) * sr), int((t["s"] + 0.8) * sr)
        seg = x[a:b]
        n = (len(seg) // frame) * frame
        if n == 0:
            continue
        env = np.abs(seg[:n]).reshape(-1, frame).max(axis=1)
        thr = max(0.02, float(env.max()) * 0.1)
        if not (env > thr).any():
            continue
        delays.append(float(np.argmax(env > thr)) * 0.005 + a / sr - t["s"])
    return round(float(np.median(delays)), 3) if delays else None


# ---------------------------------------------------------------------- sample


async def _tts_paragraph(text: str, voice: str, mp3: Path) -> list[dict]:
    import edge_tts

    words: list[dict] = []
    comm = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    with mp3.open("wb") as fh:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                s = chunk["offset"] / 1e7
                words.append({"w": chunk["text"], "s": s, "e": s + chunk["duration"] / 1e7})
    return words


def make_sample(out_dir: Path, minutes: float = 10.0) -> dict:
    """Synthesize TEXT_RU paragraph by paragraph (alternating voices) into one 16 kHz mono WAV."""
    import numpy as np
    import soundfile as sf

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "tmp_tts"
    tmp.mkdir(exist_ok=True)
    paragraphs = [" ".join(p.split()) for p in TEXT_RU.strip().split("\n\n") if p.strip()]
    sr = 16_000
    pieces: list[np.ndarray] = []
    truth: list[dict] = []
    ref_parts: list[str] = []
    t = 0.3
    pieces.append(np.zeros(int(0.3 * sr), dtype=np.float32))
    target = minutes * 60.0
    rounds = 0
    while t < target and rounds < 3:
        for i, para in enumerate(paragraphs):
            if t >= target:
                break
            voice = VOICES[(i + rounds) % len(VOICES)]
            mp3 = tmp / f"p{rounds}_{i:02d}.mp3"
            wav = tmp / f"p{rounds}_{i:02d}.wav"
            for attempt in range(8):  # edge-tts drops requests at random (NoAudioReceived)
                try:
                    words = asyncio.run(_tts_paragraph(para, voice, mp3))
                    if words:
                        break
                except Exception as exc:  # noqa: BLE001 - edge-tts transport errors are flaky
                    print(f"  tts retry {attempt + 1}: {exc}", file=sys.stderr)
                time.sleep(min(3.0 * (attempt + 1), 15.0))
            else:
                raise RuntimeError(f"edge-tts failed for paragraph {i}")
            subprocess.run(
                ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3),
                 "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(wav)],
                check=True,
            )  # fmt: skip
            audio, _ = sf.read(str(wav), dtype="float32")
            for w in words:
                truth.append({"w": w["w"], "s": round(t + w["s"], 3), "e": round(t + w["e"], 3)})
            ref_parts.append(para)
            pieces.append(audio)
            t += len(audio) / sr
            pause = PARAGRAPH_PAUSES[(i + rounds) % len(PARAGRAPH_PAUSES)]
            pieces.append(np.zeros(int(pause * sr), dtype=np.float32))
            t += pause
            print(f"  paragraph {len(ref_parts):2d} {voice:22s} total {t:6.1f}s", flush=True)
        rounds += 1
    full = np.concatenate(pieces)
    wav_out = out_dir / f"{SAMPLE_STEM}.wav"
    sf.write(str(wav_out), full, sr, subtype="PCM_16")
    (out_dir / f"{SAMPLE_STEM}.txt").write_text("\n\n".join(ref_parts) + "\n", encoding="utf-8")
    (out_dir / f"{SAMPLE_STEM}.words.json").write_text(
        json.dumps(truth, ensure_ascii=False), encoding="utf-8"
    )
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()
    info = {
        "wav": str(wav_out),
        "duration": round(len(full) / sr, 2),
        "words": len(truth),
        "paragraphs": len(ref_parts),
    }
    print(json.dumps(info, ensure_ascii=False))
    return info


# ---------------------------------------------------------------------- bench


def rss_now_mb() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # kB on Linux


def run_bench(args: argparse.Namespace) -> dict:
    from autoshorts.clipping.source_ingest import wait_for_memory
    from autoshorts.providers.asr import get_asr_provider
    from autoshorts.providers.asr.base import wav_info

    sample = Path(args.sample)
    ref_path = Path(args.reference) if args.reference else sample.with_suffix(".txt")
    truth_path = sample.with_suffix(".words.json")
    info = wav_info(sample)
    avail = wait_for_memory(args.min_mem_mb, timeout_sec=600, log=lambda m: print(m, file=sys.stderr))
    rss0 = rss_now_mb()

    kwargs: dict = {}
    if args.provider in ("gigaam_v3_onnx", "gigaam"):
        kwargs = {"threads": args.threads, "batch_size": args.batch_size}
        if args.tmp_dir:
            kwargs["tmp_dir"] = args.tmp_dir
    t0 = time.monotonic()
    provider = get_asr_provider(args.provider, **kwargs)
    if hasattr(provider, "_load"):
        provider._load()  # noqa: SLF001 - measure model load separately from transcription
    load_sec = time.monotonic() - t0
    rss_loaded = rss_now_mb()

    last = [0.0]

    def progress(f: float) -> None:
        if f - last[0] >= 0.1 or f >= 1.0:
            last[0] = f
            print(f"  {f * 100:5.1f}%  rss={rss_now_mb():.0f} MB", file=sys.stderr, flush=True)

    t1 = time.monotonic()
    tr = provider.transcribe(sample, "ru", progress=progress)
    wall = time.monotonic() - t1
    duration = info.duration if info else tr.duration
    peak = peak_rss_mb()

    result: dict = {
        "provider": tr.provider,
        "model": tr.model,
        "sample": str(sample),
        "audio_sec": round(duration, 2),
        "threads": args.threads,
        "mem_available_mb_before": avail,
        "load_sec": round(load_sec, 2),
        "transcribe_sec": round(wall, 2),
        "rtfx": round(duration / wall, 2) if wall else None,
        "rtfx_incl_load": round(duration / (wall + load_sec), 2),
        "rss_start_mb": round(rss0, 1),
        "rss_after_load_mb": round(rss_loaded, 1),
        "peak_rss_mb": round(peak, 1),
        "words": len(tr.words),
        "sentences": len(tr.sentences),
        "vad_segments": len(tr.vad),
        "cost_usd": tr.cost_usd,
    }
    if ref_path.is_file():
        ref = norm_words(ref_path.read_text(encoding="utf-8"))
        hyp = norm_words(" ".join(w.w for w in tr.words))
        w, s, d, a = wer(ref, hyp)
        result.update({"wer": round(w, 4), "wer_sub": s, "wer_del": d, "wer_ins": a, "ref_words": len(ref)})
    if truth_path.is_file():
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        result["timing_raw"] = timing_errors(truth, tr.words)
        off = args.truth_offset if args.truth_offset is not None else measure_truth_offset(sample, truth)
        if off is not None:
            shifted = [{"w": t["w"], "s": t["s"] + off, "e": t["e"] + off} for t in truth]
            result["timing"] = {"truth_offset": off, **timing_errors(shifted, tr.words)}
    gate = {"rtfx_min": 5.0, "peak_rss_max_mb": 800.0}
    result["gate"] = gate
    result["gate_pass"] = bool(
        result["rtfx"] and result["rtfx"] >= gate["rtfx_min"] and peak <= gate["peak_rss_max_mb"]
    )
    if args.save_transcript:
        tr.save(args.save_transcript)
        result["transcript"] = str(args.save_transcript)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--make-sample", action="store_true", help="synthesize var/bench/10min_ru.wav + reference"
    )
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--out-dir", type=Path, default=BENCH_DIR)
    ap.add_argument("--sample", type=Path, default=BENCH_DIR / f"{SAMPLE_STEM}.wav")
    ap.add_argument("--reference", type=Path, default=None, help="default: <sample>.txt")
    ap.add_argument("--provider", default="gigaam_v3_onnx")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--min-mem-mb", type=int, default=1500)
    ap.add_argument("--tmp-dir", type=Path, default=ROOT / "var" / "tmp")
    ap.add_argument("--save-transcript", type=Path, default=None)
    ap.add_argument(
        "--truth-offset", type=float, default=None, help="shift of edge-tts word times (default: measured)"
    )
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args(argv)

    try:
        os.nice(10)
    except OSError:
        pass
    os.environ.setdefault("OMP_NUM_THREADS", str(args.threads))
    for var in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
        os.environ.setdefault(var, "1")

    if args.make_sample:
        make_sample(args.out_dir, args.minutes)
        return 0
    result = run_bench(args)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0 if result["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
