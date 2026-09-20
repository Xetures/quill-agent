"""在**干净环境**里把项目装一遍，确认依赖清单是自洽的。

为什么需要它：本地开发环境里装着一堆东西，有些依赖是「借」别人带进来的 ——
`python-multipart` 就曾经是被另一个包顺带装上的（server 用 `Form()/File()` 收附件，
而它不是 fastapi 的强制依赖）。那个包被拆掉之后它跟着消失，用户那边直接启动失败：
`RuntimeError: Form data requires "python-multipart" to be installed`。

这类问题**在本地永远测不出来**（本地恰好有），只有在一个什么都不知道的环境里
装一遍才会现形。所以发布前跑一次它。用法：

    uv run python scripts/check_clean_install.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> int:
    print(f"$ {' '.join(command)}", flush=True)
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="quill-clean-"))
    # Windows 的 venv 把解释器放在 Scripts/ 下
    python = workdir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    try:
        if run(["uv", "venv", "--python", "3.12", str(workdir)]) != 0:
            return 1
        if run(["uv", "pip", "install", "--python", str(python), "-e", ".", "pytest"]) != 0:
            return 1

        checks = (
            ([str(python), "-c", "import server.main as m; print('应用可导入:', m.app.title)"],
             "导入应用"),
            ([str(python), "-m", "pytest", "-q"], "跑测试"),
        )

        for command, label in checks:
            if run(command) != 0:
                print(f"\n✗ {label}失败 —— 上面就是用户那边会看到的报错")
                return 1
            print(f"✓ {label}通过\n")

        print("干净环境验证通过：依赖清单是自洽的。")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
