"""recipe 引擎测试（离线，不访问网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anti_crawl_kit.recipe import (
    Recipe,
    RecipeRun,
    eval_when,
    list_recipes,
    list_steps,
    load_recipe,
    register_step,
    resolve_value,
    resume_recipe,
    run_recipe,
    validate_recipe,
)


class FakeResponse:
    status_code = 200
    url = "https://example.com/"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeClient:
    def __init__(self, text: str = '<div class="item"></div>' * 3) -> None:
        self.text = text

    def get_text(self, url: str, **kwargs) -> FakeResponse:
        response = FakeResponse(self.text)
        response.url = url
        return response

    def get_json(self, url: str, **kwargs) -> FakeResponse:
        response = FakeResponse(self.text)
        response.url = url
        response.data = {"code": 0, "data": {"count": 3}}
        return response


def _recipe(steps: list[dict], *, allow_hosts: list[str] | None = None) -> Recipe:
    return Recipe.from_dict(
        {
            "id": "test",
            "allow_hosts": allow_hosts if allow_hosts is not None else ["example.com"],
            "inputs": {"url": {"type": "string"}},
            "steps": steps,
        }
    )


def test_builtin_steps_registered() -> None:
    steps = list_steps()
    for name in ("http_get", "count_html", "classify_html", "assert", "set_var"):
        assert name in steps


def test_run_recipe_done() -> None:
    recipe = _recipe(
        [
            {"id": "fetch", "uses": "http_get", "with": {"url": "${inputs.url}"}},
            {
                "id": "count",
                "uses": "count_html",
                "with": {"html": "${steps.fetch.text}", "selector": "div.item"},
            },
            {
                "id": "check",
                "uses": "assert",
                "with": {"value": "${steps.count.count}", "op": "gte", "expected": 3},
            },
        ]
    )
    run = run_recipe(
        recipe, inputs={"url": "https://example.com/"}, http_client=FakeClient()
    )
    assert run.status == "done"
    assert run.context["steps"]["count"]["count"] == 3
    assert run.context["steps"]["check"]["ok"] is True


def test_template_resolution() -> None:
    context = {"inputs": {"url": "https://x"}, "steps": {"a": {"n": 2}}}
    assert resolve_value("${inputs.url}", context) == "https://x"
    assert resolve_value("prefix-${steps.a.n}", context) == "prefix-2"
    assert resolve_value({"x": ["${steps.a.n}"]}, context) == {"x": [2]}
    with pytest.raises(KeyError, match="模板变量不存在"):
        resolve_value("${steps.nope.x}", context)


def test_assert_failure_stops() -> None:
    recipe = _recipe(
        [
            {
                "id": "bad",
                "uses": "assert",
                "with": {"value": 1, "op": "eq", "expected": 2},
            },
            {"id": "after", "uses": "log", "with": {"message": "should not run"}},
        ]
    )
    run = run_recipe(recipe, http_client=FakeClient())
    assert run.status == "failed"
    assert "断言失败" in run.error
    assert [step.id for step in run.steps] == ["bad"]


def test_when_skips_step() -> None:
    recipe = _recipe(
        [
            {"id": "a", "uses": "set_var", "with": {"name": "x", "value": 1}},
            {
                "id": "b",
                "uses": "log",
                "when": "vars.x == 2",
                "with": {"message": "skipped"},
            },
            {"id": "c", "uses": "log", "with": {"message": "ran"}},
        ]
    )
    run = run_recipe(recipe, http_client=FakeClient())
    assert run.status == "done"
    assert [step.status for step in run.steps] == ["ok", "skipped", "ok"]


def test_on_error_continue() -> None:
    recipe = _recipe(
        [
            {
                "id": "bad",
                "uses": "assert",
                "on_error": "continue",
                "with": {"value": 1, "op": "eq", "expected": 2},
            },
            {"id": "after", "uses": "log", "with": {"message": "ran"}},
        ]
    )
    run = run_recipe(recipe, http_client=FakeClient())
    assert run.status == "done"
    assert run.steps[0].status == "failed"
    assert run.steps[1].status == "ok"


def test_agent_handoff_and_resume() -> None:
    recipe = _recipe(
        [
            {"id": "fetch", "uses": "http_get", "with": {"url": "${inputs.url}"}},
            {
                "id": "pick",
                "kind": "agent",
                "ask": {
                    "prompt": "选出卡片选择器",
                    "schema": {
                        "type": "object",
                        "properties": {"selector": {"type": "string"}},
                        "required": ["selector"],
                    },
                    "context_keys": ["steps.fetch.status"],
                    "suggestions": [".item"],
                },
            },
            {
                "id": "count",
                "uses": "count_html",
                "with": {
                    "html": "${steps.fetch.text}",
                    "selector": "${steps.pick.selector}",
                },
            },
        ]
    )
    run = run_recipe(
        recipe, inputs={"url": "https://example.com/"}, http_client=FakeClient()
    )
    assert run.status == "needs_agent"
    assert run.handoff["step_id"] == "pick"
    assert run.handoff["prompt"] == "选出卡片选择器"
    assert run.handoff["context"]["steps.fetch.status"] == 200

    resumed = resume_recipe(run.to_dict(), {"selector": "div.item"})
    assert resumed.status == "done"
    assert resumed.context["steps"]["count"]["count"] == 3
    assert resumed.run_id == run.run_id


def test_on_error_agent_handoff() -> None:
    recipe = _recipe(
        [
            {
                "id": "boom",
                "uses": "not_registered",
                "on_error": "agent",
                "ask": {"prompt": "这个步骤失败了，给一个替代值"},
            },
            {
                "id": "after",
                "uses": "set_var",
                "with": {"name": "x", "value": "${steps.boom.ok}"},
            },
        ]
    )
    run = run_recipe(recipe, http_client=FakeClient())
    assert run.status == "needs_agent"
    assert run.handoff["reason"] == "step_failed"
    assert "not_registered" in run.handoff["error"]

    resumed = resume_recipe(run, {"ok": True})
    assert resumed.status == "done"
    assert resumed.context["vars"]["x"] is True


def test_custom_step_registration() -> None:
    @register_step("test_echo_step", description="测试用", replace=True)
    def _echo(params, runtime):
        return {"echo": params.get("value")}

    recipe = _recipe(
        [
            {
                "id": "e",
                "uses": "test_echo_step",
                "with": {"value": "${inputs.url}"},
            }
        ]
    )
    assert validate_recipe(recipe) == []
    run = run_recipe(recipe, inputs={"url": "hello"}, http_client=FakeClient())
    assert run.status == "done"
    assert run.context["steps"]["e"]["echo"] == "hello"


def test_allow_hosts_blocks() -> None:
    recipe = _recipe(
        [{"id": "fetch", "uses": "http_get", "with": {"url": "${inputs.url}"}}],
        allow_hosts=["allowed.com"],
    )
    run = run_recipe(
        recipe, inputs={"url": "https://evil.com/"}, http_client=FakeClient()
    )
    assert run.status == "failed"
    assert "allow_hosts" in run.error


def test_validate_recipe_duplicate_and_unknown() -> None:
    recipe = Recipe.from_dict(
        {
            "id": "bad",
            "steps": [
                {"id": "a", "uses": "not_exists"},
                {"id": "a", "uses": "log"},
            ],
        }
    )
    issues = validate_recipe(recipe)
    assert any("未注册步骤" in item for item in issues)
    assert any("重复" in item for item in issues)


def test_eval_when_rejects_unsafe() -> None:
    with pytest.raises(ValueError):
        eval_when("__import__('os').system('echo hi')", {})
    assert eval_when("steps.x == 1", {"steps": {"x": 1}}) is True


def test_recipe_roundtrip_and_resume_without_recipe() -> None:
    recipe = _recipe([{"id": "a", "uses": "log", "with": {"message": "hi"}}])
    run = run_recipe(recipe, http_client=FakeClient())
    data = run.to_dict()
    assert data["recipe"]["id"] == "test"
    restored = RecipeRun.from_dict(data)
    assert restored.recipe_snapshot == recipe.to_dict()


def test_load_recipe_json(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps(
            {
                "id": "demo",
                "steps": [{"id": "a", "uses": "log", "with": {"message": "hi"}}],
            }
        ),
        encoding="utf-8",
    )
    recipe = load_recipe(path)
    assert recipe.id == "demo"
    assert recipe.steps[0].uses == "log"


def test_list_recipes(tmp_path: Path) -> None:
    (tmp_path / "ok.json").write_text(
        json.dumps({"id": "ok", "steps": [{"id": "a", "uses": "log"}]}),
        encoding="utf-8",
    )
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    rows = list_recipes(tmp_path)
    assert len(rows) == 2
    assert any(row["valid"] is False for row in rows)
    assert any(row["id"] == "ok" for row in rows)
