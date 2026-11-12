"""仓库一致性检查：文档数字、内链、依赖声明。

这些是「可机检」的部分，避免文档和代码再次漂移。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", "build", "dist", ".pytest_cache", ".git", "__pycache__"}


def _iter_markdown() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*.md")
        if not any(part in SKIP_DIRS for part in path.parts)
    ]


def test_readme_example_count_matches_files() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # 兼容几种写法：目录树「examples/ # N 个可运行示例」、正文「`examples/` 有 N 个脚本」等。
    match = re.search(
        r"examples/[^\n]{0,30}?(\d+)\s*个(?:可运行示例|能直接跑的脚本|可直接跑的脚本|脚本)",
        readme,
    )
    assert match, "README 里找不到 examples 的脚本数量说明"
    actual = len(list((ROOT / "examples").glob("*.py")))
    assert int(match.group(1)) == actual, f"README 写 {match.group(1)}，实际 {actual}"


def test_all_export_count_snapshot() -> None:
    import anti_crawl_kit

    assert len(anti_crawl_kit.__all__) == 137


def test_requirements_is_generated() -> None:
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "自动生成" in text
    assert "scripts/gen_requirements.py" in text


def test_requirements_matches_pyproject() -> None:
    try:
        import tomllib
    except ModuleNotFoundError:
        pytest.skip("读取 pyproject.toml 需要 Python 3.11+")

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for dep in extras["all"] + extras["dev"]:
        assert dep in req, f"requirements.txt 缺少 {dep}"


def test_markdown_links_exist() -> None:
    broken: list[str] = []
    for md in _iter_markdown():
        text = md.read_text(encoding="utf-8", errors="ignore")
        for raw in re.findall(r"\]\(([^)]+)\)", text):
            target = raw.split("#")[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#", "file:///")):
                continue
            target = target.split()[0]
            candidates = [md.parent / target, ROOT / target]
            if not any(path.exists() for path in candidates):
                broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not broken, "断链:\n" + "\n".join(broken)


def test_no_sensitive_profiles_in_repo() -> None:
    """profiles/ 下不应有 Cookies / Login Data 等真实登录态。

    只看 git 跟踪的文件：本地跑 examples 会生成 ``profiles/<platform>/``，
    那是临时浏览器数据，本来就不该进仓库，也不该让本地测试变红。
    """
    try:
        output = subprocess.run(
            ["git", "ls-files", "-z", "--", "profiles"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return
    sensitive = [
        item
        for item in output.split("\0")
        if item
        and Path(item).name
        in {"Cookies", "Login Data", "Login Data For Account", "Web Data"}
    ]
    assert not sensitive, f"发现真实登录态文件: {sensitive}"
