.PHONY: install dev run test lint fmt clean

install:            ## 安装运行时依赖
	uv sync

dev:                ## 安装运行时 + 开发依赖
	uv sync --extra dev

run:                ## 启动 Streamlit UI
	uv run streamlit run app/app.py

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
