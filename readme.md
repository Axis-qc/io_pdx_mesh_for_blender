![GitHub last commit](https://img.shields.io/github/last-commit/Axis-qc/io_pdx_mesh_for_blender.svg)
![Github All Releases](https://img.shields.io/github/downloads/Axis-qc/io_pdx_mesh_for_blender/total.svg)

## io_pdx_mesh for Blender

用于编辑 Clausewitz 引擎各款游戏（[游戏列表](https://en.wikipedia.org/wiki/Paradox_Development_Studio#List_of_games_developed)）模型与动画文件的 Blender 插件。

本仓库是 [ross-g/io_pdx_mesh](https://github.com/ross-g/io_pdx_mesh) 的 Blender 专用分叉，上游的 Maya 工具已移除，基于上游 0.91.0 为 Blender 5.2 做了适配与修复。英文版见 [readme.en.md](readme.en.md)。

### 测试版本

只在 Blender 5.2.2 LTS 上实测通过。按 5.2 的 API 写的，不向下兼容。manifest 里声明的最低版本 4.2.0 只保证装得上，不代表功能正常。

### 安装

仓库还没有发布 release，从源码安装。下载 ZIP 解压，或：

```
git clone https://github.com/Axis-qc/io_pdx_mesh_for_blender.git
```

在 Blender 里走 `编辑 > 偏好设置 > 获取扩展 > 从磁盘安装...`，选中仓库根目录（含 `blender_manifest.toml` 的那一层）；也可以直接把整个目录复制到扩展目录：

```
%APPDATA%\Blender Foundation\Blender\5.2\extensions\user_default\io_pdx_mesh
```

启用后 `3D 视口` 的 `侧栏` 里会出现 `PDX Blender Tools` 标签页。首次安装需要重启 Blender。

![界面](https://raw.githubusercontent.com/wiki/ross-g/io_pdx_mesh/images/blender/tool_ui_01.png)

### 相对上游 0.91.0 的改动

动画导入大幅提速。关键帧直接写入动作曲线，不再逐帧触发整场景求值，33 骨骼 / 900 帧的片段从约 51 秒降到约 1 秒。骨架有非默认骨骼继承设置时插件会自动切回旧写法。

修掉重名骨骼导致的动画错位与蒙皮丢失。原版骨架有多根同名骨，Blender 强制骨名唯一会在导入时加后缀改名，原先按骨名建索引会让重名骨塌缩成一根；现在按骨名加出现顺序匹配，导出时还原原名。自己做模型的话，建议不要在骨架里使用重名骨。

修掉重名骨挂点在导入导出往返后位置偏移。挂点的 `pa` 只写骨名，同一骨架里多根同名骨时，原先位置按最后一根算、父子却绑第一根，导出按父子反算就把偏差写了进去（`star_eater` 上偏 25.54）。现在统一取首根同名骨。

分数帧率不再被四舍五入丢掉（如 15.06 这类），导出时若场景帧率没被改过就还原原值。

挂点导出不再受骨架姿势影响。

修掉两类导入崩溃。只含挂点没有几何的 `.mesh`（各类 `_frame.mesh`）原先会直接报错中断；把多个网格合并成同一材质对象的操作在 5.x 上必然失败。现在前者按「只有挂点」正常导入，后者改用 `temp_override`，合并失败也保留分开的对象。

修掉贴图丢失。`.mesh` 里的贴图文件名不保证与文件同目录，插件原先只在 `.mesh` 所在目录查找，找不到就留下粉红贴图；现在会回退到该资产根 `gfx` 目录下按文件名检索。

适配 Blender 5.x 的动画数据读取，以及 `imp` 换成 `importlib`（旧模块已在 Python 3.12 移除）。

修掉几个会中断使用的问题。更新检查失败或仓库没有 release 时不再让插件加载失败，也不再谎报「有新版本」；配置文件读不出来时退化成空配置而不是让插件起不来；点发布说明弹窗不再因为空文本报错；动画导出时间轴被改过帧率后不再静默改变时长（现在按导入时的帧率写回并给出警告）；有 n-gon 的网格、空骨架、只导骨架且没选中骨架、导入中途失败等情形不再报错，中途失败留下的对象下次加载会被清掉。

修掉清场误删与资源堆积。清理上一次加载的对象原先按名字找，Blender 会把被删对象的名字回收给新对象，导致用户自建的同名对象被删掉；现在以对象上的归属标记为准，用户自己建的东西一律不动。动画、材质与贴图不再设 `fake user`，反复切换模型或动画不再一路堆积。

### 资源浏览器与粒子预览

侧栏 `PDX Blender Tools` 下是 `Info`（最上）、`PDX Tools`、`Display`、`Help`、`Settings` 五个面板。`PDX Tools` 是主入口，分 `Models` / `Particles` / `Entity` 三个标签页，对应「单独导模型」「单独看粒子」「模型和挂点粒子一起」，里面是索引、搜索、条目列表与粒子预览；场景帧率、引擎、材质、骨骼与网格顺序在 `Settings` 里。

先在 `编辑 > 偏好设置 > 插件 > IO PDX Mesh` 里配好游戏目录（有 `Detect` 按钮自动找）和自己的 mod 目录，之后面板里搜索、选中、加载即可，不用翻文件夹。

索引扫整个 `gfx/`，分类按文件所在目录自动分好，每个分类右边是条目数，点掉眼睛图标就整类隐藏，`Mod only` 只看自己 mod 的内容。装了新 mod 按 `Rescan` 重建。原版规模下建索引约 3 秒。手动选文件保留了，在面板底部的 `Load from file ...`。

粒子时间按秒走，帧率取场景的 `render.fps`，可以自己调。导入骨骼动画时会按同一个帧率重定时，所以动画和粒子的时长都跟游戏里一致。

选中模型或 entity 后，面板下方会列出它带得上的骨骼动画（entity 按 `state` 列，模型按引用它的动画列），选中一条按 `Load animation` 加载。切换条目会自动清掉上一次加载的对象，你在场景里自己建的东西不动。

支持立牌与固定朝向、点/球面/盒发射器、发射体朝向旋转、逐粒子方差、脉冲发射、三轴自转、大小/透明度/颜色动画曲线、贴图与图集，以及 `point` / `planar` / `vortex` / `spin` / `turbulence` / `friction` 六种力场。

粒子侧本轮修掉一批静默错：解析坏数据（`key =` 写在行尾、`}` 前少写值）不再抛异常或吞掉后续子系统；`color` 用 `r/g/b` 写的颜色不再被丢成纯白；带 BOM 的文件不再整份失效；动画 `op="ABS"` 不再被当成加法之外的算法（三处 size 曲线的基准是 0，算错会让粒子整批被尺寸过滤掉）；动画的 `start` / `duration` / `repeat` / `time` 正式参与求值，`time` 轴的单位差异见 [使用注意事项.md](使用注意事项.md)；`size` 与 `life` 的随机量各自独立，不再出现「越大的粒子活得越久」；用户在场景里手动删掉预览对象后再点清场或参照球不再报错。`time=spawn` / `time=system` 的计时基准无法从数据确定，按原语义处理并记一条日志说明未生效，不做猜测。

立牌粒子朝向 3D 视口里的镜头，不是场景里摆的摄像机（没有视口时才退回场景摄像机）；面板上的 `Face the viewport` 可以关掉这个行为。写了 `billboard=no` 的固定朝向粒子不跟随镜头。

粒子和模型走同一套轴系，预览里的前后上下与导入的舰船一致。

导入会自动挑一帧粒子最多的画面，不用手动找。`Seed` 控制每颗粒子的随机量（寿命、速度、发射位置、朝向、颜色），同一个种子结果完全一样，改完按旁边的刷新按钮重载才生效；默认 0 按子系统序号自动错开。

已知限制：`childsystem` 嵌套子系统只解析并在日志里提示，不参与渲染；`local_space` 不影响预览；entity 的 `attach` 挂载链不展开，只加载选中的那一个；状态机（`state_time` / `looping` / `next_state`）不模拟，只按事件的 `time` 延迟起播。`Entity` 那一栏会把粒子挂到 entity 的 `node` 指定的挂点上，挂点优先用模型自带的，entity 自己写的 `locator` 会自动补建；挂点或本体找不到时该粒子**不加载**并弹红色报错列出名字，不会静默堆在原点。没写 `node` 的粒子照常在 entity 原点播放。

界面文字做了本地化。所有界面文本以英文原文为键集中放在 `pdx_i18n.py`，跟着 Blender 的界面语言切换：界面是中文时显示中文，英文时回落英文原文。目前提供简体中文，加别的语言只需在同一个表里加一个语种键。

`version` 为 1.0.0，`current_git_tag` 保持 0.91 不动，让插件内置更新器仍以官方 0.91 为参照。

### 使用注意事项

导出勾选、骨名规范、导入顺序、位移写法、坐标轴换算、粒子字段的方差语义、骨骼动画的帧率与时长，以及动画在游戏里异常时的排查顺序，见 [使用注意事项.md](使用注意事项.md)。

### 许可与致谢

许可证见 [license.txt](license.txt)，沿用上游的 GPL-3.0-or-later。

El Tyranos，CK3 [Community Flavor Pack](https://communityflavorpack.com/) 的作者。

感谢 JetBrains 为[开源项目](https://jb.gg/OpenSourceSupport)提供的 PyCharm 授权。

<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/PyCharm_icon.png" alt="PyCharm logo." width="50" height="50">
