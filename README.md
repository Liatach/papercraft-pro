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

1. Pick the model (eyedropper uses the active object) and press **Unfold**.
2. **Show Layout** looks straight down on the pages. Split the viewport
   (View → Area → Vertical Split) to see model and layout side by side.
3. Turn on **Live Layout** and mark seams in Edit Mode (**Cut Here / Join**)
   to control where the model is cut; the pages update as you work.
4. **Export SVG Pages** writes one SVG per page with separate layers for
   faces, tabs, cut lines, mountain folds (dash-dot), valley folds (dashed)
   and glue numbers.
5. **Fold Animation → Build Fold Rig**, then drive **Fold** and **Assemble**
   or press **Keyframe Fold Animation**.

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
```

## Roadmap

* Texture printing, colour-based cuts, click-to-cut editing, print line styles
* Pop-up mechanism design add-on built on the same hinge rig
