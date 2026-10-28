"""
Atomic, owner-only JSON writes for credential and token files.
"""

import contextlib
import json
import os
import tempfile


def write_json_atomic(path: str, data: dict) -> None:
    """
    Write JSON to `path` atomically with owner-only (0o600) permissions.

    The temp file lives in the same directory so os.replace is a same-filesystem
    rename: readers see either the old token or the new one, never half a file.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
