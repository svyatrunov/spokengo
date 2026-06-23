import logging

from spokengo.logging_setup import setup_logging, tail


def test_setup_creates_file_and_logs(tmp_path):
    p = tmp_path / "spokengo.log"
    setup_logging(path=p, force=True, console=False)
    logging.getLogger("spokengo.test").error("boom: Groq отклонил запрос (400)")
    assert p.exists()
    content = tail(p, lines=10)
    assert "boom: Groq отклонил запрос (400)" in content


def test_tail_missing_file_is_empty(tmp_path):
    assert tail(tmp_path / "nope.log") == ""


def test_tail_limits_lines(tmp_path):
    p = tmp_path / "spokengo.log"
    setup_logging(path=p, force=True, console=False)
    lg = logging.getLogger("spokengo.test2")
    for i in range(20):
        lg.info("line %d", i)
    out = tail(p, lines=5)
    assert len(out.splitlines()) == 5
    assert "line 19" in out
