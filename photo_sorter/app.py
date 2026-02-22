from __future__ import annotations

import argparse
import os
import sys
from typing import cast

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QAction, QImageReader, QKeySequence, QPixmap, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressDialog,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QDragMoveEvent
from PySide6.QtWidgets import QAbstractItemView

from .fs_scan import scan_folder_for_images
from .image_model import ImageListModel
from .order_store import load_order, save_order
from .rename_commit import build_rename_plan, find_collisions, execute_rename_plan


def _build_initial_order(folder: str, scanned: list[str]) -> list[str]:
    stored = load_order(folder)
    scanned_set = set(scanned)
    if stored is None:
        return sorted(scanned)

    ordered: list[str] = []
    for f in stored.files:
        if f in scanned_set:
            ordered.append(f)
            scanned_set.remove(f)

    # append new files (not seen before) in name order
    ordered.extend(sorted(scanned_set))
    return ordered


class _ReorderCommand(QUndoCommand):
    def __init__(self, window: "MainWindow", before: list[str], after: list[str]):
        super().__init__("Reorder")
        self._w = window
        self._before = list(before)
        self._after = list(after)
        self._first_redo = True

    def undo(self) -> None:
        self._w._apply_order(self._before, record_undo=False)

    def redo(self) -> None:
        # When pushed, redo() is called immediately. The drag/drop already applied
        # the new order, so skip the first redo to avoid duplicate churn.
        if self._first_redo:
            self._first_redo = False
            return
        self._w._apply_order(self._after, record_undo=False)


class _AutoScrollListView(QListView):
    """
    QListView internal move doesn't always auto-scroll reliably in IconMode when
    dragging near the viewport edges. This forces scrolling so you can move
    items far up/down in very large folders.
    """

    def __init__(self) -> None:
        super().__init__()
        self._drag_scroll_margin = 48
        self._drag_scroll_step = 28

        self.setAutoScroll(True)
        self.setAutoScrollMargin(self._drag_scroll_margin)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        try:
            pos = event.position().toPoint()
        except Exception:
            # fallback
            pos = event.pos()

        y = pos.y()
        h = self.viewport().height()
        sb = self.verticalScrollBar()
        if sb is not None and sb.maximum() > 0:
            if y < self._drag_scroll_margin:
                sb.setValue(sb.value() - self._drag_scroll_step)
            elif y > h - self._drag_scroll_margin:
                sb.setValue(sb.value() + self._drag_scroll_step)

        super().dragMoveEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Photo Sorter")

        self._folder: str | None = None
        self._suppress_autosave = False
        self._record_undo = True

        self.undo_stack = QUndoStack(self)

        self.model = ImageListModel()
        self.model.orderChanged.connect(self._on_order_changed)
        self.model.orderChangedDetailed.connect(self._on_order_changed_detailed)

        self._make_ui()

        # debounce preview refresh during resize
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._refresh_preview)

    # --- UI ---
    def _make_ui(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        # Undo/Redo
        act_undo = self.undo_stack.createUndoAction(self, "Undo")
        act_undo.setShortcuts([QKeySequence.StandardKey.Undo])
        tb.addAction(act_undo)
        self.addAction(act_undo)  # ensure shortcuts work

        act_redo = self.undo_stack.createRedoAction(self, "Redo")
        act_redo.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Y")])
        tb.addAction(act_redo)
        self.addAction(act_redo)

        tb.addSeparator()

        act_open = QAction("Select Folder…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self.select_folder)
        tb.addAction(act_open)

        tb.addSeparator()
        tb.addWidget(QLabel("Prefix: "))
        self.prefix_edit = QLineEdit("PIC_")
        self.prefix_edit.setFixedWidth(180)
        tb.addWidget(self.prefix_edit)

        self.commit_btn = QPushButton("Commit / Rearrange (rename)")
        self.commit_btn.clicked.connect(self.commit_rename)
        tb.addWidget(self.commit_btn)

        tb.addSeparator()
        self.folder_label = QLabel("No folder selected")
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tb.addWidget(self.folder_label)

        # main split
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # left: list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.list_view = _AutoScrollListView()
        self.list_view.setModel(self.model)
        self.list_view.setViewMode(QListView.ViewMode.IconMode)
        self.list_view.setResizeMode(QListView.ResizeMode.Adjust)
        # Multi-select so you can drag a group and move/reorder them together.
        self.list_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_view.setSpacing(10)
        self.list_view.setWrapping(True)
        self.list_view.setUniformItemSizes(True)
        self.list_view.setWordWrap(True)
        self.list_view.setIconSize(QSize(160, 160))
        self._sync_thumbnail_grid_metrics()
        self.list_view.setDragEnabled(True)
        self.list_view.setAcceptDrops(True)
        self.list_view.setDropIndicatorShown(True)
        self.list_view.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_view.setDragDropMode(QListView.DragDropMode.InternalMove)
        self.list_view.setDragDropOverwriteMode(False)
        self.list_view.selectionModel().selectionChanged.connect(lambda *_: self._refresh_preview())
        left_layout.addWidget(self.list_view)

        splitter.addWidget(left)

        # right: preview
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)

        self.preview_label = QLabel("Select an image to preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Important: avoid "runaway" resizing where the QLabel's pixmap sizeHint
        # influences the layout, which then changes the label size, which triggers
        # another rescale, etc. Using Ignored prevents the pixmap from driving layout.
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.preview_label.setMinimumSize(320, 240)
        self.preview_label.setStyleSheet("QLabel { background: #111; color: #eee; border: 1px solid #333; }")
        right_layout.addWidget(self.preview_label)

        info_row = QWidget()
        info_layout = QHBoxLayout(info_row)
        info_layout.setContentsMargins(0, 0, 0, 0)
        self.info_label = QLabel("")
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(self.info_label)
        right_layout.addWidget(info_row)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.setCentralWidget(splitter)

        self.statusBar().showMessage("Ready")

    def _sync_thumbnail_grid_metrics(self) -> None:
        """Keep the QListView layout stable (critical after model resets)."""
        icon = self.list_view.iconSize()
        self.list_view.setGridSize(QSize(icon.width() + 40, icon.height() + 50))

    # --- events ---
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._preview_timer.start(120)

    # --- actions ---
    def select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder with photos")
        if not folder:
            return

        self.load_folder(folder)

    def load_folder(self, folder: str) -> None:
        if not os.path.isdir(folder):
            QMessageBox.warning(self, "Invalid folder", "That folder does not exist.")
            return

        self.undo_stack.clear()

        scan = scan_folder_for_images(folder)
        ordered = _build_initial_order(folder, scan.image_files)

        self._folder = folder
        self.folder_label.setText(folder)
        self._suppress_autosave = True
        self._record_undo = False
        try:
            self.model.set_folder_and_files(folder, ordered)
            self.model.set_icon_size(self.list_view.iconSize())
            self._sync_thumbnail_grid_metrics()
        finally:
            self._suppress_autosave = False
            self._record_undo = True

        # persist immediately so new files get recorded too
        save_order(folder, self.model.files())
        self.statusBar().showMessage(f"Loaded {len(ordered)} images")

        # After large renames + model reset, force a layout pass so icon rects
        # don’t temporarily collapse (which shows as “strip” thumbnails).
        self.list_view.doItemsLayout()

        # Make sure something is selected so preview has a stable state.
        if self.model.rowCount() > 0 and not self.list_view.currentIndex().isValid():
            self.list_view.setCurrentIndex(self.model.index(0, 0))
        self._refresh_preview()

    def _apply_order(self, order: list[str], *, record_undo: bool) -> None:
        """Apply an order programmatically (used for undo/redo)."""
        folder = self._folder
        if not folder:
            return

        # Keep selection on the same filename if possible.
        selected = self._current_selected_filename()

        self._record_undo = record_undo
        self._suppress_autosave = False
        try:
            self.model.set_files_in_order(order)
        finally:
            self._record_undo = True

        # Save order file for undo/redo too.
        save_order(folder, self.model.files())

        if selected:
            try:
                row = self.model.files().index(selected)
            except ValueError:
                row = 0
            if self.model.rowCount() > 0:
                self.list_view.setCurrentIndex(self.model.index(row, 0))
        self._refresh_preview()

    def _on_order_changed(self) -> None:
        if self._suppress_autosave:
            return
        folder = self._folder
        if not folder:
            return
        save_order(folder, self.model.files())
        self.statusBar().showMessage("Order saved")

    def _on_order_changed_detailed(self, old_order: list[str], new_order: list[str]) -> None:
        if not self._record_undo:
            return
        # Avoid pushing commands during folder load/reset.
        if self._suppress_autosave:
            return
        if old_order == new_order:
            return
        self.undo_stack.push(_ReorderCommand(self, old_order, new_order))

    def _current_selected_filename(self) -> str | None:
        sm = self.list_view.selectionModel()
        if sm is None:
            return None
        sel = sm.selectedIndexes()
        if not sel:
            return None
        idx = sel[0]
        row = idx.row()
        files = self.model.files()
        if 0 <= row < len(files):
            return files[row]
        return None

    def _refresh_preview(self) -> None:
        folder = self._folder
        filename = self._current_selected_filename()
        if not folder or not filename:
            self.preview_label.setText("Select an image to preview")
            self.preview_label.setPixmap(QPixmap())
            self.info_label.setText("")
            return

        path = os.path.join(folder, filename)
        self.info_label.setText(filename)

        # scale to label size (contents rect)
        target = self.preview_label.contentsRect().size()
        if target.width() <= 10 or target.height() <= 10:
            return

        reader = QImageReader(path)
        reader.setAutoTransform(True)
        # Read scaled-to-fit to save memory/time on large originals.
        src_size = reader.size()
        if src_size.isValid():
            scaled = src_size.scaled(target, Qt.AspectRatioMode.KeepAspectRatio)
            if scaled.isValid():
                reader.setScaledSize(scaled)

        img = reader.read()
        if img.isNull():
            self.preview_label.setText("Preview unavailable")
            self.preview_label.setPixmap(QPixmap())
            return

        # If the plugin ignored setScaledSize (some formats), do a final fit.
        pix = QPixmap.fromImage(img)
        if pix.width() > target.width() or pix.height() > target.height():
            pix = pix.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.preview_label.setPixmap(pix)
        self.preview_label.setText("")

    def commit_rename(self) -> None:
        folder = self._folder
        if not folder:
            QMessageBox.information(self, "No folder selected", "Select a folder first.")
            return

        ordered = self.model.files()
        if not ordered:
            QMessageBox.information(self, "No images", "No images found in this folder.")
            return

        prefix = self.prefix_edit.text()
        plan = build_rename_plan(folder, ordered, prefix)
        collisions = find_collisions(plan)
        if collisions:
            sample = "\n".join(f"- {c.dst} (from {c.src})" for c in collisions[:15])
            more = "" if len(collisions) <= 15 else f"\n... and {len(collisions) - 15} more"
            QMessageBox.warning(
                self,
                "Filename collisions",
                "Some target filenames already exist in this folder.\n\n"
                "Please change the prefix or move/delete the conflicting files, then try again.\n\n"
                f"Examples:\n{sample}{more}",
            )
            return

        # confirmation
        ex = "\n".join(f"- {old} → {new}" for (old, new) in plan.old_to_new[:10])
        more = "" if len(plan.old_to_new) <= 10 else f"\n... and {len(plan.old_to_new) - 10} more"
        resp = QMessageBox.question(
            self,
            "Confirm rename",
            "This will rename files on disk to match your arranged order.\n\n"
            f"Folder: {folder}\n"
            f"Count: {len(plan.old_to_new)}\n\n"
            f"Examples:\n{ex}{more}\n\nProceed?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        prog = QProgressDialog("Renaming files…", None, 0, len(plan.old_to_new) * 2, self)
        prog.setWindowModality(Qt.WindowModality.ApplicationModal)
        prog.setMinimumDuration(0)

        def progress(done: int, total: int) -> None:
            prog.setMaximum(total)
            prog.setValue(done)
            QApplication.processEvents()

        try:
            execute_rename_plan(plan, progress_cb=progress)
        except Exception as e:
            QMessageBox.critical(self, "Rename failed", f"Rename failed:\n{e}")
            return

        # reload folder to reflect new names + update order file
        self.load_folder(folder)
        self.statusBar().showMessage("Rename complete")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(prog="photo_sorter", add_help=True)
    parser.add_argument("--folder", help="Folder to load on startup (optional)")
    parser.add_argument("--prefix", help="Default rename prefix (optional)")
    parser.add_argument(
        "--smoke-test-offscreen",
        action="store_true",
        help="Run Qt with offscreen platform, start the app, then exit quickly (for CI/headless).",
    )
    args = parser.parse_args(argv)

    if args.smoke_test_offscreen:
        # Must be set before QApplication is created.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    app = QApplication([])
    w = MainWindow()

    if args.prefix:
        w.prefix_edit.setText(args.prefix)
    if args.folder:
        w.load_folder(args.folder)

    w.resize(1200, 800)
    w.show()

    if args.smoke_test_offscreen:
        QTimer.singleShot(300, app.quit)

    return cast(int, app.exec())


