"""Guards for the path that runs while the user is mid-sentence.

The live overlay polls the paste target ~3x a second for the whole recording.
When that poll reached AttachThreadInput, pressing the hotkey with a caret in a
text field destroyed the caret and the user could no longer type into it.
"""
from __future__ import annotations

import inspect

from spokengo.config import Config
from spokengo.controller import GuiController
from spokengo.storage import Storage


class _DeepInjector:
    def __init__(self):
        self.deep, self.cheap = 0, 0

    def capture_target(self):
        self.deep += 1
        return "deep"

    def peek_target(self):
        self.cheap += 1
        return "cheap"

    def inject(self, target, text):
        return False


class _LegacyInjector:
    """No peek_target — the controller must still work."""
    def __init__(self):
        self.deep = 0

    def capture_target(self):
        self.deep += 1
        return "deep"

    def inject(self, target, text):
        return False


def _ctrl(tmp_path, inj):
    return GuiController(cfg=Config(store_audio=False), root_dir=tmp_path,
                         storage=Storage(tmp_path),
                         injector_factory=lambda c: inj,
                         recorder_factory=lambda c: None,
                         provider_factory=lambda c: None)


def test_overlay_poll_uses_the_cheap_path(tmp_path):
    inj = _DeepInjector()
    c = _ctrl(tmp_path, inj)
    for _ in range(10):
        c.peek_target()
    assert inj.cheap == 10
    assert inj.deep == 0, "the overlay poll must not run the deep capture"


def test_peek_falls_back_when_injector_has_no_peek(tmp_path):
    inj = _LegacyInjector()
    c = _ctrl(tmp_path, inj)
    assert c.peek_target() == "deep"
    assert inj.deep == 1


def _body_without_docstring(fn) -> str:
    """Source of ``fn`` minus its docstring, so prose about a banned call does
    not read as the call itself."""
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    fn_node = tree.body[0]
    body = fn_node.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return " ".join(ast.dump(n) for n in body)


def test_focused_child_never_attaches_input_queues():
    """AttachThreadInput clears focus and destroys the caret in the target.
    Reading focus must go through GetGUIThreadInfo instead."""
    from spokengo.inject.windows import WindowsInjector
    code = _body_without_docstring(WindowsInjector._focused_child)
    assert "AttachThreadInput" not in code, (
        "reading the focused child must not join input queues — it costs the "
        "user their caret on every hotkey press")


def test_peek_target_skips_the_child_lookup():
    from spokengo.inject.windows import WindowsInjector
    src = inspect.getsource(WindowsInjector.peek_target)
    assert "with_child=False" in src
