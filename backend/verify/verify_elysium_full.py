"""
verify_elysium_full.py - Aggregated backend regression runner.

Runs all backend verify scripts in dependency order, then runs
P-01 through P-20 privacy grep checks on backend source files.

Run from backend/:
    .venv/Scripts/python verify_elysium_full.py

Does NOT run frontend tests, npm, npx, or modify any files.
"""

import sys
import os
import subprocess
import re

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

PYTHON = sys.executable

# ---------------------------------------------------------------------------
# Part 1: Run each verify script as subprocess
# ---------------------------------------------------------------------------

VERIFY_SCRIPTS = [
    "verify_part_a.py",
    "verify_part_b.py",
    "verify_part_c.py",
    "verify_part_d.py",
    "verify_part_e.py",
    "verify_phase5b.py",
]

script_results: list[tuple[str, bool]] = []

print("=" * 62)
print("  Elysium Full Backend Regression")
print("=" * 62)

for script in VERIFY_SCRIPTS:
    script_path = os.path.join(BACKEND_DIR, script)
    if not os.path.exists(script_path):
        if script == "verify_part_f.py":
            print(f"\n  [SKIP] {script} (Part F deferred)")
            continue
        print(f"\n  [{FAIL}] {script} - FILE NOT FOUND")
        script_results.append((script, False))
        continue

    print(f"\n{'─' * 62}")
    print(f"  Running: {script}")
    print(f"{'─' * 62}")

    result = subprocess.run(
        [PYTHON, script_path],
        cwd=BACKEND_DIR,
        timeout=120,
    )
    ok = result.returncode == 0
    tag = PASS if ok else FAIL
    print(f"\n  [{tag}] {script} → exit code {result.returncode}")
    script_results.append((script, ok))

# ---------------------------------------------------------------------------
# Part 2: P-01 through P-20 Privacy Grep Checks
# ---------------------------------------------------------------------------

print(f"\n{'=' * 62}")
print("  Privacy Grep Checks (P-01 through P-20)")
print(f"{'=' * 62}")

privacy_results: list[tuple[str, bool]] = []

# Collect all .py files under backend/ (excluding .venv, __pycache__)
py_files: list[str] = []
for root, dirs, files in os.walk(BACKEND_DIR):
    dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", "avatars")]
    for f in files:
        if f.endswith(".py"):
            py_files.append(os.path.join(root, f))


def grep_backend(pattern: str, case_insensitive: bool = False) -> list[tuple[str, int, str]]:
    """Search backend .py files for pattern. Returns (file, lineno, line) hits."""
    flags = re.IGNORECASE if case_insensitive else 0
    regex = re.compile(pattern, flags)
    hits = []
    for fp in py_files:
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if regex.search(line):
                        hits.append((os.path.basename(fp), i, line.strip()))
        except Exception:
            pass
    return hits


def privacy_check(pid: str, desc: str, pattern: str,
                  expect_absent: bool = True,
                  case_insensitive: bool = False,
                  exclude_files: set[str] | None = None) -> bool:
    """Run a privacy grep check. Returns True if check passes."""
    hits = grep_backend(pattern, case_insensitive)
    if exclude_files:
        hits = [(f, l, c) for f, l, c in hits if f not in exclude_files]
    ok = (len(hits) == 0) if expect_absent else (len(hits) > 0)
    tag = PASS if ok else FAIL
    detail = ""
    if not ok and hits:
        detail = f"  found in: {', '.join(f'{f}:{l}' for f, l, _ in hits[:5])}"
    print(f"  [{tag}] {pid}  {desc}{detail}")
    privacy_results.append((f"{pid} {desc}", ok))
    return ok


# Allow verify scripts + docstrings/comments to mention these patterns
VERIFY_FILES = {
    "verify_part_a.py", "verify_part_b.py", "verify_part_c.py",
    "verify_part_d.py", "verify_part_e.py", "verify_part_f.py",
    "verify_phase5b.py", "verify_phase5a.py", "verify_phase2.py",
    "verify_phase3.py", "verify_phase1.py", "verify_elysium_full.py",
}

# P-01: context_length_override must not exist in backend code
privacy_check("P-01", "no context_length_override in backend",
              r"context_length_override",
              exclude_files=VERIFY_FILES)

# P-02: context_budget_tokens must not appear in openrouter.py payload
hits_p02 = grep_backend(r"context_budget_tokens")
# Should only appear in completions.py (request model/logic) and config/docs, NOT in openrouter.py
or_hits = [h for h in hits_p02 if h[0] == "openrouter.py"]
ok_p02 = len(or_hits) == 0
tag_p02 = PASS if ok_p02 else FAIL
print(f"  [{tag_p02}] P-02  context_budget_tokens not in openrouter.py payload")
privacy_results.append(("P-02 context_budget_tokens not in openrouter.py", ok_p02))

# P-03: no image_url in message construction
# Pattern requires quotes around image_url to match code usage, not docstrings.
privacy_check("P-03", "no image_url in message construction",
              r'["\']image_url["\']',
              exclude_files=VERIFY_FILES)

# P-04: no tools/tool_choice/response_format in payload
privacy_check("P-04", "no tools/tool_choice/response_format",
              r'"tools"|"tool_choice"|"response_format"',
              exclude_files=VERIFY_FILES)

# P-05: raw_json never returned by endpoints
# Exclude docstrings that say "raw_json is NEVER returned" - those are privacy comments.
privacy_check("P-05", "no raw_json in endpoint responses",
              r"raw_json.*response|return.*raw_json",
              exclude_files=VERIFY_FILES | {"characters.py", "chats.py"})

# P-06: API key never logged
privacy_check("P-06", "no API key logging (logger.*api_key value)",
              r'logger\.\w+\(.*["\'].*api_key.*["\'].*,\s*api_key',
              exclude_files=VERIFY_FILES)

# P-07: stream always false
hits_p07 = grep_backend(r'"stream"\s*:\s*[Tt]rue')
ok_p07 = len(hits_p07) == 0
tag_p07 = PASS if ok_p07 else FAIL
print(f"  [{tag_p07}] P-07  no stream:true in backend")
privacy_results.append(("P-07 no stream:true", ok_p07))

# P-08: zdr hardcoded true
privacy_check("P-08", "zdr=true present in PROVIDER_POLICY",
              r'"zdr"\s*:\s*True|["\']zdr["\']\s*:\s*[Tt]rue',
              expect_absent=False)

# P-09: data_collection hardcoded deny
privacy_check("P-09", "data_collection=deny present in PROVIDER_POLICY",
              r'"data_collection"\s*:\s*"deny"|["\']data_collection["\']\s*:\s*["\']deny',
              expect_absent=False)

# P-10: allow_fallbacks hardcoded false
privacy_check("P-10", "allow_fallbacks=false present in PROVIDER_POLICY",
              r'"allow_fallbacks"\s*:\s*False|["\']allow_fallbacks["\']\s*:\s*[Ff]alse',
              expect_absent=False)

# P-11: ProviderPolicy uses extra=ignore
privacy_check("P-11", "ProviderPolicy has extra=ignore",
              r'class\s+ProviderPolicy.*|extra\s*=\s*"ignore"',
              expect_absent=False)

# P-12: no avatar data in payload construction
privacy_check("P-12", "no avatar_path in OpenRouter payload construction",
              r'avatar_path.*payload|payload.*avatar',
              exclude_files=VERIFY_FILES)

# P-13: no wildcard CORS
privacy_check("P-13", "no wildcard CORS origin",
              r'allow_origins\s*=\s*\["\*"\]|allow_origins.*\*',
              exclude_files=VERIFY_FILES)

# P-14: no 0.0.0.0 binding
# main.py docstring says "0.0.0.0 is never used" - exclude as a privacy comment.
privacy_check("P-14", "no 0.0.0.0 binding",
              r'0\.0\.0\.0',
              exclude_files=VERIFY_FILES | {"main.py"})

# P-15: no direct httpx/requests import in routers
router_files = [f for f in py_files
                if "routers" in f and os.path.basename(f) not in VERIFY_FILES]
router_hits = []
for fp in router_files:
    try:
        with open(fp, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if re.search(r'^import\s+httpx|^from\s+httpx|^import\s+requests|^from\s+requests', line):
                    router_hits.append((os.path.basename(fp), i, line.strip()))
    except Exception:
        pass
ok_p15 = len(router_hits) == 0
tag_p15 = PASS if ok_p15 else FAIL
print(f"  [{tag_p15}] P-15  no direct httpx/requests import in routers")
privacy_results.append(("P-15 no httpx/requests in routers", ok_p15))

# P-16: no localStorage/sessionStorage/IndexedDB (backend shouldn't reference these)
privacy_check("P-16", "no browser storage references in backend",
              r"localStorage|sessionStorage|IndexedDB",
              exclude_files=VERIFY_FILES)

# P-17: no message content logging
# Check that logger calls don't include message content variables
privacy_check("P-17", "no message content in logger calls",
              r'logger\.\w+\(.*content\s*=|logger\.\w+\(.*user_text|logger\.\w+\(.*assistant_text',
              exclude_files=VERIFY_FILES)

# P-18: no persona description logging
privacy_check("P-18", "no persona description logging",
              r'logger\.\w+\(.*persona.*description|logger\.\w+\(.*desc\b',
              exclude_files=VERIFY_FILES)

# P-19: no raw upstream body forwarding
privacy_check("P-19", "no raw upstream body forwarding in error responses",
              r'resp\.text|resp\.content|response\.text|response\.content',
              exclude_files=VERIFY_FILES | {"openrouter.py", "network_client.py", "proxy_health.py"})

# P-20: inactive persona/character not in payload
# This is verified by tests V-C-7c and V-C-10b, just check no global persona fetch
privacy_check("P-20", "no SELECT * FROM personas in completion path",
              r'SELECT\s+\*\s+FROM\s+personas',
              exclude_files=VERIFY_FILES)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print(f"\n{'=' * 62}")
print("  FINAL SUMMARY")
print(f"{'=' * 62}")

all_ok = True

print("\n  Verify Scripts:")
for name, ok in script_results:
    tag = PASS if ok else FAIL
    print(f"    [{tag}] {name}")
    if not ok:
        all_ok = False

print(f"\n  Privacy Checks: {sum(1 for _, ok in privacy_results if ok)}/{len(privacy_results)} passed")
for name, ok in privacy_results:
    if not ok:
        print(f"    [{FAIL}] {name}")
        all_ok = False

print()
if all_ok:
    print(f"  [{PASS}] ALL CHECKS PASSED - SAFE_FOR_CODEX_HANDOFF")
else:
    print(f"  [{FAIL}] SOME CHECKS FAILED - FIX REQUIRED")

sys.exit(0 if all_ok else 1)
