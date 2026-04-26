"""Smoke checks for Tensorlake snapshot latency, fs walk, and process continuity across snapshot/restore.

Runs on the smallest supported Tensorlake sandbox (1 CPU / 1 GB RAM — platform floor).
Snapshot captures memory + fs, so a sub-second target is not achievable at this size;
steady-state target is <3s.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env *before* importing tensorlake: the SDK freezes TENSORLAKE_API_KEY
# at import time (tensorlake/sandbox/_defaults.py) and uses it as the default
# for SandboxClient(). Importing first would bake in whatever key was already
# in the shell.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

from tensorlake.sandbox import SandboxClient  # noqa: E402

from inspector.snapshot import take_snapshot, walk_fs_tree  # noqa: E402


def seed_fs(sandbox) -> None:
    print("[seed] writing ~200MB / ~10k files under /workspace ...", flush=True)
    script = r"""
set -e
mkdir -p /workspace/data
cd /workspace/data
for i in $(seq 1 10); do
  mkdir -p "bucket_$i"
  for j in $(seq 1 1000); do
    dd if=/dev/urandom of="bucket_$i/file_$j.bin" bs=20480 count=1 status=none
  done
done
echo "wrote:"
find /workspace/data -type f | wc -l
du -sh /workspace/data
free -m | head -n 2
"""
    t0 = time.perf_counter()
    r = sandbox.run("bash", ["-c", script], timeout=600)
    dt = time.perf_counter() - t0
    print(f"[seed] done in {dt:.1f}s exit={r.exit_code}\n{r.stdout}")
    if r.exit_code != 0:
        print("[seed] stderr:", r.stderr, file=sys.stderr)
        raise SystemExit(1)


def validate_snapshot_latency(tl, sandbox) -> str:
    # Snapshot captures ~1 GB RAM + fs on the platform-floor sandbox, so sub-1s is
    # not achievable. Steady-state target: <3s.
    print("\n[#1] snapshot latency (1 CPU / 1 GB sandbox — platform floor)")
    results = []
    for i in range(3):
        res = take_snapshot(tl, sandbox.sandbox_id)
        print(f"  attempt {i+1}: snapshot_id={res.snapshot_id} elapsed_ms={res.elapsed_ms}")
        results.append(res)

    first = results[0].elapsed_ms
    steady = results[-1].elapsed_ms
    print(f"  first={first}ms  last={steady}ms")
    if steady < 3000:
        print("  PASS (<3s steady-state — usable for snapshot-per-tool-call)")
    elif steady < 8000:
        print("  MARGINAL: steady-state between 3s and 8s. Plan: snapshot only after fs-mutating tools, or every Nth call.")
    else:
        print("  FAIL: steady-state >=8s. Scope change needed: snapshot less frequently or capture fs-only diffs.")
    return results[0].snapshot_id


def validate_fs_walk(sandbox) -> None:
    print("\n[#2] fs tree walk")
    tree = walk_fs_tree(sandbox, root="/workspace", max_entries=20000)
    meta = tree.get("_meta", {})
    print(f"  entries={meta.get('entries')} elapsed_ms={meta.get('elapsed_ms')} truncated={meta.get('truncated')}")
    if meta.get("elapsed_ms", 9999) < 500:
        print("  PASS (<500ms)")
    else:
        print(f"  INFO: {meta.get('elapsed_ms')}ms (>500ms). Plan: walk async, mark as materializing in UI.")


def validate_process_continuity(tl, sandbox) -> None:
    print("\n[#3] process continuity across snapshot/restore")
    # Start python http.server in the background, snapshot while running, restore, check PID.
    print("  starting python http.server in sandbox...")
    proc = sandbox.start_process(
        "python3",
        ["-m", "http.server", "8765"],
        working_dir="/workspace",
        stdout_mode="capture",
        stderr_mode="capture",
    )
    print(f"  pid={proc.pid}")
    time.sleep(2.0)
    # confirm it's actually listening
    r = sandbox.run("bash", ["-c", "ss -lnt | grep 8765 || netstat -lnt 2>/dev/null | grep 8765 || echo not-listening"])
    print(f"  source listen check: {r.stdout.strip()}")

    snap = take_snapshot(tl, sandbox.sandbox_id)
    print(f"  snapshot_id={snap.snapshot_id} elapsed_ms={snap.elapsed_ms}")

    print("  restoring into new sandbox...")
    restored = tl.create_and_connect(snapshot_id=snap.snapshot_id, timeout_secs=300)
    try:
        time.sleep(3.0)
        r2 = restored.run(
            "bash",
            ["-c", "ss -lnt | grep 8765 || netstat -lnt 2>/dev/null | grep 8765 || echo not-listening"],
        )
        print(f"  restored listen check: {r2.stdout.strip()}")
        ps = restored.run("bash", ["-c", "ps -ef | grep http.server | grep -v grep || echo no-http-server"])
        print(f"  restored ps: {ps.stdout.strip()}")
        restored_listening = "8765" in r2.stdout
        restored_has_proc = "http.server" in ps.stdout
        print(f"  process continuity: listening={restored_listening} process_found={restored_has_proc}")
        if restored_listening and restored_has_proc:
            print("  PASS: blog can say 'fs + memory + processes'")
        else:
            print("  INFO: processes NOT restored. Blog must say 'fs + memory only'.")
    finally:
        try:
            restored.close()
        except Exception:
            pass


def main() -> None:
    tl = SandboxClient()
    print("[setup] creating sandbox (platform floor: 1 CPU / 1 GB)...")
    sandbox = tl.create_and_connect(cpus=1.0, memory_mb=1024, timeout_secs=1800)
    try:
        print(f"[setup] sandbox_id={sandbox.sandbox_id}")
        seed_fs(sandbox)
        snap_id = validate_snapshot_latency(tl, sandbox)
        validate_fs_walk(sandbox)
        validate_process_continuity(tl, sandbox)
        print("\n[done] summary:")
        print(json.dumps({"seed_snapshot_id": snap_id}, indent=2))
    finally:
        print("[teardown] terminating sandbox")
        try:
            sandbox.close()
        except Exception as e:
            print(f"[teardown] close failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
