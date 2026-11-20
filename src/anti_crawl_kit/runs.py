"""运行结果落盘：``runs/<recipe>/<时间戳>.json|html`` + ``latest`` 副本。

``ack-ui`` 和 ``ack-recipe`` 共用这套命名，保证「历史报告」和
``ack-report --baseline auto`` 看到的是同一份目录结构。

落盘失败不应该影响已经跑完的任务，所以这里只抛异常，由调用方决定
是告警还是退出。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .report import detect_kind, load_json, render_html

# --baseline auto 不当作基线的文件名：latest 是副本，index/report 是索引
AUTO_SKIP_STEMS = {"latest", "index", "report"}

# Windows 不允许用这些名字做目录/文件
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def slug(value: str) -> str:
    """把配方 id 变成安全的目录名，保留中文和常见符号。"""
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value).strip("._-")
    cleaned = cleaned[:60] or "recipe"
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def unique_stem(folder: Path) -> str:
    """同一秒内多次运行也不覆盖：YYYYmmdd-HHMMSS[-n]。"""
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = base
    index = 1
    while (folder / f"{stem}.json").exists() or (folder / f"{stem}.html").exists():
        index += 1
        stem = f"{base}-{index}"
    return stem


def find_previous_report(source: Path, kind: str) -> Path | None:
    """在同目录找上一次同类型报告。

    落盘文件名是 ``YYYYmmdd-HHMMSS``，但用户也可能手动命名，所以按 mtime
    倒序，文件名只作为同秒时的稳定次序。只找比当前文件更旧的，避免
    ``--dir`` 批量处理时旧报告反过来和新报告比。``latest.*`` 是副本，
    不参与比较，否则会拿当前报告和它自己比。
    """
    source_mtime = source.stat().st_mtime
    candidates = [
        path
        for path in source.parent.glob("*.json")
        if path.is_file()
        and path != source
        and path.stem.lower() not in AUTO_SKIP_STEMS
    ]

    def is_older(path: Path) -> bool:
        mtime = path.stat().st_mtime
        if mtime != source_mtime:
            return mtime < source_mtime
        return path.stem < source.stem

    candidates = [path for path in candidates if is_older(path)]
    candidates.sort(key=lambda path: (path.stat().st_mtime, path.stem), reverse=True)
    for path in candidates:
        try:
            if detect_kind(load_json(path)) == kind:
                return path
        except (OSError, ValueError):
            continue
    return None


def persist_run(
    result: Mapping[str, Any],
    runs_dir: str | Path,
    recipe_id: str,
    *,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """把一次运行写成时间戳 JSON/HTML，并原子更新 ``latest.*``。

    Args:
        result: ``RecipeRun.to_dict()`` 的结果。
        runs_dir: 报告根目录（``ack-ui --runs`` / ``ack-recipe --runs``）。
        recipe_id: 配方 id，决定子目录名。
        extra: 额外写入 JSON 顶层的字段，例如 ``{"ui": {...}}``。

    Returns:
        ``json`` / ``html`` / ``latest_json`` / ``latest_html`` 四个路径。
    """
    folder = Path(runs_dir) / slug(recipe_id)
    folder.mkdir(parents=True, exist_ok=True)
    stem = unique_stem(folder)

    payload = dict(result)
    if extra:
        payload.update(extra)

    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    json_path = folder / f"{stem}.json"
    html_path = folder / f"{stem}.html"
    json_path.write_text(text, encoding="utf-8")
    html_path.write_text(
        render_html(payload, kind="recipe", title=f"配方 · {recipe_id}"),
        encoding="utf-8",
    )

    # latest 先写临时文件再替换，避免页面读到写了一半的文件
    for source, target in (
        (json_path, folder / "latest.json"),
        (html_path, folder / "latest.html"),
    ):
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        os.replace(temp, target)

    return {
        "json": json_path,
        "html": html_path,
        "latest_json": folder / "latest.json",
        "latest_html": folder / "latest.html",
    }
