"""浏览器 profile 缓存治理 —— 只删缓存，绝不碰登录态。

为什么需要它（真实事故）
------------------------
持久化 profile 用久了，``browser-data`` 涨到 **3.08 GB**，大头是
``Cache / Code Cache / GrShaderCache``。每次冷启动 Chrome 都要校验这些缓存，
登录窗卡十几秒；用户以为程序卡死，反复重开，反而更容易触发风控。

白名单清理后：**3162 MB → 142 MB，登录态完好**。

安全边界（红线）
----------------
**只删**下列缓存目录（相对 profile 根）：

    Default/Cache, Default/Code Cache, Default/GPUCache,
    Default/DawnGraphiteCache, Default/DawnWebGPUCache,
    Default/GrShaderCache, Default/ShaderCache,
    Default/blob_storage, Default/CacheStorage,
    Default/Service Worker/CacheStorage,
    Default/Service Worker/ScriptCache,
    Default/Media Cache, Default/Application Cache,
    ShaderCache, GrShaderCache, GPUCache, GraphiteDawnCache,
    Crashpad, BrowserMetrics, component_crx_cache, extensions_crx_cache

**绝不碰**：

    Default/Network/Cookies, Default/Local Storage, Default/IndexedDB,
    Default/Preferences, Default/Login Data, Default/Web Data, Local State

双阈值 + 节流
-------------
- 缓存目录 > ``max_cache_mb``（默认 200MB）或 profile 总体 > ``max_total_mb``
  （默认 400MB）才动手；
- 24 小时内不重复清理（``min_interval_hours``），但缓存再次翻倍时强制清理；
- 任何异常全部吞掉，返回 ``freed_mb=0``，绝不阻塞主流程。

用法::

    from anti_crawl_kit.profile_cache import prune_profile_cache

    result = prune_profile_cache("profiles/douyin")
    print(result.reason, result.freed_mb)
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# 白名单：只允许删这些相对路径（大小写不敏感，Windows/Linux 通用）。
CACHE_DIRS: tuple[str, ...] = (
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/DawnGraphiteCache",
    "Default/DawnWebGPUCache",
    "Default/GrShaderCache",
    "Default/ShaderCache",
    "Default/blob_storage",
    "Default/CacheStorage",
    "Default/Service Worker/CacheStorage",
    "Default/Service Worker/ScriptCache",
    "Default/Media Cache",
    "Default/Application Cache",
    "ShaderCache",
    "GrShaderCache",
    "GPUCache",
    "GraphiteDawnCache",
    "Crashpad",
    "BrowserMetrics",
    "component_crx_cache",
    "extensions_crx_cache",
)

# 受保护路径：仅用于自检/文档，绝不允许出现在删除集合里。
PROTECTED_PATHS: tuple[str, ...] = (
    "Default/Network/Cookies",
    "Default/Network/Cookies-journal",
    "Default/Local Storage",
    "Default/IndexedDB",
    "Default/Preferences",
    "Default/Secure Preferences",
    "Default/Login Data",
    "Default/Web Data",
    "Local State",
)

_STATE_FILENAME = ".ack_prune_state.json"


@dataclass(frozen=True)
class PruneResult:
    """一次缓存清理的结果。"""

    profile: str
    reason: str                     # pruned / below_threshold / throttled / dry_run / profile_missing / error
    before_mb: float = 0.0
    after_mb: float = 0.0
    cache_mb: float = 0.0
    freed_mb: float = 0.0           # dry_run 时为「预计可释放」
    removed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    dry_run: bool = False
    error: str = ""

    @property
    def pruned(self) -> bool:
        return self.reason == "pruned" and self.freed_mb > 0.5

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "reason": self.reason,
            "before_mb": round(self.before_mb, 2),
            "after_mb": round(self.after_mb, 2),
            "cache_mb": round(self.cache_mb, 2),
            "freed_mb": round(self.freed_mb, 2),
            "removed": list(self.removed),
            "skipped": list(self.skipped),
            "dry_run": self.dry_run,
            "error": self.error,
        }


def _dir_size_bytes(path: Path) -> int:
    """递归统计目录大小；所有 IO 异常静默忽略（缓存目录经常边删边变）。"""
    total = 0
    try:
        stack = [path]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                total += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
    except Exception:
        return total
    return total


def _mb(size_bytes: int) -> float:
    return round(size_bytes / (1024 * 1024), 2)


def profile_cache_size_mb(profile_dir: str | Path) -> float:
    """profile 总大小（MB）；目录不存在返回 0。"""
    path = Path(profile_dir)
    if not path.is_dir():
        return 0.0
    return _mb(_dir_size_bytes(path))


def _cache_size_bytes(profile: Path) -> int:
    return sum(
        _dir_size_bytes(profile / rel)
        for rel in CACHE_DIRS
        if (profile / rel).is_dir()
    )


def _is_within(parent: Path, child: Path) -> bool:
    """child 是否在 parent 内（解析软链接，跨盘返回 False）。"""
    try:
        parent_real = os.path.realpath(parent)
        child_real = os.path.realpath(child)
        return os.path.commonpath([parent_real, child_real]) == parent_real
    except (ValueError, OSError):
        return False


def _load_state(state_file: Path) -> dict:
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(state_file: Path, *, now: float, cache_mb: float) -> None:
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(
                {"last_pruned_at": now, "cache_mb": round(cache_mb, 2)},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _candidate_dirs(profile: Path) -> tuple[list[Path], list[str]]:
    """返回 (可删除目录, 跳过原因)。所有路径必须落在 profile 内。"""
    candidates: list[Path] = []
    skipped: list[str] = []
    for rel in CACHE_DIRS:
        target = profile / rel
        if not target.is_dir():
            continue
        if not _is_within(profile, target):
            skipped.append(f"{rel}:outside_profile")
            continue
        # 防御：受保护路径即使误入白名单也不删。
        if any(_is_within(target, profile / protected) for protected in PROTECTED_PATHS):
            skipped.append(f"{rel}:contains_protected")
            continue
        candidates.append(target)
    return candidates, skipped


def prune_profile_cache(
    profile_dir: str | Path,
    *,
    max_cache_mb: float = 200.0,
    max_total_mb: float = 400.0,
    min_interval_hours: float = 24.0,
    state_file: str | Path | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> PruneResult:
    """按白名单清理浏览器缓存；失败/未达阈值一律返回结果，不抛异常。

    Args:
        profile_dir: Chrome user-data-dir。
        max_cache_mb: 缓存目录总大小阈值。
        max_total_mb: profile 总体大小阈值。
        min_interval_hours: 两次清理的最小间隔。
        state_file: 节流状态文件；默认 ``<profile>/.ack_prune_state.json``。
        dry_run: True 时只计算不删除（``freed_mb`` 为预计值）。
        now: 测试注入的当前时间戳。
    """
    profile = Path(profile_dir)
    now = time.time() if now is None else now
    if not profile.is_dir():
        return PruneResult(str(profile), "profile_missing")

    try:
        before_bytes = _dir_size_bytes(profile)
        cache_bytes = _cache_size_bytes(profile)
        before_mb = _mb(before_bytes)
        cache_mb = _mb(cache_bytes)

        # 阈值判定：缓存或总体任一超限才动手。
        if cache_mb < max_cache_mb and before_mb < max_total_mb:
            return PruneResult(
                str(profile),
                "below_threshold",
                before_mb=before_mb,
                after_mb=before_mb,
                cache_mb=cache_mb,
            )

        state_path = Path(state_file) if state_file else profile / _STATE_FILENAME
        last = _load_state(state_path).get("last_pruned_at")
        if last and min_interval_hours > 0:
            elapsed_hours = (now - float(last)) / 3600
            # 24h 内不重复清理；但缓存/总体再次翻倍时强制清理。
            if (
                elapsed_hours < min_interval_hours
                and cache_mb < max_cache_mb * 2
                and before_mb < max_total_mb * 2
            ):
                return PruneResult(
                    str(profile),
                    "throttled",
                    before_mb=before_mb,
                    after_mb=before_mb,
                    cache_mb=cache_mb,
                )

        candidates, skipped = _candidate_dirs(profile)
        if not candidates:
            return PruneResult(
                str(profile),
                "below_threshold",
                before_mb=before_mb,
                after_mb=before_mb,
                cache_mb=cache_mb,
                skipped=tuple(skipped),
            )

        removed: list[str] = []
        for target in candidates:
            rel = str(target.relative_to(profile)).replace("\\", "/")
            if dry_run:
                removed.append(rel)
                continue
            try:
                shutil.rmtree(target, ignore_errors=True)
                if not target.exists():
                    removed.append(rel)
                else:
                    skipped.append(f"{rel}:delete_failed")
            except Exception:
                skipped.append(f"{rel}:delete_failed")

        after_bytes = _dir_size_bytes(profile) if not dry_run else before_bytes - sum(
            _dir_size_bytes(profile / rel) for rel in removed
        )
        after_mb = _mb(max(0, after_bytes))
        freed_mb = round(max(0.0, before_mb - after_mb), 2)

        if dry_run:
            return PruneResult(
                str(profile),
                "dry_run",
                before_mb=before_mb,
                after_mb=before_mb,
                cache_mb=cache_mb,
                freed_mb=freed_mb,
                removed=tuple(removed),
                skipped=tuple(skipped),
                dry_run=True,
            )

        _write_state(state_path, now=now, cache_mb=cache_mb)
        return PruneResult(
            str(profile),
            "pruned",
            before_mb=before_mb,
            after_mb=after_mb,
            cache_mb=cache_mb,
            freed_mb=freed_mb,
            removed=tuple(removed),
            skipped=tuple(skipped),
        )
    except Exception as exc:  # 红线：缓存清理永不阻塞主流程
        return PruneResult(str(profile), "error", error=f"{type(exc).__name__}: {exc}")


def prune_profiles(
    profile_dirs: Iterable[str | Path],
    **kwargs,
) -> list[PruneResult]:
    """批量清理多个 profile（单个失败不影响其他）。"""
    return [prune_profile_cache(path, **kwargs) for path in profile_dirs]


__all__ = [
    "CACHE_DIRS",
    "PROTECTED_PATHS",
    "PruneResult",
    "prune_profile_cache",
    "prune_profiles",
    "profile_cache_size_mb",
]
