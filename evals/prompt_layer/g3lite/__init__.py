"""G3-lite: the sub-$1 paid evaluation of the prompt layer and prompt modules.

See ../README.md for what it measures, how it is guarded and how to run it.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL_DIR = HERE.parent
REPO_ROOT = EVAL_DIR.parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
FIXTURES_DIR = EVAL_DIR / "fixtures"
JUDGE_DIR = EVAL_DIR / "judge"
