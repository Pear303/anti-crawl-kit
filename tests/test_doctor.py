"""``ack doctor``：输出格式、退出码、端口与目录检查。"""

from __future__ import annotations

import json
import socket
from pathlib import Path

from anti_crawl_kit.tools import doctor
from anti_crawl_kit.tools.doctor import main as doctor_main


def test_json_output_is_machine_readable(tmp_path: Path, capsys):
    code = doctor_main(["--json", "--runs", str(tmp_path / "runs")])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["failures"] == 0
    names = [check["name"] for check in data["checks"]]
    assert "Python" in names
    assert any("anti-crawl-kit" in name for name in names)
    assert all(check["level"] in {"OK", "INFO", "WARN", "FAIL"} for check in data["checks"])


def test_text_output_has_next_steps(tmp_path: Path, capsys):
    code = doctor_main(["--runs", str(tmp_path / "runs")])
    out = capsys.readouterr().out
    assert code == 0
    assert "[OK]" in out
    assert "下一步" in out
    assert "ack init recipe" in out


def test_fail_makes_exit_code_1(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor,
        "_collect",
        lambda port, runs: [doctor.Check("FAIL", "坏掉了", "原因", "修法")],
    )
    assert doctor_main([]) == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "坏掉了" in out
    assert "→ 修法" in out


def test_warn_does_not_fail(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor,
        "_collect",
        lambda port, runs: [doctor.Check("WARN", "可选依赖缺失", "不影响纯逻辑", "pip install x")],
    )
    assert doctor_main([]) == 0
    assert "[WARN]" in capsys.readouterr().out


def test_port_free_detects_listener():
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        assert doctor._port_free(port) is False
    finally:
        holder.close()
    assert doctor._port_free(port) is True


def test_runs_dir_check_creates_and_writes(tmp_path: Path):
    target = tmp_path / "nested" / "runs"
    check = doctor._check_runs_dir(target)
    assert check.level == "OK"
    assert target.is_dir()
    # 临时文件不应残留
    assert not list(target.glob(".ack-doctor-*"))


def test_proxy_check_reports_env(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    check = doctor._check_proxy()
    assert check.level == "WARN"
    assert "HTTPS_PROXY" in check.detail


def test_proxy_check_silent_without_env(monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(key, raising=False)
    check = doctor._check_proxy()
    assert check.level == "OK"


def test_no_proxy_alone_is_not_a_warning(monkeypatch):
    """NO_PROXY 只是排除列表，单独存在不代表走代理。"""
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    check = doctor._check_proxy()
    assert check.level == "OK"
