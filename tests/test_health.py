"""平台健康体检测试（离线，不启动浏览器）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anti_crawl_kit.health import (
    HealthStatus,
    HealthTarget,
    check_duplicates,
    load_health_config,
    probe_health_page,
    run_static_health_checks,
)
from anti_crawl_kit.tools import health_cli


COLLECTOR_SRC = '''
class DemoCollector:
    entry_param = "uid"
    auth_cookie = "sessionid"
    card_selector = ".profile-card"
'''

LOGIN_SRC = '''
QRCODE_SELECTOR = ".qrcode img"
'''


def _make_package(root: Path) -> Path:
    pkg = root / "mypkg"
    (pkg / "collectors").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "collectors" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "collectors" / "demo.py").write_text(COLLECTOR_SRC, encoding="utf-8")
    (pkg / "login.py").write_text(LOGIN_SRC, encoding="utf-8")
    return pkg


def _target(**overrides) -> HealthTarget:
    data = dict(
        platform="demo",
        collector_module="mypkg.collectors.demo",
        collector_class="DemoCollector",
        login_module="mypkg.login",
        auth_cookies=("sessionid",),
        entry_param="uid",
        qrcode_selector=".qrcode img",
        probe_selectors=(".profile-card",),
    )
    data.update(overrides)
    return HealthTarget(**data)


def test_all_static_checks_pass(tmp_path: Path) -> None:
    _make_package(tmp_path)
    report = run_static_health_checks([_target()], module_root=tmp_path)
    assert report.has_fail() is False
    assert report.counts()["OK"] >= 5


def test_missing_module_is_fail(tmp_path: Path) -> None:
    _make_package(tmp_path)
    report = run_static_health_checks(
        [_target(collector_module="mypkg.collectors.missing")], module_root=tmp_path
    )
    assert report.has_fail()
    assert any("模块文件缺失" in item.detail for item in report.results)


def test_missing_class_is_fail(tmp_path: Path) -> None:
    _make_package(tmp_path)
    report = run_static_health_checks(
        [_target(collector_class="WrongClass")], module_root=tmp_path
    )
    assert report.has_fail()
    assert any("类名" in item.detail for item in report.results)


def test_missing_entry_param_is_fail(tmp_path: Path) -> None:
    _make_package(tmp_path)
    report = run_static_health_checks(
        [_target(entry_param="sec_uid")], module_root=tmp_path
    )
    assert report.has_fail()
    assert any("入口提取可能漏分支" in item.detail for item in report.results)


def test_empty_entry_param_is_warn(tmp_path: Path) -> None:
    _make_package(tmp_path)
    report = run_static_health_checks([_target(entry_param="")], module_root=tmp_path)
    assert report.has_fail() is False
    assert report.has_warn()


def test_missing_auth_cookie_is_fail(tmp_path: Path) -> None:
    _make_package(tmp_path)
    report = run_static_health_checks(
        [_target(auth_cookies=("SESSDATA",))], module_root=tmp_path
    )
    assert report.has_fail()
    assert any("名单漂移" in item.detail for item in report.results)


def test_missing_qrcode_selector_is_fail(tmp_path: Path) -> None:
    _make_package(tmp_path)
    report = run_static_health_checks(
        [_target(qrcode_selector=".wrong img")], module_root=tmp_path
    )
    assert report.has_fail()
    assert any("二维码选择器" in item.detail for item in report.results)


def test_probe_selectors_stale_is_fail(tmp_path: Path) -> None:
    _make_package(tmp_path)
    report = run_static_health_checks(
        [_target(probe_selectors=(".old-card",))], module_root=tmp_path
    )
    assert report.has_fail()
    assert any("清单已过期" in item.detail for item in report.results)


def test_duplicate_platform_is_fail() -> None:
    results = check_duplicates([_target(), _target()])
    assert results and results[0].status is HealthStatus.FAIL


def test_report_serialization(tmp_path: Path) -> None:
    _make_package(tmp_path)
    report = run_static_health_checks([_target()], module_root=tmp_path)
    data = report.to_dict()
    assert data["summary"]["FAIL"] == 0
    assert data["results"]
    out = report.write(tmp_path / "health.json")
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["OK"] >= 1
    assert "[OK]" in report.render()


class FakePage:
    def __init__(self, *, counts: dict[str, int], url: str = "https://example.com/profile/1"):
        self.counts = counts
        self.url = url

    def goto(self, url: str, wait_until: str = "", timeout: int = 0) -> None:
        self.url = url

    def evaluate(self, script: str, arg=None, **kwargs):
        if "querySelectorAll" in script:
            return self.counts
        if "location.href" in script:
            return self.url
        if "window" in script and "names" in script:
            return {name: True for name in (arg or [])}
        return {}


def test_probe_hits_is_ok() -> None:
    target = _target(probe_url="https://example.com/profile/1")
    result = probe_health_page(
        FakePage(counts={".profile-card": 3}), target, wait_ms=0
    )
    assert result.status is HealthStatus.OK
    assert result.extra["counts"][".profile-card"] == 3


def test_probe_zero_hits_is_fail() -> None:
    target = _target(probe_url="https://example.com/profile/1")
    result = probe_health_page(
        FakePage(counts={".profile-card": 0}), target, wait_ms=0
    )
    assert result.status is HealthStatus.FAIL


def test_probe_without_url_is_skip() -> None:
    result = probe_health_page(FakePage(counts={}), _target(), wait_ms=0)
    assert result.status is HealthStatus.SKIP


def test_load_config_json(tmp_path: Path) -> None:
    _make_package(tmp_path)
    config = {
        "module_root": str(tmp_path),
        "platforms": [
            {
                "platform": "demo",
                "collector_module": "mypkg.collectors.demo",
                "collector_class": "DemoCollector",
                "login_module": "mypkg.login",
                "auth_cookies": ["sessionid"],
                "entry_param": "uid",
                "qrcode_selector": ".qrcode img",
                "probe_selectors": [".profile-card"],
            }
        ],
    }
    path = tmp_path / "ack_health.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    module_root, targets = load_health_config(path)
    assert module_root == tmp_path
    assert targets[0].platform == "demo"
    assert targets[0].auth_cookies == ("sessionid",)


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _make_package(tmp_path)
    config = {
        "module_root": str(tmp_path),
        "platforms": [
            {
                "platform": "demo",
                "collector_module": "mypkg.collectors.demo",
                "collector_class": "DemoCollector",
                "login_module": "mypkg.login",
                "auth_cookies": ["sessionid"],
                "entry_param": "uid",
                "qrcode_selector": ".qrcode img",
            }
        ],
    }
    path = tmp_path / "ack_health.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    assert health_cli.main(["--config", str(path)]) == 0

    config["platforms"][0]["collector_class"] = "WrongClass"
    path.write_text(json.dumps(config), encoding="utf-8")
    assert health_cli.main(["--config", str(path)]) == 1

    assert health_cli.main(["--config", str(tmp_path / "missing.json")]) == 2
    capsys.readouterr()
