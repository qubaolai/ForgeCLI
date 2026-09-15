"""跨包共用的基础件: 错误基类, 取消令牌, JSON 读写, 日志, 分发版本号."""

from importlib.metadata import PackageNotFoundError, version

try:
    # 读取已安装包的分发版本; 字符串必须与 pyproject.toml 的 project.name 一致.
    __version__ = version("forgecli")
except PackageNotFoundError:
    # 本地源码树直接运行时包可能尚未安装, 此时使用开发占位版本.
    __version__ = "0.0.0+dev"
