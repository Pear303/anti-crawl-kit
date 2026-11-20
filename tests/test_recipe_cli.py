"""``ack-recipe`` CLI：运行、落盘、baseline auto。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anti_crawl_kit.tools.recipe_cli import main as recipe_main

pytest.importorskip("yaml", reason="配方测试需要 [config] extra")


DEMO = """\
id: demo
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
      op: contains
      expected: "hello"
"""

BUMP = """\
id: bump
inputs:
  value:
    type: integer
    default: 2
steps:
  - id: check
    uses: assert
    with:
      value: "${inputs.value}"
      op: gte
      expected: 2
"""

AGENT = """\
id: agent_demo
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
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _latest(runs: Path, recipe_id: str) -> dict:
    return json.loads(
        (runs / recipe_id / "latest.json").read_text(encoding="utf-8")
    )


def test_cli_runs_and_persists(tmp_path: Path, capsys):
    recipe = _write(tmp_path / "demo.yaml", DEMO)
    runs = tmp_path / "runs"

    code = recipe_main([str(recipe), "--runs", str(runs)])
    assert code == 0
    assert (runs / "demo" / "latest.json").is_file()
    assert (runs / "demo" / "latest.html").is_file()

    payload = _latest(runs, "demo")
    assert payload["recipe_id"] == "demo"
    assert payload["status"] == "done"
    assert payload["cli"]["with_browser"] is False
    assert payload["context"]["vars"]["greeting"] == "hello world"
    assert "[OK] 报告:" in capsys.readouterr().out


def test_cli_inputs_are_parsed(tmp_path: Path):
    recipe = _write(tmp_path / "demo.yaml", DEMO)
    runs = tmp_path / "runs"

    code = recipe_main([str(recipe), "--runs", str(runs), "--inputs", "name=alice"])
    assert code == 0
    assert _latest(runs, "demo")["context"]["inputs"]["name"] == "alice"


def test_cli_rejects_invalid_inputs(tmp_path: Path, capsys):
    recipe = _write(tmp_path / "demo.yaml", DEMO)
    code = recipe_main([str(recipe), "--inputs", "oops"])
    assert code == 2
    assert "KEY=VALUE" in capsys.readouterr().err


def test_cli_missing_recipe_returns_2(tmp_path: Path, capsys):
    code = recipe_main([str(tmp_path / "nope.yaml")])
    assert code == 2
    assert "配方加载失败" in capsys.readouterr().err


REQUIRED = """\
id: need_url
inputs:
  url:
    type: string
    required: true
steps:
  - id: check
    uses: assert
    with:
      value: "${inputs.url}"
      op: contains
      expected: "/"
"""


def test_cli_missing_required_input_returns_2(tmp_path: Path, capsys):
    recipe = _write(tmp_path / "need.yaml", REQUIRED)
    code = recipe_main([str(recipe), "--runs", str(tmp_path / "runs")])
    assert code == 2
    err = capsys.readouterr().err
    assert "缺少必填输入: url" in err
    assert "--inputs url=" in err


def test_cli_required_input_can_be_provided(tmp_path: Path):
    recipe = _write(tmp_path / "need.yaml", REQUIRED)
    runs = tmp_path / "runs"
    code = recipe_main([str(recipe), "--runs", str(runs), "--inputs", "url=https://a/b"])
    assert code == 0
    assert _latest(runs, "need_url")["status"] == "done"


def test_cli_needs_agent_returns_1_and_does_not_persist(tmp_path: Path, capsys):
    recipe = _write(tmp_path / "agent.yaml", AGENT)
    runs = tmp_path / "runs"

    code = recipe_main([str(recipe), "--runs", str(runs)])
    assert code == 1
    assert not (runs / "agent_demo").exists()
    err = capsys.readouterr().err
    assert "needs_agent" in err
    assert "确认继续吗" in err


def test_cli_baseline_auto_detects_regression(tmp_path: Path, capsys):
    recipe = _write(tmp_path / "bump.yaml", BUMP)
    runs = tmp_path / "runs"

    assert recipe_main([str(recipe), "--runs", str(runs), "--inputs", "value=2"]) == 0
    code = recipe_main(
        [
            str(recipe),
            "--runs",
            str(runs),
            "--inputs",
            "value=1",
            "--baseline",
            "auto",
        ]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "退化 1" in out
    assert "check: ok -> failed" in out


def test_cli_baseline_explicit_file(tmp_path: Path):
    recipe = _write(tmp_path / "bump.yaml", BUMP)
    runs = tmp_path / "runs"

    assert recipe_main([str(recipe), "--runs", str(runs), "--inputs", "value=2"]) == 0
    baseline = runs / "bump" / "latest.json"
    code = recipe_main(
        [
            str(recipe),
            "--runs",
            str(runs),
            "--inputs",
            "value=1",
            "--baseline",
            str(baseline),
        ]
    )
    assert code == 1


def test_cli_baseline_kind_mismatch_returns_2(tmp_path: Path, capsys):
    recipe = _write(tmp_path / "demo.yaml", DEMO)
    runs = tmp_path / "runs"
    other = tmp_path / "health.json"
    other.write_text(
        json.dumps(
            {
                "summary": {"OK": 1, "WARN": 0, "FAIL": 0, "SKIP": 0},
                "results": [
                    {"dimension": "registry", "platform": "a", "status": "OK"}
                ],
            }
        ),
        encoding="utf-8",
    )

    code = recipe_main(
        [str(recipe), "--runs", str(runs), "--baseline", str(other)]
    )
    assert code == 2
    assert "基线类型不一致" in capsys.readouterr().err


def test_cli_baseline_auto_without_history(tmp_path: Path, capsys):
    recipe = _write(tmp_path / "demo.yaml", DEMO)
    runs = tmp_path / "runs"

    code = recipe_main(
        [str(recipe), "--runs", str(runs), "--baseline", "auto"]
    )
    assert code == 0
    assert "没有找到上一次的 recipe 报告" in capsys.readouterr().err
