"""profile 缓存治理测试（离线，不启动浏览器）。"""

from __future__ import annotations

import json
from pathlib import Path

from anti_crawl_kit.profile_cache import (
    CACHE_DIRS,
    PROTECTED_PATHS,
    prune_profile_cache,
    profile_cache_size_mb,
)


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def _make_profile(root: Path) -> Path:
    profile = root / "browser-data"
    _write(profile / "Default/Cache/data_0", 300_000)
    _write(profile / "Default/Code Cache/js/data_1", 300_000)
    _write(profile / "Default/Network/Cookies", 1_000)
    _write(profile / "Default/Local Storage/leveldb/000001.log", 1_000)
    _write(profile / "Default/IndexedDB/000001.log", 1_000)
    _write(profile / "Default/Preferences", 1_000)
    return profile


def test_below_threshold_keeps_everything(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path)
    result = prune_profile_cache(profile, max_cache_mb=200, max_total_mb=400)
    assert result.reason == "below_threshold"
    assert result.freed_mb == 0
    assert (profile / "Default/Cache/data_0").exists()


def test_prunes_whitelist_and_keeps_login_state(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path)
    result = prune_profile_cache(
        profile,
        max_cache_mb=0.1,
        max_total_mb=0.1,
        min_interval_hours=0,
    )
    assert result.reason == "pruned"
    assert result.pruned is True
    assert result.freed_mb > 0
    assert not (profile / "Default/Cache").exists()
    assert not (profile / "Default/Code Cache").exists()

    # 登录态 / 本地存储 / 配置必须完好
    assert (profile / "Default/Network/Cookies").exists()
    assert (profile / "Default/Local Storage/leveldb/000001.log").exists()
    assert (profile / "Default/IndexedDB/000001.log").exists()
    assert (profile / "Default/Preferences").exists()


def test_throttle_skips_second_run(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path)
    first = prune_profile_cache(
        profile,
        max_cache_mb=0.1,
        max_total_mb=0.1,
        min_interval_hours=24,
        now=1_000_000,
    )
    assert first.reason == "pruned"

    # 重新制造缓存，但没到「翻倍」阈值 → 24h 内应跳过
    _write(profile / "Default/Cache/data_0", 150_000)
    second = prune_profile_cache(
        profile,
        max_cache_mb=0.1,
        max_total_mb=0.1,
        min_interval_hours=24,
        now=1_000_000 + 3600,
    )
    assert second.reason == "throttled"
    assert (profile / "Default/Cache/data_0").exists()


def test_throttle_forced_when_cache_doubles(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path)
    prune_profile_cache(
        profile,
        max_cache_mb=0.1,
        max_total_mb=0.1,
        min_interval_hours=24,
        now=1_000_000,
    )
    _write(profile / "Default/Cache/data_0", 400_000)
    forced = prune_profile_cache(
        profile,
        max_cache_mb=0.1,
        max_total_mb=0.1,
        min_interval_hours=24,
        now=1_000_000 + 3600,
    )
    assert forced.reason == "pruned"


def test_dry_run_does_not_delete(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path)
    result = prune_profile_cache(
        profile,
        max_cache_mb=0.1,
        max_total_mb=0.1,
        dry_run=True,
    )
    assert result.reason == "dry_run"
    assert result.freed_mb > 0
    assert (profile / "Default/Cache/data_0").exists()


def test_missing_profile_is_safe(tmp_path: Path) -> None:
    result = prune_profile_cache(tmp_path / "nope")
    assert result.reason == "profile_missing"
    assert result.freed_mb == 0


def test_state_file_written(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path)
    state = tmp_path / "state.json"
    prune_profile_cache(
        profile,
        max_cache_mb=0.1,
        max_total_mb=0.1,
        state_file=state,
        min_interval_hours=0,
    )
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["last_pruned_at"] > 0


def test_whitelist_never_overlaps_protected() -> None:
    cache = {item.lower() for item in CACHE_DIRS}
    protected = {item.lower() for item in PROTECTED_PATHS}
    assert not (cache & protected)
    for cache_path in cache:
        for protected_path in protected:
            assert not protected_path.startswith(cache_path + "/")
            assert not cache_path.startswith(protected_path + "/")


def test_profile_cache_size_mb(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path)
    assert profile_cache_size_mb(profile) > 0
    assert profile_cache_size_mb(tmp_path / "missing") == 0
