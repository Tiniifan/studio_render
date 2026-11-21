# studio_render

Standalone (OpenGL) viewer for Level-5 3DS files: models, characters, effects and techniques
(`.xc`, `.xv`, `.xk`, `.pck`). It renders models like StudioRender, the rendering engine of
[Studio Eleven](https://github.com/Tiniifan/studio_eleven), **without Blender**.

## What is studio_render?

studio_render reuses Studio Eleven's rendering code (StudioRender) but runs it standalone, so you can
preview Level-5 3DS files without opening Blender.

**Supported files:**

* `.xc`, `.xv`, `.xk`, `.pck` — models and archives
* `.nut`, `.nutb` — Squirrel scripts

To use a different copy of studio_eleven (e.g. the addon installed in Blender), set the
`STUDIO_ELEVEN` environment variable to its path.

## Installation

Python 3.10 or newer, OpenGL 3.3 graphics card.

```bash
pip install -r requirements.txt
python main.py
```

On first launch, a window asks you to choose a default engine (the rendering data for a specific game).
It is installed automatically and stored in `studio_render.ini`. Subsequent launches create projects in
that engine; *Options > Default engine* changes it.

## Usage

The interface: assets on the left, player in the center, timeline at the bottom (*View > Timeline*).

### Assets

Import files with *+ Import* or drag-and-drop them onto the window. A model shows its render; other types
(skeleton, animation, cameras, light, images, script) show their icon.

* Right-click: *Rename* (`F2`), *Replace…* (another file takes its place), *Remove* (`Del`).
* Double-click: open the asset alone (a model, with its own animations), the script editor or the image.

### Images and texts

* *+ Import* also takes images: `.xi` (Level-5), `.png`, `.jpg` / `.jpeg`. Their card shows the image.
* *+ Add text*: a text drawn with a font of the game (for now the *Waza Font* of the technique names, dumped
  from Skill_Creator into `waza_font.py` and `fonts/waza_font.png`, with the accented letters and symbols of
  its atlas that Skill_Creator's table left out), in the color of a color wheel. A text is
  its own kind of asset (`.text`): double-click it (or right-click > *Edit text…*) to change its text, font
  or color. Characters missing from the font are not drawn (the window lists them).
* Right-click an image or a text > *Add to the timeline* / *Remove from the timeline*, or drag its card onto
  the timeline (it starts at the frame under the mouse).
* In the timeline: drag a clip to move it, drag one of its ends to change its duration; the eye hides it;
  right-click: *Bring forward*, *Send backward*, *Native size, centred*, *Remove from the timeline* (`Del`).
* In the player: click an image to select it, drag it to move it, drag a corner to resize it (`Shift`: free
  proportions). The images are in the screenshots and the `.mp4` export.

### Custom objects

Select multiple models (`Ctrl`+click, `Shift`+click, `Ctrl+A`), then *Group as custom object…*
(bar at the bottom of the panel, right-click or `Ctrl+G`) and name it. Head + body + cleats become a
single object without copying files. The same file can be used by several objects.

### Animations

Drag an animation onto an object (or the object onto the animation) to bind them. Right-click the
object > *Animation*, or the animation > *Animate*.

### Player

* *Game camera* (cameras from the `.xv` archive) or *Free*.
* Mouse wheel: zoom. Left click: rotate. Right click: pan.
* Playback: `Space`, `←` `→`, `J` / `L` (−10 s / +10 s), `Home` / `End`. Speed 0.25× to 2×.
* The camera button captures the view (*Options > Screenshot*).
* *Success* / *Failure* (player bar): the value of `_success` passed to the technique script.

### Engines

The *Engine* menu switches the rendering engine (IE4, IE5, etc.) for the whole project.
*Options > Default engine* sets the engine for new projects.

**IE4+IE5 · Inazuma Eleven Go Extended** is a dual engine: each archive is rendered in IE4 if IE4
knows all its render programs, otherwise IE5. This is used for Miximax heads (RGB mask + palette), so
they keep their colors. In a custom object, the body skin takes the skin color of the head.

### Custom shaders

The render programs an archive holds itself are drawn as in the game: their combiner (`NNN.cmb`, or a combiner of
the game) and their vertex program (`SHDnnn.shdc`, a PICA200 binary translated to GLSL by studio_eleven's
`rendering/studio_render/vertex_binary.py`).

### Archive cache

Archives are read once, in worker processes, then kept in `%LOCALAPPDATA%\studio_render\cache` (1 GB at most, the
least recently used files go first). The cache is dropped when the reading code changes; deleting the folder is safe.

### Projects

*File > New*, *Open…*, *Save*, *Save as…*. A `.srp` file is a zip containing assets, thumbnails and
`project.json`. Dropping a `.srp` onto the window opens it.

*Options > Home project* chooses a `.srp` opened at startup when no file is passed on the command line.

### Templates

A template is an official `.srp` distributed separately. Assets can be:

* **locked** (red padlock): not deletable, replaceable, or renameable;
* **replace-only** (orange padlock): replaceable by a file of the same type, but not removable.

The **TEMPLATE** bar at the bottom of the assets panel only appears in a template project.

### Export

*File > Export > .mp4…* exports all frames at 30 fps in 800×480, with the current camera mode.

Right-click an asset > *Export…* saves it in a common format (`export/`, one module per format):

* a model (an archive with meshes, or a custom object, its parts on one skeleton): `.dae` (Collada), `.fbx`
  (binary, readable by Blender) or `.obj`; the textures are written as `.png` next to it. `.dae` and `.fbx` hold the
  skeleton with its rest pose and the skin weights, `.obj` only the geometry as drawn at rest;
* a skeleton (nodes without mesh): `.dae` or `.fbx`;
* an image (`.xi`, image archive, `.png`, `.jpg`) or a text: `.png`.

The archives of a package have the entry, the package itself does not. Files are written Y up, 1 unit = 1 m
(Blender turns them Z up on import, like studio_eleven).

## Command line

You can pass files (or a `.srp`) on the command line. `--object NAME=A,B,C` creates a custom object,
`--animate TARGET=ANIMATION` gives it its animation:

```bash
python main.py mv01b01.xc bal00.xc cp0008a.xc uaa0219.xc ssa0219.xc whs0001.pck --object cp0008=cp0008a.xc,uaa0219.xc,ssa0219.xc --animate cp0008=whs0001_aa1.xc --animate bal00.xc=whs0001_bl1.xc
```

Headless screenshot (for tests):

```bash
python main.py cp0008a.xc uaa0219.xc ssa0114.xc --screenshot cp0008.png --size 500x700 --yaw 20
```

## Squirrel scripts

Windows tools to convert between `.nut` (source) and `.nutb` (compiled):

* `tools/NutDecompiler/` — `.nutb` to `.nut`
* `tools/SquirrelCompiler/` — `.nut` to `.nutb`

Double-click a script to edit it. *OK* compiles it and writes the original file. If compilation fails,
you can return to the code or abandon changes. *Options > Languages > Squirrel* unchecked refuses and
hides scripts.

## Building the executable

```bat
build_exe.bat
```

Requires PyInstaller (`pip install pyinstaller`). Builds `out\<version>\studio_render\` and
`out\<version>\studio_render.zip`. The build is a folder, not a single exe, so installed engines and
settings are preserved between launches.