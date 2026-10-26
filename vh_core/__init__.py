"""vh_core — videohuman 内核：唯一业务逻辑所在，无 UI 依赖。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("videohuman")
except PackageNotFoundError:  # 源码直跑未安装时
    __version__ = "0.0.0+local"
