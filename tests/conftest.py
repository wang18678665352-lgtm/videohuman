"""pytest 的共用准备。

pytest 会自动找到这个文件。这里只做一件事:把 loguru 的运行日志关掉,
这样终端里剩下的就是「哪条测试过了 / 哪条没过」,不会和程序日志混在一起。

想看程序自己打出来的日志时,把下面两行暂时注释掉再跑即可。
"""

from loguru import logger

logger.remove()
