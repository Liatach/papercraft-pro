# Papercraft Unfold

A Blender extension (4.2+) that unfolds a mesh into printable papercraft
pieces with glue tabs, lays them out live on standard pages beside the model,
exports print/cut-ready SVG pages, and builds a hinge rig that animates the
sheet folding up into the model.

By Kim Lang / Liatach Makes. Written from scratch; GPL-3.0-or-later.

## Install

1. Download the latest `papercraft_unfold-x.y.z.zip` from Releases, or build
   it with `scripts/build.sh`.
2. In Blender: **Edit → Preferences → Get Extensions → ⌄ → Install from Disk**.
3. Open the sidebar (**N**) in the 3D view and pick the **Papercraft** tab.

## Use

Everything is per model: select a mesh and the **Papercraft** tab shows
*its* settings, layout and rig. Unfold as many models in one scene as you
like; selecting a layout or rig jumps to its model.

1. Select the model and press **Unfold**. Its pages appear beside it; move
   the layout object anywhere (it stays put; **Reset Layout Position**
   brings it back).
2. **Show Layout** looks straight down on the pages. Split the viewport
   (View → Area → Vertical Split) to see model and layout side by side.
3. Turn on **Live Layout** and mark seams in Edit Mode (**Cut Here / Join**)
   to control where the model is cut; the pages update as you work.
4. **Edit Cuts** (Pepakura style): hover an edge on the model *or* the
   paper layout and it lights up in both places; click to cut a fold or join
   a cut. Click a tab to move it to the other side of its seam. Works across
   split viewports. Right-click or Esc when done.
5. **Fold Animation → Build Fold Rig**, then drive **Fold** and **Assemble**
   or press **Keyframe Fold Animation**. If you change cuts or tabs later
   the panel shows *Rig is out of date*; **Rebuild Rig** keeps your
   keyframes (Edit Cuts rebuilds it for you when you finish). The bin icon
   removes the rig; **Remove Papercraft** (bin by the model name) removes
   everything generated for that model.
6. **Export SVG Pages** writes one SVG per page.

### Cut along

* **Seams**: Blender seams (Edit Mode: *Cut Here / Join*, or Edit Cuts).
* **Material Boundaries**: each material becomes its own piece.
* **Colour Boundaries**: paint regions in Vertex Paint mode (or any colour
  attribute) and every colour change becomes a cut.
* **Sharp Edges**: edges marked Sharp.

A *Join* made with Edit Cuts overrides a material/colour/sharp cut.

### Printing

* **Print: Baked Material** (default): Cycles bakes what the material shows
  onto each page, so anything prints: image textures on any UV map, layered
  paint setups such as the Paint System add-on (its `PS_UVMap` and grouped
  layers), procedurals and vertex colours. Choose *Base Colour* (no
  lighting), *Emission*, or *Full Render*, and the *Bake DPI*.
  **Texture on Tabs** carries the neighbouring art onto the tabs so tiny
  gaps at glued seams don't show tab colour.
* **Image Textures**: fast; prints the image feeding Base Color (found
  inside node groups too) through the UV map that image uses.
* **Flat Colours** / **Lines Only** for quick tests and cutting machines.
* **Bleed**: colour runs a little past each edge so imperfect cuts don't
  show white.
* **Lines**: *Default* (black cut, red mountain, blue valley), *Multiply*
  (grey lines multiplied into the art), *Colour Burn* (lines burned into the
  art), or *No Lines* (hidden layers, still there for a cutting machine).
  *Line Strength*, *Line Width* and *Hide Folds Under N°* fine-tune it.

The layout and folding meshes carry copies of all the model's UV maps and
colour layers, so the model's materials preview correctly on the pages.

### Scale options

* **Fit to Pages**: largest model that fits on N pages.
* **Model Size**: largest dimension in millimetres.
* **Fixed Ratio**: millimetres of paper per Blender unit.

## How it works

* Faces are flattened and joined greedily across the flattest edges first;
  a join is refused if pieces would overlap, and that edge becomes a cut.
* Faces bent out of plane are split into triangles (paper cannot bend).
* Each piece keeps a hinge tree (parent face, hinge edge, fold angle). The
  rig turns each hinge into a bone whose Y axis lies on the fold, driven by a
  simple driver expression (no Python auto-run needed).
* Tabs are trapezoids whose sides lean to fit the free space at each corner,
  shortened when they would run into a neighbouring piece.

## Development

The geometry core (`papercraft_unfold/core`) is pure Python and runs
outside Blender:

```bash
python tests/test_core.py          # unfold, refold and layout checks
python tests/test_blender.py       # end-to-end, needs `pip install bpy`
python tests/test_v02.py           # textures, line styles, cut rules, editor
python tests/test_v03.py           # several models, rig rebuilds, baking
```

## Roadmap

* Drag pieces around the page; PDF export
* Pop-up mechanism design add-on built on the same hinge rig
