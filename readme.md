![GitHub last commit](https://img.shields.io/github/last-commit/Axis-qc/io_pdx_mesh.svg)
![Github All Releases](https://img.shields.io/github/downloads/Axis-qc/io_pdx_mesh/total.svg)


## IO PDX MESH
This project aims to allow editing of mesh and animation files used in the various [Clausewitz Engine games](https://en.wikipedia.org/wiki/Paradox_Development_Studio#List_of_games_developed) created by [Paradox Development Studios](https://www.paradoxplaza.com).

This is a Blender-only fork. The Maya tooling that the upstream project ships has been **removed** from this repository.

### Tested version
This fork has been **only tested on Blender 5.2.2**. Other Blender versions (including 4.2.x and 3.6.x) have **not** been tested and may or may not work. Use at your own risk on anything other than 5.2.2.

### Download
Click here to view the [latest release](https://github.com/Axis-qc/io_pdx_mesh/releases/latest) and download the __*io_pdx_mesh.zip*__ file.


| Blender       |
| ------------- |
| ![Blender](https://raw.githubusercontent.com/wiki/ross-g/io_pdx_mesh/images/blender/tool_ui_01.png)  |


### Installation
#### Setup for Blender
* Start Blender and open the `User Preferences` panel (`Edit > Preferences...`).
* Version 4.2.0+
  * Switch to the `Get Extensions` category and select `Install from Disk...` from the dropdown corner menu. Pick the zip file you have downloaded.
* Tick the checkbox to enable the add-on and you should see a new tab in the `Sidebar` of the `3D Viewport`. (`View > Sidebar` if you have it closed)
* The `Sidebar` will now have a `PDX Blender Tools` tab.

<br>
<br>

---

#### Blender 5.2 changes in this fork
This fork is based on the upstream 0.91.0 release with the following changes:

* **Animation import is dramatically faster.** Animation keyframes are now written directly to the action curves (default path) instead of per-frame scene evaluation. On a 33-bone / 900-frame clip the import dropped from ~51 seconds to ~1 second (about 50x faster). The old method is kept as a fallback for rigs that use non-default bone inheritance settings, and is available as the `Legacy keyframe method` option in the import dialog.
* **Duplicate bone-name handling.** Some vanilla skeletons contain multiple bones sharing one name. Blender forces unique bone names, so the importer now tracks the original PDX bone names and matches them by name and occurrence order. This fixes animation importing misaligned or silently dropped on such rigs (the error previously showed up in-game, not in the importer).
* **Fractional playback speed round-trips correctly.** Files whose animation speed is not a whole number are imported with the exact (possibly fractional) fps preserved on the rig and restored on export, instead of being rounded away.
* **Blender 5.x animation data access.** The importer reads and writes animation through the new layered action structure (layers -> strips -> channelbags -> f-curves) required by Blender 5.x.
* **`imp` -> `importlib`.** The module import helper was switched to `importlib.reload`, because the old `imp` module was removed in Python 3.12 (Blender 5.x).

Build and verify notes: the fast keyframe path was validated against the legacy path (identical curve sets and keyframe counts, differences at floating-point noise level). The manifest keeps `current_git_tag` at 0.91 so the built-in updater still treats the upstream 0.91 release as its reference; the manifest `version` is bumped to 0.91.1 to mark this fork's changes.


#### Supporters
El Tyranos, creator of CK3's [Community Flavor Pack](https://communityflavorpack.com/)


Kindly provided a PyCharm license from JetBrains for [Open Source projects](https://jb.gg/OpenSourceSupport).

<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/PyCharm_icon.png" alt="PyCharm logo." width="50" height="50">
