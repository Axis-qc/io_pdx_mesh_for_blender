![GitHub last commit](https://img.shields.io/github/last-commit/Axis-qc/io_pdx_mesh_for_blender.svg)
![Github All Releases](https://img.shields.io/github/downloads/Axis-qc/io_pdx_mesh_for_blender/total.svg)

## io_pdx_mesh for Blender

A Blender add-on for editing the mesh and animation files used by the various [Clausewitz Engine games](https://en.wikipedia.org/wiki/Paradox_Development_Studio#List_of_games_developed).

This is a Blender-only fork of [ross-g/io_pdx_mesh](https://github.com/ross-g/io_pdx_mesh); the upstream Maya tooling has been removed. It is based on upstream 0.91.0, adapted and fixed for Blender 5.2. Chinese version: [readme.md](readme.md).

### Tested version

Tested only on Blender 5.2.2 LTS. It targets the 5.2 API and is not backward compatible. The manifest's minimum of 4.2.0 only makes the add-on installable, not functional.

### Installation

There is no published release yet, so install from source. Download the ZIP and unpack it, or:

```
git clone https://github.com/Axis-qc/io_pdx_mesh_for_blender.git
```

In Blender go to `Edit > Preferences > Get Extensions > Install from Disk...` and pick the repository root (the folder holding `blender_manifest.toml`). Alternatively, copy the whole folder into Blender's extensions directory:

```
%APPDATA%\Blender Foundation\Blender\5.2\extensions\user_default\io_pdx_mesh
```

Enable it and a `PDX Blender Tools` tab appears in the `Sidebar` of the `3D Viewport`. Restart Blender after installing for the first time.

![UI](https://raw.githubusercontent.com/wiki/ross-g/io_pdx_mesh/images/blender/tool_ui_01.png)

### Changes since upstream 0.91.0

Animation import is dramatically faster. Keyframes go straight to the action curves instead of forcing a full scene evaluation per frame; a 33-bone / 900-frame clip dropped from about 51 seconds to about 1 second. The add-on switches back to the old method automatically for rigs with non-default bone inheritance settings.

Duplicate bone names no longer break animations. Vanilla skeletons share one name across several bones, and Blender forces unique names by adding a suffix on import, which used to collapse them into one bone when indexing by name; it now matches by name plus occurrence order and restores the original names on export. When making your own models, avoid duplicate bone names in the skeleton.

Duplicate-bone locators no longer drift on an import/export round trip. A locator's `pa` is only a bone name, so with several bones sharing it the locator was positioned against the last one while parented to the first, and export then wrote the difference back into the file (25.54 off on `star_eater`). The first bone of that name is now used consistently.

Fractional playback speed is no longer rounded away (such as 15.06), and is restored on export when the scene fps was not changed.

Locator export no longer depends on the rig's current pose.

Fixed two import crashes. A `.mesh` holding only locators and no geometry (the various `_frame.mesh` files) used to abort outright, and joining several meshes into one material object always fails on 5.x. Locator-only files now import as such, and the join uses `temp_override`, keeping separate objects if it fails.

Fixed missing textures. The texture filenames inside a `.mesh` are not necessarily next to the file; the importer previously looked only in the mesh's own directory and left magenta textures behind. It now falls back to searching the asset root's `gfx` tree by filename.

Adapted for Blender 5.x animation data access, and `imp` replaced with `importlib` (removed in Python 3.12).

Fixed several problems that used to interrupt a session. A failed update check, or a repository with no releases, no longer stops the add-on from loading and no longer claims a new version exists; an unreadable settings file degrades to empty settings instead of blocking startup; the release-notes popup no longer errors on empty text; exporting an animation whose timeline frame rate was changed no longer silently alters its duration (the imported frame rate is written back, with a warning). Meshes with n-gons, an empty armature, skeleton-only export with no armature selected, and a failed import no longer raise, and objects left behind by a failed import are cleaned up on the next load.

Fixed the clear-out deleting user objects and leaking datablocks. Clearing the previous load used to look objects up by name, and since Blender recycles a deleted object's name for a new one, a user-created object with the same name could be deleted; ownership is now tracked with a tag on the object itself, so anything you created is left alone. Animations, materials and textures are no longer given a fake user, so switching models or animations repeatedly stops accumulating them.

### Asset browser and particle preview

The main panel sits at the top of the sidebar's `PDX Blender Tools` tab and has three tabs: `Models`, `Particles` and `Entity` — a model on its own, a particle system on its own, or a model together with its mount-point particles. The import/export buttons, material and bone tools, and display and frame-rate settings all live in that same panel; the `Info` / `File` / `Tools` / `Display` / `Setup` / `Help` sub-panels remain below it for the layout you may be used to.

First set the game folder (there is a `Detect` button) and your own mod folders under `Edit > Preferences > Add-ons > IO PDX Mesh`. After that you search, pick and load from the panel instead of browsing folders by hand.

The index covers the whole `gfx/` tree and groups entries by the folder they live in; each category shows its entry count and can be hidden by clicking its eye icon, and `Mod only` keeps just your own mod's content. Press `Rescan` after installing a new mod. Building the index takes about 3 seconds for the vanilla game. Picking a file by hand is still there under `Load from file ...` at the bottom of the panel.

Particle timing is in seconds and uses the scene's `render.fps`, which you can change. Importing a skeletal animation retimes it to the same frame rate, so both the animation and the particles keep the duration they have in game.

Selecting a model or entity lists the skeletal animations that go with it (entities list theirs by `state`, models list the animations that reference them); pick one and press `Load animation`. Switching entries clears what the previous load created and leaves your own scene objects alone.

Supported: billboards and fixed orientation, point/sphere/box emitters, emitter rotation, per-particle variance, pulsed emission, three-axis spin, size/alpha/colour animation curves, textures and sprite sheets, and the `point` / `planar` / `vortex` / `spin` / `turbulence` / `friction` force fields.

The particle side fixed a batch of silent errors: malformed data (`key =` at end of line, a missing value before `}`) no longer raises or swallows the following subsystems; colours written as `r/g/b` are no longer discarded to plain white; files with a BOM no longer become inert; the animation `op="ABS"` is no longer treated as something other than "add, then take the absolute value" (three size curves have a base of 0, where getting this wrong filters out every particle); an animation's `start` / `duration` / `repeat` / `time` now take part in evaluation, see [使用注意事项.md](使用注意事项.md) for how the `time` axis changes the units; `size` and `life` draw independent random numbers, so the largest particle is no longer always the longest-lived; and deleting a preview object by hand no longer makes clearing or the reference sphere raise. The `time=spawn` / `time=system` axes have no determinable time base, so they keep their old meaning and log a note that the axis is not applied rather than guessing.

Billboard particles face the viewport camera, not a camera object placed in the scene (the scene camera is used only when there is no viewport); the `Face the viewport` toggle turns this off. Particles with `billboard=no` keep their own fixed orientation and never follow the camera.

Particles share the model's axes, so a preview lines up with an imported ship fore/aft and up/down.

Import picks the busiest frame automatically. The `Seed` field controls per-particle randomness (lifetime, speed, spawn position, orientation, colour); the same seed always reproduces the same result, and the refresh button next to it reloads after a change. The default 0 staggers subsystems by index.

Known limits: `childsystem` nested subsystems are parsed and reported in the log but not rendered; `local_space` has no effect on the preview; an entity's `attach` chain is not expanded, so only the selected entity loads; the state machine (`state_time` / `looping` / `next_state`) is not simulated and events only start after their `time`. The `Entity` tab places particles on the mount point named by the entity's `node` field; mount points come from the model where it has them, and `locator` blocks written in the entity itself are created as needed. When the mount point or the body cannot be found, that particle is **not loaded** and a red error lists the names, instead of silently piling up at the origin. Events without a `node` still play at the entity origin as before.

The interface is localised. All UI text is keyed by its English source string in `pdx_i18n.py` and follows Blender's interface language: Chinese when Blender is in Chinese, the English original otherwise. Simplified Chinese ships today; another language is one more key in the same table.

`version` is 1.0.0, with `current_git_tag` left at 0.91 so the built-in updater still treats the upstream 0.91 release as its reference.

### Usage notes

Uniform scale on export, bone naming, import order, writing translation, coordinate mapping, particle field variance, animation frame rate and duration, and the order to debug an animation that misbehaves in game are covered in [使用注意事项.md](使用注意事项.md) (Chinese).

### License and credits

See [license.txt](license.txt); this fork keeps the upstream GPL-3.0-or-later license.

El Tyranos, creator of CK3's [Community Flavor Pack](https://communityflavorpack.com/).

Kindly provided a PyCharm license from JetBrains for [Open Source projects](https://jb.gg/OpenSourceSupport).

<img src="https://resources.jetbrains.com/storage/products/company/brand/logos/PyCharm_icon.png" alt="PyCharm logo." width="50" height="50">
