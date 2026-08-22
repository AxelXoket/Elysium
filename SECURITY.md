# Elysium: what protects your data, and what does not

This document exists so you never have to take a claim on trust. Every
protection below says what it does, whether it is on by default, and what it
does **not** cover. The limits are here on purpose: a security page that only
lists wins is a marketing page.

Written against version 1.1.5.

---

## The short version

- Everything you write lives in **one encrypted file**. Without your passphrase
  it cannot be opened, not even as a plain database. Three things are written
  outside it: spoken replies, as plain audio wiped at every lock; only for
  a voice model that CLONES, the reference clip you record and a transcript of
  the words in it, alongside a voiceprint the app derives from your clip by
  itself; and UI preferences, kept in the window's local
  storage - though NOT the wallpaper, which is an IndexedDB blob, as the
  section below spells out. There is also no way to recover a forgotten
  passphrase from inside the vault - the lock screen's own "Forgot your
  passphrase?" flow is a way out of one, and it is documented below under
  Resetting the vault.
- Your passphrase is **never stored**. Lose it and the data is gone. That is the
  design, not a bug.
- The app talks to **one address on the internet** (OpenRouter), and refuses
  every other destination at the connection layer - including the right host
  reached over plain `http`, which would put the API key on the wire in the
  clear. That list is pinned to the shipped address; pointing the app
  somewhere else takes a second, deliberately named switch
  (`ELYSIUM_ALLOW_BASE_URL_OVERRIDE=1`), and without it a changed address is
  refused rather than followed. One exception: the optional voice engine
  installer, which you start by hand and which reaches GitHub and PyPI.
- **Your own machine is not an exception either.** `127.0.0.1`, `localhost`
  and `::1` used to be permitted destinations unconditionally, on the reasoning
  that the app is itself a local server. It was measured on 20 August 2026 and
  the reasoning was wrong twice: a proxy you configure on loopback never
  reaches that check, and the launcher's own health probe does not go through
  it. What the entries did do was make any program that can open a listening
  socket on this machine a permitted destination for a request carrying your
  API key. They now need the same `ELYSIUM_ALLOW_BASE_URL_OVERRIDE=1` switch as
  any other address, because it is the same decision.
- Elysium writes **nothing to the Windows registry**. Deleting one folder
  removes everything it ever created, with five caveats listed at the end.

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
| `app.db.plain.bak-*` - a pre-vault copy kept after a one-time upgrade | **No, plaintext.** Settings > Security lists it and deletes it on request |
| `app.db.premigrate.bak` - a COMPLETE encrypted copy of the vault, taken before an uploads migration touches anything and kept whenever that migration does not finish cleanly | **Yes**, same cipher, and a passphrase change re-keys it so the old one stops opening it. Settings > Security now lists it and a button removes it; a later clean migration still discards it on its own. Either way, removing it needs the vault unlocked, because only your key can tell a healthy copy from a stranger's |
| `app.db.premigrate.bak.unreadable-<ts>` - that same snapshot, moved aside because it did not open with this vault's key | Encrypted, under a passphrase this vault does not hold. It is moved rather than deleted because it may be the only copy of an older vault, and nothing short of a vault reset removes it for you |
| `voice/models` - voice model weights you downloaded | No. Files you chose and put there; they hold none of your conversation |
| `voice/refs` - only for a voice model that CLONES: the reference clip you record and a transcript of the words in it, the label you gave that voice, and, for one engine, a voiceprint the app derives from your clip by itself | **No, plaintext.** Nothing purges these: not the lock, not shutdown, not the next launch. They go when you delete that voice, delete the folder, or reset the vault. The FOLDER is named with a one-way hash, so a directory listing is no longer a roster of the voices you have cloned; the file inside it still names the voice |
| `voice/cache` - generated speech, your conversation as audio | No. Cleared at every lock, every launch and every shutdown; anything older than 30 minutes is cleared as the next reply is spoken |
| `webview/` - the app window's browser profile | No. Cache, history and session files are wiped at every launch and exit; only cosmetic settings and your wallpaper are kept |
| `elysium.log` - application log | No. It carries no message text, no note text, no keys and no passphrases - but it does record that things happened: chat and note ids, counts, and the TYPE name of an error. A plaintext record of which chats have notes, sitting beside an otherwise opaque vault. A vault reset now shreds it, along with its rotated `elysium.log.1`, because the route promises the folder is left as if Elysium had never run and a log naming your chats is not that |
| `port` - the port the server last used | No, and one number with nothing in it to protect. A vault reset shreds it anyway, alongside the log |

In a development checkout this folder is the source tree instead, which is why
you may see these files beside the code.

---

## What protects it

### Your passphrase

Turned into a key with scrypt, a deliberately slow and memory-hungry function
(128 MB per attempt). That cost is what makes guessing expensive. There is no
login screen to rate-limit an attacker: somebody who copies your folder guesses
offline, as fast as their hardware allows, forever. So the passphrase floor is
**12 characters**, with a few shape checks (no keyboard walks, no one idea
repeated, no single character filling half of it).

There are deliberately **no composition rules**. Forcing an uppercase, a digit
and a symbol is how you get `Password1!`. Three unrelated words beat it.

### The database

The whole file is encrypted, not just the fields. Opening it with an ordinary
SQLite tool fails; it does not show you a table with scrambled cells, it does
not open at all.

### Idle auto-lock

**On by default, 5 minutes.** Locking clears the key from memory, tears down the
voice engine (giving the GPU memory back), drops the network client, and stands
the notebook's background reader down. Idle means nothing in flight and nothing
finished recently, so a reply that is still streaming holds the vault open
however long it takes. Change the delay or turn it off in Settings > Security.

One thing deliberately does **not** hold it open: the notebook's background
reader. It runs while you are reading something else, so counting it as
activity would mean the vault never locked on a busy chat. Instead the lock
cancels its planning loop and empties its queue. Work still queued and never
sent costs nothing, so a later run reads it exactly as if the lock had not
happened.

One thing the lock does **not** cancel, and this page said otherwise until
20 August 2026: an extraction whose reply has already ARRIVED. Once a
reply is in hand it has been paid for, so the lock waits up to five seconds
for it to be parsed and written, and only then wipes the key. Read that
precisely: the wait is for a reply already in hand, never for one still on
the wire - the paragraph below covers that case, and it is cancelled, not
awaited. So a note can be written into the vault in the seconds
**after** you press Lock. That is deliberate - the alternative is to pay for
an answer and throw it away - but "cancels whatever it was doing" was not a
true description of it.

A call already on the wire to the provider when the lock lands is a
different case, and this document used to describe it too kindly - and then,
having been rewritten, described it as a billing problem when the sharper
half is a **promise** problem. Say that half first: the lock is supposed to
mean the conversation is closed, and for this one path it does not. The
request carries decrypted chat text, it has already left the machine, and
nothing the lock does can call it back. Locking narrows the window; it does
not close it.

The billing half, which is real but smaller: that call
is billed the moment it leaves, whatever happens to the app afterward, and
until this session the same range was simply re-planned and re-sent on the
next cycle - a second copy of your conversation leaving the machine, and a
second charge for it. It is now marked as a failed call and never retried,
so the range is not sent twice; the price of that fix is that those
particular messages are never read into a note at all.

Read that as narrowly as it is written: it covers the call that was in
flight when the app stopped, and only that one. A reply that **arrives** and
then fails to be parsed, or fails to be written to disk, does not move the
notebook's cursor, so the same range is planned again on the next threshold
and paid for again. The circuit breaker caps how far that can run - five
consecutive failures put it to sleep, twenty stop it, and the daily ceiling
holds above both - but the app does not treat a paid answer it could not use
as spent. This is a deliberate trade (the alternative is to skip those
messages forever) and it had never been written down. A related gap was
closed the same way: editing or deleting a message rolls the notebook's
cursor back on purpose, so the rewritten stretch gets read again later, and a
reply that was already in flight before the edit used to land anyway and get
written from wording you had just taken back. The reply is now checked
against the trace its own call left before it was sent; if the edit got there
first and erased that trace, the reply is discarded instead of written.

### The key in memory

While unlocked, the key is in RAM. On lock, the buffer is overwritten rather
than merely dropped. **This is not "the key is wiped from memory"** and the code
says so: copies exist that Python cannot reach, including anything Windows
paged to disk. Overwriting one buffer is worth doing; claiming more would be a
lie.

### One window per vault

Locking is per process. Two Elysium windows against the same data folder each
kept their own copy of the vault key in their own memory, so locking one - or
letting it lock itself - cleared that copy and left the other window sitting on
a fully decryptable database. The one gesture the whole at-rest design rests on
did half of what it appeared to do, and the idle auto-lock had the same blind
spot.

So a second launch is now refused rather than unsupported. It raises the window
that is already open and exits before it touches the data folder at all, which
also stops it overwriting the launch token the running window is using. The
claim is a Windows kernel object named after the folder rather than a lock file,
so a crash or an End Task releases it with nothing left to clean up and nothing
to guess about. Two different data folders (`ELYSIUM_DATA_DIR`) still get a
window each, because they share no key and no database.

### Deleting things

Anything sensitive is overwritten with random bytes before being unlinked, by a
single shared routine that also refuses to delete through a junction, a symlink
or a hardlink. **It is not a guarantee against physical recovery**: on an SSD,
wear levelling can leave the original blocks readable to firmware-level
analysis. Full-disk encryption (BitLocker) is the only answer to that, and it is
yours to enable.

### The local server

Elysium runs a small web server on `127.0.0.1` so the window can talk to it.
Loopback is not a permission boundary: any program running as you could
otherwise read every conversation with one command. So the server requires a
secret generated at launch and given only to the app window.

**This is only armed in the packaged app.** A developer running the backend by
hand has no token and the gate is open. One route does not follow that rule:
`/vault/reset` refuses outright when the gate is unarmed, rather than being
left open. See "Resetting the vault," below.

Two routes are exempt because a browser cannot attach a header to an image or an
audio element. They are narrowed rather than opened: the browser must send
`Sec-Fetch-Site: same-origin`, which a command-line tool does not.

### Resetting the vault

The lock screen has a "Forgot your passphrase?" flow behind `POST
/vault/reset`. There is no way to recover a forgotten passphrase - see above -
so this is the honest answer to being locked out for good: it destroys the
vault and lets you start over, rather than leaving you with a database
nothing can open.

It only runs from the LOCKED state. If the vault is unlocked it refuses
outright with HTTP 409 before the confirmation phrase is even read, because
whoever can already unlock the vault does not need this door, and answering
it anyway would turn it into a way to destroy a conversation somebody is
reading right now. From the locked state it still requires a typed
confirmation phrase, checked against a value only the backend decides, so a
frontend bug cannot fire it with an empty or a near-miss string - but that
phrase only guards against an accident, never against someone reaching over
your shoulder.

What it destroys: the database and every backup family beside it (plaintext,
orphaned, rotation, and both premigrate names, including the one moved aside
as unreadable), the passphrase identity files including the salt mirror, and every shelved copy of them,
the empty stub a recovery can leave, the uploads folder, saved voice
references and cached speech, the desktop app's browser profile, and any
leftover OS-keyring entry from the legacy migration. It also shreds
`elysium.log`, its rotated `elysium.log.1` and `port` - all listed above.
That was not always true: they survived, and the log names chat and note
ids, so a wiped vault left behind a plaintext record of which chats had
held notes. It does not touch a downloaded voice engine, which is software
you chose rather than data you wrote.

A file held open elsewhere can survive the wipe. The route answers HTTP 200
either way: a clean run reports `{"ok": true, "left": []}`, and a file that
would not go is named rather than hidden, as `{"ok": false, "left": [...]}`
under that same 200 instead of an error status. Everything that IS removed
goes through the same overwrite-then-delete this app uses everywhere else -
see "Deleting things," above, for what that defeats and what it does not.

**This route does not exist outside the installed app.** It answers 404 unless
two things are true at once: this is the packaged build, and the launch token
gate is armed. A development checkout is neither, so `uvicorn main:app` and
`python run_app.py` from source have no reset door at all. That closes what
this document used to describe here: a bare local request in a dev checkout,
carrying the exact confirmation phrase, wiping the vault with no passphrase
and nothing else standing in the way.

Being the packaged build is not on its own enough, and neither is an armed
gate, which is why both are required. `sys.frozen` describes the process, not
whoever is calling it, and the launcher issues a token on the development path
too.

What it does NOT do, stated here rather than left to inference: it does not
distinguish the app window from another program on this machine holding the
same launch token. In a packaged build, anyone
able to present the current launch token can still do this; that is the same
"any code running as this user" boundary the rest of the unlocked vault
already accepts, extended to the locked one, not a new one - someone with
that access could already delete every one of these files by hand. The
cross-origin write shield that protects most other mutating routes from a
hostile web page does not close this gap either: a bare local process sends
no `Origin` and no `Sec-Fetch-Site` header, and the shield's own fallback
treats an absent header as non-browser tooling and lets it through.

### Where it connects

One provider host, enforced by refusing any other destination before the
connection opens. System proxy environment variables are ignored on purpose.
Every request forces `zdr: true`, `data_collection: deny`, `allow_fallbacks:
false`, and the app window **cannot** override those three.

**Two things send your conversation there, not one.** The reply you asked
for; and the notebook's note reader - a second model, which you choose, sent
excerpts of the same conversation automatically every twenty turns while you
are not watching. Both go to the same single host under the same locked
policy, and the second is off entirely until you pick a model, but each is
its own sender and each spends your credits. What the note reader did and
what it cost is in the Notes tab; the ceiling is sixty calls a day and it is
a block, not a warning, overridable only from the environment (`ELYSIUM_NOTEBOOK_DAILY_CALLS`), not
from any screen in the app. An interruption used to turn the note reader into
a third sender of the same words: a call already sent got re-sent whole on
the next cycle instead of being counted as spent. That is fixed now - see
Idle auto-lock, above, for what changed and what it costs instead.

A third request reaches the same host and carries none of your conversation:
the Security tab's key check asks OpenRouter whether the key you already stored
is still accepted. It happens only when you press it, and a provider it cannot
reach is reported as exactly that rather than as a bad key.

The one other egress is the optional voice engine setup, which you start
yourself. It downloads from GitHub and PyPI, uploads nothing, and no chat,
persona or voice data is in scope.

Once installed, the voice engine runs as a separate process that talks to the
app over pipes and is given no reason to reach the network: proxy and hub
variables are stripped from its environment and `HF_HUB_OFFLINE=1` is set, so
the model loader reads from disk instead of calling out. Be precise about what
that is, though. It is a configured boundary, not an enforced one. The engine
is an ordinary child process, so nothing at the operating-system level stops it
from opening a socket, and the app's own single-host check does not extend into
it. If you want a hard guarantee for this one component, add a firewall rule
for it; the app cannot make that promise on your behalf, so it does not.

Pictures a model returns are accepted only when they arrive inline. A link to a
remote image is refused rather than fetched, because fetching it would add a
second place your data goes.

### The app window

Browser caches are the reason this section exists: the window used to write
whole conversations to disk as plain readable JSON, surviving every lock. Those
caches are now wiped at launch and exit, API responses are marked no-store, and
the browser's crash reporter is prevented from starting at all so a crash cannot
write a memory dump and send it to Microsoft.

### The accessibility tree

The window's browser builds a second representation of the page for assistive
technology, and that representation is your conversation as TEXT, offered to
any program running as you through UI Automation and MSAA. An audit built an
unprivileged probe - no token, no elevation, the same user account - and read
the whole transcript out of it: chat title, character name, message bodies,
verbatim.

**Hiding the window from screen capture does not close this**, and if you
assumed it did, that is the assumption worth correcting first. That flag
excludes PIXELS. This is not pixels, and the probe recovered the same strings
with the flag confirmed set. MSAA and UI Automation are not two doors either:
the browser builds one tree and serves both from it, so shutting one API
surface would have shut neither.

**A switch closes it, and it is ON unless you turn it off.** It is the only
protection in this app that is on when nobody asked for it. The reason is the
shape of the trade: what it costs falls on software most people do not run,
and what it prevents is any program on this machine reading your conversation
as text, with nothing to unlock and nobody to ask.

Three things about that switch are limits rather than features, so they are
stated here rather than left to be discovered:

- **It takes effect at startup only.** It is a command-line argument to the
  browser process, read once when the browser environment is created. Changing
  it while Elysium is running does nothing at all - not on the next unlock, not
  on the next chat.
- **Its setting cannot live in the vault**, and that is not an oversight. The
  decision has to be made before a passphrase exists, and the way out of it has
  to work for somebody who cannot read the screen.
- **While it is on, a screen reader cannot read Elysium either.** That is the
  whole cost and it is a real one. This is not a setting with a free side.
- **Elysium replaces the WebView2 environment variables rather than adding to
  them**, and it does that whether the switch is on or off. Those variables
  are writable by any program running as you with one `setx` and no
  administrator rights, and what they carry goes to the browser engine drawing
  your conversation: a debugging port, a redirected profile folder, or the
  browser binary itself. Elysium used to append its own switch and pass the
  rest along; it now discards them and says so in the log, naming the switch
  and never its value. Two limits worth knowing. The same five settings can
  also come from a Windows policy key, which Elysium reports and will not
  edit, because that key belongs to every WebView2 application on the machine
  and not to this one. And this is the environment door only: it does not
  change the fact below about the renderer's memory.

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
warning naming the missing argument if the answer is no. It has three answers
rather than two: a question it could not answer is reported as unknown, never
as protected and never as broken.

The proof that the tree is really shut is not in the ordinary test run. It is a
harness that opens a real window and attacks it from a second process, run by
hand, and it must find the conversation with the switch OFF before it is
allowed to report anything about the switch being on.

### The log

`elysium.log` sits outside the vault in plain text and survives every lock, so
what may go into it is a rule rather than a habit. The rule, in the owner's
words: **a numeric id outside the vault is acceptable; a name you read on
screen, or anything from inside the vault, never.** A chat id records that
something happened. A chat's title records what it was about, and so does a
character's name, or a persona's.

The file is written only by the packaged exe. A developer running the backend
by hand gets the same lines on a console and no file at all.

Leaks of both kinds were found in this round, and the ones still open are
named at the end of this section rather than left out of it. What keeps the
rest closed is a gate that reads every logging call in the shipped tree - 73
files at the last count - and fails the build on a value that can carry
content or a name: an exception's own message, which nothing stops from being
built out of your text; anything derived from one; a value handed to a helper
that logs it; and a list of the variable names this codebase actually binds
displayable text to.

Tracebacks are the exception, and it is a large one, so it is stated rather
than buried. Writing a live exception's message into the log is the same leak
by another route, and there are forty-eight places that do it, across seventeen
modules. The gate SEES every one of them and fails on none: they are recorded
in a ledger it checks, so a new one cannot appear quietly and a paid-off one
has to be removed from the ledger, but the existing forty-eight ship as they
are. Removing them costs the ability to diagnose a crash from a user's log,
and that trade has not been made.

**What that gate cannot see, because it reads shapes and not values**: a helper
in a DIFFERENT module, a call reached through a dict or a callback, an
ATTRIBUTE carrying what a named variable would have carried, content that
leaves a string and comes back through a list, a name the list does not know
yet, and anything leaving by a route other than the logger - a `print`, a file
written by hand, a message put on the wire. It is a floor under code review,
not a replacement for it.

One leak of the second kind is still open, and it is counted rather than
described as closed. A voice's id used to be made from the label you typed for
it, and on any install created before the voice folders were hashed that id is
still that label, still recorded in that voice's own file. Four log lines print
it. Voices created since carry an opaque id and are not affected.

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
| Hiding the window from screen capture | That window | No, and **off by default**. The switch is Settings > Security and is stored in the vault; `ELYSIUM_SCREEN_PRIVACY=1` arms it at launch for somebody who wants it on before the vault is unlocked |
| Blocking the browser crash reporter | A file inside the data folder | Deleted with the folder |
| Resetting the DLL search path | This process and its children | No, re-applied at every launch |
| **Narrowing the data folder's permissions** | That folder | **Yes** - see below |

### The one that is worth reading twice

At launch, Elysium checks whether groups beyond you can reach its data folder
(Everyone, Users, Guests, and similar). If any can, it **removes their access**
and writes what it removed to the log.

Why: your database is encrypted, but `salt.bin` and `verifier.bin` beside it are
exactly what an offline passphrase attack needs. A second account on the same
computer should not be able to copy them.

What it does **not** do: it does not touch SYSTEM, Administrators or you, it
never names any folder except its own, and it makes no second pass over the
files inside. On a normal install it finds nothing to do and changes nothing at
all.

One clarification, because the earlier wording here was easy to misread. Not
walking into subfolders is about what the app *does*, not about what the change
*reaches*. Windows propagates a folder's permissions down to everything inside
that inherits them, so `salt.bin` and `verifier.bin` do lose the wider access
even though nothing touched them directly. That is the point of the change, and
it is measured by a test rather than assumed.

To undo it, in an Administrator prompt:

```
icacls "%LOCALAPPDATA%\Elysium" /reset
```

`/reset` is the honest undo. `/inheritance:e`, which this document recommended
before, switches inheritance back on but leaves behind the explicit copies of
the permissions that breaking it created, so the folder ends up in a state that
is neither the old one nor the new one. `/reset` discards the folder's own
entries and puts it back under its parent's, which is where it started.

### Your folders are yours, and this is the line

Elysium narrows **one folder: its own.** It does not look at your Desktop, your
user profile, your Documents, or anywhere else, and it will not widen or narrow
a folder it did not create. That is a deliberate limit, not an omission. Folder
permissions outside an application's own directory are the operating system's
business and yours, and an app that quietly adjusts them is doing something you
did not ask for and cannot see.

The same rule applies to us while we are building it. Elysium is developed in a
folder on a Desktop, and a Desktop grants the local `Users` group read and write
by default, which every folder underneath then inherits. That folder was
narrowed **by hand, on that one machine**, and the Desktop above it was left
exactly as Windows made it. Nothing about that change is in this repository, is
shipped, or happens to you: a permission is a property of a folder on a disk,
not something a program carries with it.

If you keep your own checkout somewhere other accounts on your PC can reach and
you would rather they could not, the same command works on any folder you own:

```
icacls "C:\path\to\your\folder" /inheritance:d
icacls "C:\path\to\your\folder" /remove:g *S-1-5-32-545
```

The first line stops the folder inheriting from its parent; the second removes
the `Users` group. `icacls "C:\path\to\your\folder" /reset` puts it back. Do it
if you want it. We are not going to do it for you, and we are not going to
touch anything above the folder we own.

**And your data folder stays an ordinary folder.** Elysium does not deny you
access to your own files. You can copy it, move it to another drive, put it in
a backup, and delete it with `shift+delete` like any other folder, and nothing
in the app will stop you or need to be asked first. The one thing that changed
is that no single small file in it is a total loss any more: `salt.bin` was
sixteen bytes whose deletion destroyed everything permanently, and
`vault.recovery` is why that is no longer true. Somebody who reads this page
and removes both copies has removed both copies. Removing only `vault.recovery`
is not a lasting choice: the next successful unlock writes it again, because a
second copy that quietly stopped existing would be worse than none.

Removing BOTH copies does work, and that is a deliberate limit rather than a
gap: this protects against the accident and the one known filename, not
against somebody who reads this page and means it.

---

## What is NOT protected

Stated plainly, because these are the things people assume. Grouped by topic so
you can find the one you care about.

### Who else sees your words

- **Your provider reads your prompts.** Elysium forces zero-data-retention
  routing, but the model still reads what you send it. Encryption at rest is not
  encryption in transit to a third party you chose to use.
- **A SECOND model reads them too, if you let it.** The notebook's note reader
  is a model you choose separately, and once chosen it is sent excerpts of the
  conversation automatically - in the background, every twenty turns, on your
  own API key. The same policy is forced on it and the list only offers
  endpoints that keep no copy, but it is another party reading your words, and
  it starts reading without asking again each time. It is off until you pick
  one; the Notes tab shows every call it has made.
- **What it writes goes into your prompts unreviewed, by default.** A note the
  model wrote is sent with every later message in that chat. "Keep suggestions
  without asking" is ON out of the box - the panel announces each note once and
  offers Undo, but if you never look, nobody reviewed it. Turn it off in the
  Notes tab to approve each one. A chat opened from an imported character card
  always requires approval regardless.
- **A note is text a model reads, and that is a soft boundary.** Notes are
  fenced with a random per-request tag so a message cannot forge a section
  break, the model's own text sits in a weaker block after the history, and
  every quote must appear verbatim in the transcript. None of that makes a
  model immune to being talked to. Do not treat the notebook as a security
  control.
- **A limit is told to the model, not enforced in code.** The limits list is
  the strongest-worded block in the payload and it is still a block in a
  payload. `on_violation` is stored and deliberately not acted on. If you need
  something to be impossible rather than discouraged, this is not the feature
  for it.
- **A running unlocked app is unlocked, and that is still the honest sentence.**
  Administrator rights reach any process's memory, and so does anything holding
  a handle it opened before the app started. The vault protects the file at
  rest and the window once it locks.

  What changed on 20 August 2026 is narrower and worth stating exactly.
  Elysium now takes `PROCESS_VM_READ` and its neighbours away from every
  program running as **you**, so an ordinary same-user program can no longer
  open Elysium by process id and read the vault key out of its memory. That
  was not hypothetical: an unprivileged program did exactly that to this app
  during an earlier audit.

  Four limits, because a protection described loosely gets read as bigger than
  it is. It runs **after the window has loaded**, not at launch, because the
  browser engine cannot start under it; the launch token is therefore readable
  for the few seconds before that, and it is in the browser anyway. It does
  not stop an administrator. It does not stop a program that already had a
  handle. And it does not touch the process that actually draws your
  conversation, which is the next bullet and is the real ceiling.
- **The renderer's memory is readable, and this is the ceiling on everything
  else on this page.** Stated as a measurement rather than a caveat, because a
  reader deserves to know where the ceiling is: `ReadProcessMemory` against the
  window's renderer process, from an unprivileged process running as you,
  returns the conversation. It still does with every switch described here
  turned on. While the vault is unlocked the plaintext has to exist in that
  process, and Windows hands one of your processes a read handle to another of
  your processes by default. Closing the accessibility tree raises the cost of
  reading your chat from "call an interface Windows documents for screen
  readers" to "walk another process's heap"; it does not remove the floor
  underneath, and no switch inside the app can. This is an accepted risk, not
  an open defect - the fix is not ours to write.
- **Screenshots are not blocked out of the box.** Hiding the window from screen
  capture is off by default, so as shipped a program running as you can
  photograph the transcript with an ordinary screen capture. Turned on, the
  exclusion is real rather than nominal: `PrintWindow` and `BitBlt` against the
  window come back a fully black buffer, measured. It still ships off, because
  the cost lands immediately on your own screenshots of your own app, with
  nothing on screen to explain why they came out black.

### What is written outside the vault

- **UI preferences are not encrypted.** Text size, bubble solidity, sampling
  numbers and which chat and character were last open live in the window's
  local storage. No chat content is there. **The wallpaper is not among
  them:** it is a picture you chose off your own disk, it is far too large for
  local storage, and it is held as a blob in the browser profile's IndexedDB
  (`elysium-appearance`) instead. Clearing local storage does not remove it,
  which is the practical reason the distinction is worth making here. The model you last
  picked used to be there and is not any more: a model id like `author/slug` is
  a name you read on screen, so it moved into the vault, and an install that
  already holds the old plaintext copy has it deleted on the next launch rather
  than merely stopped from writing a newer one.
- **Voice is not encrypted.** Generated speech is written as ordinary `.wav`
  files while the vault is open. They are wiped on lock, on exit and on the next
  launch, but a hard kill can leave one until then.
- **A reference clip is a recording of you, and it stays.** If you cloned a
  voice, the clip you recorded, the words in it and the label you gave it sit on
  disk as ordinary files that no lock touches. This is the one thing this
  section used to leave out entirely. They leave when you delete that voice,
  delete the folder, or reset the vault, and not before.
- **The app derives a voiceprint from that clip without being asked.** The first
  time a cloned voice on the Fish S2 engine speaks, Elysium encodes your clip
  into a prompt-token file and writes it beside the clip, so the next reply does
  not pay for the encoding twice. There is no screen that mentions it and you
  have no copy of your own. It is not merely a cache: the engine will speak in
  that voice from the token file with the clip itself deleted, which makes it a
  working voiceprint rather than a derived scrap. It is plaintext, outside the
  vault, and it survives every lock. Be precise about the other cloning engine
  rather than reassured by it: its equivalent is held in memory, and nothing
  in the shipped app ever asks for it to be saved. That is a statement about
  what is wired up, not about what the code can do - the engine will write
  those latents to a file if it is handed a path, and no request in this app
  hands it one. It is read out of the worker rather than proven by a test.
  Either way the difference is between two engines and not a general promise.
- **The search index remembers.** Marking the folder not-indexed is a promise
  about the future. Anything Windows already extracted stays in its index.
- **Anything you copy leaves the vault, and Elysium cannot follow it.** The
  Copy button and Ctrl+C both put plain text on the Windows clipboard, which
  every program running as you can read. **Locking the vault does not take it
  back**: the lock clears the key, the clipboard is not Elysium's to clear, and
  a message you copied stays readable by any process afterwards. If Clipboard
  History is on it is also kept for Win+V, and do not assume it is off - it was
  already on, switched on by nobody for this app, on the machine this was
  measured on. If syncing across devices is on too, Windows uploads it to your
  Microsoft account. Elysium cannot exclude itself from either: Chromium
  only sets the opt-out flags for windows running in private mode, and this
  window deliberately does not, so that your appearance settings survive a
  restart.

### Limits of secure deletion

- **Deleting every chat does not delete what the notebook spent.**
  `notebook_spend` keeps one row per day - calls, tokens and cost - inside the
  vault. It is what makes the sixty-a-day ceiling survive a restart, so nothing
  prunes it and it outlives every chat you delete: a record of which days you
  used the note reader, and how much it cost, for as long as the folder exists.
- **Deleted is not shredded on an SSD.** Overwriting defeats undelete tools, not
  a controller that quietly moved the original blocks. Full-disk encryption is
  the answer, and it is yours to enable.
- **Hidden extra streams are not overwritten.** NTFS lets a file carry hidden
  side-channels, and shredding rewrites only the main one. We scanned 34,200
  files across every folder Elysium deletes from and found **none**, so this is
  a mechanism without an instance here - and realistically it would hold a
  "downloaded from the internet" tag, not conversation.
- **Files with names Windows cannot open stay put.** A name ending in a dot or
  space, or a reserved device name, cannot be opened the ordinary way, so it
  cannot be overwritten either; Elysium reports such a file rather than
  pretending it deleted it. Also scanned, also **no instances** - the browser
  names its cache files from hashes, which cannot produce one.

### Windows crash dumps

When a program crashes, Windows can write a memory dump to
`%LOCALAPPDATA%\CrashDumps`, a system-wide folder outside Elysium's reach. We
measured this both ways: an ordinary program does get a dump written, but
crashing Elysium's own browser window on purpose produced no dump at all, in
three different configurations. So the path we were worried about did not
reproduce.

What we cannot claim: we crashed the window one way. **A different kind of
failure - a graphics driver fault, an out-of-memory kill - might behave
differently, and we have not tested those.** If you want certainty rather than
a measurement, turn crash dumps off in Windows and delete that folder; it is not
inside `%LOCALAPPDATA%\Elysium`, so removing the app leaves it behind. On most
machines it was some other program that switched this on in the first place.

### The download itself

- **Elysium.exe is not code-signed.** Nothing cryptographic ties the file you
  downloaded to its author, so Windows shows a SmartScreen warning and Smart App
  Control may refuse it outright. Until that changes, the integrity of your copy
  rests on the transport you fetched it over. A signing certificate is a cost
  decision that has not been made yet, and saying so is better than letting the
  absence pass unmentioned.
- **A voice model you download is code.** Some engines load checkpoints in a
  format that can execute code as it is read. Elysium runs them in a separate
  process, but that process is yours: treat a model folder like an executable
  and take it only from a source you trust.

---

## If I delete Elysium, what is left?

Delete `%LOCALAPPDATA%\Elysium` and the encrypted database, any premigrate
snapshot beside it, the passphrase files, the voice models, your reference
clips, the browser profile and the log all go with it. The
application files are separate and hold no user data.

Five things that folder does not cover:

1. Crash dumps in `%LOCALAPPDATA%\CrashDumps` from before this version, or from
   a window crash. Delete that folder yourself if you want to be sure.
2. Whatever the Windows search index extracted before the no-index attribute was
   set. Rebuild the index from Indexing Options to clear it.
3. A Windows Credential Manager entry named `chatbot_interface`, if you upgraded
   from a very old version and never unlocked the vault afterwards. New installs
   never write there. **Not verified as impossible** - check Credential Manager
   if you are being thorough.
4. The permission change described above, if it ran. The command to undo it is
   there.
5. Anything you copied to the clipboard, if Clipboard History is on. Clear it
   from Win+V, or from Settings, System, Clipboard. If you also sync across
   devices, the copy on your Microsoft account is not on this machine at all
   and deleting the folder does nothing to it.

---

## Reporting something

This is a single-developer, local-first project. If you find a problem, open an
issue with what you did and what you expected. Do not include your passphrase,
your API key, or a log you have not read.
