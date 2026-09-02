"""verify_image_output.py - the one question no document can answer.

RUN THIS YOURSELF. It spends your OpenRouter credits and it uses the API key in
your vault. Nothing else in the app calls it and no test imports it.

    cd backend
    .venv\\Scripts\\python verify\\verify_image_output.py

Everything about generated image output is built and tested against a fake
provider. What cannot be faked is whether Elysium's privacy routing leaves any
provider ABLE to answer with a picture. The policy is hardcoded and immutable:

    zdr=true, data_collection="deny", allow_fallbacks=false,
    require_parameters=true

`require_parameters` narrows to providers that honour every parameter sent -
including `modalities` - and `allow_fallbacks=false` forbids a second attempt.
The intersection

    {returns images} n {zero data retention} n {denies collection}
                     n {honours every parameter}

may be EMPTY, in which case every request 503s and the feature is dead however
good the code is. That is question (a), and it gates the rest.

Question (b) is nearly as decisive: is the returned url a `data:` URL we may
decode, or an `https://` link to somebody else's server? A hosted link is
refused by design - this app has exactly one egress host - so a model that
returns links cannot be used for image output here, and no amount of code
changes that.

Nothing is written to the vault. Nothing is stored. The bytes are counted and
thrown away.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ISOLATION, and it has to happen HERE - before anything imports config, whose
# DATA_DIR is resolved exactly once at module import. Same reasoning, and the
# same trap, as verify/_harness.py documents at length.
#
# Why it is needed even though the key arrives by environment: building the
# HTTP client reads the PROXY secret, and that read opens the vault. So an
# "isolated" run that only skipped the API-key lookup would still open the
# real vault on its way to the network. Pointing the whole data dir at a
# fresh temp directory is the only version of isolation that is actually true.
_ISOLATED = bool(os.environ.get("ELYSIUM_IMAGE_API_KEY", "").strip())
if _ISOLATED and not os.environ.get("ELYSIUM_DATA_DIR", "").strip():
    import tempfile

    # A caller that already pointed ELYSIUM_DATA_DIR somewhere is honoured
    # rather than overwritten. That is not a convenience: a test importing
    # this module had no way to say where the isolation should land, so the
    # only choice was the system temp root - which is exactly the place the
    # filesystem guard now refuses, because it is where user content leaves
    # the vault unnoticed. Reading the variable first keeps the isolation
    # true for a real run and lets a test give it a directory it owns.
    os.environ["ELYSIUM_DATA_DIR"] = tempfile.mkdtemp(prefix="elysium_imgverify_")

GREEN, RED, YELLOW, OFF = "\033[92m", "\033[91m", "\033[93m", "\033[0m"

#: Change this to the model you actually want to use. Left as a placeholder on
#: purpose: guessing a model id for somebody and spending their credits on it is
#: not this script's decision to make.
MODEL = os.environ.get("ELYSIUM_IMAGE_MODEL", "")

PROMPT = "Draw a single small red circle on a white background."


def line(tag: str, text: str, detail: str = "") -> None:
    colour = {"PASS": GREEN, "FAIL": RED, "INFO": YELLOW}.get(tag, "")
    print(f"  [{colour}{tag}{OFF}] {text}" + (f"  ->  {detail}" if detail else ""))


async def main() -> int:
    if not MODEL:
        print(__doc__)
        line("INFO", "No model chosen.")
        print("\n  Pick one that lists image output, then either:")
        print("    set ELYSIUM_IMAGE_MODEL=vendor/model-name")
        print("  or edit MODEL at the top of this file.\n")
        print("  Models advertising image output can be listed with:")
        print("    .venv\\Scripts\\python -c \"import asyncio,openrouter;"
              "print([m['id'] for m in asyncio.run(openrouter.fetch_models())"
              "['models'] if 'image' in (m.get('output_modalities') or [])])\"")
        return 2

    import config
    import vault_state
    from openrouter import MODALITIES_WITH_IMAGE

    # A throwaway key handed in through the environment skips the vault
    # ENTIRELY: no unlock, no passphrase, no read of app.db, nothing opened
    # that could be real data. That is the supported way to let somebody else
    # run this - a dedicated key with a spending cap, revoked afterwards - and
    # it is why the branch exists at all.
    #
    # The value is never printed and never written anywhere. Only its presence
    # is reported.
    env_key = os.environ.get("ELYSIUM_IMAGE_API_KEY", "").strip()
    if env_key:
        # A random key for a database that does not exist yet and will hold
        # nothing. It is here only so the proxy lookup inside the HTTP client
        # build has somewhere to look that is not the real vault.
        vault_state.set_key(os.urandom(32))
        import database

        database.init_db()          # empty schema, so the proxy lookup finds nothing
        line("INFO", "using ELYSIUM_IMAGE_API_KEY",
             f"throwaway vault at {os.environ['ELYSIUM_DATA_DIR']}")
        try:
            return await _probe_model(config, MODALITIES_WITH_IMAGE, env_key)
        finally:
            import secure_delete

            secure_delete.shred_tree(os.environ["ELYSIUM_DATA_DIR"])
            shutil.rmtree(os.environ["ELYSIUM_DATA_DIR"], ignore_errors=True)

    if not vault_state.is_unlocked():
        # This used to say "unlock Elysium once, then re-run", which describes
        # something that cannot work: is_unlocked() reads THIS process's memory,
        # and the key only ever lives in the running server's process. A fresh
        # CLI run always starts locked, so the script could never reach the
        # network at all. Nobody noticed because nobody ran it.
        #
        # So it unlocks itself. getpass keeps the passphrase off the screen,
        # out of the shell history and out of argv - and this is a script whose
        # own docstring says RUN THIS YOURSELF, so a prompt is the right shape.
        import getpass
        from pathlib import Path

        import crypto

        try:
            passphrase = getpass.getpass("  vault passphrase: ")
        except (EOFError, KeyboardInterrupt):
            print()
            line("FAIL", "no passphrase given")
            return 1

        key = crypto.KeyVault(Path(config.DB_PATH).resolve().parent).unlock(
            passphrase)
        del passphrase
        if key is None:
            line("FAIL", "wrong passphrase", "nothing was sent")
            return 1
        vault_state.set_key(key)
        line("OK", "vault unlocked for this run")

    from secrets_service import get_secret

    key = get_secret(config.SECRET_API_KEY)
    if not key:
        line("FAIL", "no API key in the vault")
        return 1

    return await _probe_model(config, MODALITIES_WITH_IMAGE, key)


async def _probe_model(config, MODALITIES_WITH_IMAGE, key: str) -> int:
    """The two real requests. Reached with a key from EITHER source.

    Split out so the environment-key path can skip every line of vault
    handling above it rather than merely passing through it: an isolated run
    must not open, unlock, or even look at the real vault.
    """
    import json

    from network_client import get_client

    policy = dict(config.PROVIDER_POLICY)
    print(f"\n  model:  {MODEL}")
    print(f"  policy: {json.dumps(policy)}\n")

    client = get_client()
    headers = {"Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "provider": policy,
        "modalities": list(MODALITIES_WITH_IMAGE),
        "stream": False,
        "max_tokens": 512,
    }

    # ── (a) does the policy leave anybody able to answer? ─────────────────
    try:
        resp = await client.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers=headers, json=payload, timeout=120.0,
        )
    except Exception as exc:                                 # noqa: BLE001
        line("FAIL", "(a) request failed outright", type(exc).__name__)
        return 1

    if resp.status_code != 200:
        line("FAIL", f"(a) provider policy left nothing eligible: HTTP "
                     f"{resp.status_code}")
        # The body is the ONLY place this script prints upstream text, and it is
        # the whole point of running it: "no allowed provider" reads very
        # differently from "no credits".
        print(f"      {resp.text[:400]}")
        print(f"\n  {RED}STOP.{OFF} If this says no provider meets the routing "
              f"requirements, image output is not available under Elysium's "
              f"privacy policy for this model. Try another image model before "
              f"concluding it is impossible.\n")
        return 1
    line("PASS", "(a) a provider answered under the full privacy policy")

    body = resp.json()
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}

    # ── (b) data: or https://? ────────────────────────────────────────────
    entries = message.get("images")
    if not isinstance(entries, list) or not entries:
        line("FAIL", "(b) the reply carried no images array",
             f"keys={sorted(message)}")
        print(f"\n  {YELLOW}The request was accepted but no picture came back. "
              f"Either this model does not draw, or it chose not to. Text was: "
              f"{str(message.get('content'))[:120]!r}{OFF}\n")
        return 1
    line("PASS", f"(b) images array present with {len(entries)} entry(ies)")

    ok = True
    for i, entry in enumerate(entries):
        holder = entry.get("image_url") if isinstance(entry, dict) else None
        url = holder.get("url") if isinstance(holder, dict) else None
        has_type = isinstance(entry, dict) and "type" in entry
        if not isinstance(url, str):
            line("FAIL", f"(b{i}) entry has no usable url")
            ok = False
            continue
        if url.startswith("data:"):
            head, _, payload_b64 = url[5:].partition(",")
            line("PASS", f"(b{i}) inline data: url", f"header={head!r}")
            try:
                raw = base64.b64decode(payload_b64, validate=True)
            except Exception as exc:                          # noqa: BLE001
                line("FAIL", f"(d{i}) base64 did not decode", str(exc)[:60])
                ok = False
                continue
            line("INFO", f"(d{i}) decoded size", f"{len(raw)} bytes")
            try:
                from PIL import Image

                img = Image.open(io.BytesIO(raw))
                line("INFO", f"(d{i}) decoded image",
                     f"{img.format} {img.size[0]}x{img.size[1]}")
                if len(raw) > config.MAX_UPLOAD_BYTES:
                    line("FAIL", f"(d{i}) larger than MAX_UPLOAD_BYTES",
                         f"{len(raw)} > {config.MAX_UPLOAD_BYTES}")
                    ok = False
            except Exception as exc:                          # noqa: BLE001
                line("FAIL", f"(d{i}) bytes are not a readable image",
                     type(exc).__name__)
                ok = False
        else:
            line("FAIL", f"(b{i}) REMOTE url - this model cannot be used",
                 url.split(":", 1)[0] + ":...")
            print(f"      Elysium refuses to fetch a third-party asset. That is "
                  f"the privacy contract, not a missing feature.")
            ok = False
        line("INFO", f"(b{i}) type discriminator present", str(has_type))

    # ── (c) does the same thing arrive when streaming? ────────────────────
    print()
    stream_payload = {**payload, "stream": True}
    saw_delta_images = saw_message_images = 0
    frames = 0
    try:
        async with client.stream(
            "POST", f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers=headers, json=stream_payload, timeout=120.0,
        ) as sresp:
            if sresp.status_code != 200:
                line("FAIL", f"(c) streaming refused: HTTP {sresp.status_code}")
                return 1
            from openrouter import _aiter_sse_lines, image_urls_from

            async for raw_line in _aiter_sse_lines(sresp):
                raw_line = raw_line.strip()
                if not raw_line.startswith("data:"):
                    continue
                data = raw_line[5:].strip()
                if data == "[DONE]" or not data:
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                frames += 1
                ch = (chunk.get("choices") or [{}])[0]
                saw_delta_images += len(image_urls_from(ch.get("delta")))
                saw_message_images += len(image_urls_from(ch.get("message")))
    except Exception as exc:                                  # noqa: BLE001
        line("FAIL", "(c) streaming failed", type(exc).__name__)
        return 1

    line("INFO", "(c) frames parsed", str(frames))
    line("PASS" if saw_delta_images else "INFO",
         "(c) images on delta.images", str(saw_delta_images))
    line("PASS" if saw_message_images else "INFO",
         "(c) images on a final message", str(saw_message_images))
    if not (saw_delta_images or saw_message_images):
        line("FAIL", "(c) STREAMING RETURNED NO IMAGE",
             "the non-streaming path worked, so streaming needs another look")
        ok = False

    print()
    if ok:
        print(f"  {GREEN}Image output is usable under Elysium's privacy policy "
              f"with this model.{OFF}\n")
        return 0
    print(f"  {RED}Not usable as-is. See the failures above.{OFF}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
