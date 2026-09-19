![GitHub last commit](https://img.shields.io/github/last-commit/Axis-qc/io_pdx_mesh_for_blender.svg)
![Github All Releases](https://img.shields.io/github/downloads/Axis-qc/io_pdx_mesh_for_blender/total.svg)

## io_pdx_mesh for Blender

A Blender add-on for editing the mesh and animation files used by the various [Clausewitz Engine games](https://en.wikipedia.org/wiki/Paradox_Development_Studio#List_of_games_developed) made by [Paradox Development Studios](https://www.paradoxplaza.com).

This is a Blender-only fork of [ross-g/io_pdx_mesh](https://github.com/ross-g/io_pdx_mesh). Upstream also ships Maya tooling; this repository keeps only the Blender part and has removed `pdx_maya`. It is based on the upstream 0.91.0 release, with adaptations and fixes for Blender 5.2. See the change list below.

Chinese version: [readme.md](readme.md).

### Tested version

This fork has only been tested on **Blender 5.2.2 LTS**. Other versions (including 4.2.x and 3.6.x) have not been tested and may or may not work. Use at your own risk on anything other than 5.2.2.

The manifest declares a minimum of Blender 4.2.0, but that only makes the add-on installable; it does not mean it works.

### Installation

There is no published release for this repository yet, so there is no packaged zip to download. Install from source instead.

On the repository page, click `Code` then `Download ZIP` and unpack it, or clone the repository:

```
git clone https://github.com/Axis-qc/io_pdx_mesh_for_blender.git
```

Then install it in Blender. Open Blender, go to `Edit > Preferences`, switch to the `Get Extensions` category and choose `Install from Disk...` from the dropdown corner menu, then pick the repository root (the folder containing `blender_manifest.toml`). Alternatively, copy the whole folder into Blender's extensions directory:

```
%APPDATA%\Blender Foundation\Blender\5.2\extensions\user_default\io_pdx_mesh
```

Enable the check box afterwards, and a `PDX Blender Tools` tab appears in the `Sidebar` of the `3D Viewport` (`View > Sidebar` if it is closed).

Restart Blender after installing for the first time.

![UI](https://raw.githubusercontent.com/wiki/ross-g/io_pdx_mesh/images/blender/tool_ui_01.png)

### Changes since upstream 0.91.0

The complete list of changes in this fork.

Animation import is dramatically faster. Keyframes are now written directly to the action curves instead of forcing a full scene evaluation per frame. Measured on a 33-bone / 900-frame clip the import dropped from about 51 seconds to about 1 second, and an 18-bone / 251-frame clip from 2.3 seconds to 0.07 seconds. The old method is kept as a fallback and is exposed as the `Legacy keyframe method` checkbox in the import dialog. Rigs that use non-default bone inheritance settings (`use_inherit_rotation` false, or `inherit_scale` other than `FULL`) automatically fall back to the old path, so no manual switch is needed.

Duplicate bone names are handled. Some vanilla skeletons contain several bones sharing one name, and Blender forces unique bone names, renaming them with a `.001` style suffix on import. The importer previously indexed bones by name, which collapsed duplicates into one bone, misaligning the animation sample stream and piling skin weights onto a single bone. The importer now records the original PDX bone names and matches them by name plus occurrence order, then restores the original names on export.

Fractional playback speed round-trips correctly. Files whose animation speed is not a whole number (such as 15.06 or 15.10) are imported with the scene fps rounded to an integer, while the exact value is stored on the rig as `io_pdx_anim_fps` and restored on export when the scene fps was not changed.

Animation data access for Blender 5.x. Actions in Blender 5.x no longer expose `.fcurves`, so the importer reads and writes animation by walking the new layered structure (layers, strips, channelbags, f-curves).

`imp` to `importlib`. The module reload helper changed from `imp.reload` to `importlib.reload`, because the old `imp` module was removed in Python 3.12, which Blender 5.x uses.

There is also a small UI compatibility patch: the update-check button now tests `LATEST_URL` with `hasattr` plus `isinstance`, so it does not fail on Python 3.13 when the value is a string.

The manifest keeps `current_git_tag` at 0.91 so the built-in updater still treats the upstream 0.91 release as its reference, while `version` is bumped to 0.91.1 to mark this fork's changes.

How the changes were verified: the fast keyframe path was compared against the legacy path channel by channel. The curve sets, keyframe counts and frame numbers are identical, and the numeric differences are 3.9 / 2.6 / 10.9 ulp, or 4.6e-7 / 3.1e-7 / 1.3e-6 relative to model scale. That is floating-point noise rather than bit-identical output; the difference comes from the legacy path making an extra float32 round trip through the depsgraph.

### Usage notes

Uniform scale on export, bone naming, import order, writing translation, coordinate mapping, and the order to debug an animation that misbehaves in game are covered in [使用注意事项.md](使用注意事项.md) (Chinese).

### License and credits

See [license.txt](license.txt); this fork keeps the upstream GPL-3.0-or-later license.

El Tyranos, creator of CK3's [Community Flavor Pack](https://communityflavorpack.com/).

Kindly provided a PyCharm license from JetBrains for [Open Source projects](https://jb.gg/OpenSourceSupport).

<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/PyCharm_icon.png" alt="PyCharm logo." width="50" height="50">
