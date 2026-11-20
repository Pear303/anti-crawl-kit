"""HTML 报告：类型识别、渲染、转义、CLI。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from anti_crawl_kit.report import (
    detect_kind,
    render_html,
    render_index,
    write_html,
)
from anti_crawl_kit.tools.report_cli import main as report_main


def _audit_data() -> dict:
    return {
        "platform": "demo",
        "started_at": "2026-11-11 10:00:00",
        "finished_at": "2026-11-11 10:01:00",
        "meta": {
            "test_url": "https://example.com/list",
            "hard_stop": "",
            "path_matrix": [
                {
                    "path": "anonymous_json",
                    "layer": "L1",
                    "label": "匿名接口",
                    "status": 200,
                    "latency_ms": 320,
                    "stop_reason": "completed",
                    "tested": True,
                    "fields": {"title": "x"},
                    "total_score": 22,
                },
                {
                    "path": "dom",
                    "layer": "L5",
                    "label": "DOM",
                    "stop_reason": "not_tested",
                    "tested": False,
                },
            ],
            "recommendation": {
                "primary": {"path": "anonymous_json", "reason": "评分 22/25"},
                "fallback": None,
                "rejected": [],
                "production_ready": False,
                "acceptance_gaps": ["需要复测"],
            },
        },
    }


def _health_data() -> dict:
    return {
        "started_at": "2026-11-11 09:00:00",
        "finished_at": "2026-11-11 09:00:02",
        "summary": {"OK": 1, "WARN": 0, "FAIL": 1, "SKIP": 0},
        "results": [
            {"dimension": "registry", "platform": "a", "status": "OK", "detail": "ok"},
            {"dimension": "registry", "platform": "b", "status": "FAIL", "detail": "missing"},
        ],
    }


def _smoke_data() -> dict:
    return {
        "started_at": "2026-11-11 08:00:00",
        "finished_at": "2026-11-11 08:00:30",
        "summary": {"PASS": 1, "FAIL": 1, "SKIP": 0},
        "results": [
            {
                "name": "books",
                "kind": "http",
                "ok": True,
                "status": "PASS",
                "actual": "20 条",
                "elapsed_ms": 1200,
            },
            {
                "name": "quotes",
                "kind": "browser",
                "ok": False,
                "status": "FAIL",
                "error": "timeout",
                "elapsed_ms": 30000,
            },
        ],
    }


def _assess_data() -> dict:
    return {
        "platform": "demo",
        "url": "https://example.com",
        "started_at": "2026-11-11 07:00:00",
        "finished_at": "2026-11-11 07:10:00",
        "decision": "conditional_go",
        "score": 85,
        "gaps": ["需要双跑"],
        "next_actions": ["补健康体检"],
        "gates": [
            {"gate": "G0", "name": "形态", "status": "PASS", "summary": "ssr", "elapsed_ms": 100},
            {
                "gate": "G1",
                "name": "路径",
                "status": "WARN",
                "summary": "2 条",
                "gaps": ["样本少"],
                "elapsed_ms": 0,
            },
        ],
    }


def _run_data() -> dict:
    return {
        "task": "feed",
        "platform": "demo",
        "started_at": "2026-11-11 06:00:00",
        "finished_at": "2026-11-11 06:00:05",
        "status": "empty",
        "total": 1,
        "ok": 0,
        "failed": 1,
        "success_rate": 0.0,
        "expected_count": 20,
        "actual_count": 0,
        "item_coverage": 0.0,
        "degraded_reasons": ["dom_fallback"],
        "outcomes": [{"path": "feed", "ok": False, "stop_reason": "empty"}],
    }


def _recipe_data() -> dict:
    return {
        "run_id": "abc",
        "recipe_id": "demo_recipe",
        "status": "needs_agent",
        "steps": [
            {"id": "fetch", "uses": "http_request", "status": "ok", "elapsed_ms": 10},
            {"id": "decide", "uses": "agent", "status": "needs_agent"},
        ],
        "handoff": {"step_id": "decide", "prompt": "确认继续吗", "reason": "agent"},
    }


def test_detect_kind():
    assert detect_kind(_audit_data()) == "audit"
    assert detect_kind(_health_data()) == "health"
    assert detect_kind(_smoke_data()) == "smoke"
    assert detect_kind(_assess_data()) == "assess"
    assert detect_kind(_run_data()) == "run"
    assert detect_kind(_recipe_data()) == "recipe"
    assert detect_kind({}) == "run"


def test_audit_report_renders_matrix_and_recommendation():
    html = render_html(_audit_data())
    assert "路径审计 · demo" in html
    assert "匿名接口" in html
    assert "部分通过" in html
    assert "选型建议" in html
    assert "验收缺口" in html
    assert 'data-tone="fail"' not in html  # 没有失败路径
    assert 'data-tone="muted"' in html     # 未测路径


def test_health_report_counts():
    html = render_html(_health_data())
    assert "平台健康体检" in html
    assert "1 项失败" in html
    assert "missing" in html


def test_smoke_report_lists_cases():
    html = render_html(_smoke_data())
    assert "真实站点冒烟" in html
    assert "quotes" in html
    assert "timeout" in html


def test_assess_report_gates():
    html = render_html(_assess_data())
    assert "技术验证 · demo" in html
    assert "CONDITIONAL GO" in html
    assert "需要双跑" in html
    assert "补健康体检" in html


def test_run_report_shows_silent_empty_fields():
    html = render_html(_run_data())
    assert "静默失效四字段" in html
    assert "expected_count" in html
    assert "dom_fallback" in html
    assert "EMPTY" in html


def test_recipe_report_shows_handoff():
    html = render_html(_recipe_data())
    assert "配方执行 · demo_recipe" in html
    assert "确认继续吗" in html
    assert "NEEDS_AGENT" in html


def test_render_escapes_platform_content():
    data = _health_data()
    data["results"][0]["detail"] = "<script>alert('xss')</script>"
    html = render_html(data)
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html
    # 页面只有自己的一个 script 标签
    assert html.count("</script>") == 1


def test_empty_data_does_not_crash():
    for kind in ("audit", "health", "smoke", "assess", "run", "recipe"):
        html = render_html({}, kind=kind)
        assert "<!doctype html>" in html
        assert len(html) > 1000


def test_write_html_creates_parent(tmp_path: Path):
    target = write_html(_health_data(), tmp_path / "nested" / "health.html")
    assert target.is_file()
    assert "平台健康体检" in target.read_text(encoding="utf-8")


def test_render_index_links_reports():
    html = render_index(
        [
            {
                "path": "a.html",
                "title": "报告 A",
                "kind": "health",
                "verdict": "PASS",
                "tone": "ok",
                "finished_at": "2026-11-11",
            }
        ]
    )
    assert 'href="a.html"' in html
    assert "报告 A" in html
    assert "PASS" in html


def test_cli_converts_single_file(tmp_path: Path, capsys):
    source = tmp_path / "health.json"
    source.write_text(json.dumps(_health_data(), ensure_ascii=False), encoding="utf-8")
    code = report_main(["--input", str(source)])
    assert code == 0
    assert (tmp_path / "health.html").is_file()
    assert "health" in capsys.readouterr().out


def test_cli_converts_directory(tmp_path: Path):
    (tmp_path / "health.json").write_text(
        json.dumps(_health_data(), ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "assess.json").write_text(
        json.dumps(_assess_data(), ensure_ascii=False), encoding="utf-8"
    )
    code = report_main(["--dir", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "health.html").is_file()
    assert (tmp_path / "assess.html").is_file()
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "health.html" in index
    assert "assess.html" in index


def test_cli_converts_directory_recursively(tmp_path: Path):
    """ack-audit 默认写 runs/<platform>-<date>/，--dir 必须能扫到子目录。"""
    runs = tmp_path / "runs"
    nested = runs / "bilibili-2026-11-12"
    nested.mkdir(parents=True)
    (nested / "health.json").write_text(
        json.dumps(_health_data(), ensure_ascii=False), encoding="utf-8"
    )
    code = report_main(["--dir", str(runs)])
    assert code == 0
    assert (nested / "health.html").is_file()
    index = (runs / "index.html").read_text(encoding="utf-8")
    assert "bilibili-2026-11-12/health.html" in index


def test_cli_skips_profile_directory(tmp_path: Path):
    """profile 目录里的 cookie JSON 不是报告，不能被转成 HTML。"""
    runs = tmp_path / "runs"
    profiles = runs / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "cookies.json").write_text('{"cookies": []}', encoding="utf-8")
    (runs / "health.json").write_text(
        json.dumps(_health_data(), ensure_ascii=False), encoding="utf-8"
    )
    code = report_main(["--dir", str(runs)])
    assert code == 0
    assert (runs / "health.html").is_file()
    assert not (profiles / "cookies.html").exists()


def test_cli_dir_out_directory(tmp_path: Path):
    """--out 不是 .html 时当作输出目录，保留子目录结构。"""
    runs = tmp_path / "runs"
    nested = runs / "bilibili-2026-11-12"
    nested.mkdir(parents=True)
    (nested / "health.json").write_text(
        json.dumps(_health_data(), ensure_ascii=False), encoding="utf-8"
    )
    out = tmp_path / "reports"
    code = report_main(["--dir", str(runs), "--out", str(out)])
    assert code == 0
    assert (out / "bilibili-2026-11-12" / "health.html").is_file()
    assert (out / "index.html").is_file()
    assert not (nested / "health.html").exists()
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "bilibili-2026-11-12/health.html" in index


def test_cli_dir_out_index_path(tmp_path: Path):
    """--out 以 .html 结尾时只决定索引位置，报告仍写输入目录。"""
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "health.json").write_text(
        json.dumps(_health_data(), ensure_ascii=False), encoding="utf-8"
    )
    index_path = tmp_path / "reports" / "index.html"
    code = report_main(["--dir", str(runs), "--out", str(index_path)])
    assert code == 0
    assert (runs / "health.html").is_file()
    assert index_path.is_file()


def _write_pair(tmp_path: Path, current: dict, baseline: dict) -> tuple[Path, Path]:
    current_path = tmp_path / "current.json"
    baseline_path = tmp_path / "baseline.json"
    current_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    baseline_path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    return current_path, baseline_path


def test_render_html_with_baseline_shows_diff():
    current = _health_data()
    baseline = _health_data()
    baseline["results"][1]["status"] = "OK"
    baseline["summary"] = {"OK": 2, "WARN": 0, "FAIL": 0, "SKIP": 0}

    html = render_html(current, baseline=baseline)
    assert "与基线对比" in html
    assert "退化" in html
    assert "FAIL" in html


def test_cli_baseline_regression_returns_1(tmp_path: Path, capsys):
    current = _health_data()
    baseline = _health_data()
    baseline["results"][1]["status"] = "OK"
    baseline["summary"] = {"OK": 2, "WARN": 0, "FAIL": 0, "SKIP": 0}
    current_path, baseline_path = _write_pair(tmp_path, current, baseline)

    code = report_main(
        ["--input", str(current_path), "--baseline", str(baseline_path)]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "退化 1" in out
    assert "registry · b" in out
    assert "OK -> FAIL" in out
    assert (tmp_path / "current.html").is_file()


def test_cli_baseline_no_change_returns_0(tmp_path: Path):
    current = _health_data()
    current_path, baseline_path = _write_pair(tmp_path, current, _health_data())
    code = report_main(
        ["--input", str(current_path), "--baseline", str(baseline_path)]
    )
    assert code == 0


def test_cli_baseline_kind_mismatch_returns_2(tmp_path: Path, capsys):
    current_path, baseline_path = _write_pair(
        tmp_path, _health_data(), _smoke_data()
    )
    code = report_main(
        ["--input", str(current_path), "--baseline", str(baseline_path)]
    )
    assert code == 2
    assert "基线类型不一致" in capsys.readouterr().err


def test_cli_baseline_rejects_directory_mode(tmp_path: Path, capsys):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(_health_data()), encoding="utf-8")
    code = report_main(
        ["--dir", str(tmp_path), "--baseline", str(baseline_path)]
    )
    assert code == 2
    assert "只支持 auto" in capsys.readouterr().err


def _write_timed(path: Path, data: dict, mtime: float) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _health_all_ok() -> dict:
    data = _health_data()
    data["results"] = [
        {"dimension": "registry", "platform": "a", "status": "OK", "detail": "ok"},
        {"dimension": "registry", "platform": "b", "status": "OK", "detail": "ok"},
    ]
    data["summary"] = {"OK": 2, "WARN": 0, "FAIL": 0, "SKIP": 0}
    return data


def test_cli_baseline_auto_picks_previous(tmp_path: Path, capsys):
    now = time.time()
    _write_timed(tmp_path / "20261113-090000.json", _health_all_ok(), now - 3600)
    current = _write_timed(tmp_path / "20261114-090000.json", _health_data(), now)

    code = report_main(["--input", str(current), "--baseline", "auto"])
    assert code == 1
    out = capsys.readouterr().out
    assert "基线:" in out
    assert "退化 1" in out
    assert "OK -> FAIL" in out


def test_cli_baseline_auto_skips_latest(tmp_path: Path, capsys):
    """latest.json 是副本，不能拿来和当前报告比。"""
    now = time.time()
    _write_timed(tmp_path / "20261113-090000.json", _health_data(), now - 3600)
    current = _write_timed(tmp_path / "20261114-090000.json", _health_all_ok(), now - 60)
    _write_timed(tmp_path / "latest.json", _health_all_ok(), now)

    code = report_main(["--input", str(current), "--baseline", "auto"])
    assert code == 0
    out = capsys.readouterr().out
    assert "改善 1" in out


def test_cli_baseline_auto_without_history(tmp_path: Path, capsys):
    current = _write_timed(tmp_path / "20261114-090000.json", _health_data(), time.time())
    code = report_main(["--input", str(current), "--baseline", "auto"])
    assert code == 0
    assert "没有找到上次的同类型报告" in capsys.readouterr().err


def _recipe_with_status(status: str) -> dict:
    return {
        "run_id": "abc",
        "recipe_id": "demo",
        "status": "done" if status == "ok" else "failed",
        "steps": [{"id": "fetch", "uses": "http_get", "status": status, "elapsed_ms": 10}],
    }


def test_cli_dir_baseline_auto_summarizes(tmp_path: Path, capsys):
    folder = tmp_path / "runs" / "demo"
    folder.mkdir(parents=True)
    now = time.time()
    _write_timed(folder / "20261113-090000.json", _recipe_with_status("ok"), now - 3600)
    _write_timed(folder / "20261114-090000.json", _recipe_with_status("failed"), now)

    code = report_main(["--dir", str(tmp_path / "runs"), "--baseline", "auto"])
    assert code == 1
    out = capsys.readouterr().out
    assert "demo/20261114-090000.json: 退化 1" in out
    assert "合计退化 1" in out
    assert (folder / "20261114-090000.html").is_file()
    assert (tmp_path / "runs" / "index.html").is_file()


def test_cli_dir_skips_latest_copy(tmp_path: Path, capsys):
    """latest.json 是副本，不参与索引，否则会重复且干扰基线选择。"""
    folder = tmp_path / "runs" / "demo"
    folder.mkdir(parents=True)
    now = time.time()
    _write_timed(folder / "20261113-090000.json", _recipe_with_status("ok"), now - 3600)
    _write_timed(folder / "latest.json", _recipe_with_status("ok"), now)

    code = report_main(["--dir", str(tmp_path / "runs")])
    assert code == 0
    assert not (folder / "latest.html").is_file()
    index = (tmp_path / "runs" / "index.html").read_text(encoding="utf-8")
    assert "20261113-090000" in index
    assert "latest" not in index


def test_cli_dir_with_only_latest_returns_2(tmp_path: Path, capsys):
    folder = tmp_path / "runs" / "demo"
    folder.mkdir(parents=True)
    _write_timed(folder / "latest.json", _recipe_with_status("ok"), time.time())

    code = report_main(["--dir", str(tmp_path / "runs")])
    assert code == 2
    assert "目录里没有 JSON 报告" in capsys.readouterr().err


def test_cli_rejects_missing_file(tmp_path: Path):
    assert report_main(["--input", str(tmp_path / "nope.json")]) == 2
