"""Every number the prompt modules feature is bounded by, in one place.

The packaged build does not ship the repo's shared/ folder, so the backend
cannot read these at runtime. They live here as constants and the gate suite
pins them against shared/prompt_module_limits.json and
shared/prompt_module_schema_v1.json, which the frontend reads. A drift between
the two sides is a red test, not a surprise.
"""

from __future__ import annotations

#: Byte caps, checked before a single body byte is read (Content-Length) and
#: again while streaming. 24,000 characters is at most ~96 KB of UTF-8 for the
#: text files; a SillyTavern preset carries many entries, so it gets 1 MiB.
TXT_MD_MAX_BYTES = 96 * 1024
JSON_MAX_BYTES = 1024 * 1024

#: One module's text. A .txt/.md/plain-json file over this is refused; a
#: SillyTavern entry over it is stored (nothing dropped) but cannot be enabled.
MODULE_MAX_CHARS = 24_000

#: A module name as the user edits it, and the plain JSON schema's name cap.
MODULE_NAME_MAX_CHARS = 64

#: The plain JSON `note` field: shown in the UI, never sent to the model.
MODULE_NOTE_MAX_CHARS = 500

#: Plain JSON: how many modules one file may carry.
PLAIN_JSON_MAX_MODULES = 100

#: What sits between two enabled modules, and between the last one and the
#: card data. There is no wrapper (H2 closed, Ek O 6).
MODULE_SEPARATOR = "\n\n"

#: H4: with at least one module on, modules plus separators may take at most
#: this percentage of (history budget minus every non-module fixed cost).
MODULE_BUDGET_SHARE_PERCENT = 50

#: H4 at toggle time with no chat open: the card is assumed to be this long.
REFERENCE_CARD_CHARS = 3_000

#: Reply length setting (A9): None means "adaptive", no sentence is sent.
REPLY_LENGTH_WORDS_ALLOWED = (150, 300)

#: Plain JSON format marker and the only version this build reads.
PLAIN_JSON_FORMAT = "elysium.prompt-modules"
PLAIN_JSON_VERSION = 1

#: Upload header that carries the file name (percent-encoded UTF-8, because
#: HTTP header values are latin-1 on the wire).
FILENAME_HEADER = "X-Prompt-Module-Filename"

#: The decoded file name, at most. 255 is the longest name Windows, macOS and
#: the usual Linux file systems allow, so a real file never hits it.
FILENAME_MAX_CHARS = 255
