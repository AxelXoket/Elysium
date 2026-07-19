"""secrets_service + new-schema unit tests (E5/E6 step 1).

Covers: table creation, CRUD, upsert, conn= transaction join (rollback takes
the secret write with it), and the locked-vault contract.
"""

import pytest

import database
import secrets_service
import vault_state

# Same fixed key the client fixture pre-unlocks with (kept local so this file
# does not depend on tests/ being an importable package).
TEST_VAULT_KEY = bytes(range(32))


def test_new_tables_exist(client):
    with database.get_db() as con:
        names = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "attachment_blobs" in names
    assert "vault_secrets" in names


def test_secret_crud_roundtrip(client):
    assert secrets_service.get_secret("t_secret") is None
    secrets_service.set_secret("t_secret", "v1")
    assert secrets_service.get_secret("t_secret") == "v1"
    secrets_service.set_secret("t_secret", "v2")  # upsert
    assert secrets_service.get_secret("t_secret") == "v2"
    secrets_service.delete_secret("t_secret")
    assert secrets_service.get_secret("t_secret") is None
    secrets_service.delete_secret("t_secret")  # idempotent


def test_conn_param_joins_callers_transaction(client):
    """A rollback in the caller's transaction takes the secret write with it -
    proving conn= really joins the transaction instead of opening its own."""
    with pytest.raises(RuntimeError):
        with database.get_db() as con:
            secrets_service.set_secret("t_tx", "should-roll-back", conn=con)
            assert secrets_service.get_secret("t_tx", conn=con) == "should-roll-back"
            raise RuntimeError("force rollback")
    assert secrets_service.get_secret("t_tx") is None


def test_locked_vault_raises(client):
    from vault_state import VaultLockedError

    vault_state.clear_key()
    try:
        with pytest.raises(VaultLockedError):
            secrets_service.get_secret("t_locked")
        with pytest.raises(VaultLockedError):
            secrets_service.set_secret("t_locked", "x")
    finally:
        vault_state.set_key(TEST_VAULT_KEY)
