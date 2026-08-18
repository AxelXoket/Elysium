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
    """
    try:
        return bool(os.lstat(path).st_file_attributes
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return Path(path).is_symlink()  # non-Windows: no st_file_attributes
    except OSError:
        return True  # cannot tell what it is, so do not delete through it


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
                while written < len(noise):
                    step = handle.write(noise[written:])
                    if not step:
                        log.warning(
                            "secure_delete: %s could not be fully overwritten "
                            "- left in place rather than unlinked",
                            target.name)
                        return False
                    written += step
                handle.flush()
                os.fsync(handle.fileno())
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
        # Nothing was destroyed on this path: every guard above returns
        # early, and the only remaining raiser is the open itself.
        log.warning("secure_delete: %s could not be opened - left untouched",
                    target.name)
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


def shred_tree(root: Path) -> tuple[int, list[str], bool]:
    """Shred every file under a directory, refusing to leave it.

    Returns (removed, stuck, pruned): how many files were destroyed, the names
    of those that could not be, and whether any redirected folder was found
    and skipped.

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
    pruned = False
    target = Path(root)
    if not target.is_dir() or is_redirected(target):
        return 0, [], is_redirected(target)
    for current, subdirs, files in os.walk(target):
        keep = [name for name in subdirs
                if not is_redirected(Path(current) / name)]
        if len(keep) != len(subdirs):
            pruned = True
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
    """
    target = Path(path)
    if not os.path.lexists(target):
        return True
    return shred(target)
