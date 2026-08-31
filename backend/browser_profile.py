"""browser_profile.py - empty the browser's own disk between sessions.

The encrypted vault has a blind spot, and it is not the vault's fault.

run_app.py runs WebView2 with a PERSISTENT profile (private_mode=False) so the
wallpaper, the font size and the last-open chat survive a restart. A persistent
Chromium profile is free to write any cacheable response body to disk - and the
/api routes carry the whole conversation, the character cards and the personas.
They landed in %LOCALAPPDATA%\\Elysium\\webview\\EBWebView\\Default\\Cache as
plain JSON: readable in a text editor, no passphrase, no crypto, surviving
every vault lock. An attacker holding the disk never has to touch app.db.

main.py's no_store_api closed the tap. It did not empty the bucket - entries
written before that middleware existed stayed. This module empties the bucket,
at launch and again at exit, so that one session's cache never outlives it.

Deliberately narrow, in both directions:

  KEPT - "Code Cache", the shader caches and the GPU caches hold compiled
  bytecode for OUR OWN bundle, not response bodies. Wiping them costs startup
  time on every launch and protects nothing.

  KEPT - "Local Storage", "IndexedDB" and "WebStorage" hold the cosmetic
  scalars and the wallpaper that the persistent profile exists for. Wiping
  those would delete the user's settings, which is not a security fix, it is
  data loss.

  REMOVED - everything that can hold a response body, a URL history or a
  session: the HTTP cache, the network state, blob storage, the service worker
  store, the history databases, and the crash reports (a renderer minidump
  contains the decrypted DOM).
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

# One deletion primitive for the whole app: the vault discards a plaintext
# backup through the same function. Two copies of a shred would drift.
from secure_delete import (is_redirected as _is_redirected,
                           shred as _shred, shred_tree as _shred_tree)

log = logging.getLogger(__name__)

#: Per-profile directories that can hold response bodies, URLs or sessions.
_BODY_DIRS: tuple[str, ...] = (
    "Cache",
    "Network",
    "Service Worker",
    "blob_storage",
    "Session Storage",
    "Sessions",
    "EdgeSessions",
    "EdgeJourneys",
    "Shared Dictionary",
)

#: Per-profile single files in the same category.
_BODY_FILES: tuple[str, ...] = (
    "History",
    "History-journal",
    "DIPS",
    "Top Sites",
    "Top Sites-journal",
    "Favicons",
    "Favicons-journal",
    "Network Action Predictor",
    "Network Action Predictor-journal",
    "SharedStorage",
    "favorites_diagnostic.log",
)

#: Under EBWebView/Crashpad. A renderer minidump holds the decrypted DOM, and
#: WebView2 uploads crash reports to Microsoft by default - so these are both
#: data at rest AND the residue of an egress this app never asked for.
_CRASH_DIRS: tuple[str, ...] = ("reports", "attachments")


def _profile_dirs(root: Path) -> list[Path]:
    """The browser profiles under a pywebview storage_path.

    WebView2 nests everything under EBWebView and names the first profile
    "Default"; additional ones are "Profile 1", "Profile 2". Matching by name
    rather than assuming "Default" means a profile Chromium invents later is
    still swept instead of silently retaining conversation.
    """
    ebwebview = root / "EBWebView"
    if not ebwebview.is_dir() or _is_redirected(ebwebview):
        return []
    try:
        entries = sorted(ebwebview.iterdir())
    except OSError:
        return []
    return [
        entry for entry in entries
        if entry.is_dir()
        and not _is_redirected(entry)
        and (entry.name == "Default" or entry.name.startswith("Profile "))
    ]


def block_crash_reporting(storage_path: Path | str) -> bool:
    """Leave Crashpad nowhere to write, so it never starts.

    The renderer process holds the decrypted conversation in its DOM. If it
    crashes, Crashpad writes a minidump of that memory and uploads it to
    Microsoft - a second egress this app never asked for, outside the vault
    and outside the one-host promise.

    Microsoft's own switch for this is IsCustomCrashReportingEnabled, and it
    lives on CoreWebView2EnvironmentOptions. pywebview builds the control from
    CoreWebView2CreationProperties, which does not expose it, so it cannot be
    reached without rebuilding the environment by hand.

    So this closes it from the filesystem instead, and the choice is measured
    rather than assumed. Three approaches were tried against a real WebView2:

        --disable-breakpad            handler still started, database created
        --crash-dumps-dir elsewhere   handler still started, database created
        the path occupied by a file   handler never started, nothing created

    Only the last one works. Crashpad has to create <profile>/Crashpad as a
    directory; a plain file sitting on that name makes CreateDirectory fail
    and the handler is never launched. The window still loads normally - that
    was checked, not hoped.

    Called before every launch, because a guarantee that depends on a file
    nobody re-checks is not a guarantee. Returns whether the block is in
    place; never raises, since failing to start is worse than the risk.
    """
    root = Path(storage_path)
    blocker = root / "EBWebView" / "Crashpad"
    try:
        # The PARENT, and BEFORE the mkdir.
        #
        # The check below catches a junction on `Crashpad` itself, and that
        # was the whole defence. `mkdir(exist_ok=True)` on an EBWebView that
        # is already a junction succeeds quietly, so everything after it -
        # the shred, the rmtree, the touch - was operating inside somebody
        # else's directory, reached through a name this app created.
        #
        # `is_dir()` first, and it is load-bearing: `_is_redirected` fails
        # CLOSED on any OSError, ENOENT included, so a profile that does not
        # exist yet answers True. Written without this gate the guard would
        # refuse every first launch. Same pattern as `_profile_dirs` above
        # and `host.py`'s cache trim.
        if blocker.parent.is_dir() and _is_redirected(blocker.parent):
            log.warning("browser_profile: the profile parent is a redirected "
                        "name - crash reporting was not blocked")
            return False
        blocker.parent.mkdir(parents=True, exist_ok=True)
        if blocker.is_file():
            return True
        if blocker.is_dir() and not _is_redirected(blocker):
            # An existing profile has a real Crashpad database here. Its
            # contents are the exact thing being defended against, so shred
            # before replacing rather than merely unlinking.
            # Same walk, same prune, same reporting as purge(): a renderer
            # minidump in here holds the decrypted DOM, and this was the last
            # hand-rolled loop that threw away what it could not destroy.
            _, left, _pruned = _shred_tree(blocker)
            if left:
                log.warning("browser_profile: %d crash file(s) could not be "
                            "deleted: %s", len(left), ", ".join(left[:5]))
            shutil.rmtree(blocker, ignore_errors=True)
        if blocker.exists():
            return False  # a junction, or a directory that refused to go
        blocker.touch()
        log.info("browser_profile: crash reporting blocked")
        return True
    except OSError:
        return False


def purge(storage_path: Path | str) -> int:
    """Shred every cached response body under a WebView2 storage_path.

    Returns the number of files removed, so a caller can log it and a test can
    tell "nothing was there" from "nothing was done".

    Never raises. This runs on the launch path, where an exception would mean
    the user cannot open the app at all - a far worse outcome than a cache
    entry that survives one more session.
    """
    root = Path(storage_path)
    if not root.is_dir():
        return 0

    removed = 0
    stuck: list[str] = []

    def sweep_dir(target: Path) -> None:
        nonlocal removed
        if not target.is_dir():
            return
        # The walk, the junction prune and the per-file shred all live in
        # secure_delete now. They were written here first and then written
        # again, badly, at a second call site - which is the argument for one
        # copy rather than three.
        gone, left, pruned = _shred_tree(target)
        removed += gone
        # Every refusal, not just the redirected ones. A name ending in a dot
        # or space, or a reserved device name, cannot be opened through the
        # ordinary Win32 path - so the shred failed and the file stayed on
        # disk with nobody counting it. Silence is what made it a problem.
        stuck.extend(left)
        if pruned:
            # rmtree would walk straight into what was just pruned, so the
            # empty directories stay. Leaving a few empty folders behind is
            # the cheap half of this trade; the expensive half was deleting
            # somebody else's files.
            return
        # Whatever could not be shredded stays; ignore_errors keeps a locked
        # file from aborting the sweep of everything else.
        shutil.rmtree(target, ignore_errors=True)

    for profile in _profile_dirs(root):
        for name in _BODY_DIRS:
            sweep_dir(profile / name)
        for name in _BODY_FILES:
            candidate = profile / name
            if candidate.is_file() and _shred(candidate):
                removed += 1

    crashpad = root / "EBWebView" / "Crashpad"
    for name in _CRASH_DIRS:
        sweep_dir(crashpad / name)

    if removed:
        log.info("browser_profile: purged %d cached files", removed)
    if stuck:
        log.warning(
            "browser_profile: %d cached file(s) could not be deleted and are "
            "still readable on disk: %s", len(stuck), ", ".join(stuck[:5]))
    return removed
