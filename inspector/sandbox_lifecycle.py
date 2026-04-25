from __future__ import annotations

import sys
from typing import Any


def terminate_sandbox(tl: Any, sandbox: Any, *, label: str = "sandbox") -> None:
    """Close + delete the sandbox; tolerates errors so callers can use this in
    a `finally:` without masking the original exception. Belt-and-suspenders:
    `close()` usually terminates, but a follow-up `delete()` makes leaks
    impossible when the connection is half-dead (broken pipe, KeyboardInterrupt).
    The delete typically 404s after a successful close — that's the happy path.
    """
    sandbox_id = getattr(sandbox, "sandbox_id", None)
    try:
        sandbox.close()
    except Exception as e:
        print(f"[{label}] close failed: {e}", file=sys.stderr)
    if sandbox_id:
        try:
            tl.delete(sandbox_id)
        except Exception:
            pass
