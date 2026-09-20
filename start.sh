#!/usr/bin/env bash
# 一键启动 quill：装依赖 → 起服务 → 打开浏览器。
#
# 唯一的前置是 uv（它会顺带装好本项目需要的 Python）。首次运行要联网下载，
# 之后就是秒起。想换个端口：./start.sh --port 9000
set -euo pipefail

# 切到脚本所在目录：数据默认放在这里（见 config.py 里「就地运行」那条规则），
# 从别处执行也能得到一致的行为
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  cat <<'EOF'
没有找到 uv —— 本项目用它来装依赖和运行（它会顺带装好所需的 Python）。

macOS / Linux 安装：
  curl -LsSf https://astral.sh/uv/install.sh | sh

装完重开一个终端，再运行一次本脚本。
EOF
  exit 1
fi

exec uv run quill serve --open-browser "$@"
