from importlib.metadata import PackageNotFoundError, version

try:
    # 这里的字符串要换成 pyproject.toml 中 [project] 的 name(分发名)
    __version__ = version("forgecli")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"
