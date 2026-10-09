"""
PDX particle preview subpackage.

 .gfx/.asset 文本解析（parser）→ 确定性逐帧模拟（simulator）→ Blender 预览构建
 （blender_preview）→ 侧栏 UI（ui）。

 author : Axis-qc (fork of ross-g/io_pdx_mesh 0.91.2)
"""

import importlib

import bpy  # type: ignore

from .. import IO_PDX_LOG
from . import parser, simulator, blender_preview, ui

importlib.reload(parser)
importlib.reload(simulator)
importlib.reload(blender_preview)
importlib.reload(ui)


def register():
    IO_PDX_LOG.info("Loading particle preview module.")
    ui.register()


def unregister():
    ui.unregister()
