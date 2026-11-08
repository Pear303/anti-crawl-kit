"""打包与惰性导出的回归测试。

这些测试保护两件事：
1. ``import anti_crawl_kit`` 不会拖起 patchright（纯逻辑环境可用）；
2. ``__all__`` 与 ``_LAZY`` 映射一致，没有重复或漏项。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def test_package_import_does_not_load_patchright() -> None:
    """子进程里 import 包，断言 patchright 没有被加载。"""
    code = (
        "import sys\n"
        "import anti_crawl_kit\n"
        "assert 'patchright' not in sys.modules, 'patchright 被顶层导入了'\n"
        "assert 'curl_cffi' not in sys.modules, 'curl_cffi 被顶层导入了'\n"
        "print(anti_crawl_kit.__version__)\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC),
    }
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip()


def test_all_exports_are_unique() -> None:
    import anti_crawl_kit

    assert len(anti_crawl_kit.__all__) == len(set(anti_crawl_kit.__all__))


def test_lazy_map_covers_all_exports() -> None:
    import anti_crawl_kit

    missing = [name for name in anti_crawl_kit.__all__ if name not in anti_crawl_kit._LAZY]
    assert not missing, f"__all__ 里有未映射的符号: {missing}"


def test_core_exports_resolve_without_optional_deps() -> None:
    """不依赖 patchright / curl_cffi 的符号必须能解析。"""
    import anti_crawl_kit

    core = [
        "detect_waf",
        "classify_http_result",
        "hunt_api",
        "extract_state",
        "classify_page_shape",
        "relocate",
        "check_content",
        "AntiCrawlGuard",
        "StopReason",
    ]
    for name in core:
        assert getattr(anti_crawl_kit, name) is not None


def test_py_typed_exists() -> None:
    assert (SRC / "anti_crawl_kit" / "py.typed").exists()


def test_cli_entry_points_declared() -> None:
    try:
        import tomllib
    except ModuleNotFoundError:
        pytest.skip("读取 pyproject.toml 需要 Python 3.11+")

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert set(scripts) == {
        "ack-probe",
        "ack-hunt",
        "ack-dom",
        "ack-detect",
        "ack-bench",
        "ack-audit",
        "ack-health",
    }
    for target in scripts.values():
        module, _, func = target.partition(":")
        assert module.startswith("anti_crawl_kit.")
        assert func == "main"
