"""リンクオフ時の取込・反映前に、現在の状態を保存して取り消せるようにする。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMessageBox, QWidget

from models.criteria_repo import (
    capture_grade_checkpoint,
    hold_grade_checkpoint,
    peek_grade_checkpoint,
    pop_grade_checkpoint,
    restore_checkpoint_slot,
    restore_grade_checkpoint,
)


def confirm_saved_then_transfer(
    parent: QWidget,
    test_id: str,
    field_id: str,
    *,
    criteria_rules: list[dict[str, Any]] | None,
    title: str,
    detail_lines: list[str],
) -> bool:
    """現在の状態を保存し、確定してから取込・反映してよいか尋ねる。

    キャンセル時は、今回の保存を取り消し地点に残さない。
    """
    checkpoint = capture_grade_checkpoint(test_id, field_id, criteria_rules)
    previous = hold_grade_checkpoint(checkpoint)
    lines = [
        "現在の状態を保存しました。",
        "この保存が確定してから、取込・反映を行います。",
        "実行後は「取り消す」で、この保存状態に戻せます。",
        "",
        *detail_lines,
    ]
    ask = QMessageBox.question(
        parent,
        title,
        "\n".join(lines),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if ask != QMessageBox.StandardButton.Yes:
        restore_checkpoint_slot(test_id, field_id, previous)
        return False
    return True


def undo_last_transfer(parent: QWidget, test_id: str, field_id: str) -> bool:
    checkpoint = peek_grade_checkpoint(test_id, field_id)
    if checkpoint is None:
        QMessageBox.information(parent, "取り消せません", "戻せる保存状態がありません。")
        return False
    ask = QMessageBox.question(
        parent,
        "状態を戻す",
        "取込・反映の前に保存した状態へ戻します。",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if ask != QMessageBox.StandardButton.Yes:
        return False
    stored = pop_grade_checkpoint(test_id, field_id)
    if stored is None:
        return False
    restore_grade_checkpoint(stored)
    return True


def has_transfer_undo(test_id: str, field_id: str) -> bool:
    return peek_grade_checkpoint(test_id, field_id) is not None
