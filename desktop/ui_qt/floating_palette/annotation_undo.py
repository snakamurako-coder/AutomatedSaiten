"""描画・テキスト注釈の巻き戻し・やり直し（各最大20操作）。"""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from ui_qt.stylus_overlay import CropInkImageStack

MAX_UNDO_OPS = 20


@dataclass(frozen=True)
class HistoryEntry:
    result_id: int
    field_id: str
    before: dict[str, Any]
    after: dict[str, Any]


def snapshot_stack(stack: CropInkImageStack) -> dict[str, Any]:
    return {
        "strokes": copy.deepcopy(stack.ink_overlay.strokes()),
        "annotations": stack.text_layer.annotations(),
    }


def snapshots_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (
        copy.deepcopy(a.get("strokes") or []) == copy.deepcopy(b.get("strokes") or [])
        and copy.deepcopy(a.get("annotations") or [])
        == copy.deepcopy(b.get("annotations") or [])
    )


class AnnotationUndoStack:
    """画像タイル単位のスナップショット巻き戻し・やり直し。"""

    def __init__(self) -> None:
        self._undo: deque[HistoryEntry] = deque(maxlen=MAX_UNDO_OPS)
        self._redo: deque[HistoryEntry] = deque(maxlen=MAX_UNDO_OPS)
        self._pending: dict[int, dict[str, Any]] = {}
        self._applying = False
        self._stack_lookup: dict[tuple[int, str], CropInkImageStack] = {}
        self._on_changed: Callable[[], None] | None = None

    def set_on_changed(self, cb: Callable[[], None] | None) -> None:
        self._on_changed = cb

    def register_stack(self, stack: CropInkImageStack) -> None:
        self._stack_lookup[(stack.result_id, stack.field_id)] = stack

    def unregister_stack(self, stack: CropInkImageStack) -> None:
        self._stack_lookup.pop((stack.result_id, stack.field_id), None)
        self._pending.pop(id(stack), None)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def begin(self, stack: CropInkImageStack) -> None:
        if self._applying:
            return
        self.register_stack(stack)
        self._pending[id(stack)] = snapshot_stack(stack)

    def commit(self, stack: CropInkImageStack) -> None:
        if self._applying:
            return
        before = self._pending.pop(id(stack), None)
        if before is None:
            return
        after = snapshot_stack(stack)
        self.push(stack, before, after)

    def push(
        self,
        stack: CropInkImageStack,
        before: dict[str, Any],
        after: dict[str, Any] | None = None,
    ) -> None:
        if self._applying:
            return
        self.register_stack(stack)
        after_snap = after if after is not None else snapshot_stack(stack)
        if snapshots_equal(before, after_snap):
            return
        self._redo.clear()
        self._undo.append(
            HistoryEntry(
                result_id=stack.result_id,
                field_id=stack.field_id,
                before=copy.deepcopy(before),
                after=copy.deepcopy(after_snap),
            )
        )
        self._notify_changed()

    def undo(self, stacks: list[CropInkImageStack]) -> bool:
        if not self._undo:
            return False
        entry = self._undo.pop()
        stack = self._resolve_stack(entry, stacks)
        if stack is None:
            self._notify_changed()
            return False
        self._applying = True
        try:
            stack.apply_snapshot(entry.before)
        finally:
            self._applying = False
        self._redo.append(entry)
        self._notify_changed()
        return True

    def redo(self, stacks: list[CropInkImageStack]) -> bool:
        if not self._redo:
            return False
        entry = self._redo.pop()
        stack = self._resolve_stack(entry, stacks)
        if stack is None:
            self._notify_changed()
            return False
        self._applying = True
        try:
            stack.apply_snapshot(entry.after)
        finally:
            self._applying = False
        self._undo.append(entry)
        self._notify_changed()
        return True

    def cancel(self, stack: CropInkImageStack) -> None:
        self._pending.pop(id(stack), None)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._pending.clear()
        self._notify_changed()

    def _resolve_stack(
        self,
        entry: HistoryEntry,
        stacks: list[CropInkImageStack],
    ) -> CropInkImageStack | None:
        stack = self._stack_lookup.get((entry.result_id, entry.field_id))
        if stack is None:
            for candidate in stacks:
                if (
                    candidate.result_id == entry.result_id
                    and candidate.field_id == entry.field_id
                ):
                    stack = candidate
                    self.register_stack(candidate)
                    break
        return stack

    def _notify_changed(self) -> None:
        if self._on_changed:
            self._on_changed()
