"""``ack`` 统一入口：子命令转发与帮助。"""

from __future__ import annotations

from pathlib import Path

import pytest

from anti_crawl_kit.tools.ack_cli import COMMANDS, main as ack_main

pytest.importorskip("yaml", reason="部分子命令需要 [config] extra")


def test_help_lists_commands(capsys):
    assert ack_main([]) == 0
    out = capsys.readouterr().out
    assert "ack doctor" in out
    assert "ack init recipe" in out


def test_version(capsys):
    assert ack_main(["--version"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.count(".") == 2


def test_unknown_command_returns_2(capsys):
    assert ack_main(["nope"]) == 2
    err = capsys.readouterr().err
    assert "未知命令" in err


def test_doctor_is_forwarded(tmp_path: Path, capsys):
    assert ack_main(["doctor", "--json", "--runs", str(tmp_path / "runs")]) == 0
    assert '"checks"' in capsys.readouterr().out


def test_init_recipe_is_forwarded(tmp_path: Path):
    target = tmp_path / "x.yaml"
    assert ack_main(["init", "recipe", "--out", str(target)]) == 0
    assert target.is_file()


def test_every_alias_module_is_importable():
    import importlib

    for name, (module, func) in COMMANDS.items():
        imported = importlib.import_module(module)
        assert callable(getattr(imported, func)), name


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_every_command_help_exits_zero(name):
    """ack <子命令> --help 必须能跑：主入口会转发 argv，不能假设目标 main() 只收 sys.argv。"""
    with pytest.raises(SystemExit) as exc:
        ack_main([name, "--help"])
    assert exc.value.code == 0


@pytest.mark.parametrize("name", ["dom", "detect"])
def test_browser_tools_expose_skip_version_check(name, capsys):
    """版本超前/滞后时，探测工具要能用显式开关跑通，而不是只能等降级。"""
    with pytest.raises(SystemExit):
        ack_main([name, "--help"])
    assert "--skip-version-check" in capsys.readouterr().out
