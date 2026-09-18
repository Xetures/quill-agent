"""提示词库：管理 prompt/ 目录下的六类提示词文件。

目录结构（应用根目录）：

    prompt/
    ├── 身份/
    │   └── AI助手.md
    ├── 能力/
    ├── 工具策略/
    ├── 工作流程/
    ├── 输出规范/
    └── 约束/

每个类别一个子目录，用户直接在文件系统里新建 / 修改 / 删除 .md 文件。
文件名（不含扩展名）就是引用标识，会被写进模式配置里。
"""

from __future__ import annotations

from pathlib import Path

# 六类提示词；元组的顺序就是界面上的展示顺序
PROMPT_CATEGORIES: tuple[str, ...] = (
    "身份",
    "能力",
    "工具策略",
    "工作流程",
    "输出规范",
    "约束",
)

MARKDOWN_SUFFIX = ".md"


class PromptLibrary:
    """读写 prompt/ 目录下的提示词文件。

    Args:
        root: prompt 目录路径；子目录会在首次访问时自动创建。
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def ensure_dirs(self) -> None:
        """确保六类子目录都存在（应用首次运行时自动建好）。"""
        for category in PROMPT_CATEGORIES:
            self.category_dir(category).mkdir(parents=True, exist_ok=True)

    def category_dir(self, category: str) -> Path:
        """返回某个类别的目录路径。"""
        return self._root / category

    def list_names(self, category: str) -> list[str]:
        """列出某类别下的提示词名（不含扩展名），按名称排序。"""
        directory = self.category_dir(category)
        if not directory.exists():
            return []

        names = [path.stem for path in directory.glob(f"*{MARKDOWN_SUFFIX}") if path.is_file()]
        return sorted(names)

    def path_of(self, category: str, name: str) -> Path:
        """拼出提示词文件的完整路径。"""
        return self.category_dir(category) / f"{name}{MARKDOWN_SUFFIX}"

    def exists(self, category: str, name: str) -> bool:
        """判断某个提示词文件是否还存在（模式配置里的引用可能已失效）。"""
        return self.path_of(category, name).is_file()

    def read(self, category: str, name: str) -> str | None:
        """读取提示词内容；文件不存在时返回 None。"""
        path = self.path_of(category, name)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def collect(self, settings: dict[str, str]) -> dict[str, str]:
        """按模式配置取出所有提示词正文。

        Args:
            settings: {类别: 提示词名}，即模式里的选择结果。

        Returns:
            {类别: 正文}；文件已不存在的条目会被跳过。
        """
        collected: dict[str, str] = {}
        for category, name in settings.items():
            content = self.read(category, name)
            if content is not None:
                collected[category] = content
        return collected
