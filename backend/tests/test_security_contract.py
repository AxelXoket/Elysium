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
#: what TestTheKnownGapsStayNamed exists to catch. A table row is extracted
#: twice, whole and again as its answer cell, so a row's ANSWER needs its own
#: marker and cannot ride on one taken from the filename that labels it.
#:
#: Anything that is neither a bullet nor a row belongs in PROSE_CLAIMS. The
#: boundary is not decoration: a marker registered here is implicitly a
#: claim about a line the parser can see, and one that is really prose sits
#: in this table pointing at nothing the orphan check will ever reach.
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
    # "Settings > Secrets" named a tab that does not exist even before this
    # correction - the tab's label has always been Security (the persisted
    # value stays "secrets" internally; see RightPanel.tsx). The two backend
    # proofs below cover the ROUTE only, so the screen half - that the tab
    # actually lists the copy and a button actually removes it - now has its
    # own frontend proofs alongside them.
    ("Settings > Security lists it and deletes it on request", (
        "tests/test_plaintext_backup_discard.py::TestTheCopyIsVisible"
        "::test_status_reports_a_plaintext_backup",
        "tests/test_plaintext_backup_discard.py::TestTheCopyCanBeRemoved"
        "::test_discarding_deletes_it",
        "frontend/src/test/components/PlaintextBackupNotice.test.tsx"
        "::says so whenever one is on disk",
        "frontend/src/test/components/PlaintextBackupNotice.test.tsx"
        "::asks the backend once confirmed",
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
    # The row above used to end "no screen reports it and no button removes
    # it", and that sentence sat in UNPROVEN with its own exit written into
    # it: the honest fix is a UI that reports the file, at which point the
    # sentence changes rather than gaining a test. The UI was built, so the
    # sentence changed and the debt is retired rather than carried. It named
    # "Settings > Secrets", a tab that has never existed under that label -
    # the tab is Security - and its two proofs covered the ROUTE only, not
    # the screen the sentence actually claims; both are fixed here together.
    ("Settings > Security now lists it and a button removes it", (
        "tests/test_vault_premigrate_discard.py::TestStatusReportsPresence"
        "::test_present_but_unreadable_while_locked",
        "tests/test_vault_premigrate_discard.py::TestDiscardRoute"
        "::test_removes_a_snapshot_that_opens_with_the_current_key",
        "frontend/src/test/components/PremigrateBackupNotice.test.tsx"
        "::says so when one is on disk",
        "frontend/src/test/components/PremigrateBackupNotice.test.tsx"
        "::asks the backend once confirmed",
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
    # The two runtime-file rows, each keyed on its ANSWER cell rather than on
    # the filename that labels it. Both say a reset destroys the file, and
    # _reset_runtime_files shreds elysium.log, elysium.log.1 and port in one
    # loop, so one test covers both halves.
    #
    # The `port` row is why _promise_lines() now emits a row's answer cell
    # separately. It read "a vault reset does not touch it either" long after
    # the reset started shredding it, and nothing here objected: the only
    # registration pointing at that row was an ACKNOWLEDGED_UNTESTABLE entry
    # keyed on "the port the server last used", the label. A row that names a
    # file is not a claim about what happens to it.
    ("A vault reset now shreds it, along with its rotated", (
        "tests/test_vault_reset_hardening.py::TestRuntimeFilesLeaveNoTrace"
        "::test_log_and_port_files_are_removed",
    )),
    ("A vault reset shreds it anyway, alongside the log", (
        "tests/test_vault_reset_hardening.py::TestRuntimeFilesLeaveNoTrace"
        "::test_log_and_port_files_are_removed",
    )),
    # These two rows used to say "nothing removes it for you" and "Nothing
    # purges these" without qualification - both false once /vault/reset
    # existed: _reset_premigrate_family globs and shreds the .unreadable-*
    # name, and the same route shreds TTS_REFS_DIR whole. One test builds
    # both real artefacts and checks both are gone after a reset.
    ("nothing short of a vault reset removes it for you", (
        "tests/test_vault_reset.py::TestTheFullWipe"
        "::test_every_ground_truth_artefact_is_destroyed",
    )),
    ("delete that voice, delete the folder, or reset the vault", (
        "tests/test_vault_reset.py::TestTheFullWipe"
        "::test_every_ground_truth_artefact_is_destroyed",
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
    # The voice/refs row grew three disclosures this round, and each one is
    # keyed on its own words rather than riding on the clip-and-transcript
    # marker that was already there. That marker covers the two files the row
    # has always named; it says nothing about a label, a derived voiceprint,
    # or how the folder is spelled, and letting it stand in for those would be
    # the exact half-a-row failure the `port` row taught this file.
    ("the label you gave that voice", (
        "tests/test_voice_ref_dir_naming.py::TestNewVoicesGetAnOpaqueFolder"
        "::test_the_id_still_resolves_and_still_round_trips_over_the_api_shape",
    )),
    ("The FOLDER is named with a one-way hash", (
        "tests/test_voice_ref_dir_naming.py::TestNewVoicesGetAnOpaqueFolder"
        "::test_the_folder_name_is_not_the_id_or_the_label",
        "tests/test_voice_ref_dir_naming.py::TestNewVoicesGetAnOpaqueFolder"
        "::test_listing_the_refs_root_shows_no_slug_at_all",
        "tests/test_voice_ref_dir_naming.py::TestNewVoicesGetAnOpaqueFolder"
        "::test_two_different_labels_never_collide_on_disk",
    )),
    # Same proof as "the label you gave that voice" and deliberately so: the
    # test saves a voice and reads its label back through describe(), which
    # can only come from the file inside the hashed folder. One behaviour,
    # two sentences that disclose it - the row's label cell and its answer.
    ("the file inside it still names the voice", (
        "tests/test_voice_ref_dir_naming.py::TestNewVoicesGetAnOpaqueFolder"
        "::test_the_id_still_resolves_and_still_round_trips_over_the_api_shape",
    )),
    # The accessibility switch's first limit. What is testable without a
    # window is that the argument is in the environment BEFORE the window is
    # created - which is what "at startup" means here. That it can then never
    # be changed is WebView2's behaviour and is acknowledged separately.
    ("It takes effect at startup only", (
        "tests/test_accessibility_privacy.py::TestItIsArmedBeforeTheWindowExists"
        "::test_the_argument_is_in_place_when_the_window_is_created",
        "tests/test_accessibility_privacy.py::TestTheLaunchPathArmsIt"
        "::test_harden_reports_it",
    )),
    # Two halves of the screenshot bullet, registered apart on purpose. This
    # one is the DEFAULT, which is code and is tested. The black-buffer half
    # beside it is a measurement and is acknowledged, not proven.
    ("Screenshots are not blocked out of the box", (
        "tests/test_win_hardening.py::TestScreenCaptureExclusion"
        "::test_it_stays_off_unless_the_user_asks",
        "tests/test_screen_privacy.py::TestTheSettingFailsClosed"
        "::test_absent_means_off",
    )),
    # The bullet this section was missing entirely. Registered on its own
    # words because the sentence beside it - "delete that voice, delete the
    # folder, or reset the vault" - is an OLD marker from the table row, and
    # a bullet that borrows one is a bullet nobody actually checked.
    ("A reference clip is a recording of you, and it stays", (
        "tests/test_tts_refs.py::TestSavingAClip"
        "::test_a_clip_and_its_words_are_stored_together",
        "tests/test_tts_refs.py::TestDeleting::test_deleting_removes_the_folder",
        "tests/test_vault_reset.py::TestTheFullWipe"
        "::test_every_ground_truth_artefact_is_destroyed",
    )),
)


#: Claims made in paragraphs, table cells and code samples rather than as a
#: top-level bullet or a routing-table row. Anchored by marker only, same
#: asymmetry as the README's PROSE_CLAIMS: a reworded or deleted sentence
#: fails, a brand new one does not, and that is why DOCUMENT_DIGEST exists.
PROSE_CLAIMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # The traceback debt, stated in the document rather than buried. The
    # number IS the assertion: the ledger fails in both directions, so it
    # cannot grow quietly and a paid debt has to be recorded as paid.
    ("there are forty-six places that do it, across sixteen", (
        "tests/test_log_identifier_privacy.py"
        "::test_no_new_traceback_leak_anywhere_in_the_tree",
    )),
    # The model id left the browser profile this round. It is a name a
    # person reads on screen, which is the one thing the owner's rule keeps
    # out of anywhere a locked vault cannot protect.
    ("The model you last picked used to be there and is not any more", (
        "tests/test_model_selection.py::TestTheRouteRoundTrips"
        "::test_setting_it_is_read_back",
        "tests/test_model_selection.py::TestTheRouteRoundTrips"
        "::test_nothing_chosen_yet_reads_as_null",
    )),
    # The two sentences this document gained when it stopped describing the
    # interrupted-extraction case "too kindly". Both are claims about money
    # and about a second copy of a conversation leaving the machine, so both
    # get a named proof rather than resting on the digest alone.
    ("It is now marked as a failed call and never retried", (
        "tests/test_notebook_paid_once.py::TestACallMadeAndNeverSettled"
        "::test_the_range_is_not_sent_a_second_time",
        "tests/test_notebook_paid_once.py::TestACallMadeAndNeverSettled"
        "::test_the_orphaned_row_is_closed_out",
    )),
    ("against the trace its own call left before it was sent", (
        "tests/test_notebook_paid_once.py"
        "::TestAReplyThatArrivesAfterItsQuestionWasWithdrawn"
        "::test_the_notes_are_not_written",
        "tests/test_notebook_paid_once.py"
        "::TestAReplyThatArrivesAfterItsQuestionWasWithdrawn"
        "::test_the_range_stays_unread",
    )),
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
    # The Notes tab's dry-run preview claims a call against the SAME daily
    # ledger the background note reader spends from - proven by driving the
    # cap to zero and watching the preview route refuse exactly like the
    # reader would, rather than opening a side door around the block.
    ("shared by the note reader and the preview alike", (
        "tests/test_notebook_spend_cap.py::TestTheRouteIsWiredToTheLedger"
        "::test_the_dry_run_refuses_once_the_day_is_spent",
    )),
    # /vault/reset, added when this document first named the route at all.
    # Four claims from its new section, each tied to the test that would
    # fail if the route stopped behaving the way the sentence says.
    ("refuses outright with HTTP 409 before the confirmation phrase is "
     "even read", (
        "tests/test_vault_reset.py::TestRefusesWhileUnlocked"
        "::test_refuses_and_destroys_nothing",
        "tests/test_vault_reset.py::TestRefusesWhileUnlocked"
        "::test_the_check_runs_before_the_confirmation_phrase_is_even_read",
    )),
    ("requires a typed confirmation phrase, checked against a value only "
     "the backend decides", (
        "tests/test_vault_reset.py::TestRefusesTheWrongConfirmationPhrase"
        "::test_refuses_and_destroys_nothing",
        "tests/test_vault_reset.py::TestRefusesTheWrongConfirmationPhrase"
        "::test_surrounding_whitespace_is_forgiven",
    )),
    ("the database and every backup family beside it (plaintext, orphaned, "
     "rotation, and both premigrate names, including the one moved aside "
     "as unreadable)", (
        "tests/test_vault_reset.py::TestTheFullWipe"
        "::test_every_ground_truth_artefact_is_destroyed",
    )),
    ('a clean run reports `{"ok": true, "left": []}`', (
        "tests/test_vault_reset.py::TestTheFullWipe"
        "::test_every_ground_truth_artefact_is_destroyed",
    )),
    # This sat in CLAIMS, whose markers are supposed to come from a bulleted
    # line or a table row. The sentence is neither; it is prose, and this is
    # the table prose belongs in. The marker was also the bare words "It also
    # shreds" - fifteen generic characters, matched by substring - so it
    # would have vouched for any future line that happened to contain them.
    # It named exactly one sentence today and would have kept saying yes to
    # sentences nobody wrote yet.
    #
    # Replaces an UNPROVEN entry that read "It does not touch elysium.log or
    # port". True when written, false now: the log names chat and note ids,
    # so a wiped vault was leaving a plaintext record of which chats had held
    # notes beside the vault that no longer existed. The sweep is the promise
    # now, and it has a test.
    ("It also shreds `elysium.log`, its rotated `elysium.log.1` and "
     "`port`", (
        "tests/test_vault_reset_hardening.py::TestRuntimeFilesLeaveNoTrace"
        "::test_log_and_port_files_are_removed",
    )),
    # ── The accessibility tree ────────────────────────────────────────────
    # The only default-ON protection in the app, so the default itself is the
    # claim that most needs a test rather than a sentence. Three proofs: it is
    # on when the variable is absent, the launch path really arms it, and the
    # argument really lands in the variable WebView2 reads.
    ("A switch closes it, and it is ON unless you turn it off", (
        "tests/test_accessibility_privacy.py::TestTheDefaultIsOn"
        "::test_nobody_asked_and_it_is_on",
        "tests/test_accessibility_privacy.py::TestTheLaunchPathArmsIt"
        "::test_harden_reports_it",
        "tests/test_accessibility_privacy.py::TestArmingTheSwitch"
        "::test_it_puts_the_argument_where_webview2_will_find_it",
    )),
    # The typo clause. Worth its own proof precisely because it is the kind of
    # sentence a document asserts and nobody checks: the positive control is
    # that "0" turns it off, and the negative one is that nothing else does.
    ("Exactly `0` turns it off", (
        "tests/test_accessibility_privacy.py::TestTheDefaultIsOn"
        "::test_exactly_zero_turns_it_off",
        "tests/test_accessibility_privacy.py::TestTheDefaultIsOn"
        "::test_anything_else_leaves_it_on",
        "tests/test_accessibility_privacy.py::TestTheLaunchPathArmsIt"
        "::test_harden_respects_the_refusal",
    )),
    ("the app asks the browser process afterwards what it actually received", (
        "tests/test_accessibility_privacy.py::TestTheVerdictHasThreeAnswers"
        "::test_the_flag_on_every_browser_process_is_a_yes",
        "tests/test_accessibility_privacy.py::TestTheVerdictHasThreeAnswers"
        "::test_one_browser_process_without_it_is_a_no",
        "tests/test_accessibility_privacy.py::TestTheVerdictHasThreeAnswers"
        "::test_the_report_says_so_out_loud",
    )),
    ("It has three answers rather than two", (
        "tests/test_accessibility_privacy.py::TestTheVerdictHasThreeAnswers"
        "::test_no_browser_process_yet_is_unknown",
        "tests/test_accessibility_privacy.py::TestTheVerdictHasThreeAnswers"
        "::test_an_unreadable_process_is_unknown",
        "tests/test_accessibility_privacy.py::TestTheVerdictHasThreeAnswers"
        "::test_a_broken_reader_never_raises_on_the_launch_path",
    )),
    # ── The log ───────────────────────────────────────────────────────────
    # The owner's rule itself. Two proofs for its two halves: the tree-wide
    # AST gate is what keeps a NAME out, and the at-rest test is what keeps
    # message CONTENT out of a log written by a real completed turn.
    ("a numeric id outside the vault is acceptable; a name you read on "
     "screen, or anything from inside the vault, never", (
        "tests/test_log_identifier_privacy.py"
        "::test_no_new_content_leak_anywhere_in_the_tree",
        "tests/test_privacy_at_rest.py::TestTheLogNeverCarriesWhatWasSaid"
        "::test_a_completed_turn_logs_no_message_text",
    )),
    # "fails the build" is a claim about the gate, so one of its proofs has to
    # be that the gate can fire at all - a scanner that matched nothing would
    # pass the two tree-wide sweeps every single time.
    ("fails the build on a value that can carry content or a name", (
        "tests/test_log_identifier_privacy.py"
        "::test_no_new_content_leak_anywhere_in_the_tree",
        "tests/test_log_identifier_privacy.py"
        "::test_no_new_traceback_leak_anywhere_in_the_tree",
        "tests/test_log_identifier_privacy.py::TestTheScannerCanActuallyFire"
        "::test_a_raw_exception_is_caught",
    )),
    # The residual, and the number in it is not decoration. The gate's ledger
    # pins tts/refs.py at exactly four content hits and fails in BOTH
    # directions - a fifth is a new leak, a third is a fix nobody wrote down -
    # so the sentence "four log lines" is the assertion, not a summary of it.
    ("Four log lines print it", (
        "tests/test_log_identifier_privacy.py"
        "::test_no_new_content_leak_anywhere_in_the_tree",
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
    ("Files you chose and put there", ReasonCategory.DEFINITIONAL,
     "a bare table-row disclosure ('No', unencrypted) rather than a "
     "protection claim: the weights arrived because the user fetched them. "
     "Registered so the row is not silently upgraded to a promise later "
     "without anyone noticing. The reference clips used to share this row "
     "and no longer do: they ARE testable, they are now their own row, and "
     "they are registered as proven in CLAIMS above. This entry used to be "
     "keyed on 'voice model weights you downloaded' - the row's LABEL - "
     "which let it vouch for the answer cell beside it; the rest of that "
     "cell is real and untested, and is counted in UNPROVEN instead.",
     None, None),
    ("any premigrate snapshot beside it", ReasonCategory.DEFINITIONAL,
     "that deleting a folder removes the files inside it is a property of "
     "the filesystem, not of this app. The sentence exists because the "
     "snapshot was in no document at all, so a reader counting what leaves "
     "with the folder could not know it was there; what it claims about "
     "Elysium is only that the file lives under that folder, which the "
     "premigrate_backup_path proofs in CLAIMS already establish.",
     None, None),
    ("one number with nothing in it to protect", ReasonCategory.DEFINITIONAL,
     "same as the row above: a localhost TCP port is one unencrypted "
     "number, registered only so the disclosure cannot change unnoticed. "
     "This entry used to be keyed on 'the port the server last used', the "
     "row's LABEL, and so it went on certifying the row while the answer "
     "beside it said a vault reset does not touch the file and "
     "_reset_runtime_files was shredding it. What the reset does to this "
     "file is a separate claim with a separate proof, in CLAIMS above.",
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
    ("Locking the vault does not take it back", ReasonCategory.OS_GUARANTEE,
     "the third face of the clipboard entries above, and the one a reader is "
     "most likely to get wrong. What the lock DOES is tested (the key buffer "
     "is overwritten); that the Windows clipboard is untouched by it is a "
     "statement about a store this app never writes to and this suite cannot "
     "read. Nothing here can observe a clipboard entry surviving, because "
     "nothing here can observe a clipboard entry.",
     None, None),
    # A reading taken off this machine, recorded HERE because the agent that
    # took it owns this file and SECURITY.md and nothing else, so there is no
    # module docstring of its own to point at. The reading, in full, on
    # 2026-08-20: HKCU\Software\Microsoft\Clipboard, value
    # EnableClipboardHistory = 1, with CloudClipboardAutomaticUpload unset.
    # Nobody set it for Elysium; it was simply already on, which is the whole
    # content of the sentence in the document. What it does NOT establish is
    # what a fresh Windows install does - one machine is one machine, and the
    # document says "the machine this was measured on" for that reason.
    ("it was already on, switched on by nobody for this app, on the machine "
     "this was measured on", ReasonCategory.ONE_TIME_MEASUREMENT,
     "a registry read on the development machine, recorded in the comment "
     "directly above this entry with the key, the value and the date. "
     "Nothing runnable re-checks it: a test that read the owner's real "
     "clipboard setting would be a test whose result depends on the machine "
     "it runs on, which is the opposite of a gate.",
     "2026-08-20", "backend/tests/test_security_contract.py:814-822"),
    # ── The accessibility tree, and the ceiling under it ──────────────────
    ("read the whole transcript out of it: chat title, character name, "
     "message bodies, verbatim", ReasonCategory.ONE_TIME_MEASUREMENT,
     "the probe an audit built and ran against a real window on the dev "
     "machine. It is recorded in the docstring the pointer names, and it is "
     "re-runnable by hand as tests/accessibility_tree_harness.py, but it "
     "cannot be a collected test: it needs a real window on a real desktop "
     "and takes about a minute, and a test like that is a test people learn "
     "to skip.",
     "2026-08-20", "backend/tests/test_accessibility_privacy.py:1-8"),
    ("the probe recovered the same strings with the flag confirmed set",
     ReasonCategory.ONE_TIME_MEASUREMENT,
     "the second run of that same probe, with SetWindowDisplayAffinity "
     "verified at 0x11. This is the sentence that stops a reader treating "
     "the screen-capture switch as cover for the accessibility tree, so it "
     "is registered rather than left to the digest.",
     "2026-08-20", "backend/win_hardening.py:114-121"),
    ("come back a fully black buffer, measured",
     ReasonCategory.ONE_TIME_MEASUREMENT,
     "PrintWindow and BitBlt against an excluded window, measured on the dev "
     "machine. The suite proves the affinity FLAG is set on a real window "
     "(test_a_real_window_is_actually_excluded); what the compositor then "
     "hands a capture call is the OS's answer, taken once and written down.",
     "2026-08-20", "backend/win_hardening.py:114-121"),
    ("a harness that opens a real window and attacks it from a second "
     "process", ReasonCategory.ONE_TIME_MEASUREMENT,
     "the document is describing the recorded measurement procedure itself, "
     "so the pointer is that procedure. Registered so the sentence cannot "
     "outlive the harness: if the file is deleted or renamed, the pointer "
     "check fails here rather than the document quietly promising a proof "
     "that no longer exists.",
     "2026-08-20", "backend/tests/accessibility_tree_harness.py:1-30"),
    ("the browser builds one tree and serves both from it",
     ReasonCategory.THIRD_PARTY,
     "Chromium's own architecture: one accessibility tree per frame, cached "
     "in the browser process, served to IAccessible, IAccessible2 and UI "
     "Automation alike. Nothing in this tree implements it and nothing here "
     "can assert it - what this app can do is switch the tree off, which is "
     "proven above.",
     None, None),
    ("Changing it while Elysium is running does nothing at all",
     ReasonCategory.THIRD_PARTY,
     "WebView2 reads browser arguments once, when the browser environment is "
     "created, and exposes no way to change them afterwards. The half that "
     "IS ours - that the argument is in place before the window exists - is "
     "proven in CLAIMS under 'It takes effect at startup only'. This "
     "sentence is about the third party's side of that line.",
     None, None),
    ("Its setting cannot live in the vault", ReasonCategory.DEFINITIONAL,
     "a value that can only be read after the vault is unlocked cannot "
     "govern a decision that has to be made before a passphrase exists. "
     "There is no code path to exercise; the sentence states why one cannot "
     "be written.",
     None, None),
    ("While it is on, a screen reader cannot read Elysium either",
     ReasonCategory.THIRD_PARTY,
     "the switch tells Chromium not to build the accessibility tree, and "
     "that tree is the only thing a screen reader has to read. Whether a "
     "given assistive product then reports nothing, or something degraded, "
     "is that product's behaviour against a browser this project did not "
     "write. Stated as a cost rather than measured across screen readers "
     "nobody here owns.",
     None, None),
    ("The renderer's memory is readable", ReasonCategory.OS_GUARANTEE,
     "the true ceiling on this whole document, and it is Windows' access "
     "model rather than a defect in this app: a process running as a user is "
     "granted a read handle to another process of the same user by default, "
     "and while the vault is unlocked the plaintext must exist in the "
     "renderer. Nothing in this tree can revoke that and no test can "
     "demonstrate its absence, because there is no absence to demonstrate. "
     "Registered as accepted risk, not as debt: UNPROVEN would imply a test "
     "is owed, and none is.",
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
    ("they hold none of your conversation",
     "surfaced by keying the voice/models row on its answer cell instead of "
     "its label. True by inspection - tts/registry.py only ever READS "
     "TTS_MODELS_DIR and no chat, note or audio path writes into it - but "
     "inspection is what this registry refuses to accept as a proof. "
     "Testable as a standing invariant over every write site the way the "
     "narrow_data_dir gap below is, not written."),
    ("Nothing purges these",
     "the lock, shutdown and launch paths are each tested for the AUDIO "
     "cache; no test asserts the opposite for voice/refs - that a reference "
     "clip and its transcript are still on disk after a lock and a relaunch. "
     "Testable the same way test_audio_cache_launch_wipe.py tests the cache, "
     "and it is the more valuable half, because here survival is the "
     "behaviour being promised FOR THOSE THREE PATHS specifically. The row's "
     "other exits - deleting the voice, deleting the folder, or a vault "
     "reset - are a separate claim and the reset half of it is already "
     "proven (see 'delete that voice, delete the folder, or reset the "
     "vault' above), not part of this debt."),
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
    # /vault/reset again - the two halves of it nothing exercises yet.
    ("a file that would not go is named rather than hidden, as "
     '`{"ok": false, "left": [...]}`',
     "the ok:true, left:[] shape is proven (test_every_ground_truth_"
     "artefact_is_destroyed); nothing simulates a file the route cannot "
     "remove - held open, permission-denied - and checks the response comes "
     "back ok:false with that file named in left. Testable by holding a "
     "handle open on one artefact before calling the route, not written."),
    # ── The derived voiceprint ────────────────────────────────────────────
    # Two markers, one gap: the short version and the voice/refs row use the
    # same words, and the bullet in "What is written outside the vault" says
    # it at length. Both are counted rather than one riding on the other,
    # because the row's other markers already cover the clip and transcript
    # and would happily have covered this too.
    ("a voiceprint the app derives from your clip by itself",
     "tts/worker/fish_s2.py's ordinary synthesis path encodes the reference "
     "clip and calls _save_tokens, writing <clip>.prompt_tokens.npy beside "
     "the clip, the first time a voice speaks. Verified by reading that "
     "path; no test drives it, because the encode needs the real codec "
     "(~4.9 GB) and no fake stands in for it yet. Testable by faking the "
     "codec and asserting the .npy lands in voice/refs/<folder>, not "
     "written."),
    ("The app derives a voiceprint from that clip without being asked",
     "the same unwritten test as the entry above; this is the long form of "
     "the sentence, in 'What is written outside the vault'. Its extra claim "
     "- that the engine will speak from the token file with the clip gone - "
     "is visible in _resolve_reference/_load_tokens, which accept tokens "
     "with clip None, and is not driven by any test either."),
    ("its equivalent is held in memory and never written to disk",
     "the XTTS half of that bullet, and the reason it is stated separately: "
     "that engine keeps its latents in a capped in-process dict and only "
     "ever writes them when a prepare_ref request supplies an output path, "
     "which nothing in the shipped app sends. Read out of tts/worker/"
     "xtts_v2.py rather than tested. Testable as a standing invariant over "
     "the worker's write sites, the same shape as the narrow_data_dir gap "
     "above, not written."),
    # ── The log ───────────────────────────────────────────────────────────
    ("The file is written only by the packaged exe",
     "half proven, and the wrong half. test_release_hardening.py::"
     "TestAuditRegressions2026_07_25::test_uvicorn_logs_reach_the_file_the_"
     "error_dialog_points_at forces sys.frozen true, runs "
     "_setup_frozen_logging and reads the file back, so the PACKAGED half is "
     "exercised. Nothing asserts the negative - that a run which is not "
     "frozen creates no file at all - and the negative is what this sentence "
     "actually promises. Testable by calling the same function without "
     "sys.frozen and asserting nothing appears, not written."),
    ("What that gate cannot see, because it reads shapes and not values",
     "every blind spot listed after that phrase is testable the same way the "
     "scanner's positive controls are - feed it a source with a cross-module "
     "helper, an attribute access, a value round-tripped through a list, and "
     "assert it reports nothing - and none of them is. The scanner's own "
     "docstring names them honestly, which is where the sentence comes from; "
     "an honest note is not a test."),
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

    THE ROW'S ANSWER CELL IS ITS OWN PROMISE

    A "Where your data lives" row is two cells with different jobs: the first
    NAMES a file, the second says what is true of it. Emitting only the whole
    row made those one promise, so a marker matching the NAME half marked the
    whole row covered and the answer half never had to be registered at all.
    That is not hypothetical. The `port` row said "a vault reset does not
    touch it either" while /vault/reset shredded it (_reset_runtime_files),
    and the row passed every check here because an ACKNOWLEDGED_UNTESTABLE
    entry keyed on "the port the server last used" - the label - vouched for
    it. The registry positively certified a false sentence, which is the one
    outcome it exists to prevent.

    So the final cell is emitted as a promise in its own right, in addition
    to the whole row. The whole row still appears, so a marker legitimately
    taken from a label (what a premigrate snapshot IS, say) keeps working;
    what stops working is a label registration silently standing in for an
    answer nobody checked. It is a cell, not a sentence, so a cell packing
    several claims can still have one registration cover its neighbours -
    coarse, but strictly finer than the row, and it closes the case that
    actually went wrong.
    """
    lines: list[str] = []
    raw = _document().splitlines()
    i, n = 0, len(raw)
    while i < n:
        line = raw[i].strip()
        if line.startswith("| `"):
            lines.append(re.sub(r"\s+", " ", line))
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) > 1:
                lines.append(re.sub(r"\s+", " ", cells[-1]))
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
DOCUMENT_DIGEST = "b166dc10e89ebea4611742a58b27f1f02a54c9d83b30056b2d4dba3cfac0e152"


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

    def test_a_table_rows_answer_is_its_own_promise(self) -> None:
        # The defect this split exists for. "the port the server last used"
        # is the row's label; the answer beside it is a different claim, and
        # while the two were one promise a registration on the label
        # certified an answer that contradicted _reset_runtime_files. The
        # answer must be reachable on its own, and the label must not reach
        # it.
        answers = [
            line for line in _promise_lines()
            if line.startswith("No, and one number")
        ]
        assert answers, "the port row's answer cell is not extracted"
        assert not any(
            "the port the server last used" in line for line in answers), (
            "the row's label still reaches its answer"
        )

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
