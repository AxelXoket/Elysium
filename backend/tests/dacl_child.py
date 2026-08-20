"""A child that narrows its own process, or deliberately does not.

Separate from the test because narrowing is IRREVERSIBLE for the life of a
process: doing it inside pytest would change every test that runs afterwards,
including the ones in this file that read a child's command line. So the thing
under test happens over there, and the test probes it from here.

Prints one JSON line and then sleeps, so the parent has a live process to open.
"""
from __future__ import annotations

import ctypes
import json
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

import win_hardening  # noqa: E402

#: The sentinel the parent will try to read out of this process. A fresh value
#: per run, passed in, so a stale copy in the page file cannot pass for a live
#: read.
SECRET = sys.argv[2].encode("ascii") if len(sys.argv) > 2 else b"no-secret"
_KEEP_ALIVE = ctypes.create_string_buffer(SECRET)


def main() -> None:
    arm = sys.argv[1] if len(sys.argv) > 1 else "narrow"
    took = win_hardening.narrow_own_process() if arm == "narrow" else None
    print(json.dumps({
        "pid": __import__("os").getpid(),
        "arm": arm,
        "narrowed": took,
        "address": ctypes.addressof(_KEEP_ALIVE),
        "length": len(SECRET),
    }), flush=True)
    time.sleep(25)


if __name__ == "__main__":
    main()
