"""tts/readiness.py - "here are its settings, and here is why it will not run".

A voice model is ALWAYS inspectable. Discovery, the settings descriptor and the
saved values all work on a laptop with no GPU, nothing installed and a
half-finished download, because taking the page away teaches the user nothing.

What is NOT allowed is letting them tune knobs on something that cannot speak
and only finding out when they press play - or, worst of all, finding out
through silence. So this module produces the second half of that promise: a
plain verdict carrying every reason at once, each as a contract code the UI
turns into real words.

Three rules shape it:

  * Report EVERYTHING, not the first thing. Fixing one blocker only to discover
    the next is the most demoralising shape this screen could have.
  * Name the actual cause. "Not enough VRAM" on a machine with no NVIDIA card
    sends someone off closing programs for nothing, so no-GPU is its own code.
  * Separate "cannot" from "will not be what you expect". A model that speaks
    English but not Turkish is not broken; blocking it would take away a model
    the user may well want. That is a warning.

Host half: pure stdlib, no torch. The GPU is read through nvidia-smi only.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from . import manifest, runtimes
from .base import DetectedModel
from .errors import (
    TTS_ENGINE_UNKNOWN,
    TTS_GPU_UNAVAILABLE,
    TTS_INSUFFICIENT_VRAM,
    TTS_LANGUAGE_UNSUPPORTED,
    TTS_MODEL_INCOMPLETE,
    TTS_RUNTIME_BROKEN,
    TTS_RUNTIME_MISSING,
    TTS_WORKER_FAILED,
    TTS_RUNTIME_UNTRUSTED,
)
from .host import worker_script
from .preflight import FitResult, check_fit


def _already_on_the_card(uid: str) -> bool:
    """Is this exact model resident, or on its way in, right now?

    Read from the host rather than from the GPU: nvidia-smi reports free memory
    AFTER our own allocation, so a loaded model makes itself look like the
    reason it cannot be loaded.

    Never raises - a readiness verdict must not fail because the host is
    mid-transition.
    """
    try:
        from .host import get_host

        snap = get_host().snapshot()
        return snap.get("uid") == uid and snap.get("state") in ("loading", "loaded")
    except Exception:                                    # noqa: BLE001
        return False
from .registry import adapter_for
from .vram import GpuInfo, query_gpu

BLOCKER = "blocker"
WARNING = "warning"

# What the UI should offer to DO about it. Anything else is just bad news.
ACTION_SETUP_RUNTIME = "setup_runtime"
ACTION_FREE_VRAM = "free_vram"
ACTION_REDOWNLOAD = "redownload"
ACTION_CHANGE_LANGUAGE = "change_language"


#: How many file names one issue will spell out before it counts the rest.
#: Half the reason is that a list of two hundred names is not a sentence
#: anybody reads. The other half is that the manifest half of the incomplete
#: check reads its names from a JSON file inside a folder the user dropped in,
#: so this is the length of attacker-influenced text the payload can carry.
#: It is not rendered today (the UI keys off the code, never the detail), and
#: this is what keeps that from being the only thing standing between a
#: planted file name and the screen.
MAX_NAMED_FILES = 8


def _name_list(names) -> str:
    """The first few names, then a count of the rest. Never unbounded."""
    names = list(names)
    if len(names) <= MAX_NAMED_FILES:
        return ", ".join(names)
    shown = ", ".join(names[:MAX_NAMED_FILES])
    return f"{shown}, and {len(names) - MAX_NAMED_FILES} more"


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    detail: str = ""
    transient: bool = False        # clears on its own; retrying is meaningful
    action: str | None = None

    def to_json(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "transient": self.transient,
            "action": self.action,
        }


@dataclass(frozen=True)
class Readiness:
    uid: str
    engine_id: str
    runnable: bool
    settings_available: bool
    runtime_state: str
    issues: tuple[Issue, ...]
    languages: tuple[str, ...]
    fit: FitResult | None = None

    def to_json(self) -> dict:
        return {
            "uid": self.uid,
            "engine_id": self.engine_id,
            "runnable": self.runnable,
            "settings_available": self.settings_available,
            "runtime_state": self.runtime_state,
            "issues": [i.to_json() for i in self.issues],
            "languages": list(self.languages),
            "fit": self.fit.to_json() if self.fit else None,
        }


_RUNTIME_ISSUE = {
    "missing": (TTS_RUNTIME_MISSING, "the voice engine has not been set up yet"),
    # Deliberately distinct: "set it up" and "set it up again, something removed
    # it" are different sentences, and collapsing them makes the second a lie.
    "broken": (TTS_RUNTIME_BROKEN, "the voice engine was set up but is gone now"),
    # And a third, for the same reason the second exists. "Gone" and "not the
    # one we installed" send the user to the same button and mean very
    # different things; reporting a tampered interpreter as a routine disk
    # cleanup is exactly the collapse the comment above refuses to make.
    "untrusted": (TTS_RUNTIME_UNTRUSTED,
                   "the voice engine on this machine is not the one Elysium "
                   "installed"),
}


def evaluate(
    model: DetectedModel,
    values: dict | None = None,
    *,
    language: str | None = None,
    gpu: GpuInfo | None = None,
    probe_gpu: bool = True,
    runtime_state: str | None = None,
) -> Readiness:
    """Everything standing between this model and speaking, right now.

    `gpu`/`runtime_state` are injection points for `evaluate_all`, which reads
    each of them once for a whole list.
    """
    issues: list[Issue] = []
    adapter = adapter_for(model.engine_id)

    # 1. No adapter means no descriptor to render. This is the ONE case where
    #    settings genuinely cannot be shown, so it has to be said out loud
    #    rather than presented as an empty page.
    if adapter is None:
        return Readiness(
            uid=model.uid,
            engine_id=model.engine_id,
            runnable=False,
            settings_available=False,
            # NOT "missing": that word means "never installed, offer Set up
            # voice" in every other payload, and no install can help an engine
            # this build does not know.
            runtime_state="unknown",
            issues=(Issue(TTS_ENGINE_UNKNOWN, BLOCKER,
                          f"no adapter for engine '{model.engine_id}'"),),
            languages=(),
            fit=None,
        )

    # ONE CODE, ONE ISSUE. Everything that reports the model's own files as
    # incomplete collects here and leaves as a single Issue at step 2. That is
    # a frontend fact rather than a preference: VoiceSettingsPage renders the
    # blocker list with `key={issue.code}` and prints getErrorMessage(code),
    # never the detail. Two Issues carrying this code would be a duplicate
    # React key AND the identical sentence printed twice - which the descriptor
    # branch below and the missing-files branch could already do together,
    # before any of this was added.
    trouble: list[str] = []

    # 1b. The settings page renders from the descriptor; prove it renders.
    #     A descriptor that raises would otherwise 500 the /schema endpoint
    #     while this verdict kept promising settings_available=True.
    settings_available = True
    try:
        adapter.describe_settings(model)
    except Exception:
        settings_available = False
        trouble.append("this model's settings could not be read from its files")

    # 1c. The worker program ships INSIDE the app; without it nothing can ever
    #     start, however healthy the model and the runtime are.
    try:
        script_ok = Path(worker_script(model.engine_id)).is_file()
    except Exception:
        script_ok = False
    if not script_ok:
        issues.append(Issue(
            TTS_WORKER_FAILED, BLOCKER,
            f"this installation is missing the worker program for "
            f"{model.engine_id}",
        ))

    # 2. An interrupted download looks exactly like a working model until it is
    #    loaded, so name the files rather than failing later with a stack trace.
    #
    #    TWO WAYS TO BE INCOMPLETE, ONE ISSUE. `missing` asks is_file(), and a
    #    zero-byte or half-written model.pth answers yes - so the interrupted
    #    download this check is named after went straight through it (defect
    #    Q-28). The manifest a downloader leaves beside the weights carries the
    #    size each file had when the fetch finished, which is the number
    #    nothing here ever had, and it closes the second half.
    #
    #    NO MANIFEST MEANS NO CLAIM. Every model already on a user's disk
    #    predates the manifest and cannot grow one; for those the second half
    #    does nothing at all, on purpose. It strengthens downloads made from
    #    now on; it is not a new way to refuse an install that already speaks.
    #
    #    The two halves overlap, and the overlap has to be subtracted rather
    #    than printed twice. A file the manifest recorded and the folder no
    #    longer has is reported by BOTH: `missing` because the adapter requires
    #    it, and the manifest because zero bytes is not the size it recorded.
    #    Left alone, the sentence named the same file twice and the second
    #    clause called a deleted file "present", which is the exact wrong
    #    errand this wording exists to avoid - sending someone to look at a
    #    file that is not there.
    if model.missing:
        trouble.append("missing from the model folder: "
                       + _name_list(model.missing))
    short = tuple(f for f in manifest.short_files(Path(model.path))
                  if f not in model.missing)
    if short:
        trouble.append("present but not the size the download recorded: "
                       + _name_list(short))
    if trouble:
        issues.append(Issue(
            TTS_MODEL_INCOMPLETE, BLOCKER, "; ".join(trouble),
            action=ACTION_REDOWNLOAD,
        ))

    # 3. The runtime. Actionable in one click, so it is worth its own code.
    state = runtime_state or runtimes.status(model.engine_id).state
    if state in _RUNTIME_ISSUE:
        code, detail = _RUNTIME_ISSUE[state]
        issues.append(Issue(code, BLOCKER, detail, action=ACTION_SETUP_RUNTIME))

    # 4. Will it fit right now. Only ever READS the GPU - never allocates.
    fit = check_fit(model, values, gpu=gpu, probe=probe_gpu)
    # ...unless THIS model is already on the card. Free VRAM is measured after
    # the load, so our own resident (or still-loading) weights counted as
    # "used by others" and the panel announced "Not enough GPU memory to load
    # this voice model" about the very model it had just loaded. Whether it
    # fits is settled at that point: it is in there.
    if not fit.fits and _already_on_the_card(model.uid):
        fit = replace(fit, fits=True, reason=None)
    if not fit.fits:
        # Branch on the REASON the fit check gave, not on a field it also
        # sets (KÖK 14). check_fit already decided why it refused and says so;
        # re-deriving that from gpu_available threw the answer away and
        # substituted a guess - which reported "no readable NVIDIA GPU" for a
        # model whose only problem was that nobody could estimate its size.
        # host.py reads the same FitResult the right way, so the two surfaces
        # were contradicting each other about the same machine.
        if fit.reason == TTS_GPU_UNAVAILABLE:
            issues.append(Issue(
                TTS_GPU_UNAVAILABLE, BLOCKER,
                fit.detail or "no readable NVIDIA GPU on this machine",
            ))
        else:
            issues.append(Issue(
                TTS_INSUFFICIENT_VRAM, BLOCKER,
                f"needs about {fit.estimate_mb} MB plus {fit.headroom_mb} MB "
                f"headroom; {fit.free_mb} MB free",
                transient=True,                 # close the game and try again
                action=ACTION_FREE_VRAM,
            ))

    # 5. Compatibility, not failure: it runs, just not in that language.
    languages_unreadable = False
    try:
        languages = tuple(adapter.languages_for(model))
    except Exception:
        languages = ()
        languages_unreadable = True
    if language and languages and language not in languages:
        issues.append(Issue(
            TTS_LANGUAGE_UNSUPPORTED, WARNING,
            f"this model does not speak '{language}'",
            action=ACTION_CHANGE_LANGUAGE,
        ))
    elif language and languages_unreadable:
        # We were ASKED about a language and cannot answer. Saying nothing
        # would read as "yes" - the one thing this module promises not to do.
        issues.append(Issue(
            TTS_LANGUAGE_UNSUPPORTED, WARNING,
            "this model's language list could not be read, so support for "
            f"'{language}' is unknown",
        ))

    return Readiness(
        uid=model.uid,
        engine_id=model.engine_id,
        runnable=not any(i.severity == BLOCKER for i in issues),
        settings_available=settings_available,
        runtime_state=state,
        issues=tuple(issues),
        languages=languages,
        fit=fit,
    )


def evaluate_all(
    models: list[DetectedModel],
    *,
    language: str | None = None,
    values_for=None,
) -> dict[str, Readiness]:
    """Verdicts for a whole list, reading the GPU ONCE.

    Twenty models must not mean twenty nvidia-smi subprocesses: free VRAM cannot
    meaningfully change between the first row and the last, and paying for it
    per row turns the settings page into a multi-second stall.
    """
    gpu = query_gpu()
    runtime_cache: dict[str, str] = {}
    out: dict[str, Readiness] = {}
    for model in models:
        if model.engine_id not in runtime_cache:
            runtime_cache[model.engine_id] = runtimes.status(model.engine_id).state
        out[model.uid] = evaluate(
            model,
            values_for(model) if values_for else None,
            language=language,
            gpu=gpu,
            probe_gpu=False,
            runtime_state=runtime_cache[model.engine_id],
        )
    return out
