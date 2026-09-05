"""secure_delete.py - the one way this app removes a file it should not keep.

The callers are scattered and they are not neighbours: the browser profile
purge clearing cached conversation, the vault discarding the plaintext database
left by migration, the passphrase rotation removing the OLD salt and verifier,
the voice cache dropping spoken conversation as WAV, the legacy migration
deleting plaintext uploads it has just sealed. Every one of them is removing
decrypted user content, and a copy of this logic per caller would drift.

That list started at two, and the drift the docstring warned about had already
happened: six other places deleted exactly this class of file with a plain
unlink, which leaves the bytes on disk for any undelete tool. The rule now is
that nothing in this app calls unlink on user content directly.

Nothing here raises. Every caller runs on a path where an exception is worse
than the residue: the launch sequence, or a route that must still answer.
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

log = logging.getLogger(__name__)

#: What an I/O failure during the overwrite leaves behind, in the three
#: shapes it really has. shred() answers False for all three, so the return
#: value cannot tell them apart and the sentence is the entire report: the
#: first means every byte is still readable, the second means some of them
#: are, the third means none of them are and only the name is left. They are
#: named here rather than typed at the log call because a reader of that log
#: is deciding whether to go on looking for those bytes, and a test that has
#: to retype the sentence to check it is checking its own typing.
NOT_OPENED = "secure_delete: %s could not be opened - left untouched"
OVERWRITE_STOPPED_SHORT = (
    "secure_delete: %s was overwritten as far as %d of %d bytes before the "
    "write failed - the rest of the content may still be recoverable"
)
OVERWRITTEN_NOT_SYNCED = (
    "secure_delete: %s was fully overwritten but the write could not be "
    "flushed to disk - the content is replaced, the name remains"
)


def _reparse_attribute(path: Path) -> bool | None:
    """Does this NAME carry the reparse-point attribute? None if unknowable.

    Split out because the two callers want opposite answers to "I cannot
    tell": is_redirected() has to fail CLOSED and refuse, while a caller that
    is about to REMOVE the entry has to fail OPEN and leave it alone. Writing
    that as one boolean is how a guard ends up deleting on an error.

    The second attempt is the fix for a measured lie. Win32 path
    normalisation trims trailing spaces and dots from the LAST component, so
    `os.lstat(r"C:\\...\\reports ")` looks for `reports`, does not find it,
    and raises ENOENT - and an ordinary directory with an awkward name came
    back as "redirected". That was not only a wrong log line: shred_tree
    pruned the folder, so the plaintext inside it was never overwritten and
    never counted, and the sweep reported success. The extended-length prefix
    turns normalisation off and answers honestly. `os.path.abspath` cannot
    stand in for it - measured, it trims the space as well - so the raw
    fspath string is what gets prefixed.
    """
    raw = os.fspath(path)
    attempts = [raw]
    if os.name == "nt" and isinstance(raw, str) and not raw.startswith("\\\\?\\"):
        # Only meaningful for an absolute path; a relative one simply fails
        # this second lstat too and the caller keeps its old answer.
        attempts.append("\\\\?\\" + raw)
    for candidate in attempts:
        try:
            info = os.lstat(candidate)
        except (OSError, ValueError):
            continue
        attributes = getattr(info, "st_file_attributes", None)
        if attributes is None:
            return Path(path).is_symlink()  # non-Windows
        return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return None


def confirmed_reparse(path: Path) -> bool:
    """True only when this name IS demonstrably a junction, symlink or mount.

    The mirror image of is_redirected(), and the difference is the whole
    reason it exists. is_redirected() answers "is it safe to delete THROUGH
    this name" and fails closed, so an unreadable name, a missing one and a
    junction all answer True. That is the right default for walking into
    something, and exactly the wrong one for removing the entry: acting on a
    fail-closed True would unlink names on any I/O error at all.

    So this one fails OPEN. Unknown means False means do nothing, and the
    caller reports instead of deleting.
    """
    return _reparse_attribute(path) is True


def is_redirected(path: Path) -> bool:
    """Whether this name leads somewhere else - junction, symlink, mount point.

    This guard exists because the obvious check does not work. On NTFS,
    `os.path.islink()` answers False for a junction while
    `DirEntry.is_dir(follow_symlinks=False)` answers True, so `os.walk`
    descends into one exactly as if it were an ordinary folder - and creating
    a junction needs no administrator rights.

    That made a real attack: any code running as this user replaces a folder
    the app cleans with a junction pointing at the user's documents, and the
    cleanup shreds them. Reproduced before this was written; two files outside
    the profile were overwritten and deleted.

    The reparse-point attribute is the one answer that covers junctions,
    symlinks and mount points alike. A name that fails this check is left
    alone entirely: refusing to delete is always the safe direction.

    Still fails CLOSED, and every caller's `is_dir()` or `lexists()` gate is
    written against that: a path that does not exist answers True. What it no
    longer does is fail closed on a name that is merely awkward to spell -
    see _reparse_attribute for the trailing-space case that was leaving
    plaintext un-shredded and calling it a refused junction.
    """
    answer = _reparse_attribute(path)
    if answer is None:
        return True  # cannot tell what it is, so do not delete through it
    return answer


def is_shared(path: Path) -> bool:
    """Whether these bytes answer to more than one path - a hardlink.

    Distinct from is_redirected() and missed by it: a hardlink is not a
    reparse point, it is a second directory entry pointing at the same inode.
    Nothing about the name says so, and creating one needs no privilege.

    That matters only because this module OVERWRITES before unlinking.
    Unlinking one of two names is harmless; writing random bytes through it
    destroys the file the other name still opens. Reproduced before this
    existed, with a hardlink named like a vault backup aimed at a notes file.

    Counting links is the whole check, and it fails closed: a file whose link
    count cannot be read is treated as shared and left alone.
    """
    try:
        return os.stat(path).st_nlink > 1
    except (AttributeError, OSError):
        return True


def shred(path: Path) -> bool:
    """Overwrite a file's bytes, then unlink it. True if it is gone.

    The overwrite defeats recovery through the file system - undelete tools,
    and anything reading the freed blocks through the same filesystem view.
    It is NOT a guarantee against physical recovery: on an SSD the controller's
    wear levelling may map the write to different blocks and leave the original
    ones readable to firmware-level analysis. Full-disk encryption is the only
    answer to that, and it is the user's to enable.

    Refuses three kinds of name, and this is the single deletion primitive so
    every caller inherits all three:

      * a REDIRECTED name - junction, symlink, mount point;
      * a SHARED name - a hardlink, where the same bytes answer to more than
        one path;
      * a name that stopped meaning the same file between those checks and
        this open - see _still_the_same_file.

    The second was found by trying it. A hardlink is not a reparse point, so
    is_redirected() says False and the overwrite goes through - onto the inode
    both names share. Reproduced: a file called app.db.plain.bak-999 hardlinked
    to somebody's notes left the notes file present and its contents random.
    Unlinking one name would have been harmless; overwriting first is what
    turns a shred into a weapon, and creating a hardlink needs no privilege.

    The third was found the same way, and it is the first two arriving late:
    both of them read the NAME, and the open() below reads it again. Doing that
    attack on purpose - answer honestly, then swap the name - destroyed a notes
    file while this function returned True.
    """
    target = Path(path)
    # The identity anchor is taken FIRST, before either guard, and that
    # ordering is load-bearing rather than tidy. Taking it afterwards was
    # tried and a test caught it: a swap performed during the guards then
    # happened BEFORE the anchor, so the anchor described the attacker's file
    # and the comparison below agreed with itself. The anchor has to predate
    # every other read of this name.
    #
    # lstat, not stat: a name that leads somewhere else must not even
    # contribute an identity, and is_redirected refuses it on the next line.
    try:
        expected = os.lstat(target)
    except OSError:
        return False
    if is_redirected(target):
        log.warning("secure_delete: %s is a redirected name - left alone",
                    target.name)
        return False
    if is_shared(target):
        log.warning("secure_delete: %s has more than one name - left alone",
                    target.name)
        return False

    try:
        with open(target, "r+b", buffering=0) as handle:
            info = os.fstat(handle.fileno())
            if not _still_the_same_file(info, expected):
                log.warning(
                    "secure_delete: %s is not the file that was checked - "
                    "left alone", target.name)
                return False
            # Size from the HANDLE, not the earlier stat: a file that grew
            # between them would otherwise be overwritten only as far as its
            # old length and the rest left readable.
            if info.st_size:
                # K-47. The return value was discarded, and this is a FileIO -
                # RawIOBase.write is allowed to write fewer bytes than it was
                # given and to say so by returning a count, not by raising. A
                # short write left the TAIL of the file un-overwritten, and
                # this function still answered True. Loop until the whole
                # length is covered, and refuse if it stops making progress
                # rather than spinning.
                noise = os.urandom(info.st_size)
                written = 0
                # The write, the flush and the fsync used to sit under the
                # outer handler with the open, and that handler says the file
                # was left untouched. A full disk, removed media or an I/O
                # error arriving mid-loop therefore told the user nothing had
                # happened to a file that had just been half destroyed - the
                # one direction this module must never be wrong in. The three
                # of them answer for themselves here. The contract does not
                # move: False still means "not destroyed". Only the sentence
                # changes, and it now depends on how far the loop got.
                try:
                    while written < len(noise):
                        step = handle.write(noise[written:])
                        if not step:
                            log.warning(
                                "secure_delete: %s could not be fully "
                                "overwritten - left in place rather than "
                                "unlinked",
                                target.name)
                            return False
                        written += step
                    handle.flush()
                    os.fsync(handle.fileno())
                    # Closed HERE rather than by the `with` on the way out,
                    # and only because of where the two closes land: a close
                    # that fails after a finished overwrite is the same event
                    # as a flush that fails, and outside this block it would
                    # be reported as "left untouched" again. Closing twice is
                    # harmless; being wrong about it is not.
                    handle.close()
                except OSError:
                    if written == len(noise):
                        log.warning(OVERWRITTEN_NOT_SYNCED, target.name)
                    else:
                        log.warning(OVERWRITE_STOPPED_SHORT,
                                    target.name, written, len(noise))
                    return False
        try:
            target.unlink()
        except OSError:
            # K-48. The bytes ARE gone - the overwrite above finished and was
            # fsynced. Only the name is left. Returning False here made every
            # caller report "still readable on disk", which was the opposite
            # of the truth and sent the user looking for a file whose content
            # no longer exists. Say what actually happened.
            log.warning(
                "secure_delete: %s was overwritten but its name could not be "
                "removed - the content is destroyed, the entry remains",
                target.name)
            return False
        return True
    except OSError:
        # Nothing was destroyed on this path, and that is true again rather
        # than merely written down: every guard above returns early, the
        # overwrite answers for itself now, and what is left able to raise
        # here is the open, the fstat beside it, and the close of a file that
        # had no bytes to overwrite.
        log.warning(NOT_OPENED, target.name)
        return False


def _still_the_same_file(info: os.stat_result, expected: os.stat_result) -> bool:
    """Whether an open handle points at the file the guards above approved.

    Both guards read the NAME, and then open() reads it again. Between those
    two reads the name belongs to whoever can write to that directory, which
    on this app's own threat model is any code running as this user - the same
    assumption is_redirected() and is_shared() were written under.

    Reproduced before this was written, against the real module: is_shared()
    answered honestly about the cache file, the name was then swapped for a
    hardlink to a notes file, and shred() overwrote the notes and returned
    True. The guards were not wrong; they were early.

    Asking the handle is what fixes it, because a handle cannot be swapped.
    On Windows os.fstat fills st_dev, st_ino and st_nlink from
    GetFileInformationByHandle, so the identity of the OPEN file and its link
    count both come from the object we are about to write to rather than from
    a name that may since have moved. ctypes was the other candidate and buys
    nothing here: it is the same call, reached through more code, inside the
    one primitive whose quiet failure leaves user content on disk.

    Known limit, stated rather than hidden: on a filesystem that reports no
    inode number at all both sides compare equal and the identity half of this
    is vacuous. That is exactly the behaviour this module had before, so it is
    a floor and not a regression - and the link-count half still holds there,
    which is the half the reproduced attack tripped.
    """
    if info.st_nlink > 1:
        return False
    return (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino)


def shred_tree(root: Path) -> tuple[int, list[str], list[str]]:
    """Shred every file under a directory, refusing to leave it.

    Returns (removed, stuck, pruned): how many files were destroyed, the names
    of those that could not be, and the FULL PATHS of the redirected names
    that were found and skipped.

    That third value used to be a bare bool, and the bool was the reason the
    sweep could never finish. Every caller wanted to know whether to run
    `shutil.rmtree` afterwards, and answering only yes-or-no left them with
    one option: stop. Naming the paths lets a caller that is entitled to -
    browser_profile, over names this app created - remove the ENTRY and try
    again. Nothing here does that; this function still only refuses.

    The shape is deliberately falsy-compatible: an empty list reads the same
    as the old False at the four call sites that only ask `if pruned:`.

    The pruning is the whole point and it cannot be delegated. os.walk has a
    followlinks flag, and it does not help: a junction is not reported as a
    symlink, so walk descends into one exactly as if it were an ordinary
    subfolder. Checking each FILE is not enough either - a file reached
    THROUGH a junction has an ordinary path of its own, is_redirected() says
    False about it, and the overwrite lands on somebody's documents. Only the
    ancestor is the reparse point, so the ancestor is where the check belongs.

    This exists as one function because it was written twice: browser_profile
    got it right, and a later caller reimplemented the walk without the prune
    and reopened the same hole.
    """
    removed = 0
    stuck: list[str] = []
    pruned: list[str] = []
    target = Path(root)
    redirected = is_redirected(target)
    if not target.is_dir() or redirected:
        # The root ITSELF being redirected is reported as a pruned path too,
        # and it has to be: that is the one position `shutil.rmtree` cannot
        # clear on its own - it raises "Cannot call rmtree on a symbolic
        # link" and `ignore_errors=True` swallows it, so a caller told only
        # "nothing removed" would leave the name standing for ever.
        return 0, [], [str(target)] if redirected else []
    def unlistable(error: OSError) -> None:
        """A folder the walk could not open is a REFUSAL, not an absence.

        `os.walk` swallows these by default, and the silence is the bug.
        MEASURED: a directory named `reports.` - one trailing dot, no
        privilege needed to create through the extended path - cannot be
        opened through the ordinary Win32 path, so `scandir` raises ENOENT,
        the walk yields nothing for it, and the plaintext inside is neither
        shredded nor counted. The sweep then reported a clean tree.

        Recording it in `stuck` is what makes that honest, and it is
        load-bearing for the caller: browser_profile only lets `shutil.rmtree`
        near a tree that came back with nothing stuck, so an unwalkable
        subtree now stops the plain unlink instead of hiding under it.
        """
        name = getattr(error, "filename", None) or str(target)
        try:
            stuck.append(os.path.relpath(name, target))
        except ValueError:
            stuck.append(os.path.basename(str(name)) or str(name))
        log.warning("secure_delete: %s could not be listed - anything under "
                    "it is still on disk", name)

    for current, subdirs, files in os.walk(target, onerror=unlistable):
        keep = []
        for name in subdirs:
            child = Path(current) / name
            if is_redirected(child):
                pruned.append(str(child))
            else:
                keep.append(name)
        if len(keep) != len(subdirs):
            log.warning("secure_delete: redirected folder under %s - "
                        "not followed", current)
        subdirs[:] = keep
        for name in files:
            if shred(Path(current) / name):
                removed += 1
            else:
                stuck.append(name)
    return removed, stuck, pruned


def discard(path: Path) -> bool:
    """shred(), shaped for the callers that were written as missing_ok=True.

    True when the name is gone, INCLUDING when it was never there. shred()
    cannot answer that case usefully: its own guards fail closed, so a missing
    path comes back as "redirected - left alone" with a warning about a file
    that does not exist. Every site converted from unlink(missing_ok=True)
    wants this shape, and giving them each their own exists() check is how the
    guards get forgotten one call site at a time.

    lexists, not exists: a broken junction is a name that IS there, and it is
    exactly the name shred() must refuse rather than follow.

    The absence has to be checked the same way _reparse_attribute checks the
    name, and for the same measured reason: `os.path.lexists` goes through
    Win32 normalisation, which trims a trailing space or dot from the last
    component. A file actually called `backup.` therefore reports absent, and
    this would answer "gone" about a file still sitting on disk in the clear.
    """
    target = Path(path)
    if _reparse_attribute(target) is None and not os.path.lexists(target):
        # Neither spelling of the name could be stat'ed: it really is absent.
        return True
    return shred(target)
