from __future__ import annotations

from inspector.diff import diff_trees, summarize_diff


def _file(name: str, path: str, size: int) -> dict:
    return {"name": name, "path": path, "type": "file", "size": size}


def _dir(name: str, path: str, children: list[dict]) -> dict:
    return {"name": name, "path": path, "type": "dir", "children": children}


def test_diff_added_removed_modified():
    old = _dir(
        "/workspace",
        "/workspace",
        [
            _file("a.txt", "/workspace/a.txt", 10),
            _file("b.txt", "/workspace/b.txt", 20),
            _file("keep.txt", "/workspace/keep.txt", 5),
        ],
    )
    new = _dir(
        "/workspace",
        "/workspace",
        [
            _file("b.txt", "/workspace/b.txt", 21),  # modified
            _file("keep.txt", "/workspace/keep.txt", 5),  # unchanged
            _file("c.txt", "/workspace/c.txt", 30),  # added
        ],
    )
    d = diff_trees(old, new)
    assert d["added"] == ["/workspace/c.txt"]
    assert d["removed"] == ["/workspace/a.txt"]
    assert d["modified"] == ["/workspace/b.txt"]
    assert d["truncated"] is False


def test_diff_handles_none_old_tree():
    new = _dir(
        "/workspace",
        "/workspace",
        [_file("a.txt", "/workspace/a.txt", 10)],
    )
    d = diff_trees(None, new)
    assert d["added"] == ["/workspace/a.txt"]
    assert d["removed"] == []
    assert d["modified"] == []


def test_diff_ignores_directories():
    old = _dir(
        "/workspace",
        "/workspace",
        [_dir("sub", "/workspace/sub", [])],
    )
    new = _dir(
        "/workspace",
        "/workspace",
        [_dir("other", "/workspace/other", [])],
    )
    d = diff_trees(old, new)
    assert d["added"] == []
    assert d["removed"] == []
    assert d["modified"] == []


def test_diff_truncates_at_500():
    new = _dir(
        "/workspace",
        "/workspace",
        [_file(f"f{i}.txt", f"/workspace/f{i}.txt", 1) for i in range(600)],
    )
    d = diff_trees(None, new)
    assert len(d["added"]) == 500
    assert d["truncated"] is True


def test_diff_walks_nested_dirs():
    old = _dir(
        "/workspace",
        "/workspace",
        [
            _dir(
                "src",
                "/workspace/src",
                [_file("main.py", "/workspace/src/main.py", 100)],
            )
        ],
    )
    new = _dir(
        "/workspace",
        "/workspace",
        [
            _dir(
                "src",
                "/workspace/src",
                [
                    _file("main.py", "/workspace/src/main.py", 150),
                    _file("util.py", "/workspace/src/util.py", 50),
                ],
            )
        ],
    )
    d = diff_trees(old, new)
    assert d["added"] == ["/workspace/src/util.py"]
    assert d["modified"] == ["/workspace/src/main.py"]


def test_summarize_diff_counts_only():
    d = {
        "added": ["a", "b"],
        "removed": ["c"],
        "modified": ["d", "e", "f"],
        "truncated": False,
    }
    assert summarize_diff(d) == {"added": 2, "removed": 1, "modified": 3}
