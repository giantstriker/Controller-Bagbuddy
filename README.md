# Controller Bagbuddy

> 中文说明：[README.zh-CN.md](README.zh-CN.md)

Map inventory cells from a screenshot, then glide the cursor onto them with hotkeys. Windows / Python.

## Overview

Screenshot a bag, stash, or shop grid, draw the regions, and set columns and rows. After you activate, arrow hotkeys slide the mouse onto the next cell so you can aim. The app does not click for you.

Screen position is `image pixel × (target resolution / image size)`, with the screenshot’s top-left treated as the screen’s top-left. Fullscreen or borderless, with the screenshot taken at the same resolution as the game, is the case that lands correctly. The program only moves the cursor. It does not read items or inject into the game.

## Features

- [x] Open a screenshot or paste one, then drag out regions
- [x] Cell size from columns and rows; aim at cell center or top-left
- [x] Several layouts (bag / stash / shop), each with its own resolution and switch key
- [x] Bidirectional links between regions, chosen from their centers, so a step off the edge can cross
- [x] Color probes that switch layout when the live UI matches
- [x] Hotkey mouse glide: a short multi-step slide, then a position correction
- [x] Full-screen grid overlay, green for the active region, white for the others, click-through
- [x] Named profiles so each game keeps its own file

## Requirements

- Windows 10/11. Global hotkeys and cursor motion work on Windows only
- Administrator. If the process is not elevated, it relaunches itself with UAC and exits when you cancel
- From source: Python 3.9+ and [Pillow](https://pypi.org/project/Pillow/)
- To build an EXE: [PyInstaller](https://pyinstaller.org/)

On other platforms the editor still opens, but hotkeys and cursor motion do not.

## Quick start

```bat
pip install pillow
python code.py
```

1. Accept the UAC prompt.
2. **Open screenshot** or **Paste screenshot**. A full-screen capture at the game’s resolution is the one to use.
3. Drag a region on the empty canvas and set **columns** and **rows**.
4. Set the bottom-bar resolution to the game, or click **Use screenshot size**.
5. Click the key chip next to **Activate** and press a key that does not fight the game. The arrow keys are already bound to cell steps.
6. Press **Activate**, then use the arrow hotkeys to glide onto cells.

This repo does not ship an EXE. Build one with the command in [Build](#build) if you want a single file.

## Usage

### Drawing the grid

1. **Open screenshot** loads a file. **Paste screenshot** reads the clipboard. A new image replaces the previous one.
2. Drag on empty canvas to create a region, or click **Add region**. Drag inside a region to move it; drag a corner to resize.
3. Nudges apply only while mouse mode is off and the region is unlocked:
   - `Ctrl` + Left / Up: pull the right or bottom edge in by 1 px; `Ctrl` + Right / Down: push it out by 1 px
   - Add `Shift`: 10 px
   - `[` `]`: shrink or grow all four sides by 2 px
   - `{` `}`: 10 px
4. Changing **columns** / **rows** refills cell width and height as `region size ÷ count`.
5. **Cell center** (default) aims at the middle of the cell. **Top-left** aims at the cell’s top-left corner. A miss of about half a cell is usually this setting.
6. If you already know the cell size in pixels, fill width and height and click **Rebuild region from cell size**. The box grows from its top-left by `width × columns` and `height × rows`.

Runtime mapping:

```text
screen x = image x × (target width / image width)
screen y = image y × (target height / image height)
```

Each layout stores its own target size. If the game window is not at the screen origin, capture the whole screen rather than the window alone, or the cursor will be offset.

### Layouts

Split bag, stash, and shop into separate layouts. Resolution and color probes belong to that layout.

- **Add layout** / **Copy** / **Delete**. One layout must remain. Copy keeps regions and probes; the switch key starts empty.
- Click a row to select it. Right-click → **Rename layout**.
- The key chip on the row is the dedicated “switch to this layout” key. Click it, then press a key. `Esc` or right-click cancels. `Backspace` / `Delete` clears it.
- Bottom-bar **Previous layout** / **Next layout** cycle the list.

### Cross-region links

One layout can hold several grids (main bag, hotbar, a side panel).

1. Right-click a region → **Link region…** and pick another. You need at least two regions.
2. The axis comes from the two **centers**: the larger of horizontal and vertical distance becomes left/right or up/down, and the link is written both ways. An existing link on that side is replaced.
3. Creating a link turns **Cross-region move** on.
4. The region list shows marks such as `←1` `→2`. The number is the other region’s 1-based index.
5. **Clear links** drops this region’s four sides and the reverse links that pointed back at it.

At the edge of a region:

| Cross-region move | Link on that side | Result |
|-------------------|-------------------|--------|
| On | Yes | Enter the neighbor, on the same row or column, at the edge cell |
| On | No | Stay on the edge. No wrap |
| Off | — | Wrap inside this region only |

### Color probes

Probes answer “which layout is on screen?” and switch automatically. Sampling starts with the process; you do not have to activate mouse mode first.

1. Load a screenshot that matches the live UI.
2. Click **色** on that layout’s row (the button turns green once probes exist).
3. **Start picking**, then click stable pixels on the canvas: a title bar, a fixed icon, a background that does not change with the item. `Esc` or the button again stops picking.
4. Tolerance defaults to ±15. 12–18 is the range the dialog suggests. **Save & enable** writes the current tolerance onto every probe of this layout and saves immediately.
5. Right-click **色** to clear the probes and turn detection off for that layout.

Each probe stores image `(x, y)` and RGB. About every 0.4s the app maps those points through the layout’s resolution and samples the screen (`GetPixel`). **All** probes must fall inside tolerance. If several layouts match, the current one stays. After a real switch there is about a 1 second cooldown. An empty `feature_points` list means off.

Plain white or plain black matches too often. Several distinctive points on the same layout are steadier.

### Mouse glide

1. Click a bottom-bar chip, then press the key. `Esc` / right-click cancels. `Backspace` / `Delete` clears. Shift, Ctrl, and Alt alone are ignored.
2. **Activate** starts unbound. Arrows move one cell. `PageUp` / `PageDown` select the previous / next region.
3. Click **Activate**, or press its hotkey.
   - Activation turns **global lock** on so the boxes are not dragged by accident. Turning activation off does not clear the lock; uncheck **Global lock** on the left before editing.
   - If **Draw layout borders** is checked, the full-screen overlay appears, and it hides again when you deactivate.
4. Cell hotkeys move the cursor only while activated. Holding a key repeats.
5. Motion is a series of relative `SendInput` moves (about 8 steps when close, about 26 when far), then `SetCursorPos`. The whole glide is on the order of a few tens of milliseconds. While mouse mode is on, the process priority is raised; it returns to normal when you turn it off.

Actions on the bottom bar:

| Label | Role | Default |
|-------|------|---------|
| Activate | Toggle mouse mode | unbound |
| Overlay | Toggle the full-screen grid | unbound |
| Global lock | Toggle the lock on every region | unbound |
| Prev / next region | Select another region in this layout and glide to its current cell | PageUp / PageDown |
| Cell left / right / up / down | Glide one cell | arrows |
| Prev / next layout | Cycle layouts | unbound |

`cell_home` (first cell of the current region) and `cell_again` (glide to the current cell again) exist in the JSON only. The bottom bar does not show them. `HotkeyDialog` is in the source and is not opened by any button; bind keys from the bar or the file.

The overlay is a topmost full-screen grid: active region `#00e676`, others white. On Windows it is click-through, so it should not eat game input.

## Interface

### Left panel

- **Open screenshot** / **Paste screenshot**
- **Global lock**. Mouse activation checks this, and it stays checked after you deactivate
- **Screen overlay**. Manual toggle for the full-screen grid
- **Layouts**: list, add, copy, delete, name. Each row has a switch-key chip and **色**
- **Regions of the current layout**: list, add, copy, delete, name, columns, rows, cell size, origin, rebuild from cell size. **Lock** on a row locks that region only; it is disabled while global lock is on
- **Cross-region move**. On: follow a link, or stick at the edge when there is none. Off: wrap inside the region
- **Save** / **Copy JSON** / **Export**
- **Profiles**: combo box, **Switch**, **New**, **Save as**, **Delete**
- The green and gray lines under that are the current cell and the status text

Scroll the left panel when it is taller than the window. The wheel scrolls a list when the pointer is over that list.

### Canvas

Dark background. The active region is green, the others white. Grid dots follow columns, rows, and cell size. Links are drawn as arrows.

While color picking is on, a click adds a probe instead of dragging a box.

### Bottom bar

Left to right:

- **Activate**, plus **Draw layout borders**
- **Resolution** presets (640p through 4K, plus two ultrawide sizes; anything else shows as custom), width, height, **Use screenshot size**
- **Hotkeys**: two rows of chips. Click the key, then press it

Resolution is stored on the current layout and changes when you switch layouts.

## Configuration

One file per profile: `configs/<name>.json`. The active name is `active_config.json`:

```json
{ "active": "2" }
```

The repo includes `configs/2.json` (one empty region) and `configs/default.json` (links and color probes). The active profile is `2`.

From the left panel:

- **Save** writes the current file and updates `active_config.json`. It also writes `hotkeys.json` beside the program for older builds. Do not commit that file
- **Copy JSON** puts the whole profile on the clipboard
- **Export** writes a JSON anywhere you pick, without switching the active profile
- **Switch**: pick a name in the combo first. If the combo is still the current profile, a prompt asks for the name. You can save before leaving
- **New** creates a default bag (1920×1080, one default 10×5 region, default hotkeys)
- **Save as** stores the current contents under a new name and switches to it. An existing name asks before overwrite
- **Delete** refuses to remove the last profile. After a delete, the first remaining profile loads

Closing the window saves the current profile.

`\ / : * ? " < > |` in a name are replaced with underscores.

Legacy `layouts.json` / `hotkeys.json` in the program directory are imported into `configs/default.json` only when `configs/` contains no JSON yet.

Shape:

```json
{
  "layouts": [
    {
      "name": "Bag",
      "switch_key": "d",
      "res_w": "1920",
      "res_h": "1080",
      "feature_points": [
        { "x": 884.23, "y": 333.14, "r": 248, "g": 248, "b": 248, "tol": 15 }
      ],
      "regions": [
        {
          "name": "Region1",
          "rx1": 40, "ry1": 40, "rx2": 440, "ry2": 280,
          "cols": 10, "rows": 5,
          "cell_w": "40", "cell_h": "48",
          "origin": "center",
          "locked": false,
          "link_left": null, "link_right": 1,
          "link_up": null, "link_down": null
        }
      ]
    }
  ],
  "hotkeys": {
    "cell_left": "left",
    "cell_right": "right",
    "cell_up": "up",
    "cell_down": "down",
    "prev_region": "prior",
    "next_region": "next",
    "toggle_mouse": "",
    "prev_layout": "",
    "next_layout": "",
    "cell_home": "",
    "cell_again": "",
    "toggle_overlay": "",
    "toggle_global_lock": ""
  },
  "cross_region": false
}
```

| Field | Meaning |
|-------|---------|
| `switch_key` | Key that selects this layout. Empty string means unbound |
| `res_w` / `res_h` | Target resolution for this layout, stored as strings |
| `rx1,ry1,rx2,ry2` | Region box in screenshot pixels |
| `origin` | `center` or `topleft` |
| `link_*` | Zero-based index of the target region in the same layout, or `null` |
| `feature_points[].tol` | Per-channel tolerance, 0–255 |
| `hotkeys` values | Lowercase Tk keysyms: `left`, `prior` (PageUp), `next` (PageDown), `f10`, `a` |
| `cross_region` | Master switch for crossing |

Legacy `region_left` / `region_right` / `region_up` / `region_down` are cleared on load and no longer move the cursor.

## Build

From the source directory:

```bat
pyinstaller --noconfirm --clean --onefile --windowed --uac-admin ^
  --name "Controller Bagbuddy" --icon backpack_tool.ico ^
  --distpath . --workpath build_tmp --specpath build_tmp code.py
```

Delete `build_tmp` after a good build. An elevated process locks the EXE; quit it first. If the file cannot be replaced, PyInstaller may emit `*_更新.exe`. Swap it in after the process has exited.

The EXE, `*_更新.exe`, `build_tmp/`, and `*.spec` are gitignored. Leave them out of commits.

## Repository layout

| Path | Role |
|------|------|
| `code.py` | Source |
| `backpack_tool.ico` | Window and EXE icon |
| `configs/default.json` | Sample with several regions, links, and probes |
| `configs/2.json` | Sample with a single region |
| `active_config.json` | Active profile name |
| `README.md` | This file. GitHub renders it on the repository home |
| `README.zh-CN.md` | Chinese documentation |
| `.gitignore` | Cache, virtualenvs, and build output |

## FAQ

**The window flashes and disappears after I cancel UAC.**  
Global hotkeys need an administrator. Start again and accept the prompt.

**Hotkeys do nothing.**  
Use the elevated process. If a chip is stuck on “press a key…”, press `Esc`. Modifier keys alone cannot be bound. If the game already uses that key, bind another one, such as a numpad key or `F10`.

**Activate is on, but the cursor does not move, or it leaves the screen.**  
Check that the region’s columns and rows are not zero. Then compare target resolution with the screenshot. A capture of the window only, or a game that is not at the top-left of the screen, shifts the landing point. A miss of about half a cell is the origin: switch between **Cell center** and **Top-left**.

**The second screenshot is still the old image.**  
Run `code.py` from this repo, or take a new capture and use **Paste screenshot**.

**The overlay blocks the game.**  
On Windows the overlay is click-through. Uncheck **Draw layout borders**, or turn **Screen overlay** off, if you do not want the lines.

**Stepping off the edge never enters the next region.**  
Right-click **Link region…**. The arrows in the list follow the centers, not the names you gave the regions. **Cross-region move** has to be on; off means wrap inside the current region.

**Color detection never switches, or two layouts flip back and forth.**  
Pick pixels that do not change with the item, and require every probe to match. Start with tolerance 12–18. That layout’s `res_w` / `res_h` must match the live resolution or the sample point is in the wrong place. Very common colors need more probes, or a more distinctive pixel. After a switch the detector waits about a second.

**Regions will not drag, and the nudge keys do nothing.**  
Global lock or that region’s **Lock** is on. Activation turns global lock on, and deactivating does not turn it off. Click **Global lock** on the left again before editing.

**The build cannot overwrite the EXE.**  
Quit the elevated process. If you got `*_更新.exe`, rename it once the process is gone.

**A new build lost my profiles.**  
Look in `configs/` and `active_config.json` next to the program, not at the samples in the git repo. Old `layouts.json` is imported only when the config directory has no JSON files.

## License

There is no LICENSE file and no chosen open-source license. The source is published here as a local tool. Decide for yourself before you reuse or redistribute it, and follow the game’s terms if you point it at one.
