# Changelog

Unreleased work sits under the newest "Since" heading. When the version moves,
that heading becomes the release heading and a fresh "Since" block opens above
it. README links here rather than carrying its own copy.

## Since 1.1.6

The next release is **v1.2.0**. Everything below is in the working tree and in
no exe you can download yet.

### Added

- **Reading, scrolling, hovering and typing keep the vault open.** The idle lock
  only ever saw HTTP requests, so twenty minutes spent reading a long reply
  without pressing Send locked the vault on the fifth minute. The window now
  watches real input: a click, a key, a wheel, a touch, focus arriving, and the
  pointer moving or hovering over the focused, visible window at least ten
  pixels from the last point that counted. It tells the backend through
  `POST /activity`, at most once every thirty seconds and only after such an
  event. Programmatic scrolling does not count, nothing counts while the vault
  is locked, and the ping carries nothing: a 204 with no body, recorded by the
  same gate every request passes, so there is still one definition of activity.
- **The auto-lock hint says the default out loud.** It read "off by default"
  for a lock that has been on at five minutes since August; it now says on,
  with the number the server itself reports, and lists what counts as being
  here.

- **An empty name box gets a name instead of an error.** Chats, characters,
  personas, limits and voices all answered the empty box differently and every
  answer was wrong: a chat was stamped with the character and the UTC minute,
  so three chats opened in the same minute were identical; characters and
  personas refused with a validation error; the proxy label was deleted and the
  save reported success; a voice stored its own opaque id as its label. One
  rule now covers all five, on the server side of each door: `<name> <n>`,
  where n is the smallest free number, so a deleted "New Chat 2" is available
  again.
- **The notebook panel says what went wrong.** Extraction failures, suggestions
  that were thrown away and runs that placed nothing are on screen, and notes
  written in the background arrive without reopening the panel.
- **Turkish examples in the extractor**, and the prompt is at version 2.
- **The composer's meter says when the message will be refused**, rather than
  counting up to a limit you only meet on send.
- **The keyboard can follow the app, and so can a screen reader once the
  accessibility tree is open.** Who said what was carried only by which side of
  the column a bubble sat on and what colour it was, so the thread read back as
  one undifferentiated run of text. Now: each message is announced as a unit
  with its speaker, an arriving reply is read out as it lands instead of in
  silence, the chat action menu takes arrow keys, the focus ring is no longer
  clipped away by an open section, the delete confirmation's description is
  tied to the button it describes, and a toast countdown pauses while the
  dismiss button has focus rather than expiring under the one person who
  cannot hover. The keyboard half needs nothing switched on. The spoken half
  does, and saying otherwise would be the more comfortable sentence rather
  than the true one: the accessibility tree defaults to CLOSED, for the reason
  v1.1.5 records below, so none of this reaches a screen reader until
  `setx ELYSIUM_ACCESSIBILITY_PRIVACY 0` is set and Elysium is restarted. The
  work is here so that the switch is the only thing between this app and a
  screen reader, rather than the switch and an unlabelled interface behind
  it.

### Changed

- **One motion language, and a watchman that can see it.** Durations and
  easings lived in three places, fifteen hand-written `prefers-reduced-motion`
  blocks between them missed twenty-five rules including every Base UI dialog,
  and the popup cap overrode every component with a blanket 90ms `!important`.
  There is now one token set with `--duration-exit` and `--ease-exit` in it,
  popups open at `--duration-fast` and close at `--duration-exit`, and a single
  universal reduced-motion block covers everything, with only the slowed
  loading spinners and the 120ms opacity fades still moving under it, by name.
  `lib/theme/tokens.ts` carries the curves and durations as numbers, the seven
  motion call sites read them, and tests hold the token file equal to the
  stylesheet in both directions and the reduced-motion set difference at zero.
- **Panels open and close instead of blinking.** Every conditional block in the
  notebook panels now arrives and leaves through `Collapse`, `FadeIn` or an
  `AnimatePresence` crossfade, and a closed section is out of the
  accessibility tree rather than merely invisible. The chat action menu joins
  the reduced-transparency list it had been missing, and the blur guard reads
  the compiled sheet through a parser so a name behind a comma is no longer
  skipped.

- **Provider failover is on, inside the privacy filters.** Every completion
  request asked OpenRouter for `allow_fallbacks: false`, so when the one
  provider it picked was unavailable, the send failed with it, even for a
  model that other zero-retention providers serve; a server error from that
  provider reached the window as "The provider returned an error". The policy
  now says `true`, on the chat routes and the notebook's reader alike, and
  OpenRouter may use a backup instead. It relaxes neither privacy filter:
  `zdr: true` and `data_collection: deny` still decide which providers are
  candidates, and a backup comes only from them. Model fallback is a separate
  `models` parameter that no request carries, so failover does not swap the
  model. One thing is new: when the first provider fails after receiving the
  prompt, a second zero-retention provider may receive it too, and that
  provider may price the call differently. The window
  still cannot set any of the three.
- **The quote under a note is cut at 240 characters.** It was stored at
  whatever length arrived. The schema asked the model for 240 and a model that
  ignored the request was obeyed, so a note could carry an unbounded stretch of
  the conversation into the vault and into every later prompt.
- **Old spoken audio is cleared on the voice engine's health tick**, not only
  when the next sentence is spoken. A session that stopped talking used to
  leave everything it had said in the cache until the next launch.
- **The notebook's circuit breaker is per chat as well as global.** One chat
  whose content the extractor could not parse walked the single process-wide
  breaker to "stopped" and took every other conversation down with it. A
  failure a chat caused now trips that chat; a rate limit or a dead key still
  trips everything.
- **How often the reader runs and how much it reads move together.** Under
  repeated failures the batch halved and the trigger threshold did not, so the
  same backlog was bought in more pieces and the fixed part of every prompt was
  paid again in each of them.
- **The nearby-conversation window has its own budget** instead of living on
  whatever the rest of the prompt left over.
- **Three pieces of the extractor's prompt have a ceiling, and each is
  settable.** The character card had none at all, so a long card could ship
  sixty thousand characters to the small model, twenty-four times what the chat
  itself is ever allowed to send. The card is capped at 4,000 characters, the
  existing notes at 6,000 and the recent turns at 12,000, each overridable by
  environment variable.
- **The proxy label is capped at 50 characters.** It had no ceiling anywhere:
  a 200,000 character label was accepted by both save paths and read back
  whole.
- **The stop list on a completion is bounded.** Every entry is walked on the
  event loop from six routes, so an oversized list did not merely cost the
  request that sent it, it stalled every reply already streaming. Measured
  through the route with five million entries: 0.139s without control against
  0.016s for the same request carrying no list.

### Security

- **Activity is a looser word than it was, and this is a relaxation.** Until
  now activity meant a request you sent. It now includes the mouse moving or
  hovering over the focused window. A hand resting on the mouse while the
  window is focused, or software that moves the cursor over it by more than ten
  pixels, keeps the vault open; no web signal separates that from a person, and
  the only one that would, the operating system's injected-input flag, needs a
  global input hook this app will not install. Movement over another
  application's window never reaches this one, and nothing counts while the
  window is unfocused. A `focus()` call from script also produces the trusted
  focus event that counts; every such call in the app today runs on a user
  action or when the request that action started settles, at which instant the
  clock was already reset by that request. There is no server-side limit on the
  ping this turn; the client's thirty-second gap is the only limit.

- **The safeword is cut out of the whole outgoing body**, not just the message
  being typed. A message you type carrying the safeword is still refused whole
  and nothing is sent. Everything else in the body is a different situation
  with the same word in it: a line stored last week, a note, a character card,
  a stop sequence. Refusing those would turn a word written once into a
  permanent stop on every later conversation, so the word is cut and the rest
  goes. The cut runs in both payload builders after the body is complete and
  before the socket, which is the only place all three senders meet. Protocol
  values are scanned and left alone, because a three character safeword lands
  inside a model id or a schema by accident, and attached images are skipped
  for the same reason measured: `kes` occurs 51 times per megabyte of base64
  by chance, so treating one photograph as prose would destroy it silently.
- **Setup no longer goes out bare when a proxy is mandatory.** The installer
  has two ways onto the network and they disagreed: its own download refused
  without a proxy, while the package fetch removed every proxy variable and
  put none back, which is the most direct instruction there is to go out
  without one. That was the multi-gigabyte path. Both now pass through one
  gate that is allowed to refuse.
- **The browser profile sweep does not stop at a redirected name.** A link
  planted inside the profile folder sent the cache somewhere the sweep never
  looked. The link is now removed and what it pointed at is left alone and
  named in the log; crash dumps this app is responsible for are shredded even
  behind one; anything that could not be overwritten is left in place and
  reported rather than unlinked with the bytes still readable.
- **Speaking a stored message stays inside the chat that asked.** Message ids
  are one global counter, so a replay request carrying an id and nothing else
  addressed the whole vault: any row at all could be read aloud, in its raw
  form, from a request that never said which conversation it belonged to.
- **A validation failure no longer echoes the request back.** A 422 returned
  the body it had just refused, and its code differed by route.
- **The launch token is the only way in.** Routes that had been exempt because
  an element loads them are no longer exempt, and `media-src` is declared.
- **Rewriting a voice transcript or label destroys the old text.** Both are
  your own words, and both were replaced with a plain truncating write: the old
  blocks went back to the filesystem with the old words still in them, so a
  correction made precisely because the first version should not be kept left
  both versions recoverable. They now go through the same shred the vault uses,
  write-new before destroy-old, and a transcript is capped at 20,000
  characters. There had been no ceiling: five million characters went into a
  voice folder in one call.

### Fixed

- **The proxy kill-switch is not written down while the server's answer is not
  in hand.** While the settings fetch was still out, or after it had failed, the
  "Require proxy" switch sat live at off, and a save that only changed the URL
  sent that off as a decision: a proxy that was required became optional, with
  the line above the switch saying "unknown" at the same moment. The switch, the
  Save button and that line now read one derived value, so all three close until
  the answer is back, no request carrying the requirement leaves before then,
  and a switch the user moved keeps its value through a failed refetch and
  reopens with it.
- **The proxy name box shows the stored name.** It used to open empty with the
  name printed on a read-only line above it, so an empty box meant two things at
  once and clearing what looked like nothing reset the name to the default. The
  box now opens with the name, emptying it is a choice made in view of it that
  asks for the default back, and a save that only changes the URL keeps the
  name.

- **Voice engine setup can finish, for every engine, on a machine that has
  nothing.** Several things stood in the way, each measured on 8 September
  2026.
  Pressing Set up for Fish or Chatterbox could never complete: each pinned
  list is a freeze of an environment that was measured working, and handed to
  the resolver as a request it was refused, because a package's own metadata
  contradicts the versions it was measured beside (chatterbox-tts asks for
  torch 2.6.0 where the measured set runs 2.8.0; a Fish dependency asks for
  protobuf below 3.20 where 5.29 is what works). A short overrides file
  beside each of those two freezes now tells the resolver to believe the
  freeze there, and everything else is still resolved and checked. Fish itself
  was pinned as a source archive to be built into a wheel, and that wheel
  would have carried none of the modules the engine loads, so the install
  would have passed and the first load would have failed; the installer now
  fetches that archive itself, checks the extracted tree against a content
  digest, and puts the tree on the engine's path the way the measured
  environment had it. On a machine without the package manager, the download
  of the pinned one was refused by the app's own host list, because GitHub
  now sends release downloads through a host the list did not have; the list
  has it. And a package manager older than the pinned one is no longer used,
  the pinned one is fetched instead, which retires a retry path that could
  have taken packages from a different index than the one they were measured
  from. The few pins that ship as source archives are built with a pinned
  setuptools and wheel rather than whatever the index offers that day. The
  readiness proof after an install now imports, module for module,
  what each engine's worker imports, so a wheel that lacks them cannot be
  called ready. The suite holds every freeze line to an exact pin and every
  override to a line of its freeze, and a hand-run check
  (`backend/verify/verify_tts_resolve.py`) dry-runs all three freezes against
  the index so a stale pin is found before you find it. This is also the way
  back for a voice set up by hand before 1.1.5: the trust check refuses an
  interpreter Elysium did not install, and Set up again now installs one.
- **An engine that failed its trust check offers "Set up again".** The row
  already said the engine on this machine was not the one Elysium installed
  and told you to press Set up again; the button under it read "Set up".
- **A Chatterbox checkpoint is judged by the files the engine actually
  opens.** The settings page accepted the multilingual build's .pt weights for
  the english build and named s3gen.pt as missing whatever the build, while the
  worker demanded the .safetensors names plus tokenizer.json, so an english
  folder read as complete and then failed at load as "incomplete". Both sides
  now read one table per build, and a folder holding a build the bundled
  engine has no loader for (Chatterbox Turbo, Multilingual v3) is reported as
  exactly that, without a redownload button for files that are all there.
- **A Fish S2 checkpoint is judged by the files the engine actually
  opens.** A folder with `config.json` and `codec.pth` but no text2semantic
  weights, or shard 1 of 2, read as complete, because a `*.pth` pattern was
  satisfied by the codec. The verdict now follows the engine's own loader: the
  shards named in `model.safetensors.index.json` must exist and missing ones
  are listed by name, else `model.safetensors`; `codec.pth` never counts as a
  weight. `tokenizer.json` and `tokenizer_config.json` are required, because
  the engine loads without them and crashes on the first sentence. The worker
  refuses an incomplete folder with the same names before importing torch, so
  the app's "download did not finish" and the worker agree.
- **The voice model scan finds a model wherever you put it, and says when it
  could not look.** Pointing `extra_roots` at the model folder itself found
  nothing, because the walk only ever looked at children; `~`, `%LOCALAPPDATA%`
  and relative paths were taken literally, and a relative one resolved against
  wherever the app happened to be started from; the same folder listed twice,
  or once inside a wider root, appeared twice; one directory budget was shared
  by every root, so a large first root left the second unscanned; a root that
  did not exist or sat on a removed drive was skipped in silence; and a
  HuggingFace cache showed its `trees` folders and `.no_exist` markers as
  "not recognised" rows. Now the root itself is identified first (the app's
  own drop folder excepted, which stays a container), paths are expanded and
  anchored to the data folder (a relative root that a 1.1.6 exe read against
  its own folder now reads against the data folder, and has to be respelled
  as an absolute path), two roots that nest are both walked, each with
  its own budget, and the wider walk steps around the inner one, so a model is
  listed once, under the nearest root above it, and carries that root's id.
  Adding a root above an existing one changes nothing under it; adding a root
  inside one moves the folders under the new root onto its id, and when the
  new root is itself the model, the id it had under the wider root is
  remembered, so a saved selection and its saved settings follow it to the
  new id on the first read, whether by the chat, the settings page or a
  speak; the old settings row is left in place. One real folder reached
  through a junction and through its own name is one row, under the nearest
  configured root, whichever root's walk reaches it first. A model behind a
  junction that leaves its root (`models\hf` pointing at `D:\hf`) is
  identified by the path the scan reached it by, so two such folders of one
  name are two rows, and the id it had in 1.1.6, by its bare folder name, is
  remembered: a selection saved then follows it to the new id on the first
  read, and two folders that shared that id resolve to the first row, the one
  1.1.6 loaded. A junction that is itself a root inside a wider root
  remembers the id it had under that root as well. A root that holds files no
  engine claims is a "not recognised" row only when nothing under it is a
  model or a row of its own, by any walk, so adding a root never makes the
  folder above it appear as a row. The manual engine override reaches a model
  behind a junction: the folder is confined as the scan spelled it and the
  link check on the sidecar's own name is the guard. The cap is per
  root, the walk goes one level deeper so `HF_HOME` and the ModelScope cache
  are reached, none of `blobs`, `refs`, `trees`, `.no_exist`, `.locks`, `xet`
  or `.cache` is listed or entered, and the voice settings page says in two
  sentences when a walk was cut, naming the folder, and which folders it
  could not open.
- **XTTS speaks a long sentence instead of failing it.** With "Split long
  text" on, the engine's own splitter asked for spacy past 250 characters,
  spacy is not part of the engine's environment, and any single sentence of
  that length came back as a synthesis failure. Measured on a 299-character
  sentence through the real installer and model. The worker now cuts the
  text itself, at sentence ends first, then clause marks, then spaces, hands
  the engine one piece at a time with its own splitter off, and joins the
  audio.
- **Two unnamed voice uploads no longer overwrite each other.** The panel fell
  back to one fixed label for both, so the second upload replaced the first
  clip and its hand-corrected words.

- **A vault lock could never return.** The stand-down waited on the notebook
  worker's task even when that task belonged to a different event loop, and the
  wait was resolved from the other loop's thread on a future this one would
  never be woken for. Measured: the worker task reached CANCELLED and the wait
  had not returned seventy-five seconds later.
- **The Sweep button and the background reader could buy the same range
  twice**, and either could mark the other's live, already billed call failed
  and move the cursor past a range nothing had read.
- **A daily call reserved before midnight was refunded out of the next day's
  allowance**, so the ceiling let one extra billed call through per stale
  reservation, and the cost landed on a day whose call count was zero.
- **Handing a call back on a locked vault raised instead of logging.** The
  two blocks that clean up after an abandoned turn, the refund of the day's
  call slot and the removal of the `running` row the turn left behind, catch
  the vault's lock error by a name that was imported only inside the worker's
  `run()`. An `except` clause is evaluated once something is caught, so on
  the one path both blocks exist for, the vault locking, the lookup raised
  `NameError` mid-unwind, the `except Exception` beneath it never ran, and
  the refund's failure ate the row removal. Measured on that path: one
  database attempt inside the undo, two once the import moved to module
  level. Each block is now driven through a locked vault and through an
  ordinary failure, with a working database as the ground control.
- **A worker's own words could reach the log.** The voice engine's progress
  frames were written to `elysium.log` verbatim, and those carry the sentence
  being spoken, a Windows path and therefore the account name, and the name of
  a reference clip, which for an older voice is the label typed on screen.
- **A crashed rotation's empty leftover pinned superseded key material** beside
  the vault on every unlock, permanently.
- **Re-initialising a vault could destroy the only key to a copy it kept.** The
  superseded salt and mirror were shredded whenever no rotation backup was
  left, without asking whether a premigrate or orphaned encrypted copy was
  still on disk.
- **The schema version stamp had not moved** for three objects, so an older
  build would have opened and edited a database it did not understand.
- **A call that reached the provider counts as billed.** A timestamp column
  cannot tell a connect timeout from a read timeout, and one of the two was
  paid for. The fact is now recorded where the answer arrives, and an
  unrecognised error is treated as billed, because guessing the other way
  refunds calls that really happened.
- **Switching tabs in the right panel no longer destroys what you typed.**
- **A network drop no longer condemns a long chat to grinding forever.**
- **A note placed against a chat that no longer exists does not undo the call
  that was paid for**, and no longer overwrites the recorded cost.
- **The abandoned branch of a run is no longer something the breaker counts.**
- **The idle lock stopped holding up every reply in flight.** Its watchdog
  asked whether to lock straight from the event loop, and that question opens
  the database, which queues behind any held write. The feature whose whole job
  is to be invisible until it fires was stalling every stream in the process.
- **A half-downloaded voice model is detected** instead of being treated as
  installed.
- **The engine sidecar is written under a name nobody can predict.** The old
  one was a fixed temporary path opened with a call that follows links, so
  planting a link at that name let the next engine selection truncate the
  target.
- **"This chat has been read" can tell its two histories apart.** The flag was
  written unconditionally and backfilled from any extraction row at all, so a
  chat that had never been read looked identical to one whose extraction rows
  were later deleted, and clearing it sent a long chat back to its oldest
  message twenty at a time.
- **Three attachment races were guarded by a comment and nothing else.** Two of
  the three could have their protection deleted with all 105 touching tests
  still green.
- **Sentences telling you to reload are gone**, because the packaged window has
  no reload.
- **Checks that could not fail were rewritten.** The panel contrast checks read
  one colour at a time and never against the surface it sits on, so a readable
  number could be printed about text nobody could read; the draft checks trusted
  a stand-in for locking instead of locking the vault; the check that says the
  API key is not on disk in the clear had never been shown finding a key it was
  looking at. Each now carries the case that proves it can go red.

## v1.1.6

Mostly repair, but not only: the side panels can be collapsed for the first
time, the notebook's "Try it on this chat" is gone, and the voice settings are
reorganised. Nothing you already have is migrated or rewritten, so upgrading is
replacing the exe and nothing else.

### Added

- **The side panels collapse.** A handle at each edge of the conversation hides
  the chat list or the settings panel and brings it back, and each side is
  remembered separately across a restart. The handle is an arrow with no box
  around it, drawn quietly at all times rather than appearing on hover, and it
  takes the weight it needs once its panel is shut, at which point it is the
  only way back in. The conversation does not re-flow while a panel is moving:
  the panel travels first and the message column follows, so the text is never
  re-wrapped mid-animation and the place you were reading is held
- **Voice parameters are sorted by what the engine can actually do**, three
  tiers driven by the engine's own reported status, with nothing hidden that
  you had already set. An engine that reports itself untrusted now says so

### Removed

- **"Try it on this chat" is gone.** The notebook's dry-run, the button that
  ran the extractor once and showed the result without storing anything, is
  removed in full: the route, the panel, the schema, the query keys, two error
  codes, the contract rows and two documentation claims. A test now holds the
  route closed rather than trusting that nobody re-adds it
- **The licence badge.** It advertised a licence this repository does not have.
  There has never been a LICENSE file, and GitHub reports none. The badge stays
  off until that decision is made

### Security

- **The voice parameter field is closed to autofill.** A text field with a DOM
  id is a valid autofill key to Chromium, so what you typed there could land in
  the browser engine's own Web Data file, outside the vault
- **The packaged build now checks where each bundled binary came from.** Forty
  Windows C-runtime DLLs were being collected from a folder that happened to be
  on `PATH` rather than from Windows itself. Anything sourced from outside the
  project, its virtual environment, the Python installation or Windows now
  stops the build by name

### Fixed

- **PyInstaller's own GPL modules are no longer frozen into the shipped exe.**
  23 modules of `PyInstaller.compat`, `.utils.hooks`, `.building` and `.depend`
  were being embedded in the binary, which is outside the scope of the
  bootloader exception; that exception names only `./bootloader/` and
  `./PyInstaller/loader`, both of which still ship and are still covered. Both
  spec files add the package to `excludes` along with the two hook packages
  that dragged it in, and filter those hooks back out of the data files as
  well. The already published v1.1.5 binary has this property
- **Three more dependencies that no runtime path can reach left with it.**
  `pygments` (336 modules, reached only from httpx's command line half, which
  cannot load here because `rich` is not installed), `setuptools` (133) and
  `pefile`. That took the exe from 33,642,105 to 29,130,534 bytes, a drop of
  13.4 per cent, with the headless boot check still green on the frontend, the
  SQLCipher native library and the voice payload
- **Locking the vault no longer destroys what you were typing.** Locking
  unmounts the app's subtree, and drafts lived inside components that went with
  it, so a lock threw away every unsent sentence and every open edit while
  merely switching chats kept them. Drafts now live outside the tree that gets
  unmounted. Nothing is written to disk; the buffers are capped at 2 MiB each
  and 64 MiB in total, and a write over the ceiling is refused with the reason
  rather than quietly dropping an older draft
- **A fast model's reply is typed out instead of appearing all at once.** The
  smoothing layer guessed that a jump of more than two hundred characters meant
  a chat switch, but at the START of a stream nothing has been shown yet, so
  that condition was always true and any model whose first chunk exceeded two
  hundred characters had its whole reply painted in one frame. The cliff was
  measured at exactly 201 characters. It is told which conversation it is
  smoothing now instead of guessing from size, and the promise that the text
  never falls more than 1500ms behind is enforced rather than declared
- **Editing a message no longer opens what looked like a separate window.** The
  box was always inside the bubble; only its styling made it read as its own
  panel. It painted a fixed dark surface over whatever contrast preset, custom
  ink, bubble opacity or surface finish you had chosen, and it took its size
  from a fixed 14px rather than from your own font setting. It now wears the
  bubble, matches the text it replaced exactly, and shows a border only while
  focused. Its height follows the font and line-height sliders too
- **The delete confirmation opens under the button that opened it.** The
  controls sit at the top right of every message, but on the model's messages
  the panel was anchored to the opposite corner, so the question about
  something irreversible appeared as far from the pointer as the row allowed
- **The right panel is readable.** An invisible heading, an unreadable selected
  chip, two undefined tokens, a meter warning nobody could see and a delete
  button for the API key that could not be read. The panel's main ink moved
  from 14.15:1 to 8.39:1, twelve press states were added, and five transitions
  that jumped instantly now do not
- **`anyio` is declared as a dependency instead of arriving by accident.**
  Thirteen shipped runtime modules import `anyio.to_thread` directly, while the
  lock file only carried it as something httpx and starlette happened to pull
  in. The resolved version does not move
- **The README's Node requirement says what it actually is.** It read "20+";
  the truth is `^20.19.0 || ^22.13.0 || >=24`, the intersection of what the
  tools declare. 21.x is refused by all of them, and 23.x is accepted by the
  bundler but refused by the linter and the test DOM. The same range is
  declared in `package.json` and a test now binds the two together
- **vite moved to 8.2.2**, closing two Windows advisories

## v1.1.5

### Added

- **A notebook, one per chat**, a place for what the story has established, sent with every message so the model stops forgetting. You write notes yourself, or let a small model read the last turns and suggest them. Notes never disappear: one that supersedes another retires it rather than deleting it, and one that does not fit the ceiling stays in the panel saying why it was not sent this turn
- **Limits that outrank the story**, a separate list of things you never want written, kept apart from the notebook because it has a different life: limits never expire, never merge, and are never trimmed away to make room. `everywhere` or `this chat`, and any chat can be told to ignore your global set and use only its own. If the limits themselves cannot fit the model's context, the app **refuses to send** rather than quietly dropping them
- **The model can propose notes for you**, off until you choose a model, because it is your API key. The list only offers models that keep no copy of what they read and can return a strict schema; `:free` variants are excluded, since those sit behind a training consent this app cannot read. It runs in the background every twenty turns and never blocks a message
- **"Try it on this chat"**, one button that runs the extractor once and shows the result **beside the text it read**, storing no notes. It exists because one thing could not be settled by argument: whether a small, cheap model reads Turkish well enough to be trusted with it. It is removed again in v1.1.6
- **A hard daily ceiling on what the notebook may spend**, sixty calls a day, counted in the database so a restart does not reset it, and enforced as a **block before the request** rather than a warning after it. Repeated failures pause the reader; enough of them stop it until you press "Try again now"
- **It tells you what it did**, runs, calls and credits spent today, the same two figures summed over its whole life sitting beside them, and why anything was skipped, in words. The lifetime figure is informational only; spend today alone still governs the daily cap. A refused run and a quiet week are otherwise the same screen
- **Saved, and takeable back**, when the model writes a note without asking, the panel says so once and offers Undo. With automatic acceptance on there is no review step, so this is the only moment you are told
- **The Turkish original, under the English note**, notes are written in English on purpose: a small cheap model reads and writes it far better. The cost is a paraphrase you cannot check, so the sentence as it was actually said is kept and shown underneath
- **A note says when it came from the model's own words**, each note records whose sentence its quote was lifted from, and one lifted out of the model's own reply says so on its face. This is the one thing no checker can supply: the app already verifies that a note matches its quote, and that check says nothing whatever when the model is quoting itself. So it is a label, not a filter, and the note still goes in. Marking was chosen over a review queue on measurement rather than taste: at the rate this happens such a queue would be almost entirely correct notes, and the research on queues that cry wolf is that people stop reading them and then miss the real one
- **Hide the window from screen capture**, Settings › Security. Screenshots, screen recording and screen sharing see a blank window instead of your conversation. Off by default, stored in the vault rather than the browser, and deliberately **not** applied while the vault is locked, because there is nothing on that screen to protect. A layer, not a guarantee
- **The conversation is closed to the accessibility tree, and this switch defaults ON.** An unprivileged probe with no launch token and no elevation, just a program running as the same user, read the chat title, the character name and the message bodies out of WebView2's accessibility tree, verbatim. That tree is the page handed to Windows so screen readers can speak it, and hiding the window from screen capture is no defence against it: capture exclusion hides PIXELS, and this is text. Elysium now starts its browser with the tree switched off. Every other protection here waits to be asked for and this one does not, deliberately: the screen-capture switch has a visible cost that lands on every screenshot taken of this app, while this one costs assistive technology, which most people do not run, and what it prevents is any program on the machine reading a whole private conversation without asking anyone for permission. The cost is stated rather than buried: while it is on, a screen reader cannot read Elysium at all. The way out is `setx ELYSIUM_ACCESSIBILITY_PRIVACY 0` and a restart, and an environment variable is the requirement rather than the lazy option, because somebody who needs a screen reader cannot navigate to a checkbox inside an app their screen reader cannot read, and the switch has to be usable before the vault is unlocked, which rules out keeping it in the vault. It takes effect at STARTUP ONLY, because the flag is an argument to the browser process and is read once as that process is created; changing it while Elysium runs does nothing, unlike the screen-capture switch, which is a call on a window that already exists and can follow every lock. Only exactly `0` disables it, so `false`, `no`, `off` and an empty value leave it on rather than letting a typo quietly undo a privacy control, and the launch log names the state that took effect
- **The Models tab says whether your key is set.** The catalogue is public, so the list loaded and every card answered a click before a key existed. The panel now says which state it is in, above the list: a quiet confirmation once a key is stored, and otherwise a line saying where the list came from and that nothing in it can be used yet. It claims neither until the server has answered, so a key that is already stored never raises a false alarm about your own setup
- **Check the key you already stored.** Settings › Security can ask OpenRouter whether the stored key is still accepted, without you retyping it. A key that is revoked, expired or out of credit stops working long after it was saved, and until now the only ways to find out were to send a message and read the failure, or to retype the key into the save box, which is a save and not a test. "Rejected" and "could not reach OpenRouter" stay two different answers, because they are opposite instructions
- **The right panel gained a Notes tab** and the Secrets tab is now **Security**, with `Secrets & Security` as its heading, because it holds the switches that protect the secrets as well as the secrets
- **"Start over instead", a way out when the passphrase is gone.** There is no recovery, so the only honest answer to a lost passphrase is starting over. A quiet link on the lock screen, below the passphrase field and never beside Unlock, explains first what goes: chats, characters, personas, notes, uploads, generated images, saved voice, the saved API key, all of it, and that it cannot be undone, and that Elysium then opens on the same setup screen first run used, before it asks for the phrase itself. The button stays off until what you typed matches exactly, and submitting checks again so Enter cannot slip past a disabled button. The door only opens while the vault is LOCKED: one that can be unlocked does not need it, so an open vault refuses outright before the phrase is even read
- **A cut-off reply now says so.** A reply can end before it reaches a natural stop: the provider hit its own token ceiling, the connection closed without ever saying how it ended, or the reader disconnected mid-stream and the words that had already arrived were kept anyway. Every message now carries whether that happened to it, so a reply trimmed short reads as trimmed short instead of as one that simply stopped talking
- **Frame the wallpaper yourself.** The picture used to be centred and cropped for you, with no say in which part survived. Settings › Chat background now has a preview drawn in the shape of your chat area right now: drag the picture to choose what shows, or zoom in to crop closer. The framing is stored as a position on the picture rather than as a fixed rectangle, so it holds when you resize the window instead of needing to be set again
- **See through the message bubbles.** A solidity dial in Settings › Text & readability lets the wallpaper come through behind what is being said. Only the bubble thins; the words stay fully solid at every setting
- **Find a model by what it can do.** The Models tab has a capability row under the search box: pick one or more of what a model must READ and what it must MAKE, and the list narrows to those that do all of it. Each option carries its own count, options nobody offers are not shown at all, and a "most capable first" switch pulls the versatile models to the top. The capability badges on each card also say which way they go, so a model that looks at pictures no longer looks identical to one that draws them
- **Replies can include pictures.** Models that can draw may answer with an image as well as words. Off until you turn it on, in Generation settings › Pictures in replies. The picture is decoded here and stored in your encrypted vault exactly like an image you attached yourself: re-encoded so nothing rides along in its metadata, size and pixel capped, and served only to this app. A link to somebody else's server is refused rather than followed, because the app still talks to exactly one host. The picture is shown to you but never sent back to the model on later turns, so it costs nothing after the reply that made it
- **Copy a message with one click.** What lands on the clipboard is the message as it was written, asterisks and all, rather than the tidied version on screen. The icon turns to a tick to confirm it; if the clipboard could not be reached it says so and keeps saying so instead of fading away. A reply that is still only a picture, or one whose words have not started arriving, offers nothing to copy rather than quietly emptying your clipboard
- **Select any text in the app.** The window would not let you highlight a single word, so a conversation could be read but never quoted. You can now drag across a reply and copy it the usual way. Timestamps, the variant counter and the blinking cursor stay out of what you pick up, so what you paste is only the words

### Changed

- `response_format` is sent by exactly two call sites, both of them the notebook's own extractor: the background reader and the "Try it on this chat" preview, one feature asking the same question with the same fixed schema defined in this repository. No request carrying a conversation to a chat model carries it, and nothing the frontend sends can add it. The Privacy Contract said "never sent" and now says what is true
- The `done` frame of a completion reports how much of the notebook went and how many older messages were trimmed to fit
- Deleting or editing a message rolls back how far the note reader had got, so the rewritten text is read again rather than skipped forever

### Fixed

- **The one-address promise is enforced against the machine, not just against the code.** The list of hosts this app may connect to used to be DERIVED from the address it was configured to use, so the gate always agreed with wherever a request was already going. One environment variable, writable by any program running as you, no admin, surviving a restart, moved the destination and the permission together, and the API key went with them. The list is pinned now; a different address needs a second, differently named switch (`ELYSIUM_ALLOW_BASE_URL_OVERRIDE=1`) before the gate will honour it
- **Plain http to the right host is refused too.** `http://openrouter.ai/...` named the correct host, passed the host check, and put the `Authorization` header on the wire in the clear. Anything that is not loopback must be https
- **Your own machine is no longer a permitted destination for your API key.** `127.0.0.1`, `localhost` and `::1` were on the egress allowlist unconditionally, for two reasons that were measured on 20 August 2026 and were both wrong: a proxy you configure on loopback never reaches that check, and the launcher's health probe does not go through it. What the entries did do was make any program that can open a listening socket a permitted destination. They now take the same `ELYSIUM_ALLOW_BASE_URL_OVERRIDE=1` opt-in as any other address. The local mock provider still works; the documented recipe now sets that flag
- **A refused outbound request says WHY in the log.** With `OPENROUTER_BASE_URL` pointed somewhere other than the shipped provider and the opt-in absent, every network surface failed with a different generic message and two of them told you to go and check a proxy that was working. The refusal now names the variable that caused it, once, where the fact is known. Host names only; no paths and no headers
- **The browser profile is swept when the server starts, not only when the window closes.** Running the backend by hand (`start_backend.bat`) never went through the launcher, so on that path nothing had ever cleared the WebView2 disk cache, and that cache holds whole API responses: chat titles, character cards, personas, as readable JSON. Measured on one machine: 21 MB, six weeks old
- **The WebView2 environment variables are replaced rather than added to, on both branches.** All nine of them, not the one this app sets. They can hand the browser engine drawing your conversation a debugging port, a redirected profile folder that the cache sweep would never find, or a different browser binary entirely, and any program running as you can set them with one `setx` and no admin. Elysium used to append its own switch and pass everything else along, and if you had turned the accessibility switch OFF it did not touch them at all, which made that the one configuration where a planted flag sailed through untouched. Discarded values are logged by name and never by value
- **The check that says "the accessibility tree is closed" stops reporting success during the attack it exists to notice.** It asked only whether OUR switch was present. Because the loader appends, a planted `--remote-debugging-port` arrives alongside ours rather than instead of it, so the check found our flag, said yes, and wrote "verified closed" to the log while the DevTools protocol was listening on that same process
- **The accessibility log names the failure that happened.** It said "the browser did not get the flag" even when the browser HAD the flag and was also carrying a debugging port, which is the case the check was written for. The port is now named, by switch name and never by value
- **The WebView2 policy check actually runs.** It was written and tested and wired to nothing, so the one control that would have noticed a policy key handing the browser a different binary could only ever fire under the test suite, while this document told you the app reports it
- **A same-user program can no longer read the vault key out of Elysium's memory.** On Windows any program running as you could open another of your processes and read it, with no administrator rights, and one did exactly that to this app during an earlier review. Elysium now takes that right away from every program including itself. It runs after the window has loaded rather than at launch, because the browser engine will not start under it, so the launch token stays readable for the few seconds before that; the vault key does not exist until you type a passphrase, which is later still. It does not stop an administrator, does not stop a program that already had a handle open, and does not touch the browser process that draws the conversation, which SECURITY.md names as the ceiling on all of it
- **The vault's identity backups can no longer be committed.** `.gitignore` covered `salt.bin` and `verifier.bin` but not the timestamped backups the app actually writes beside them, and not `kdf.json` at all. Together those files are an offline guessing oracle against your passphrase. Nothing had ever been committed; the rule now matches what the code writes
- **Deleting one sixteen-byte file no longer destroys the vault permanently.** `salt.bin` had no second copy anywhere: delete it, or flip a single byte in it, and the correct passphrase opened nothing ever again, while the app reported the vault as "not set up" and then refused to set it up. `vault.recovery` beside it now carries the salt and its cost settings, never the verifier, so a passphrase change still revokes the old key, and the app repairs itself from it. Your data folder is otherwise untouched: no permissions are changed, and it copies, moves and deletes like any other folder
- **A failed vault reset could brick the vault, and a partial one reported success.** "Forgot your passphrase?" destroys the database and then the `salt.bin`, `verifier.bin` and `kdf.json` beside it. But the database shred can fail for entirely ordinary Windows reasons, a hardlink, an antivirus or sync client holding a byte range, a read-only attribute, and destroying the recipe for the key while the file it opens is still on disk left a vault that neither the original passphrase nor a fresh setup could ever open again. That is strictly worse than the locked but intact vault you started with. Those three files are now held back entirely unless `app.db` is confirmed gone, while every other family still runs, because none of the rest is the recipe for anything. When something does survive, the screen says so: the route answers `{"ok": false, "left": [...]}` and the lock screen lists exactly what is still on disk rather than announcing a wipe that did not finish. The sweep also reaches further: `elysium.log`, the single rotated copy the log keeps, and the `port` file now go too. The log never carried message text, but it did carry chat and note ids, so leaving it behind kept a plaintext record of which chats had notes after the vault holding them had been destroyed
- **The vault reset route does not exist outside the installed app.** It answers 404 unless this is the packaged build and the launch token gate is armed. Previously a development checkout could be wiped by one local request carrying the confirmation phrase, with no passphrase and nothing else in the way
- **A directory listing spelled out what you had called your voices.** A cloning reference lived in `voice/refs/<the voice id>`, and that id is the label you typed, slugged. So listing that folder was a readable roster of who had been cloned: no passphrase, nothing to unlock, and it outlived every lock. The clip and its transcript being plaintext files is a disclosed trade, made so that your own recording does not become unreachable outside Elysium; reading the roster from the outside was never part of that trade. The folder is now named by a hash of the id under a random key generated once for that folder. One-way rather than a lookup table: the names give nothing back, and somebody who guesses a label and hashes it needs that key file too. Existing installs are renamed on first touch and the rename is resumable, because the id is written into `voice.json` and flushed to disk before the rename is attempted, so a crash between the two is a state the next run continues from rather than a voice nobody can find again. What this does NOT change, and it is the larger half: the clip, its transcript and the label inside `voice.json` are still plaintext files outside the vault that nothing purges. Moving them in is decided and is not done
- **A voice folder's salt can no longer be lost in silence.** A crash while first creating it left an empty file, and every later run then named folders with an unsalted hash, the exact guessing attack the salt exists to stop, with nothing on screen to say so
- **The model you had picked was sitting in browser storage in the clear.** An OpenRouter model id reads as `author/slug`, which is a name a person reads on screen rather than a bare number, and that is the shape that must not live outside the vault. It was in the desktop app's localStorage blob, readable with no passphrase and surviving every lock, relaunch and shutdown, because the browser-profile purge deliberately spares Local Storage. It now lives in the encrypted settings table and is read back after unlock. An install that already has the plaintext copy is cleaned rather than merely frozen: leaving the key out of what gets written next would have left the old value parked in that blob for good, so the store's migration deletes it outright. The last-open chat and character ids stay local, because those are numbers and not names
- **Two files beside the vault were in no document.** An uploads migration that does not finish cleanly keeps `app.db.premigrate.bak`, a complete encrypted copy of everything, and moves one it cannot open to `app.db.premigrate.bak.unreadable-<timestamp>`. A passphrase change re-keys them. They are named in SECURITY.md's file table and in the README's limitations. The first now has its own screen and its own button: `GET /vault/status` reports it as `premigrate_backup` and whether it opens under the key you hold, and Security can shred it on your word, refusing while locked, because the file is encrypted and only the key can tell a redundant copy from one written under a passphrase this vault no longer holds. The unreadable, moved-aside copy has neither yet; a full vault reset sweeps it, nothing smaller does, and that half stays written down as debt
- **The documents no longer imply that everything but the wallpaper is encrypted.** Two things are not, and neither was said plainly. Spoken replies are written as plain audio under the data folder, wiped at every lock, launch and shutdown, and trimmed at thirty minutes, and a voice model that CLONES keeps the reference clip you recorded and a transcript of it as ordinary files that nothing purges. README, SECURITY.md and the two screens that make the promise now say so, and the clone sentence is conditional so somebody whose model cannot clone is not told they have files they do not have
- **Two settings routes stopped silently deleting what they were meant to save.** Both had no required field, so absence meant "clear it", and a request with a misspelt field name arrived as an empty one, wiped the stored value and answered with success. The published contract even spelled one of the field names wrong. Unknown fields are refused now
- **The log gate can see a shape of logging call it had been blind to**, which is how eight of one module's ten log calls were reporting clean
- **A second copy of Elysium can no longer open against the same vault.** Double clicking the exe again used to open a second window on the same data folder. Locking is per process, so the two windows each held their own copy of the vault key: locking one cleared that copy and left the other with the database fully decryptable, and the idle auto-lock had the same blind spot. A second launch now raises the window already open and stops before it touches the folder. Two different data folders still get a window each
- **The delete confirmation on a persona is readable again.** It was painted from the dark shell's danger colour on the app's one light panel and measured 3.29:1, worse than the button that opens it
- **An error toast no longer cuts off halfway through the fix.** The message was clipped to one line, and because every sentence in the catalogue is written problem first, the half that vanished was always what to DO about it. The text now wraps, and a longer message is given longer to be read rather than the same few seconds
- **The notebook could pay twice for the same messages.** An extraction that was interrupted, the window closed or the vault locked, left a record that neither counted as finished nor let the reader move on, so the identical stretch of conversation was sent to the provider and billed again on the next run, and again after that. The call is now recorded as having happened and the reader moves past it. Closing the app with a reader mid-request was the ordinary way to trigger this, not an exotic one
- **A reply you had already paid for could be thrown away by locking the vault.** The bill for a notebook call is settled the moment the provider's answer arrives; everything after that is only writing the result down. Locking the vault in that gap cancelled the write along with the request, so the money was gone and nothing was recorded: no notes, no failure, no cost. The tail of an already billed call now runs as its own shielded piece of work, and the lock waits for it, up to five seconds, before the key leaves memory. Either the notes land or the failure is recorded with the cost attached to it. Five seconds is headroom for one small database write on a slow disk rather than a promise that every write finishes: when it runs out the lock goes ahead and says so in the log, because a key that refuses to leave memory is the worse of the two bugs
- **A reply from an abandoned attempt could overwrite the retry that replaced it.** A stretch of conversation is identified by which messages it covers, and that name is deliberately the same every time it is planned, so an attempt that was interrupted and the retry that reclaimed the same stretch afterwards were writing to one row with nothing to tell them apart. The straggler arrived last and won, stamping a stale answer over work that had genuinely completed, and the retry's own freshly billed reply was then discarded as a duplicate: two calls paid for and the wrong one kept. Every attempt now carries a token minted for that attempt alone, and a final outcome whose token is not the one the row is holding is refused instead of applied. Claiming a stale row for a NEW attempt is still allowed, because that is the retry mechanism working. What the loser still does is record its cost, since it was billed either way
- **A note could describe a sentence you had taken back.** Editing a message correctly told the reader to read that stretch again, but a reply already in flight, built from the old wording, arrived afterwards and overwrote that instruction. The notes were written from text that no longer existed, and the corrected wording was then never read by any later run. The reply is now discarded when the question it answered has been withdrawn, the cost is still reported, and the stretch stays queued for a real re-read
- **A withdrawn edit could reach the panel as a raw code instead of a sentence.** The reason recorded when a reply is thrown away because its question was edited or deleted mid-flight, `plan_invalidated`, is written from a different place than every other skip reason: `commit_extraction`'s own rollback check, not the worker's. The closed vocabulary that guarantees a skip reason always has prose behind it did not cover that writer, so the token could reach the panel untranslated. Both writers now check the same declared list before anything is written
- **Two skip reasons named a cause the app had never established.** When a reply lands and finds the stretch it was reading gone, the panel said either "you edited or deleted a message" or "the chat was cleared". The backend does not know which of those happened: it asks one question, whether the LAST message of that stretch is still there. Probed against the real routes, a deleted message, a cleared chat, an aborted send being cleaned up and an edit whose swept tail reached the end of the range all arrive at the same answer, and only rewriting the last message of the range arrives at the other. So "or deleted" named the wrong reason for every delete there is, and "the chat was cleared" named one of the four ways into the other. Both sentences are now written from the thing that was actually observed, and both say what you most need to know: the call was paid for either way, and what happens to that stretch next. A reason the panel has no sentence for at all now reads as a sentence with the code in brackets instead of a bare snake_case token
- **The lifetime total showed a count of calls and never their cost.** The panel put today's spend beside a lifetime figure, but that figure was calls only; the money itself was summed in the database and rendered nowhere. The line now reads both the lifetime calls and the lifetime cost, in the same "N calls · N credits" words today's figure already uses, dimmed and kept behind the word "lifetime" so today's figure, the one that governs the daily cap, stays the one your eye lands on first
- **What the notebook spent is a permanent record, and now says so.** The per-day ledger of calls, tokens and cost is what makes the daily ceiling survive a restart, so nothing prunes it and deleting every chat you have leaves it intact
- **Rewriting an older question now asks first, and says how much goes.** Editing a message throws away everything said after it, permanently, and the only warning was a tooltip that spoke of "the reply" even when a dozen were about to go. Saving an edit that would discard more than one message now asks, and gives the number, counted the way the server counts it, so a reply you browsed away from is included. Editing the last question, where only its own reply is rewritten, still saves straight away
- **A reply that arrived in full no longer reports itself as failed.** After a reply had finished and been saved, a late message from the connection closing behind it could still put a failure notice on screen. On a normal send it did more than that: the message was taken back off the screen and your own sentence was returned to the composer, inviting you to send, and pay for, it a second time. Nothing had gone wrong in any of these cases; the words were on screen and in your vault
- **A question with no answer can be asked again.** Deleting a reply left its question standing at the end of the chat with no way to get another one: the regenerate arrow lives on the reply that was just deleted, and Send needs new text, so an empty composer looked like a dead button. The only route through was to press Edit and then Save without changing a word. That message now carries its own button
- **A chat deleted mid-reply no longer reports a crash.** If you deleted a conversation while a reply was still being written, the reply tried to save itself into it and the app reported an internal error. It now says the chat is gone, and saves nothing
- **A character's greeting keeps every word again.** If the card's opening line began with something in square brackets, like `[she smiles]`, that part was quietly deleted from the greeting shown to you in any vault where voice had ever been switched on. It was being mistaken for a delivery cue meant for the voice engine. Cards are written by people, not by the model, so their text is now left exactly as written
- **A reply can no longer lose a piece of itself in transit.** Certain uncommon characters a model may write, the Unicode line and paragraph separators, were being treated as the end of a network frame by the library underneath, which cut that piece of the reply in half and discarded both halves. The missing text vanished from the screen, from your vault and from the spoken reply at once, with nothing to say it had happened
- **Messages no longer reconnect from scratch every time.** The connection to OpenRouter was being thrown away after five seconds of quiet, so any message sent after a normal pause paid for a fresh network handshake before a single word could be generated: measured at around 190 milliseconds, more than every other delay on the send path put together. It is now kept for two minutes, deliberately under the limit the far end imposes so we let go of it first rather than reaching for one it has already closed
- **Sending is quicker to get going.** With continuous voice on, the whole speech engine was being brought up before the request even left, one step at a time on the same thread that was supposed to be delivering your reply, and it held up every other reply in flight with it. Reading a conversation back did the same thing at the end of every exchange, while audio was still being sent. Both now happen out of the way
- **Faster first word.** The voice engine was shuffling its model in and out of the graphics card before nearly every sentence, to make room it already had, and then parking the audio decoder before every one as well. Both were reserving space for the longest reply the settings allow rather than for the sentence actually being spoken. Measured end to end, the fixed cost per sentence went from 2.3 seconds to under 0.6, and speech now starts inside three seconds on the very first reply of a session instead of the third
- **The first sentence no longer waits for the second.** A reply is synthesised a piece ahead of what you are hearing, so playback never runs dry. That read-ahead was being paid for before anything was handed over: the opening piece finished, and then sat in memory until the piece behind it was finished too. It is the one delay you experience in full, because nothing is playing yet to cover it. Measured end to end on a real reply, from pressing speak to the first sound: 10.5 seconds, now 5.6
- **Speech starts sooner on a long reply.** A reply is spoken sentence by sentence, and the first piece is cut short on purpose so it can start early. Nothing was keeping the NEXT piece small, so a short opener could be followed by one three times its length, and playback then sat waiting for it rather than starting, which threw away the head start it had just been given. The chunks are now sized against what the reply has already banked
- **Spoken replies no longer stop short.** A reply could go quiet a few words before the end, with no error and nothing to explain it. The last sentence was being dropped while it was still being made
- **Delivery holds up over a long reply.** Two separate things were flattening it. A repeated direction was being treated as one continuing instruction and dropped from the sentence that repeated it, though nothing was continuing it: every sentence is synthesised separately, so the dropped tag simply left that sentence in the plain voice. And the per-reply ceiling was eight directions while a normal reply is around thirteen sentences, so everything after the eighth was stripped, always at the end, so replies started performed and finished flat. Narration was also spending the tag density dial, which is a budget for the model's own cues. The direction the engine is given now follows the voice engine's own published guidance on where a direction belongs and how many a sentence can carry
- **Narration no longer flattens the speech next to it.** With narration voice set to *Narrator*, dialogue that shared a sentence with a `*narrated action*` was performed in the narrator's flat tone too. Narration is told; the speech after it now returns to the character's voice, including your standing tone if you have set one
- **The narration voice setting means one thing.** It was stored twice, once on this device and once in your vault, and the two were only written together when you touched the dropdown. A choice made before that, or a save that failed once, left replies performed one way as they arrived and another way when the speak button repeated them. Your current choice is moved into the vault on first launch; nothing to do
- **Four tests that could not fail** were found and rewritten, including one guarding the note-eviction order, the rule that stops the notebook freezing on its first two dozen notes. The old assertion carried an escape clause that was always true, so the regression it existed to catch would have passed straight through it
