"""``ack init``：生成的骨架必须能解析、纯逻辑模板必须能直接跑。"""

from __future__ import annotations

from pathlib import Path

import pytest

from anti_crawl_kit.recipe import load_recipe, run_recipe
from anti_crawl_kit.tools.init_cli import main as init_main

pytest.importorskip("yaml", reason="生成配方需要 [config] extra")


def test_logic_recipe_is_parseable_and_runs(tmp_path: Path, capsys):
    target = tmp_path / "my_recipe.yaml"
    assert init_main(["recipe", "--out", str(target)]) == 0
    assert target.is_file()

    recipe = load_recipe(target)
    assert recipe.id == "my_recipe"
    run = run_recipe(recipe, inputs={"name": "world"})
    assert run.status == "done", run.error
    assert run.context["steps"]["greet"]["value"] == "hello world"

    out = capsys.readouterr().out
    assert "[OK] 已生成配方" in out
    assert "ack-recipe" in out


def test_http_template_has_allow_hosts(tmp_path: Path):
    target = tmp_path / "http.yaml"
    assert init_main(["recipe", "--type", "http", "--out", str(target)]) == 0
    recipe = load_recipe(target)
    assert recipe.id == "my_http_recipe"
    assert "quotes.toscrape.com" in recipe.allow_hosts
    assert [step.uses for step in recipe.steps] == [
        "http_get",
        "assert",
        "assert",
    ]


def test_browser_template_is_parseable(tmp_path: Path):
    target = tmp_path / "browser.yaml"
    assert init_main(["recipe", "--type", "browser", "--out", str(target)]) == 0
    recipe = load_recipe(target)
    assert recipe.id == "my_browser_recipe"
    assert recipe.steps[0].uses == "browser_goto"
    assert any(step.uses == "browser_count" for step in recipe.steps)


def test_existing_file_is_not_overwritten(tmp_path: Path, capsys):
    target = tmp_path / "keep.yaml"
    target.write_text("id: mine\nsteps: []\n", encoding="utf-8")

    assert init_main(["recipe", "--out", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == "id: mine\nsteps: []\n"
    assert "未覆盖" in capsys.readouterr().out


def test_force_overwrites(tmp_path: Path):
    target = tmp_path / "keep.yaml"
    target.write_text("id: mine\nsteps: []\n", encoding="utf-8")

    assert init_main(["recipe", "--out", str(target), "--force"]) == 0
    assert load_recipe(target).id == "my_recipe"


def test_default_name_by_type(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert init_main(["recipe"]) == 0
    assert (tmp_path / "my_recipe.yaml").is_file()

    assert init_main(["recipe", "--type", "http"]) == 0
    assert (tmp_path / "my_http_recipe.yaml").is_file()


def test_invalid_kind_rejected(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        init_main(["recipe", "--type", "nope", "--out", str(tmp_path / "x.yaml")])
    assert exc.value.code == 2


def test_health_init_delegates(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert init_main(["health"]) == 0
    assert (tmp_path / "ack_health.yaml").is_file()
    assert "配置模板" in capsys.readouterr().out


def test_health_init_honors_out(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "nested" / "my_health.yaml"

    assert init_main(["health", "--out", str(target)]) == 0

    assert target.is_file()
    assert "platforms" in target.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert str(target) in out
    assert "ack-health --config" in out


def test_health_init_does_not_overwrite(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "keep.yaml"
    target.write_text("已存在", encoding="utf-8")

    assert init_main(["health", "--out", str(target)]) == 0

    assert target.read_text(encoding="utf-8") == "已存在"
    assert "未覆盖" in capsys.readouterr().out
