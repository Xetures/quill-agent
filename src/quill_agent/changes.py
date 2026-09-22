"""一轮里的文件改动：既给界面看 diff，也给「还原」存底。

**为什么两件事共用一个记录器。** 它们消费的是同一条数据 ——「这次调用把哪个文件从什么
改成了什么」。分头去做，就会有两份「谁改了哪个文件」的判断，迟早对不上：界面显示的
改动和能还原的改动不是一回事，那种不一致比不做还糟。

**记录器怎么送到工具里。** `ContextVar`，和 `interaction` 同一套办法（理由见那边开头：
工具签名不用加参数，谁要用谁自己取）。没激活时（比如单元测试直接调工具）记录静默跳过，
**工具本身照常工作** —— 这层是附加的，不能让它成为工具的依赖。

**改动一发生就落盘，不等这一轮跑完。** 原先是在整轮结束时写一次（`stream_round` 收尾
那句 `save`）。问题在于：中途重启或断电时，**被改的文件已经在磁盘上了，而还原要的前像
还没落盘** —— 结果是文件改了、还原不了，用户没有任何办法退回去。跑得越久（几十步的长
任务）这个窗口越大，而长任务恰恰最需要能退。所以 `record_text` 每次记完就 `_flush` 一次，
代价是 manifest 会被反复重写 —— 它是几百字节，而换来的是「还原」在任何时刻都是可用的。

**落盘失败不打断这一轮。** 文件已经改了，这里丢的只是还原能力；让工具调用因此失败，
等于用一个小毛病换一个大毛病。但也不是没声音 —— 磁盘满这类事得有条线索（见 `_flush`）。

**边界，写在这里也写进界面。**
- `run_command` 改的文件**记不到**：它走的是 shell，我们不解析命令行。还原时界面要说明
  这一点，否则用户会以为还原干净了。
- 二进制或超过阈值的文件只记「改过」，不存内容，还原时跳过（存了也还原不回去）。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from quill_agent.locking import atomic_write_text

# 单个文件超过它就不存内容了。把 base64 和大文件存两份会把内存和磁盘一起吃满，
# 而那种文件本来也不是「还原」救得回来的东西。
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024

# diff 最多给界面多少行。它是给人扫一眼的，不是让人在浏览器里读完整个改动。
MAX_DIFF_LINES = 200


def _sha(text: str) -> str:
    """内容摘要，用来给快照文件命名：同一份内容只存一次。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class FileChange:
    """一个文件的一次改动。"""

    path: str  # 相对工作目录；界面显示和还原都用它
    kind: str  # write / edit / delete / move / mkdir
    before: str | None  # 改动前的内容；新建时为 None
    after: str | None  # 改动后的内容；删除时为 None
    binary: bool = False  # 二进制或过大：只记「改过」，内容不存

    @cached_property
    def _opcodes(self) -> list[tuple[str, int, int, int, int]]:
        before = (self.before or "").splitlines()
        after = (self.after or "").splitlines()
        return difflib.SequenceMatcher(None, before, after).get_opcodes()

    @property
    def added(self) -> int:
        """新增行数。用 opcodes 算而不是比长度：改一行时两边长度相同，但确实改了。"""
        if self.binary:
            return 0
        return sum(j2 - j1 for tag, _, _, j1, j2 in self._opcodes if tag in ("insert", "replace"))

    @property
    def removed(self) -> int:
        if self.binary:
            return 0
        return sum(i2 - i1 for tag, i1, i2, _, _ in self._opcodes if tag in ("delete", "replace"))

    def unified(self, context: int = 3) -> str:
        """给界面看的 unified diff。

        **新建文件返回空串**：它的「改动」就是整份文件的内容，全绿的一片没有信息量，
        用户要看内容直接看文件就行。删除保留（那份内容消失了，值得看一眼）。
        """
        if self.binary or self.before is None:
            return ""

        before = self.before.splitlines()
        after = (self.after or "").splitlines()
        lines = list(difflib.unified_diff(before, after, lineterm="", n=context))
        if len(lines) > MAX_DIFF_LINES:
            hidden = len(lines) - MAX_DIFF_LINES
            lines = [*lines[:MAX_DIFF_LINES], f"… 还有 {hidden} 行未显示"]
        return "\n".join(lines)

    def payload(self) -> dict:
        """给前端的一份（随 SSE 走，也会进消息的汇总）。"""
        return {
            "path": self.path,
            "kind": self.kind,
            "added": self.added,
            "removed": self.removed,
            "binary": self.binary,
            "diff": self.unified(),
        }


@dataclass
class ChangeRecorder:
    """一轮里攒下来的改动，按发生顺序。

    落盘要用的三样（数据根、会话、运行）也挂在这里，而不是另外传一路参数：它们和改动
    是同一次运行的东西，分散传的话调用链上每一层都要多带三个参数。
    """

    work_dir: Path
    home: Path
    conversation_id: str
    run_id: str
    changes: list[FileChange] = field(default_factory=list)

    def add(self, change: FileChange) -> None:
        self.changes.append(change)

    def rel(self, target: Path) -> str:
        """转成界面用的路径。工作目录之外的（沙箱关掉时可能）就用绝对路径。"""
        try:
            return target.resolve().relative_to(self.work_dir.resolve()).as_posix()
        except ValueError:
            return str(target)


_current: ContextVar[ChangeRecorder | None] = ContextVar("quill_changes", default=None)


def activate(recorder: ChangeRecorder) -> Token:
    """在当前线程启用记录；返回的 token 交给 `deactivate`。"""
    return _current.set(recorder)


def deactivate(token: Token) -> None:
    _current.reset(token)


def current() -> ChangeRecorder | None:
    return _current.get()


def mark() -> int:
    """记下现在的条数，配 `taken()` 用（见 agent 里「这次调用改了什么」）。"""
    recorder = _current.get()
    return len(recorder.changes) if recorder else 0


def taken(index: int) -> list[FileChange]:
    """`mark()` 之后新增的改动。"""
    recorder = _current.get()
    return recorder.changes[index:] if recorder else []


def record_text(target: Path, after: str | None, *, kind: str) -> None:
    """把一次写入/删除记下来。`after` 为 None 表示文件被删掉。

    **必须在动手之前调用**：读的是磁盘上那份旧内容，改完再读就没得记了。
    """
    recorder = _current.get()
    if recorder is None:
        return

    before: str | None = None
    binary = False
    if target.is_file():
        try:
            raw = target.read_bytes()
        except OSError:
            binary = True
        else:
            if len(raw) > MAX_SNAPSHOT_BYTES:
                binary = True
            else:
                try:
                    before = raw.decode("utf-8")
                except UnicodeDecodeError:
                    # 二进制：解不出文本就没有 diff 也没有还原可言
                    binary = True

    recorder.add(
        FileChange(
            path=recorder.rel(target),
            kind=kind,
            before=before,
            after=None if binary else after,
            binary=binary,
        )
    )

    # 记完就落盘，不等这一轮跑完 —— 理由见模块开头。这里的一行是「改了就能退」
    # 和「跑到一半重启就退不回去」的全部区别
    _flush(recorder)


def _persist(recorder: ChangeRecorder, first: dict[str, FileChange]) -> None:
    """把去重后的改动写进检查点目录（前像 blobs + manifest）。

    **写盘一律走 `atomic_write_text`**：manifest 现在是跑一轮期间反复写的（见 `_flush`），
    而它正是「还原」唯一的路标 —— 直接 write_text 中途被杀会留下一个截断的 JSON，
    之后 restore 打不开它，这一轮改过的文件就再也退不回去了。
    """
    folder = recorder.home / "checkpoints" / recorder.conversation_id / recorder.run_id
    blobs = folder / "files"
    blobs.mkdir(parents=True, exist_ok=True)

    manifest = []
    for change in first.values():
        before_hash = None
        if change.before is not None and not change.binary:
            before_hash = _sha(change.before)
            blob = blobs / before_hash
            # 按内容命名 → 同一个文件的多轮改动、多个文件内容相同，都只占一份
            if not blob.exists():
                atomic_write_text(blob, change.before)

        manifest.append(
            {
                "path": change.path,
                "kind": change.kind,
                "before_hash": before_hash,
                "binary": change.binary,
            }
        )

    atomic_write_text(folder / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def _flush(recorder: ChangeRecorder) -> list[dict] | None:
    """把**当前累积的**改动落盘，返回文件摘要；没有改动时返回 None。

    幂等，可以反复调：`record_text` 每记一笔调一次（改动一发生就能还原），这一轮结束时
    `save` 再调一次（把摘要带回给消息）。重复调用只是重算一遍，不会重复存内容。

    写盘失败**不往外抛**：文件已经改了，这里丢的只是还原能力；让工具调用因此失败，等于拿
    一个小毛病换一个大毛病。但也不是完全没声音 —— 磁盘满这类事得留条线索，否则用户会发现
    「还原」悄悄不管用了，却不知道是从什么时候开始的。
    """
    changes = recorder.changes
    if not changes:
        return None

    # 按文件去重，只留**最早**那一条：还原要的是「这一轮开始时它长什么样」。同一个文件
    # 被改过几次的话，只有最早那条的 before 是对的 —— 用最后一条会把中间那次的结果当成原样。
    first: dict[str, FileChange] = {}
    for change in changes:
        first.setdefault(change.path, change)

    # 摘要先算出来：它只依赖内存里的改动。界面显示的「改了哪几个文件」是已经发生的事实，
    # 不该因为快照没写成功就变成「没改过」
    summary = [{"path": c.path, "kind": c.kind, "binary": c.binary} for c in first.values()]

    try:
        _persist(recorder, first)
    except OSError as exc:
        print(f"[changes] 快照落盘失败，这一轮将无法还原：{exc}", flush=True)

    return summary


def save(recorder: ChangeRecorder) -> dict | None:
    """把这一轮的改动落盘，供之后还原。返回给消息用的汇总；没有改动时返回 None。

    存的是**改动前**的内容：还原只需要它（现在的样子就在磁盘上，不用再存一份）。

    真正的写入其实早就在 `record_text` 里逐次发生过了（见 `_flush`）；这里再调一次是为了
    拿到摘要 —— 顺带兜住「最后一次改动之后又发生了什么」的收尾情况。
    """
    summary = _flush(recorder)
    if summary is None:
        return None
    return {"run_id": recorder.run_id, "files": summary}


def restore(
    home: Path, conversation_id: str, run_id: str, work_dir: Path
) -> tuple[list[str], list[str]]:
    """把这一轮改过的文件还原到改动前。

    Returns:
        (还原了的文件, 没能还原的文件)。后者是二进制/过大的那些 —— 我们没存内容，
        界面得如实说，不能假装还原干净了。
    """
    folder = home / "checkpoints" / conversation_id / run_id
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        return [], []

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 原子写之后不该出现，但更早版本留下的截断文件要扛得住：还原不了就如实说，
        # 总比让「还原」这个按钮直接报错强
        return [], []
    root = work_dir.resolve()
    restored: list[str] = []
    skipped: list[str] = []

    for entry in manifest:
        target = (root / entry["path"]).resolve()
        # 路径必须落在工作目录里。manifest 是磁盘上的文件，别让它成了一条穿越的跳板
        if not target.is_relative_to(root):
            skipped.append(entry["path"])
            continue

        if entry.get("binary"):
            skipped.append(entry["path"])
            continue

        before_hash = entry["before_hash"]
        if before_hash is None:
            # 这一轮新建的 → 删掉它，回到「原本不存在」
            if target.is_file():
                target.unlink()
                restored.append(entry["path"])
            continue

        blob = folder / "files" / before_hash
        if not blob.exists():
            skipped.append(entry["path"])
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(blob.read_text(encoding="utf-8"), encoding="utf-8")
        restored.append(entry["path"])

    return restored, skipped


def forget_conversation(home: Path, conversation_id: str) -> None:
    """清掉某个会话的全部检查点。会话被永久删除时调，否则那些快照会永远留在数据目录里。"""
    folder = home / "checkpoints" / conversation_id
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
