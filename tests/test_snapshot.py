from __future__ import annotations

from inspector.snapshot import take_snapshot, walk_fs_tree


def test_take_snapshot_records_elapsed(fake_tl):
    fake_tl.snapshot_latency_s = 0.01
    res = take_snapshot(fake_tl, "sbx_xyz", timeout=30.0)
    assert res.snapshot_id == "snap_fake"
    assert res.elapsed_ms >= 10
    assert fake_tl.calls == [("snapshot_and_wait", "sbx_xyz", 30.0)]


def test_walk_empty(make_sandbox):
    sb = make_sandbox({})
    tree = walk_fs_tree(sb, root="/workspace")
    assert tree["name"] == "/workspace"
    assert tree["type"] == "dir"
    assert tree["children"] == []
    assert tree["_meta"]["entries"] == 0
    assert tree["_meta"]["truncated"] is False


def test_walk_nested(make_sandbox):
    sb = make_sandbox({"src": {"a.py": 10, "b.py": 20}, "README": 5})
    tree = walk_fs_tree(sb, root="/workspace")
    names = {c["name"] for c in tree["children"]}
    assert names == {"src", "README"}
    src = next(c for c in tree["children"] if c["name"] == "src")
    assert src["type"] == "dir"
    assert {c["name"] for c in src["children"]} == {"a.py", "b.py"}
    a = next(c for c in src["children"] if c["name"] == "a.py")
    assert a["type"] == "file"
    assert a["size"] == 10
    assert tree["_meta"]["entries"] == 4


def test_walk_truncates_on_max_entries(make_sandbox):
    sb = make_sandbox({f"f{i}.txt": 1 for i in range(20)})
    tree = walk_fs_tree(sb, root="/workspace", max_entries=5)
    assert tree["_meta"]["truncated"] is True
    assert tree["_meta"]["entries"] == 5
    assert len(tree["children"]) == 5


def test_walk_respects_max_depth(make_sandbox):
    sb = make_sandbox({"a": {"b": {"c": {"deep.txt": 1}}}})
    tree = walk_fs_tree(sb, root="/workspace", max_depth=1)
    a = tree["children"][0]
    assert a["name"] == "a"
    b = a["children"][0]
    assert b["name"] == "b"
    # b is a directory but children past max_depth are not expanded
    assert b["children"] == []


def test_walk_captures_list_directory_error(make_sandbox):
    sb = make_sandbox(
        {"ok_dir": {"file.txt": 1}, "bad_dir": {}},
        error_paths={"/workspace/bad_dir"},
    )
    tree = walk_fs_tree(sb, root="/workspace")
    bad = next(c for c in tree["children"] if c["name"] == "bad_dir")
    assert "error" in bad
    assert "boom" in bad["error"]
    # Sibling still walked
    ok = next(c for c in tree["children"] if c["name"] == "ok_dir")
    assert len(ok["children"]) == 1
