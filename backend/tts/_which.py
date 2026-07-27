"""Executable lookup that will not run something out of the working directory.

`shutil.which` on Windows prepends `os.curdir` to the search path - documented
behaviour, mirroring the shell. For a desktop app that is a hole: an exe
launched by double-clicking it in Downloads has that folder as its working
directory, so a `uv.exe` or `nvidia-smi.exe` sitting among the user's other
downloads is found FIRST and executed with the application's full environment,
bypassing the SHA-256 pin the module went to the trouble of checking.

`shutil.which(name, path=...)` does not fix it: the curdir entry is added after
the path argument is resolved. The only reliable answer is to reject a result
that resolves inside the current directory - which is what this does.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def which_trusted(name: str) -> str | None:
    """`shutil.which`, minus anything found in the working directory.

    Returns None rather than falling through to the next PATH entry: a
    hostile file shadowing a real tool is worth reporting as "not found" and
    letting the caller take its own fallback, not worth silently papering
    over by scanning further.
    """
    found = shutil.which(name)
    if not found:
        return None
    try:
        resolved = Path(found).resolve()
        if resolved.parent == Path.cwd().resolve():
            return None
    except OSError:
        # An unresolvable path is not one we are going to execute.
        return None
    return found
