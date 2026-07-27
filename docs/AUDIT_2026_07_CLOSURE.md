# Elysium - second whole-repo audit, closure record

Source: 158 findings across 18 root causes, worked in 13 groups (G1-G13).
Every fix landed with its own regression test.

**Backend 1221 passed / 5 skipped · Frontend 1163 passed / 84 files**
(from 1110 / 1095 at the start of the pass). The real vault (`backend/app.db`)
was checksummed before and after every suite run and never changed.

**Open: 1.** The published tree is not the working tree - see the last section.
It is the only finding that cannot be closed by a code change.

---

## What was actually wrong, by root cause

The 158 findings collapse into a handful of shapes. Naming the shape is worth
more than the count: the same mistake was made independently in a dozen places.

**Detection with no carrier (KÖK 1, 7).** The code noticed the problem, wrote
it down, sometimes with a comment saying "so the endpoint can report it" - and
no consumer ever read the field. Dropped sentences, truncated speech, images
the model never received, a pronunciation dictionary threaded through four
modules with no setting to fill it. The detection was always fine; the wire was
missing.

**One ambiguity, read optimistically (KÖK 18).** "I could not look" was treated
as "there is nothing there", and a destructive step ran on that reading. An
unreadable uploads directory deleted every attachment row AND the backup that
could undo it; a 0-byte `app.db` blocked crash recovery and a fresh empty vault
was built over the user's data. The only irreversible class in the audit.

**Two paths, two meanings for one setting (KÖK 6).** The live reply synthesises
sentence by sentence; the speak button synthesises a stored message. Every
delivery dial was applied at a different granularity on each side, so "density
3" meant three tags per reply on one path and three per sentence on the other.

**Blocking work on the event loop (KÖK 8).** `BEGIN IMMEDIATE` in five async
handlers, a thread join in an SSE `finally`, a lock held across a model load.
None of it was wrong; all of it froze every other live stream while it ran.

**Collections with no ceiling (KÖK 10).** A registry entry per message, decoded
PCM held until something else spoke, and the whole conversation accumulating on
disk as plaintext wav for as long as the vault stayed unlocked.

**The wrong sentence (KÖK 14).** A backend restart was reported as "No audio
output device is available", sending people after a sound-card driver. A working
RTX 5080 was described as "no readable NVIDIA GPU". A too-long passphrase got
"Something went wrong. Please try again.", so the user pasted it again, forever.

**The screen and the source diverging (KÖK 15).** The only `/tts/state` poller
was imported by nothing, so a crashed worker was invisible until somebody
pressed Speak. Two polling predicates never stopped once the endpoint started
failing. Deleting a character left its chats' streams generating and billing.

**Tests that read source text (KÖK 13).** This is the answer to why the rest
shipped green. A test asserting that a call string appears in a file passes with
that call inside `if False:`. A loop asserting `msg.length > 5` passes on the
fallback sentence. The rule now: a source-text test may pin a DELETION, never a
behaviour.

---

## Group by group

| Group | Root causes | What landed |
|-------|-------------|-------------|
| G1 | 12 (verify) | The aggregate runner found its own scripts; `_harness.py` stopped 11 verify scripts pointing at the real vault and running 32 `DELETE FROM` |
| G2 | 18 | Unreadable uploads directory raises instead of looking empty; 0-byte `app.db` no longer blocks adoption; the stub is moved aside, never deleted; `get_db` stopped leaking a handle on a failed PRAGMA |
| G3 | 2 | Best-effort cleanup that said "deleted" now reports what it could not delete: `unrevoked[]`, `audio_left[]`, revoked keys really revoked |
| G4 | 11 | Upload spool/body ceilings derived from one constant; decompression bomb by header; `which_trusted()` refuses a cwd executable |
| G5 | 3, 4 | The `<3 s first sound` mechanism actually fires; a 20 s heartbeat during compile; stream deadlines that keepalives cannot reset |
| G6 | 5 | Seam rules for the text cutters; the WSOLA tail fixed and verified numerically against the real engine |
| G7 | 1 | The `voice_notice` frame; dropped/truncated counts on `voice_done`; `omitted` collected |
| G8 | 8, 16 | Three duplicated SSE bodies became one; a reply that ends early keeps what was read, however it ended; the finalize guard checks the tail like its siblings; write transactions left the event loop |
| G9 | 6, 7 | One tag budget per reply; the standing tone reaches every sentence; the narration mode is finally stored; reading rules and the pause dial shipped; the transcribe button that no engine can answer is gone |
| G10 | 14, 15 | The audio player reports the real code; three failures stopped sharing one; the fit check reports the card it actually read; uid collisions across roots; the `/tts/state` heartbeat mounted |
| G11 | 9, 10 | The codec's own VRAM term; the live registry and the scheduler let go; generated audio expires |
| G12/G13 | 12, 13, 17 | Docs re-synced and made enforceable; source-grep tests replaced with behaviour; the DNS-rebinding shield, `voice_error` and `/speak_stream`'s error contract tested for the first time; undefined CSS token, closed panels in the tab order, an invisible warning tier |

---

## Decisions taken during the pass

- **`MIN_PASSPHRASE_LEN` stays 8.** Raising it would lock out existing users and
  8 is the ordinary floor.
- **The standing tone stays global.** A per-character column was on the table
  and was not built: the tone is a delivery tag, not the reference clip's
  timbre, and one default is what the user actually wants to set.
- **The pronunciation dictionary and the pause dial were finished**, not
  deleted. Both had working, tested machinery and no way in.

---

## The one open finding

`backend/keyring_service.py` has never been committed. Nor has
`backend/tts/` (the whole voice subsystem), `speech_prep.py`, `voice_tags.py`,
`messages_common.py`, or 99 other source files - 172 in total.

Measured, not inferred. `git archive HEAD` into a temp directory:

```
import main   ->  ModuleNotFoundError: No module named 'keyring_service'
pytest        ->  83 tests collected, 1 collection error
                  (this working tree collects 1221)
```

So the committed tree cannot start the app, cannot run its own suite, and does
not contain v1.1's headline feature.

No test run from inside the working tree can catch this class: the suite reads
the working tree, which is the copy that HAS the files.
`backend/tests/test_release_tree.py` is the gate that can - it builds the
published tree and tries to use it - and it currently **skips**, printing the
list of untracked files. Committing them turns it into a real gate with no edit
needed.
