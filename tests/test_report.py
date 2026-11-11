"""HTML 报告：类型识别、渲染、转义、CLI。"""

from __future__ import annotations

import json
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


def test_cli_rejects_missing_file(tmp_path: Path):
    assert report_main(["--input", str(tmp_path / "nope.json")]) == 2
