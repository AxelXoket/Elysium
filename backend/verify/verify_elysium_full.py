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
import glob
import subprocess
import re

# The scripts moved from backend/ into backend/verify/ (commit d8da7db) and
# this line did not, so the whole grep suite walked its OWN directory: it
# scanned 12 files, every one of them in the VERIFY_FILES exclusion set, and
# reported PASS on privacy assertions that had examined no application code
# at all. Adding `allow_origins=["*"]` to main.py still printed PASS.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The same move broke Part 1 in the mirror image of the above: the grep walk
# wanted backend/ and got verify/, while the subprocess launcher wants verify/
# and got backend/. Every script resolved to backend/verify_part_a.py, which
# does not exist, so all six printed FILE NOT FOUND and the aggregate gate was
# permanently red while running nothing. Two roots, two constants.
VERIFY_DIR = os.path.dirname(os.path.abspath(__file__))

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

# Derived, not hand-listed. The hand-written list had drifted to 6 of the 12
# scripts on disk: verify_phase1..phase4 and verify_phase5a - 3,435 lines of
# phase regression - were aggregated by nothing at all, and no test noticed
# because nothing asserted the list matched the directory. A glob cannot drift,
# and a script added tomorrow joins the gate without anyone remembering to.
#
# Two deliberate exclusions, both by name so the reason survives:
#   - this file, which would recurse.
#   - verify_tts_latency.py, which imports tts.host and needs a real GPU and a
#     downloaded model. It is a measurement tool, not a regression gate; on a
#     machine without the model it would fail for a reason that says nothing
#     about the code.
NOT_AGGREGATED = {
    "verify_elysium_full.py",
    "verify_tts_latency.py",
}

VERIFY_SCRIPTS = [
    os.path.basename(p)
    for p in sorted(glob.glob(os.path.join(VERIFY_DIR, "verify_*.py")))
    if os.path.basename(p) not in NOT_AGGREGATED
]

script_results: list[tuple[str, bool]] = []

print("=" * 62)
print("  Elysium Full Backend Regression")
print("=" * 62)
print(f"  Aggregating {len(VERIFY_SCRIPTS)} scripts from {VERIFY_DIR}")
if NOT_AGGREGATED:
    print(f"  Excluded by name: {', '.join(sorted(NOT_AGGREGATED))}")

for script in VERIFY_SCRIPTS:
    script_path = os.path.join(VERIFY_DIR, script)
    if not os.path.exists(script_path):
        # Only reachable if the tree changed under us between the glob and
        # here. Still an honest red rather than a silent skip: a name in the
        # list that has no file is exactly the state this gate exists to catch.
        print(f"\n  [{FAIL}] {script} - FILE NOT FOUND")
        script_results.append((script, False))
        continue

    print(f"\n{'─' * 62}")
    print(f"  Running: {script}")
    print(f"{'─' * 62}")

    # A hung script must not take the other ten down with it. Before the list
    # was derived this loop ran six scripts and an uncaught TimeoutExpired was
    # survivable; at eleven, losing every remaining result to one wedged
    # subprocess would hide more than it reports. Timing out IS a failure - it
    # is recorded as one - it just stops being a failure of the whole run.
    try:
        result = subprocess.run(
            [PYTHON, script_path],
            cwd=BACKEND_DIR,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        print(f"\n  [{FAIL}] {script} - TIMED OUT after 300s")
        script_results.append((script, False))
        continue
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
# tests/ and verify/ are EXCLUDED: these checks assert properties of the
# APPLICATION. A fixture that fakes an upstream error body, or a verify script
# quoting the pattern it looks for, is not a privacy leak - and letting them
# turn the suite red is how a gate stops being read.
for root, dirs, files in os.walk(BACKEND_DIR):
    dirs[:] = [d for d in dirs
               if d not in (".venv", "__pycache__", "avatars", "tests", "verify")]
    for f in files:
        if f.endswith(".py"):
            py_files.append(os.path.join(root, f))


def grep_backend(
    pattern: str, case_insensitive: bool = False, skip_prose: bool = False,
) -> list[tuple[str, int, str]]:
    """Search backend .py files for pattern. Returns (file, lineno, line) hits.

    skip_prose drops comment and docstring lines. A sentence EXPLAINING a
    decision is not the decision - and without this, the only way to keep a
    check green is to stop documenting why, which is a bad trade for a suite
    whose whole purpose is to be read.
    """
    flags = re.IGNORECASE if case_insensitive else 0
    regex = re.compile(pattern, flags)
    hits = []
    for fp in py_files:
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                in_doc = False
                for i, line in enumerate(fh, 1):
                    prose = False
                    if skip_prose:
                        stripped = line.strip()
                        prose = in_doc or stripped.startswith("#")
                        # Triple quotes opening and closing on the same line
                        # cancel out, so counting them tracks the state.
                        ticks = line.count('"""') + line.count("'''")
                        if ticks % 2 == 1:
                            prose = True
                            in_doc = not in_doc
                    if not prose and regex.search(line):
                        hits.append((os.path.basename(fp), i, line.strip()))
        except Exception:
            pass
    return hits


def privacy_check(pid: str, desc: str, pattern: str,
                  expect_absent: bool = True,
                  case_insensitive: bool = False,
                  exclude_files: set[str] | None = None,
                  skip_comments: bool = False,
                  exclude_lines: tuple[str, ...] = ()) -> bool:
    """Run a privacy grep check. Returns True if check passes.

    exclude_lines exempts individual LINES by substring, for the case where a
    whole-file exemption would be too coarse: a rule about what this app SENDS
    should not be switched off for a file merely because that file also reads
    something the provider sent us. Every entry must be justified at its use.
    """
    hits = grep_backend(pattern, case_insensitive, skip_prose=skip_comments)
    if exclude_files:
        hits = [(f, l, c) for f, l, c in hits if f not in exclude_files]
    if exclude_lines:
        hits = [(f, l, c) for f, l, c in hits
                if not any(frag in c for frag in exclude_lines)]
    ok = (len(hits) == 0) if expect_absent else (len(hits) > 0)
    tag = PASS if ok else FAIL
    detail = ""
    if not ok and hits:
        detail = f"  found in: {', '.join(f'{f}:{l}' for f, l, _ in hits[:5])}"
    print(f"  [{tag}] {pid}  {desc}{detail}")
    privacy_results.append((f"{pid} {desc}", ok))
    return ok


# Allow verify scripts + docstrings/comments to mention these patterns
# Belt and braces: the walk above already drops the whole verify/ directory,
# so nothing here can reach py_files today. Kept - and derived rather than
# typed - because it is the check that survives if that exclusion is ever
# narrowed, and because the hand-written version had gone stale in both
# directions at once: it named verify_part_f.py, which has never existed, and
# omitted verify_phase4.py and verify_tts_latency.py, which do.
VERIFY_FILES = {
    os.path.basename(p)
    for p in glob.glob(os.path.join(VERIFY_DIR, "verify_*.py"))
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

# P-03: image_url is CONFINED to the one module allowed to build it.
# Vision attachments are a shipped feature, so "never" is no longer the rule -
# "only here" is. attachments_service.build_image_part is the single place, and
# an image_url literal anywhere else is a second path that neither the
# attachment gate nor the model-capability rule (_model_accepts_images) covers.
# Pattern requires quotes around image_url to match code usage, not docstrings.
#
# AMENDED, consciously, when generated image output shipped. The check is about
# the OUTBOUND direction: what this app puts INTO a provider payload. Generated
# images arrive the other way round - `choices[].message.images[].image_url.url`
# is something the provider sent US - and that has to be read somewhere.
# openrouter.image_urls_from is the single reader, it CONSTRUCTS nothing, and the
# rule it upholds is the mirror of this one: one place, and only one.
#
# Weakening the pattern to "image_url outside these two files" would have made
# the check unable to see a new outbound builder in openrouter.py, so the
# exemption is scoped to the one function name instead of the whole file.
privacy_check("P-03", "image_url built only in attachments_service",
              r'["\']image_url["\']',
              exclude_files=VERIFY_FILES | {"attachments_service.py"},
              exclude_lines=('entry.get("image_url")',))

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

# P-07: SSE streaming is a shipped feature, so this is a CONFINEMENT check -
# the outbound `stream: true` belongs to openrouter.complete_stream and nowhere
# else. A second construction site would be a request path that skips the
# provider policy assembled around it.
hits_p07 = [h for h in grep_backend(r'"stream"\s*:\s*[Tt]rue')
            if h[0] != "openrouter.py"]
ok_p07 = len(hits_p07) == 0
tag_p07 = PASS if ok_p07 else FAIL
detail_p07 = ""
if not ok_p07:
    detail_p07 = "  found in: " + ", ".join(f"{f}:{l}" for f, l, _ in hits_p07[:5])
print(f"  [{tag_p07}] P-07  stream:true only in openrouter.py{detail_p07}")
privacy_results.append(("P-07 stream:true only in openrouter.py", ok_p07))

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
# Matched against an actual BIND rather than the bare address: run_app.py reads
# the WebView2 runtime's registry version, where "0.0.0.0" means "not
# installed" and has nothing to do with a listening socket.
privacy_check("P-14", "no 0.0.0.0 binding",
              r'(?:bind|host|HOST)\s*[=(]\s*\(?\s*["\']0\.0\.0\.0["\']',
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
              exclude_files=VERIFY_FILES,
              skip_comments=True)

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
