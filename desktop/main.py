"""模範解答ベース自動採点システム — PC版エントリポイント（Qt / PySide6）。"""

from __future__ import annotations

import sys
from pathlib import Path

# desktop/ を import パスに追加（python main.py / python -m どちらでも動作）
DESKTOP_ROOT = Path(__file__).resolve().parent
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))


def main() -> None:
    from PySide6.QtWidgets import QApplication

    from ui_qt.main_window import MainWindow
    from ui_qt.style import apply_style

    app = QApplication(sys.argv)
    apply_style(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
