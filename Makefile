.DEFAULT_GOAL := help
# install               poetry install
# lock                  poetry lock
# check                 poetry check
# lint                  poetry run ruff check .
# format                poetry run ruff format .
# type                  poetry run mypy
# test                  poetry run pytest
# ci                    check + lint + format-check + type + test
# run                   poetry run forge
# format-check          poetry run ruff format --check .
# package               poetry build
# install-cli           build wheel and install forge with pip
# uninstall-cli         uninstall forgecli with pip
# verify-cli            verify installed forge command
.PHONY: help install lock check lint format format-check type test ci run package install-cli uninstall-cli verify-cli

# 变量定义
POETRY ?= poetry
PYTHON ?= python3.13
PACKAGE_NAME ?= forgecli
VERSION = $(shell $(POETRY) version -s)
WHEEL = dist/$(PACKAGE_NAME)-$(VERSION)-py3-none-any.whl
PIP_INSTALL_ARGS ?= --user --break-system-packages --force-reinstall

# 帮助
help:
	@echo "make install           - 创建虚拟环境并安装依赖"
	@echo "make lock              - 生成或更新 poetry.lock"
	@echo "make check             - 检查 Poetry 配置是否合法"
	@echo "make lint              - 检查 lint 问题"
	@echo "make format            - 自动格式化代码"
	@echo "make format-check      - 只检查格式，不修改文件"
	@echo "make type              - 类型检查"
	@echo "make test              - 运行测试"
	@echo "make ci                - 本地等价 CI: lint + format-check + type + test"
	@echo "make run               - 运行 CLI 入口"
	@echo "make package           - 构建 wheel 和 sdist 到 dist/"
	@echo "make install-cli       - 构建后用 python -m pip 安装 forge 命令"
	@echo "make uninstall-cli     - 用 python -m pip 卸载 forgecli"
	@echo "make verify-cli        - 验证当前 shell 可直接运行 forge"

install: 
	$(POETRY) install

lock: 
	$(POETRY) lock

check: 
	$(POETRY) check

lint: 
	$(POETRY) run ruff check .

lint-fix:
	$(POETRY) run ruff check . --fix

format: 
	$(POETRY) run ruff format .

format-check: 
	$(POETRY) run ruff format --check .

type: 
	$(POETRY) run mypy

test: 
	$(POETRY) run pytest

ci: check lint format-check type test

run: 
	$(POETRY) run forge

package:
	$(POETRY) build

install-cli: package
	$(PYTHON) -m pip install $(PIP_INSTALL_ARGS) $(WHEEL)

uninstall-cli:
	$(PYTHON) -m pip uninstall -y $(PACKAGE_NAME)

verify-cli:
	forge --version
