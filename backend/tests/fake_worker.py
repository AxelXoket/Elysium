"""A stand-in engine worker: speaks the real protocol, needs no GPU.

This is what makes the worker lifecycle testable at all. It is spawned as a
REAL subprocess by the real client over real pipes, so the tests cover the
things that actually break in production - pipe deadlocks, encoding, timeouts,
orphan killing, crash-code mapping - without a 6 GB environment.

It imports the REAL `_wire`, so a protocol change that breaks workers breaks
these tests too. That is the point.

Behaviour is driven by the request, so one script covers every scenario:
    op=ping                      -> {"pong": true}
    op=load, mode=ok             -> {"loaded": true}
    op=load, mode=oom            -> raises a CUDA-looking OOM (exit 2)
    op=load, mode=slow, secs=N   -> sleeps, for timeout tests
    op=load, mode=hang           -> never answers, and stops reading stdin
    op=load, mode=crash          -> dies instantly, no frame
    op=load, mode=coded          -> a named failure the app can say out loud
    op=synthesize                -> writes a tiny wav, returns its path
    op=noise                     -> prints junk to stdout FIRST (see below)
"""
import os
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tts" / "worker"))
import _wire  # noqa: E402


def _tiny_wav(path: str, seconds: float = 0.1, rate: int = 44100) -> None:
    n = int(rate * seconds)
    data = b"\x00\x00" * n
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
           + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
           + b"data" + struct.pack("<I", len(data)))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(hdr + data)


def handle(op, req, send):
    # The control channel lives in the VALUES map (or a top-level "mode" for
    # direct protocol tests) - production code passes values through verbatim
    # and knows nothing about it.
    mode = req.get("mode") or (req.get("values") or {}).get("__fake_mode") or "ok"

    if op == _wire.OP_PING:
        return {"pong": True, "pid": os.getpid()}

    if op == _wire.OP_LOAD:
        if mode == "oom":
            raise _wire.oom("CUDA out of memory. Tried to allocate 2.00 GiB")
        if mode == "crash":
            os._exit(9)                       # no frame, no goodbye
        if mode == "hang":
            time.sleep(3600)                  # answers nothing, reads nothing
        if mode == "slow":
            time.sleep(float(req.get("secs") or 1.0))
        if mode == "coded":
            raise _wire.WorkerError(_wire.CODE_REFERENCE_INVALID, "clip unusable")
        if mode == "alien":
            # A code the app's vocabulary does not contain - the client must
            # coerce it rather than hand the frontend an unmappable string.
            raise _wire.WorkerError("engine_specific_gibberish", "boom")
        if mode == "noisy":
            # A library banner on the REAL stdout would desynchronise the
            # stream - unless claim_stdout() took it away first. print() here
            # goes to stderr, which is exactly what must happen.
            print("Downloading shards: 100%|##########| 4/4")
            sys.stderr.write("loud library warning\n")
        send(_wire.event("progress", stage="compiling", pct=0.5))
        return {"loaded": True, "vram_mb": 4096}

    if op == _wire.OP_SYNTHESIZE:
        out = req.get("out")
        if not out:
            raise _wire.WorkerError(_wire.CODE_SYNTHESIS_FAILED, "no output path")
        _tiny_wav(out)
        return {"path": out, "sample_rate": 44100,
                "text_len": len(req.get("text") or "")}

    # NO OP_TRANSCRIBE here, deliberately. It used to answer with a canned
    # sentence, which was the only implementation of that op anywhere: all
    # three real workers refuse it. So the transcribe test was green against a
    # feature that has never existed, and the always-enabled "Listen & fill in"
    # button it was supposed to cover produced a mistranslated 500 in the app.
    # A fake that can do more than every real engine is not a test double.

    raise _wire.WorkerError(_wire.CODE_WORKER_FAILED, f"unknown op {op}")


if __name__ == "__main__":
    channel = _wire.claim_stdout()
    CHANNEL = channel
    # argv is fixed by WorkerClient, so the noise switch rides the environment.
    if "--stdout-noise" in sys.argv or os.environ.get("ELYSIUM_FAKE_STDOUT_NOISE"):
        # Worst case: junk written to the protocol channel before we start.
        # The reader must skip it, not choke on it.
        channel.write("this is not json at all\n")
        channel.flush()
    sys.exit(_wire.serve(handle, channel=channel))
