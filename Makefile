.PHONY: install dev run api web test lint fmt clean

install:            ## 安装运行时依赖
	uv sync

dev:                ## 安装运行时 + 开发依赖
	uv sync --extra dev

run:                ## 启动 Streamlit UI（旧界面，8501）
	uv run streamlit run app/app.py

api:                ## 启动后端 API（FastAPI，8000）
	uv run uvicorn server.main:app --reload --port 8000

web:                ## 启动前端开发服务器（Vue，5173）
	cd web && npm run dev

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
