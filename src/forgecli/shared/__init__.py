"""跨层共享的轻量基础设施。

仅放无业务方向性的基础类型（如错误基类），避免形成新的业务杂物层。
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # 读取已安装包的分发版本；字符串必须与 pyproject.toml 的 project.name 一致。
    __version__ = version("forgecli")
except PackageNotFoundError:
    # 本地源码树直接运行测试时包可能尚未安装，此时使用开发占位版本。
    __version__ = "0.0.0+dev"
