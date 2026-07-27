"""tts/worker - the halves that run in an ENGINE's own interpreter.

Only `_wire` (stdlib-only, the protocol) may be imported by the app. The engine
scripts in here are never imported by the app process; they are spawned by path
in a different interpreter, because their dependency sets are incompatible with
each other and with the app.
"""
