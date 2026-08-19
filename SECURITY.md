# Elysium: what protects your data, and what does not

This document exists so you never have to take a claim on trust. Every
protection below says what it does, whether it is on by default, and what it
does **not** cover. The limits are here on purpose: a security page that only
lists wins is a marketing page.

Written against version 1.1.0.

---

## The short version

- Everything you write lives in **one encrypted file**. Without your passphrase
  it cannot be opened, not even as a plain database. Two things are written
  outside it: spoken replies, as plain audio wiped at every lock, and, only for
  a voice model that CLONES, the reference clip you record and a transcript of
  the words in it, which nothing purges.
- Your passphrase is **never stored**. Lose it and the data is gone. That is the
  design, not a bug.
- The app talks to **one address on the internet** (OpenRouter), and refuses
  every other destination at the connection layer. The one exception is the
  optional voice engine installer, which you start by hand.
- Elysium writes **nothing to the Windows registry**. Deleting one folder
  removes everything it ever created, with four caveats listed at the end.

---

## Where your data lives

Everything is under one folder:

```
%LOCALAPPDATA%\Elysium
```

| What | Encrypted? |
|---|---|
| `app.db` - messages, characters, personas, images, your notebook and limits, what the note reader has read and spent, your API key, proxy URL | **Yes**, AES-256 (SQLCipher), including the journal files |
| `salt.bin`, `verifier.bin`, `kdf.json` - passphrase machinery | No, and they are not secret. Knowing them does not reveal your key |
| `app.db.plain.bak-*` - a pre-vault copy kept after a one-time upgrade | **No, plaintext.** Settings > Secrets lists it and deletes it on request |
| `app.db.premigrate.bak` - a COMPLETE encrypted copy of the vault, taken before an uploads migration touches anything and kept whenever that migration does not finish cleanly | **Yes**, same cipher, and a passphrase change re-keys it so the old one stops opening it. But no screen reports it and no button removes it: only a later clean migration does |
| `app.db.premigrate.bak.unreadable-<ts>` - that same snapshot, moved aside because it did not open with this vault's key | Encrypted, under a passphrase this vault does not hold. It is moved rather than deleted because it may be the only copy of an older vault, and nothing removes it for you |
| `voice/models` - voice model weights you downloaded | No. Files you chose and put there; they hold none of your conversation |
| `voice/refs` - only for a voice model that CLONES: the reference clip you record and a transcript of the words in it | **No, plaintext.** Nothing purges these: not the lock, not shutdown, not the next launch. They go when you delete that voice, or the folder |
| `voice/cache` - generated speech, your conversation as audio | No. Cleared at every lock, every launch and every shutdown; anything older than 30 minutes is cleared as the next reply is spoken |
| `webview/` - the app window's browser profile | No. Cache, history and session files are wiped at every launch and exit; only cosmetic settings and your wallpaper are kept |
| `elysium.log` - application log | No. It carries no message text, no note text, no keys and no passphrases - but it does record that things happened: chat and note ids, counts, and the TYPE name of an error. A plaintext record of which chats have notes, sitting beside an otherwise opaque vault |
| `port` - the port the server last used | No. One number |

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
cancels whatever it was doing - including a request already in flight - and
empties its queue. Nothing is lost: the messages it had not finished reading
stay unread, and a later run picks them up.

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
hand has no token and the gate is open.

Two routes are exempt because a browser cannot attach a header to an image or an
audio element. They are narrowed rather than opened: the browser must send
`Sec-Fetch-Site: same-origin`, which a command-line tool does not.

### Where it connects

One provider host, enforced by refusing any other destination before the
connection opens. System proxy environment variables are ignored on purpose.
Every request forces `zdr: true`, `data_collection: deny`, `allow_fallbacks:
false`, and the app window **cannot** override those three.

**Two things send your conversation there, not one.** The reply you asked for,
and the notebook's note reader - a second model, which you choose, sent
excerpts of the same conversation automatically every twenty turns while you
are not watching. It goes to the same single host under the same locked
policy, and it is off entirely until you pick a model, but it is a second
sender and it spends your credits. What it did and what it cost is in the
Notes tab; the ceiling is sixty calls a day and it is a block, not a warning.

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

---

## Does Elysium change my Windows settings?

**It writes nothing to the registry.** The only registry access anywhere in the
code is a read, to detect whether the WebView2 runtime is installed.

| Change | Scope | Survives the app closing? |
|---|---|---|
| Excluding the process heap from crash dumps | This process only | No, re-applied at every launch |
| Marking the data folder "do not index" | A file attribute on that folder | Yes, until the folder is deleted |
| Hiding the window from screen capture | That window | No, and **off by default** (set `ELYSIUM_SCREEN_PRIVACY=1` to enable) |
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
- **A running unlocked app is unlocked.** Anything with your user account, or
  administrator rights, can reach a running process's memory. The vault protects
  the file at rest and the window once it locks.

### What is written outside the vault

- **UI preferences are not encrypted.** Text size, bubble solidity, the
  wallpaper, sampling numbers and the model you last picked live in the window's
  local storage. No chat content is there.
- **Voice is not encrypted.** Generated speech is written as ordinary `.wav`
  files while the vault is open. They are wiped on lock, on exit and on the next
  launch, but a hard kill can leave one until then.
- **The search index remembers.** Marking the folder not-indexed is a promise
  about the future. Anything Windows already extracted stays in its index.
- **Anything you copy leaves the vault, and Elysium cannot follow it.** The
  Copy button and Ctrl+C both put plain text on the Windows clipboard, which
  every program running as you can read. If you have turned on Clipboard
  History it is kept for Win+V; if you have also turned on syncing across
  devices, Windows uploads it to your Microsoft account. Both are off unless
  you switched them on. Elysium cannot exclude itself from either: Chromium
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
