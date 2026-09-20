![GitHub last commit](https://img.shields.io/github/last-commit/Axis-qc/io_pdx_mesh_for_blender.svg)
![Github All Releases](https://img.shields.io/github/downloads/Axis-qc/io_pdx_mesh_for_blender/total.svg)

## io_pdx_mesh for Blender

A Blender add-on for editing the mesh and animation files used by the various [Clausewitz Engine games](https://en.wikipedia.org/wiki/Paradox_Development_Studio#List_of_games_developed) made by [Paradox Development Studios](https://www.paradoxplaza.com).

This is a Blender-only fork of [ross-g/io_pdx_mesh](https://github.com/ross-g/io_pdx_mesh). Upstream also ships Maya tooling; this repository keeps only the Blender part and has removed `pdx_maya`. It is based on the upstream 0.91.0 release, with adaptations and fixes for Blender 5.2. See the change list below.

Chinese version: [readme.md](readme.md).

### Tested version

This fork has only been tested on **Blender 5.2.2 LTS**. Other versions are untested and may not work: Blender's Python API changes between major releases, and this fork targets the 5.2 API. It is not backward compatible, so use other versions at your own risk.

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

Animation import is dramatically faster. Keyframes go straight to the action curves instead of forcing a full scene evaluation per frame; a 33-bone / 900-frame clip dropped from about 51 seconds to about 1 second. The old method is kept as a fallback and is used automatically for rigs with non-default bone inheritance settings.

Duplicate bone names no longer break animations. Some vanilla skeletons share one name across several bones, and Blender forces unique bone names by adding a suffix on import. The importer used to index bones by name and collapse duplicates. It now matches by name plus occurrence order and restores the original names on export. When making your own models, avoid duplicate bone names in the skeleton so this cannot come up in the first place.

Fractional playback speed is no longer rounded away (such as 15.06), and is restored on export when the scene fps was not changed.

Locator export no longer depends on the rig's current pose. Previously, exporting a `.mesh` from a rig that was not in its rest pose baked that pose into any locator parented to a bone.

Adapted for Blender 5.x animation data access, and `imp` replaced with `importlib` (the old module was removed in Python 3.12).

`version` is 0.91.2, with `current_git_tag` left at 0.91 so the built-in updater still treats the upstream 0.91 release as its reference.

### Usage notes

Uniform scale on export, bone naming, import order, writing translation, coordinate mapping, and the order to debug an animation that misbehaves in game are covered in [使用注意事项.md](使用注意事项.md) (Chinese).

### License and credits

See [license.txt](license.txt); this fork keeps the upstream GPL-3.0-or-later license.

El Tyranos, creator of CK3's [Community Flavor Pack](https://communityflavorpack.com/).

Kindly provided a PyCharm license from JetBrains for [Open Source projects](https://jb.gg/OpenSourceSupport).

<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/PyCharm_icon.png" alt="PyCharm logo." width="50" height="50">
