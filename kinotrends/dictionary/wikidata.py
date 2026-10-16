"""Сборка словаря названий из Wikidata.

Два этапа, оба возобновляемые:
  fetch   — список сущностей через SPARQL (без меток, быстро), затем карточки через wbgetentities
            по 50 штук: метки ru/en, псевдонимы, оригинальное название, год, жанры, страны, IMDb,
            Кинопоиск, франшиза. Результат копится в var/dict/entities.jsonl.
  labels  — русские названия для жанров, стран и франшиз (var/dict/labels.json).
Импорт в SQLite делает kinotrends.dictionary.importer, он может запускаться когда угодно,
пока fetch ещё идёт.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from ..config import DICT_DIR, WIKIDATA_UA

SPARQL_URL = "https://query.wikidata.org/sparql"
API_URL = "https://www.wikidata.org/w/api.php"

# Класс Wikidata -> наш тип произведения.
TYPES: dict[str, str] = {
    "Q11424": "film",               # фильм
    "Q506240": "film",              # телефильм
    "Q202866": "animated",          # анимационный фильм
    "Q29168811": "animated",        # полнометражный мультфильм
    "Q20650540": "anime",           # аниме-фильм
    "Q5398426": "series",           # телесериал
    "Q1259759": "series",           # мини-сериал
    "Q117467246": "animated_series",# анимационный сериал
    "Q63952888": "anime",           # аниме-сериал
    "Q15416": "tv_show",            # телепрограмма
    "Q24856": "franchise",          # киносерия
}

ITEMS_PATH = DICT_DIR / "items.json"
ENTITIES_PATH = DICT_DIR / "entities.jsonl"
LABELS_PATH = DICT_DIR / "labels.json"
MIN_SITELINKS = 3


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def sparql(query: str, timeout: float = 170.0) -> list[dict]:
    with httpx.Client(timeout=timeout, headers={"User-Agent": WIKIDATA_UA}) as c:
        for attempt in range(4):
            r = c.get(SPARQL_URL, params={"format": "json", "query": query})
            if r.status_code == 200:
                return r.json()["results"]["bindings"]
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
    raise RuntimeError("SPARQL: исчерпаны попытки")


def list_items() -> list[dict]:
    """Список (qid, type, sitelinks, kp) для всех классов. Диапазоны по sitelinks, чтобы не упираться в таймаут."""
    if ITEMS_PATH.exists():
        items = json.loads(ITEMS_PATH.read_text())
        log(f"items.json уже есть: {len(items)} записей")
        return items
    seen: dict[str, dict] = {}
    ranges = [(50, None), (20, 49), (10, 19), (6, 9), (4, 5), (MIN_SITELINKS, 3)]
    for qtype, kind in TYPES.items():
        for lo, hi in ranges:
            cond = f"FILTER(?sl >= {lo})" if hi is None else f"FILTER(?sl >= {lo} && ?sl <= {hi})"
            q = (
                "SELECT ?item ?sl (SAMPLE(?kp) AS ?kp1) WHERE { "
                f"?item wdt:P31 wd:{qtype}; wdt:P2603 ?kp; wikibase:sitelinks ?sl. {cond} }} "
                "GROUP BY ?item ?sl"
            )
            try:
                rows = sparql(q)
            except Exception as e:  # noqa: BLE001
                log(f"  {qtype} sl {lo}-{hi}: ошибка {e}, пробую без GROUP BY")
                q2 = f"SELECT ?item ?sl ?kp WHERE {{ ?item wdt:P31 wd:{qtype}; wdt:P2603 ?kp; wikibase:sitelinks ?sl. {cond} }}"
                rows = sparql(q2)
            n_new = 0
            for r in rows:
                qid = r["item"]["value"].rsplit("/", 1)[-1]
                if qid in seen:
                    continue
                seen[qid] = {
                    "qid": qid,
                    "type": kind,
                    "sl": int(r["sl"]["value"]),
                    "kp": (r.get("kp1") or r.get("kp") or {}).get("value"),
                }
                n_new += 1
            log(f"  {qtype} ({kind}) sl {lo}-{hi if hi else '∞'}: {len(rows)} строк, новых {n_new}")
            time.sleep(1.0)
    items = sorted(seen.values(), key=lambda x: -x["sl"])
    ITEMS_PATH.write_text(json.dumps(items, ensure_ascii=False))
    log(f"всего сущностей: {len(items)}")
    return items


def _time_year(v: dict) -> int | None:
    t = v.get("time") or ""
    # формат +1990-06-27T00:00:00Z, точность может быть годовой
    try:
        return int(t[1:5])
    except ValueError:
        return None


def parse_entity(qid: str, e: dict) -> dict:
    claims = e.get("claims", {})

    def ids(p: str) -> list[str]:
        out = []
        for c in claims.get(p, []):
            dv = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(dv, dict) and dv.get("id"):
                out.append(dv["id"])
        return out

    def strs(p: str) -> list[str]:
        out = []
        for c in claims.get(p, []):
            dv = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(dv, str):
                out.append(dv)
            elif isinstance(dv, dict) and dv.get("text"):
                out.append(dv["text"])
        return out

    years = []
    for c in claims.get("P577", []):
        dv = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(dv, dict):
            y = _time_year(dv)
            if y:
                years.append(y)
    labels = e.get("labels", {})
    aliases = e.get("aliases", {})
    return {
        "qid": qid,
        "label_ru": labels.get("ru", {}).get("value"),
        "label_en": labels.get("en", {}).get("value"),
        "aliases_ru": [a["value"] for a in aliases.get("ru", [])],
        "aliases_en": [a["value"] for a in aliases.get("en", [])],
        "original_title": (strs("P1476") or [None])[0],
        "year": min(years) if years else None,
        "genres": ids("P136"),
        "countries": ids("P495"),
        "imdb": (strs("P345") or [None])[0],
        "kp": (strs("P2603") or [None])[0],
        "franchise": ids("P179") + ids("P8345"),
        "types": ids("P31"),
    }


def fetch_batch(client: httpx.Client, qids: list[str]) -> dict[str, dict]:
    params = {
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "props": "labels|aliases|claims",
        "languages": "ru|en",
        "format": "json",
    }
    for attempt in range(6):
        try:
            r = client.get(API_URL, params=params)
        except httpx.HTTPError as e:
            log(f"  сеть: {e}, повтор")
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                if data["error"].get("code") == "maxlag":
                    time.sleep(5 * (attempt + 1))
                    continue
                raise RuntimeError(data["error"])
            return data.get("entities", {})
        if r.status_code == 429:
            time.sleep(20 * (attempt + 1))
            continue
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("wbgetentities: исчерпаны попытки")


def fetch_entities(items: list[dict], workers: int = 2) -> None:
    done: set[str] = set()
    if ENTITIES_PATH.exists():
        with ENTITIES_PATH.open() as f:
            for line in f:
                try:
                    done.add(json.loads(line)["qid"])
                except Exception:  # noqa: BLE001
                    pass
    todo = [it["qid"] for it in items if it["qid"] not in done]
    log(f"карточек уже есть {len(done)}, осталось {len(todo)}")
    if not todo:
        return
    batches = [todo[i:i + 50] for i in range(0, len(todo), 50)]
    headers = {"User-Agent": WIKIDATA_UA}
    t0 = time.time()
    n = 0
    with ENTITIES_PATH.open("a") as out, ThreadPoolExecutor(max_workers=workers) as ex:
        clients = [httpx.Client(timeout=60, headers=headers) for _ in range(workers)]
        futs = {}
        for i, b in enumerate(batches):
            futs[ex.submit(fetch_batch, clients[i % workers], b)] = b
        for fut in as_completed(futs):
            try:
                ents = fut.result()
            except Exception as e:  # noqa: BLE001
                log(f"  пакет не получен: {e}")
                continue
            for qid, e in ents.items():
                if "missing" in e:
                    continue
                out.write(json.dumps(parse_entity(qid, e), ensure_ascii=False) + "\n")
            out.flush()
            n += 1
            if n % 50 == 0:
                rate = n * 50 / (time.time() - t0)
                log(f"  пакетов {n}/{len(batches)}, {rate:.0f} карточек/с, осталось ~{(len(batches) - n) * 50 / max(rate, 1) / 60:.0f} мин")
        for c in clients:
            c.close()
    log("карточки собраны")


def fetch_labels() -> None:
    """Русские подписи жанров, стран и франшиз."""
    qids: set[str] = set()
    with ENTITIES_PATH.open() as f:
        for line in f:
            e = json.loads(line)
            qids.update(e.get("genres", []))
            qids.update(e.get("countries", []))
            qids.update(e.get("franchise", []))
    labels: dict[str, dict] = {}
    if LABELS_PATH.exists():
        labels = json.loads(LABELS_PATH.read_text())
    todo = sorted(q for q in qids if q not in labels)
    log(f"подписей нужно {len(qids)}, не хватает {len(todo)}")
    with httpx.Client(timeout=60, headers={"User-Agent": WIKIDATA_UA}) as c:
        for i in range(0, len(todo), 50):
            batch = todo[i:i + 50]
            for attempt in range(6):
                r = c.get(API_URL, params={"action": "wbgetentities", "ids": "|".join(batch),
                                           "props": "labels", "languages": "ru|en", "format": "json"})
                if r.status_code == 200:
                    break
                time.sleep(15 * (attempt + 1))
            else:
                log(f"подписи: {r.status_code}, прерываю на этом пакете")
                break
            for qid, e in r.json().get("entities", {}).items():
                lb = e.get("labels", {})
                labels[qid] = {"ru": lb.get("ru", {}).get("value"), "en": lb.get("en", {}).get("value")}
            if (i // 50) % 20 == 19:
                LABELS_PATH.write_text(json.dumps(labels, ensure_ascii=False, indent=0))
            time.sleep(0.5)
    LABELS_PATH.write_text(json.dumps(labels, ensure_ascii=False, indent=0))
    log(f"подписей сохранено {len(labels)}")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "fetch"
    workers = int(argv[2]) if len(argv) > 2 else 2
    if cmd == "fetch":
        items = list_items()
        fetch_entities(items, workers=workers)
        fetch_labels()
    elif cmd == "labels":
        fetch_labels()
    else:
        print("использование: python -m kinotrends.dictionary.wikidata [fetch|labels]")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
