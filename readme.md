![GitHub last commit](https://img.shields.io/github/last-commit/Axis-qc/io_pdx_mesh_for_blender.svg)
![Github All Releases](https://img.shields.io/github/downloads/Axis-qc/io_pdx_mesh_for_blender/total.svg)

## io_pdx_mesh for Blender

用于编辑 Clausewitz 引擎各款游戏（[游戏列表](https://en.wikipedia.org/wiki/Paradox_Development_Studio#List_of_games_developed)）模型与动画文件的 Blender 插件，由 [Paradox Development Studios](https://www.paradoxplaza.com) 的游戏格式衍生而来。

本仓库是 [ross-g/io_pdx_mesh](https://github.com/ross-g/io_pdx_mesh) 的 Blender 专用分叉。上游同时提供 Maya 工具，本仓库只保留 Blender 部分，`pdx_maya` 已移除。基于上游 0.91.0 版本，针对 Blender 5.2 做了适配与修复，详见下文改动说明。

英文版说明见 [readme.en.md](readme.en.md)。

### 测试版本

本分叉目前只在 **Blender 5.2.2 LTS** 上实测通过。其他 Blender 版本（包括 4.2.x 与 3.6.x）没有测试过，能否正常工作不确定，在非 5.2.2 版本上使用请自行承担风险。

插件 manifest 里声明的最低版本是 Blender 4.2.0，但这只是让它能被安装，不代表功能正常。

### 安装

本仓库目前还没有发布 release，所以没有可直接下载的打包 zip。请从源码安装：

从仓库页面依次点击 `Code` → `Download ZIP`，下载后解压；或者直接克隆仓库：

```
git clone https://github.com/Axis-qc/io_pdx_mesh_for_blender.git
```

然后在 Blender 里安装：

打开 Blender，进入 `编辑 > 偏好设置`，切换到 `获取扩展` 分类，从右上角下拉菜单选择 `从磁盘安装...`，选中仓库根目录（即含有 `blender_manifest.toml` 的那一层）。也可以把整个目录复制到 Blender 的扩展目录里：

```
%APPDATA%\Blender Foundation\Blender\5.2\extensions\user_default\io_pdx_mesh
```

安装后勾选启用，在 `3D 视口` 的 `侧栏`（`视图 > 侧栏`，若未打开）里就会出现 `PDX Blender Tools` 标签页。

首次安装后需要重启 Blender 才会生效。

![界面](https://raw.githubusercontent.com/wiki/ross-g/io_pdx_mesh/images/blender/tool_ui_01.png)

### 相对上游 0.91.0 的改动

本分叉的全部改动如下。

动画导入大幅提速。关键帧现在直接写入动作曲线，不再逐帧触发整场景求值。实测 33 骨骼 / 900 帧的片段从约 51 秒降到约 1 秒，18 骨骼 / 251 帧的片段从 2.3 秒降到 0.07 秒。旧写法保留作回退，导入对话框里对应 `Legacy keyframe method` 勾选框。骨架若使用非默认的骨骼继承设置（`use_inherit_rotation` 为假，或 `inherit_scale` 不是 `FULL`），插件会自动回退到旧路径，不需要手动切换。

重名骨骼处理。部分原版骨架存在多根同名骨骼，而 Blender 强制骨骼名唯一，会在导入时自动改名并加 `.001` 一类后缀。插件原先按骨骼名建索引，重名骨会塌缩成一根，导致动画采样流错位、蒙皮权重堆到同一根骨上。现在导入器记录原始 PDX 骨名，按名称加出现顺序匹配，导出时再还原成文件里的原名。

分数帧率可正确往返。动画速度不是整数（如 15.06、15.10）的文件，导入时会取整成 Blender 场景帧率，精确值记录在骨架属性 `io_pdx_anim_fps` 上，导出时若场景帧率没被改过就还原原值，不再被四舍五入丢掉。

适配 Blender 5.x 的动画数据读取。Blender 5.x 的 Action 不再有 `.fcurves`，导入器改为遍历新的分层结构（layers、strips、channelbags、f-curves）来读写动画。

`imp` 改为 `importlib`。模块重载由 `imp.reload` 换成 `importlib.reload`，因为旧 `imp` 模块已在 Python 3.12（Blender 5.x 所用的版本）中移除。

另有一处界面兼容修补：更新检查按钮改用 `hasattr` 加 `isinstance` 判断 `LATEST_URL` 的类型，避免在 Python 3.13 下取到字符串时报错。

manifest 中 `current_git_tag` 保持 0.91，让插件内置更新器仍以官方 0.91 版本为参照；`version` 提升为 0.91.1 以标记本分叉的改动。

改动验证方式：快速关键帧路径与旧路径做过逐通道对比，曲线集合、关键帧数量与帧号完全一致，数值差异在 3.9 / 2.6 / 10.9 ulp，相对模型尺度为 4.6e-7 / 3.1e-7 / 1.3e-6，属浮点噪声量级而非位相同，差异来自旧路径多经了一道 float32 的 depsgraph 往返。

### 使用注意事项

这几条都是实际踩过的坑，导入导出前后值得对照。

导出动画必须勾选 `chk_uniform_scale`，让缩放每骨只写 1 个分量。原版 2698 个 `.anim` 文件里，每根骨的初始缩放向量长度全是 1，用 3 分量布局的文件数为 0。3 分量是 CK3 的格式，若误用，引擎每帧只前进 1/3 个读指针，缩放值整体错位，表现为动画前段正常、后段模型突然大小抽搐。

自家新做的骨架一律使用唯一骨名。改原版重名骨模型时，确认重名骨匹配逻辑生效，判据是导入后骨名带有 `.001` 后缀。

先导入网格建骨架，再导入动画。动画文件里的骨名与骨序必须和场景里的骨架对应，顺序颠倒会报 `Missing unique armature`。

动画时长不是异常来源。原版 `base_star_idle.anim` 有 10002 帧、约 666 秒，超过 900 帧的原版文件有 158 个。

整体位移要加在有蒙皮权重的躯干骨上，不要移动物体本身，也不要只加在 root 骨上。引擎的蒙皮不吃 root 骨的平移，把位移加在 root 上游戏里网格不动。位移还必须落进 `.anim` 的每帧位移采样流，只改 info 段的初始位移、或写成 Blender 里恒定的 location 关键帧，都会在导出时被压成「该骨没有位移通道」，Blender 里看着生效但游戏里不生效。

动画在游戏里表现异常时，先查 `error.log` 的映射链，最后才怀疑动画数据本身。`.asset` 是整文件替换，mod 里放一个同名 `.asset` 会把原版注册的动画全顶掉，症状是模型抽搐且不触发技能，与数据问题很像但根因完全不同。查 `error.log` 里 `Failed to find animation` 一类报错，以及 `pdxmeshtype.cpp`、`pdx_entity.cpp` 的记录。

坐标系换算：Blender 的 Z 轴对应模型的 Y 轴（PDX 为 Y-up）。插件内部做 `(x, z, y)` 对调，让东西在游戏里竖直向上，位移要写在 Blender 的 +Z 方向，写成 +Y 会在水平面内横飞。

### 许可与致谢

许可证见 [license.txt](license.txt)，沿用上游的 GPL-3.0-or-later。

El Tyranos，CK3 [Community Flavor Pack](https://communityflavorpack.com/) 的作者。

感谢 JetBrains 为[开源项目](https://jb.gg/OpenSourceSupport)提供的 PyCharm 授权。

<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/PyCharm_icon.png" alt="PyCharm logo." width="50" height="50">
