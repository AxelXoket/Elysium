"""The README's Privacy Contract, bound to the tests that prove it.

A promise in a README is a claim about the software that the software does not
know it is making. The inventory that produced this file found the three
loudest ones - zdr, data_collection, allow_fallbacks, each printed with a red
cross under "Overridable?" - proved by a regex in verify/ that greps config.py
for the literal text, plus one incidental assertion inside an image test. Six
others had nothing at all, including "without the passphrase it does not open
as SQLite at all".

So this file is the join. Every promise in that section names the test that
would fail if it stopped being true, and the build fails three ways:

  * a promise in the README with no entry here - a new claim landed without a
    proof, which is the case that made this necessary;
  * an entry here whose marker is no longer in the README - a claim was
    reworded or dropped and its registration went stale;
  * an entry pointing at a test that does not exist - the proof was renamed,
    moved or deleted and nothing noticed.

It parses the README rather than the source. That is deliberate and it is the
same shape as test_release_sync.py, which parses the PyInstaller specs: the
document IS the artefact under test here. What it must never become is a
source-text scan standing in for a behaviour test - the registered proofs are
all behaviour tests, and this file only checks that they are reachable.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from tests import _contract_gate

_REPO = Path(__file__).resolve().parents[2]
#: pytest's rootdir for this suite, and the directory a node id like
#: "tests/test_x.py::test_y" is relative to. Held apart from _REPO so a test
#: can point the gate at a synthetic tree without writing into this one.
_BACKEND = _REPO / "backend"
_README = _REPO / "README.md"
_FRONTEND = _REPO / "frontend"

#: (marker, proofs). The marker is a distinctive substring of the README line;
#: the proofs are pytest node ids, or `<path>::<vitest name>` for the two
#: promises that are about frontend code and cannot be proved from here.
CLAIMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("`provider.zdr`", (
        "tests/test_privacy_promises.py::TestTheLockedProviderPolicy"
        "::test_a_client_cannot_turn_zero_data_retention_off",
    )),
    ("`provider.data_collection`", (
        "tests/test_privacy_promises.py::TestTheLockedProviderPolicy"
        "::test_a_client_cannot_opt_into_data_collection",
    )),
    ("`provider.allow_fallbacks`", (
        "tests/test_privacy_promises.py::TestTheLockedProviderPolicy"
        "::test_a_client_cannot_re_enable_fallbacks",
    )),
    ("`context_budget_tokens` is **never** forwarded", (
        "tests/test_privacy_promises.py::TestWhatIsNeverSent"
        "::test_the_context_budget_is_an_app_side_number_only",
    )),
    ("`response_format` is sent by exactly one path", (
        "tests/test_privacy_promises.py::TestWhatIsNeverSent"
        "::test_response_format_is_sent_by_the_extractor_and_nowhere_else",
        "tests/test_privacy_promises.py::TestWhatIsNeverSent"
        "::test_a_smuggled_field_cannot_ride_in_on_generation_params",
    )),
    ("`raw_json`, `avatar_path`, `tools`", (
        "tests/test_privacy_promises.py::TestWhatIsNeverSent"
        "::test_the_field_does_not_reach_the_wire",
        "tests/test_privacy_promises.py::TestWhatIsNeverSent"
        "::test_a_smuggled_field_cannot_ride_in_on_generation_params",
    )),
    ("Raw upstream OpenRouter error bodies", (
        "tests/test_privacy_at_rest.py::TestUpstreamErrorBodiesAreNotForwarded"
        "::test_the_plain_path_relays_a_code_and_nothing_else",
        "tests/test_privacy_at_rest.py::TestUpstreamErrorBodiesAreNotForwarded"
        "::test_the_streaming_path_relays_a_code_and_nothing_else",
    )),
    ("API key is sealed inside the encrypted vault", (
        "tests/test_privacy_promises.py::TestTheApiKeyIsNeverHandedBack"
        "::test_no_readable_endpoint_returns_it",
        "tests/test_privacy_promises.py::TestTheApiKeyIsNeverHandedBack"
        "::test_storing_and_reading_it_writes_nothing_to_the_log",
    )),
    ("Browser storage holds only UI preferences", (
        "frontend/src/test/static-safety.test.ts::S-09",
        "frontend/src/test/static-safety.test.ts::S-09b",
    )),
    ("Frontend never emits an `Authorization` header", (
        "frontend/src/test/static-safety.test.ts::S-11",
        "frontend/src/test/static-safety.test.ts::S-11b",
    )),
    ("Logs carry numeric ids, counts and status codes", (
        "tests/test_privacy_at_rest.py::TestTheLogNeverCarriesWhatWasSaid"
        "::test_a_completed_turn_logs_no_message_text",
        "tests/test_privacy_at_rest.py::TestTheLogNeverCarriesWhatWasSaid"
        "::test_a_failed_turn_logs_no_message_text",
        # The promise names ONE measured gap rather than claiming there are
        # none, so the thing that counts it is registered beside the two
        # behaviour tests. Both sweep the whole shipped backend and both go
        # red the moment a debt grows.
        #
        # It named TWO until 31 August 2026. The second - a reference voice's
        # label reaching the log when it was deleted or listed - was closed,
        # those four lines now write a keyed hash, and `KNOWN_CONTENT_DEBT`
        # is empty. The sentence went on describing the leak for a while
        # after the leak was gone, which is its own kind of false: a reader
        # deciding whether to trust this app was being told about a hole
        # that had been filled. The content sweep below is what makes the
        # new half of the claim - "the ledger is empty" - checkable.
        "tests/test_log_identifier_privacy.py"
        "::test_no_new_content_leak_anywhere_in_the_tree",
        "tests/test_log_identifier_privacy.py"
        "::test_no_new_traceback_leak_anywhere_in_the_tree",
    )),
    ("Content-Security-Policy", (
        "tests/test_security_headers.py::test_every_response_carries_the_baseline",
        "tests/test_security_headers.py::test_a_locked_vault_423_is_covered",
        "tests/test_security_headers.py"
        "::test_the_packaged_build_closes_the_no_cors_read",
    )),
    ("scrypt at OWASP's current floor", (
        "tests/test_kdf_upgrade.py::TestTheParametersAreRecorded"
        "::test_the_current_parameters_meet_the_owasp_floor",
        "tests/test_kdf_upgrade.py::TestTheOldVaultStillOpens"
        "::test_a_legacy_vault_unlocks_with_its_own_parameters",
        "tests/test_kdf_upgrade.py::TestTheUpgradeHappensAtUnlockAndOnlyThere"
        "::test_unlocking_a_legacy_vault_upgrades_it",
    )),
    ("A passphrase must be at least 12 characters", (
        "tests/test_passphrase_strength.py::TestLength"
        "::test_the_floor_is_high_enough_to_matter",
        "tests/test_passphrase_strength.py::TestShapesThatAreLongButNotSecret"
        "::test_a_walk_along_the_keyboard_is_refused",
        "tests/test_passphrase_strength.py::TestWhatMustStillBeAccepted"
        "::test_a_passphrase_with_no_digits_or_symbols_is_fine",
    )),
    ("Locking overwrites the key in memory", (
        "tests/test_auto_lock.py::TestLockingActuallyDestroysTheKey"
        "::test_the_bytes_are_overwritten_not_merely_dropped",
        "tests/test_auto_lock.py::TestWhenItDecidesToLock"
        "::test_an_idle_vault_with_the_setting_on_locks",
        "tests/test_auto_lock.py::TestWhenItDecidesToLock"
        "::test_a_busy_vault_does_not_lock_however_long_it_has_been",
        "tests/test_auto_lock.py::TestAStreamedReplyIsNotInterrupted"
        "::test_the_vault_is_never_idle_while_the_body_is_streaming",
        "tests/test_notebook_worker_hardening.py"
        "::TestABilledCallLeavesATraceBeforeItIsMade"
        "::test_a_cancellation_in_flight_leaves_the_trace_behind",
    )),
    ("The desktop window is given a secret at launch", (
        "tests/test_launch_token.py::TestWhenItIsArmed"
        "::test_a_request_without_the_header_is_refused",
        "tests/test_launch_token.py::TestWhenItIsArmed"
        "::test_a_request_with_the_header_passes",
        "tests/test_launch_token.py::TestWhatStaysReachable"
        "::test_the_same_image_route_is_refused_without_that_signal",
        # The sentence used to say this gate "still refuses a program with
        # curl". It does not, and this is the test that measures the real
        # size of the exemption so the sentence cannot drift back.
        "tests/test_launch_token.py::TestWhatStaysReachable"
        "::test_a_program_that_sets_the_header_by_hand_is_not_refused",
        "frontend/src/test/launchToken.test.ts::never writes the token to browser storage",
        "tests/test_launch_token.py::TestTheTokenDoesNotReachOurOwnSubprocesses"
        "::test_the_voice_engine_is_not_given_it",
        "tests/test_launch_token.py::TestTheTokenDoesNotReachOurOwnSubprocesses"
        "::test_the_installer_subprocess_is_not_given_it",
        # A secret the window holds is not a secret the window holds if it is
        # also in this process's environment block, which any program running
        # as the same user reads out of our PEB. It was, until it was not.
        "tests/test_launch_token.py"
        "::TestTheTokenIsNeverPublishedToTheProcessEnvironment"
        "::test_issuing_a_token_does_not_put_it_in_the_win32_environment_block",
    )),
    # The marker was "Every outbound request passes one check" until 31
    # August 2026. It reads as a claim about the whole process, and the
    # voice-engine installer is not covered by it: `provision.py` builds its
    # own urllib opener for the package manager, and the package manager
    # then runs as separate processes that reach PyPI and the model host.
    # The three tests below measure the httpx chokepoint, which is what the
    # sentence now says - so the proof and the promise describe the same
    # thing again.
    ("Every outbound request THIS APP MAKES passes one check", (
        "tests/test_egress_chokepoint.py::TestWhatIsRefused"
        "::test_a_request_to_another_host_never_leaves",
        "tests/test_egress_chokepoint.py::TestWhatIsRefused"
        "::test_the_provider_itself_passes",
        "tests/test_egress_chokepoint.py::TestTheHookIsActuallyInstalled"
        "::test_a_proxied_client_still_checks_the_destination",
    )),
    ("The app window refuses to navigate off its own origin", (
        # The full it.each template, not the readable half of it. The name
        # vitest reports is "refuses to leave for %s" (navigationGuard.test.ts
        # :49); the truncated spelling resolved only because the old frontend
        # matcher had no closing delimiter.
        "frontend/src/test/navigationGuard.test.ts::refuses to leave for %s",
        "frontend/src/test/navigationGuard.test.ts::allows a link inside the app",
    )),
    ("A stored image is served only if its recorded type", (
        "tests/test_security_headers.py"
        "::test_a_row_whose_mime_is_not_an_image_is_refused",
    )),
)

#: Claims made in the prose paragraphs rather than as list items. The line
#: parser cannot see these, so they are anchored by marker only: a reworded or
#: deleted sentence still fails, a NEW sentence does not. That asymmetry is the
#: honest cost of not trying to parse English, and it is why the guarantees
#: that matter live in the bulleted list.
PROSE_CLAIMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("it does not open as SQLite at all", (
        "tests/test_privacy_at_rest.py::TestTheDatabaseFileIsCiphertext"
        "::test_stdlib_sqlite_cannot_read_it",
        "tests/test_privacy_at_rest.py::TestTheDatabaseFileIsCiphertext"
        "::test_a_message_is_not_findable_in_the_raw_bytes",
    )),
    ("Images are stored as encrypted\nblobs INSIDE that database", (
        "tests/test_privacy_at_rest.py::TestTheDatabaseFileIsCiphertext"
        "::test_an_image_blob_is_not_findable_in_the_raw_bytes",
    )),
    # KADEME: the "At rest" paragraph used to name only what IS sealed, and
    # the Features list said the wallpaper was "the one exception" - which
    # made the two files that matter more read as encrypted. Registered as
    # prose because it is a paragraph, not a bullet; the audio half is proven
    # by the same wipe tests SECURITY.md registers, and the clone-reference
    # half by the refs test that reads clip and transcript back off disk.
    ("Three things are deliberately outside the vault", (
        "tests/test_bounded_resources.py"
        "::test_generated_audio_older_than_the_window_is_cleared",
        "tests/test_audio_cache_launch_wipe.py"
        "::TestLaunchClearsWhatTheLastSessionLeft"
        "::test_audio_from_a_previous_session_does_not_survive",
        "tests/test_tts_refs.py::TestSavingAClip"
        "::test_a_clip_and_its_words_are_stored_together",
    )),
    ("served images carry `Cache-Control: no-store`", (
        "tests/test_security_headers.py"
        "::test_a_served_image_carries_nosniff_and_no_store",
    )),
    # A NUMBER, and numbers are the thing this registry was weakest at.
    #
    # The registered proof for the traceback gap measures the MECHANISM - the
    # scan equals the ledger - and stayed green through three separate
    # occasions when the sentence quoting it went out of date. The count is
    # now read out of this document and compared to the ledger it claims to
    # be quoting, so the prose is the assertion rather than a summary of one.
    ("there are fifty such places", (
        "tests/test_locked_numbers.py::TestTheTracebackCount"
        "::test_the_readme_says_it_too",
    )),
    # The other half of the same sentence: a leak named as CLOSED must stay
    # closed, and the ledger is what says so.
    ("the scanner's content ledger is empty", (
        "tests/test_locked_numbers.py::TestTheContentLedger"
        "::test_an_empty_ledger_is_not_described_as_an_open_leak",
        "tests/test_voice_log_privacy.py::TestTheRouterLogLine"
        "::test_the_delete_route_does_not_name_the_voice",
        "tests/test_tts_worker.py::TestAWorkersOwnWordsDoNotReachTheLog"
        "::test_a_reference_clip_name_does_not_survive_a_detail",
    )),
)


#: The whole section, normalised for whitespace, as it stands with every
#: promise above registered. The line parser sees table rows and bullets; it
#: cannot see a claim added to the "At rest" paragraph or to the proxy note,
#: which are prose - so a false sentence could be written into either and no
#: test would notice. This closes that by refusing ANY change to the section
#: until somebody comes here, decides what the change claims, and registers a
#: proof for it. Updating this constant is the deliberate act.
SECTION_DIGEST = "717cea6009cc15828877c734b0080cb8b078f889d5eec8bebbc596de0fcf0052"


def _section() -> str:
    text = _README.read_text(encoding="utf-8")
    start = text.index("## Privacy Contract")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def _promise_lines() -> list[str]:
    """Every line in the section that states a promise.

    Two shapes: a data row of the routing table, and a bullet under
    "Additional guarantees". Headers, separators and the prose paragraphs are
    not lines that can be individually registered.
    """
    lines: list[str] = []
    for raw in _section().splitlines():
        line = raw.strip()
        if line.startswith("|") and line.startswith("| `"):
            lines.append(line)
        elif line.startswith("- "):
            lines.append(line)
    return lines


def _backend_proof_nodes() -> tuple[str, ...]:
    """Every backend node id this registry claims, for one batched run."""
    return tuple(sorted({
        node for _, proofs in _all_claims() for node in proofs
        if not node.startswith("frontend/")
    }))


def _resolve_backend(node_id: str) -> bool:
    """True if pytest can collect this node id AND it passes today.

    Was: import the module, walk the attributes, return `callable(target)`.
    Callable is not collectable and neither one is green - a test that exists
    and fails read as a proof, and so did any helper that happened to be a
    function. The measurement now happens by running pytest;
    _contract_gate.py holds the mechanism and the argument for it.
    """
    return _contract_gate.resolve_backend(
        node_id, _BACKEND, prime=_backend_proof_nodes)


def _resolve_frontend(node_id: str) -> bool:
    # A vitest name cannot be imported from here, so this is the one place the
    # registry falls back to looking for the literal. It checks that a proof
    # EXISTS, never what it asserts. The delimiter rule, and the two rules
    # measured against it and rejected, are argued in _contract_gate.py.
    return _contract_gate.resolve_frontend(node_id, _REPO)


#: The gate runs pytest as a child process. Any test that asks it a question
#: would, inside that child, ask for another child. These are the tests that
#: reach it; they are skipped one level down rather than allowed to recurse.
#: Everything else in this file still runs there, which is what lets a proof
#: registered against a test in this very file be measured honestly.
_needs_the_gate = pytest.mark.skipif(
    _contract_gate.GATE_CHILD_AT_IMPORT,
    reason="the contract gate is measuring this file from a child pytest",
)


def _resolve(node_id: str) -> bool:
    if node_id.startswith("frontend/"):
        return _resolve_frontend(node_id)
    return _resolve_backend(node_id)


def _all_claims() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return CLAIMS + PROSE_CLAIMS


class TestEveryPromiseHasAProof:
    def test_no_promise_is_unregistered(self) -> None:
        # The case this file exists for: somebody adds a line to the Privacy
        # Contract and ships it with nothing behind it.
        markers = [marker for marker, _ in CLAIMS]
        orphans = [line for line in _promise_lines()
                   if not any(marker in line for marker in markers)]
        assert orphans == [], (
            "these README promises have no registered proof:\n  "
            + "\n  ".join(orphans))

    @pytest.mark.parametrize("marker, proofs", _all_claims(),
                             ids=lambda v: v if isinstance(v, str) else "")
    @_needs_the_gate
    def test_the_proof_exists(self, marker: str, proofs: tuple[str, ...]
                              ) -> None:
        missing = [node for node in proofs if not _resolve(node)]
        assert missing == [], (
            f"the proof registered for {marker!r} does not exist: {missing}")

    @pytest.mark.parametrize("marker, proofs", _all_claims(),
                             ids=lambda v: v if isinstance(v, str) else "")
    def test_the_promise_is_still_in_the_readme(
        self, marker: str, proofs: tuple[str, ...]
    ) -> None:
        # A registration that outlives its claim is worse than none: it reads
        # as coverage while proving something nobody promises any more.
        section = _section()
        haystack = re.sub(r"\s+", " ", section)
        needle = re.sub(r"\s+", " ", marker)
        assert needle in haystack, (
            f"{marker!r} is registered here but no longer in the README")

    def test_every_claim_registers_at_least_one_proof(self) -> None:
        empty = [marker for marker, proofs in _all_claims() if not proofs]
        assert empty == []


class TestNothingInTheSectionChangesUnnoticed:
    """The line parser has a blind spot, and prose is where claims hide.

    "without the passphrase it does not open as SQLite at all" is a paragraph,
    not a bullet. So is the proxy note. A new sentence in either - or a few
    words appended to an existing bullet, which the substring match would read
    as already covered - is a promise nothing here would have seen.
    """

    def test_the_section_matches_the_registered_digest(self) -> None:
        current = hashlib.sha256(
            re.sub(r"\s+", " ", _section()).strip().encode("utf-8")
        ).hexdigest()
        assert current == SECTION_DIGEST, (
            "The Privacy Contract section changed. "
            "Whatever was added or reworded is a claim about this software. "
            "Register a proof for it in CLAIMS or PROSE_CLAIMS, then update "
            f"SECTION_DIGEST to: {current}"
        )


class TestTheRegistryCanActuallyFail:
    """The guard needs its own guard.

    test_release_sync.py taught this the hard way: its spec-drift check
    compared two empty sets for months because the regex matched the wrong
    quote style. A check that cannot fail is indistinguishable from one that
    passes.
    """

    def test_the_readme_section_was_actually_found(self) -> None:
        section = _section()
        assert "Additional guarantees" in section
        assert len(section) > 1000

    def test_promise_lines_are_actually_being_extracted(self) -> None:
        lines = _promise_lines()
        assert len(lines) >= 10, f"only found {len(lines)} promises to check"
        assert any("provider.zdr" in line for line in lines)

    def test_the_digest_would_notice_a_reworded_sentence(self) -> None:
        altered = _section().replace("Privacy Contract", "Privacy Contract ")
        assert hashlib.sha256(
            re.sub(r"\s+", " ", altered + " and one more promise").strip()
            .encode("utf-8")).hexdigest() != SECTION_DIGEST

    def test_an_unregistered_promise_would_be_caught(self) -> None:
        invented = "- The app promises something nobody has tested"
        markers = [marker for marker, _ in CLAIMS]
        assert not any(marker in invented for marker in markers)

    def test_no_environment_variable_can_quietly_skip_these(self) -> None:
        """The skip guard is a recursion stop, not a way out of the gate.

        Every proof-resolution test in this file carries `_needs_the_gate`,
        which skips it inside a child pytest. Exporting that variable in an
        ordinary shell therefore skips them all and the suite still exits
        zero - the exact shape of failure the gate exists to refuse, one
        level up. This test carries no marker, so it is the one thing that
        cannot be skipped that way.
        """
        # The SAME value the skip marker read, not a fresh reading. A plugin
        # that set the variable before import and cleared it during
        # collection satisfied a fresh reading while every proof test above
        # had already been skipped: measured, and it took ten lines.
        assert not _contract_gate.GATE_CHILD_AT_IMPORT, (
            f"{_contract_gate.DEPTH_ENV} was set when this module was "
            "imported. Every proof resolution test in this file skips when "
            "it is, and the run still reports success. Unset it and run "
            "again."
        )

    def test_a_proof_that_does_not_exist_is_reported_missing(self) -> None:
        assert _resolve("tests/test_privacy_promises.py::NoSuchClass"
                        "::test_nothing") is False
        assert _resolve("tests/test_no_such_module.py::test_nothing") is False
        assert _resolve("frontend/src/test/nope.test.ts::S-99") is False

    @_needs_the_gate
    def test_a_registered_name_that_cannot_run_is_not_a_proof(self) -> None:
        """Resolving by attribute alone accepted anything non-None.

        A guarantee registered against `test_x = "disabled"` would have read as
        proven, while pytest collected nothing: the attribute exists, so the
        walk succeeded. The registry's whole job is to make an unproven promise
        impossible, and a name that cannot run proves nothing.
        """
        assert _resolve(
            "tests/test_privacy_contract.py::SECTION_DIGEST") is False, (
            "a string attribute resolved as if it were a test"
        )
        # _resolve is a callable in this module and the old gate said True for
        # it. pytest collects test_* functions inside test_* files, never a
        # private helper, so True was the wrong answer and this assertion
        # pinned it in place. The red test was right; the defect was in the
        # gate.
        assert _resolve("tests/test_privacy_contract.py::_resolve") is False, (
            "a helper pytest cannot collect resolved as if it were a test"
        )
        # And the gate still accepts the real thing, so this did not simply
        # break resolution for everyone.
        assert _resolve(
            "tests/test_privacy_contract.py::TestTheRegistryCanActuallyFail"
            "::test_an_unregistered_promise_would_be_caught") is True

    def test_a_proof_that_does_exist_resolves(self) -> None:
        assert _resolve(
            "tests/test_privacy_promises.py::TestTheLockedProviderPolicy"
            "::test_a_client_cannot_turn_zero_data_retention_off") is True
        assert _resolve(
            "frontend/src/test/static-safety.test.ts::S-11") is True

    @staticmethod
    def _neighbour_only(tmp_path: Path,
                        monkeypatch: pytest.MonkeyPatch) -> None:
        """A synthetic tree holding S-09b and nothing else."""
        probe = tmp_path / "frontend" / "probe.test.ts"
        probe.parent.mkdir(parents=True)
        probe.write_text(
            'it("S-09b: only the neighbour exists", () => {});\n',
            encoding="utf-8")
        monkeypatch.setitem(globals(), "_REPO", tmp_path)

    @_needs_the_gate
    def test_a_prefix_of_a_registered_name_is_not_that_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The collision static-safety.test.ts documents against itself."""
        self._neighbour_only(tmp_path, monkeypatch)
        assert _resolve("frontend/probe.test.ts::S-09") is False

    @_needs_the_gate
    def test_the_neighbour_that_is_really_there_still_resolves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POSITIVE CONTROL for the test above, on the same probe file.

        Held apart from it so the two answers can be observed one at a time:
        a resolver that says False to everything passes the test above and
        fails this one.
        """
        self._neighbour_only(tmp_path, monkeypatch)
        assert _resolve("frontend/probe.test.ts::S-09b") is True

    @_needs_the_gate
    def test_the_delimiter_does_not_drop_a_prefix_style_rule_id(self) -> None:
        """Ground control for the delimiter, and the naive fix's headstone.

        S-11 and S-11b are both registered here, and both are spelled in the
        file as `it("S-11: ...")` - the name is deliberately a prefix and a
        closing quote never follows it. Requiring one drops four registered
        rules at once, so this stays green while the test above goes green.
        """
        assert _resolve(
            "frontend/src/test/static-safety.test.ts::S-11") is True
        assert _resolve(
            "frontend/src/test/static-safety.test.ts::S-11b") is True

    @_needs_the_gate
    def test_half_of_an_it_each_template_is_not_the_test(self) -> None:
        """The registered name has to be the whole name vitest reports.

        navigationGuard.test.ts:49 is `])("refuses to leave for %s", ...)`.
        Registering the readable half of that resolved only because the old
        matcher stopped at the opening quote, so the registry certified a
        name no runner would ever print.
        """
        assert _resolve(
            "frontend/src/test/navigationGuard.test.ts"
            "::refuses to leave for") is False
        assert _resolve(
            "frontend/src/test/navigationGuard.test.ts"
            "::refuses to leave for %s") is True

    @staticmethod
    def _two_probes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """One red probe and one green one, in a synthetic tests package.

        syspath_prepend matters even though the gate resolves by path. It is
        what lets an IMPORT-based resolver reach these files too, so putting
        the old attribute walk back makes the red probe answer True and the
        test below go red for the right reason. Without it neither probe is
        importable, the old walk answers False by accident, and the
        measurement stops telling the two gates apart.

        Nothing is written into this repository: the probes live under
        tmp_path, and `tests` is a namespace package, so prepending a second
        root adds to it instead of shadowing it.
        """
        probes = tmp_path / "tests"
        probes.mkdir()
        (probes / "test_probe_red.py").write_text(
            "def test_always_fails():\n    assert False\n", encoding="utf-8")
        (probes / "test_probe_green.py").write_text(
            "def test_always_passes():\n    assert True\n", encoding="utf-8")
        monkeypatch.setitem(globals(), "_BACKEND", tmp_path)
        monkeypatch.syspath_prepend(tmp_path)

    @_needs_the_gate
    def test_a_registered_name_that_runs_red_is_not_a_proof(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Collectable is not the whole of it: the test has to be green."""
        self._two_probes(tmp_path, monkeypatch)
        assert _resolve("tests/test_probe_red.py::test_always_fails") is False

    @_needs_the_gate
    def test_a_registered_name_that_runs_green_still_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POSITIVE CONTROL, beside the red probe in the same package."""
        self._two_probes(tmp_path, monkeypatch)
        assert _resolve(
            "tests/test_probe_green.py::test_always_passes") is True
