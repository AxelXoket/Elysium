"""verify/verify_tts_install.py - the app's own installer, end to end, for real.

The suite proves the installer's COMMANDS: it replaces the subprocess and
asserts on argv. Nothing in it downloads a wheel, builds an environment, or
starts an engine, and on 8 September 2026 that is exactly where the defects
were: two freezes the resolver refused, a source archive whose wheel lacked
the modules the engine loads, a download host the allowlist did not know, and
a .pth line that died when the staging folder was renamed. Each of them passed
every test and failed the first real install. This script is the measurement
those tests cannot make.

What it does, through the app's own HTTP surface (in-process, no window):

  1. Points the app at a THROWAWAY data folder, keys a fresh vault there with
     the suite's fixed test key, and lists the model roots you give it as
     `extra_roots`. Your real vault is never opened: ELYSIUM_DATA_DIR is set
     before config is imported, and the folder you name is the only one it
     writes to.
  2. For each engine: POST /tts/runtimes/<engine>/install, watch the job to
     the end, check the runtime reads `ready`, find that engine's model in the
     scan, POST /tts/load, POST /tts/speak twice (a short sentence and one past
     250 characters, because one engine splits text at that mark), read the
     wav back, POST /tts/unload.
  3. Prints one table and writes the job logs, the scan and the wavs into
     <data folder>/verify-evidence/. Exit 0 only when every engine installed,
     loaded and spoke.

    .venv/Scripts/python.exe verify/verify_tts_install.py --data-dir D:\\elysium-verify `
        --root D:\\models --root "$env:USERPROFILE\\.cache\\huggingface\\hub" [fish_s2 ...] [--fresh] [--fresh-machine]

  --fresh          remove the folder's voice/envs, voice/bin and voice/python
                   first, so every engine is built from nothing (the package
                   cache is kept: it only speeds the download up)
  --fresh-machine  make the first install behave as on a machine without uv:
                   find_uv answers None, so the pinned uv is downloaded and
                   verified the way it would be there

Needs the network, an NVIDIA GPU with room for one engine at a time, and
about 14 GB of disk per engine. Downloads several gigabytes. Runs by hand and
before a release, never from a hook.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import wave
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

SENTENCE = ("The voice engine was built by the app itself, and this sentence "
            "is the proof that it can speak.")
LONG_SENTENCE = (
    "This is a deliberately long single sentence, written without a full stop "
    "anywhere inside it, so that it runs past the two hundred and fifty "
    "character mark that one of the engines uses as its own splitting "
    "threshold, and thereby shows whether a long sentence is spoken or refused "
    "by the engine today.")
ALL_ENGINES = ("fish_s2", "chatterbox", "xtts_v2")
POLL_S = 5


def _args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("engines", nargs="*", default=list(ALL_ENGINES))
    ap.add_argument("--data-dir", default=str(Path(tempfile.gettempdir()) / "elysium-verify-install"),
                    help="throwaway data folder; created if missing")
    ap.add_argument("--root", action="append", default=[],
                    help="a folder holding model folders (repeatable)")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--fresh-machine", action="store_true")
    return ap.parse_args(argv)


class _Log:
    def __init__(self, path: Path, total_phases: int):
        self.path = path
        self.total = total_phases
        self.done = 0
        self.t0 = time.time()

    def __call__(self, title: str, msg: str, *, advance: bool = False) -> None:
        if advance:
            self.done += 1
        pct = int(100 * self.done / self.total) if self.total else 0
        line = (f"{time.strftime('%H:%M:%S')} [{title}] {pct:3d}% "
                f"+{int(time.time() - self.t0):5d}s {msg}")
        print(line, flush=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def main(argv: list[str]) -> int:
    args = _args(argv)
    unknown = [e for e in args.engines if e not in ALL_ENGINES]
    if unknown:
        print(f"unknown engine(s): {', '.join(unknown)}; known: {', '.join(ALL_ENGINES)}")
        return 2
    data = Path(args.data_dir).resolve()
    data.mkdir(parents=True, exist_ok=True)
    evidence = data / "verify-evidence"
    evidence.mkdir(exist_ok=True)
    log = _Log(evidence / "status.log", total_phases=len(args.engines) * 6)
    log("verify", f"data folder {data} engines={args.engines} fresh={args.fresh} "
                  f"fresh_machine={args.fresh_machine}")
    if args.fresh:
        for sub in ("voice/envs", "voice/bin", "voice/python"):
            d = data / sub
            if d.exists():
                shutil.rmtree(d)
                log("verify", f"removed {d}")

    # Before config is imported, or the app would read its usual folder.
    os.environ["ELYSIUM_DATA_DIR"] = str(data)
    sys.path.insert(0, str(BACKEND))
    import launch_token
    os.environ.pop(launch_token.ENV_VAR, None)

    import config
    assert Path(config.DATA_DIR) == data, (config.DATA_DIR, data)
    import database
    import keyring_service
    import vault_state
    # Never this machine's Credential Manager, the same rule the suite keeps.
    keyring_service.read_legacy = lambda name: None
    keyring_service.delete_legacy = lambda name: True
    vault_state.set_key(bytes(range(32)))
    database.init_db()
    log("verify", "throwaway vault keyed, schema built")

    registry_path = Path(config.TTS_RUNTIMES_PATH)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if registry_path.is_file():
        try:
            existing = json.loads(registry_path.read_text(encoding="utf-8"))
        except ValueError:
            existing = {}
    existing["extra_roots"] = [str(Path(r).resolve()) for r in args.root]
    registry_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    log("verify", f"extra_roots = {existing['extra_roots']}")

    from tts import provision
    real_find_uv = provision.find_uv
    if args.fresh_machine:
        provision.find_uv = lambda: None

    import main as app_main
    from fastapi.testclient import TestClient

    rows: list[dict] = []
    failures: list[str] = []
    with TestClient(app_main.app) as c:
        api = "/api/v1/tts"
        for n, engine in enumerate(args.engines, 1):
            title = f"{engine} {n}/{len(args.engines)}"
            row: dict = {"engine": engine}
            rows.append(row)

            t_install = time.time()
            r = c.post(f"{api}/runtimes/{engine}/install")
            if r.status_code != 200:
                failures.append(f"{engine}: install refused {r.status_code} {r.text}")
                row["install"] = f"refused {r.status_code}"
                continue
            last = None
            while True:
                job = c.get(f"{api}/runtimes/{engine}/install").json()
                tail = job["log"][-1] if job["log"] else ""
                if (job["state"], tail) != last:
                    log(title, f"{job['state']}: {tail}")
                    last = (job["state"], tail)
                if not job["running"]:
                    break
                time.sleep(POLL_S)
            (evidence / f"{engine}.install.json").write_text(json.dumps(job, indent=2),
                                                             encoding="utf-8")
            row["install"] = f"{job['state']} {int(time.time() - t_install)}s"
            log(title, f"install {job['state']} in {int(time.time() - t_install)}s "
                       f"{job['error_code'] or ''} {job['error_detail'][:160]}", advance=True)
            if n == 1 and args.fresh_machine:
                provision.find_uv = real_find_uv
            if job["state"] != "done":
                failures.append(f"{engine}: install {job['state']} {job['error_code']} "
                                f"{job['error_detail']}")
                continue

            st = next(s for s in c.get(f"{api}/runtimes").json()["runtimes"]
                      if s["engine_id"] == engine)
            row["runtime"] = st["state"]
            log(title, f"runtime {st['state']}", advance=True)
            if st["state"] != "ready":
                failures.append(f"{engine}: runtime {st}")
                continue

            scan = c.get(f"{api}/models").json()
            (evidence / f"{engine}.scan.json").write_text(json.dumps(scan, indent=2),
                                                          encoding="utf-8")
            mine = [m for m in scan["models"] if m["engine_id"] == engine]
            log(title, f"scan: {len(mine)} model(s) for this engine, "
                       f"{len(scan['unrecognized'])} unrecognised, truncated={scan.get('truncated')}",
                advance=True)
            if not mine:
                failures.append(f"{engine}: no model under {scan['roots']}")
                row["model"] = "none found"
                continue
            model = mine[0]
            row["model"] = model["name"]
            issues = [i["code"] for i in (model.get("readiness") or {}).get("issues", [])]
            log(title, f"model {model['name']} variant={model['variant']} "
                       f"missing={model['missing']} issues={issues}")

            t_load = time.time()
            r = c.post(f"{api}/load", json={"uid": model["uid"]})
            row["load"] = f"{r.status_code} {int(time.time() - t_load)}s"
            log(title, f"load -> {r.status_code} in {int(time.time() - t_load)}s {r.text[:200]}",
                advance=True)
            if r.status_code != 200:
                failures.append(f"{engine}: load {r.status_code} {r.text}")
                continue

            for label, text in (("speak", SENTENCE), ("speak_long", LONG_SENTENCE)):
                t_speak = time.time()
                r = c.post(f"{api}/speak", json={"text": text, "uid": model["uid"]})
                took = int(time.time() - t_speak)
                if r.status_code != 200:
                    row[label] = f"{r.status_code}"
                    failures.append(f"{engine}: {label} {r.status_code} {r.text}")
                    log(title, f"{label} -> {r.status_code} in {took}s {r.text[:200]}")
                    continue
                body = r.json()
                audio = c.get(f"{api}/audio/{body['audio_id']}")
                wav_path = evidence / f"{engine}.{label}.wav"
                wav_path.write_bytes(audio.content)
                try:
                    with wave.open(str(wav_path), "rb") as w:
                        seconds = w.getnframes() / float(w.getframerate())
                except wave.Error as exc:
                    seconds = -1.0
                    failures.append(f"{engine}: {label} wav unreadable {exc}")
                row[label] = f"{seconds:.1f}s audio in {took}s"
                log(title, f"{label} -> {seconds:.2f}s of audio in {took}s "
                           f"({len(audio.content)} bytes)")
                if seconds < 1.0:
                    failures.append(f"{engine}: {label} audio too short ({seconds:.2f}s)")
            log(title, "spoken", advance=True)
            r = c.post(f"{api}/unload")
            log(title, f"unload -> {r.status_code}", advance=True)

    print()
    cols = ("engine", "install", "runtime", "model", "load", "speak", "speak_long")
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
    print()
    verdict = "PASS" if not failures else "FAIL"
    print(f"{verdict}: {len(failures)} failure(s); evidence in {evidence}")
    for f in failures:
        print("  " + f)
    (evidence / "verdict.json").write_text(
        json.dumps({"verdict": verdict, "failures": failures, "rows": rows}, indent=2),
        encoding="utf-8")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
