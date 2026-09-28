# Elysium: what protects your data, and what does not

This document exists so you never have to take a claim on trust. Every
protection below says what it does, whether it is on by default, and what it
does **not** cover. The limits are here on purpose: a security page that only
lists wins is a marketing page.

Written against version 1.1.6 and the work sitting on top of it. Some of what
follows is in the source tree and not in any exe you can download yet;
CHANGELOG.md says which.

---

## The short version

- Everything you write lives in **one encrypted file**. Without your
  passphrase it cannot be opened, not even as a plain database. Two other
  files can hold a complete encrypted copy of it, and the table below says
  when each appears and which passphrase opens it, because in both cases it
  may not be your current one. Four things you write live outside it: spoken
  replies, as plain audio wiped at every lock and at three other moments the
  table below lists; the reference clip you record and a transcript of the
  words in it, plus what the `voice/refs` row below adds to that, if you
  cloned a voice at all. Every engine here can clone, so this is about whether
  you recorded a clip rather than which model you picked. And UI preferences,
  kept in the window's local storage; and the wallpaper, which is too big for
  that and lives as a blob in the browser profile's IndexedDB instead, as the
  section below spells out. The log is a fifth, and it is different in kind:
  it holds no message text by rule, but the rule has a measured hole and the
  row below says how big. Two more things can exist and neither should. One is
  a plaintext copy of the whole database, kept after a one-time upgrade until
  you delete it, and the table below lists it. The other is an `uploads`
  folder of plaintext images, which is in no table because nothing puts
  pictures there any more. The one-time migration into the vault empties it
  and removes the folder when it manages to empty it, but it steps over
  anything that is not an ordinary file, and any ordinary file whose name it
  does not recognise unless that name ends in `.tmp`, which it shreds. So a
  symlink, a folder, or a picture somebody dropped in by hand keeps both
  alive. If the folder is itself a shortcut the migration abandons its own
  pass and touches nothing there, but the step after it still runs, and that
  step deletes the record of any picture whose file it cannot see. The
  encrypted snapshot taken at the start of that unlock is thrown away in the
  same pass, because a pass that gave up reports nothing failed, so the one
  thing that could undo those deletions goes with them. The reset route
  destroys all of it, with the exceptions that section lists. There is also no
  way to recover a forgotten passphrase from inside the vault. The lock
  screen's own "Forgot your passphrase?" flow is a way out of one, and it is
  documented below under Resetting the vault.
- Your passphrase is **never stored**. Lose it and the data is gone. That is
  the design, not a bug.
- The app talks to **one address on the internet** (OpenRouter), and refuses
  every other destination before the request goes out - including the right
  host reached over plain `http`, which would put the API key on the wire in
  the clear. What it reads is the address being ASKED FOR rather than the
  machine a packet reaches, so a proxy you configure is not measured by it.
  The permitted list is pinned to the shipped address; pointing the app
  somewhere else takes a second, deliberately named switch
  (`ELYSIUM_ALLOW_BASE_URL_OVERRIDE=1`), and without it a changed address is
  refused rather than followed. One exception: the optional voice engine
  installer, which you start by hand and which reaches the package and release
  hosts named under "Where it connects".
- **Your own machine is not an exception either.** `127.0.0.1`, `localhost`
  and `::1` used to be permitted destinations unconditionally, on the
  reasoning that the app is itself a local server. It was measured on 20
  August 2026 and the reasoning was wrong twice: a proxy you configure on
  loopback never reaches that check, and the launcher's own health probe does
  not go through it. What the entries did do was make any program that can
  open a listening socket on this machine a permitted destination for a
  request carrying your API key. They now need the same
  `ELYSIUM_ALLOW_BASE_URL_OVERRIDE=1` switch as any other address, because it
  is the same decision.
- Elysium writes **nothing to the Windows registry**. Deleting one folder
  removes everything it ever created, with six caveats listed at the end, one
  of which depends on how you started it.

---

## Where your data lives

Everything is under one folder:

```
%LOCALAPPDATA%\Elysium
```

| What | Encrypted? |
|---|---|
| `app.db` - messages, characters, personas, images, your notebook and limits, what the note reader has read and spent, your API key, proxy URL | **Yes**, AES-256 (SQLCipher), including the journal files |
| `salt.bin`, `verifier.bin`, `kdf.json`, `vault.recovery` - passphrase machinery | No, and they are not secret. Knowing them does not reveal your key. `vault.recovery` is a second copy of the salt and its cost settings, and nothing else: deleting or corrupting `salt.bin` alone used to destroy the vault permanently even with the right passphrase, and this is what survives that. It deliberately does NOT hold the verifier, so this one file is not on its own the complete offline attack kit. What keeps a passphrase change revoking the old key is a separate check: after a change, the app reads the mirror back, and a mirror it could not replace is reported to you by name rather than left describing the salt that was just revoked |
| `app.db.plain.bak-*` - a pre-vault copy kept after a one-time upgrade | **No, plaintext.** The Security tab lists it and deletes it on request |
| `app.db.premigrate.bak` - a COMPLETE encrypted copy of the vault, taken before an uploads migration touches anything and kept when that migration reports a file it could not move, or fails outright. Read that narrowly: a pass that gives up before it starts, which is what happens when the uploads folder is a redirected name, reports nothing failed, so the snapshot is discarded on that same pass | **Yes**, same cipher, and a passphrase change re-keys it so the old one stops opening it. A re-key that fails is reported to you by name rather than passed over, and until you act on it that copy still opens with the old passphrase. The Security tab now lists it and a button removes it; a later clean migration still discards it on its own. Removing it from that screen needs the vault unlocked, because only your key can tell a healthy copy from a stranger's; the reset route takes it from the locked state along with everything else |
| `app.db.premigrate.bak.unreadable-<ts>` - that same snapshot, moved aside because it did not open with this vault's key | Encrypted, under a passphrase this vault does not hold. It is moved rather than deleted because it may be the only copy of an older vault, and nothing short of a vault reset removes it for you |
| `voice/models` - voice model weights you downloaded | No. Files you chose and put there; they hold none of your conversation |
| `voice/refs` - only for a voice model that CLONES, the reference clip you record and a transcript of the words in it, the label you gave that voice, and, for one engine, a voiceprint the app derives from your clip by itself. Every engine here clones, so the condition is about whether you recorded a clip | **No, plaintext.** Nothing purges these: not the lock, not shutdown, not the next launch. They go when you delete that voice, delete the folder, or reset the vault. The FOLDER is named with a one-way hash, so a directory listing is no longer a roster of the voices you have cloned; the file inside it still names the voice |
| `voice/cache` - generated speech, your conversation as audio | No. Cleared at every lock, every unlock, every launch and every shutdown, unless somebody has pointed that folder somewhere else with a shortcut, in which case every one of those refuses to touch it and none of them takes the shortcut away; anything older than 30 minutes is cleared as well, on a check that runs whenever the window asks the voice engine how it is, which is about every ten seconds while a chat is open whether or not you ever loaded a voice, and on a half-minute timer once one is loaded |
| `webview/` - the app window's browser profile | No. Cache, history and session names are wiped at every launch and exit, twenty-two of them, eleven folders and eleven files; two of the twenty-two live under `Crashpad` rather than under a profile. Everything not on that list stays: your cosmetic settings and your wallpaper are kept, and so are browser files this app never asks for, saved form data among them. The crash reporter's folder is on that list too, by the two names inside it that hold dumps |
| `elysium.log` - application log | No. It is written to a rule that carries no message text, no note text, no keys and no passphrases, and the measured holes in that rule are named under "The log" rather than left out. It does record that things happened: chat and note ids, counts, and errors. Some of those name the exception's TYPE and nothing else; in the fifty traceback places counted further down this document it is the exception's own message, which nothing stops from being built out of your text. That is the gap, it is measured, and it is described at length under "The log". That leaves a plaintext record of which chats have notes sitting beside an otherwise opaque vault. A vault reset now shreds it, along with its rotated `elysium.log.1`, for that reason |
| `port` - the port the server last used | No, and one number with nothing in it to protect. A vault reset shreds it anyway, alongside the log |

In a development checkout this folder is the `backend` folder inside the
checkout instead, which is why you may see these files beside the code.

---

## What protects it

### Your passphrase

Turned into a key with scrypt, a deliberately slow and memory-hungry function
(128 MB per attempt). A vault made before that figure was raised runs at 32
MB, mebibytes in both cases, until the next unlock that manages to re-key it,
or until you change the passphrase; those are the moments the passphrase
exists in memory and the key can be derived again at the higher cost. The
upgrade is best effort, so a vault that fails it stays at 32 MB and tries
again next time. That cost is what makes guessing expensive. A wrong
passphrase costs two seconds here, but that is a courtesy rather than a
defence: somebody who copies your folder guesses offline, as fast as their
hardware allows, forever, and never touches this screen. So the passphrase
floor is **12 characters**, checked when a passphrase is SET and never when
one is used, so a vault made under an older, shorter floor keeps whatever it
has until you change it. Five shape checks go with the length: no keyboard or
alphabet run, and that one fires only when the run is the whole phrase or
fills it end to end by repeating; no one idea repeated; no single character
filling more than half of it; at least five different characters; and not one
of the phrases a guessing run starts with.

There are deliberately **no composition rules**. Forcing an uppercase, a digit
and a symbol is how you get `Password1!`. Three unrelated words beat it.

### The database

The whole file is encrypted, not just the fields. Opening it with an ordinary
SQLite tool fails; it does not show you a table with scrambled cells, it does
not open at all.

### Prompt modules

A prompt module file you import is read in memory and never written to disk
as a file. Its text, the module names, the file name and the raw entries kept
from a SillyTavern preset are stored only in the encrypted database. Before
anything is stored, fields that can hold a secret (a proxy password, custom
headers, a reverse proxy or custom address, and any key named like a
password, token or API key) are removed from every stored copy. The import
report lists which fields were removed, never their values. Prompt text itself
is not searched for keys: a key pasted into a module's text is stored as
typed, and sent as typed: only your own key is cut out on the way.

### Idle auto-lock

**On by default, 5 minutes.** Locking overwrites the key buffer it holds,
tears down the voice engine (giving the GPU memory back), drops the network
client, and stands the notebook's background reader down. It also wipes the
generated speech on disk - a separate act from unloading the engine, which
deliberately leaves those files alone - and turns the screen-capture exclusion
back off. A file it could not remove is named either way: the answer to a Lock
you pressed carries the whole list, and the log names the first five however
the lock was triggered. Read "The key in memory", below, for how far the
overwrite goes and how far it does not. Idle means nothing in flight and
nothing finished recently, so a reply that is still streaming holds the vault
open however long it takes. Change the delay or turn it off in the Security
tab.

One thing deliberately does **not** hold it open: the notebook's background
reader. It runs while you are reading something else, so counting it as
activity would mean the vault never locked on a busy chat. Instead the lock
cancels its planning loop and empties its queue. Work still queued and never
sent costs nothing, so a later run reads it exactly as if the lock had not
happened. What restarts it is the next message in that chat, or the sweep you
press yourself, so a conversation you walked away from and did not go back to
is not read again on its own.

One thing the lock does **not** cancel: an extraction whose reply has already
ARRIVED. Once a reply is in hand it has been paid for, so the lock waits up to
five seconds for it to be parsed and written, and only then wipes the key. So
a note can be written into the vault in the seconds **after** you press Lock,
and the alternative is to pay for an answer and throw it away. Read the wait
precisely, because it is wider than "a reply already in hand": what it waits
on is the whole in-flight step, so on the path that sends and parses in one
task the five seconds can cover a request that is still on the wire. It is a
bounded wait either way.

A call already on the wire when the lock lands is the other case, and it is a
promise problem before it is a billing one. The lock is supposed to mean the
conversation is closed, and for this one path it does not: the request carries
decrypted chat text, it has already left the machine, and nothing the lock
does can call it back. Locking narrows the window; it does not close it.

The billing half is smaller but real. That call is billed the moment it
leaves, whatever happens to the app afterward. It is now marked as a failed
call and never retried: the range is recorded as spent, which is what stops it
being planned and sent a second time, and the price of that fix is that those
particular messages are never read into a note at all.

That covers the call in flight when the app stopped, and only that one. A
reply that **arrives** and then fails to be parsed, or fails to be written to
disk, does not move the notebook's cursor, so the same range is planned again
on the next threshold and paid for again. The circuit breaker caps how far
that can run - five consecutive failures put it to sleep and twenty failures
since the last reset stop it, which are two different counters, and the daily
ceiling holds above both. For the failures this paragraph is about, both
counters are kept per chat, so what stops is usually that conversation rather
than the feature, and it is not meant to be permanent: a stopped chat is
dropped a day after its last failure, though the sweep works from the least
recently touched end and stops at the first record still inside the day, and
every lookup moves a chat to the other end, so with several chats in play a
stopped one can sit longer. Enough chats failing at once is the exception:
past eight of them the failure counts against the global breaker as well, and
twenty of those do stop the feature. None of that makes the app treat a paid
answer it could not use as spent. That is a deliberate trade; the alternative
is to skip those messages forever.

Editing or deleting a message rolls the notebook's cursor back on purpose, so
the rewritten stretch gets read again later. A reply already in flight before
the edit used to land anyway and get written from wording you had just taken
back. The reply is now checked against the trace its own call left before it
was sent; if the edit got there first and erased that trace, the reply is
discarded instead of written.

### The key in memory

While unlocked, the key is in RAM. On lock, the buffer is overwritten rather
than merely dropped. **This is not "the key is wiped from memory"** and the
code says so: copies exist that Python cannot reach, including anything
Windows paged to disk. Overwriting one buffer is worth doing; claiming more
would be a lie.

### The line at the top of every chat request

The system message of every chat request starts with one line of hex that
carries no instruction and not the chat's number. It is a keyed hash of the chat id
under a random key the app makes in memory, so it stays the same for a chat
while the app runs and differs between chats. That key is never written to
disk and is dropped when the vault locks; after a lock or a restart the line
is a different one.

### What a chat request refuses, and what it leaves out

Models that search the web on their own are refused before anything is sent:
any `:online` variant, `openrouter/auto`, any `@preset/...` id, and model ids
naming `sonar`, `-search` or `deep-research`. The check sits in the one sender
every request passes through, so it covers the chat routes and the notebook's
background reader alike.

A message that contains your stored API key is refused before it is stored or
sent, and the key is cut out of every outgoing request body wherever else it
appears (history, card, modules, notes).

`reasoning` is sent only when the cached model list says the model takes it:
to switch reasoning off, or, for a model that requires it, at its lowest
effort with the reasoning text excluded. Temperature, top_p, top_k and
repetition_penalty are not sent unless you set them.

### One window per vault

Locking is per process. Two Elysium windows against the same data folder each
kept their own copy of the vault key in their own memory, so locking one - by
hand or on the idle timer - cleared that copy and left the other window
sitting on a fully decryptable database. The one gesture the whole at-rest
design rests on did half of what it appeared to do.

So a second launch is now refused rather than unsupported. It raises the
window that is already open and exits before it opens the vault, reads the
database or starts a server. It is not quite silent: on a packaged build the
log is already open by then, so the refusal itself is written into
`elysium.log` in that folder. The claim is a Windows kernel object named after
the folder rather than a lock file, so a crash or an End Task releases it with
nothing left to clean up and nothing to guess about. It fails OPEN rather than
shut: if the claim cannot be made at all the launch continues, and `uvicorn
main:app` never makes one, so a hand-started server can sit on the same folder
as a running window. Two different data folders (`ELYSIUM_DATA_DIR`) still get
a window each, because they share no key and no database.

### Deleting things

Anything sensitive is overwritten with random bytes before being unlinked, by
a single shared routine that also refuses to delete through a junction, a
symlink or a hardlink. One deliberate exception to that refusal is described
under "The app window", below. **It is not a guarantee against physical
recovery**: on an SSD, wear levelling can leave the original blocks readable
to firmware-level analysis. Full-disk encryption (BitLocker) is the only
answer to that, and it is yours to enable.

### The local server

Elysium runs a small web server on `127.0.0.1` so the window can talk to it.
Loopback is not a permission boundary: any program running as you could
otherwise read every conversation with one command. So the server requires a
secret generated at launch and given only to the app window.

**The launcher is what arms it.** Whatever starts the app issues a token
before the server comes up, so the packaged exe and `python run_app.py` from a
checkout are both armed. Running `uvicorn main:app` by hand is the unarmed
case: no launcher ran, so no token exists and the gate lets everything
through. One seam in that: the token can also come from
`ELYSIUM_LAUNCH_TOKEN`, so a hand-started server does have a gate if you set
that variable yourself. One route does not follow that rule: `/vault/reset`
refuses outright when the gate is unarmed, rather than being left open. See
"Resetting the vault," below.

Two routes used to be exempt because a browser cannot attach a header to an
image or an audio element. The exemption asked for `Sec-Fetch-Site:
same-origin` instead. A page cannot forge that header - the browser sets it -
so a hostile page was refused, but a local program sets it with one flag and
walked in, and upload ids are sequential, so every stored picture could be
read by counting upward. That is closed. The app fetches both routes itself,
with the token, and hands the bytes to the page: a `blob:` URL for a picture
and for a voice preview, and decoded audio straight into the player for a
spoken reply. No API route is exempt now. The gate is a prefix rule rather
than a list: it covers everything under the API path and nothing else. Three
things sit outside it today: the health probe the launcher polls before the
window exists, which answers `{"ok": true}` and nothing else; the
machine-readable description of the API; and the window's own files. None of
the three reads anything you wrote. One method is exempt on every route,
`OPTIONS`, which a browser sends to ask what it is allowed to do and which
carries and returns nothing.

### Resetting the vault

The lock screen has a "Forgot your passphrase?" flow behind `POST
/vault/reset`. There is no way to recover a forgotten passphrase - see above -
so this is the honest answer to being locked out for good: it destroys the
vault and lets you start over, rather than leaving you with a database nothing
can open.

It only runs from the LOCKED state. If the vault is unlocked it refuses
outright with HTTP 409 before the confirmation phrase is compared, because
whoever can already unlock the vault does not need this door, and answering it
anyway would turn it into a way to destroy a conversation somebody is reading
right now. Two checks sit above that one, and the order is worth stating: the
request body is shape-checked first, so an empty phrase is answered 422 before
anything else looks at it; then a build with no reset door answers 404; then
this 409. From the locked state it still requires a typed confirmation phrase,
checked against a value only the backend decides, so a frontend bug cannot
fire it with an empty or a near-miss string - but that phrase only guards
against an accident, never against someone reaching over your shoulder.

What it destroys: the database and every backup family beside it (plaintext,
orphaned, rotation, and both premigrate names, including the one moved aside
as unreadable), the passphrase identity files including the salt mirror and
every shelved copy of them, the empty stub a recovery can leave, the uploads
folder, saved voice references and cached speech, the desktop app's browser
profile, and any leftover OS-keyring entry from the legacy migration. The
database's own `-wal`, `-shm` and `-journal` sidecars go with it, as do the
half-written `.partial` premigrate name and the `.new` files a passphrase
rotation stages. The BACKUPS are matched by name, and four of those patterns
cannot reach a journal file left beside the backup itself: the rotation
copies, the canonical premigrate name, its `.partial` twin, and the empty
stub. The `.partial` is the one to think about, because it exists only while a
copy is being written, which is exactly when a crash would leave a `-wal` next
to it. Such a file survives the reset and is still reported as a clean run. It
also shreds `elysium.log`, its rotated `elysium.log.1` and `port` - all listed
above. The log names chat and note ids, so leaving it behind meant a wiped
vault left a plaintext record of which chats had held notes. It does not touch
a downloaded voice engine, which is software you chose rather than data you
wrote.

A file held open elsewhere can survive the wipe. The route answers HTTP 200
either way: a clean run reports `{"ok": true, "left": []}`, and a file that
would not go is named rather than hidden, as `{"ok": false, "left": [...]}`
under that same 200 instead of an error status. One thing escapes that
accounting: `ELYSIUM_SKIP_LEGACY_MIGRATION=1` makes the old keyring entry
report itself deleted without the delete being attempted, and the run then
looks clean. Everything that IS removed goes through the same
overwrite-then-delete this app uses everywhere else - see "Deleting things,"
above, for what that defeats and what it does not.

One family is held back on purpose. If the database itself would not go,
because something has it open, the passphrase files are left exactly as they
were. The route's own answer says which files would not go; the log does not,
because by then the reset has already shredded it. A half-finished reset that
took the salt and left the database would turn a vault you can still unlock
into a file nothing can open, which is the outcome this whole route exists to
spare you.

**This route does not exist outside the installed app.** It answers 404 unless
two things are true at once: this is the packaged build, and the launch token
gate is armed. A checkout is not the packaged build, so `uvicorn main:app` and
`python run_app.py` from source have no reset door at all - the second of
those is armed, as the section above says, and being armed is not on its own
enough. Without that pair, a bare local request carrying the confirmation
phrase wiped the vault with no passphrase and nothing else in the way.

Either condition alone would be too weak, which is why both are required.
`sys.frozen` describes the process, not whoever is calling it, and the
launcher issues a token on the development path too.

What it does NOT do, stated here rather than left to inference: it does not
distinguish the app window from another program on this machine holding the
same launch token. In a packaged build, anyone able to present the current
token can do this. That is the same "any code running as this user" boundary
the rest of the unlocked vault already accepts, extended to the locked one,
not a new one: someone with that access could already delete every one of
these files by hand. The cross-origin write shield that protects most other
mutating routes from a hostile web page does not close this gap either: a bare
local process sends no `Origin` and no `Sec-Fetch-Site` header, and the
shield's own fallback treats an absent header as non-browser tooling and lets
it through.

### The safe word

One phrase, set in the Notes tab, that this app enforces in code rather than
asking a model to respect. It lives inside the vault like everything else, and
setting it or reading it needs the vault open.

It acts in two places. A message you type that contains it never becomes a
turn: the request is refused with `400 safeword_triggered` before the key
check, before the proxy gate and before anything is assembled, so nothing is
sent, nothing is stored and nothing is billed. Be precise about the key: it is
decrypted earlier, in the batch of reads this route makes off the database
before it decides anything, and then never used. The refusal also covers the
stored message a regenerate re-sends, so arming a word can refuse regenerates
on a chat that already holds it.

The second place is what makes that a whole promise rather than half of one.
The same word anywhere ELSE in an outgoing body - older history, a note the
reader wrote, a persona, a character card, a stop sequence - used to travel
with the request, because only the newest message was ever looked at. The
whole body is now walked before it leaves and the word is cut out of the
string values in it, apart from the ones named below as left alone, and never
from the names of the fields, which are structure rather than prose, at the
last gate before the provider call, on the streaming path and the plain one
alike. A message the cut empties is dropped rather than sent blank, and so is
an emptied stop sequence. Both senders pass it: your reply and the notebook's
note reader. A locked vault does not fail open here - a word that cannot be
read is not the same as no word, so the request stops instead of going out
unfiltered.

Five limits, worth knowing before you rely on it.

Part of the body is read and deliberately left alone: the model id, the
provider policy block, the response schema, the modality list, whether the
reply streams, and a picture you attached, both its address and its bytes.
Read the last one precisely: what is skipped is a value that IS a data URI
from end to end, wherever in the body it sits. One quoted inside a sentence is
prose, and is cut like prose. A three-letter word turns up by chance about
fifty times inside the base64 of a one-megabyte image, so cutting there would
not degrade the picture, it would destroy it, and say nothing about having
done so. When the word turns up in one of the five protocol fields, that
field's NAME goes to the log and never its value. A match inside a picture is
not logged at all.

It removes a string, not an idea. Cutting the word out of a sentence leaves
the sentence, and the model still reads that.

Nothing inside the vault is rewritten. This governs what leaves; a word
already sitting in an old note stays in that note, in the stored message and
on your screen.

It matches the letters you stored and not the sound of them, and this is the
limit most likely to bite. The comparison normalises the text, swaps the two
Turkish I forms, lowercases, and only then strips the combining dot that
lowercasing a dotted capital leaves behind. It does nothing beyond that, and
lowercasing is not the same as full case folding, so a German sharp s does not
match `ss`. That I is handled by looking for both spellings, so a word you
saved with a capital `I` in it is found either way, though a capital `İ`
collapses to one spelling and is not; but a word saved in lower case with `ı`
in it is only ever looked for with the `ı`. The other Turkish letters
lower-case like any other, and that is all that happens to them: `ş` is never
treated as `s`, nor `ğ` as `g`, `ü` as `u`, `ö` as `o` or `ç` as `c`. A safe
word carrying any of those will not be found in a message typed in plain
ASCII. Spacing is exact on both sides, and only the phrase you SAVE is tidied:
it is collapsed to single spaces on the way in, and nothing collapses the
message being checked, so a double space or a line break inside a two-word
phrase is enough to miss. A single ASCII word is the shape that always lands.

The phrase is readable by the app window over the local API, on a route of its
own rather than with the other settings, so it sits behind the launch token
and behind the vault but it is not a secret from a program holding both. It is
never written to the log.

### Where it connects

One provider host, enforced by refusing any other destination before the
connection opens. System proxy environment variables are ignored on purpose.
Every completion request forces `zdr: true`, `data_collection: deny`,
`allow_fallbacks: true`, and the app window **cannot** override those three.
The first two are filters: OpenRouter routes only to endpoints with a zero
data retention policy, and only to providers that do not collect data. The
third relaxes neither. When the provider OpenRouter picked is unavailable,
it may use a backup from that filtered list, for the same model.
Model fallback is a separate `models` parameter, and no request carries a
`models` list, so failover does not swap the model.
When a reply fails before any of it has reached you, and the failure is the
passing kind (busy, down, rate limited with a short wait, timed out, empty),
the app sends the identical request once more: same body, same model, same
policy. Never a third time, and never once any of the reply has arrived.
The other requests below are GETs with no body, so they carry no policy block
and have nothing to force.

**Two things send your conversation there, not one.** The reply you asked for,
and the notebook's note reader - a second model, which you choose, sent
excerpts of the same conversation automatically every twenty turns while you
are not watching. Read "turns" as stored messages rather than exchanges: one
exchange writes two of them, so in practice this is about every ten times you
press send. Both go to the same single host under the same locked policy. The
reader is off entirely until you pick a model, it is its own sender, and it
spends your credits: what it did and what it cost is in the Notes tab. The
ceiling is sixty calls a day, a block rather than a warning, overridable from
the environment (`ELYSIUM_NOTEBOOK_DAILY_CALLS`) and from no screen in the
app. What an interruption costs is under Idle auto-lock, above.

Twenty turns is the healthy figure. Consecutive failures halve it, up to three
times, down to a floor of two, and one success puts it back; this is the same
breaker described under Idle auto-lock, seen from the other side. What that
actually changes is how much goes out per call rather than how often a call
happens: the trigger halves alongside the batch and was measured to make no
difference, because an ordinary failure, one that was not recorded as spent,
leaves the cursor where it was, so the backlog waiting at the gate is already
past the unhalved figure every time the halved one is consulted. Under
pressure the reader sends less each time. It does not send less overall:
against a provider failing half the time the halving costs about 1.9 times the
calls for smaller reads, and at four failures to one landing about 1.3 times,
which is the case the code sums up as paying more and doing less. Both figures
were measured once by hand and neither is pinned by a test; what a test does
hold is a ceiling, that the halving may not cost more than twice the calls.

Four other paths on the same host carry none of your conversation, reached
from five places in the code, counting the lines that build a request, of
which two ask for the same path. Counting calling routes instead would give a
larger and less useful number, because the proxy check is reached from most of
them. There is no tidy way to group them, so here they are one at a time.

The KEY VALIDATION. The Security tab's key check asks OpenRouter whether the
key you already stored is still accepted, and a provider it cannot reach is
reported as exactly that rather than as a bad key. Saving a key sends the same
question about the key you have just TYPED, before it is stored at all, so a
key that fails is never written down.

The MODEL LIST, which is two of the four paths rather than one: it is asked
with your key, and asked again without it when the first attempt failed for
any reason other than the key being rejected or the answer arriving in a
recognisable but wrong shape. Either of those is reported to you rather than
quietly retried anonymously; a reply that is JSON but not an object, or not
JSON at all, is not caught by that and does fall through to the anonymous
retry, which sends no key.

The PROXY CHECK, which is the fifth place in the code rather than a fifth
path, because what it asks for is that same anonymous model list, timed.
**Opening the Security tab with a proxy URL stored sends it by itself.** The
panel asks for the proxy's health as it appears, whether or not you have armed
"require a proxy", so that switch does not cover this one, and no chat is
involved.

The ZERO-RETENTION ENDPOINT LIST, which the notebook uses so it can offer you
only models that keep nothing. The chat reads the same list to send only the
settings a zero-retention provider for your model accepts, and to refuse, and
mark in the model list, a model that has no such provider. It goes out
unauthenticated when there is no key to send.

So what carries your key depends on what you have stored, which is why this
counts paths rather than promising anything about the key. The key check is
the only one that never goes out on its own; the model list and the proxy
check have refresh buttons as well. The model list, both ways round, goes out
when the chat screen loads, and the zero-retention list goes out with it,
before a send when the copy held is older than five minutes, and when the
notebook needs it. A locked vault is a different matter: while it is locked
everything under the API path answers 423 except the vault's own routes and
the `OPTIONS` method, so none of the four is reachable at all. The three
things outside that path stay reachable, and "The local server" above says why
none of them matters.

The one other egress is the optional voice engine setup, which you start
yourself. It downloads from GitHub and PyPI, uploads nothing, and no chat,
persona or voice data is in scope. `download.pytorch.org` is the third host,
for the CUDA builds. Three footnotes, so the list can be checked rather than
taken. Those are the names you would recognise, not the hosts a packet capture
would show: PyPI serves its metadata and `files.pythonhosted.org` serves the
actual wheels, and a GitHub archive URL redirects to `codeload.github.com`.
GitHub is a source host here as well as a release one, because one engine's
source tree is fetched as an archive there by the installer's own client, and
the package manager can also fetch a Python build from a GitHub release. And PyPI is the package manager's own default
index rather than an address written down anywhere in this repository.

That setup reaches the network two ways. It fetches a package manager from
GitHub itself, then runs it, and the package manager fetches the packages. The
two halves used to disagree under a required proxy: the app's own download
refused to go without one, and the multi-gigabyte half went out bare. Both now
ask the same gate before anything opens a connection. With that switch armed,
a proxy that cannot be produced stops the install with `tts_proxy_unavailable`
rather than starting anyway. A vault that locked while you were away stops it
whichever way the switch is set, because a setting that cannot be read counts
as required. With the switch off and no proxy stored, which is how it ships,
both downloads go out directly. Asking for an install that cannot be proxied
is refused up front, with HTTP 409. The two later checks happen after the job
is already running and fail the job with the same code rather than an HTTP
status, which is the only difference between them. The switch itself is
described in README under `proxy_required`.

The installer's own download is built in the same place as every other client
here, with the same kind of host check in front of it, rather than through the
hand-rolled `urllib` opener it used before. It is still its own client:
synchronous, and handed the proxy the gate already decided on instead of
reading that setting a second time and risking a different answer. Its list is
its own - `github.com`, the `release-assets.githubusercontent.com` and
`objects.githubusercontent.com` addresses a release download lands on, and
`codeload.github.com`, where a source archive URL lands, `https` only - and it
is deliberately kept apart
from the provider list rather than added to it, because the client that
carries your API key must not be able to reach a release CDN. What the package
manager then does is a child process: it is bounded by the index and proxy
variables it is handed, not by a host list.

Once installed, the voice engine runs as a separate process that talks to the
app over pipes and is given no reason to reach the network: proxy, hub and
telemetry variables are stripped from its environment, along with the launch
token itself, which is what stops the engine turning round and asking the
local API for your conversation, and `HF_HUB_OFFLINE=1` is set, so the model
loader reads from disk instead of calling out. Be precise about what that is,
though. It is a configured boundary, not an enforced one. The engine is an
ordinary child process, so nothing at the operating-system level stops it from
opening a socket, and the app's own single-host check does not extend into it.
If you want a hard guarantee for this one component, add a firewall rule for
it; the app cannot make that promise on your behalf, so it does not.

Pictures a model returns are accepted only when they arrive inline. A link to
a remote image is refused rather than fetched, because fetching it would add a
second place your data goes.

### The app window

Browser caches are the reason this section exists: the window used to write
whole conversations to disk as plain readable JSON, surviving every lock.
Those caches are now wiped at launch and exit, API responses are marked
no-store, and the browser's crash reporter is prevented from starting at all
so a crash cannot write a memory dump and send it to Microsoft. That block is
a file Elysium puts where Crashpad needs to write, inside the browser profile
root and beside the per-profile folders rather than inside one, and the
launcher puts it back at every launch, from a checkout exactly as from the
exe. Only `uvicorn main:app` skips it, because that path runs the wipe alone.
A shortcut used to defeat it for good: nothing was written through the link,
which was the point, but the link stayed, so the block could never be
re-established and crash reporting stayed on until somebody removed it by
hand. The block now removes a shortcut on its own name, and on the folder
above it, before it writes. It can still fail to go in, and when it does the
launch says so and carries on, which means crash reporting is live for that
session. The same trick works against the wipe below, and what a shortcut can
leave behind while it is there is two paragraphs after that.

A shortcut used to switch that wipe off. Any program running as you can point
one of the profile's folder names somewhere else with `mklink /J`, no
administrator rights needed, and the wipe refused to delete through a
redirected name. The refusal is right - deleting through one is how you
destroy a folder that was never yours - but it was permanent, so a single
junction turned the wipe into a no-op for the life of the install.

The rule now has two halves. The wipe still refuses to shred a file merely
because a redirected name led it there: nothing at the far end is destroyed
for having been walked into. What is removed is the shortcut's own entry,
which takes away the redirection and leaves the folder behind it standing. Be
exact about which names that covers, because it is narrower than it sounds:
the wipe walks fixed lists of the names this browser profile uses, twenty-two
of them, plus the profile folders themselves, the `EBWebView` folder above
them, and `Crashpad`, whose position is described above. A shortcut is only
noticed on a name those lists already visit, and not on all of those: eleven
of the twenty-two are names the wipe expects to be FILES, and a folder
shortcut wearing one of those names is skipped rather than removed. It is
skipped in both directions, so nothing behind it is touched either, but it
stays. Depth inside those names does not matter, but a shortcut planted on a
name nothing sweeps is never looked at - it is also never followed, so it
hides a folder rather than opening one. A shortcut that is removed goes into
the log by full path, with the folder it pointed at beside it whenever that
can be read; one that resists removal is logged too, by its own path, as
stuck. That covers a shortcut the wipe removes or fails to remove. A folder
shortcut wearing a name the wipe expects to be a file is the silent case: it
is stepped over without a line, without being counted, and without anything at
the far end being touched.

One kind of file on the far side is deleted, and it is the only one: a crash
dump this app's own browser wrote there. Finding it means walking that folder,
which happens past a shortcut on the profile root, on the crash reporter's own
folder, or on either of the two names inside it, and nowhere else, and the
walk is bounded three ways - four folders deep, four thousand files and
folders counted together, two seconds - and never follows a second shortcut it
meets on the way. The file then has to carry three marks at once: a Crashpad
report name, the `MDMP` header bytes, and a timestamp no older than the
shortcut or the profile, whichever is younger. A file missing one mark is left
where it is. The worst case somebody can arrange by forging all three is the
deletion of a file they wrote themselves.

Nine handlers catch a failure inside this cleanup. Two write the exception's
CLASS name; the other seven write nothing at all, and a tenth where the server
starts rather than where the launcher runs writes a line that names neither
the exception's class nor its message. Two of the silent ones matter to the
sentences above: a folder whose name cannot be read is dropped from the log
line rather than reported, and a profile list that cannot be read comes back
empty, so a purge that swept nothing looks like a purge that found nothing.
Elsewhere in the app a failure can write the exception's own message, which is
the gap described under "The log" below; here the tree being cleaned is one an
attacker can shape, so that message could be a path they chose.

### The accessibility tree

The window's browser builds a second representation of the page for assistive
technology, and that representation is your conversation as TEXT, offered to
any program running as you through UI Automation and MSAA. It is not a still
picture either: a reply in progress sits in a live region, so a program
watching the tree gets the words as they arrive rather than at the end. An
unprivileged probe - no token, no elevation, the same user account - was
pointed at a real window and read the whole transcript out of it: chat title,
character name, message bodies, verbatim.

**Hiding the window from screen capture does not close this**, and if you
assumed it did, that is the assumption worth correcting first. That flag
excludes PIXELS. This is not pixels, and the probe recovered the same strings
with the flag confirmed set. MSAA and UI Automation are not two doors either:
the browser builds one tree and serves both from it, so shutting one API
surface would have shut neither.

**A switch closes it, and it is ON unless you turn it off.** It is the only
protection here whose cost falls on software you may be relying on. Other
things are on unasked - the idle lock, the folder permissions, the crash-dump
flags - and none of them takes a capability away from you. This one does. The
reason it is on anyway is the shape of the trade: what it costs falls on
software most people do not run, and what it prevents is any program on this
machine reading your conversation out of an interface Windows documents for
screen readers, with nothing to unlock and nobody to ask. It does not prevent
the same program reading it out of the window's memory, which is the ceiling
described further down and is not one this app can move.

Four things worth knowing before you rely on it. The first three are limits of
the switch; the fourth is a neighbouring protection that belongs here because
it is set in the same place:

- **It takes effect at startup only, and only for this app's own window.** It
  is a command-line argument to the browser process, read once when the
  browser environment is created, so it says nothing about a browser you point
  at the dev server yourself. Changing it while Elysium is running does
  nothing at all - not on the next unlock, not on the next chat.
- **Its setting cannot live in the vault**, and that is not an oversight. The
  decision has to be made before a passphrase exists, and the way out of it
  has to work for somebody who cannot read the screen.
- **While it is on, a screen reader cannot read Elysium either.** That is the
  whole cost and it is a real one. This is not a setting with a free side.
- **Elysium replaces the WebView2 environment variables rather than adding to
  them**, and it does that whether the switch is on or off. It is here rather
  than in its own section because this is where those variables are set. Those
  variables are writable by any program running as you with one `setx` and no
  administrator rights, and what they carry goes to the browser engine drawing
  your conversation: a debugging port, a redirected profile folder, or the
  browser binary itself. Elysium used to append its own switch and pass the
  rest along; it now discards all nine of them and names in the log every one
  that had something in it, an empty one going without a line, naming the
  variable and not its value, with two edges worth stating. For the variable
  that carries browser arguments the switch NAMES inside it are logged, so a
  planted `--remote-debugging-port` is not reported as an anonymous string,
  and anything after the `=` is dropped; a value that is only whitespace
  yields no names and so no line at all. There are two further limits. Five of
  those nine can also come from a Windows policy key, which Elysium reports
  and will not edit, because that key belongs to every WebView2 application on
  the machine and not to this one - the same reason given under "Does Elysium
  change my Windows settings?", below. And this is the environment door only:
  it does not change the fact below about the renderer's memory.

To turn it off, from a command prompt, and then start Elysium again:

```
setx ELYSIUM_ACCESSIBILITY_PRIVACY 0
```

An environment variable rather than a checkbox in the app, and that is the
requirement rather than the lazy option: somebody who needs a screen reader
cannot navigate to a setting inside an app their screen reader cannot read.
Exactly `0` turns it off. `false`, `no`, `off` and an empty value all leave it
on, because a privacy control a typo can disable is a control that reports its
own state wrongly.

Setting an argument is not the same as the browser having taken it. So the app
asks the browser process afterwards what it actually received, and writes a
warning if the answer is no: naming the argument that is missing, or, when the
argument is there but something dangerous was planted beside it, naming that
instead. That second check matches switch names in lower case only, while the
browser itself does not care about case, so a capitalised spelling of one gets
past it and the report reads as verified. It has three answers rather than
two: a question it could not answer is unknown, never protected and never
broken. Unknown is the quiet one - the launch report simply carries no line
for it.

The proof that the tree is really shut is not in the ordinary test run. It is
a harness that opens a real window and attacks it from a second process, run
by hand, and its verdict is withheld unless it first finds the conversation
with the switch OFF.

### The log

`elysium.log` sits outside the vault in plain text and survives every lock, so
what may go into it is a rule rather than a habit. The rule, stated plainly:
**a numeric id outside the vault is acceptable; a name you read on screen, or
anything from inside the vault, never.** A chat id records that something
happened. A chat's title records what it was about, and so does a character's
name, or a persona's.

The file is written only by the packaged exe. Running `uvicorn main:app` by
hand gets the same lines on a console and no file at all. From a checkout
started the ordinary way the lines written before the server exists reach no
file, and only the warnings among them reach the console.

Both kinds have been found here. The name kind is closed, and the end of this
section says how. The other kind, a value out of your text reaching a log
line, is open and measured, and is the next paragraph. What keeps the rest
closed is a gate that reads every logging call in the shipped tree - 91 files,
and that number is checked rather than remembered - and fails the build on a
value that can carry content or a name: an exception's own message, which
nothing stops from being built out of your text; anything derived from one; a
value handed to a helper that logs it; and a list of the variable names this
codebase actually binds displayable text to. One wrinkle in that count, since
it is quoted as a fact: the gate reads what is on this disk rather than what
the build takes, so files kept only for development are counted with the rest.
Five of the seventy-eight are that today, and one of those five is not in the
repository at all, which is why the number is pinned to this disk rather than
to a fresh clone of it.

Tracebacks are the exception, and it is a large one, so it is stated rather
than buried. Writing a live exception's message into the log is the same leak
by another route, and there are fifty places that do it, across seventeen
modules. The gate SEES every one of them and fails on none: they are recorded
in a ledger of their own, separate from the content ledger further down, and
it checks that, so a new one cannot appear quietly and a paid-off one has to
be removed from the ledger, but the existing fifty ship as they are. Removing
them costs the ability to diagnose a crash from a user's log, and that trade
has not been made.

**What that gate cannot see, because it reads shapes and not values**: a
helper in a DIFFERENT module, a call reached through a dict or a callback, an
ATTRIBUTE carrying what a named variable would have carried, content that
leaves a string and comes back through a list, a name the list does not know
yet, and anything leaving by a route other than the logger - a `print`, a file
written by hand, a message put on the wire. It is a floor under code review,
not a replacement for it.

One thing the rule allows and a reader would rather be told about: absolute
file paths. The browser profile cleanup writes them when it removes a shortcut
somebody planted or shreds a crash dump of its own, and a path under your
profile folder carries your Windows account name. That is not a name off your
screen and not vault content, so the rule permits it. It is still a name, and
the alternative was a cleanup nobody could check afterwards.

The name kind used to be open here and is now CLOSED. A voice's id used to be
made from the label you typed for it, and on any install created before the
voice folders were hashed that id is still that label, still recorded in that
voice's own file - so the lines that deleted or listed a voice wrote a name
off your screen into the log. Those lines now write twelve characters of a
keyed hash instead, and the gate's content ledger is empty: not "a small
number that gets tolerated", zero, and it cannot grow without the build going
red.

---

## Does Elysium change my Windows settings?

**It writes nothing to the registry.** Registry access anywhere in the code is
READ ONLY, and there are two places: one detects whether the WebView2 runtime
is installed, and one reports whether a WebView2 policy key exists on this
machine. That second one is deliberately a report and not an edit: the policy
key belongs to every WebView2 application on the machine rather than to this
one, and this app does not touch another product's settings.

| Change | Scope | Survives the app closing? |
|---|---|---|
| Excluding the process heap from crash dumps | This process only | No, re-applied at every launch |
| Marking the data folder "do not index" | A file attribute on that folder | Yes, until the folder is deleted |
| Hiding the window from screen capture | That window | No, and **off by default**. The switch is in the Security tab and is stored in the vault; `ELYSIUM_SCREEN_PRIVACY=1` arms it at launch, which covers the lock screen and nothing after it: the first unlock re-reads the setting out of the vault, so an install where that setting is off turns the exclusion back off as soon as you are in |
| Blocking the browser crash reporter | A file inside the data folder | Deleted with the folder |
| Resetting the DLL search path | This process. PyInstaller documents children inheriting it, which is why it is worth doing, but nothing here measures a child | No, re-applied at every launch |
| **Narrowing the data folder's permissions** | That folder | **Yes**, on an install where it finds anything to do - see below |

### The one that is worth reading twice

At launch, Elysium checks whether any of eight principals beyond you can reach
its data folder: Everyone, Authenticated Users, Users, Guests, Anonymous,
Interactive, Network, and the one Windows calls Write Restricted Code, which
the log names as `Restricted Code`. If any can, it stops the folder inheriting
from its parent and then **removes their access**, and writes what it removed
to the log. Breaking inheritance first is why `/reset` rather than
`/inheritance:e` is the honest undo below.

Why: your database is encrypted, but `salt.bin` and `verifier.bin` beside it
are exactly what an offline passphrase attack needs. A second account on the
same computer should not be able to copy them.

What it does **not** do: it does not touch SYSTEM, Administrators or you, it
never names any folder except its own, and it makes no second pass over the
files inside. On a normal install it finds nothing to do and changes nothing
at all.

Read "you" as your account rather than every group you belong to. Four of the
eight - Everyone, Users, Authenticated Users, Interactive - are groups you are
in, and any of them that can reach the folder goes with the rest. Detaching
inheritance first turns the entries that were coming down from the parent into
the folder's own, so your account keeps its explicit entry and reaches the
folder through that instead. A test opens the folder as its owner afterwards
to check that.

One distinction that is easy to misread. Not walking into subfolders is about
what the app *does*, not about what the change *reaches*. Windows propagates a
folder's permissions down to everything inside that inherits them, so
`salt.bin` and `verifier.bin` do lose the wider access even though nothing
touched them directly. That is the point of the change.

To undo it, in an Administrator prompt:

```
icacls "%LOCALAPPDATA%\Elysium" /reset
```

`/reset` is the honest undo. The obvious alternative, `/inheritance:e`,
switches inheritance back on but leaves behind the explicit copies of the
permissions that breaking it created, so the folder ends up in a state that is
neither the old one nor the new one. `/reset` discards the folder's own
entries and puts it back under its parent's, which is where it started.

### Your folders are yours, and this is the line

Elysium narrows **one folder: its own.** It does not look at your Desktop,
your user profile, your Documents, or anywhere else, and it will not widen or
narrow a folder it does not keep its data in. That is a deliberate limit, not
an omission. Folder permissions outside an application's own directory are the
operating system's business and yours, and an app that quietly adjusts them is
doing something you did not ask for and cannot see.

One thing does reach past that folder. If a shortcut has been planted on the
app's own browser profile, the cleanup follows it far enough to destroy crash
dumps this app's own browser wrote on the far side. It removes nothing else
there, it needs three independent signs before it removes even that, and the
search is bounded. "The app window", above, has the detail. It is a deliberate
exception to the sentence above, so it is written down as one.

One consequence of that catches people out. Run from source and the data
folder is the `backend` folder inside the checkout, as the table at the top
says, so that is the folder this narrowing applies to: inheritance detached,
and the groups above removed from it. It is the same rule applied to the same
thing - the folder this app keeps its data in - but on a checkout under a
Desktop it is a folder you did not think of as Elysium's. Nothing above
`backend` is touched, `icacls ... /reset` undoes it exactly as it does for an
installed copy, and it only happens when the launcher starts the app: `uvicorn
main:app` by hand narrows nothing.

If you keep your own checkout somewhere other accounts on your PC can reach
and you would rather they could not, the same command works on any folder you
own:

```
icacls "C:\path\to\your\folder" /inheritance:d
icacls "C:\path\to\your\folder" /remove:g *S-1-5-32-545
```

The first line stops the folder inheriting from its parent; the second removes
the `Users` group. `icacls "C:\path\to\your\folder" /reset` puts it back. Do
it if you want it. Elysium will not do it for you and will not touch anything
above the folder it owns.

**And your data folder stays an ordinary folder.** Elysium does not deny you
access to your own files. You can copy it, move it to another drive, put it in
a backup, and delete it with `shift+delete` like any other folder, and nothing
in the app will stop you or need to be asked first. The one thing that changed
is that no single small file in it is a total loss any more: `vault.recovery`
is a second copy of the salt, and the table at the top says what it holds and
what it deliberately does not. Removing only `vault.recovery` is not a lasting
choice: the next successful unlock writes it again, because a second copy that
quietly stopped existing would be worse than none.

Removing BOTH copies does work, and that is a deliberate limit rather than a
gap: this protects against the accident and the one known filename, not
against somebody who reads this page and means it.

---

## What is NOT protected

Stated plainly, because these are the things people assume. Grouped by topic
so you can find the one you care about.

### Who else sees your words

- **Your provider reads your prompts.** Elysium forces zero-data-retention
  routing, but the model still reads what you send it. Encryption at rest is
  not encryption in transit to a third party you chose to use.
- **A SECOND model reads them too, if you let it.** The notebook's note reader
  is a model you choose separately, and once chosen it is sent excerpts of the
  conversation automatically, in the background, on your own API key - see
  "Where it connects" for how often and what stops it. The same policy is
  forced on it and the list only offers endpoints that keep no copy, but it is
  another party reading your words, and it starts reading without asking again
  each time.
- **What it writes goes into your prompts unreviewed, by default.** A note the
  model wrote is sent with every later message in that chat. "Keep suggestions
  without asking" is ON out of the box - the panel announces each note once
  and offers Undo, but if you never look, nobody reviewed it. Turn it off in
  the Notes tab to approve each one. A chat opened from an imported character
  card requires approval whatever that switch says, and that one cannot be
  undone: the route that sets a chat's own preference refuses any value but
  "always review" for an imported chat, including handing the decision back to
  the global switch. The per-chat control exists for the opposite case, a chat
  you started from a character you wrote yourself.
- **A note is text a model reads, and that is a soft boundary.** Notes are
  fenced with a random per-request tag so a message cannot forge a section
  break, the model's own text sits in a weaker block after the history, and
  every quote must appear verbatim in the transcript, after both sides have
  had their quote marks, dashes and spacing normalised. Two limits inside
  that. A quote carrying no names and no numbers gives the check nothing to
  hold on to, and a note resting on one is written without that half being
  tested. And the transcript it checks against includes the model's own
  replies, so a line it was talked into writing can be quoted back as evidence
  on the next pass. None of that makes a model immune to being talked to. Do
  not treat the notebook as a security control.
- **A limit is told to the model, not enforced in code.** The limits list is
  the strongest-worded block in the payload and it is still a block in a
  payload. `on_violation` and `rating_ceiling` are told to the model in the
  same block, as words: "if it happens, stop the scene and say so", "keep this
  at a PG-13 rating". Nothing in this application inspects a reply against
  them, stops a scene, or refuses a rating - they are instructions a model can
  follow, ignore or be talked out of, exactly like the limit they are attached
  to. If you need something to be impossible rather than discouraged, this is
  not the feature for it. The safe word is the single exception in this app,
  and it is described above under that name: a string this application cuts
  out of the request itself.
- **A running unlocked app is unlocked, and that is still the honest
  sentence.** Administrator rights reach any process's memory, and so does
  anything holding a handle it opened before the app started. The vault
  protects the file at rest and the window once it locks.

  What changed on 20 August 2026 is narrower and worth stating exactly.
  Elysium now takes `PROCESS_VM_READ` and its neighbours away from every
  program running as **you**, so an ordinary same-user program can no longer
  open Elysium by process id and read the vault key out of its memory. That
  was not hypothetical: an unprivileged program did exactly that to this app
  while this was being measured.

  Four limits, because a protection described loosely gets read as bigger than
  it is. It runs **after the window has loaded**, not at launch, because the
  browser engine cannot start under it; the launch token is therefore readable
  for the few seconds before that, and it is in the browser anyway. It does
  not stop an administrator. It does not stop a program that already had a
  handle. And it does not touch the process that actually draws your
  conversation, which is the next bullet and is the real ceiling.
- **The renderer's memory is readable, and this is the ceiling on everything
  else on this page.** Stated as a measurement rather than a caveat, because a
  reader deserves to know where the ceiling is: `ReadProcessMemory` against
  the window's renderer process, from an unprivileged process running as you,
  returns the conversation. It still does with every switch described here
  turned on. While the vault is unlocked the plaintext has to exist in that
  process, and Windows hands one of your processes a read handle to another of
  your processes by default. Closing the accessibility tree raises the cost of
  reading your chat from "call an interface Windows documents for screen
  readers" to "walk another process's heap"; it does not remove the floor
  underneath, and no switch inside the app can. This is an accepted risk, not
  an open defect, and the fix is not this project's to write.
- **Screenshots are not blocked out of the box.** Hiding the window from
  screen capture is off by default, so as shipped a program running as you can
  photograph the transcript with an ordinary screen capture. Turned on, the
  exclusion is real rather than nominal: `PrintWindow` and `BitBlt` against
  the window come back a fully black buffer, measured once by hand; what the
  ordinary test run checks is that the flag is set on a real window. It still
  ships off, because the cost lands immediately on your own screenshots of
  your own app, with nothing on screen to explain why they came out black.

### What is written outside the vault

- **UI preferences are not encrypted.** Text size, bubble solidity, sampling
  numbers and which chat and character were last open live in the window's
  local storage. No chat content is there. **The wallpaper itself is not among
  them, though numbers describing it are:** it is a picture you chose off your
  own disk, it is far too large for local storage, and it is held as a blob in
  the browser profile's IndexedDB (`elysium-appearance`) instead. Clearing
  local storage does not remove it, which is the practical reason the
  distinction is worth making here. The model you last picked used to be there
  and is not any more: a model id like `author/slug` is a name you read on
  screen, so it moved into the vault, and an install that already holds the
  old plaintext copy has it deleted on the next launch rather than merely
  stopped from writing a newer one.
- **Voice is not encrypted.** Generated speech is written as ordinary `.wav`
  files while the vault is open. They are wiped on lock, on unlock, on exit
  and on the next launch, the four moments the table above lists, but a hard
  kill can leave one until the next of them.
- **A reference clip is a recording of you, and it stays.** If you cloned a
  voice, the clip you recorded, the words in it and the label you gave it sit
  on disk as ordinary files that no lock touches. The table at the top of this
  page lists the three ways they leave.
- **The app derives a voiceprint from that clip without being asked.** The
  first time a cloned voice on the Fish S2 engine speaks, Elysium encodes your
  clip into a prompt-token file and writes it beside the clip, so the next
  reply does not pay for the encoding twice. There is no screen that mentions
  it and you have no copy of your own. It is not merely a cache: the engine
  will speak in that voice from the token file with the clip itself deleted,
  which makes it a working voiceprint rather than a derived scrap. That is
  what the file IS; no request this app sends takes that path, because every
  one of them hands the engine the clip. It is plaintext, outside the vault,
  and it survives every lock. Recording the clip again is not one of the ways
  it goes: replacing a clip shreds audio files and leaves the token file
  beside them. It is overwritten, in place and without shredding, the next
  time that voice speaks; if the voice never speaks again, the voiceprint of
  the take you replaced stays until you delete the voice. Two other engines
  clone and neither leaves one behind. Chatterbox keeps its conditioning in
  memory in a single slot, so it is worked out again every time you switch
  between two cloned voices, and it writes only the reply. Nothing in this
  app's worker for it saves a voiceprint, handed a path or not; what the
  engine underneath could do is not in this repository, so take that as safe
  by wiring like the other one rather than by construction. XTTS is where the
  difference is visible in code: its equivalent is held in memory, and nothing
  in the shipped app ever asks for it to be saved. That is a statement about
  what is wired up, not about what the code can do - the engine will write
  those latents to a file if it is handed a path, and no request in this app
  hands it one. It is read out of the worker rather than proven by a test.
  Either way the difference is between Fish S2 and the other two, not a
  general promise about cloning.
- **The search index remembers.** Marking the folder not-indexed is a promise
  about the future. Anything Windows already extracted stays in its index.
- **Anything you copy leaves the vault, and Elysium cannot follow it.** The
  Copy button puts plain text on the Windows clipboard, which every program
  running as you can read. Selecting a message and pressing Ctrl+C is the
  browser's own copy, not this app's, and that one puts a formatted copy
  alongside the plain one. **Locking the vault does not take it back**: the
  lock overwrites its key buffer, the clipboard is not Elysium's to clear, and
  a message you copied stays readable by any process afterwards. If Clipboard
  History is on it is also kept for Win+V, and do not assume it is off - it
  was already on, switched on by nobody for this app, on the machine this was
  measured on. If syncing across devices is on too, Windows uploads it to your
  Microsoft account. Elysium cannot exclude itself from either: Chromium only
  sets the opt-out flags for windows running in private mode, and this window
  deliberately does not, so that your appearance settings survive a restart.

### Limits of secure deletion

- **Deleting every chat does not delete what the notebook spent.** Two tables
  inside the vault hold it. `notebook_spend` keeps one row per day: calls,
  tokens, cost, and how many of them came back with no price at all, which the
  app is careful not to record as free. `notebook_spend_calls` keeps a row per
  call: an id, when it happened, its tokens and its cost. That id is the
  request id the provider returned when there was one, and a random one this
  app minted when there was not; a call that ends up with neither is still
  counted in the daily row but gets no row of its own here. The first is what
  makes the sixty-a-day ceiling survive a restart, the second is what stops
  one call being counted twice, and neither is tied to a chat. Nothing prunes
  either, so both outlive every chat you delete: a record of which days you
  used the note reader, how often, and how much it cost, for as long as the
  folder exists.
- **Deleted is not shredded on an SSD.** Overwriting defeats undelete tools,
  not a controller that quietly moved the original blocks - see "Deleting
  things" above for the answer to that.
- **Hidden extra streams are not overwritten.** NTFS lets a file carry hidden
  side-channels, and shredding rewrites only the main one. A scan of 34,200
  files across every folder Elysium deletes from found **none**, so this is a
  mechanism without an instance here - and realistically it would hold a
  "downloaded from the internet" tag, not conversation. That figure was
  measured once by hand and nothing in the tree reproduces it, which is worth
  knowing before you lean on it.
- **Files with names Windows cannot open stay put.** A name ending in a dot or
  space, or a reserved device name, cannot be opened the ordinary way, so it
  cannot be overwritten the ordinary way either, and Elysium reports such a
  file rather than pretending it deleted it. Be exact about "cannot": the app
  already turns Windows path normalisation off in one place, so that a folder
  with a trailing space is not misread as a shortcut, and it does not do the
  same to overwrite one. That is a line it does not cross rather than one it
  could not. Scanned the same way as the streams above, with the same caveat -
  once, by hand, with nothing in the tree that reproduces it - and also **no
  instances**, because the browser names its cache files from hashes, which
  cannot produce one.

### Windows crash dumps

When a program crashes, Windows can write a memory dump to
`%LOCALAPPDATA%\CrashDumps`, a system-wide folder outside Elysium's reach. It
was measured both ways: an ordinary program does get a dump written, but
crashing Elysium's own browser window on purpose produced no dump at all, in
three different configurations. The path this section exists to worry about
did not reproduce.

The limit on that measurement: the window was crashed one way. **A different
kind of failure - a graphics driver fault, an out-of-memory kill - might
behave differently, and those were not tested.** If you want certainty rather
than a measurement, turn crash dumps off in Windows and delete that folder; it
is not inside `%LOCALAPPDATA%\Elysium`, so removing the app leaves it behind.
On most machines it was some other program that switched this on in the first
place.

### The download itself

- **Elysium.exe is not code-signed.** Nothing cryptographic ties the file you
  downloaded to its author, so Windows shows a SmartScreen warning and Smart
  App Control may refuse it outright. Until that changes, the integrity of
  your copy rests on the transport you fetched it over. A signing certificate
  is a cost decision that has not been made yet, and saying so is better than
  letting the absence pass unmentioned.
- **A voice model you download is code.** Some engines load checkpoints in a
  format that can execute code as it is read. Elysium runs them in a separate
  process, but that process is yours: treat a model folder like an executable
  and take it only from a source you trust.

---

## If I delete Elysium, what is left?

Delete `%LOCALAPPDATA%\Elysium` and the encrypted database, the plaintext
backup a one-time upgrade may have left, any premigrate snapshot beside it,
the passphrase files, the voice models, your reference clips, the browser
profile and the log all go with it. The application files are separate and
hold no user data.

Six things that folder does not cover, though the fourth applies only if you
ran from source:

1. Crash dumps in `%LOCALAPPDATA%\CrashDumps` from before this version, or
   from the kinds of window failure "Windows crash dumps" above says were not
   tested. Delete that folder yourself if you want to be sure.
2. Whatever the Windows search index extracted before the no-index attribute
   was set. Rebuild the index from Indexing Options to clear it.
3. A Windows Credential Manager entry named `chatbot_interface`, if you
   upgraded from a very old version and never unlocked the vault afterwards.
   New installs never write there. **Not verified as impossible** - check
   Credential Manager if you are being thorough.
4. The permission change described above, if it ran, and only where the
   narrowed folder is not the one you just deleted. On a packaged install it
   is that folder, so deleting it takes the change with it; on a source
   checkout it is the `backend` folder and it stays. The command to undo it is
   there.
5. Anything you copied to the clipboard, if Clipboard History is on. Clear it
   from Win+V, or from Settings, System, Clipboard. A synced copy on your
   Microsoft account is not on this machine at all, so deleting the folder
   does nothing to it.
6. A `_MEI` folder or two under your temp directory. Elysium ships as a single
   exe that unpacks itself there at every launch and clears it again on a
   clean exit; a crash or an End Task can leave one behind. They hold
   application files, never anything you wrote, and they are safe to delete.

---

## Reporting something

This is a single-developer, local-first project. If you find a problem, open
an issue with what you did and what you expected. Do not include your
passphrase, your API key, or a log you have not read.
