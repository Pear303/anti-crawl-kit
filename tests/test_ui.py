"""ack-ui 服务层与 HTTP API 测试（不启动真实浏览器）。"""

from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from anti_crawl_kit.ui import UiService, make_handler

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

        status, body = _http(
            port,
            "POST",
            "/api/run",
            {"recipe": "local_demo.yaml", "inputs": {"name": "world"}},
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
