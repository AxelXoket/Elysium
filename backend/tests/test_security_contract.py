"""SECURITY.md, bound to the tests that prove it.

test_privacy_contract.py did this for the README's Privacy Contract section
first, and found six promises with nothing behind them. SECURITY.md is a
harder document to guard the same way, for two reasons that shaped everything
below.

First, it has no fenced section. The README's contract lives between one
heading and the next; SECURITY.md's claims are spread across the whole file,
from "The short version" to the last paragraph about filing an issue. So
DOCUMENT_DIGEST covers the entire file, whitespace-normalised, not a slice
of it.

Second, most of the high-value claims here are prose, not bullets. The
README's line parser worked because its promises were routing-table rows and
short bullets that fit on one physical line. SECURITY.md's bullets wrap
across two or three lines of 80-column prose, so a marker taken from the
wrapped continuation ("not even as a plain database") would never be found by
a parser that only looks at the first physical line of a bullet. _promise_lines()
below joins a bullet's continuation lines before matching for exactly that
reason; a table row here never wraps, so those are taken as-is, same as the
README. That is the one deliberate deviation from copying the mechanism
verbatim, and it exists because the document's shape is different, not
because the guarantee is.

Three registries, not two. A claim in this document is either:

  * PROVEN - CLAIMS or PROSE_CLAIMS names a behaviour test that would fail if
    the claim stopped being true;
  * ACKNOWLEDGED_UNTESTABLE - the claim is real but nothing runnable can
    prove it (hardware behaviour, a third party's behaviour, an OS guarantee,
    a release-time artefact, something true by definition, or a one-time
    manual measurement recorded with a date and a pointer to where);
  * UNPROVEN - the claim is testable in principle and nobody has written the
    test yet. Named here so the debt is counted rather than silent.

Two claims in "What is NOT protected" - the alternate-data-stream scan and the
unopenable-filename scan - are none of the three. SECURITY.md reports both as
one-time measurements ("we scanned 34,200 files... found none") but no script
that produced either count exists anywhere in this tree, unlike the crash-dump
measurement, which is dated and pointed at in win_hardening.py. Faking a
ONE_TIME_MEASUREMENT entry without a real pointer would be exactly the kind of
proof this file exists to refuse, so those two markers are named in
KNOWN_UNREGISTERED_GAPS instead: TestTheKnownGapsStayNamed asserts the orphan
set is EXACTLY those two, so the suite stays green while the gap stays visible
and machine-checked, and grows loudly the moment a THIRD claim goes
unregistered.
"""
from __future__ import annotations

import enum
import hashlib
import importlib
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SECURITY = _REPO / "SECURITY.md"


class ReasonCategory(str, enum.Enum):
    """The closed set of reasons a true claim can be untestable.

    Closed on purpose: a category invented on the spot to excuse a claim that
    is actually just untested is how ACKNOWLEDGED_UNTESTABLE would rot into a
    second UNPROVEN with better PR. Enum membership is checked by Python
    itself at construction time, which is a stronger guarantee than a test
    that remembers to check a frozenset.
    """

    HARDWARE = "HARDWARE"
    THIRD_PARTY = "THIRD_PARTY"
    OS_GUARANTEE = "OS_GUARANTEE"
    RELEASE_ARTIFACT = "RELEASE_ARTIFACT"
    DEFINITIONAL = "DEFINITIONAL"
    ONE_TIME_MEASUREMENT = "ONE_TIME_MEASUREMENT"


#: (marker, proofs). Markers taken from a bulleted line or a table row - the
#: two shapes _promise_lines() extracts, so leaving one of these lines
#: unregistered anywhere (here, in ACKNOWLEDGED_UNTESTABLE or in UNPROVEN) is
#: what TestTheKnownGapsStayNamed exists to catch.
CLAIMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("not even as a plain database", (
        "tests/test_privacy_at_rest.py::TestTheDatabaseFileIsCiphertext"
        "::test_stdlib_sqlite_cannot_read_it",
    )),
    ("one address on the internet", (
        "tests/test_egress_chokepoint.py::TestWhatIsRefused"
        "::test_a_request_to_another_host_never_leaves",
        "tests/test_egress_chokepoint.py::TestWhatIsRefused"
        "::test_the_provider_itself_passes",
        "tests/test_egress_chokepoint.py::TestTheHookIsActuallyInstalled"
        "::test_a_proxied_client_still_checks_the_destination",
    )),
    ("AES-256 (SQLCipher)", (
        "tests/test_privacy_at_rest.py::TestTheDatabaseFileIsCiphertext"
        "::test_stdlib_sqlite_cannot_read_it",
    )),
    ("Settings > Secrets lists it and deletes it on request", (
        "tests/test_plaintext_backup_discard.py::TestTheCopyIsVisible"
        "::test_status_reports_a_plaintext_backup",
        "tests/test_plaintext_backup_discard.py::TestTheCopyCanBeRemoved"
        "::test_discarding_deletes_it",
    )),
    # The clone reference, and the reason the sentence is conditional. A
    # reference clip only exists for an engine that clones, so an unconditional
    # "you have a recording on disk" would be false for every user whose model
    # cannot. save_upload writing the clip AND transcript.txt as ordinary files
    # under voice/refs/<id>/ is what the row claims, and that is what the test
    # reads back off the filesystem.
    ("only for a voice model that CLONES, the reference clip you record "
     "and a transcript of the words in it", (
        "tests/test_tts_refs.py::TestSavingAClip"
        "::test_a_clip_and_its_words_are_stored_together",
        "tests/test_tts_refs.py::TestDeleting::test_deleting_removes_the_folder",
    )),
    ("the reference clip you record and a transcript of the words in it", (
        "tests/test_tts_refs.py::TestSavingAClip"
        "::test_a_clip_and_its_words_are_stored_together",
    )),
    # app.db.premigrate.bak. Named in no document until now, which is exactly
    # the shape of claim this registry exists to make impossible in reverse:
    # the FILE existed, the sentence did not. Three proofs for three halves of
    # the row - a dirty pass keeps it, a clean pass is the only thing that
    # removes it, and a passphrase rotation re-keys it so the old passphrase
    # stops opening a complete copy of the vault.
    ("a COMPLETE encrypted copy of the vault, taken before an uploads "
     "migration touches anything", (
        "tests/test_legacy_migration.py::test_a_failed_pass_still_keeps_the_snapshot",
        "tests/test_legacy_migration.py"
        "::test_a_clean_pass_discards_a_snapshot_left_by_an_earlier_dirty_one",
        "tests/test_vault_audit.py::test_rekey_file_moves_a_snapshot_to_the_new_key",
    )),
    ("moved aside because it did not open with this vault's key", (
        "tests/test_legacy_migration.py::TestAHalfWrittenSnapshotIsNotASnapshot"
        "::test_a_snapshot_that_does_not_open_is_replaced_not_trusted",
    )),
    ("anything older than 30 minutes is cleared as the next reply is spoken", (
        "tests/test_bounded_resources.py"
        "::test_generated_audio_older_than_the_window_is_cleared",
    )),
    ("only cosmetic settings and your wallpaper are kept", (
        "tests/test_browser_profile_purge.py::TestPurgeKeepsWhatTheProfileIsFor"
        "::test_settings_and_wallpaper_survive_intact",
        "tests/test_browser_profile_purge.py::TestPurgeKeepsWhatTheProfileIsFor"
        "::test_compiled_bundle_bytecode_survives",
    )),
    ("carries no message text", (
        "tests/test_privacy_at_rest.py::TestTheLogNeverCarriesWhatWasSaid"
        "::test_a_completed_turn_logs_no_message_text",
        "tests/test_privacy_at_rest.py::TestTheLogNeverCarriesWhatWasSaid"
        "::test_a_failed_turn_logs_no_message_text",
    )),
    ("No chat content is there", (
        "frontend/src/test/static-safety.test.ts::S-09",
        "frontend/src/test/static-safety.test.ts::S-09b",
    )),
    ("wiped on lock", (
        "tests/test_vault_honesty.py::test_lock_reports_audio_that_survived_the_wipe",
        "tests/test_audio_cache_launch_wipe.py"
        "::TestLaunchClearsWhatTheLastSessionLeft"
        "::test_audio_from_a_previous_session_does_not_survive",
    )),
    # KADEME 15b: this stood in UNPROVEN saying "no test triggers the ordinary
    # app-exit path and checks the voice cache afterwards". The entry was
    # stale - the test existed, in test_tts_audit_fixes.py, and nobody had
    # registered it. The chain is proven in two halves because that is how it
    # is built: the exit hook reaches THIS host exactly once, and the teardown
    # it runs leaves no audio behind.
    ("on exit and on the next launch", (
        "tests/test_tts_host.py"
        "::TestItLetsGo"
        "::test_process_teardown_wipes_the_audio_cache",
        "tests/test_tts_host.py"
        "::TestItLetsGo"
        "::test_process_teardown_reaches_the_current_host_exactly_once",
        "tests/test_audio_cache_launch_wipe.py"
        "::TestLaunchClearsWhatTheLastSessionLeft"
        "::test_audio_from_a_previous_session_does_not_survive",
    )),
)


#: Claims made in paragraphs, table cells and code samples rather than as a
#: top-level bullet or a routing-table row. Anchored by marker only, same
#: asymmetry as the README's PROSE_CLAIMS: a reworded or deleted sentence
#: fails, a brand new one does not, and that is why DOCUMENT_DIGEST exists.
PROSE_CLAIMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("128 MB per attempt", (
        "tests/test_kdf_upgrade.py::TestTheParametersAreRecorded"
        "::test_the_current_parameters_meet_the_owasp_floor",
    )),
    ("no keyboard walks, no one idea repeated, "
     "no single character filling half of it", (
        "tests/test_passphrase_strength.py::TestLength"
        "::test_the_floor_is_high_enough_to_matter",
        "tests/test_passphrase_strength.py::TestShapesThatAreLongButNotSecret"
        "::test_a_walk_along_the_keyboard_is_refused",
    )),
    ("Three unrelated words beat it", (
        "tests/test_passphrase_strength.py::TestWhatMustStillBeAccepted"
        "::test_a_passphrase_with_no_digits_or_symbols_is_fine",
    )),
    ("it does not open at all", (
        "tests/test_privacy_at_rest.py::TestTheDatabaseFileIsCiphertext"
        "::test_stdlib_sqlite_cannot_read_it",
    )),
    # The notebook's second sender, and what it writes.
    ("A SECOND model reads them too", (
        "tests/test_notebook_offer_wiring.py::TestEverySendPathTellsTheNotebook"
        "::test_plain_send",
        "tests/test_extraction_model_list.py::TestTheSchemaCondition"
        "::test_a_qualifying_endpoint_survives",
    )),
    ("What it writes goes into your prompts unreviewed, by default", (
        "tests/test_notebook_worker.py::TestAutoAccept::test_the_default_is_ON",
        "tests/test_notebook_worker_hardening.py::TestAnImportedCardNeverAutoAccepts"
        "::test_a_chat_from_an_imported_card_waits_for_review",
    )),
    ("Notes are fenced with a random per-request tag", (
        "tests/test_notebook_sentries.py::TestANoteCannotForgeAMarker"
        "::test_the_tag_is_different_every_time",
        "tests/test_notebook_sentries.py::TestModelTextIsNeverMergedWithTheUsers"
        "::test_the_headers_say_whose_notes_they_are",
    )),
    ("`on_violation` is stored and deliberately not acted on", (
        "tests/test_notebook_context.py::TestLimitsRefuseRatherThanShrink"
        "::test_limits_survive_a_full_notebook",
    )),
    ("the vault never locked on a busy chat", (
        "tests/test_notebook_worker.py::TestALockedVaultIsNotAFailure"
        "::test_cancellation_propagates_and_does_not_open_the_breaker",
    )),
    # One window per vault. Every proof here is a behaviour test and three of
    # them drive a real second process, which is the only place the property
    # that matters - a hard kill releases the claim - can be observed at all.
    ("a second launch is now refused rather than unsupported", (
        "tests/test_single_instance.py"
        "::test_a_second_claim_on_the_same_folder_is_refused",
        "tests/test_single_instance.py"
        "::test_a_second_instance_exits_zero_and_says_so",
        "tests/test_single_instance.py"
        "::test_a_window_of_ours_is_found_and_raised",
    )),
    ("a crash or an End Task releases it", (
        "tests/test_single_instance.py"
        "::test_a_hard_killed_instance_leaves_nothing_behind",
        "tests/test_single_instance.py"
        "::test_releasing_lets_the_next_launch_in",
    )),
    ("Two different data folders (`ELYSIUM_DATA_DIR`) still get a "
     "window each", (
        "tests/test_single_instance.py"
        "::test_a_different_data_folder_runs_alongside",
        "tests/test_single_instance.py"
        "::test_the_name_follows_the_folder_not_its_spelling",
    )),
    # The key check is a THIRD outbound path, so it belongs in the section
    # that counts them. The registered proofs are the two that make the
    # sentence true rather than merely present: nothing goes out until the
    # button is pressed, and an unreachable provider is not reported as a
    # rejected key.
    ("the Security tab's key check asks OpenRouter whether the key you "
     "already stored is still accepted", (
        "tests/test_api_key_check.py"
        "::test_nothing_is_checked_until_the_route_is_called",
        "tests/test_api_key_check.py"
        "::test_an_unreachable_provider_reports_something_else_entirely",
        "tests/test_api_key_check.py"
        "::test_the_key_is_never_returned_or_logged",
    )),
    ("On by default, 5 minutes", (
        "tests/test_auto_lock.py::TestTheSettingIsReadSafely"
        "::test_never_configured_means_the_default_not_off",
    )),
    ("tears down the voice engine (giving the GPU memory back), "
     "drops the network client, and stands", (
        "tests/test_auto_lock.py::TestTheLockItPerforms"
        "::test_it_is_the_same_lock_the_button_performs",
    )),
    ("a reply that is still streaming holds the vault open "
     "however long it takes", (
        "tests/test_auto_lock.py::TestWhenItDecidesToLock"
        "::test_a_busy_vault_does_not_lock_however_long_it_has_been",
    )),
    ("the buffer is overwritten rather than merely dropped", (
        "tests/test_auto_lock.py::TestLockingActuallyDestroysTheKey"
        "::test_the_bytes_are_overwritten_not_merely_dropped",
    )),
    ("overwritten with random bytes before being unlinked", (
        "tests/test_secure_delete.py::TestItDeletesWhatItShould"
        "::test_an_ordinary_file_is_overwritten_then_removed",
        "tests/test_secure_delete.py::TestItRefusesARedirectedName"
        "::test_a_junction_is_left_alone",
        "tests/test_secure_delete.py::TestItRefusesASharedName"
        "::test_a_hardlink_is_left_alone_and_so_is_its_twin",
    )),
    ("the server requires a secret generated at launch "
     "and given only to the app window", (
        "tests/test_launch_token.py::TestWhenItIsArmed"
        "::test_a_request_without_the_header_is_refused",
        "tests/test_launch_token.py::TestWhenItIsArmed"
        "::test_a_request_with_the_header_passes",
    )),
    ("This is only armed in the packaged app", (
        "tests/test_launch_token.py::TestTheGateIsUnarmedUnlessALaunchIssuedAToken"
        "::test_no_token_means_everything_is_accepted",
    )),
    ("Sec-Fetch-Site: same-origin", (
        "tests/test_launch_token.py::TestWhatStaysReachable"
        "::test_an_element_loaded_image_passes_with_a_same_origin_signal",
        "tests/test_launch_token.py::TestWhatStaysReachable"
        "::test_the_same_image_route_is_refused_without_that_signal",
        "tests/test_launch_token.py::TestWhatStaysReachable"
        "::test_a_cross_site_signal_does_not_open_it",
    )),
    ("System proxy environment variables are ignored on purpose", (
        "tests/test_privacy_promises.py::TestTheHttpClientIgnoresTheAmbientEnvironment"
        "::test_an_exported_proxy_does_not_capture_traffic",
        "tests/test_privacy_promises.py::TestTheHttpClientIgnoresTheAmbientEnvironment"
        "::test_the_configured_proxy_is_still_used",
    )),
    ("the app window **cannot** override those three", (
        "tests/test_privacy_promises.py::TestTheLockedProviderPolicy"
        "::test_a_client_cannot_turn_zero_data_retention_off",
        "tests/test_privacy_promises.py::TestTheLockedProviderPolicy"
        "::test_a_client_cannot_opt_into_data_collection",
        "tests/test_privacy_promises.py::TestTheLockedProviderPolicy"
        "::test_a_client_cannot_re_enable_fallbacks",
    )),
    ("HF_HUB_OFFLINE=1", (
        "tests/test_privacy_promises.py::TestTheVoiceEngineGetsNoCredentialsAndNoWayHome"
        "::test_an_exported_proxy_does_not_reach_the_engine",
        "tests/test_privacy_promises.py::TestTheVoiceEngineGetsNoCredentialsAndNoWayHome"
        "::test_offline_is_forced_over_an_inherited_setting",
    )),
    ("A link to a remote image is refused rather than fetched", (
        "tests/test_generated_image_ingest.py"
        "::test_a_remotely_hosted_image_is_refused_not_fetched",
        # test_nothing_is_fetched_for_a_remote_url was registered here too. It
        # ran the same scenario with strictly weaker assertions and was folded
        # into the test above, whose docstring now carries the reason the
        # socket trap - rather than a patched client factory - is what proves
        # this claim.
        "tests/test_generated_image_ingest.py"
        "::test_the_socket_trap_in_the_test_above_actually_works",
    )),
    ("the browser's crash reporter is prevented from starting at all", (
        "tests/test_browser_profile_purge.py::TestCrashReportingIsBlocked"
        "::test_the_path_crashpad_needs_is_taken",
        "tests/test_browser_profile_purge.py::TestCrashReportingIsBlocked"
        "::test_it_works_on_a_profile_that_does_not_exist_yet",
    )),
    ("Those caches are now wiped at launch and exit", (
        "tests/test_browser_profile_purge.py::TestPurgeRemovesConversation"
        "::test_no_byte_of_the_conversation_survives",
    )),
    ("API responses are marked no-store", (
        "tests/test_security_headers.py"
        "::test_a_route_that_chose_its_own_cache_policy_keeps_it",
    )),
    ("Excluding the process heap from crash dumps", (
        "tests/test_win_hardening.py::TestCrashDumpHeapExclusion"
        "::test_windows_accepts_the_no_heap_flag",
        "tests/test_win_hardening.py::TestCrashDumpHeapExclusion"
        "::test_a_refusal_is_reported_as_a_refusal",
    )),
    ('Marking the data folder "do not index"', (
        "tests/test_win_hardening.py::TestSearchIndexExclusion"
        "::test_the_indexer_is_actually_told_to_skip_the_folder",
    )),
    ("Hiding the window from screen capture", (
        "tests/test_win_hardening.py::TestScreenCaptureExclusion"
        "::test_it_stays_off_unless_the_user_asks",
        "tests/test_win_hardening.py::TestScreenCaptureExclusion"
        "::test_a_real_window_is_actually_excluded",
    )),
    ("Blocking the browser crash reporter", (
        "tests/test_browser_profile_purge.py::TestCrashReportingIsBlocked"
        "::test_the_path_crashpad_needs_is_taken",
    )),
    ("Resetting the DLL search path", (
        "tests/test_win_hardening.py::TestTheDllSearchPathIsReset"
        "::test_windows_accepts_the_reset",
        "tests/test_win_hardening.py::TestTheDllSearchPathIsReset"
        "::test_it_actually_clears_a_directory_that_was_set",
    )),
    ("Narrowing the data folder's permissions", (
        "tests/test_win_hardening.py::TestTheDataFolderIsNarrowed"
        "::test_a_widened_folder_is_taken_back",
    )),
    ("writes what it removed to the log", (
        "tests/test_win_hardening.py::TestDataFolderIsNotShared"
        "::test_granting_everyone_access_is_reported",
        "tests/test_win_hardening.py::TestTheDataFolderIsNarrowed"
        "::test_the_launch_path_reports_what_it_took_away",
    )),
    ("it does not touch SYSTEM, Administrators or you", (
        "tests/test_win_hardening.py::TestTheDataFolderIsNarrowed"
        "::test_the_owner_can_still_use_the_folder_afterwards",
    )),
    ("makes no second pass over the files inside", (
        "tests/test_win_hardening.py::TestTheDataFolderIsNarrowed"
        "::test_it_is_not_recursive",
    )),
    ("`salt.bin` and `verifier.bin` do lose the wider access", (
        "tests/test_win_hardening.py::TestTheDataFolderIsNarrowed"
        "::test_the_vault_files_inside_it_are_taken_back_too",
    )),
    ('icacls "%LOCALAPPDATA%\\Elysium" /reset', (
        "tests/test_win_hardening.py::TestTheDataFolderIsNarrowed"
        "::test_the_undo_command_in_security_md_actually_undoes_it",
    )),
    ("no keys", (
        "tests/test_privacy_promises.py::TestTheApiKeyIsNeverHandedBack"
        "::test_storing_and_reading_it_writes_nothing_to_the_log",
    )),
)


#: Real, true, and nothing runnable can prove them - stated here in the
#: reader's own terms rather than papered over. Each one names the sentence
#: it excuses, so a reworded or deleted sentence still fails
#: TestNothingIsAcknowledgedThatIsNotStillWritten, the same drift check
#: PROSE_CLAIMS gets. date and pointer are populated only for
#: ONE_TIME_MEASUREMENT entries; TestAcknowledgedUntestableStaysHonest
#: enforces that split.
ACKNOWLEDGED_UNTESTABLE: tuple[
    tuple[str, ReasonCategory, str, str | None, str | None], ...
] = (
    ("Deleted is not shredded on an SSD", ReasonCategory.HARDWARE,
     "what a controller does with a block after TRIM is firmware behaviour "
     "on hardware this suite does not run on; BitLocker is the actual "
     "answer and the document says so in the same sentence.",
     None, None),
    ("wear levelling can leave the original blocks readable",
     ReasonCategory.HARDWARE,
     "the same SSD remapping fact, stated a second time under 'Deleting "
     "things'; one hardware behaviour, two sentences that reference it.",
     None, None),
    ("Your provider reads your prompts", ReasonCategory.THIRD_PARTY,
     "what OpenRouter's chosen model does with a prompt after zero-data-"
     "retention routing hands it over happens on hardware we do not "
     "control. The routing itself is tested (see 'cannot** override those "
     "three'); what the provider does with what it reads is not "
     "observable from here.",
     None, None),
    ("A voice model you download is code", ReasonCategory.THIRD_PARTY,
     "whether a given checkpoint format can execute code as it loads is a "
     "property of the third-party engine and the file the user chose to "
     "fetch, not of anything in this tree.",
     None, None),
    ("A running unlocked app is unlocked", ReasonCategory.DEFINITIONAL,
     "an unlocked vault's key being reachable by anything running as the "
     "same user is what 'unlocked' means. There is no code path to "
     "exercise and no failure mode to catch; the sentence is the "
     "definition, not a promise about one.",
     None, None),
    ("nothing at the operating-system level stops it from opening a socket",
     ReasonCategory.DEFINITIONAL,
     "this sentence exists specifically to say that no enforcement layer "
     "sits under the configured one. The configured half (HF_HUB_OFFLINE, "
     "stripped proxy vars) is tested; the sentence's whole content is that "
     "nothing further exists to test.",
     None, None),
    ("they are not secret", ReasonCategory.DEFINITIONAL,
     "salt.bin and verifier.bin being safe to disclose is a property of "
     "how scrypt verification works, true by definition of the "
     "construction, not something a unit test demonstrates by running "
     "code.",
     None, None),
    ("voice model weights you downloaded", ReasonCategory.DEFINITIONAL,
     "a bare table-row disclosure ('No', unencrypted) rather than a "
     "protection claim. Registered so the row is not silently upgraded to "
     "a promise later without anyone noticing. The reference clips used to "
     "share this row and no longer do: they ARE testable, they are now their "
     "own row, and they are registered as proven in CLAIMS above.",
     None, None),
    ("any premigrate snapshot beside it", ReasonCategory.DEFINITIONAL,
     "that deleting a folder removes the files inside it is a property of "
     "the filesystem, not of this app. The sentence exists because the "
     "snapshot was in no document at all, so a reader counting what leaves "
     "with the folder could not know it was there; what it claims about "
     "Elysium is only that the file lives under that folder, which the "
     "premigrate_backup_path proofs in CLAIMS already establish.",
     None, None),
    ("the port the server last used", ReasonCategory.DEFINITIONAL,
     "same as the row above: one unencrypted number, nothing to protect, "
     "registered only so the row cannot change unnoticed.",
     None, None),
    ("Anything Windows already extracted stays in its index",
     ReasonCategory.OS_GUARANTEE,
     "the search index is Windows Search's own store, outside the data "
     "folder and outside anything this app can inspect or clear. The "
     "forward-looking half (marking the folder not-indexed) is tested; "
     "what the index already holds from before is not observable from "
     "here.",
     None, None),
    ("copies exist that Python cannot reach, including anything Windows "
     "paged to disk", ReasonCategory.OS_GUARANTEE,
     "what the OS pager does with process memory is outside the "
     "interpreter's visibility. The one buffer Python does own is "
     "overwritten and that part is tested; this sentence is about the "
     "part that is not reachable to test.",
     None, None),
    ("Elysium.exe is not code-signed", ReasonCategory.RELEASE_ARTIFACT,
     "code signing is a property of the release pipeline, applied to a "
     "built binary at publish time. Nothing in this source tree builds or "
     "ships that binary for a test to inspect.",
     None, None),
    ("in three different configurations", ReasonCategory.ONE_TIME_MEASUREMENT,
     "the WebView2-renderer-crash measurement: a manual test performed on "
     "the dev machine on 9 August 2026, recorded with its date and its "
     "three configurations in the win_hardening module docstring. Nothing "
     "here re-runs it; this only proves the pointer still exists and the "
     "sentence was not silently reworded away from what was measured.",
     "2026-08-09", "backend/win_hardening.py:33-51"),
    ("Anything you copy leaves the vault", ReasonCategory.OS_GUARANTEE,
     "what Windows does with the clipboard after a write is the OS's, and "
     "Clipboard History and cross-device sync are user settings this suite "
     "cannot read or change. The one part that IS ours - that the window "
     "cannot opt out - is a consequence of private_mode=False in run_app, "
     "which is asserted where that flag is set, not here.",
     None, None),
    ("Anything you copied to the clipboard", ReasonCategory.OS_GUARANTEE,
     "the same OS behaviour restated under deletion: a clipboard entry, and "
     "a synced copy that is not on this machine at all, are outside every "
     "path this suite can reach.",
     None, None),
)


#: Testable in principle, not tested yet. Not a place to invent a test or
#: fake a proof - a one-line note each, so the debt is explicit and counted
#: instead of growing silently under a claim nobody is watching.
UNPROVEN: tuple[tuple[str, str], ...] = (
    ("never stored",
     "no test scans the vault directory for the passphrase itself the way "
     "test_privacy_at_rest.py scans it for message text; testable the same "
     "way, not written."),
    ("nothing to the Windows registry",
     "no test enumerates registry writes across the app and asserts there "
     "are none; testable with a monkeypatched winreg, not written."),
    ("The only registry access anywhere in the code is a read",
     "same gap as above, the second place the document makes the claim: "
     "nothing here proves the ONE read exists and is the only registry "
     "call in the tree."),
    ("including the journal files",
     "SQLCipher's own guarantee that -wal/-shm journal files are "
     "encrypted is trusted, not independently checked the way "
     "test_an_image_blob_is_not_findable_in_the_raw_bytes checks the main "
     "file."),
    ("no passphrases",
     "message text and API keys are proven not to reach the log "
     "elsewhere in this file; nothing tests that a passphrase specifically "
     "cannot leak into a log line, for instance through an exception "
     "message during a failed unlock."),
    ("downloads from GitHub and PyPI, uploads nothing",
     "the installer's own network behaviour (which hosts, one-way "
     "transfer) is not exercised by any test; testable with a mocked "
     "download harness, not written."),
    ("Not verified as impossible",
     "the document's own hedge about the legacy Credential Manager entry, "
     "registered here so the debt it already names is tracked by code and "
     "not just by a sentence."),
    ("Nothing purges these",
     "the lock, shutdown and launch paths are each tested for the AUDIO "
     "cache; no test asserts the opposite for voice/refs - that a reference "
     "clip and its transcript are still on disk after a lock and a relaunch. "
     "Testable the same way test_audio_cache_launch_wipe.py tests the cache, "
     "and it is the more valuable half, because here survival is the "
     "behaviour being promised."),
    ("no screen reports it and no button removes it",
     "nothing asserts that /vault/status omits app.db.premigrate.bak, nor "
     "that no route deletes it. Testable directly (the status payload names "
     "three sidecar families and this is in none of them), and it is written "
     "down here rather than proven because the honest fix is a UI that "
     "reports the file, at which point the sentence changes rather than "
     "gaining a test."),
    ("Deleting every chat does not delete what the notebook spent",
     "test_notebook_spend_cap.py proves the ledger accumulates and blocks; "
     "nothing deletes every chat and then asserts the rows survive, and "
     "nothing asserts no code path prunes them. Testable in one short test, "
     "not written."),
    ("It does not look at your Desktop, your user profile, your Documents, "
     "or anywhere else",
     "true by inspection (narrow_data_dir is only ever called with the "
     "app's own data directory), but no test asserts that as a standing "
     "invariant across every call site in the app."),
)


#: The two "What is NOT protected" claims that are one-time measurements
#: with NO recorded script anywhere in this tree, unlike the crash-dump one
#: above. Registering either as ONE_TIME_MEASUREMENT without a real pointer
#: would be exactly the fabricated proof this file exists to refuse, so they
#: are named here instead: TestTheKnownGapsStayNamed asserts the orphan set
#: from _promise_lines() is EXACTLY this pair. The suite stays green, and
#: the gap stays visible and machine-checked - it fails loudly the moment a
#: THIRD line goes unregistered, or the moment someone tries to shrink this
#: tuple without actually writing the measurement down somewhere real.
KNOWN_UNREGISTERED_GAPS: tuple[str, ...] = (
    "Hidden extra streams are not overwritten",
    "Files with names Windows cannot open stay put",
)


def _document() -> str:
    return _SECURITY.read_text(encoding="utf-8")


def _promise_lines() -> list[str]:
    """Every bulleted line and table row in the document, one promise each.

    Two shapes, same as the README's version, plus one adjustment this
    document needs: a README bullet was one physical line, but SECURITY.md
    wraps a bullet's sentence across two or three lines of prose. Taking only
    the first physical line would silently hide the back half of nearly
    every bullet from every marker check below, so a bullet's continuation
    lines (indented, not themselves a new bullet, heading or table row) are
    joined onto it before matching. A table row here never wraps, so those
    are taken exactly as the README took them.
    """
    lines: list[str] = []
    raw = _document().splitlines()
    i, n = 0, len(raw)
    while i < n:
        line = raw[i].strip()
        if line.startswith("| `"):
            lines.append(re.sub(r"\s+", " ", line))
            i += 1
            continue
        if line.startswith("- "):
            block = [line]
            j = i + 1
            while j < n:
                cont = raw[j].strip()
                if not cont or cont.startswith(("- ", "#", "|")):
                    break
                block.append(cont)
                j += 1
            lines.append(re.sub(r"\s+", " ", " ".join(block)))
            i = j
            continue
        i += 1
    return lines


def _resolve_backend(node_id: str) -> bool:
    """True if this pytest node id names something that exists.

    Copied unchanged from test_privacy_contract.py, attribute walk and all,
    including the callable check: a registered name that resolves to a
    string or any other non-callable attribute is not something pytest can
    collect, so it is not a proof.
    """
    file_part, _, rest = node_id.partition("::")
    module_name = file_part.removesuffix(".py").replace("/", ".")
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return False
    target = module
    for part in rest.split("::"):
        target = getattr(target, part, None)
        if target is None:
            return False
    return callable(target)


def _resolve_frontend(node_id: str) -> bool:
    path_part, _, name = node_id.partition("::")
    path = _REPO / path_part
    if not path.is_file():
        return False
    return f'"{name}' in path.read_text(encoding="utf-8")


def _resolve(node_id: str) -> bool:
    if node_id.startswith("frontend/"):
        return _resolve_frontend(node_id)
    return _resolve_backend(node_id)


def _all_proven_claims() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return CLAIMS + PROSE_CLAIMS


def _all_registered_markers() -> list[str]:
    """Every marker registered anywhere, regardless of which table it is in.

    A bulleted line counts as covered if ANY table names it - a proof, an
    acknowledged gap, or a counted piece of debt. Only the line's own
    presence matters here; whether the registration is a real proof is a
    separate question the other tests answer.
    """
    markers = [m for m, _ in CLAIMS]
    markers += [m for m, _ in PROSE_CLAIMS]
    markers += [m for m, _, _, _, _ in ACKNOWLEDGED_UNTESTABLE]
    markers += [m for m, _ in UNPROVEN]
    return markers


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_POINTER_RE = re.compile(r"^(?P<path>[^:]+):(?P<start>\d+)(?:-(?P<end>\d+))?$")


def _pointer_problem(pointer: str) -> str | None:
    match = _POINTER_RE.match(pointer)
    if not match:
        return f"{pointer!r} is not a file:line or file:line-line pointer"
    path = _REPO / match.group("path")
    if not path.is_file():
        return f"{pointer!r} names a file that does not exist"
    total = len(path.read_text(encoding="utf-8").splitlines())
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if not (1 <= start <= end <= total):
        return f"{pointer!r} is out of range for a {total}-line file"
    return None


def _acknowledged_problems(
    entries: tuple[tuple[str, ReasonCategory, str, str | None, str | None], ...]
) -> list[str]:
    """Every reason an ACKNOWLEDGED_UNTESTABLE entry is not honestly filled in.

    A one-time measurement without a date and a real pointer is a claim
    dressed up as a measurement; a plain untestable entry that carries a
    date or a pointer nobody asked for is a sign the wrong category was
    picked. Both are reported, neither is silently accepted.
    """
    problems: list[str] = []
    for marker, category, justification, date, pointer in entries:
        if not isinstance(category, ReasonCategory):
            problems.append(f"{marker!r}: category is not a ReasonCategory member")
            continue
        if not justification:
            problems.append(f"{marker!r}: needs a justification")
        if category is ReasonCategory.ONE_TIME_MEASUREMENT:
            if not date or not _DATE_RE.match(date):
                problems.append(
                    f"{marker!r}: ONE_TIME_MEASUREMENT needs a YYYY-MM-DD date")
            if not pointer:
                problems.append(
                    f"{marker!r}: ONE_TIME_MEASUREMENT needs a file:line pointer")
            else:
                pointer_problem = _pointer_problem(pointer)
                if pointer_problem:
                    problems.append(f"{marker!r}: {pointer_problem}")
        elif date is not None or pointer is not None:
            problems.append(
                f"{marker!r}: only ONE_TIME_MEASUREMENT entries carry "
                "a date or a pointer")
    return problems


#: The whole file, normalised for whitespace. There is no section to slice
#: out here the way the README's Privacy Contract could be - a false or
#: reworded sentence could land in any paragraph, any table cell, any code
#: sample - so this covers all of it. Updating this constant is the
#: deliberate act that means a human decided what a change claims and
#: registered a proof for it.
DOCUMENT_DIGEST = "12021284eca91b9ed60b438fccd871c37a240157999caa9658752c1b61301b09"


class TestEveryProvenClaimHasAProof:
    def test_the_proof_exists(self) -> None:
        broken = []
        for marker, proofs in _all_proven_claims():
            missing = [node for node in proofs if not _resolve(node)]
            if missing:
                broken.append((marker, missing))
        assert broken == [], (
            "a proof registered here does not exist:\n  "
            + "\n  ".join(f"{m!r}: {p}" for m, p in broken))

    def test_every_proven_claim_registers_at_least_one_proof(self) -> None:
        empty = [marker for marker, proofs in _all_proven_claims() if not proofs]
        assert empty == []

    def test_the_claim_is_still_in_the_document(self) -> None:
        haystack = re.sub(r"\s+", " ", _document())
        missing = [
            marker for marker, _ in _all_proven_claims()
            if re.sub(r"\s+", " ", marker) not in haystack
        ]
        assert missing == [], (
            "these markers are registered as proven here but are no "
            f"longer in SECURITY.md: {missing}")


class TestAcknowledgedUntestableStaysHonest:
    def test_every_entry_is_filled_in_correctly(self) -> None:
        assert _acknowledged_problems(ACKNOWLEDGED_UNTESTABLE) == []

    def test_the_claim_is_still_in_the_document(self) -> None:
        haystack = re.sub(r"\s+", " ", _document())
        missing = [
            marker for marker, _, _, _, _ in ACKNOWLEDGED_UNTESTABLE
            if re.sub(r"\s+", " ", marker) not in haystack
        ]
        assert missing == [], (
            "these markers are acknowledged as untestable here but are no "
            f"longer in SECURITY.md: {missing}")

    def test_the_category_enum_is_actually_closed(self) -> None:
        with pytest.raises(ValueError):
            ReasonCategory("SOMETHING_NOBODY_AGREED_TO")


class TestTheUnprovenDebtIsExplicit:
    def test_every_entry_has_a_real_note(self) -> None:
        empty = [marker for marker, note in UNPROVEN if not note]
        assert empty == []

    def test_no_marker_is_registered_twice(self) -> None:
        markers = [marker for marker, _ in UNPROVEN]
        assert len(markers) == len(set(markers))

    def test_the_claim_is_still_in_the_document(self) -> None:
        haystack = re.sub(r"\s+", " ", _document())
        missing = [
            marker for marker, _ in UNPROVEN
            if re.sub(r"\s+", " ", marker) not in haystack
        ]
        assert missing == [], (
            "these markers are counted as unproven debt here but are no "
            f"longer in SECURITY.md: {missing}")

    def test_this_is_the_only_debt_on_the_books(self) -> None:
        # Not zero - that would mean either every claim in the document is
        # proven (unlikely for a document this size) or the debt is being
        # tracked somewhere nobody can see it grow.
        assert len(UNPROVEN) > 0


class TestTheKnownGapsStayNamed:
    """The line parser's honest failure mode: a bullet nobody registered.

    Every OTHER bulleted line and table row must be covered by CLAIMS,
    PROSE_CLAIMS, ACKNOWLEDGED_UNTESTABLE or UNPROVEN. These two are not,
    because no recorded measurement backs either one, and inventing a proof
    or a date for them would be worse than leaving them out. The test
    asserts the orphan set is EXACTLY this pair - not empty, not a
    superset - so the suite reports a new unregistered claim the moment one
    appears, and reports a stale entry here the moment either gap actually
    gets a real proof.
    """

    def test_the_orphan_set_is_exactly_the_known_gap(self) -> None:
        markers = _all_registered_markers()
        orphans = [
            line for line in _promise_lines()
            if not any(marker in line for marker in markers)
        ]
        unexplained = [
            line for line in orphans
            if not any(gap in line for gap in KNOWN_UNREGISTERED_GAPS)
        ]
        assert unexplained == [], (
            "these SECURITY.md lines have no registered proof, "
            "acknowledgement or counted debt:\n  " + "\n  ".join(unexplained))
        still_open = [
            gap for gap in KNOWN_UNREGISTERED_GAPS
            if not any(gap in line for line in orphans)
        ]
        assert still_open == [], (
            "a KNOWN_UNREGISTERED_GAPS entry now has a proof somewhere and "
            f"should be removed from that tuple: {still_open}"
        )


class TestNothingInTheDocumentChangesUnnoticed:
    def test_the_document_matches_the_registered_digest(self) -> None:
        current = hashlib.sha256(
            re.sub(r"\s+", " ", _document()).strip().encode("utf-8")
        ).hexdigest()
        assert current == DOCUMENT_DIGEST, (
            "SECURITY.md changed. Whatever was added or reworded is a claim "
            "about this software. Register a proof for it in CLAIMS or "
            "PROSE_CLAIMS, acknowledge it in ACKNOWLEDGED_UNTESTABLE, or "
            "count it in UNPROVEN, then update DOCUMENT_DIGEST to: "
            f"{current}"
        )


class TestTheRegistryCanActuallyFail:
    """The guard needs its own guard.

    test_release_sync.py taught this the hard way: its spec-drift check
    compared two empty sets for months because the regex matched the wrong
    quote style. A check that cannot fail is indistinguishable from one that
    passes.
    """

    def test_the_document_was_actually_found(self) -> None:
        text = _document()
        assert "## What is NOT protected" in text
        assert len(text) > 5000

    def test_promise_lines_are_actually_being_extracted(self) -> None:
        lines = _promise_lines()
        assert len(lines) >= 15, f"only found {len(lines)} promises to check"
        assert any("one address on the internet" in line for line in lines)

    def test_a_wrapped_bullet_is_joined_not_truncated(self) -> None:
        # The one adjustment this file makes to the README's mechanism.
        # Without it, "not even as a plain database" - the back half of the
        # short version's first bullet - would never be visible to any
        # marker check, and this whole class of claim would silently pass
        # unregistered forever.
        lines = _promise_lines()
        assert any("not even as a plain database" in line for line in lines)

    def test_the_digest_would_notice_a_reworded_sentence(self) -> None:
        altered = _document().replace("SECURITY.md", "SECURITY.md ")
        assert hashlib.sha256(
            re.sub(r"\s+", " ", altered + " and one more promise").strip()
            .encode("utf-8")).hexdigest() != DOCUMENT_DIGEST

    def test_an_unregistered_promise_would_be_caught(self) -> None:
        invented = "- The app promises something nobody has tested"
        markers = _all_registered_markers()
        assert not any(marker in invented for marker in markers)

    def test_a_proof_that_does_not_exist_is_reported_missing(self) -> None:
        assert _resolve("tests/test_privacy_promises.py::NoSuchClass"
                        "::test_nothing") is False
        assert _resolve("tests/test_no_such_module.py::test_nothing") is False
        assert _resolve("frontend/src/test/nope.test.ts::S-99") is False

    def test_a_registered_name_that_cannot_run_is_not_a_proof(self) -> None:
        assert _resolve(
            "tests/test_security_contract.py::DOCUMENT_DIGEST") is False, (
            "a string attribute resolved as if it were a test"
        )
        assert _resolve("tests/test_security_contract.py::_resolve") is True

    def test_a_proof_that_does_exist_resolves(self) -> None:
        assert _resolve(
            "tests/test_privacy_at_rest.py::TestTheDatabaseFileIsCiphertext"
            "::test_stdlib_sqlite_cannot_read_it") is True
        assert _resolve(
            "frontend/src/test/static-safety.test.ts::S-09") is True

    def test_the_category_enum_actually_rejects_an_unknown_value(self) -> None:
        bad = (
            ("a claim", "MADE_UP_CATEGORY", "an excuse", None, None),
        )
        problems = []
        for marker, category, justification, date, pointer in bad:
            if not isinstance(category, ReasonCategory):
                problems.append(marker)
        assert problems == ["a claim"]

    def test_a_one_time_measurement_without_a_date_is_reported(self) -> None:
        bad = (
            ("something measured once", ReasonCategory.ONE_TIME_MEASUREMENT,
             "measured, allegedly", None, "backend/win_hardening.py:1"),
        )
        assert _acknowledged_problems(bad) != []

    def test_a_one_time_measurement_with_a_fake_pointer_is_reported(self) -> None:
        bad = (
            ("something measured once", ReasonCategory.ONE_TIME_MEASUREMENT,
             "measured, allegedly", "2026-08-09", "backend/no_such_file.py:1"),
        )
        assert _acknowledged_problems(bad) != []

    def test_a_well_formed_acknowledged_entry_passes(self) -> None:
        good = (
            ("Deleted is not shredded on an SSD", ReasonCategory.HARDWARE,
             "a real reason", None, None),
        )
        assert _acknowledged_problems(good) == []
