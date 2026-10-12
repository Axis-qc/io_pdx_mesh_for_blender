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

Fixed two import crashes. A `.mesh` that holds only locators and no geometry (the various `_frame.mesh` files) used to abort because no `<object>` element was found, and joining several meshes into one material object used the pre-2.8 context-passing form, which always fails on 5.x. Locator-only files now import as such, and the join uses `temp_override`, keeping separate objects instead of aborting the whole import if it fails.

Fixed missing textures. The texture filenames stored inside a `.mesh` are not necessarily next to the file itself; plenty of vanilla models keep their textures in a sibling style directory. The importer previously looked only in the mesh's own directory and left magenta textures behind. It now falls back to searching the asset root's `gfx` tree by filename, preferring the match whose directory corresponds to the model when several share a name.

Adapted for Blender 5.x animation data access, and `imp` replaced with `importlib` (the old module was removed in Python 3.12).

### Particle preview

Added preview import for `.gfx` registrations and `.asset` particle systems, scrub-able on the timeline once imported. Particle timing is in seconds at a fixed 60 frames per second, independent of the scene frame rate (changing the scene fps will not speed particles up or slow them down).

Supported features: billboard and fixed orientation, sphere/box/point emitters (including all three box axes), emitter rotation, per-particle variance, pulsed emission, three-axis spin, animation curves (size/alpha/colour), textures and sprite sheets, and force fields (`point` / `planar` / `vortex` / `spin` / `turbulence` / `friction`).

Billboard particles face the **actual viewport camera** (the 3D view's own view), not a camera object placed in the scene; the scene camera is only a fallback when there is no 3D viewport. With several windows open it follows the viewport of the active window. Rotating the view refreshes once the camera settles, rather than recomputing while you drag; if the view never settles (viewport camera animation, continuous navigation) it falls back to refreshing once per second so it never stops following entirely. The `Face the viewport` toggle in the panel turns this off, after which the scene camera is used. Particles with `billboard=no` keep their own fixed orientation and never follow the camera.

Particles share the model's axes: positions are mapped with the same (x, z, y) conversion the `.mesh` importer uses, so a preview lines up with an imported ship fore/aft and up/down (-Z is forward in model space, which lands on Blender -Y). A subsystem's `position` block uses a different axis order from the world and is rotated first, `(px, py, pz) → (pz, py, -px)`, before that mapping: write `position.z` for world +X, and the negated `position.x` for world +Z. Fixed-orientation (`billboard=no`) particles follow their own `particle_yaw` / `particle_pitch`: with both at 0 the quad stands upright with the texture arrow pointing along +X and the texture top edge along +Y (engine space, i.e. +X / +Z once imported). `particle_yaw` rotates about world +Y, while `particle_pitch` spins the quad about its own long axis (it is really a roll: it does not change where the arrow points, it only tips the quad flat). `billboard=yes` particles always face the camera, and both fields are ignored entirely for them.

Import picks the busiest frame automatically, so you do not have to hunt for one. Scrubbing backwards only rewinds to the nearest checkpoint rather than replaying from the start. The checkpoint interval is tuned to half a second based on measurements: rewinding by a second now takes a few milliseconds instead of hundreds, at the cost of roughly 240 MB of extra memory when the heaviest systems are all loaded. Subsystems with `vortex` / `spin` force fields now also use the vectorised integrator, same as `point` / `turbulence`, bit-for-bit identical (around 1.6~1.8x on large systems like astral_rift).

`test/` contains the self-check and benchmark scripts for the particle preview: `check_particle_features.py` is the 57-assertion feature regression; `bench_checkpoint_stride.py` and `bench_vortex_vec.py` are the measurement scripts behind two earlier tunings and can be re-run.

Known limits: `childsystem` nested subsystems are parsed and reported in the log but not rendered (only 15 occurrences in the whole game, 6 of them `hide=yes`); `local_space` has no effect on the preview, as the preview has no transformed parent entity.

`version` is 0.96.8, with `current_git_tag` left at 0.91 so the built-in updater still treats the upstream 0.91 release as its reference.

### Usage notes

Uniform scale on export, bone naming, import order, writing translation, coordinate mapping, and the order to debug an animation that misbehaves in game are covered in [使用注意事项.md](使用注意事项.md) (Chinese).

### License and credits

See [license.txt](license.txt); this fork keeps the upstream GPL-3.0-or-later license.

El Tyranos, creator of CK3's [Community Flavor Pack](https://communityflavorpack.com/).

Kindly provided a PyCharm license from JetBrains for [Open Source projects](https://jb.gg/OpenSourceSupport).

<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/PyCharm_icon.png" alt="PyCharm logo." width="50" height="50">
