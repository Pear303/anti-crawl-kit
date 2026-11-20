"""示例 16：本地 fixture 必须在不联网、不启动浏览器的情况下跑通。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "16_local_fixture.py"
FIXTURES = ROOT / "examples" / "fixtures"


def test_fixture_files_exist():
    for name in ("list.html", "spa_shell.html", "captcha.html"):
        assert (FIXTURES / name).is_file(), name


def test_example_runs_offline_and_passes():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        cwd=ROOT,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "全部通过" in out
    assert "spa_shell" in out
    assert "captcha_wall" in out
    assert "命中 10 条" in out


def test_example_survives_gbk_console():
    """Windows 默认 GBK 控制台下 ¥ 不能让进程崩（safe_print 兜底）。"""
    env = dict(os.environ, PYTHONIOENCODING="gbk")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="gbk",
        errors="replace",
        timeout=60,
        cwd=ROOT,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "[OK]" in proc.stdout
    assert "Traceback" not in proc.stdout + proc.stderr
