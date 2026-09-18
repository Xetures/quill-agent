"""会话历史的持久化。

一个会话 = 一个 JSONL 文件（一行一条消息），支持归档：

    data/conversations/
    ├── active/
    │   └── 20260918-034512-a1b2.jsonl
    └── archived/
        └── 20260918-030000-c3d4.jsonl

为什么用 JSONL 而不是 JSON 数组：对话是「只追加」的，JSONL 追加一行是 O(1)，
JSON 数组每次都得全量重写；而且某一行写坏了只丢一条消息，不会让整个文件报废。

会话 id 直接取文件名（时间戳 + 随机后缀），这样按文件名排序就是按时间排序。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# 标题取首条用户消息的前多少个字
TITLE_MAX_CHARS = 20

SUFFIX = ".jsonl"


@dataclass(frozen=True)
class ConversationMeta:
    """会话的元信息，用于列表展示（不含消息内容）。

    Attributes:
        updated_at: 时间戳。活跃会话里是「最后写入消息的时间」；
            归档会话里是「归档时间」（archive() 会显式打上）。
    """

    id: str
    title: str
    updated_at: float

    @property
    def updated_text(self) -> str:
        """最后更新时间的展示文本，用于侧边栏。"""
        return datetime.fromtimestamp(self.updated_at).strftime("%m-%d %H:%M")

    @property
    def archived_text(self) -> str:
        """归档时间的展示文本。带年份 —— 归档可能放很久。"""
        return datetime.fromtimestamp(self.updated_at).strftime("%Y-%m-%d %H:%M")


class ConversationStore:
    """多会话的读写与归档。

    Args:
        root: 会话根目录，其下自动维护 active / archived 两个子目录。
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._active = self._root / "active"
        self._archived = self._root / "archived"

    def ensure_dirs(self) -> None:
        """确保两个子目录存在。"""
        self._active.mkdir(parents=True, exist_ok=True)
        self._archived.mkdir(parents=True, exist_ok=True)

    def list_active(self) -> list[ConversationMeta]:
        """活跃会话，按最近更新倒序。"""
        return self._collect(self._active)

    def list_archived(self) -> list[ConversationMeta]:
        """已归档会话，按最近更新倒序。"""
        return self._collect(self._archived)

    def create(self) -> str:
        """新建一个空会话，返回它的 id。"""
        self.ensure_dirs()
        conv_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:4]}"
        (self._active / f"{conv_id}{SUFFIX}").touch()
        return conv_id

    def load(self, conv_id: str) -> list[dict]:
        """读取某个会话的全部消息；文件不存在或坏行都会被安全跳过。"""
        path = self._active / f"{conv_id}{SUFFIX}"
        if not path.is_file():
            return []

        messages: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 坏行跳过，不影响其它消息
        return messages

    def append(self, conv_id: str, message: dict) -> None:
        """追加一条消息 —— 只写一行，不重写整个文件。"""
        self.ensure_dirs()
        path = self._active / f"{conv_id}{SUFFIX}"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message, ensure_ascii=False) + "\n")

    def archive(self, conv_id: str) -> bool:
        """归档会话；不存在时静默忽略。

        空会话（一条消息都没有）没有归档价值，直接删除，不在归档目录里留下空文件。

        Args:
            conv_id: 会话 id。

        Returns:
            True 表示已移入归档目录，False 表示是空会话被直接删除（或本就不存在）。
        """
        self.ensure_dirs()
        source = self._active / f"{conv_id}{SUFFIX}"
        if not source.is_file():
            return False

        if self._is_empty(source):
            source.unlink()
            return False

        target = self._archived / source.name
        source.replace(target)
        # replace 只是改名，不会更新 mtime。这里显式打上归档时刻，
        # 归档列表才能显示正确的「归档时间」而不是最后一条消息的时间。
        os.utime(target, None)
        return True

    def restore(self, conv_id: str) -> bool:
        """把归档会话恢复回活跃列表。

        Returns:
            True 表示已恢复，False 表示归档里没有这个会话。
        """
        self.ensure_dirs()
        source = self._archived / f"{conv_id}{SUFFIX}"
        if not source.is_file():
            return False

        # 同样不动 mtime：恢复只是换目录，消息时间应该保持真实
        source.replace(self._active / source.name)
        return True

    def remove_archived(self, conv_id: str) -> bool:
        """永久删除一个归档会话。

        Returns:
            True 表示已删除，False 表示归档里不存在。
        """
        path = self._archived / f"{conv_id}{SUFFIX}"
        if not path.is_file():
            return False

        path.unlink()
        return True

    def remove_all_archived(self) -> int:
        """清空归档目录。

        Returns:
            实际删除的文件数；个别删不掉（权限等）会跳过，不影响其余的。
        """
        self.ensure_dirs()
        removed = 0
        for path in self._archived.glob(f"*{SUFFIX}"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue

        return removed

    @staticmethod
    def _is_empty(path: Path) -> bool:
        """判断会话文件里是否一条有效消息都没有。"""
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        return False
        except OSError:
            return True  # 读不了就当作空，避免留下坏文件

        return True

    def _collect(self, directory: Path) -> list[ConversationMeta]:
        """扫描目录，组装会话元信息。"""
        if not directory.is_dir():
            return []

        metas: list[ConversationMeta] = []
        for path in directory.glob(f"*{SUFFIX}"):
            try:
                metas.append(
                    ConversationMeta(
                        id=path.stem,
                        title=self._read_title(path),
                        updated_at=path.stat().st_mtime,
                    )
                )
            except OSError:
                continue

        return sorted(metas, key=lambda meta: meta.updated_at, reverse=True)

    @staticmethod
    def _read_title(path: Path) -> str:
        """标题 = 首条用户消息的前若干字。

        只读到第一条 user 消息就返回，不用解析整个文件。
        """
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    message = json.loads(line)
                    if message.get("role") != "user":
                        continue

                    text = " ".join(str(message.get("content", "")).split())
                    if not text:
                        break
                    clipped = text[:TITLE_MAX_CHARS]
                    return clipped + ("…" if len(text) > TITLE_MAX_CHARS else "")
        except (OSError, json.JSONDecodeError):
            return "（读取失败）"

        return "（空会话）"
