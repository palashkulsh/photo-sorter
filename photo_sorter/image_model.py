from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QMimeData,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    Signal,
    QSize,
)
from PySide6.QtGui import QIcon, QImageReader, QPixmap


@dataclass(frozen=True)
class ImageEntry:
    filename: str  # basename only


class _ThumbResult(QObject):
    ready = Signal(str, QPixmap)  # filename, pixmap


class _ThumbTask(QRunnable):
    def __init__(self, folder: str, filename: str, size: QSize, sink: _ThumbResult):
        super().__init__()
        self.folder = folder
        self.filename = filename
        self.size = size
        self.sink = sink

    def run(self) -> None:
        path = os.path.join(self.folder, self.filename)
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        if self.size.width() > 0 and self.size.height() > 0:
            reader.setScaledSize(self.size)
        img = reader.read()
        if img.isNull():
            return
        pix = QPixmap.fromImage(img)
        if self.size.width() > 0 and self.size.height() > 0:
            pix = pix.scaled(
                self.size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.sink.ready.emit(self.filename, pix)


class ImageListModel(QAbstractListModel):
    orderChanged = Signal()
    orderChangedDetailed = Signal(object, object)  # old_files, new_files

    MIME_FMT = "application/x-photo-sorter-rows"

    def __init__(self) -> None:
        super().__init__()
        self._folder: str | None = None
        self._files: list[str] = []

        self._icon_size = QSize(160, 160)
        self._pool = QThreadPool.globalInstance()
        self._thumb_sink = _ThumbResult()
        self._thumb_sink.ready.connect(self._on_thumb_ready)
        self._thumb_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._thumb_cache_limit = 600
        self._thumb_pending: set[str] = set()

        self._placeholder = QIcon()

    def folder(self) -> str | None:
        return self._folder

    def files(self) -> list[str]:
        return list(self._files)

    def set_icon_size(self, size: QSize) -> None:
        self._icon_size = size
        self._thumb_cache.clear()
        self._thumb_pending.clear()
        if self.rowCount() > 0:
            top_left = self.index(0, 0)
            bottom_right = self.index(self.rowCount() - 1, 0)
            self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DecorationRole])

    def set_folder_and_files(self, folder: str, files_in_order: list[str]) -> None:
        self.beginResetModel()
        self._folder = folder
        self._files = list(files_in_order)
        self._thumb_cache.clear()
        self._thumb_pending.clear()
        self.endResetModel()

    def set_files_in_order(self, files_in_order: list[str]) -> None:
        """Replace the current order (folder remains unchanged)."""
        self.beginResetModel()
        self._files = list(files_in_order)
        self.endResetModel()

    # --- Qt model ---
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._files)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._files):
            return None
        filename = self._files[row]

        if role == Qt.ItemDataRole.DisplayRole:
            return filename

        if role == Qt.ItemDataRole.SizeHintRole:
            # Ensure a stable item rect even before thumbnails are ready.
            # Without this, Qt may compute a tiny height from an "empty" icon,
            # and then the real thumbnail gets clipped into horizontal strips.
            return QSize(self._icon_size.width() + 40, self._icon_size.height() + 50)

        if role == Qt.ItemDataRole.DecorationRole:
            pix = self._thumb_cache.get(filename)
            if pix is not None:
                # refresh LRU
                self._thumb_cache.move_to_end(filename)
                return QIcon(pix)
            self._schedule_thumb(filename)
            return self._placeholder

        if role == Qt.ItemDataRole.ToolTipRole:
            return filename

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not index.isValid():
            return base | Qt.ItemFlag.ItemIsDropEnabled
        return base | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled

    def mimeTypes(self) -> list[str]:  # noqa: N802
        return [self.MIME_FMT]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:  # noqa: N802
        rows = sorted({i.row() for i in indexes if i.isValid()})
        md = QMimeData()
        md.setData(self.MIME_FMT, QByteArray(",".join(map(str, rows)).encode("utf-8")))
        return md

    def supportedDropActions(self) -> Qt.DropActions:  # noqa: N802
        return Qt.DropAction.MoveAction

    def dropMimeData(  # noqa: N802
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex,
    ) -> bool:
        if action == Qt.DropAction.IgnoreAction:
            return True
        if action != Qt.DropAction.MoveAction:
            return False
        if not data.hasFormat(self.MIME_FMT):
            return False

        if self._folder is None:
            return False

        raw = bytes(data.data(self.MIME_FMT)).decode("utf-8").strip()
        if not raw:
            return False

        try:
            src_rows = sorted({int(x) for x in raw.split(",") if x != ""})
        except ValueError:
            return False
        if not src_rows:
            return False

        if row == -1:
            row = parent.row() if parent.isValid() else self.rowCount()

        row = max(0, min(row, self.rowCount()))

        # Compute target row after removing sources before it.
        adjust = sum(1 for r in src_rows if r < row)
        target_row = row - adjust

        # Extract + reinsert.
        moving = [self._files[r] for r in src_rows if 0 <= r < len(self._files)]
        if not moving:
            return False

        old_order = list(self._files)
        self.beginResetModel()
        for r in reversed(src_rows):
            if 0 <= r < len(self._files):
                del self._files[r]
        for i, f in enumerate(moving):
            self._files.insert(target_row + i, f)
        self.endResetModel()
        new_order = list(self._files)
        self.orderChanged.emit()
        self.orderChangedDetailed.emit(old_order, new_order)
        return True

    # --- thumbs ---
    def _schedule_thumb(self, filename: str) -> None:
        if self._folder is None:
            return
        if filename in self._thumb_pending:
            return
        self._thumb_pending.add(filename)
        task = _ThumbTask(self._folder, filename, self._icon_size, self._thumb_sink)
        self._pool.start(task)

    def _on_thumb_ready(self, filename: str, pixmap: QPixmap) -> None:
        self._thumb_pending.discard(filename)
        self._thumb_cache[filename] = pixmap
        self._thumb_cache.move_to_end(filename)
        while len(self._thumb_cache) > self._thumb_cache_limit:
            self._thumb_cache.popitem(last=False)

        try:
            row = self._files.index(filename)
        except ValueError:
            return
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])


