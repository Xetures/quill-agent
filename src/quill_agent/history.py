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
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from quill_agent.locking import atomic_write_text, file_lock

# 标题取首条用户消息的前多少个字
TITLE_MAX_CHARS = 20

SUFFIX = ".jsonl"

# 会话 id 的前半段就是创建时刻。解析它比读文件 mtime 可靠：归档会改 mtime。
ID_TIME_FORMAT = "%Y%m%d-%H%M%S"


@dataclass(frozen=True)
class ConversationMeta:
    """会话的元信息，用于列表展示（不含消息内容）。

    Attributes:
        updated_at: 时间戳。活跃会话里是「最后写入消息的时间」；
            归档会话里是「归档时间」（archive() 会显式打上）。
        archived: 是否躺在归档目录里。用量统计要跨两个目录一起扫，
            需要它来判断每一行的状态。
    """

    id: str
    title: str
    updated_at: float
    archived: bool = False

    @property
    def created_at(self) -> float:
        """创建时间（Unix 秒）。

        id 里就编着创建时刻（见 `create()`），直接解出来即可 —— 比文件 mtime
        可靠得多，归档会把 mtime 改成归档那一刻。id 被手工改过、解不出来时
        退回 updated_at，宁可时间不准也不要抛异常。
        """
        parts = self.id.split("-")
        if len(parts) >= 2:
            try:
                return datetime.strptime(f"{parts[0]}-{parts[1]}", ID_TIME_FORMAT).timestamp()
            except ValueError:
                pass

        return self.updated_at

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
        return self._collect(self._active, archived=False)

    def list_archived(self) -> list[ConversationMeta]:
        """已归档会话，按最近更新倒序。"""
        return self._collect(self._archived, archived=True)

    def iter_all(self) -> Iterator[tuple[ConversationMeta, list[dict]]]:
        """遍历全部会话（活跃 + 归档）的元信息与消息。

        给用量统计这类「全局扫描」用的：`load()` 只认 active/，归档会话读不到。
        把「哪个目录对应哪种状态」这件事留在 store 里，调用方不必自己拼路径。
        """
        for meta in self.list_active() + self.list_archived():
            directory = self._archived if meta.archived else self._active
            yield meta, self._read(directory / f"{meta.id}{SUFFIX}")

    def create(self) -> str:
        """新建一个空会话，返回它的 id。"""
        self.ensure_dirs()
        conv_id = f"{datetime.now().strftime(ID_TIME_FORMAT)}-{uuid4().hex[:4]}"
        (self._active / f"{conv_id}{SUFFIX}").touch()
        return conv_id

    def load(self, conv_id: str) -> list[dict]:
        """读取某个活跃会话的全部消息；文件不存在或坏行都会被安全跳过。"""
        return self._read(self._active / f"{conv_id}{SUFFIX}")

    @staticmethod
    def _read(path: Path) -> list[dict]:
        """读一个会话文件；不存在、读不了、坏行都安全跳过。"""
        if not path.is_file():
            return []

        messages: list[dict] = []
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return []

        for line in content.splitlines():
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

    def upsert_run(self, conv_id: str, run_id: str, message: dict) -> None:
        """写入「某一轮的助手消息」：已有这一轮的记录就替换它，没有才追加。

        **为什么 append 不够用。** 助手消息现在是**边跑边写**的（见 `chat.stream_round`）：
        跑到一半进程被杀，盘上也得有「这一轮做到哪了」。而 JSONL 的 append 只会往末尾加
        新行 —— 每写一次就多一条，重启后看起来像模型回答了十几次。

        所以按 `run_id` 找位置：找到就替换、找不到才追加。写多少次，盘上都只有一条。

        代价是重写整个文件（和 `delete_at` 同一套办法）。会话只有几十条消息，而调用方带
        节流（见 `chat.ANSWER_FLUSH_SECONDS`），不值得为它换一套更复杂的结构。
        """
        self.ensure_dirs()
        path = self._active / f"{conv_id}{SUFFIX}"
        with file_lock(path):
            messages = self._read(path)
            # 从后往前找：这一轮的消息总在最后几条里
            for index in range(len(messages) - 1, -1, -1):
                if messages[index].get("run_id") == run_id:
                    messages[index] = message
                    break
            else:
                messages.append(message)

            atomic_write_text(
                path,
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in messages),
            )

    def delete_at(self, conv_id: str, index: int) -> bool:
        """删掉第 index 条消息（从 0 数）。索引越界或会话不存在时返回 False。

        **重写整个文件**，而不是想办法挖个洞：这个格式靠**行号**定位消息，留一个空洞会让
        「第几条」这个约定失效 —— 而前端的删除按钮正是按索引传进来的。

        删掉之后，这条消息就不再参与后续对话的上下文组装（下一轮不会带上它）。

        Raises:
            ValueError: 会话不存在。
        """
        path = self._active / f"{conv_id}{SUFFIX}"
        if not path.is_file():
            raise ValueError(f"会话不存在：{conv_id}")

        with file_lock(path):
            messages = self._read(path)
            if index < 0 or index >= len(messages):
                return False

            del messages[index]
            atomic_write_text(
                path,
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in messages),
            )
        return True

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

    def _collect(self, directory: Path, archived: bool) -> list[ConversationMeta]:
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
                        archived=archived,
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


def render_markdown(messages: list[dict], *, title: str = "") -> str:
    """把会话记录渲染成 Markdown —— 导出的「给人读」格式。

    原则是**正文照抄、过程收起**：对话本身要能直接读；工具调用是过程（回看时通常
    不关心，但删掉就丢了信息），所以收进折叠块。

    放在这里而不是服务层：它是纯数据渲染，和界面无关 —— 以后 TUI 想导出也用同一个。
    """
    lines = [f"# {title or '会话记录'}", "", f"> 共 {len(messages)} 条记录", ""]

    for record in messages:
        role = record.get("role")
        content = str(record.get("content") or "").strip()
        steps = [item for item in (record.get("steps") or []) if isinstance(item, dict)]

        if role == "summary":
            lines += ["## 已压缩的早期对话", "", content or "（空）", ""]
            continue

        if role not in {"user", "assistant"}:
            continue

        lines += ["## 用户" if role == "user" else "## 助手", ""]
        if content:
            lines += [content, ""]

        if steps:
            lines += [f"<details><summary>工具调用（{len(steps)} 次）</summary>", ""]
            for step in steps:
                lines += [
                    f"**{step.get('name') or '?'}**",
                    "",
                    "```",
                    str(step.get("arguments") or ""),
                    "```",
                    "",
                    "```",
                    str(step.get("result") or ""),
                    "```",
                    "",
                ]
            lines += ["</details>", ""]

        stats = record.get("stats")
        if isinstance(stats, dict) and stats.get("total_tokens"):
            elapsed = float(stats.get("elapsed") or 0)
            model = record.get("model") or ""
            lines += [f"*{elapsed:.1f}s · {stats['total_tokens']} tokens · {model}*", ""]

    return "\n".join(lines).rstrip() + "\n"
