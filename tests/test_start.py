import sys
from pathlib import Path

from lam.jobs import human_progress
from start import interpreter_works, resolve_lam_argv


def test_default_is_serve():
    assert resolve_lam_argv([]) == ["serve"]


def test_flags_without_command_go_to_serve():
    assert resolve_lam_argv(["--host", "0.0.0.0"]) == ["serve", "--host", "0.0.0.0"]


def test_human_progress_is_plain_english():
    line = human_progress("scan", {"seen": 12, "source": "screenshots"})
    assert "12" in line
    assert "{" not in line
    assert "screenshots" in line


def test_current_interpreter_works():
    assert interpreter_works(Path(sys.executable))


def test_missing_interpreter_does_not_work(tmp_path: Path):
    assert interpreter_works(tmp_path / "no-such-python.exe") is False


def test_explicit_commands_pass_through():
    assert resolve_lam_argv(["scan"]) == ["scan"]
    assert resolve_lam_argv(["apply", "--yes"]) == ["apply", "--yes"]
    assert resolve_lam_argv(["--help"]) == ["--help"]
