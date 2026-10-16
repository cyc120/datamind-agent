"""注册 pywin32 的 DLL 目录（便携运行时专用）。

为什么需要
----------
pywin32 正常安装时会往 site-packages 放一个 ``pywin32.pth``，由它把 ``win32\\lib``
加进 ``sys.path`` 并导入 ``pywin32_bootstrap`` 注册 ``pywin32_system32`` 的 DLL 目录。
本便携包把依赖装在 ``runtime\\pylibs``（不是 site-packages），``.pth`` 不会被处理，
于是 ``import pywintypes`` 会失败 —— 而 ``qdrant_client`` 会经 ``portalocker`` 导入它，
属于启动路径上的硬依赖。

本模块由 ``runtime\\python\\Lib\\site-packages\\dm_pylibs.pth`` 的 ``import`` 行加载，
路径全部相对自身计算，整个 runtime 目录可以整体搬移（不写任何绝对路径）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _pylibs_candidates():
    # 情况一：本文件就在 runtime\pylibs 下
    yield HERE
    # 情况二：本文件被放在 runtime\python\Lib\site-packages 下
    yield os.path.normpath(os.path.join(HERE, '..', '..', '..', 'pylibs'))


def _register() -> None:
    for root in _pylibs_candidates():
        system32 = os.path.join(root, 'pywin32_system32')
        if not os.path.isdir(system32):
            continue
        if system32 not in sys.path:
            sys.path.append(system32)
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(system32)
            except OSError:
                pass
        return


_register()
