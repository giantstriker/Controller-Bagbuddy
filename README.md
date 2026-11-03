# Controller Bagbuddy

Windows utility for mapping backpack / inventory UI grids from a screenshot, then sliding the mouse onto cells with hotkeys.

Source of truth for this build: **`code.py` in this folder** (kept in sync with the shipped EXE).

## Requirements

- Windows 10/11
- Administrator elevation (UAC) for global hotkeys and cursor control
- To run from source: Python 3.9+, [Pillow](https://pypi.org/project/Pillow/)
- To build the EXE: [PyInstaller](https://pyinstaller.org/)

## Quick start

1. Run `Controller Bagbuddy.exe` (or `python code.py`).
2. Accept the UAC prompt.
3. **Open screenshot** or **Paste screenshot**.
4. Draw / select a region, set columns and rows.
5. Set **target resolution** to match the game.
6. Bind hotkeys on the bottom bar (click a key chip → press a key).
7. Press the **Activate** hotkey, then use cell-move hotkeys.

## Build

```bat
pyinstaller --noconfirm --clean --onefile --windowed --uac-admin ^
  --name "Controller Bagbuddy" --icon backpack_tool.ico ^
  --distpath . --workpath build_tmp --specpath build_tmp code.py
```

Remove `build_tmp` after a successful build. Keep a single release EXE in this folder.

## Repository layout

| Path | Purpose |
|------|---------|
| `code.py` | Application source |
| `Controller Bagbuddy.exe` | One-file build |
| `backpack_tool.ico` | Window / EXE icon |
| `configs/*.json` | Named profiles (layouts + hotkeys + options) |
| `active_config.json` | Name of the active profile |
| `README.md` | This file (English) |
| `README.zh-CN.md` | Chinese documentation |

Legacy `layouts.json` / `hotkeys.json` are migrated into `configs/default.json` on first launch if `configs` is empty.

---

## UI overview

### Left panel

- **Open / Paste screenshot** — load the reference image used for editing.
- **Global lock** — lock all regions against accidental drag/resize.
- **Screen overlay** — fullscreen grid guides (green while mouse mode is on).
- **Layouts list** — backpack / UI modes.
  - Click a row to select.
  - Right-click → rename.
  - Key button → bind a dedicated switch hotkey for that layout.
  - **色** button → color feature points (auto layout switch). Left-click edit; right-click clear.
  - Add / Copy / Delete under the list.
- **Regions list** — large areas inside the current layout.
  - Click to select; right-click → rename / link / delete.
  - Per-region **Lock** checkbox.
  - Add / Copy / Delete; name / cols / rows / cell size fields below.
- **Cross-region move** — master switch for edge crossing via links.
- **Save / Copy JSON / Export** — persist or export the current profile.
- **Config (multi-game)** — switch, save-as, or manage named configs under `configs/`.

### Canvas (center)

- Drag to create or adjust the active region (unless locked).
- Grid points follow cols × rows and cell size.
- Active region outline is green (`#00e676`); inactive regions are white.

### Bottom hotkey bar

Two rows of actions. Click a chip, then press a key to bind. Typical actions:

| Action | Role |
|--------|------|
| Activate | Toggle mouse-move mode |
| Overlay | Toggle screen guides |
| Global lock | Toggle global lock |
| Prev / Next region | Cycle regions in the current layout |
| Cell left / right / up / down | Slide mouse one cell |
| Prev / Next layout | Cycle layouts |

There is also a full **Hotkey settings** dialog for the same bindings.

---

## Feature guide

### 1. Screenshot and regions

1. Load an image (**Open** or **Paste**).
2. Select or add a region; drag handles on the canvas.
3. Set **cols** / **rows**. Cell width/height fill automatically from region size ÷ cols/rows.
4. Origin: **Cell center** (default) or **Top-left**.
5. **Rebuild region from cell size** expands/shrinks the region box from current cell W/H.

Coordinates are in image pixels. At runtime they are scaled to the layout’s `res_w` × `res_h`.

### 2. Multiple layouts

Use separate layouts for different UI screens (bag, stash, vendor, …).

- Dedicated **switch key** per layout (button on the right of each row).
- **Prev / Next layout** hotkeys cycle the list.
- **Copy layout** duplicates regions and feature points.

### 3. Cross-region links

1. Right-click a region → link left / right / up / down to another region.
2. Enable **Cross-region move** (auto-enabled when a link is created).
3. At a region edge: with a link, the next cell step jumps into the linked region; without a link, the cursor sticks (no wrap). With the switch off, movement wraps inside the region only.

Link direction uses region **centers** (horizontal vs vertical neighbor), not screen wrap.

### 4. Color feature points (auto layout)

Detect on-screen colors to switch layouts automatically.

1. Load a screenshot that matches the live UI.
2. Click **色** on the target layout row.
3. **Start picking** → click stable pixels on the canvas → **Save & enable**.
4. Each point stores image `(x, y)`, RGB, and tolerance (default ±15).
5. While running, the app samples the mapped screen pixels ~every 0.4s. If **all** points of a layout match, it switches to that layout.
6. Right-click **色** to clear points and disable detection for that layout.
7. Empty `feature_points` = feature off (default).

If several layouts match, the current one wins when possible; a short cooldown reduces flicker.

### 5. Mouse movement

Cell moves use a short multi-step relative slide (`SendInput`), then a final position correction. Intended to look like a very fast real mouse glide rather than an instant teleport.

### 6. Profiles

- Profiles live in `configs/<name>.json`.
- Switch from the config controls on the left; **Save** writes the active file.
- Payload includes `layouts`, `hotkeys`, and `cross_region`.

Example shape:

```json
{
  "layouts": [
    {
      "name": "Bag",
      "switch_key": "d",
      "res_w": "1920",
      "res_h": "1080",
      "feature_points": [],
      "regions": [
        {
          "name": "Region1",
          "rx1": 40, "ry1": 40, "rx2": 440, "ry2": 280,
          "cols": 10, "rows": 5,
          "cell_w": "", "cell_h": "",
          "origin": "center",
          "locked": false,
          "link_left": null, "link_right": null,
          "link_up": null, "link_down": null
        }
      ]
    }
  ],
  "hotkeys": {},
  "cross_region": false
}
```

`feature_points` items: `{ "x", "y", "r", "g", "b", "tol" }` in image pixels.

---

## Help / troubleshooting

| Problem | What to try |
|---------|-------------|
| Hotkeys do nothing | Run as administrator; click the bottom-bar chip and rebind; ensure the window isn’t stuck in “press a key…” capture mode. |
| Mouse doesn’t move | Press **Activate**; confirm target resolution matches the game; check the region has cols/rows > 0. |
| Second screenshot looks wrong / stuck on old image | Use a build that invalidates the PhotoImage cache on reload (current `code.py`). Prefer **Paste** after a fresh capture. |
| Overlay in the way | Toggle overlay off; overlay is click-through on Windows when supported. |
| Cross-region never fires | Create links via region right-click; turn **Cross-region move** on. |
| Auto layout never switches | Set feature points on a live-matching screenshot; widen tolerance; ensure `res_w`/`res_h` match the monitor/game. |
| EXE won’t overwrite while building | Close the running elevated EXE; rebuild. If locked, the build may write a `*_更新.exe` — replace after exit. |
| Config lost after update | Check `configs/` and `active_config.json`; old root `layouts.json` is only auto-migrated when `configs` is empty. |

## License

Personal / local tool. No separate license file is included.
