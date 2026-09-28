"""The git variables a hook exports, and how I keep them out of child processes.

When git runs a hook it exports the location of the repository it is working
on: GIT_DIR always, GIT_INDEX_FILE during a commit, and from a linked worktree
GIT_COMMON_DIR and friends as well. Every process the hook starts inherits
them, and so does every git command those processes start. A git command run
with GIT_DIR set ignores its working directory when it looks for a repository.
`git init` in a fresh temporary folder then re-initialises the repository
GIT_DIR names, and `git config` and `git commit` write into it.

That happened. The pre-push hook ran the backend suite from a linked
worktree, a test built a scratch repository in tmp_path with
`git init`, `git config user.*` and `git commit`, and every one of those
commands landed in the real repository instead: core.bare=true, a test
identity, core.hooksPath=my-hooks, and test commits on the branch being pushed.

This file is the one list of what has to go. run_gates.py strips it from every
child process it starts, and backend/tests/_git_env.py builds the environment
every git call in the suite runs under on top of it.

What stays on purpose: GIT_CEILING_DIRECTORIES and GIT_CONFIG_NOSYSTEM only
narrow what git reads, never point it somewhere else, and the terminal and
editor switches (GIT_TERMINAL_PROMPT, GIT_EDITOR, GIT_ASKPASS) are about
prompts, not about which repository is touched.

Not stripped from the hook process itself. verify_hygiene.py --staged runs in
that process and has to read the index being committed, and during a partial
commit that index is a temporary file only GIT_INDEX_FILE names.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: Every variable that changes WHICH repository, index, object store or config
#: a git command reads or writes.
#:
#: The first block is exactly what `git rev-parse --local-env-vars` prints on
#: git 2.55: the list git itself clears when it crosses into a submodule, for
#: the same reason. A test asks git for that list and fails if this set stops
#: covering it, so a git release that adds one cannot slip past here.
#:
#: The second block is what that list leaves out because git only clears
#: repository-local state there, while I also want the config a child reads to
#: be the child's own business.
REDIRECTING: frozenset[str] = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",

    "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_NAMESPACE",
    "GIT_QUARANTINE_PATH",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
})

#: GIT_CONFIG_COUNT comes with numbered pairs, GIT_CONFIG_KEY_0 and
#: GIT_CONFIG_VALUE_0 and so on. Dropping the count alone would already make
#: git ignore them, but a stray pair left behind is one edit away from
#: counting again.
REDIRECTING_PREFIXES: tuple[str, ...] = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def is_redirecting(name: str) -> bool:
    """True when `name` would point a git command at another repository.

    Upper-cased first because Windows environment names are case-insensitive:
    a copy of the environment handed around as a plain dict can carry
    `Git_Dir`, and git would still honour it.
    """
    upper = name.upper()
    return upper in REDIRECTING or upper.startswith(REDIRECTING_PREFIXES)


def without_redirects(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of `env` (the process environment by default) minus REDIRECTING.

    Always a new dict. The caller is free to add to it, and nothing it does
    can reach back into os.environ.
    """
    source = os.environ if env is None else env
    return {k: v for k, v in source.items() if not is_redirecting(k)}
