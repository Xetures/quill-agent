.PHONY: install dev api serve web build test lint fmt clean

install:            ## 安装运行时依赖
	uv sync

dev:                ## 安装运行时 + 开发依赖
	uv sync --extra dev

api:                ## 启动后端（8000）；web/dist 存在时连前端一起托管
	uv run uvicorn server.main:app --reload --port 8000

serve:              ## 启动服务：端口顺延 + 自动开浏览器（日常用它）
	uv run quill serve --open-browser

web:                ## 启动前端开发服务器（Vue，5173）
	cd web && npm run dev

build:              ## 构建前端产物（之后 make api 即可单进程使用）
	cd web && npm install && npm run build

cli:                ## 运行命令行入口
	uv run quill -v

test:               ## 运行测试
	uv run pytest

lint:               ## 静态检查
	uv run ruff check .

fmt:                ## 格式化代码
	uv run ruff format .

clean:              ## 清理缓存文件
	rm -rf .pytest_cache .ruff_cache .venv
	find . -type d -name __pycache__ -exec rm -rf {} +
