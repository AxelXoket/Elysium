"""The policy has to allow the audio it now asks the app to fetch.

A spoken reply used to be an `<audio src>` pointed straight at
`/api/v1/tts/audio/{id}`. An element load cannot carry a custom header, which
is why the launch-token gate accepted a browser signal in the token's place on
that route - and a signal any local process can type is not a gate. The app
fetches the wav itself now, with the token, and plays it from a `blob:` URL.

That move is not free on the policy side. `media-src` was never declared, so it
fell back to `default-src 'self'`, and `'self'` does not cover `blob:`. Landing
the fetch without the directive would have traded a security hole for a mute
app: the bytes would arrive and the policy would refuse to play them.

These cases are the reason the two halves have to ship together.
"""
from __future__ import annotations

import pytest

#: Read off a live response rather than imported from main. A constant this
#: file also declared would agree with itself while the header said something
#: else, which is the failure the whole policy exists to prevent.
_HEADER = "content-security-policy"


@pytest.fixture()
def policy(client) -> str:
    return client.get("/healthz").headers[_HEADER]


def _directive(policy: str, name: str) -> str | None:
    """The one directive's value, or None if it was never declared.

    Split on the separator the header actually uses. Substring matching would
    call `media-src` present when the policy only said `img-src`, since one
    name ends with the other's tail.
    """
    for part in policy.split(";"):
        head, _, rest = part.strip().partition(" ")
        if head == name:
            return rest.strip()
    return None


class TestTheAudioBlobCanPlay:
    def test_media_src_is_declared_at_all(self, policy: str) -> None:
        # It was not, and default-src answered for it. That is the state this
        # case exists to keep the policy out of.
        assert _directive(policy, "media-src") is not None, (
            "media-src is undeclared, so the audio falls back to default-src")

    def test_media_src_allows_blob(self, policy: str) -> None:
        media = _directive(policy, "media-src")
        assert media is not None
        assert "blob:" in media, (
            "the spoken reply is played from a blob: URL and this policy "
            f"refuses it: media-src {media!r}")

    def test_media_src_still_allows_self(self, policy: str) -> None:
        # GROUND CONTROL. Widening to blob: must not have dropped the origin
        # the rest of the app is served from.
        media = _directive(policy, "media-src")
        assert media is not None
        assert "'self'" in media

    def test_media_src_opens_nothing_else(self, policy: str) -> None:
        # The containment argument for this whole policy is that a script
        # which somehow ran has nowhere to send data. An audio element is a
        # send channel like any other, so this directive gets exactly the two
        # sources it needs.
        assert _directive(policy, "media-src") == "'self' blob:"


class TestTheDirectiveReaderIsHonest:
    """Ground and positive controls for the parser above.

    An absence assertion is worth nothing if the thing doing the looking
    cannot see a presence, and worth less than nothing if it reports one that
    is not there.
    """

    def test_it_finds_a_directive_that_is_present(self) -> None:
        assert _directive("default-src 'self'; media-src 'self' blob:",
                          "media-src") == "'self' blob:"

    def test_it_reports_none_for_a_directive_that_is_absent(self) -> None:
        assert _directive("default-src 'self'; img-src 'self' blob:",
                          "media-src") is None

    def test_it_does_not_mistake_one_directive_for_another(self) -> None:
        # `img-src` and `media-src` share no prefix, but `script-src` and
        # `script-src-elem` do, and a substring reader would confuse them.
        assert _directive("script-src-elem 'self'", "script-src") is None

    def test_it_reads_the_policy_this_server_actually_sends(
        self, policy: str
    ) -> None:
        # POSITIVE CONTROL tying the parser to the real header: a directive
        # nobody disputes is present is found in the live policy, so a green
        # media-src result above is not the parser silently failing open.
        assert _directive(policy, "default-src") == "'self'"
        assert _directive(policy, "img-src") == "'self' blob:"
