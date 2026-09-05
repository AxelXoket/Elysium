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
import re
import shutil
import stat
import time
from pathlib import Path

# One deletion primitive for the whole app: the vault discards a plaintext
# backup through the same function. Two copies of a shred would drift.
from secure_delete import (confirmed_reparse as _confirmed_reparse,
                           is_redirected as _is_redirected,
                           shred as _shred, shred_tree as _shred_tree)

log = logging.getLogger(__name__)

#: What this module says out loud when it removes a redirected NAME. Named
#: here rather than typed at the log call because these are the only record a
#: user gets of a deletion they did not ask for, and a test that retypes the
#: sentence to check it is checking its own typing. One argument each, so a
#: caller comparing against the formatted line cannot drift on the second.
LINK_ENTRY_REMOVED = (
    "browser_profile: %s was a redirected name inside this app's own profile "
    "- the link was removed"
)
LINK_TARGET_KEPT = (
    "browser_profile: it pointed at %s, which was NOT deleted"
)
PROFILE_LINK_REMOVED = (
    "browser_profile: the WebView2 profile name %s was a link - it was "
    "removed and the profile will be rebuilt on the default disk; if you "
    "moved it there on purpose, nothing at the other end was deleted"
)
LINK_ENTRY_STUCK = (
    "browser_profile: %s is a redirected name that could not be removed - it "
    "is still on disk and the folder under it was not swept"
)
MINIDUMP_SHREDDED = (
    "browser_profile: %s was a crash minidump this app is responsible for, "
    "written behind a redirected name - it was overwritten and deleted"
)
SWEEP_HELD_BACK = (
    "browser_profile: %s was left in place - something under it could not be "
    "overwritten, and unlinking it unread would leave the bytes recoverable"
)
STILL_READABLE = (
    "browser_profile: %d cached file(s) could not be deleted and are still "
    "readable on disk: %s"
)
SCAN_CUT_SHORT = (
    "browser_profile: stopped looking for our own crash dumps under %s - the "
    "folder is too large to search on the launch path"
)

#: Crashpad names a finished report after the UUID it minted for it, and
#: writes the minidump format's own magic number at byte zero.
_CRASHPAD_REPORT = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\.dmp\Z")
_MINIDUMP_MAGIC = b"MDMP"

#: Bounds on the search described in _shred_our_minidumps. The folder on the
#: far side of a link is chosen by whoever made the link, so it can be a drive
#: root; this runs on the launch path and is not allowed to walk one.
#:
#: All three are needed and the first two alone were not enough. MEASURED: a
#: target holding 20 000 empty directories took 4.23 seconds while the file
#: counter never moved, because breadth costs nothing in files. So directories
#: count against the same budget, and a wall clock ends the search whatever
#: the shape of the tree - the budget is per LAUNCH, not per call, so nine
#: swept names cannot multiply it by nine.
_SCAN_DEPTH = 4
_SCAN_ENTRIES = 4000
_SCAN_SECONDS = 2.0

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


def _created(path: Path, follow: bool = True) -> float:
    """When a name came into being, as a timestamp.

    Used twice: for this app's own profile folder, and - with `follow` off -
    for a link ENTRY itself, which is a much tighter floor because nothing we
    wrote through a link can be older than the link.

    Fails CLOSED by answering infinity: if the age cannot be read, nothing on
    the far side of a link is young enough to qualify and nothing out there
    is touched.
    """
    try:
        info = os.stat(path) if follow else os.lstat(path)
    except OSError:
        return float("inf")
    born = getattr(info, "st_birthtime", None)  # Windows and macOS
    return info.st_ctime if born is None else born


def _is_our_minidump(path: Path, name: str, not_before: float) -> bool:
    """Three independent signs, all three required, and none of them alone.

    This is the ONE thing this module reaches for through a redirected name,
    so the reasoning belongs next to it rather than in a design note.

    A renderer minidump is a photograph of the decrypted DOM - the entire
    conversation, in the clear. If Crashpad wrote one before the link was
    found, it is ours and this app is responsible for it. Leaving it because
    it happens to sit on the far side of a link would be the cleanup
    declining to clean up after itself.

    But deleting on a HUNCH points this shredder wherever an attacker likes.
    That is the classic confused deputy: they cannot delete the user's files,
    we can, so they arrange for us to want to. A `.dmp` suffix is not a
    secret. Neither is the MDMP magic number - four bytes anyone can write
    into a file named whatever they choose, in a folder they then junction
    into this profile. Either sign ALONE turns a privacy feature into a
    remote delete button, and secure_delete's own docstring records that a
    weaker version of exactly this attack was built and worked.

    So all three have to hold at once:

      (i)   the name is a Crashpad report id - a UUID and nothing else;
      (ii)  the first four bytes really are the minidump magic number, so a
            file merely NAMED like one is not enough;
      (iii) it was written after BOTH this app's profile folder existed and
            the link itself was created, so it can causally have been written
            by us through that link.

    Sign (iii) carries the `not_before` its caller computed, and the link's
    own birth time is the half that does the work. MEASURED with only the
    profile root's date: that is the install date, months old in the field,
    so every minidump on the machine passed and the predicate collapsed to
    "UUID name plus MDMP magic" - which is exactly and only what any Chromium
    application writes. A junction aimed at another browser's crash database
    then had this function destroy five of its dumps. Nothing we wrote
    through a link can be older than the link, so that is the honest floor.

    Cheapest and least invasive first, on purpose: the name is checked before
    anything is opened, so an ordinary document in the folder at the far end
    is never read at all.

    Named limit, stated rather than hidden: an attacker who knows all the
    signs can still satisfy all of them. What that buys them is the deletion
    of a file they had to author, name with a UUID, stamp with MDMP and write
    AFTER planting the link - a file they already control - not the deletion
    of a file the user cared about.
    """
    if not _CRASHPAD_REPORT.match(name):
        return False
    try:
        if os.stat(path).st_mtime < not_before:
            return False
    except OSError:
        return False
    try:
        with open(path, "rb") as handle:
            head = handle.read(len(_MINIDUMP_MAGIC))
    except OSError:
        return False
    return head == _MINIDUMP_MAGIC


def _shred_our_minidumps(target: str, not_before: float) -> int:
    """Destroy the crash dumps WE wrote at the far end of a link. Nothing else.

    Bounded three ways, because the folder is chosen by whoever made the link
    and this runs before the window opens: no deeper than _SCAN_DEPTH, no
    more than _SCAN_ENTRIES directories AND files together, no longer than
    _SCAN_SECONDS, and never through a second reparse point found on the way.
    """
    seen = 0
    gone = 0
    deadline = time.monotonic() + _SCAN_SECONDS
    base = target.replace("/", os.sep).rstrip(os.sep).count(os.sep)
    for current, subdirs, files in os.walk(target):
        # Directories count too. Breadth is free in files and is not free in
        # time, so a counter that only sees files does not bound anything.
        seen += 1 + len(subdirs)
        if seen > _SCAN_ENTRIES or time.monotonic() > deadline:
            log.warning(SCAN_CUT_SHORT, target)
            return gone
        if current.count(os.sep) - base >= _SCAN_DEPTH:
            subdirs[:] = []
        else:
            subdirs[:] = [name for name in subdirs
                          if not _is_redirected(Path(current) / name)]
        for name in files:
            seen += 1
            if seen > _SCAN_ENTRIES:
                log.warning(SCAN_CUT_SHORT, target)
                return gone
            path = Path(current) / name
            if _is_our_minidump(path, name, not_before) and _shred(path):
                gone += 1
                log.warning(MINIDUMP_SHREDDED, _readable(path))
    return gone


def _readable(path: Path | str) -> str:
    """The same path with the extended-length prefix off, for the LOG only.

    Never fed back to the filesystem. Taking the prefix off a path puts Win32
    normalisation back in front of it, which is the exact lie this module
    stopped believing - but a user reading a warning about a file that was
    destroyed should see the path they would type, not the kernel spelling.
    """
    text = os.fspath(path)
    return text[4:] if text.startswith("\\\\?\\") else text


def _link_target(entry: Path) -> str:
    """Where a link points, exactly as the filesystem spells it.

    The extended-length prefix is deliberately NOT stripped. Taking it off
    would put Win32 normalisation back in front of the path this function's
    caller is about to walk, and the whole reason the prefix is there is that
    normalisation lies about names ending in a space or a dot.
    """
    try:
        return os.readlink(entry)
    except (OSError, ValueError):
        return ""


def _unlink_entry(entry: Path) -> bool:
    """Remove the NAME, never what it leads to. Refuses anything but a link.

    The reparse check is repeated HERE rather than trusted from the caller,
    and it is not defensive decoration. Between `confirmed_reparse` and this
    call the app walks the far side of the link, which on a wide target takes
    seconds - and the name in between belongs to whoever can write to that
    directory, which on this app's threat model is any code running as this
    user. MEASURED: with a 20 000-directory target the swap won on the first
    attempt and an ordinary file was unlinked with no overwrite at all. One
    `lstat`, and the decision and the removal are made from the same answer.

    `shutil.rmtree` cannot stand in for this: it raises "Cannot call rmtree on
    a symbolic link", and with `ignore_errors=True` that refusal is a silent
    no-op - which is how the trap became permanent in the first place.

    MEASURED, and stated because the obvious reading is wrong: on this
    interpreter `os.unlink` will also remove a directory junction, so the
    ordering below is not a correctness requirement. It is kept because it
    asks for the call that matches what the attribute says the entry is, and
    falls back rather than leaving the name standing on a wrong guess.
    """
    try:
        attributes = getattr(os.lstat(entry), "st_file_attributes", None)
    except OSError:
        return False
    if attributes is None:
        if not Path(entry).is_symlink():  # non-Windows
            return False
        is_directory = Path(entry).is_dir()
    else:
        if not attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            log.warning(LINK_ENTRY_STUCK, entry)
            return False
        is_directory = bool(attributes & stat.FILE_ATTRIBUTE_DIRECTORY)
    order = (os.rmdir, os.unlink) if is_directory else (os.unlink, os.rmdir)
    for remove in order:
        try:
            remove(entry)
            return True
        except OSError:
            continue
    return False


def _remove_link_entry(entry: Path, born: float,
                       note: str = LINK_ENTRY_REMOVED,
                       scan: bool = False) -> tuple[bool, int]:
    """Take a confirmed reparse point out of this app's own profile.

    Returns (removed, shredded): whether the entry went, and how many crash
    dumps of ours were destroyed at the far end on the way.

    Fails OPEN, and that is the whole difference between this and the guard
    it replaces. `_is_redirected` fails CLOSED - a missing name, or one Win32
    cannot spell, answers True - which is right for "may I walk in there" and
    catastrophic for "may I delete this". `confirmed_reparse` answers True
    only when the reparse attribute was actually read, so a name this cannot
    identify is reported and left exactly where it is.

    `scan` is off by default and that default is a security boundary, not a
    performance one. The far side is only searched at the positions where
    Crashpad could actually have written through the link - EBWebView and the
    Crashpad database under it. MEASURED before this argument existed: a
    junction on `Default/Cache`, where Crashpad never writes at all, reached
    another browser's crash database two directories down and destroyed five
    of its dumps. A name we have no reason to have written to gets no search.

    The second half of that fix is `not_before`. The profile root's birth date
    is the install date, months old in the field, so on its own sign (iii)
    passes essentially every minidump on the machine. A file we wrote THROUGH
    this link cannot predate the link itself, so the link's own creation time
    is the honest floor and it is not a heuristic - it is a causal necessity.
    """
    if not _confirmed_reparse(entry):
        return False, 0
    target = _link_target(entry)
    shredded = 0
    if target and scan:
        # Before the name goes, because the name is how we find the far end.
        shredded = _shred_our_minidumps(
            target, max(born, _created(entry, follow=False)))
    if not _unlink_entry(entry):
        log.warning(LINK_ENTRY_STUCK, entry)
        return False, shredded
    log.warning(note, entry)
    if target:
        log.warning(LINK_TARGET_KEPT, _readable(target))
    return True, shredded


def _sweep_tree(target: Path, born: float,
                scan: bool = False) -> tuple[int, list[str]]:
    """Shred a directory and then remove it, in the order that stays safe.

    Returns (removed, held): how many files were destroyed, and the names
    that stopped the directory itself from going.

    Four steps, and each one exists because of a measured failure:

      1. The swept NAME itself may be the link. `shutil.rmtree` is a silent
         no-op at this position, so only an explicit removal clears it.
      2. `shred_tree` reports the redirected names it refused to walk into.
         The confirmed ones are removed - entry only.
      3. A SECOND `shred_tree`. This is not belt and braces: `rmtree` unlinks
         without overwriting, so it may only ever be handed a tree that
         `shred_tree` has just walked from end to end. Anything the first
         pass skipped has to be re-walked before the plain unlink is allowed
         near it. It also closes the window between the two: a link posted
         after the first pass is pruned by the second, and `rmtree` does not
         run at all.
      4. `rmtree` only on a completely clean second pass. If anything was
         stuck or still pruned, the folder stays and is named out loud -
         deleting it would unlink files whose bytes were never replaced, and
         the next launch can try again.

    The second pass is skipped when the first found nothing to prune, because
    then the first pass IS the end-to-end walk that step 3 exists to
    guarantee - and this runs twice per session on a real cache.
    """
    gone_entry, removed = _remove_link_entry(target, born, scan=scan)
    if gone_entry:
        return removed, []
    if not target.is_dir():
        return 0, []
    gone, stuck, pruned = _shred_tree(target)
    removed += gone
    if pruned:
        for name in pruned:
            _, shredded = _remove_link_entry(Path(name), born, scan=scan)
            removed += shredded
        gone, later_stuck, pruned = _shred_tree(target)
        removed += gone
        # UNION, not replacement. The second pass re-walks the same tree, so
        # a name that refused the first time refuses again and would be
        # counted twice by a plain concatenation - but a name the first pass
        # could not open and the second one could must not vanish from the
        # report just because the two lists disagree.
        stuck = sorted(set(stuck) | set(later_stuck))
    if stuck or pruned:
        log.warning(SWEEP_HELD_BACK, target)
        return removed, stuck + [f"{name} (a redirected name)"
                                 for name in pruned]
    shutil.rmtree(target, ignore_errors=True)
    return removed, []


def _profile_dirs(root: Path) -> tuple[list[Path], int]:
    """The browser profiles under a pywebview storage_path.

    WebView2 nests everything under EBWebView and names the first profile
    "Default"; additional ones are "Profile 1", "Profile 2". Matching by name
    rather than assuming "Default" means a profile Chromium invents later is
    still swept instead of silently retaining conversation.

    A link on EBWebView or on a profile name is removed rather than obeyed,
    and the cost of that is named where the user can read it. MEASURED before
    the change: a junction at either position made `purge` return 0 and
    `block_crash_reporting` return False for the life of the install, so one
    `mklink /J` - no elevation needed - switched this module off for good and
    said so in a warning nobody reads on the launch path. A profile somebody
    moved to another disk on purpose loses the link and is rebuilt on the
    default one; the data at the other end is not touched, and the full path
    goes into the log so the loss is visible rather than mysterious.

    Returns the profiles AND the number of our own crash dumps destroyed on
    the way, because a link on EBWebView is one of the two places Crashpad
    could have written through - and a count this function swallowed would
    make `purge` report zero on the most destructive thing it ever does.
    """
    born = _created(root)
    ebwebview = root / "EBWebView"
    # scan=True here and nowhere else in this function: `EBWebView/Crashpad`
    # is under this name, so a link here IS a place Crashpad wrote. A link on
    # `Default` is not - Crashpad's database is a sibling of the profiles,
    # never inside one - so the far side of that link is not searched at all.
    _, shredded = _remove_link_entry(ebwebview, born, PROFILE_LINK_REMOVED,
                                     scan=True)
    if not ebwebview.is_dir() or _is_redirected(ebwebview):
        return [], shredded
    try:
        entries = sorted(ebwebview.iterdir())
    except OSError:
        return [], shredded
    profiles = []
    for entry in entries:
        # The name filter comes FIRST. A redirected name under EBWebView that
        # is not one of ours is not this function's to remove.
        if not (entry.name == "Default" or entry.name.startswith("Profile ")):
            continue
        if _remove_link_entry(entry, born, PROFILE_LINK_REMOVED)[0]:
            continue
        if entry.is_dir() and not _is_redirected(entry):
            profiles.append(entry)
    return profiles, shredded


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
    born = _created(root)
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
        # Refusing here was only half the answer, and the half that expired.
        # Nothing was written through the link, which was the point and still
        # holds - but the link STAYED, so the block could never be
        # established afterwards either. One unprivileged `mklink /J` on
        # EBWebView and crash reporting was on for the life of the install.
        # The confirmed link is removed instead; see _remove_link_entry for
        # why "confirmed" is not the same test as "redirected".
        #
        # `is_dir()` on the fallback, and it is load-bearing: `_is_redirected`
        # fails CLOSED on any OSError, ENOENT included, so a profile that does
        # not exist yet answers True. Written without this gate the guard
        # would refuse every first launch. Same pattern as `_profile_dirs`
        # above and `host.py`'s cache trim.
        if not _remove_link_entry(blocker.parent, born,
                                  PROFILE_LINK_REMOVED, scan=True)[0]:
            if blocker.parent.is_dir() and _is_redirected(blocker.parent):
                log.warning("browser_profile: the profile parent is a "
                            "redirected name - crash reporting was not "
                            "blocked")
                return False
        blocker.parent.mkdir(parents=True, exist_ok=True)
        # And the Crashpad name itself, before `is_file()` is asked anything.
        # A symlink to a file would answer is_file() True, and this function
        # would report the block in place while the thing occupying the name
        # belongs to somebody else.
        _remove_link_entry(blocker, born, scan=True)
        if blocker.is_file():
            return True
        if blocker.is_dir():
            # An existing profile has a real Crashpad database here. Its
            # contents are the exact thing being defended against, so shred
            # before replacing rather than merely unlinking. Same sweep as
            # purge(), including the verification pass: a renderer minidump
            # in here holds the decrypted DOM, and handing it to `rmtree`
            # unread would leave every byte recoverable.
            _, left = _sweep_tree(blocker, born, scan=True)
            if left:
                log.warning("browser_profile: %d crash file(s) could not be "
                            "deleted: %s", len(left), ", ".join(left[:5]))
        if os.path.lexists(blocker):
            return False  # a directory that refused to go
        blocker.touch()
        log.info("browser_profile: crash reporting blocked")
        return True
    except Exception as exc:                                  # noqa: BLE001
        # Not OSError alone. This runs before the window opens and the
        # docstring promises it never raises; a guard that only catches the
        # errors we thought of keeps that promise only for those. The one
        # thing worse than crash reporting staying on is the app not
        # starting at all.
        # The class name, not the traceback: everything reached from here
        # walks a tree an attacker can shape, so the exception's own message
        # can carry a path that person chose. The class is what diagnoses it.
        log.warning("browser_profile: crash reporting could not be "
                    "blocked (%s)", type(exc).__name__)
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

    born = _created(root)
    removed = 0
    stuck: list[str] = []

    def sweep_dir(target: Path, scan: bool = False) -> None:
        nonlocal removed
        # The walk, the junction prune and the per-file shred all live in
        # secure_delete now. They were written here first and then written
        # again, badly, at a second call site - which is the argument for one
        # copy rather than three. The ORDER around them is _sweep_tree.
        gone, left = _sweep_tree(target, born, scan=scan)
        removed += gone
        # Every refusal, not just the redirected ones. A name ending in a dot
        # or space, or a reserved device name, cannot be opened through the
        # ordinary Win32 path - so the shred failed and the file stayed on
        # disk with nobody counting it. Silence is what made it a problem.
        stuck.extend(left)

    try:
        profiles, shredded = _profile_dirs(root)
        removed += shredded
        for profile in profiles:
            for name in _BODY_DIRS:
                sweep_dir(profile / name)
            for name in _BODY_FILES:
                candidate = profile / name
                if not candidate.is_file():
                    continue
                if _shred(candidate):
                    removed += 1
                else:
                    # The same silence, one loop over. MEASURED: a hardlink
                    # on `History` made `shred` refuse - correctly, since
                    # overwriting would destroy the file the other name opens
                    # - and this loop said nothing at all, so a profile with
                    # its whole browsing history still in the clear reported
                    # a clean purge.
                    stuck.append(candidate.name)

        # The Crashpad NAME itself, before either sweep below reaches through
        # it. `shred_tree` cannot cover this position: `Crashpad/reports`
        # reached THROUGH a junction has an ordinary path of its own, so the
        # prune never fires and the sweep would run inside whatever the link
        # points at. Same reasoning as the per-profile gate, one level down.
        crashpad = root / "EBWebView" / "Crashpad"
        gone_entry, shredded = _remove_link_entry(crashpad, born, scan=True)
        removed += shredded
        if gone_entry:
            pass
        elif crashpad.is_dir() and _is_redirected(crashpad):
            log.warning(LINK_ENTRY_STUCK, crashpad)
            stuck.append(f"{crashpad} (a redirected name)")
        else:
            for name in _CRASH_DIRS:
                sweep_dir(crashpad / name, scan=True)
    except Exception as exc:                                  # noqa: BLE001
        # The docstring promises this never raises and the launch path is why.
        # OSError alone would keep that promise only for the failures already
        # thought of; everything reached from here walks a tree an attacker
        # can shape. Whatever was swept before the failure is still swept, and
        # the count below is still honest about it.
        # Class name only, for the reason named three lines up: the tree is
        # attacker-shaped, so the message can be too.
        log.warning("browser_profile: the sweep stopped early (%s)",
                    type(exc).__name__)

    if removed:
        log.info("browser_profile: purged %d cached files", removed)
    if stuck:
        log.warning(STILL_READABLE, len(stuck), ", ".join(stuck[:5]))
    return removed
