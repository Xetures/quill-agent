#!/usr/bin/env bash
# macOS 上双击运行的那个文件（扩展名 .command 才会被 Finder 当作可执行脚本）。
# 内容与 start.sh 一致，只是双击时没有终端参数要传。
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  cat <<'EOF'
没有找到 uv —— 本项目用它来装依赖和运行（它会顺带装好所需的 Python）。

安装（在「终端」里执行这一行）：
  curl -LsSf https://astral.sh/uv/install.sh | sh

装完再双击一次本文件。
EOF
  # 双击启动时窗口会立刻关掉，等用户看完提示
  read -r -p "按回车关闭…" _
  exit 1
fi

uv run quill serve --open-browser || {
  echo
  read -r -p "启动失败，按回车关闭…" _
  exit 1
}
