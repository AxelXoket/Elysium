"""tts/registry.py - fingerprint a dropped folder, scan the model roots.

Discovery contract:
  * A model is always a DIRECTORY (true for all three engines), never a file.
  * Identification reads only small metadata files - never weights, never torch.
  * Chain: explicit sidecar override  >  content signature  >  unrecognized.
  * A folder the user dropped that we cannot place is REPORTED, not ignored;
    silence would look like the app is broken.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import NamedTuple

import logging

import config

from .adapters import ADAPTERS
from .base import DetectedModel, IdentifyResult, ScanResult, TtsAdapter, UnrecognizedDir
from .runtimes import extra_roots
from .util import read_json

logger = logging.getLogger(__name__)

SIDECAR_NAME = "elysium-model.json"

# HuggingFace cache bookkeeping, skipped outright: not listed, not entered.
# blobs/, refs/, trees/ and .no_exist/ sit next to snapshots/; .locks/ sits at
# the top of the hub and xet/ at the top of HF_HOME. Between them they hold
# content hashes, ref pointers, tree listings, lock files and "this file does
# not exist upstream" markers, never a loadable model. Listing them as
# "unrecognized" put permanent noise in the UI for a perfectly normal cache
# layout, and ENTERING them was how `.no_exist/<rev>` reached the list even
# though `.no_exist` itself was suppressed by name. Measured against a real
# cache on 8 September 2026: 27 rows, 12 of them from these folders. The cost,
# accepted: a model somebody deliberately put INSIDE a folder with one of these
# names is not found (a folder of that name that IS a model is still
# identified, the name check comes after identification). The names are HF's
# own and nobody stores a model there. `.cache` is the folder
# huggingface-cli --local-dir writes INSIDE the model folder
# (.cache/huggingface/download/<file>.metadata and a .gitignore): with the
# root row a fallback, that folder was the row for a downloaded model that
# matched no engine, instead of the model folder itself.
_HF_BOOKKEEPING = frozenset({"blobs", "refs", "trees", ".no_exist", ".locks", "xet", ".cache"})

_BY_ID: dict[str, type[TtsAdapter]] = {a.engine_id: a for a in ADAPTERS}
_ORDERED: tuple[type[TtsAdapter], ...] = tuple(
    sorted(ADAPTERS, key=lambda a: (a.priority, a.engine_id))
)


def adapter_for(engine_id: str) -> type[TtsAdapter] | None:
    return _BY_ID.get(engine_id)


def all_adapters() -> tuple[type[TtsAdapter], ...]:
    return _ORDERED


def identify_dir(model_dir: Path) -> IdentifyResult | None:
    """Identify one directory, or None if nothing claims it."""
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        return None

    # 1. Sidecar override - lets a user rescue a model we guessed wrong.
    side = read_json(model_dir / SIDECAR_NAME)
    if side:
        forced = str(side.get("engine_id") or "")
        adapter = _BY_ID.get(forced)
        if adapter is not None:
            try:
                own = adapter.identify(model_dir)
            except Exception:
                own = None      # same rule as the signature loop below
            if own is not None:
                missing = own.missing
                variant = own.variant
            else:
                # The forced engine does not recognise this folder. Reporting
                # missing=() here would fabricate a "complete, ready to load"
                # model out of an override the files do not support - the user
                # would only find out at load time. Say so instead.
                missing = ("(engine override not corroborated by these files)",)
                variant = None
            return IdentifyResult(adapter.engine_id, "sidecar", variant, missing)
        # A sidecar naming an unknown engine is bad data, not a veto: fall
        # through to the signature chain rather than refusing the folder.

    # 2. Content signature, in priority order.
    for adapter in _ORDERED:
        try:
            res = adapter.identify(model_dir)
        except Exception:
            continue          # a broken adapter must never break the whole scan
        if res is not None:
            return res
    return None


def _uid_for(root: Path, model_dir: Path) -> str:
    """Identity = WHERE the model sits, relative to its scan root.

    Deliberately NOT derived from the engine, the folder name alone, or file
    sizes. Those were tried and each broke a real flow:
      * name + sizes  -> two identically-named copies collided onto one settings
                         row, and finishing a partial download silently changed
                         the id, losing the user's saved values;
      * anything engine-derived -> a manual engine override re-identified the
                         same folder as a different model, orphaning its
                         settings and the active selection.
    A path is unique per folder and survives content edits and re-identification.
    Cost, accepted and documented: moving or renaming the folder is a new model
    and starts from defaults.
    """
    return _digest(_root_key(root), _rel_key(root, model_dir))


def legacy_uid_for(root: Path, model_dir: Path) -> str:
    """The uid this child folder had under an earlier build.

    Kept so a stored selection can be recognised and rewritten once, rather
    than silently resolving to nothing (see model_for_uid in routers/tts).
    Two earlier recipes exist, and which one applies is decided by where the
    folder really is:

      * a folder whose resolved path sits under the resolved root: the uid
        before the root was mixed in, the relative path alone;
      * a folder reached through a junction that leaves the root: every
        build from 1.1.0 to 1.1.6 gave it digest(root, bare folder name),
        the old fallback in _rel_key. Its uid today is by the spelling the
        walk reached it by (see _rel_key), so a 1.1.6 user with
        models\\hf -> D:\\hf and hf\\xtts_v2 selected would otherwise update
        into "tts_model_unknown" with the tuned values orphaned; the legacy
        used to be digest("", spelling), which no build ever produced. Two
        junctions to two folders of one name both carry the same legacy,
        and model_for_uid picks the first, which is the row 1.1.6 loaded.
    """
    rel = _resolved_rel(root, model_dir)
    if rel is None:
        return _digest(_root_key(root), model_dir.name)
    return _digest("", rel)


def _resolved_rel(root: Path, model_dir: Path) -> str | None:
    """The folder's path relative to the root, both resolved, or None when
    the resolved folder is not under the resolved root, which is what a
    junction inside the root pointing outside it looks like."""
    try:
        return model_dir.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _rel_key(root: Path, model_dir: Path) -> str:
    rel = _resolved_rel(root, model_dir)
    if rel is not None:
        return rel
    # The resolved path is not under the resolved root. The fallback here
    # was the bare folder name, so two junctions to two model folders of the
    # same name (c\ext1 -> d1, c\ext2 -> d2, an xtts_v2 in each) were two
    # rows under ONE uid: a duplicate React key, shared settings, and the
    # first row loaded whichever was picked. The spelling the walk reached
    # the folder by is under the root and is unique per folder. A folder
    # whose resolve worked never gets here, so every uid that existed before
    # this fallback is untouched; the one this fallback moved is remembered
    # by legacy_uid_for.
    try:
        return model_dir.relative_to(root).as_posix()
    except ValueError:
        return model_dir.name


def _root_key(root: Path) -> str:
    """Which root this folder was found under.

    Mixed into the uid because the relative path alone is NOT unique across
    roots (audit KÖK 15): runtimes.json carries `extra_roots`, so two roots can
    each hold a folder called `aurora` - verified, one uid for both. _resolve
    then returned whichever came first (loading the wrong model),
    evaluate_all overwrote one verdict with the other, the two shared their
    saved settings, and React rendered a duplicate key.
    """
    try:
        return root.resolve().as_posix()
    except OSError:
        return str(root)


def _digest(root_key: str, rel: str) -> str:
    # Windows paths are case-insensitive; the same folder must not yield two ids.
    raw = f"{root_key}\x00{rel}" if root_key else rel
    return hashlib.sha256(raw.casefold().encode("utf-8")).hexdigest()[:16]


def _has_files(d: Path) -> bool:
    try:
        return any(p.is_file() for p in d.iterdir())
    except OSError:
        return False


def _canonical(p: Path) -> Path:
    """One spelling per folder.

    realpath resolves junctions, symlinks and 8.3 short names for a path that
    exists, and falls back to a lexical answer for one that does not, which is
    the spelling an unreadable root is reported under. It agrees with the
    resolve() inside _root_key, so a root that was already absolute and
    canonical keeps every uid it produced before roots were normalised.
    """
    try:
        return Path(os.path.realpath(p))
    except OSError:
        return Path(os.path.abspath(p))


def _key(p: Path | str) -> str:
    # NTFS is case insensitive; every comparison between roots goes through
    # this so C:\Models and c:\models are one root, not two.
    return os.path.normcase(str(p))


def _inside(child_key: str, parent_key: str) -> bool:
    """Strictly below, by path component: a prefix test on characters would
    put C:\\models-old inside C:\\models."""
    try:
        return (
            child_key != parent_key
            and os.path.commonpath([child_key, parent_key]) == parent_key
        )
    except ValueError:
        return False          # different drives share no path at all


class _PlannedRoot(NamedTuple):
    root: Path
    #: The canonical key of `root`, the spelling every comparison uses.
    key: str
    #: Canonical keys of the roots inside this one; its walk steps around them.
    step_around: frozenset[str]
    #: Canonical keys of every other configured root. A child that resolves
    #: to one of them, or below one, through a junction, is left to that
    #: root's walk (see _stepped_around).
    others: frozenset[str]
    #: The uid this root had as a CHILD of the nearest root above it, or ""
    #: when no root is above it. A root that is itself a model carries it as
    #: its legacy uid (see _legacy_of_root and _detected).
    legacy: str


def _plan_roots(configured: list[Path]) -> list[_PlannedRoot]:
    """Which roots to walk, what each walk must step around, and which root
    sits nearest above each.

    The same folder listed twice used to list one model twice under ONE uid
    (two settings rows, a duplicate React key), and a root inside a wider
    root listed one folder under TWO uids (whichever came first was loaded,
    the other kept the saved settings). The rule that closes both:

      * the same folder twice is one root, the first spelling;
      * two roots that nest are BOTH walked, and a walk steps around every
        other root inside it. So each folder is reached by exactly one walk,
        the one whose root sits nearest above it, and that root is the one
        in its uid. The wider root keeps its uids for everything outside the
        inner root, the inner root keeps its own, and the inner root gets a
        depth budget and a directory budget of its own;
      * with more than one root, a junction can put any folder under any
        root, so every other root's key rides along and a child that
        resolves into another root is stepped around wherever it sits (see
        _stepped_around).

    The inner root used to be dropped instead, on the claim that the outer
    walk reached its folders anyway. It does not once the inner root sits
    deeper than TTS_SCAN_MAX_DEPTH, or past the directory cap of a wide
    outer root: measured on a real hub cache with the limits lowered, the
    pair [hub, hub/models--X/snapshots] found nothing where [snapshots] on
    its own found the model, and nothing said so.

    Nesting is not symmetric for the uids. Adding a root ABOVE the default
    drop folder (or above any root) changes nothing under the inner root:
    the wider walk steps around it, and the inner root's models carry the
    uid they always had. Adding a root INSIDE one moves the folders under
    the new root onto its uid, because the nearest root wins; when the new
    root is itself the model, the uid it had under the wider root rides
    along as its legacy uid (see _legacy_of_root), and model_for_uid
    rewrites a saved selection once instead of losing it. The claim that the drop folder's
    models kept their uid "whichever way round the roots nest" was wrong in
    the second direction, and P3 of the review's probe showed it: a root at
    drop/xtts_v2 re-identified xtts_v2 with no legacy uid at all.
    """
    kept: list[Path] = []
    spellings: list[Path] = []
    keys: list[str] = []
    for raw in configured:
        spelling = Path(os.path.abspath(raw))
        root = _canonical(spelling)
        key = _key(root)
        if key in keys:
            continue
        kept.append(root)
        spellings.append(spelling)
        keys.append(key)
    planned: list[_PlannedRoot] = []
    for i, root in enumerate(kept):
        step_around = frozenset(k for k in keys if _inside(k, keys[i]))
        others = frozenset(k for k in keys if k != keys[i])
        legacy = _legacy_of_root(i, kept, spellings, keys)
        planned.append(_PlannedRoot(root, keys[i], step_around, others, legacy))
    return planned


def _legacy_of_root(
    i: int, kept: list[Path], spellings: list[Path], keys: list[str],
) -> str:
    """The uid root i had yesterday as a child of the nearest root above it,
    "" when no root is above it.

    By canonical key first: the walk of the root above reached this folder
    by its real path, and of the roots this one sits inside the nearest is
    the longest key, since a longer key that contains this root sits deeper
    than a shorter one.

    Then by the configured spelling. A junction ROOT whose target sits
    outside every root but whose spelling sits inside one ([wide,
    wide\\jlink] with jlink -> elsewhere\\model) has a canonical key inside
    nothing, and it carried no legacy at all, although [wide] alone listed
    it: that walk builds every path from wide down, and a folder whose
    resolved path leaves the root gets the spelling relative to the root as
    its rel (see _rel_key), so the uid was digest(wide, "jlink"). The
    spelling is compared against the other root's canonical spelling and its
    configured one, because [jwide, jwide\\jlink] with jwide -> realwide
    walks realwide and lists realwide\\jlink under digest(realwide, "jlink").
    Canonical wins when both apply: with [A, B, B\\jlink] and jlink ->
    A\\model, the walk of B stepped around the junction into A, and A's walk
    listed the folder under A.
    """
    enclosing = [j for j, k in enumerate(keys) if _inside(keys[i], k)]
    if enclosing:
        above = kept[max(enclosing, key=lambda j: len(keys[j]))]
        return _uid_for(above, kept[i])
    spelling_key = _key(spellings[i])
    nearest: tuple[int, str] | None = None        # (root index, base key)
    for j in range(len(kept)):
        if j == i:
            continue
        for base in (keys[j], _key(spellings[j])):
            if _inside(spelling_key, base) and (nearest is None or len(base) > len(nearest[1])):
                nearest = (j, base)
    if nearest is None:
        return ""
    j, base = nearest
    rel = Path(spelling_key).relative_to(base).as_posix()
    return _digest(_root_key(kept[j]), rel)


def _stepped_around(child: Path, plan: _PlannedRoot) -> bool:
    """Is this child of a walk another root's business, left to that walk?

    By spelling first, which is the whole test for an ordinary folder under
    a single root, and free. With more than one root configured every child
    is also compared by where it really is: a junction can put any folder
    under any root, and the walk that went through it reached the folder
    first, listed it under ITS root's uid, and the folder's own root never
    got its row. Measured with real junctions: a link from one root into a
    sibling root, from the drop folder into an extra root, from a root to
    its own parent (which holds the sibling root), and a link into a plain
    folder that holds an inner root one level down, where the child that
    resolves into the root is not the junction itself. Comparing only the
    reparse point by where it goes missed that last shape, so every child
    is compared. Each real folder belongs to the nearest configured root
    above it, whichever walk arrives first, and its uid no longer depends
    on the root order or on a junction in an unrelated root.

    Which roots count as nearer depends on where the child really is:
      * still under this root (an ordinary folder, or a junction back into
        the root): only a root INSIDE this one is nearer, so the roots this
        one sits inside are left out of the pool. Otherwise every child of
        a root that sits inside a wider root would be handed to the wider
        walk, which steps around this root and never lists them;
      * outside this root (a junction that left it): this root does not
        contain the folder at all, so any root that does is nearer, the
        roots above this one included.
    A junction back into this root itself is followed; the first-sighting
    rule in scan_roots keeps that to one row.

    The cost is one realpath per directory, paid only while a second root
    exists; a scan of the drop folder alone pays nothing.
    """
    if _key(child) in plan.step_around:
        return True
    if not plan.others:
        return False
    real = _key(_canonical(child))
    if real == plan.key or _inside(real, plan.key):
        pool = plan.step_around
    else:
        pool = plan.others
    return any(real == k or _inside(real, k) for k in pool)


def _detected(
    root: Path, model_dir: Path, res: IdentifyResult, root_legacy: str = "",
) -> DetectedModel:
    if model_dir != root:
        legacy = legacy_uid_for(root, model_dir)
    else:
        # A root that is itself a model. Inside a wider root, yesterday the
        # wider walk listed it as a child, and a selection saved then names
        # THAT uid, so it rides along here (computed once per root by
        # _legacy_of_root) and model_for_uid rewrites the selection once
        # rather than losing it. Under no wider root there is nothing to be
        # migrated from: no root was ever a model under the old scheme, the
        # walk only looked at children. The empty legacy uid is what keeps
        # model_for_uid from matching two such roots to each other through
        # the "." they share.
        legacy = root_legacy
    return DetectedModel(
        uid=_uid_for(root, model_dir),
        legacy_uid=legacy,
        engine_id=res.engine_id,
        # A drive root has no name; fall back to the path rather than "".
        name=model_dir.name or str(model_dir),
        path=str(model_dir),
        variant=res.variant,
        source=res.source,
        missing=res.missing,
    )


def scan_roots(roots: list[Path] | None = None) -> ScanResult:
    """Walk the model roots and classify every directory.

    Bounded by TTS_SCAN_MAX_DEPTH and TTS_SCAN_MAX_DIRS PER ROOT, so a root
    accidentally pointed at something huge cannot hang the request and cannot
    starve the roots listed after it either: one budget shared by every root
    let a 2000-directory first root leave the second unscanned, measured.

    Every root is looked at in the same order: the root itself first (the
    user may well point extra_roots at the model folder rather than at its
    parent, and that used to find nothing at all), then its children down to
    the depth limit. A root that is a model is that model's row; a root that
    holds files no engine claims is an unrecognised row, the way any child
    folder would be, unless a model or a row turns up below it, by its own
    walk or by another root's (an inner root, or a root reached through a
    junction from under it). The one root never
    judged is the default drop folder: the app defines it as the place model
    FOLDERS go, and treating it as a model when loose files sat in it hid
    every proper subfolder under it, with no row and no flag. A root the
    scan could not open is named in `unreadable_roots` instead of being
    skipped; the one exception, the default drop folder not existing yet, is
    what a fresh install looks like.

    One real folder is one row, whichever spelling reached it, and the row
    belongs to the nearest configured root above the real folder. A
    junction can put any folder under any root; with more than one root
    configured every child is compared by where it really is, and one that
    resolves to another root, or below one, is left to that root's walk
    (see _stepped_around), so a uid depends neither on the root order nor
    on which walk went through the junction first. A junction back into
    its own root is followed, and the first sighting of a real folder keeps
    it, so a folder reached at several depths is one row. A junction back
    to the root's own PARENT is followed too, since that is how a model
    behind it is found at all, and every revisit of the root through it
    counts against the root's directory cap; the cut is reported like any
    other, in `truncated_roots`.
    """
    if roots is None:
        # Extra roots are re-read here, not cached at import: adding a folder
        # must take effect without restarting the app.
        default = Path(config.TTS_MODELS_DIR)
        configured = [default] + [Path(r) for r in extra_roots()]
        lenient = {_key(_canonical(default))}
        containers = set(lenient)
    else:
        configured = [Path(r) for r in roots]
        lenient = set()
        containers = set()

    result = ScanResult()
    identified: list[Path] = []
    candidates: list[tuple[Path, Path]] = []      # (root walked, folder)
    found: set[str] = set()                        # real folders already listed
    real_keys: dict[str, str] = {}                 # spelling -> canonical key
    # For a root that is a row candidate: its own canonical key and those of
    # the children its walk handed to other roots, see the candidate loop.
    territory: dict[str, list[str]] = {}

    def real_key(folder: Path) -> str:
        spelled = str(folder)
        if spelled not in real_keys:
            real_keys[spelled] = _key(_canonical(folder))
        return real_keys[spelled]

    def first_sighting(folder: Path) -> bool:
        real = real_key(folder)
        if real in found:
            return False
        found.add(real)
        return True

    for plan in _plan_roots(configured):
        root = plan.root
        result.roots.append(str(root))
        if not root.is_dir():
            if _key(root) in lenient and not root.exists():
                continue      # the drop folder the user has not made yet
            result.unreadable_roots.append(str(root))
            continue

        root_holds_files = False
        if _key(root) not in containers:
            res = identify_dir(root)
            if res is not None and res.engine_id in _BY_ID:
                identified.append(root)
                if first_sighting(root):
                    result.models.append(_detected(root, root, res, plan.legacy))
                continue      # never descend into a recognised model
            # A root that holds files no engine claims, a Piper or Kokoro
            # folder pointed at directly, used to be nothing at all: no
            # model, no row, no hint that the folder had even been looked
            # at. It is a row, but only as a fallback once its walk is done
            # and has nothing to list (below). Judged up front, it was the
            # first row of its own walk and absorbed every row under it: a
            # hub with a version.txt at its top was the row and the LLM
            # snapshot under it, the folder the user has to look at, was
            # gone from the list. The cost, accepted: a Kokoro folder
            # pointed at directly lists its voices/ subfolder rather than
            # itself, which is what the scan said before roots were judged
            # at all; nothing in the files tells a Kokoro folder from a
            # cache, both hold files at the top and files one level down.
            root_holds_files = _has_files(root)

        handed_off: list[str] = []      # canonical keys, left to other walks

        def children(folder: Path, depth: int) -> list[tuple[Path, int]]:
            out: list[tuple[Path, int]] = []
            for c in sorted(folder.iterdir()):
                if not c.is_dir():
                    continue
                if _stepped_around(c, plan):
                    # Another root's business, but reached from under THIS
                    # root, so whatever that walk lists there sits under
                    # this root as well; recorded for the root-row rule in
                    # the candidate loop.
                    handed_off.append(real_key(c))
                    continue
                out.append((c, depth))
            return out

        stack: list[tuple[Path, int]] = []
        try:
            stack = children(root, 1)
        except OSError:
            result.unreadable_roots.append(str(root))
            continue

        seen = 0
        unlistable = 0
        below = 0         # models and candidate rows this walk produced
        while stack:
            if seen >= config.TTS_SCAN_MAX_DIRS:
                # Say so rather than returning a short list that looks
                # complete. `stack` still holding entries is the proof the
                # walk was cut, not finished. The root is named so the page
                # can say WHICH folder was cut: a bool cannot tell two roots
                # apart, and there is no control for narrowing the scan.
                result.truncated = True
                result.truncated_roots.append(str(root))
                logger.warning(
                    "tts: model scan of one root stopped at the %d directory "
                    "limit; some models may be missing from the list.",
                    config.TTS_SCAN_MAX_DIRS,
                )
                break
            current, depth = stack.pop(0)
            seen += 1

            res = identify_dir(current)
            if res is not None:
                if res.engine_id in _BY_ID:
                    # Kept in `identified` even when it is a repeat, so a
                    # folder on the way to it still counts as a container.
                    identified.append(current)
                    below += 1
                    if first_sighting(current):
                        result.models.append(_detected(root, current, res))
                continue      # never descend into a recognised model

            if current.name in _HF_BOOKKEEPING:
                continue      # neither listed nor entered, see the note above
            if _has_files(current):
                candidates.append((root, current))
                below += 1
            if depth < config.TTS_SCAN_MAX_DEPTH:
                try:
                    stack.extend(children(current, depth + 1))
                except OSError:
                    # Not a root, so it has no place in unreadable_roots,
                    # and it used to vanish with no trace at all.
                    unlistable += 1

        if root_holds_files and not below:
            # Nothing THIS walk reached under the root is a model or a row
            # of its own, so the root is judged the way any child folder
            # holding files is, unless another walk lists something under
            # it (the candidate loop decides that, once every walk is done).
            # A walk cut at the cap with nothing found lands here too; the
            # root is then named in truncated_roots beside its row.
            candidates.append((root, root))
            territory[plan.key] = [plan.key, *handed_off]

        if unlistable:
            # Counted, never named. elysium.log sits outside the vault, and
            # the part of a path below its root carries the Windows account
            # name as soon as the root sits above the profile folder (C:\,
            # the mis-pointed root config.py names, or C:\Users), where
            # every profile holds a set of legacy junctions that refuse to
            # be listed: Users/<account>/Cookies, ten of them. Naming the
            # folder relative to its root only kept the account name out
            # for a root at or below the profile. The truncation warning
            # above names nothing for the same reason, and the roots are
            # not shown on the settings page, so a relative name had nothing
            # to be read against there anyway.
            logger.warning(
                "tts: model scan could not list %d folder(s) inside one "
                "root; anything below them is not in the list.",
                unlistable,
            )

    # A container on the way to a model (models--X/, snapshots/) is not an
    # error. And a folder under a LISTED folder is not a second row: the row
    # already names the folder the user has to look at, and depth 4 exposed
    # what the extra rows look like on a real cache (a snapshot and its
    # 1_Pooling subfolder, both with a config.json). The walk is breadth
    # first, so a parent is always considered before its children. Only a
    # row from the SAME walk absorbs a folder, because a root the user
    # listed on purpose gets its own rows even when a wider root lists a
    # folder above it. And one real folder is one row here too.
    #
    # A root is a row only as a fallback, and only when nothing under it is
    # listed by ANY walk, not just its own: with [hub, hub\inner] and a
    # version.txt at the top of hub, the walk of hub stepped around inner,
    # found nothing, and hub was a row beside inner's rows, where [hub] alone
    # listed inner's folder and not hub. The same through a junction: with
    # [a, b] and a\link -> b, the walk of a handed the link to b's walk, so
    # a was a row although [a] alone listed the model behind the link with a
    # as its container. What counts as "under" the root is its own canonical
    # key and the keys of the children its walk handed off, and what is
    # compared is every identified folder and every other walk's candidate,
    # by canonical key, so the answer does not depend on the root order. A
    # root whose inner root lists nothing stays the row, as it was alone.
    def listed_below(root: Path) -> bool:
        inside = territory[_key(root)]
        rows = [*identified, *(c for _, c in candidates if c != root)]
        return any(
            real_key(p) == k or _inside(real_key(p), k)
            for p in rows for k in inside
        )

    listed: list[tuple[Path, Path]] = []
    for root, cand in candidates:
        if cand == root and listed_below(root):
            continue
        if any(_is_ancestor(cand, m) for m in identified):
            continue
        if any(r == root and _is_ancestor(p, cand) for r, p in listed):
            continue
        if not first_sighting(cand):
            continue
        listed.append((root, cand))
        result.unrecognized.append(
            UnrecognizedDir(str(cand), "no engine signature matched")
        )

    result.models.sort(key=lambda m: m.name.lower())
    return result


def _is_ancestor(parent: Path, child: Path) -> bool:
    try:
        return os.path.commonpath([str(parent), str(child)]) == str(parent) and parent != child
    except ValueError:
        return False
