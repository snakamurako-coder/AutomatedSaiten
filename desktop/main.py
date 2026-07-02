"""模範解答ベース自動採点システム — PC版エントリポイント。"""

from __future__ import annotations

import sys
from pathlib import Path

# desktop/ を import パスに追加（python main.py / python -m どちらでも動作）
DESKTOP_ROOT = Path(__file__).resolve().parent
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from ui.app import AutomatedSaitenApp  # noqa: E402


def main() -> None:
    app = AutomatedSaitenApp()
    app.mainloop()


if __name__ == "__main__":
    main()
