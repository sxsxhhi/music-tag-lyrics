# -*- coding: utf-8 -*-
"""缓存路径五级候选链与零侵入系统盘回归测试（P1-01 / v1.8.2）。"""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from core.utils.cache_paths import (
    get_cache_dir,
    get_cache_path,
    set_cache_dir_override,
    _warn_system_cache_once,
)
import core.utils.cache_paths as cache_paths_mod


def test_override_takes_precedence_and_creates_dir():
    """set_cache_dir_override 生效且自动创建目录。"""
    temp_parent = Path(tempfile.mkdtemp(prefix="mtl_test_cache_override_"))
    test_sub = temp_parent / "nested" / "override_cache"
    assert not test_sub.exists()
    try:
        set_cache_dir_override(test_sub)
        p = get_cache_dir()
        assert p == test_sub
        assert test_sub.is_dir()
    finally:
        set_cache_dir_override(None)
        shutil.rmtree(temp_parent, ignore_errors=True)


def test_env_var_takes_precedence():
    """MTL_CACHE_DIR 环境变量优先于 repo_cache 生效。"""
    temp_env = Path(tempfile.mkdtemp(prefix="mtl_test_cache_env_"))
    old_env = os.environ.get("MTL_CACHE_DIR")
    try:
        set_cache_dir_override(None)
        os.environ["MTL_CACHE_DIR"] = str(temp_env)
        p = get_cache_dir()
        assert p.resolve() == temp_env.resolve()
    finally:
        if old_env is not None:
            os.environ["MTL_CACHE_DIR"] = old_env
        else:
            os.environ.pop("MTL_CACHE_DIR", None)
        set_cache_dir_override(None)
        shutil.rmtree(temp_env, ignore_errors=True)


def test_default_cache_dir_under_repo_and_not_localappdata():
    """核心断言：默认路径位于 skill 仓库根目录下的 .cache/，绝不包含 LOCALAPPDATA。"""
    old_env = os.environ.get("MTL_CACHE_DIR")
    try:
        set_cache_dir_override(None)
        if "MTL_CACHE_DIR" in os.environ:
            del os.environ["MTL_CACHE_DIR"]

        p = get_cache_dir()
        # 1. 必须位于仓库根目录下
        assert REPO_ROOT.resolve() in p.resolve().parents, f"Cache path {p} not under repo root {REPO_ROOT}"
        assert p.resolve() == (REPO_ROOT / ".cache").resolve()

        # 2. 绝对不得回退到 Windows 平台的系统缓存目录
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            platform_cache = (Path(local_app_data) / "music-tag-lyrics").resolve()
            assert p.resolve() != platform_cache, (
                f"Cache path {p} leaked into platform cache ({platform_cache})"
            )
            # 若仓库本身不在 LOCALAPPDATA 内部（如 F 盘工作区），路径绝不包含 LOCALAPPDATA
            appdata_resolved = Path(local_app_data).resolve()
            if appdata_resolved not in REPO_ROOT.resolve().parents and REPO_ROOT.resolve() != appdata_resolved:
                assert str(appdata_resolved).lower() not in str(p.resolve()).lower(), (
                    f"Cache path {p} leaked into LOCALAPPDATA ({local_app_data})"
                )

    finally:
        if old_env is not None:
            os.environ["MTL_CACHE_DIR"] = old_env
        else:
            os.environ.pop("MTL_CACHE_DIR", None)
        set_cache_dir_override(None)


def test_unwritable_env_var_graceful_fallback():
    """MTL_CACHE_DIR 指向不可写路径时平滑回退，不抛出异常。"""
    old_env = os.environ.get("MTL_CACHE_DIR")
    temp_parent = Path(tempfile.mkdtemp(prefix="mtl_test_unwritable_"))
    try:
        set_cache_dir_override(None)
        # 创建一个普通文件，在其下创建子路径必然抛出 FileExistsError/NotADirectoryError（全平台通用）
        blocked_file = temp_parent / "blocked_file"
        blocked_file.write_text("x", encoding="utf-8")
        os.environ["MTL_CACHE_DIR"] = str(blocked_file / "unwritable_sub")
        p = get_cache_dir()
        # 应当回退到 repo 缓存或更低层级，而不抛出任何异常
        assert p is not None
        assert p.is_dir()
        assert p.resolve() == (REPO_ROOT / ".cache").resolve()
    finally:
        if old_env is not None:
            os.environ["MTL_CACHE_DIR"] = old_env
        else:
            os.environ.pop("MTL_CACHE_DIR", None)
        set_cache_dir_override(None)
        shutil.rmtree(temp_parent, ignore_errors=True)


def test_get_cache_path():
    """get_cache_path(name) 严格等于 get_cache_dir() / name。"""
    set_cache_dir_override(None)
    cd = get_cache_dir()
    cp = get_cache_path("itunes-cache.json")
    assert cp == cd / "itunes-cache.json"


def test_system_cache_warning_emitted_once():
    """当回退至系统盘默认路径时，向 stderr 输出且仅输出一次告警。"""
    old_warned = cache_paths_mod._WARNED_SYSTEM_CACHE
    cache_paths_mod._WARNED_SYSTEM_CACHE = False
    stderr_captured = []

    def mock_stderr_write(msg: str):
        stderr_captured.append(msg)

    test_target = Path("C:/Users/fake/AppData/Local/music-tag-lyrics")
    try:
        with patch.object(sys.stderr, "write", side_effect=mock_stderr_write):
            # 第一次调用
            _warn_system_cache_once(test_target)
            assert len(stderr_captured) == 1
            assert "⚠️ 缓存回退至系统盘目录" in stderr_captured[0]
            assert str(test_target) in stderr_captured[0]

            # 第二次调用，哨兵已生效，不应重复输出
            _warn_system_cache_once(test_target)
            assert len(stderr_captured) == 1
    finally:
        cache_paths_mod._WARNED_SYSTEM_CACHE = old_warned


def test_override_none_reverts():
    """set_cache_dir_override(None) 能正确撤销覆盖。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="mtl_test_revert_"))
    try:
        set_cache_dir_override(temp_dir)
        assert get_cache_dir() == temp_dir
        set_cache_dir_override(None)
        assert get_cache_dir() != temp_dir
    finally:
        set_cache_dir_override(None)
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_env_var_empty_string_ignored():
    """MTL_CACHE_DIR 环境变量为空字符串或空白字符时被忽略，平滑回退。"""
    old_env = os.environ.get("MTL_CACHE_DIR")
    try:
        set_cache_dir_override(None)
        for empty_val in ["", "   ", "\t\n"]:
            os.environ["MTL_CACHE_DIR"] = empty_val
            p = get_cache_dir()
            assert p.resolve() == (REPO_ROOT / ".cache").resolve()
    finally:
        if old_env is not None:
            os.environ["MTL_CACHE_DIR"] = old_env
        else:
            os.environ.pop("MTL_CACHE_DIR", None)


def test_get_cache_dir_is_path_and_exists():
    """get_cache_dir() 返回的必须是有效的 Path 实例且目录真实存在。"""
    set_cache_dir_override(None)
    p = get_cache_dir()
    assert isinstance(p, Path)
    assert p.is_dir()
    assert p.exists()


def test_get_cache_path_nested():
    """get_cache_path 支持多级相对路径拼接。"""
    set_cache_dir_override(None)
    cp = get_cache_path("sub/nested/file.txt")
    assert cp.name == "file.txt"
    assert cp.parent.name == "nested"


def test_temp_fallback_when_repo_unwritable():
    """当 repo .cache 不可写时降级至平台缓存，平台缓存亦不可写时降级至临时目录，不崩溃且绝不污染系统盘。"""
    set_cache_dir_override(None)
    fake_platform_parent = Path(tempfile.mkdtemp(prefix="mtl_test_fake_platform_"))
    fake_localappdata = fake_platform_parent / "AppData" / "Local"
    fake_localappdata.mkdir(parents=True, exist_ok=True)

    old_env = os.environ.get("MTL_CACHE_DIR")
    old_local = os.environ.get("LOCALAPPDATA")
    old_xdg = os.environ.get("XDG_CACHE_HOME")
    old_warned = cache_paths_mod._WARNED_SYSTEM_CACHE
    orig_is_writable = cache_paths_mod._is_writable_dir

    def mock_repo_unwritable(path: Path) -> bool:
        if str((REPO_ROOT / ".cache").resolve()).lower() in str(path.resolve()).lower():
            return False
        return orig_is_writable(path)

    def mock_repo_and_platform_unwritable(path: Path) -> bool:
        if str((REPO_ROOT / ".cache").resolve()).lower() in str(path.resolve()).lower():
            return False
        if str(fake_platform_parent.resolve()).lower() in str(path.resolve()).lower():
            return False
        return orig_is_writable(path)

    try:
        os.environ.pop("MTL_CACHE_DIR", None)
        os.environ["LOCALAPPDATA"] = str(fake_localappdata)
        os.environ["XDG_CACHE_HOME"] = str(fake_platform_parent / "xdg_cache")
        cache_paths_mod._WARNED_SYSTEM_CACHE = False

        # 1. 验证 Tier 4 降级：repo .cache 不可写时，降级到平台标准缓存（在此为隔离的 fake 目录）
        with patch.object(cache_paths_mod, "_is_writable_dir", side_effect=mock_repo_unwritable), \
             patch("sys.stderr.write"):
            fallback_p4 = get_cache_dir()
            assert fallback_p4 is not None
            assert fallback_p4.is_dir()
            assert (REPO_ROOT / ".cache").resolve() != fallback_p4.resolve()
            assert fake_platform_parent.resolve() in fallback_p4.resolve().parents

        # 2. 验证 Tier 5 兜底：当平台标准缓存亦不可写时，平滑降级至系统临时目录
        cache_paths_mod._WARNED_SYSTEM_CACHE = False
        with patch.object(cache_paths_mod, "_is_writable_dir", side_effect=mock_repo_and_platform_unwritable), \
             patch("sys.stderr.write"):
            fallback_p5 = get_cache_dir()
            assert fallback_p5 is not None
            assert fallback_p5.is_dir()
            tempdir_resolved = Path(tempfile.gettempdir()).resolve()
            assert tempdir_resolved in fallback_p5.resolve().parents or fallback_p5.resolve() == tempdir_resolved

    finally:
        if old_env is not None:
            os.environ["MTL_CACHE_DIR"] = old_env
        else:
            os.environ.pop("MTL_CACHE_DIR", None)
        if old_local is not None:
            os.environ["LOCALAPPDATA"] = old_local
        else:
            os.environ.pop("LOCALAPPDATA", None)
        if old_xdg is not None:
            os.environ["XDG_CACHE_HOME"] = old_xdg
        else:
            os.environ.pop("XDG_CACHE_HOME", None)
        cache_paths_mod._WARNED_SYSTEM_CACHE = old_warned
        set_cache_dir_override(None)
        shutil.rmtree(fake_platform_parent, ignore_errors=True)


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())

