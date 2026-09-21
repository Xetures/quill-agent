"""执行权限（沙箱）接口。

为什么不并进 `/preferences`：这个接口要**校验**。沙箱档位填错不能像别的偏好那样
「当成默认值处理」—— 那等于「以为开着沙箱、其实在裸跑」，是这里最坏的失败方式
（见 `sandbox.py` 的说明）。走通用偏好接口的话，谁都能塞一个 `workspace-writ`
进去让它静默回落。

和审批不是一回事（别混）：审批管「要不要问用户」，沙箱管「命令够不够得着」。
本接口只覆盖后者；前者是模式的组成部分（工具组里的 `confirm` 名单）。
"""

from __future__ import annotations

from fastapi import APIRouter

from quill_agent import sandbox
from quill_agent.preferences import SANDBOX_MODE_KEY, SANDBOX_NETWORK_KEY
from server import stores
from server.schemas import SandboxPayload

router = APIRouter(tags=["sandbox"])


@router.get("/sandbox")
def get_sandbox() -> dict:
    """此刻的档位与后端状态，连界面要用的档位清单一并给（见 `sandbox.status`）。"""
    return sandbox.status()


@router.put("/sandbox")
def set_sandbox(payload: SandboxPayload) -> dict:
    """改档位与联网开关：写进偏好，**下一条命令就生效**（不必重启）。

    存偏好而不是 .env：这个设置天然按任务变。要是改一次就得重启进程，
    用户的理性选择就是干脆一关了之 —— 那才是最坏的结果。

    返回改完之后的完整状态，而不是一个 `{"ok": true}`：界面拿它直接刷新自己，
    不必再补一次 GET。
    """
    store = stores.preferences()
    # 两个键分别写。PreferenceStore 每写一次就「读 → 改 → 写」并持一次文件锁，
    # 理论上两次之间可能被别的请求插进来 —— 但这里不是高频并发路径，
    # 为它单独加一个批量接口不划算
    store.set(SANDBOX_MODE_KEY, payload.mode.value)
    store.set(SANDBOX_NETWORK_KEY, "true" if payload.network else "false")
    return sandbox.status()
