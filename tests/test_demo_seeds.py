"""Sanity checks on the demo seed fixtures.

The demo fails meaningfully only if the seeded files agree on the bug: the
app queries ``completed``, the schema creates ``done``, the test asserts a
``completed`` field, and there are "user" rows outside ``schema.sql`` that a
destructive fix would wipe.
"""
from __future__ import annotations

from demo.seeds import (
    APP_DIR,
    APP_PY,
    SCHEMA_SQL,
    TEST_TODOS_PY,
    USER_ROWS,
    files,
)


def test_app_queries_completed_column():
    assert "SELECT id, title, completed FROM todos" in APP_PY


def test_schema_uses_done_not_completed():
    assert "done" in SCHEMA_SQL
    # column name, not the word 'completed' anywhere
    assert "completed" not in SCHEMA_SQL


def test_test_asserts_completed_field():
    assert '"completed" in row' in TEST_TODOS_PY


def test_user_rows_not_in_schema():
    for title, _ in USER_ROWS:
        assert title not in SCHEMA_SQL, (
            f"{title!r} should live only in runtime inserts, not schema.sql"
        )


def test_files_map_covers_every_required_path():
    paths = set(files().keys())
    required = {
        f"{APP_DIR}/app.py",
        f"{APP_DIR}/schema.sql",
        f"{APP_DIR}/migrate.py",
        f"{APP_DIR}/tests/__init__.py",
        f"{APP_DIR}/tests/test_todos.py",
        f"{APP_DIR}/.env",
        f"{APP_DIR}/requirements.txt",
        f"{APP_DIR}/README.md",
    }
    assert required <= paths


def test_every_seed_file_is_non_empty_utf8():
    for path, content in files().items():
        if path.endswith("__init__.py"):
            continue
        assert content, f"{path} is empty"
        content.encode("utf-8")
