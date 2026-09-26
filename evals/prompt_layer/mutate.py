"""Mutation check for every G3-lite guard (d7 section 4).

For each row of mutants.json: md5 the file, check the original snippet
occurs exactly once, write the mutated bytes, run only the named tests, write
the ORIGINAL bytes back and verify the md5. A mutant whose tests stay green is
a surviving mutant: the guard is decoration. Never uses git to undo anything.

Run from the repository root with the backend interpreter:
    backend/.venv/Scripts/python.exe evals/prompt_layer/mutate.py
Exit 0 only when every mutant was killed and every file was restored.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "backend"


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_tests(node_ids: list[str]) -> bool:
    """True when the tests PASS (the mutant survived)."""
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header", "-x",
                           "-p", "no:cacheprovider", *node_ids],
                          cwd=str(BACKEND), capture_output=True, text=True)
    return proc.returncode == 0


def main() -> int:
    rows = json.loads((HERE / "mutants.json").read_text(encoding="utf-8"))
    killed = survived = 0
    for row in rows:
        path = REPO / row["file"]
        original = path.read_bytes()
        before = md5(path)
        text = original.decode("utf-8")
        n = text.count(row["original"])
        if n != 1:
            print(f"ERROR {row['id']}: snippet found {n} times in {row['file']}")
            return 2
        try:
            path.write_bytes(text.replace(row["original"], row["mutant"]).encode("utf-8"))
            passed = run_tests(row["tests"])
        finally:
            path.write_bytes(original)
        after = md5(path)
        if after != before:
            print(f"FATAL {row['id']}: {row['file']} md5 {after} != {before} after restore")
            return 3
        status = "SURVIVED" if passed else "killed"
        killed += not passed
        survived += passed
        print(f"{status:8} {row['id']}: {row['what']} (md5 restored {after[:12]})")
    print(f"\n{killed} killed, {survived} survived, {len(rows)} mutants")
    return 0 if survived == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
