"""会话接口：列表、新建、归档、恢复、删除。"""

from __future__ import annotations

from fastapi import APIRouter

from quill_agent.history import ConversationMeta
from quill_agent.preferences import draft_key, mode_key
from server import stores

router = APIRouter(tags=["conversations"])


def _brief(meta: ConversationMeta) -> dict:
    """只给原始数据，展示文本交给前端。

    时间戳是浮点数，前端自己决定显示成「09-19 14:30」还是「3 分钟前」——
    后端一旦帮它格式化，换时区、换格式就得改后端。
    """
    return {"id": meta.id, "title": meta.title, "updated_at": meta.updated_at}


@router.get("/conversations")
def list_conversations() -> dict[str, list[dict]]:
    """会话列表，活跃与归档分开返回。"""
    store = stores.conversations()
    return {
        "active": [_brief(meta) for meta in store.list_active()],
        "archived": [_brief(meta) for meta in store.list_archived()],
    }


@router.post("/conversations")
def create_conversation() -> dict[str, str]:
    """新建一个空会话，返回它的 id。"""
    return {"id": stores.conversations().create()}


@router.get("/conversations/{conversation_id}")
def get_messages(conversation_id: str) -> dict[str, list[dict]]:
    """某个会话的全部消息。

    记录里带 `steps` / `notices` / `reasoning` / `stats` 等界面上要用的字段，
    原样透传 —— 前端渲染时需要哪个就用哪个。
    """
    return {"messages": stores.conversations().load(conversation_id)}


@router.post("/conversations/{conversation_id}/archive")
def archive_conversation(conversation_id: str) -> dict[str, bool]:
    """归档会话。

    返回 false 表示它是个空会话、被直接删掉了（归档没有意义）。
    """
    archived = stores.conversations().archive(conversation_id)
    # 顺手清掉草稿，别在偏好文件里留孤儿键
    stores.preferences().remove(draft_key(conversation_id))
    return {"archived": archived}


@router.post("/conversations/{conversation_id}/restore")
def restore_conversation(conversation_id: str) -> dict[str, bool]:
    """把归档会话恢复回活跃列表。"""
    return {"restored": stores.conversations().restore(conversation_id)}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict[str, bool]:
    """永久删除一个归档会话。"""
    removed = stores.conversations().remove_archived(conversation_id)
    # 会话真的没了，它记住的模式也一并清掉 —— 别在偏好文件里留孤儿键。
    # 归档 / 恢复不清：会话还在，只是搬了个目录，恢复后模式应该原样回来
    stores.preferences().remove(mode_key(conversation_id))
    return {"removed": removed}


@router.delete("/conversations")
def clear_archived() -> dict[str, int]:
    """清空归档。"""
    store = stores.conversations()
    preferences = stores.preferences()

    # 先按名单清模式偏好，再删文件 —— 删完就不知道该清哪些键了
    for meta in store.list_archived():
        preferences.remove(mode_key(meta.id))

    return {"removed": store.remove_all_archived()}
