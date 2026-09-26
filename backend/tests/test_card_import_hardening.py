"""Character import and the two name doors: what used to be a 500, and the cap.

PK-06: a deeply nested card and a card holding a 5000-digit number made
`json.loads` raise RecursionError / ValueError, which the import did not catch.
PK-07: a lone surrogate (`"\\ud800"`) made the INSERT raise, and in `tags` the
response encoder raise. All four are now a named 400, and nothing is stored.

Ek P, B8: character and persona names are capped at NAME_MAX_CODE_POINTS code
points. A form refuses a longer name with `name_too_long`, but only when the
name CHANGES; an import shortens it and says so in `import_report`.

Every constant is imported from the router, never retyped.
"""
from __future__ import annotations

import json
import logging

from routers.characters import NAME_MAX_CODE_POINTS

IMPORT = "/api/v1/characters/import"
CHARS = "/api/v1/characters"
PERSONAS = "/api/v1/personas"


def _import(client, body: str | bytes):
    raw = body.encode("utf-8") if isinstance(body, str) else body
    return client.post(IMPORT, content=raw)


def _count(client, path: str = CHARS) -> int:
    resp = client.get(path)
    assert resp.status_code == 200, resp.text
    return len(resp.json())


def _card(**fields) -> str:
    base = {"name": "Plain", "description": "d"}
    base.update(fields)
    return json.dumps(base)


def _seed_long_name(table: str, column: str, name: str) -> int:
    """A record written before the cap existed: straight into the vault."""
    from database import get_db
    with get_db() as con:
        if table == "characters":
            cur = con.execute(
                "INSERT INTO characters (name, tags, raw_json) VALUES (?, '[]', '{}')",
                (name,))
        else:
            cur = con.execute(
                "INSERT INTO personas (display_name, description) VALUES (?, '')",
                (name,))
        return cur.lastrowid


# ── PK-06 / PK-07: the import answers 400, never 500 ───────────────────────


class TestImportRefusesWhatItCannotRead:
    def test_ground_control_an_ordinary_card_imports(self, client):
        resp = _import(client, _card())
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == "Plain"
        assert resp.json()["import_report"] == {}

    def test_deep_array_is_a_named_400(self, client):
        before = _count(client)
        resp = _import(client, "[" * 200_000 + "]" * 200_000)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "invalid_character_json"
        assert _count(client) == before

    def test_deep_object_inside_a_card_is_a_named_400(self, client):
        nested = '{"a":' * 50_000 + "1" + "}" * 50_000
        resp = _import(client, '{"name": "X", "extra": ' + nested + "}")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "invalid_character_json"

    def test_positive_control_modest_nesting_still_imports(self, client):
        nested = '{"a":' * 50 + "1" + "}" * 50
        resp = _import(client, '{"name": "Nest", "extra": ' + nested + "}")
        assert resp.status_code == 201, resp.text

    def test_huge_integer_is_a_named_400(self, client):
        before = _count(client)
        resp = _import(client, '{"name": "N", "x": ' + "9" * 5000 + "}")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "invalid_character_json"
        assert _count(client) == before

    def test_positive_control_an_ordinary_integer_imports(self, client):
        resp = _import(client, '{"name": "N", "x": ' + "9" * 30 + "}")
        assert resp.status_code == 201, resp.text

    def test_lone_surrogate_in_any_stored_field_is_a_named_400(self, client):
        before = _count(client)
        for field in ("name", "description", "personality", "scenario",
                      "first_mes", "mes_example", "system_prompt",
                      "post_history_instructions"):
            body = '{"name": "S", "%s": "a\\ud800b"}' % field
            if field == "name":
                body = '{"name": "a\\ud800b"}'
            resp = _import(client, body)
            assert resp.status_code == 400, (field, resp.text)
            assert resp.json()["detail"] == "invalid_character_json"
        # tags: stored escaped, so the INSERT passed and the ANSWER broke.
        resp = _import(client, '{"name": "T", "tags": ["\\udc00"]}')
        assert resp.status_code == 400, resp.text
        assert _count(client) == before

    def test_lone_surrogate_in_a_v2_data_wrapper_is_caught_too(self, client):
        resp = _import(client, '{"spec": "chara_card_v2", "data": {"name": "\\ud800"}}')
        assert resp.status_code == 400

    def test_positive_control_a_paired_surrogate_is_an_emoji_and_imports(self, client):
        resp = _import(client, '{"name": "a\\ud83d\\ude00b", "tags": ["\\ud83d\\ude00"]}')
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == "a\U0001F600b"
        assert resp.json()["tags"] == ["\U0001F600"]

    def test_a_lone_surrogate_outside_the_stored_fields_is_harmless(self, client):
        """Positive control for the scope of the check: an unused key is only
        kept in raw_json, which is written from the raw bytes and never
        answered, so it breaks nothing and is not refused."""
        resp = _import(client, '{"name": "U", "creator": "\\ud800"}')
        assert resp.status_code == 201, resp.text


# ── B8: the import shortens a long name and reports it ─────────────────────


class TestImportShortensLongNames:
    def test_ground_control_a_name_at_the_cap_is_untouched(self, client):
        name = "n" * NAME_MAX_CODE_POINTS
        resp = _import(client, _card(name=name))
        assert resp.status_code == 201
        assert resp.json()["name"] == name
        assert resp.json()["import_report"] == {}

    def test_one_over_the_cap_is_shortened_and_reported(self, client):
        name = "n" * (NAME_MAX_CODE_POINTS + 1)
        resp = _import(client, _card(name=name))
        assert resp.status_code == 201, resp.text
        out = resp.json()
        assert out["name"] == name[:NAME_MAX_CODE_POINTS]
        assert out["import_report"] == {"name_shortened_from": NAME_MAX_CODE_POINTS + 1}
        # And it is what was stored, not only what was answered.
        stored = client.get(f"{CHARS}/{out['id']}").json()["name"]
        assert stored == name[:NAME_MAX_CODE_POINTS]

    def test_a_10kb_name_is_shortened_not_refused(self, client):
        resp = _import(client, _card(name="x" * 10_000))
        assert resp.status_code == 201
        assert len(resp.json()["name"]) == NAME_MAX_CODE_POINTS

    def test_the_cap_counts_code_points_not_utf16_units(self, client):
        """An astral character is one code point in Python and two UTF-16
        units: the cut keeps whole characters and exactly the cap of them."""
        name = "\U0001F600" * (NAME_MAX_CODE_POINTS + 3)
        resp = _import(client, _card(name=name))
        assert resp.status_code == 201
        assert resp.json()["name"] == "\U0001F600" * NAME_MAX_CODE_POINTS

    def test_a_cut_that_lands_on_a_space_does_not_keep_it(self, client):
        name = "a" * (NAME_MAX_CODE_POINTS - 1) + " " + "b" * 10
        resp = _import(client, _card(name=name))
        assert resp.json()["name"] == "a" * (NAME_MAX_CODE_POINTS - 1)

    def test_the_long_name_is_never_logged(self, client, caplog):
        marker = "Zq" * 60
        with caplog.at_level(logging.DEBUG):
            resp = _import(client, _card(name=marker))
        assert resp.status_code == 201
        assert caplog.records, "ground control: the import logs something"
        assert not any(marker[:NAME_MAX_CODE_POINTS] in r.getMessage()
                       for r in caplog.records)


# ── Regression: the cap must not touch the default-name path ───────────────


class TestTheCapLeavesDefaultNamesAlone:
    """The first version of the cap called len() on a name the body left out.
    Pydantic does not run validators on defaults, so an absent name reached
    the cap as None and a create with no name answered 500."""

    def test_character_missing_null_and_blank_names_get_a_default(self, client):
        import default_names
        made = [client.post(CHARS, json=body) for body in (
            {}, {"name": None}, {"name": ""}, {"name": "   "})]
        assert [r.status_code for r in made] == [201] * 4, [r.text for r in made]
        assert [r.json()["name"] for r in made] == [
            f"{default_names.CHARACTER} {n}" for n in (1, 2, 3, 4)]

    def test_persona_missing_null_and_blank_names_get_a_default(self, client):
        import default_names
        made = [client.post(PERSONAS, json=body) for body in (
            {}, {"display_name": None}, {"display_name": ""})]
        assert [r.status_code for r in made] == [201] * 3, [r.text for r in made]
        assert [r.json()["display_name"] for r in made] == [
            f"{default_names.PERSONA} {n}" for n in (1, 2, 3)]

    def test_patch_without_a_name_is_not_a_rename(self, client):
        cid = client.post(CHARS, json={"name": "Keep"}).json()["id"]
        resp = client.patch(f"{CHARS}/{cid}", json={"description": "x"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Keep"


# ── B8: the forms refuse, on change only ───────────────────────────────────


class TestCharacterFormCap:
    def test_ground_control_the_cap_itself_is_accepted(self, client):
        resp = client.post(CHARS, json={"name": "c" * NAME_MAX_CODE_POINTS})
        assert resp.status_code == 201, resp.text

    def test_create_one_over_is_refused_and_nothing_stored(self, client):
        before = _count(client)
        resp = client.post(CHARS, json={"name": "c" * (NAME_MAX_CODE_POINTS + 1)})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "name_too_long"
        assert _count(client) == before

    def test_surrounding_spaces_do_not_count(self, client):
        resp = client.post(CHARS, json={"name": "  " + "c" * NAME_MAX_CODE_POINTS + "  "})
        assert resp.status_code == 201

    def test_rename_to_a_long_name_is_refused(self, client):
        cid = client.post(CHARS, json={"name": "Short"}).json()["id"]
        resp = client.patch(f"{CHARS}/{cid}", json={"name": "c" * (NAME_MAX_CODE_POINTS + 1)})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "name_too_long"
        assert client.get(f"{CHARS}/{cid}").json()["name"] == "Short"

    def test_an_old_long_name_sent_back_unchanged_is_not_refused(self, client):
        long_name = "L" * (NAME_MAX_CODE_POINTS + 20)
        cid = _seed_long_name("characters", "name", long_name)
        resp = client.patch(f"{CHARS}/{cid}", json={"name": long_name, "description": "new"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == long_name
        assert resp.json()["description"] == "new"
        # Positive control on the same record: a DIFFERENT long name is a change.
        resp = client.patch(f"{CHARS}/{cid}", json={"name": long_name + "x"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "name_too_long"


class TestPersonaFormCap:
    def test_ground_control_the_cap_itself_is_accepted(self, client):
        resp = client.post(PERSONAS, json={"display_name": "p" * NAME_MAX_CODE_POINTS})
        assert resp.status_code == 201, resp.text

    def test_create_one_over_is_refused_and_nothing_stored(self, client):
        before = _count(client, PERSONAS)
        resp = client.post(PERSONAS, json={"display_name": "p" * (NAME_MAX_CODE_POINTS + 1)})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "name_too_long"
        assert _count(client, PERSONAS) == before

    def test_rename_long_refused_same_long_accepted(self, client):
        long_name = "P" * (NAME_MAX_CODE_POINTS + 5)
        pid = _seed_long_name("personas", "display_name", long_name)
        ok = client.patch(f"{PERSONAS}/{pid}", json={"display_name": long_name, "description": "x"})
        assert ok.status_code == 200, ok.text
        bad = client.patch(f"{PERSONAS}/{pid}", json={"display_name": long_name + "y"})
        assert bad.status_code == 400
        assert bad.json()["detail"] == "name_too_long"


# ── PK-07 on the form doors: 422, and the broken text is not echoed ────────


class TestFormsRefuseLoneSurrogates:
    def _post_raw(self, client, method, path, body: bytes):
        return client.request(method, path, content=body,
                              headers={"content-type": "application/json"})

    def test_character_create_and_patch(self, client):
        before = _count(client)
        resp = self._post_raw(client, "POST", CHARS, b'{"name": "a\\ud800"}')
        assert resp.status_code == 422
        assert resp.json()["detail"] == "validation_failed"
        assert "\\ud800" not in resp.text and "\ud800" not in resp.text
        assert _count(client) == before

        cid = client.post(CHARS, json={"name": "Ok"}).json()["id"]
        resp = self._post_raw(client, "PATCH", f"{CHARS}/{cid}",
                              b'{"tags": ["\\udfff"]}')
        assert resp.status_code == 422
        assert resp.json()["detail"] == "validation_failed"

    def test_persona_create_and_patch(self, client):
        resp = self._post_raw(client, "POST", PERSONAS,
                              b'{"display_name": "Ok", "description": "\\ud800"}')
        assert resp.status_code == 422
        assert resp.json()["detail"] == "validation_failed"
        pid = client.post(PERSONAS, json={"display_name": "Ok"}).json()["id"]
        resp = self._post_raw(client, "PATCH", f"{PERSONAS}/{pid}",
                              b'{"display_name": "\\udbff"}')
        assert resp.status_code == 422
        assert resp.json()["detail"] == "validation_failed"

    def test_positive_control_emoji_passes_every_form(self, client):
        resp = self._post_raw(client, "POST", CHARS,
                              b'{"name": "\\ud83d\\ude00", "tags": ["\\ud83d\\ude00"]}')
        assert resp.status_code == 201, resp.text
        resp = self._post_raw(client, "POST", PERSONAS,
                              b'{"display_name": "\\ud83d\\ude00"}')
        assert resp.status_code == 201, resp.text
