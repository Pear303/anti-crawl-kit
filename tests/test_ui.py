"""ack-ui 服务层与 HTTP API 测试（不启动真实浏览器）。"""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from anti_crawl_kit.ui import UiService, _default_recipes, make_handler

pytest.importorskip("yaml", reason="配方测试需要 [config] extra")

LOCAL_RECIPE = """\
id: local_demo
title: 本地演示
inputs:
  name:
    type: string
    default: world
steps:
  - id: greet
    uses: set_var
    with:
      name: greeting
      value: "hello ${inputs.name}"
  - id: check
    uses: assert
    with:
      value: "${steps.greet.value}"
      expected: "hello world"
      op: eq
"""

AGENT_RECIPE = """\
id: agent_demo
title: 带检查点
steps:
  - id: prepare
    uses: set_var
    with:
      name: x
      value: 1
  - id: decide
    kind: agent
    ask:
      prompt: 确认继续吗
      context_keys: [steps.prepare.value]
  - id: after
    uses: assert
    with:
      value: "${agent.decide}"
      expected: ok
      op: eq
"""


@pytest.fixture()
def service(tmp_path: Path) -> UiService:
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "local_demo.yaml").write_text(LOCAL_RECIPE, encoding="utf-8")
    (recipes / "agent_demo.yaml").write_text(AGENT_RECIPE, encoding="utf-8")
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "demo.html").write_text("<html>demo</html>", encoding="utf-8")
    instance = UiService(recipes, tmp_path / "runs")
    yield instance
    instance.close()


def _wait(service: UiService, job_id: str, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = service.snapshot(job_id)
        if snapshot["status"] in ("done", "failed", "needs_agent"):
            return snapshot
        time.sleep(0.02)
    raise TimeoutError(service.snapshot(job_id))


def test_recipes_and_detail(service: UiService):
    rows = service.recipes()
    assert {row["path"] for row in rows} == {"local_demo.yaml", "agent_demo.yaml"}
    assert all(row["valid"] for row in rows)

    detail = service.recipe_detail("local_demo.yaml")
    assert detail["id"] == "local_demo"
    assert detail["inputs"]["name"]["default"] == "world"
    assert detail["path"] == "local_demo.yaml"


def test_start_and_detail_by_recipe_id(service: UiService):
    """前端从 /api/recipes 拿到 id 后，直接用 id 也能启动和查看配方。"""
    detail = service.recipe_detail("local_demo")
    assert detail["id"] == "local_demo"
    assert detail["path"] == "local_demo.yaml"

    job_id = service.start("local_demo", {"name": "world"})
    snapshot = _wait(service, job_id)
    assert snapshot["status"] == "done", snapshot
    assert snapshot["recipe_id"] == "local_demo"
    # 作业内部统一存相对路径，恢复执行不依赖调用方传的是什么
    assert snapshot["recipe_path"] == "local_demo.yaml"


def test_run_emits_events_and_report(service: UiService):
    job_id = service.start("local_demo.yaml", {"name": "world"})
    snapshot = _wait(service, job_id)
    assert snapshot["status"] == "done", snapshot
    assert snapshot["has_result"] is True

    events = service.events(job_id)
    types = [event["type"] for event in events]
    assert types[0] == "step_start"
    assert types[-1] == "done"
    assert types.count("step_ok") == 2
    assert all("at" in event for event in events)

    html = service.report_html(job_id)
    assert "local_demo" in html
    assert "配方" in html


def test_run_persists_latest_report(service: UiService):
    job_id = service.start("local_demo", {"name": "world"})
    snapshot = _wait(service, job_id)
    assert snapshot["status"] == "done", snapshot

    folder = service.runs_dir / "local_demo"
    latest_json = folder / "latest.json"
    latest_html = folder / "latest.html"
    assert latest_json.is_file()
    assert latest_html.is_file()

    payload = json.loads(latest_json.read_text(encoding="utf-8"))
    assert payload["recipe_id"] == "local_demo"
    assert payload["status"] == "done"
    assert payload["ui"]["job_id"] == job_id
    assert "local_demo" in latest_html.read_text(encoding="utf-8")

    rows = service.runs()
    row = next(item for item in rows if item["name"] == "local_demo")
    assert row["kind"] == "recipe"
    assert row["url"].endswith("/latest.html")
    # 时间戳报告 + latest 副本
    assert row["count"] >= 2


def test_snapshot_done_means_report_already_persisted(service: UiService, monkeypatch):
    """status 变成 done 时报告必须已经落盘，前端刷新不会看到空的历史列表。"""
    import anti_crawl_kit.ui as ui_module

    real_render = ui_module.render_html

    def slow_render(*args, **kwargs):
        time.sleep(0.15)
        return real_render(*args, **kwargs)

    monkeypatch.setattr(ui_module, "render_html", slow_render)
    job_id = service.start("local_demo", {"name": "world"})
    snapshot = _wait(service, job_id)
    assert snapshot["status"] == "done"
    assert (service.runs_dir / "local_demo" / "latest.html").is_file()


def test_default_recipes_falls_back_to_bundled(tmp_path: Path, monkeypatch):
    """pip 安装后没有 examples/，默认必须回退到包内 recipes。"""
    monkeypatch.chdir(tmp_path)
    resolved = Path(_default_recipes())
    assert resolved.is_dir()
    assert (resolved / "local_demo.yaml").is_file()


def test_runs_prefers_latest_even_if_mtime_is_older(service: UiService):
    """Windows 的 mtime 精度可能让 latest 和时间戳打平，必须显式优先 latest。"""
    job_id = service.start("local_demo", {"name": "world"})
    assert _wait(service, job_id)["status"] == "done"
    latest = service.runs_dir / "local_demo" / "latest.html"
    old = time.time() - 3600
    os.utime(latest, (old, old))

    row = next(item for item in service.runs() if item["name"] == "local_demo")
    assert row["url"].endswith("/latest.html")


def test_pure_logic_recipe_does_not_require_http_extra(service: UiService, monkeypatch):
    """内置 demo 不能因为没装 [http] 就失败。"""

    def unavailable():
        raise ModuleNotFoundError("No module named 'curl_cffi'")

    monkeypatch.setattr(service, "_client", unavailable)
    job_id = service.start("local_demo", {"name": "world"})
    assert _wait(service, job_id)["status"] == "done"


def test_needs_agent_not_persisted_until_resume(service: UiService):
    job_id = service.start("agent_demo.yaml", {})
    assert _wait(service, job_id)["status"] == "needs_agent"
    # 中间态不落盘，避免 latest 指向未完成的报告
    assert not (service.runs_dir / "agent_demo").exists()

    service.resume(job_id, "ok")
    assert _wait(service, job_id)["status"] == "done"
    assert (service.runs_dir / "agent_demo" / "latest.json").is_file()


def test_agent_checkpoint_and_resume(service: UiService):
    job_id = service.start("agent_demo.yaml", {})
    snapshot = _wait(service, job_id)
    assert snapshot["status"] == "needs_agent"

    result = service.require(job_id).result
    assert result is not None
    assert result["handoff"]["step_id"] == "decide"
    assert result["handoff"]["prompt"] == "确认继续吗"

    service.resume(job_id, "ok")
    snapshot = _wait(service, job_id)
    assert snapshot["status"] == "done", snapshot


def test_path_guard_blocks_escape(service: UiService):
    with pytest.raises((PermissionError, FileNotFoundError)):
        service.recipe_detail("../outside.yaml")
    with pytest.raises(FileNotFoundError):
        service.start("missing.yaml")


def test_runs_list(service: UiService):
    rows = service.runs()
    assert len(rows) == 1
    assert rows[0]["name"] == "demo"
    assert rows[0]["url"].startswith("/runs/")


def test_unknown_job_raises(service: UiService):
    with pytest.raises(KeyError):
        service.snapshot("nope")


def _http(port: int, method: str, path: str, payload: dict | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, data


def test_http_api_end_to_end(service: UiService):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        status, body = _http(port, "GET", "/api/recipes")
        assert status == 200
        assert len(json.loads(body)) == 2

        status, body = _http(port, "GET", "/")
        assert status == 200
        assert "ack-ui" in body.decode("utf-8")

        # 用 /api/recipes 返回的 id 启动，而不是文件路径
        status, body = _http(
            port,
            "POST",
            "/api/run",
            {"recipe": "local_demo", "inputs": {"name": "world"}},
        )
        assert status == 200
        job_id = json.loads(body)["job_id"]

        deadline = time.time() + 8
        snapshot = {}
        while time.time() < deadline:
            status, body = _http(port, "GET", f"/api/jobs/{job_id}")
            snapshot = json.loads(body)
            if snapshot["status"] in ("done", "failed"):
                break
            time.sleep(0.03)
        assert snapshot["status"] == "done", snapshot

        status, body = _http(port, "GET", f"/api/jobs/{job_id}/report")
        assert status == 200
        assert "local_demo" in body.decode("utf-8")

        status, body = _http(port, "GET", "/runs/demo.html")
        assert status == 200
        assert b"demo" in body

        status, _ = _http(port, "GET", "/runs/../secret.html")
        assert status in (403, 404)
    finally:
        server.shutdown()
        server.server_close()
