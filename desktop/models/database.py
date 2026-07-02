"""SQLite 接続とスキーマ初期化。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from config import DB_PATH, ensure_data_dirs


SCHEMA = """
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tests (
    id TEXT PRIMARY KEY,
    test_name TEXT NOT NULL,
    subject TEXT DEFAULT '',
    datetime TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    status TEXT DEFAULT '作成中',
    current_step INTEGER DEFAULT 0,
    last_saved_at TEXT DEFAULT '',
    student_folder_path TEXT DEFAULT '',
    model_answer_path TEXT DEFAULT '',
    ref_width INTEGER DEFAULT 0,
    ref_height INTEGER DEFAULT 0,
    use_id_mark INTEGER DEFAULT 1,
    selected_roster TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS test_info (
    test_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT DEFAULT '',
    PRIMARY KEY (test_id, key),
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS answer_fields (
    test_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    x INTEGER DEFAULT 0,
    y INTEGER DEFAULT 0,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    ocr_lang TEXT DEFAULT 'en',
    PRIMARY KEY (test_id, field_id),
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS points (
    test_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    points INTEGER DEFAULT 0,
    PRIMARY KEY (test_id, field_id),
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id TEXT NOT NULL,
    student_id TEXT DEFAULT '',
    file_name TEXT NOT NULL,
    source_path TEXT DEFAULT '',
    warped_path TEXT DEFAULT '',
    name TEXT DEFAULT '',
    texts_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (test_id, file_name),
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS roster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roster_name TEXT NOT NULL,
    student_id TEXT NOT NULL,
    year TEXT DEFAULT '',
    class_name TEXT DEFAULT '',
    number TEXT DEFAULT '',
    student_name TEXT DEFAULT '',
    attr1 TEXT DEFAULT '',
    attr2 TEXT DEFAULT '',
    attr3 TEXT DEFAULT '',
    UNIQUE (roster_name, student_id)
);

CREATE TABLE IF NOT EXISTS grading_criteria (
    test_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    judgment TEXT DEFAULT '×',
    score INTEGER DEFAULT 0,
    reason TEXT DEFAULT '',
    PRIMARY KEY (test_id, field_id, answer_text),
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS summary_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id TEXT NOT NULL,
    category TEXT NOT NULL,
    item TEXT NOT NULL,
    value TEXT DEFAULT '',
    note TEXT DEFAULT '',
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ocr_replacements (
    test_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    search_text TEXT NOT NULL,
    replace_text TEXT DEFAULT '',
    use_regex INTEGER DEFAULT 0,
    PRIMARY KEY (test_id, field_id, search_text),
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS deemed_draft (
    test_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    canonical TEXT DEFAULT '',
    source_answer TEXT NOT NULL,
    PRIMARY KEY (test_id, field_id, source_answer),
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS deemed_scoring (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    canonical TEXT NOT NULL,
    source_answer TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS domain_settings (
    test_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    dai_mon TEXT DEFAULT '',
    han_i TEXT DEFAULT '',
    noryoku TEXT DEFAULT '',
    PRIMARY KEY (test_id, field_id),
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS external_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    score REAL DEFAULT 0,
    source TEXT DEFAULT 'CSV取込',
    imported_at TEXT NOT NULL,
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS identity_fields (
    test_id TEXT NOT NULL,
    field_type TEXT NOT NULL,
    x INTEGER DEFAULT 0,
    y INTEGER DEFAULT 0,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    extra_json TEXT DEFAULT '{}',
    PRIMARY KEY (test_id, field_type),
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS output_slots (
    test_id TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    x INTEGER DEFAULT 0,
    y INTEGER DEFAULT 0,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    extra_json TEXT DEFAULT '{}',
    PRIMARY KEY (test_id, slot_key),
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(results)").fetchall()}
    if "judgments_json" not in cols:
        conn.execute("ALTER TABLE results ADD COLUMN judgments_json TEXT DEFAULT '{}'")
    if "scores_json" not in cols:
        conn.execute("ALTER TABLE results ADD COLUMN scores_json TEXT DEFAULT '{}'")
    if "domain_scores_json" not in cols:
        conn.execute("ALTER TABLE results ADD COLUMN domain_scores_json TEXT DEFAULT '{}'")
    if "external_score" not in cols:
        conn.execute("ALTER TABLE results ADD COLUMN external_score REAL DEFAULT 0")
    if "total_score" not in cols:
        conn.execute("ALTER TABLE results ADD COLUMN total_score REAL DEFAULT 0")


def init_db() -> None:
    ensure_data_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_data_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def get_active_test_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM app_state WHERE key = 'active_test_id'"
    ).fetchone()
    return row["value"] if row else None


def set_active_test_id(conn: sqlite3.Connection, test_id: str) -> None:
    conn.execute(
        "INSERT INTO app_state(key, value) VALUES('active_test_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (test_id,),
    )
