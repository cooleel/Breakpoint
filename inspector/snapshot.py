from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SnapshotResult:
    snapshot_id: str
    elapsed_ms: int


@dataclass
class SnapshotWalkResult:
    snapshot_id: Optional[str]
    fs_tree_json: Optional[str]
    snapshot_failed: bool
    error_suffix: str  # "" on success; "\n[snapshot error] <msg>" on failure


def snapshot_and_walk_sync(tl_client: Any, sandbox: Any) -> SnapshotWalkResult:
    """Snapshot + fs walk in parallel, swallowing any exception into the
    returned ``snapshot_failed`` flag. Used by sync callers (framework-agnostic
    drop-in tools); the async hook path uses ``asyncio.gather`` directly."""
    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_snap = ex.submit(take_snapshot, tl_client, sandbox.sandbox_id)
            f_tree = ex.submit(walk_fs_tree, sandbox)
            snap = f_snap.result()
            tree = f_tree.result()
        return SnapshotWalkResult(
            snapshot_id=snap.snapshot_id,
            fs_tree_json=json.dumps(tree),
            snapshot_failed=False,
            error_suffix="",
        )
    except Exception as e:
        return SnapshotWalkResult(
            snapshot_id=None,
            fs_tree_json=None,
            snapshot_failed=True,
            error_suffix=f"\n[snapshot error] {e}",
        )


def take_snapshot(tl_client: Any, sandbox_id: str, timeout: float = 300.0) -> SnapshotResult:
    t0 = time.perf_counter()
    snap = tl_client.snapshot_and_wait(sandbox_id, timeout=timeout)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return SnapshotResult(snapshot_id=snap.snapshot_id, elapsed_ms=elapsed_ms)


def walk_fs_tree(
    sandbox: Any,
    root: str = "/workspace",
    max_entries: int = 5000,
    max_depth: int = 12,
) -> dict:
    root_node = {"name": root, "path": root, "type": "dir", "children": []}
    count = 0
    truncated = False

    def walk(node: dict, depth: int) -> None:
        nonlocal count, truncated
        if truncated or depth > max_depth:
            return
        try:
            listing = sandbox.list_directory(node["path"])
        except Exception as e:
            node["error"] = str(e)
            return
        entries = getattr(listing, "entries", [])
        for entry in entries:
            if count >= max_entries:
                truncated = True
                return
            count += 1
            name = getattr(entry, "name", None) or (entry.get("name") if isinstance(entry, dict) else None)
            size = getattr(entry, "size", None) if not isinstance(entry, dict) else entry.get("size")
            is_dir_flag = _is_dir(entry)
            child_path = f"{node['path'].rstrip('/')}/{name}" if name else node["path"]
            child: dict = {
                "name": name,
                "path": child_path,
                "type": "dir" if is_dir_flag else "file",
            }
            if size is not None and not is_dir_flag:
                child["size"] = size
            if is_dir_flag:
                child["children"] = []
                walk(child, depth + 1)
            node["children"].append(child)

    t0 = time.perf_counter()
    walk(root_node, 0)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    root_node["_meta"] = {"entries": count, "elapsed_ms": elapsed_ms, "truncated": truncated}
    return root_node


def _is_dir(entry: Any) -> bool:
    for attr in ("is_dir", "isDir", "is_directory", "type"):
        v = getattr(entry, attr, None) if not isinstance(entry, dict) else entry.get(attr)
        if v is None:
            continue
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in {"dir", "directory", "folder"}
    return False
