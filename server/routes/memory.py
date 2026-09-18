"""长期记忆、界面偏好、工作目录接口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from quill_agent.memory import MAX_MEMORIES
from quill_agent.tools.files import (
    clear_work_dir,
    current_work_dir,
    list_subdirs,
    pick_dir_with_system_dialog,
    set_work_dir,
    system_dir_picker_command,
)
from server import stores
from server.schemas import MemoryPayload, PreferencePayload, TogglePayload, WorkDirPayload

router = APIRouter(tags=["memory"])


@router.get("/memory")
def list_memory() -> dict:
    """全部记忆（新的在前），连同上限一起返回 —— 前端要提示「还剩几条」。"""
    return {"items": stores.memory().list(), "max": MAX_MEMORIES}


@router.post("/memory")
def add_memory(payload: MemoryPayload) -> dict:
    """手动添加一条记忆。

    走的是和 `remember` 工具同一个入口，所以去重、长度、上限规则完全一致。
    """
    try:
        item = stores.memory().add(payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return item.model_dump()


@router.patch("/memory/{memory_id}")
def toggle_memory(memory_id: str, payload: TogglePayload) -> dict:
    """开关一条记忆。关掉不等于删除，留着以后还能用回来。"""
    stores.memory().set_enabled(memory_id, payload.enabled)
    return {"id": memory_id, "enabled": payload.enabled}


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: str) -> dict[str, bool]:
    stores.memory().remove(memory_id)
    return {"removed": True}


@router.delete("/memory")
def clear_memory() -> dict[str, int]:
    return {"removed": stores.memory().clear()}


# ---------------------------------------------------------------------------
# 界面偏好
# ---------------------------------------------------------------------------
@router.get("/preferences")
def get_preferences() -> dict[str, str]:
    """整个偏好文件。

    前端启动时读一次就能拿回「上次选的模型 / 模式 / 工作目录」，
    不必为每一项单独发一次请求。
    """
    return stores.preferences().all()


@router.put("/preferences")
def set_preferences(payload: PreferencePayload) -> dict[str, str]:
    """批量写入偏好，返回写完之后的全量。"""
    store = stores.preferences()
    for key, value in payload.values.items():
        store.set(key, value)
    return store.all()


# ---------------------------------------------------------------------------
# 工作目录
# ---------------------------------------------------------------------------
@router.get("/workdir")
def get_workdir() -> dict:
    """当前生效的工作目录，以及这个环境能不能拉系统对话框。

    工作目录是文件工具的根，同时也是它们的安全边界（见 `tools/files.py`）——
    界面选过的优先，否则回落配置里的默认值。

    `native_picker` 让界面知道该用哪种选目录方式：本机跑（终端里有图形会话）
    可以拉系统对话框；云端部署拉不起来 —— 服务器上弹的窗用户根本看不见。
    """
    return {
        "path": str(current_work_dir()),
        "native_picker": system_dir_picker_command() is not None,
    }


@router.put("/workdir")
def change_workdir(payload: WorkDirPayload) -> dict[str, str]:
    """切换工作目录。

    只允许**空会话**换：工作目录是文件工具的安全边界，聊到一半换掉的话，
    模型脑子里「我读过哪些文件」和实际边界就对不上了（历史里还留着旧目录的
    文件内容，而新目录下同名文件是另一个东西）。
    """
    if payload.conversation_id and stores.conversations().load(payload.conversation_id):
        raise HTTPException(status_code=400, detail="会话已经开始，不能再改工作目录。")

    error = set_work_dir(payload.path)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"path": str(current_work_dir())}


@router.post("/workdir/pick")
def pick_workdir() -> dict:
    """拉起系统的目录选择器。

    **阻塞调用**：对话框关掉之前这个请求不会返回（见 `pick_dir_with_system_dialog`），
    界面得为此显示等待状态，别让用户以为点空了。

    没有可用选择器时返回 `available: false`，界面据此改用浏览器里的目录浏览。
    """
    if system_dir_picker_command() is None:
        return {"available": False, "path": None, "error": None}

    path, error = pick_dir_with_system_dialog()
    return {"available": True, "path": path, "error": error}


@router.delete("/workdir")
def reset_workdir() -> dict[str, str]:
    """清除界面选择，回到配置里的默认工作目录。"""
    clear_work_dir()
    return {"path": str(current_work_dir())}


@router.get("/workdir/browse")
def browse_dirs(path: str = "") -> dict:
    """列出某个目录下的子目录，供界面的「目录浏览器」使用。

    不传 path 就从当前工作目录起步 —— 界面刚打开选择器时正好需要这个行为。
    只列目录不列文件：这个接口的唯一用途就是挑一个目录当工作目录。

    路径失效（目录被删或移走）时静默退回当前工作目录，不报错 —— 用户看到的
    应该是一个还能用的选择器，而不是一句「路径不存在」。
    """
    start = Path(path).expanduser() if path.strip() else current_work_dir()
    if not start.is_dir():
        start = current_work_dir()

    dirs, error = list_subdirs(start)
    parent = start.parent

    return {
        "path": str(start.resolve()),
        # 根目录的 parent 就是它自己，这种情况不提供「上一级」
        "parent": str(parent.resolve()) if parent != start else "",
        "dirs": [{"name": item.name, "path": str(item)} for item in dirs],
        "error": error,
    }
