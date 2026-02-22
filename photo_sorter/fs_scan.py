from __future__ import annotations

import os
from dataclasses import dataclass

from .constants import IMAGE_EXTS


@dataclass(frozen=True)
class FolderScan:
    folder: str
    image_files: list[str]  # basenames only


def scan_folder_for_images(folder: str) -> FolderScan:
    entries: list[str] = []
    with os.scandir(folder) as it:
        for de in it:
            if not de.is_file():
                continue
            _, ext = os.path.splitext(de.name)
            if ext.lower() in IMAGE_EXTS:
                entries.append(de.name)
    return FolderScan(folder=folder, image_files=entries)


