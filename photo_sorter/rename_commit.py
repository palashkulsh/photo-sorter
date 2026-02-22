from __future__ import annotations

import os
import time
from dataclasses import dataclass


def sanitize_prefix(prefix: str) -> str:
    prefix = prefix.strip()
    # Keep simple + safe: disallow path separators.
    prefix = prefix.replace(os.sep, "_")
    if os.altsep:
        prefix = prefix.replace(os.altsep, "_")
    return prefix


@dataclass(frozen=True)
class RenamePlan:
    folder: str
    old_to_new: list[tuple[str, str]]  # basenames


@dataclass(frozen=True)
class Collision:
    src: str
    dst: str


def build_rename_plan(folder: str, ordered_files: list[str], prefix: str) -> RenamePlan:
    prefix = sanitize_prefix(prefix)
    pairs: list[tuple[str, str]] = []
    for i, old in enumerate(ordered_files):
        _, ext = os.path.splitext(old)
        # Keep extension exactly as-is (case included). This is still "rename only"
        # without any content/metadata changes.
        new = f"{prefix}{i:05d}{ext}"
        pairs.append((old, new))
    return RenamePlan(folder=folder, old_to_new=pairs)


def find_collisions(plan: RenamePlan) -> list[Collision]:
    collisions: list[Collision] = []
    existing = set(os.listdir(plan.folder))
    for old, new in plan.old_to_new:
        if new in existing and new != old:
            collisions.append(Collision(src=old, dst=new))
    return collisions


def execute_rename_plan(
    plan: RenamePlan,
    *,
    progress_cb: callable | None = None,
) -> None:
    """
    Two-phase rename:
    1) old -> temp (unique hidden names)
    2) temp -> final
    """
    folder = plan.folder
    ts = int(time.time() * 1000)
    tmp_pairs: list[tuple[str, str, str]] = []  # (old, tmp, final)

    # Phase 0: compute temp names that don't exist
    existing = set(os.listdir(folder))
    for i, (old, final) in enumerate(plan.old_to_new):
        _, ext = os.path.splitext(final)
        tmp = f".__photo_sorter_tmp__{ts}_{i:05d}{ext}"
        while tmp in existing:
            ts += 1
            tmp = f".__photo_sorter_tmp__{ts}_{i:05d}{ext}"
        existing.add(tmp)
        tmp_pairs.append((old, tmp, final))

    # Phase 1: old -> tmp
    for idx, (old, tmp, _final) in enumerate(tmp_pairs):
        os.rename(os.path.join(folder, old), os.path.join(folder, tmp))
        if progress_cb:
            progress_cb(idx + 1, len(tmp_pairs) * 2)

    # Phase 2: tmp -> final
    for idx, (_old, tmp, final) in enumerate(tmp_pairs):
        os.rename(os.path.join(folder, tmp), os.path.join(folder, final))
        if progress_cb:
            progress_cb(len(tmp_pairs) + idx + 1, len(tmp_pairs) * 2)


