from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .constants import ORDER_FILENAME


@dataclass(frozen=True)
class StoredOrder:
    files: list[str]


def order_file_path(folder: str) -> str:
    return os.path.join(folder, ORDER_FILENAME)


def load_order(folder: str) -> StoredOrder | None:
    path = order_file_path(folder)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        files = data.get("files")
        if not isinstance(files, list) or not all(isinstance(x, str) for x in files):
            return None
        return StoredOrder(files=list(files))
    except Exception:
        return None


def save_order(folder: str, files: Iterable[str]) -> None:
    path = order_file_path(folder)
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "files": list(files),
    }
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)


