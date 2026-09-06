"""Кого бот любит, а кого нет.

Списки правит админ командами, поэтому жить в памяти они не могут — переживут
перезапуск в `people.json` рядом с ботом. Файл читается один раз при импорте и
переписывается на каждую правку: записей тут единицы, экономить не на чем.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("advicebot")

CRUEL = "cruel"      # злые ответы, злые стикеры и иногда отказ
LOVED = "loved"      # ласковые ответы и стикеры
SWEET = "sweet"      # то же, но совсем уж приторно
NEUTRAL = "neutral"  # обычное отношение, даже если оно прошито в коде

MOODS = (CRUEL, LOVED, SWEET, NEUTRAL)

PATH = Path(__file__).parent / "people.json"


def _read() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("не разобрал %s, начинаю со списков с нуля", PATH.name)
        return {}
    return data if isinstance(data, dict) else {}


_people = _read()


def _write() -> None:
    try:
        PATH.write_text(json.dumps(_people, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        log.exception("не смог сохранить %s", PATH.name)


def mood(user_id: int) -> str | None:
    """Что записано про человека; NEUTRAL — это тоже запись, а не пустота."""
    entry = _people.get(str(user_id))
    return entry.get("mood") if entry else None


def name(user_id: int) -> str:
    entry = _people.get(str(user_id))
    return (entry or {}).get("name", "")


def remember(user_id: int, new_mood: str, title: str = "") -> None:
    """Записывает отношение; имя — просто подпись, чтобы список читался."""
    entry = _people.setdefault(str(user_id), {})
    entry["mood"] = new_mood
    if title:
        entry["name"] = title
    _write()


def listing() -> dict[str, list[tuple[int, str]]]:
    """Списки по настроениям — для отчёта админу."""
    out: dict[str, list[tuple[int, str]]] = {m: [] for m in MOODS}
    for key, entry in _people.items():
        if entry.get("mood") in out:
            out[entry["mood"]].append((int(key), entry.get("name", "")))
    return out
