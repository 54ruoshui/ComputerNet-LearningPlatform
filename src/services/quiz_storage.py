"""
JSON file-based storage for quiz progress and query history.
Thread-safe via file locking.
"""

import json
import os
import threading
from pathlib import Path
from typing import List, Optional

_BASE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "quiz"
_WRONG_FILE = _BASE_DIR / "wrong_answers.json"
_DONE_FILE = _BASE_DIR / "done_ids.json"
_HISTORY_FILE = _BASE_DIR / "history.json"

_lock = threading.Lock()
_history_lock = threading.Lock()

MAX_HISTORY = 50


def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---- Done IDs ----

def get_done_ids() -> List[str]:
    with _lock:
        return _read_json(_DONE_FILE)


def mark_done(question_id: str) -> List[str]:
    with _lock:
        ids = _read_json(_DONE_FILE)
        if question_id not in ids:
            ids.append(question_id)
            _write_json(_DONE_FILE, ids)
    return ids


# ---- Wrong Answers ----

def get_wrong_list() -> List[dict]:
    with _lock:
        return _read_json(_WRONG_FILE)


def add_wrong(entry: dict) -> List[dict]:
    with _lock:
        lst = _read_json(_WRONG_FILE)
        idx = next((i for i, w in enumerate(lst) if w["id"] == entry["id"]), -1)
        if idx >= 0:
            lst[idx] = entry
        else:
            lst.insert(0, entry)
        _write_json(_WRONG_FILE, lst)
    return lst


def remove_wrong(question_id: str) -> List[dict]:
    with _lock:
        lst = _read_json(_WRONG_FILE)
        lst = [w for w in lst if w["id"] != question_id]
        _write_json(_WRONG_FILE, lst)
    return lst


def clear_wrong() -> None:
    with _lock:
        _write_json(_WRONG_FILE, [])


# ---- Query History ----

def get_history() -> List[dict]:
    with _history_lock:
        return _read_json(_HISTORY_FILE)


def add_history(item: dict) -> List[dict]:
    with _history_lock:
        lst = _read_json(_HISTORY_FILE)
        lst.insert(0, item)
        lst = lst[:MAX_HISTORY]
        _write_json(_HISTORY_FILE, lst)
    return lst


def clear_history() -> None:
    with _history_lock:
        _write_json(_HISTORY_FILE, [])
