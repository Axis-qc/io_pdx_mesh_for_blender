![GitHub last commit](https://img.shields.io/github/last-commit/Axis-qc/io_pdx_mesh_for_blender.svg)
![Github All Releases](https://img.shields.io/github/downloads/Axis-qc/io_pdx_mesh_for_blender/total.svg)

## io_pdx_mesh for Blender

用于编辑 Clausewitz 引擎各款游戏（[游戏列表](https://en.wikipedia.org/wiki/Paradox_Development_Studio#List_of_games_developed)）模型与动画文件的 Blender 插件，由 [Paradox Development Studios](https://www.paradoxplaza.com) 的游戏格式衍生而来。

本仓库是 [ross-g/io_pdx_mesh](https://github.com/ross-g/io_pdx_mesh) 的 Blender 专用分叉。上游同时提供 Maya 工具，本仓库只保留 Blender 部分，`pdx_maya` 已移除。基于上游 0.91.0 版本，针对 Blender 5.2 做了适配与修复，详见下文改动说明。

英文版说明见 [readme.en.md](readme.en.md)。

### 测试版本

本分叉目前只在 **Blender 5.2.2 LTS** 上实测通过。其他版本没测试过，不一定能用——Blender 大版本之间的 Python API 有变动，本分叉是按 5.2 的 API 写的，不向下兼容，请自行承担风险。

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

动画导入大幅提速。关键帧直接写入动作曲线，不再逐帧触发整场景求值，33 骨骼 / 900 帧的片段从约 51 秒降到约 1 秒。旧写法保留作回退，骨架有非默认骨骼继承设置时插件会自动切过去。

修掉重名骨骼导致的动画错位与蒙皮丢失。部分原版骨架有多根同名骨，而 Blender 强制骨名唯一，导入时会被加后缀改名。插件原先按骨名建索引，重名骨会塌缩成一根。现在改为按骨名加出现顺序匹配，导出时还原文件里的原名。自己做模型的话，建议不要在骨架里使用重名骨，从源头避免这类问题。

分数帧率不再被四舍五入丢掉（如 15.06 这类），导出时若场景帧率没被改过就还原原值。

挂点导出不再受骨架姿势影响。原先导出 `.mesh` 时，只要骨架不在静止姿态，挂在骨上的挂点就会被当前姿势带偏。

修掉两类导入崩溃。只含挂点没有几何的 `.mesh`（各类 `_frame.mesh`）原先会因为找不到 `<object>` 元素直接报错中断；导入时把多个网格合并成同一材质对象的操作使用了 Blender 2.8 之前的老式上下文传参，在 5.x 上必然失败。现在前者按「只有挂点」正常导入，后者改用 `temp_override`，合并失败也会保留分开的对象而不是中断整个导入。

修掉贴图丢失。`.mesh` 里的贴图文件名不保证与文件同目录，原版大量模型把贴图放在同风格的兄弟目录里，插件原先只在 `.mesh` 所在目录查找，找不到就留下粉红贴图。现在会回退到该资产根 `gfx` 目录下按文件名检索，同名多份时优先选目录名和模型匹配的那一份。

适配 Blender 5.x 的动画数据读取，以及 `imp` 换成 `importlib`（旧模块已在 Python 3.12 移除）。

### 粒子预览

新增了 `.gfx` 注册项与 `.asset` 粒子系统的预览导入，导入后可在时间轴上擦洗查看。粒子数据里的时间量按秒处理，固定 1 秒 = 60 帧，与场景帧率无关（改场景帧率不会让粒子变快变慢）。

支持的特性：立牌与固定朝向、球面/盒/点发射器（含三轴尺寸）、发射体朝向旋转、逐粒子方差、脉冲发射、三轴自转、动画曲线（大小/透明度/颜色）、贴图与图集、力场（`point` / `planar` / `vortex` / `spin` / `turbulence` / `friction`）。

立牌粒子朝向**界面里的实际镜头**（3D 视口视角），不是场景里摆的摄像机；没有 3D 视口时才退回场景摄像机。多开窗口时跟当前活动窗口的视口。转动视角时等镜头停稳再刷新，拖动过程中不重算；镜头一直转不停（视口相机动画、连续导航）则每秒兜底刷一次，不会完全不跟随。面板里的 `Face the viewport` 可关掉这个行为，关掉后按场景摄像机处理。写了 `billboard=no` 的固定朝向粒子不跟随镜头。

粒子的坐标与朝向跟模型走同一套轴：坐标映射和 `.mesh` 导入一样是 (x, z, y)，所以粒子在预览里的前后、上下与导入的舰船一致（模型空间 -Z 是船头，导入后船头在 Blender -Y）。子系统 `position` 的轴序和世界不同，要先转一次 `(px, py, pz) → (pz, py, -px)` 再按上面的映射走：想要世界 +X 就写 `position.z`，想要世界 +Z 就写 `position.x` 的负值。`billboard=no` 的固定朝向粒子按 `particle_yaw` / `particle_pitch` 摆：默认（都写 0）是竖直立着的面片，贴图箭头朝 +X、贴图上边朝 +Y（引擎空间，即导入后的 +X 与 +Z）；`particle_yaw` 绕世界 +Y 转，`particle_pitch` 绕面片自身长轴自转（它其实是 roll：不改变箭头指向，只把面片翻成水平）。`billboard=yes` 的粒子恒正面朝镜头，此时这两个字段完全无效。

导入会自动挑一帧粒子最多的画面，不必手动找。时间轴往回拖只回退到最近的检查点，不会从头重算。回退关键帧间隔按实测调到 0.5 秒一档：1 秒级回退从几百毫秒降到几毫秒，代价是最重的几个系统同时载入时多占约 240MB 内存。`vortex` / `spin` 力场的子系统也走向量化积分，与 `point` / `turbulence` 同等待遇，逐位一致（astral_rift 等大粒子系统约 1.6~1.8 倍）。

`test/` 里有粒子功能的自检与基准脚本：`check_particle_features.py` 是 57 项特征回归，`bench_checkpoint_stride.py` 与 `bench_vortex_vec.py` 是此前两项调优的实测脚本，可复跑。

已知限制：`childsystem` 嵌套子系统只解析并在日志里提示，不参与渲染（全库仅 15 处，其中 6 处是 `hide=yes`）；`local_space` 不影响预览，因为预览里没有带变换的父实体。

`version` 为 0.96.8，`current_git_tag` 保持 0.91 不动，让插件内置更新器仍以官方 0.91 为参照。

### 使用注意事项

导出勾选、骨名规范、导入顺序、位移写法、坐标轴换算，以及动画在游戏里异常时的排查顺序，见 [使用注意事项.md](使用注意事项.md)。

### 许可与致谢

许可证见 [license.txt](license.txt)，沿用上游的 GPL-3.0-or-later。

El Tyranos，CK3 [Community Flavor Pack](https://communityflavorpack.com/) 的作者。

感谢 JetBrains 为[开源项目](https://jb.gg/OpenSourceSupport)提供的 PyCharm 授权。

<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/PyCharm_icon.png" alt="PyCharm logo." width="50" height="50">
