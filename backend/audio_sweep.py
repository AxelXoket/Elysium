"""The spoken form of a deleted message goes with the row. K-45.

This lived inside routers/chats.py and was reachable from exactly the three
delete paths in that one file. Three OTHER paths delete messages - a character
being removed takes its chats with it, an edit sweeps everything after the
message it replaces, and an aborted turn deletes the row it had already
written - and none of them swept the audio. The promise held on half the
routes that could break it.

Two rules are carried over from the original, and both are load-bearing:

  * It runs AFTER the commit, never inside the transaction. The rows are the
    source of truth and have to land whether or not a file on disk lets go.
  * It swallows. Voice is optional and may not be installed at all; a missing
    engine is not a reason to fail a delete the database already committed.
"""

from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)


def forget_spoken_audio(message_ids: Iterable[int]) -> None:
    """Destroy the audio of these messages. Never raises.

    The import is deferred rather than done at module scope: on a machine
    with no voice engine, pulling the host in at import time would make every
    router that deletes anything pay for a subsystem it is not using.
    """
    wanted = [int(m) for m in message_ids if isinstance(m, int) and m > 0]
    if not wanted:
        return
    try:
        from tts.host import get_host
        get_host().forget_messages_audio(wanted)
    except Exception:                                     # noqa: BLE001
        logger.warning("could not sweep audio for %d deleted message(s)",
                       len(wanted), exc_info=True)
