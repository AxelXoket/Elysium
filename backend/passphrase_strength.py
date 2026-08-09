"""What makes a passphrase good enough to be the ONLY thing protecting a vault.

The threat here is not somebody typing guesses at a login box. There is no
login box and no rate limit: the database file, the salt and the KDF
parameters all sit in one folder, so an attacker who copies that folder guesses
offline, as fast as their hardware allows, forever. scrypt sets the price per
guess. The passphrase sets how many guesses are needed. Only the second one is
the user's to choose, and the floor was 8 characters.

What this deliberately does NOT do is impose composition rules - one capital,
one digit, one symbol. Those are the rules that produce "Password1!", and NIST
withdrew them for exactly that reason: they narrow the space people actually
choose from while looking like they widen it. Length and variety are what
survive an offline attack, so length and variety are what is checked.

Every rule here rejects a shape, never a specific secret, and nothing in this
module logs, stores or hashes what it was given.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

#: 8 was the old floor and it is not defensible against an offline attack: a
#: lowercase 8-character passphrase is about 2^37 candidates, which scrypt at
#: 0.24s per guess turns into weeks on one machine and hours on rented GPUs.
#: 12 is the shortest floor that still buys real time, and it is short enough
#: that a three-word phrase clears it without effort.
MIN_PASSPHRASE_LEN = 12
MAX_PASSPHRASE_LEN = 512

#: Below this the passphrase is one idea typed several times - "abababababab"
#: is twelve characters and two.
MIN_DISTINCT_CHARS = 5

#: The share of the passphrase one character may occupy. "aaaaaaaabcde" clears
#: every other rule here - twelve characters, five distinct, not a full run,
#: not a full repetition - and "a long run plus a short tail" is among the
#: first masks any cracking tool tries. Two thirds leaves ordinary phrases
#: alone: a space is the commonest repeat in real text and never approaches it.
MAX_SINGLE_CHAR_SHARE = 0.5

#: Long enough to pass the length floor AND common enough to be in any
#: wordlist an attacker starts with. Short common passwords are not listed:
#: the length floor already excludes them, and a list that pretends to be
#: exhaustive invites the belief that anything not on it is safe.
_COMMON = frozenset({
    "123456789012", "1234567890123", "12345678901234", "123456789012345",
    "qwertyuiopas", "qwertyuiop123", "qwerty12345678", "asdfghjklzxcv",
    "passwordpassword", "password1234", "password12345", "password123456",
    "iloveyou1234", "letmein123456", "welcome123456", "trustno1234567",
    "administrator", "adminadminadmin", "qazwsxedcrfv", "1qaz2wsx3edc",
    "abcdefghijkl", "abcd1234abcd", "zaq12wsxcde3", "loveyouforever",
    "thisismypassword", "mypasswordis123", "correcthorse",
    "correcthorsebatterystaple",
})

#: Walks, in both directions. The rows are joined as well as listed
#: separately, because a walk does not stop at the end of a row: the first
#: version of this checked each row alone and let "qwertyuiopasd" straight
#: through - eleven characters of one finger sliding left to right.
_ROWS = (
    "qwertyuiopasdfghjklzxcvbnm",
    "1234567890",
    "abcdefghijklmnopqrstuvwxyz",
)


def _normalise(passphrase: str) -> str:
    """Fold to a comparable form for the SHAPE checks only.

    NFKC plus casefold, so "PASSWORD1234" and "ｐａｓｓｗｏｒｄ１２３４" are recognised
    as the same shape. The key is always derived from the ORIGINAL bytes; this
    form never leaves this module.
    """
    return unicodedata.normalize("NFKC", passphrase).casefold()


def _is_a_run(text: str) -> bool:
    """Whether the whole thing is a straight walk along one keyboard row."""
    if len(text) < 4:
        return False
    for row in _ROWS:
        backwards = row[::-1]
        if text in row or text in backwards:
            return True
        # A repeated walk: "abcabcabcabc" is not more secret than "abc".
        # Up to the full length, not half of it. "0987654321098" repeats a
        # ten-character walk once and a bit; stopping at len//2 never tried a
        # piece that long and the whole thing passed as a real passphrase.
        for size in range(3, len(text)):
            piece = text[:size]
            if text == piece * (len(text) // size) + piece[:len(text) % size]:
                if piece in row or piece in backwards:
                    return True
    return False


def _is_one_thing_repeated(text: str) -> bool:
    """"abababab", "passpasspass" - long, and one idea."""
    for size in range(1, len(text) // 2 + 1):
        if len(text) % size:
            continue
        if text == text[:size] * (len(text) // size):
            return True
    return False


def _is_mostly_one_character(text: str) -> bool:
    """One character carrying most of the length, whatever the rest is.

    Not covered by the repetition or run checks: those ask whether the WHOLE
    string is one shape, and a short tail breaks both while adding almost
    nothing to what an attacker has to try.
    """
    if not text:
        return False
    commonest = Counter(text).most_common(1)[0][1]
    return commonest > len(text) * MAX_SINGLE_CHAR_SHARE


def assess(passphrase: str) -> str | None:
    """The reason to refuse this passphrase, or None to accept it.

    Returns a stable code rather than a sentence: the wording belongs to the
    frontend, and a code is what a test can assert on without being a copy of
    the message.
    """
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        return "passphrase_too_short"
    if len(passphrase) > MAX_PASSPHRASE_LEN:
        return "passphrase_too_long"

    folded = _normalise(passphrase)
    if folded in _COMMON:
        return "passphrase_too_common"
    # Whitespace-stripped too, so "password 1234" is not a way around the list.
    if re.sub(r"\s+", "", folded) in _COMMON:
        return "passphrase_too_common"

    if len(set(folded)) < MIN_DISTINCT_CHARS:
        return "passphrase_too_simple"
    if _is_one_thing_repeated(folded):
        return "passphrase_too_simple"
    if _is_a_run(folded):
        return "passphrase_too_simple"
    if _is_mostly_one_character(folded):
        return "passphrase_too_simple"
    return None
