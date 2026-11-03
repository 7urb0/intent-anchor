# intent-anchor 开发入口（对齐业界惯例：单入口 make check）
.PHONY: check test test-review lint cov live

# L0 静态门禁 + L1/L2 全量测试（CI 同款）
check: lint test

lint:
	uv run ruff check .

test:
	uv run pytest -m "not live" --cov=goal_harness --cov-report=term-missing

# 带慢测试的完整验证（发布前跑）
test-review:
	uv run pytest -m "not live"

# 只跑覆盖率
cov:
	uv run pytest -m "not live" --cov=goal_harness --cov-report=html

# L3 真实模型冒烟（需 .env，手动执行，会产生少量费用）
live:
	uv run pytest -m live -q

# L4 消融评测（真实调用，产生费用；先 runner 再 analyze）
eval:
	uv run python -m eval.runner --repeats 3 --workers 6
