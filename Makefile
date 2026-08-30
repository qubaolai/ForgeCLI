.DEFAULT_GOAL := help
# install               poetry install
# lock                  poetry lock
# check                 poetry check
# lint                  poetry run ruff check $(LINT_PATHS)
# format                poetry run ruff format $(LINT_PATHS)
# type                  poetry run mypy
# arch                  poetry run python scripts/check_arch.py + check_abstractions.py
#                       + check_prompt_text.py + check_deps.py
# test                  poetry run pytest
# ci                    check + lint + format-check + type + arch + test
# run                   poetry run forge
# format-check          poetry run ruff format --check $(LINT_PATHS)
# package               web-build + poetry build
# install-cli           build wheel and install forge with pip
# uninstall-cli         uninstall forgecli with pip
# verify-cli            verify installed forge command
.PHONY: help install lock check lint format format-check type arch test web-install web-type web-test web-build ci run package install-cli uninstall-cli verify-cli

# 变量定义
POETRY ?= poetry
PYTHON ?= python3.13
PACKAGE_NAME ?= forgecli
VERSION = $(shell $(POETRY) version -s)
WHEEL = dist/$(PACKAGE_NAME)-$(VERSION)-py3-none-any.whl
PIP_INSTALL_ARGS ?= --user --force-reinstall
# lint / format 的范围. 写成显式目录而不是 `.`: 仓库根下会挂进编辑器与工具的本地
# 目录 (.claude/ 之类), 它们不是本仓库的代码, 却会让 `make lint` 报一堆与改动无关的错.
LINT_PATHS ?= src tests scripts

# 帮助
help:
	@echo "make install           - 创建虚拟环境并安装依赖"
	@echo "make lock              - 生成或更新 poetry.lock"
	@echo "make check             - 检查 Poetry 配置是否合法"
	@echo "make lint              - 检查 lint 问题"
	@echo "make format            - 自动格式化代码"
	@echo "make format-check      - 只检查格式，不修改文件"
	@echo "make type              - 类型检查"
	@echo "make arch              - 依赖方向, 抽象保留, 提示词归属与依赖声明检查 (ADR-0028, ADR-0031, ADR-0040)"
	@echo "make test              - 运行测试"
	@echo "make web-install       - 安装 Web 前端依赖"
	@echo "make web-type          - 检查 React/TypeScript 类型"
	@echo "make web-test          - 运行 Web 状态模型回归测试"
	@echo "make web-build         - 构建并嵌入 Web 静态资源"
	@echo "make ci                - 本地等价 CI: Python + Web + test"
	@echo "make run               - 启动本地 Forge Web"
	@echo "make package           - 构建 wheel 和 sdist 到 dist/"
	@echo "make install-cli       - 构建后用 python -m pip 安装 forge 命令"
	@echo "make uninstall-cli     - 用 python -m pip 卸载 forgecli"
	@echo "make verify-cli        - 验证当前 shell 可直接运行 forge"

install:
	$(POETRY) install
	cd web && npm ci

lock: 
	$(POETRY) lock

check: 
	$(POETRY) check

lint: 
	$(POETRY) run ruff check $(LINT_PATHS)

format: 
	$(POETRY) run ruff format $(LINT_PATHS)

format-check: 
	$(POETRY) run ruff format --check $(LINT_PATHS)

type: 
	$(POETRY) run mypy

arch:
	$(POETRY) run python scripts/check_arch.py
	$(POETRY) run python scripts/check_abstractions.py
	$(POETRY) run python scripts/check_prompt_text.py
	$(POETRY) run python scripts/check_deps.py

test: 
	$(POETRY) run pytest

web-install:
	cd web && npm ci

web-type:
	cd web && npm run typecheck

web-test:
	cd web && npm test

web-build:
	cd web && npm run build

ci: check lint format-check type arch web-type web-test web-build test

run: 
	$(POETRY) run forge $(FORGE_RUN_ARGS)

# 先重建前端再打包: wheel 里的静态资源不入库 (见 .gitignore), 打包时现生成.
# 少了这条依赖, 打出来的 wheel 带的是上一次谁在本机构建过的那一版.
package: web-build
	$(POETRY) build

install-cli: package
	$(PYTHON) -m pip install $(PIP_INSTALL_ARGS) $(WHEEL)

uninstall-cli:
	$(PYTHON) -m pip uninstall -y $(PACKAGE_NAME)

verify-cli:
	forge --version
