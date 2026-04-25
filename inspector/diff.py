"""Tree-level fs diff between two `walk_fs_tree` blobs.

Modified is defined as same path with a different size. mtime would be more
precise but `walk_fs_tree` doesn't capture it today, and size catches every
write that actually changes content. Directories themselves are ignored — only
file paths land in the buckets.
"""
from __future__ import annotations

from typing import Any, Iterable

MAX_BUCKET = 500


def flatten_files(tree: Any) -> dict[str, int | None]:
    """Walk an fs-tree dict and return {path: size_or_None} for every file."""
    out: dict[str, int | None] = {}
    if not isinstance(tree, dict):
        return out
    stack: list[Any] = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        path = node.get("path")
        if ntype == "file" and isinstance(path, str):
            out[path] = node.get("size")
        children = node.get("children")
        if isinstance(children, list):
            stack.extend(children)
    return out


def _cap(items: Iterable[str], cap: int = MAX_BUCKET) -> tuple[list[str], bool]:
    """Sort + cap a bucket; return (items, was_truncated)."""
    sorted_items = sorted(items)
    if len(sorted_items) > cap:
        return sorted_items[:cap], True
    return sorted_items, False


def diff_flat_files(
    old_files: dict[str, int | None], new_files: dict[str, int | None]
) -> dict[str, Any]:
    """Diff two pre-flattened {path: size} maps. Lets callers iterating over
    a sequence of trees flatten each tree once instead of twice (as `prev`
    then as `current` on the next iteration)."""
    added_keys = new_files.keys() - old_files.keys()
    removed_keys = old_files.keys() - new_files.keys()
    modified_keys = {
        p
        for p in old_files.keys() & new_files.keys()
        if old_files[p] != new_files[p]
    }

    added, t_a = _cap(added_keys)
    removed, t_r = _cap(removed_keys)
    modified, t_m = _cap(modified_keys)
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "truncated": t_a or t_r or t_m,
    }


def diff_trees(old_tree: Any, new_tree: Any) -> dict[str, Any]:
    """Return tree-level diff buckets between two fs-tree blobs.

    Shape: {added, removed, modified, truncated}. Each list contains absolute
    paths sorted lexicographically and capped at MAX_BUCKET. `truncated` is
    True if any bucket was capped.
    """
    return diff_flat_files(flatten_files(old_tree), flatten_files(new_tree))


def summarize_diff(d: dict[str, Any]) -> dict[str, int]:
    """Counts-only projection used by /runs/{id}/diff-summary."""
    return {
        "added": len(d.get("added", [])),
        "removed": len(d.get("removed", [])),
        "modified": len(d.get("modified", [])),
    }
