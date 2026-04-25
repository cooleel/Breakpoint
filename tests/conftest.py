from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

import pytest

from inspector.storage import init_db


@pytest.fixture
def tmp_db(tmp_path):
    """Fresh SQLite per test. init_db() replaces the module-global engine."""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    return str(db_path)


@dataclass
class FakeSnapshot:
    snapshot_id: str = "snap_fake"


@dataclass
class FakeTLClient:
    snapshot_id: str = "snap_fake"
    snapshot_latency_s: float = 0.0
    calls: list = field(default_factory=list)

    def snapshot_and_wait(self, sandbox_id: str, timeout: float = 300.0, **kwargs) -> FakeSnapshot:
        import time
        self.calls.append(("snapshot_and_wait", sandbox_id, timeout))
        if self.snapshot_latency_s:
            time.sleep(self.snapshot_latency_s)
        return FakeSnapshot(snapshot_id=self.snapshot_id)


@pytest.fixture
def fake_tl():
    return FakeTLClient()


def _entry(name: str, *, is_dir: bool = False, size: Optional[int] = None) -> dict:
    return {"name": name, "is_dir": is_dir, "size": size}


@dataclass
class FakeSandbox:
    sandbox_id: str = "sbx_fake"
    # Map path -> list of entry dicts. Missing path => raises.
    tree: dict[str, list[dict]] = field(default_factory=dict)
    error_paths: set[str] = field(default_factory=set)

    def list_directory(self, path: str):
        if path in self.error_paths:
            raise RuntimeError(f"boom: {path}")
        entries = self.tree.get(path, [])
        return SimpleNamespace(entries=entries)


@pytest.fixture
def make_sandbox():
    """Factory: build a FakeSandbox from a nested dict describing the tree."""

    def _factory(layout: dict, error_paths: Optional[set[str]] = None) -> FakeSandbox:
        flat: dict[str, list[dict]] = {}

        def walk(path: str, node: dict) -> None:
            entries = []
            for name, child in node.items():
                if isinstance(child, dict):
                    entries.append(_entry(name, is_dir=True))
                    walk(f"{path.rstrip('/')}/{name}", child)
                else:
                    entries.append(_entry(name, size=int(child)))
            flat[path] = entries

        walk("/workspace", layout)
        return FakeSandbox(tree=flat, error_paths=error_paths or set())

    return _factory
