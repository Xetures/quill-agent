"""全仓库共用的测试夹具。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quill_agent import sandbox
from quill_agent.preferences import PreferenceStore


@pytest.fixture
def sandbox_preferences(tmp_path: Path) -> PreferenceStore:
    """沙箱读取的那份偏好，指向临时文件。

    档位与联网开关现在也能从偏好里来（见 `sandbox.effective_mode`），而偏好文件是
    **开发机上的真实状态**。不隔离的话，只要本机在界面上动过「执行权限」，
    这一整套用例的结果就会跟着变：今天全绿，明天莫名挂 —— 而且挂的地方看起来
    和被测代码毫无关系，那种失败最难查。

    要验「用户设成这样会怎样」的用例直接请求它，往里写值即可。

    这里**不用 `monkeypatch`**：它的建立时机比这个夹具早，而 `test_sandbox` 的
    `_clear_caches` 在收尾时假定两个探测还是原来的函数（它按顺序调 `cache_clear`，
    见那边的注释）。请求 `monkeypatch` 会把那个假设打破，收尾时报
    `AttributeError: 'function' object has no attribute 'cache_clear'`。
    自己存一份再还原，时机和改动前完全一致。
    """
    store = PreferenceStore(tmp_path / "preferences.json")
    original = sandbox._preference_store
    sandbox._preference_store = lambda: store
    try:
        yield store
    finally:
        sandbox._preference_store = original


@pytest.fixture(autouse=True)
def _isolate_preferences(sandbox_preferences: PreferenceStore) -> None:
    """让上面那份隔离对**所有**用例自动生效，而不只是显式请求了它的那些。

    会执行命令的用例（`test_shell` / `test_process` / `test_subagent` …）都要经过
    `sandbox.policy_for`，它们不会主动想到「我依赖了开发机的偏好文件」。
    隔离这种依赖不该是「记得写才生效」的东西。
    """
