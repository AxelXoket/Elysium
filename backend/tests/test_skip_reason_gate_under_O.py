"""U-28 - the skip-reason gate had a command-line switch on it.

Three writers share one declared vocabulary of skip reasons, and all three
refused an undeclared one with `assert`. `python -O` deletes assertions, so
under that flag all three would write the undeclared reason silently: the
status route returns it verbatim and the panel, which translates reasons
from a private map, would show the reader raw snake_case.

LATENT, not live. Neither spec file carries `optimize=` and `PYTHONOPTIMIZE`
appears nowhere in the repo, so nothing SHIPS with assertions stripped. The
refusal is now a real exception, which holds however the process was
started - including one somebody starts by hand.

MEASURED IN A SUBPROCESS, because there is no other honest way: `-O` is
decided when the interpreter starts and nothing inside a running test can
turn it on.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def optimized(body: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run `body` in a fresh interpreter with assertions stripped.

    `ELYSIUM_DATA_DIR` is handed over deliberately, and it is not decoration.
    `fs_guard` protects this suite by monkeypatching ONE interpreter; a child
    process sees none of it. Without the variable, `config._resolve_data_dir`
    falls back to the backend folder itself, so `DB_PATH` would point at the
    developer's own `app.db` - the vault they open Elysium with.

    Nothing here reaches it today: `get_db` asks `get_key()` first and that
    raises `VaultLockedError` in a fresh interpreter, one line before
    `connect`. But that is an ordering accident, not a guarantee, and the
    accident is invisible - no test would go red if the order changed. The
    child is pointed at an empty temporary directory instead, which is the
    remedy `fs_guard`'s own docstring prescribes for spawned interpreters.
    """
    env = dict(os.environ, PYTHONOPTIMIZE="1", PYTHONPATH=str(BACKEND),
               ELYSIUM_DATA_DIR=str(tmp_path))
    return subprocess.run(
        [sys.executable, "-O", "-c", textwrap.dedent(body).strip()],
        cwd=str(BACKEND), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)


class TestTheHarnessReallyStripsAssertions:
    def test_an_assertion_does_not_fire_in_that_interpreter(
            self, tmp_path) -> None:
        """GROUND CONTROL for every test in this file.

        If `-O` were not taking effect, the old `assert`-based gate would
        still refuse and each measurement below would pass while measuring
        nothing at all. This is the guard against a green run that means
        nothing.
        """
        result = optimized(tmp_path=tmp_path, body='''
            assert False, "this must not fire"
            print("STRIPPED")
        ''')

        assert result.returncode == 0, result.stderr
        assert "STRIPPED" in result.stdout, (
            "assertions still ran under -O; nothing here proves anything")

    def test_the_child_is_pointed_away_from_the_real_vault(
            self, tmp_path) -> None:
        """The OTHER thing that has to be true before anything below runs.

        `fs_guard` monkeypatches ONE interpreter and a child sees none of it,
        so the only protection a spawned process has is where its
        `ELYSIUM_DATA_DIR` points. Left unset, `config` falls back to the
        backend folder and `DB_PATH` becomes the developer's own `app.db` -
        the vault they open Elysium with. Nothing below reaches it today,
        because `get_db` asks for the key one line before it connects and
        that raises in a fresh interpreter - but that is an ordering
        accident, and an accident nothing would notice losing.
        """
        result = optimized(tmp_path=tmp_path, body="""
            import config
            print("DATA_DIR:", config.DATA_DIR)
            print("DB_PATH:", config.DB_PATH)
        """)

        assert str(tmp_path) in result.stdout, result.stdout
        assert "chatbot_interface" not in result.stdout.replace(
            str(tmp_path), ""), (
            "the child resolved a path inside the repository: "
            + result.stdout)


class TestEveryWriterRefusesAnUndeclaredReasonUnderO:
    def test_the_worker(self, tmp_path) -> None:
        result = optimized(tmp_path=tmp_path, body='''
            import asyncio

            import notebook_worker

            async def main():
                try:
                    await notebook_worker._record_skip(
                        1, "invented_reason",
                        {"from_id": 1, "to_id": 2, "work_key": "k"})
                except ValueError as exc:
                    print("REFUSED:", exc)
                    return
                except Exception as exc:
                    print("OTHER:", type(exc).__name__, exc)
                    return
                print("WROTE_IT")

            asyncio.run(main())
        ''')

        # The REASON, not just any ValueError. The store test next door
        # already did this; the worker's accepted anything raised anywhere
        # on the path, including an unrelated failure that happens to be a
        # ValueError.
        assert "REFUSED: undeclared skip reason" in result.stdout, (
            f"the worker's gate did not hold under -O: "
            f"{result.stdout!r} {result.stderr[-400:]!r}")

    def test_both_writers_in_the_store(self, tmp_path) -> None:
        """POSITIVE CONTROL for the writers the ledger did not count.

        Two of the three live in `commit_extraction` - one on its UPDATE
        branch and one on its INSERT branch. A fix that closed only the
        worker's would leave the gate reading as closed while it was half
        open, so both are driven here, on a connection of their own.
        """
        result = optimized(tmp_path=tmp_path, body='''
            import sqlite3

            import notebook_store

            import pathlib
            import re

            import database

            # The table the writer reads before it reaches the gate, built
            # from `database`'s OWN text rather than retyped here - a
            # hand-written copy of a schema is the thing that quietly stops
            # matching the schema. The CREATE, then every ALTER that adds a
            # column to the same table, in file order.
            src = pathlib.Path(database.__file__).read_text(encoding="utf-8")
            m = re.search(
                'CREATE TABLE IF NOT EXISTS notebook_extractions.*?"""',
                src, re.S)
            if not m:
                print("OTHER: DDL_NOT_FOUND")
                raise SystemExit(0)

            con = sqlite3.connect(":memory:")
            con.row_factory = sqlite3.Row
            con.execute(m.group(0)[:-3].rstrip().rstrip(";"))
            for alter in re.findall(
                    '"(ALTER TABLE notebook_extractions [^"]+)"', src):
                con.execute(alter)
            def refuses(work_key):
                try:
                    notebook_store.commit_extraction(
                        con, work_key=work_key, chat_id=1, from_id=1,
                        to_id=2, status="skipped",
                        skip_reason="invented_reason", proposals=[])
                except ValueError as exc:
                    return "undeclared skip reason" in str(exc)
                except Exception as exc:
                    print("OTHER:", type(exc).__name__, exc)
                return False

            # BRANCH 1, the INSERT: no row for this key yet.
            insert_branch = refuses("fresh-key")

            # BRANCH 2, the UPDATE: a prior non-`done` row for this key, so
            # the function takes its update path instead. Written straight
            # through SQL rather than through the function, because the
            # function is the thing under test.
            con.execute(
                "INSERT INTO notebook_extractions "
                "(work_key, chat_id, from_message_id, to_message_id, status) "
                "VALUES (?, ?, ?, ?, ?)",
                ("retried-key", 1, 1, 2, "failed"))
            update_branch = refuses("retried-key")

            print("INSERT_BRANCH:", insert_branch)
            print("UPDATE_BRANCH:", update_branch)
        ''')

        # BOTH, separately. Accepting "at least one" is what let a mutation
        # that disabled a single writer pass: the gate would read as closed
        # while it was half open, which is the exact failure this test is
        # here to rule out.
        assert "INSERT_BRANCH: True" in result.stdout, (
            f"the INSERT branch's gate did not hold under -O: "
            f"{result.stdout!r} {result.stderr[-400:]!r}")
        assert "UPDATE_BRANCH: True" in result.stdout, (
            f"the UPDATE branch's gate did not hold under -O: "
            f"{result.stdout!r} {result.stderr[-400:]!r}")

    def test_a_declared_reason_is_not_refused_under_O(
            self, tmp_path) -> None:
        """GROUND. Without it a gate that refused EVERY reason would satisfy
        both tests above and switch the feature off entirely - which is the
        failure that looks like safety.
        """
        result = optimized(tmp_path=tmp_path, body='''
            import asyncio

            import notebook_worker

            # A reason from the PANEL's vocabulary, not from the frozenset
            # the gate checks against. Drawing it from `SKIP_REASONS` makes
            # the assertion `x in S for x drawn from S`, which Python
            # guarantees - it could only ever catch a gate that stopped
            # consulting the set at all.
            reason = "proxy_gate"
            assert reason in notebook_worker.SKIP_REASONS

            async def main():
                # WHERE it got to, not merely that it survived. A
                # `_record_skip` that returned early and did nothing printed
                # the same word as one that passed the gate and then failed
                # on the locked vault, so this snippet could not tell the
                # two apart - measured, and it stayed green with the body
                # replaced by `return False`.
                import database

                reached = []
                real = database.get_db

                def watched(*a, **kw):
                    reached.append(1)
                    return real(*a, **kw)

                database.get_db = watched
                try:
                    await notebook_worker._record_skip(
                        1, reason,
                        {"from_id": 1, "to_id": 2, "work_key": "k"})
                except ValueError as exc:
                    if "undeclared skip reason" in str(exc):
                        print("WRONGLY_REFUSED:", reason)
                        return
                except Exception:
                    pass
                finally:
                    database.get_db = real
                print("REACHED_THE_WRITER:" if reached else "STOPPED_EARLIER:",
                      reason)

            asyncio.run(main())
        ''')

        assert "REACHED_THE_WRITER:" in result.stdout, (
            f"a declared reason did not get past the gate: "
            f"{result.stdout!r} {result.stderr[-400:]!r}")

    def test_the_early_exit_still_comes_first(self, tmp_path) -> None:
        """`plan is None` returns False WITHOUT reaching the gate, and that
        ordering is worth pinning: a fix that moved the check above it would
        start raising on a path whose whole point is to say "nothing to
        record" quietly."""
        result = optimized(tmp_path=tmp_path, body='''
            import asyncio

            import notebook_worker

            async def main():
                # The gate must NOT be reached. Asserting only the return
                # value cannot see that: a `_record_skip` that did nothing
                # at all returned False too, which is how this snippet used
                # to stay green with the body replaced.
                out = await notebook_worker._record_skip(
                    1, "invented_reason", None)
                print("RETURNED:", out)
                try:
                    await notebook_worker._record_skip(
                        1, "invented_reason",
                        {"from_id": 1, "to_id": 2, "work_key": "k"})
                except ValueError:
                    print("AND_THE_GATE_IS_LIVE")

            asyncio.run(main())
        ''')

        assert "AND_THE_GATE_IS_LIVE" in result.stdout, (
            "the gate is not running at all, so returning False proves "
            f"nothing: {result.stdout!r}")
        assert "RETURNED: False" in result.stdout, (
            f"the plan-is-None early exit changed: "
            f"{result.stdout!r} {result.stderr[-400:]!r}")
