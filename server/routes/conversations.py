"""会话接口：列表、新建、归档、恢复、删除。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Body, HTTPException, Response

from quill_agent import changes
from quill_agent.config import get_settings
from quill_agent.history import ConversationMeta, render_markdown
from quill_agent.preferences import draft_key, mode_key
from quill_agent.tools.files import current_work_dir
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


@router.get("/conversations/{conversation_id}/export")
def export_conversation(conversation_id: str, format: str = "markdown") -> Response:
    """导出会话：Markdown（给人读）或 JSON（给程序用）。

    会话是这个应用的产出物，得有办法拿出去 —— 换机器、备份、当笔记存档。
    """
    messages = stores.conversations().load(conversation_id)
    if not messages:
        raise HTTPException(status_code=404, detail="这个会话不存在，或者还没有内容。")

    if format == "json":
        body = json.dumps(messages, ensure_ascii=False, indent=2)
        media, suffix = "application/json", "json"
    else:
        body = render_markdown(messages, title=f"会话 {conversation_id}")
        media, suffix = "text/markdown; charset=utf-8", "md"

    return Response(
        content=body,
        media_type=media,
        # 让浏览器直接存成文件：导出本来就是「拿走」，不是「在页面上看」
        headers={
            "Content-Disposition": f'attachment; filename="quill-{conversation_id}.{suffix}"'
        },
    )


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
    # 改动快照同理：会话都没了，还留着它的历史版本没有意义（而且会一直占着磁盘）。
    # 归档 / 恢复同样不清 —— 那个会话还能回来，它的「还原」也该还能用
    changes.forget_conversation(get_settings().home, conversation_id)
    return {"removed": removed}


# 路径里带 `/changes/`：`/conversations/{id}/restore` 已经归「从归档恢复」了
# （见上面那个端点）。两者语义也完全不同 —— 一个是把会话搬回来，一个是把文件改回去，
# 挤在同一个路径上迟早会有人调错
@router.post("/conversations/{conversation_id}/changes/restore")
def restore_changes(conversation_id: str, run_id: str = Body(embed=True)) -> dict:
    """把某一轮改过的文件还原到改动前。

    这里**不再确认一次**：前端那个按钮本来就要用户点两下（按钮 + 确认框），后端再加一道
    只会让确认变成噪音。但要把话说清楚 —— **命令的副作用不在还原范围内**（见
    `changes` 模块开头），所以返回值里带着没还原成功的文件，界面得如实转告。
    """
    restored, skipped = changes.restore(
        get_settings().home, conversation_id, run_id, current_work_dir()
    )
    return {"restored": restored, "skipped": skipped}


@router.delete("/conversations/{conversation_id}/messages/{index}")
def delete_message(conversation_id: str, index: int) -> dict[str, bool]:
    """删掉会话里的一条消息（按位置，从 0 数）。

    是**真删**，不是界面上藏起来：删掉之后它不再参与后续对话的上下文组装，模型下一轮
    就看不到它了。只藏不删的话，用户以为「删掉了」，而模型还记得 —— 那种「删了但没删」
    比不给删更糟。
    """
    try:
        removed = stores.conversations().delete_at(conversation_id, index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not removed:
        raise HTTPException(
            status_code=400,
            detail="这条消息不在会话里（可能已经被删过一次了），刷新一下看看。",
        )
    return {"removed": True}


@router.delete("/conversations")
def clear_archived() -> dict[str, int]:
    """清空归档。"""
    store = stores.conversations()
    preferences = stores.preferences()

    # 先按名单清模式偏好，再删文件 —— 删完就不知道该清哪些键了
    for meta in store.list_archived():
        preferences.remove(mode_key(meta.id))

    return {"removed": store.remove_all_archived()}
