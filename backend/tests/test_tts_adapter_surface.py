"""U-45 - the adapter contract could be changed without anything noticing.

`TtsAdapter` declares three abstract members and three engines implement
them. Nothing in the repository read that surface: no test named the class,
`signature_files` appeared in zero tests, and either the interface or an
override could be added, renamed or deleted in silence.

That is the real exposure in this record. `signature_files` itself breaks
nothing today - it is an unused deferral, and its docstring now says so
accurately - but "nothing breaks" is exactly the problem: the shape of the
contract was unmeasured in both directions.

THE CLASS OBJECT, not its source text. `__abstractmethods__` and
`getattr` are what Python itself enforces at instantiation, so this measures
the contract that actually binds an adapter rather than the words that
describe it.
"""
from __future__ import annotations

import pytest

from tts.base import TtsAdapter


#: What an adapter MUST provide.
#:
#: Written down HERE and compared against the class, which is the point: a
#: list read off the class would agree with it by construction. This is the
#: claim; `__abstractmethods__` is the evidence.
REQUIRED = {"identify", "signature_files", "describe_settings"}


class TestTheContractItself:
    def test_it_declares_exactly_these_three(self) -> None:
        assert set(TtsAdapter.__abstractmethods__) == REQUIRED

    def test_the_class_is_actually_abstract(self) -> None:
        """GROUND CONTROL. An empty `__abstractmethods__` is a frozenset that
        compares equal to nothing above, but it is also what a class that
        forgot `ABC` looks like - and then the test above would be measuring
        a contract Python does not enforce."""
        assert TtsAdapter.__abstractmethods__, "nothing is abstract here"

        with pytest.raises(TypeError):
            TtsAdapter()  # type: ignore[abstract]


@pytest.mark.parametrize("module_name", [
    "tts.adapters.xtts_v2",
    "tts.adapters.fish_s2",
    "tts.adapters.chatterbox",
])
class TestEveryShippedAdapterHonoursIt:
    @staticmethod
    def _adapter(module_name: str):
        """The adapter this module DEFINES, not one it imported.

        `getmembers` returns imported names too, so a module that ever pulls
        in a sibling adapter would have handed back whichever sorted first -
        right by luck today, and silently testing the wrong class tomorrow.
        `__module__` settles it.
        """
        import importlib
        import inspect

        mod = importlib.import_module(module_name)
        found = [obj for _, obj in inspect.getmembers(mod, inspect.isclass)
                 if issubclass(obj, TtsAdapter) and obj is not TtsAdapter
                 and obj.__module__ == module_name]
        assert len(found) == 1, (
            f"{module_name} defines {len(found)} TtsAdapter subclasses")
        return found[0]

    def test_it_implements_every_required_member(
            self, module_name: str) -> None:
        adapter = self._adapter(module_name)

        assert not adapter.__abstractmethods__, (
            f"{adapter.__name__} leaves these unimplemented: "
            f"{sorted(adapter.__abstractmethods__)}")
        for name in REQUIRED:
            assert callable(getattr(adapter, name, None)), (
                f"{adapter.__name__} has no callable {name}")

    def test_signature_files_answers_for_a_folder_that_is_not_there(
            self, module_name: str) -> None:
        """The one behavioural thing that can be said about an unused method:
        it does not explode on the input every caller would give it first.

        A deferred contract that raises the moment somebody tries to use it
        is not deferred, it is broken - and nothing would have found out.
        """
        from pathlib import Path

        adapter = self._adapter(module_name)

        pairs = adapter.signature_files(Path("no-such-model-folder"))

        assert isinstance(pairs, list)
        # NON-EMPTY, or the loop below asserts nothing at all. An adapter
        # that returned `[]` used to satisfy every line of this test.
        assert pairs, f"{adapter.__name__} named no files"
        for entry in pairs:
            assert isinstance(entry, tuple) and len(entry) == 2, entry
            relpath, size = entry
            assert isinstance(relpath, str) and isinstance(size, int)
