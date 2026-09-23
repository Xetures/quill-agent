.PHONY: help install dev api serve web build cli test e2e lint fmt clean distclean \
        desktop desktop-deps

# `make` 不带参数时列目标，**不要**让它落到第一个真目标上 —— 那会执行 `install`，
# 而 `uv sync` 不带 extra 会把 dev / desktop 的依赖卸掉（ruff、pytest、pywebview 全没），
# 症状是「昨天还能跑测试，今天命令都找不到了」。
.DEFAULT_GOAL := help

help:               ## 列出所有可用目标
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# 只装运行时依赖。**注意它会卸掉没写进 extra 的包** —— 日常开发用 `make dev`。
install:            ## 安装运行时依赖（会卸掉 dev/desktop 的包，慎用）
	uv sync

# 桌面壳（pywebview）是可选依赖。**`uv sync` 会把没写进 extra 的包卸掉** ——
# 只跑 `uv sync --extra dev` 的话，上次用 --extra desktop 装的 pywebview 会被移除，
# 表现为「昨天还能 make desktop，今天就报没有 webview」。两个都带上。
dev:                ## 安装运行时 + 开发依赖 + 桌面壳依赖
	uv sync --extra dev --extra desktop

api:                ## 启动后端（8000）；web/dist 存在时连前端一起托管
	uv run uvicorn server.main:app --reload --port 8000

serve:              ## 启动服务：端口顺延 + 自动开浏览器（日常用它）
	uv run quill serve --open-browser

web:                ## 启动前端开发服务器（Vue，5173）
	cd web && npm run dev

build:              ## 构建前端产物（之后 make api 即可单进程使用）
	cd web && npm install && npm run build

# 打 macOS 桌面 App。两步的顺序不能反：`make_desktop.py` 是把 `web/dist` **复制**进包里，
# 不重新构建前端 —— 先 build 才会把最新的界面打进去。
#
# 产出 `release/Quill.app`（未签名，首次要右键 → 打开）。脚本自己会：
#   - 复用它找到的本机 Python 运行时（找不到才下载）
#   - 打完向 Launch Services 重新登记（不登记的话图标会显示成通用的，见脚本里的注释）
#   - 处理批量删除的保护阈值（见脚本 main() 开头）
desktop:            ## 打包 macOS 桌面 App（产物 release/Quill.app）
	cd web && npm install && npm run build
	uv run python scripts/make_desktop.py

desktop-deps:       ## 只装桌面壳依赖（不想动其它依赖时用它）
	uv sync --extra desktop

i18n:               ## 看界面文案的 i18n 迁移进度（还有多少中文没接进来）
	uv run python scripts/i18n_progress.py

cli:                ## 运行命令行入口
	uv run quill -v

test:               ## 运行测试
	uv run pytest

e2e:                ## 前端端到端冒烟测试（Playwright，用系统 Chrome，无需后端）
	cd web && npm run e2e

lint:               ## 静态检查
	uv run ruff check .

fmt:                ## 格式化代码
	uv run ruff format .

clean:              ## 清理缓存文件
	rm -rf .pytest_cache .ruff_cache .venv
	find . -type d -name __pycache__ -exec rm -rf {} +

# 打包与调试留下的中间物。`release/` 里是要给用户的 .app，删掉只是「下次重新打」，
# 不是丢源码 —— 所以单独一个目标，别混进日常的 clean。
distclean:          ## 清理打包中间物（build/、release/、桌面壳日志、浏览器调试快照）
	rm -rf build release desktop.log .playwright-cli
	rm -rf web/test-results web/playwright-report web/blob-report
