![GitHub last commit](https://img.shields.io/github/last-commit/Axis-qc/io_pdx_mesh.svg)
![Github All Releases](https://img.shields.io/github/downloads/Axis-qc/io_pdx_mesh/total.svg)


## IO PDX MESH
本项目用于编辑由 [Paradox Development Studios](https://www.paradoxplaza.com) 开发、基于 Clausewitz 引擎的各款游戏（见 [引擎游戏列表](https://en.wikipedia.org/wiki/Paradox_Development_Studio#List_of_games_developed)）使用的模型与动画文件。

这是一个 **仅支持 Blender** 的分叉版本。上游项目原本附带、用于 Maya 的工具已经从本仓库中 **移除**。

### 测试版本
本分叉 **仅在 Blender 5.2.2 上测试过**。其它 Blender 版本（包括 4.2.x 与 3.6.x）**均未测试**，能否正常工作未知。在非 5.2.2 版本上使用请自行承担风险。

### 下载
点击查看 [最新发布](https://github.com/Axis-qc/io_pdx_mesh/releases/latest)，下载 __*io_pdx_mesh.zip*__ 文件。


| Blender       |
| ------------- |
| ![Blender](https://raw.githubusercontent.com/wiki/ross-g/io_pdx_mesh/images/blender/tool_ui_01.png)  |


### 安装
#### Blender 安装
* 打开 Blender，进入 `用户偏好设置` 面板（`编辑 > 偏好设置...`）。
* 版本 4.2.0 及以上：
  * 切换到 `获取扩展` 分类，从右上角下拉菜单选择 `从磁盘安装...`，选中你下载的 zip 文件。
* 勾选启用该插件的复选框，之后在 `3D 视口` 的 `侧栏`（`视图 > 侧栏`，若已关闭）里会出现一个新标签页。
* 侧栏里会出现 `PDX Blender Tools` 标签页。

<br>
<br>

---

#### 本分叉在 Blender 5.2 上的改动
本分叉基于上游 0.91.0 版本，包含以下改动：

* **动画导入大幅提速。** 关键帧现在直接写入动作曲线（默认路径），不再逐帧触发整场景求值。在一个 33 骨骼 / 900 帧的片段上，导入耗时从约 51 秒降到约 1 秒（约 50 倍）。旧写法作为回退保留，用于骨骼继承设置非默认的骨架，导入对话框里提供 `Legacy keyframe method`（旧版关键帧方法）选项。
* **重名骨骼处理。** 部分原版骨架存在多个同名骨骼。Blender 强制骨骼名唯一，因此导入器现在记录原始 PDX 骨骼名，并按名称与出现顺序匹配。这修复了此类骨架上动画导入错位或静默丢失的问题（之前这个错误只会在游戏里表现出来，导入器不报错）。
* **分数播放帧率可正确往返。** 动画速度不是整数（带小数）的文件，导入时会把精确的（可能为分数）fps 记录在骨架上，导出时再还原，而不是被四舍五入丢掉。
* **Blender 5.x 动画数据访问。** 导入器通过 Blender 5.x 新增的分层动作结构（layers -> strips -> channelbags -> f-curves）来读写动画。
* **`imp` -> `importlib`。** 模块导入辅助由 `imp` 改为 `importlib.reload`，因为旧的 `imp` 模块已在 Python 3.12（Blender 5.x）中移除。

构建与验证说明：快速关键帧路径已与旧路径做过对比验证（两边的曲线集合与关键帧数量完全一致，差异仅在浮点噪声量级）。manifest 中保留 `current_git_tag` 为 0.91，使插件内置更新器仍以官方 0.91 版本为基准；manifest 的 `version` 提升到 0.91.1 以标记本分叉的改动。


#### 支持者
El Tyranos，CK3 [Community Flavor Pack](https://communityflavorpack.com/) 的作者


感谢 JetBrains 为 [开源项目](https://jb.gg/OpenSourceSupport) 提供的 PyCharm 授权。

<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/PyCharm_icon.png" alt="PyCharm logo." width="50" height="50">
