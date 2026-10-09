# Live recreation: when compression can't do it

`scripts/triage.py` sends a sequence here when **sharp features travel across the frame** (folds, streaks, crisp
edges sliding over the image). SVD rebuilds frames from a few fixed images, so it can only smear a moving edge.
Slowing the motion doesn't fix that. Instead, recreate what After Effects did, live in the shader: ship one still
image plus the effect math, and compute every frame.

What it gets you, on two real heroes: 0.75–1.4% mean error against AE, 27–51 KB total, sharp at any size, no loop
seam, and **every effect setting becomes a live config value** (palette, wave speed and shape, glow, interactions).
What it needs: the **After Effects project** (to measure the effects), and more work than the compress path.

The governing rule is the same as the rest of this skill: **measure, never assume.** Every convention below was wrong
at least once before it was measured.

## 1. Read the comp and decide what's live

List the effect stack and order of operations. Typical live-able structure:
**still image → geometric warps → edge/glow effects → tone → palette.**

- The **still** is the layer before any time-varying effect (a blurred shape, a gradient). Render it from AE once.
- **Warps** (Wave Warp, rotation, scale) become shader math. Static distortions (Warp/Arc, Mesh Warp, Bulge) are
  measured as coordinate grids (step 3).
- **Glows/blurs** become small blur passes (live-warp-glow template), fitted to AE's output.
- **Palette** must be the **last** step for it to be editable and exact (step 4).

**Shape and text layers are continuously rasterized: AE applies their transform (rotation, scale) BEFORE their
effects.** So a wave warp on a rotating shape layer stays fixed on screen while the shape turns underneath. Render the
still with the layer's scale but zero rotation, then rotate the still before warping. Getting this backwards gave
~20% error; correct order gave 0.3%.

## 2. Render the passes from AE

Script AE (ExtendScript via `osascript -e 'tell application "Adobe After Effects <version>" to DoScriptFile "<path>"'`).
Render through the Render Queue with an Output Module template named "PNG Sequence" (see `scripts/ae/probe_warp.jsx`
for how to create it once). Typical passes, framed exactly like the deliverable:

- `still` (one frame, warps/glows/palette off), `gray` (everything except the final palette), `final` (as shipped),
  `final-<palette>` (the same comp re-rendered with 1–2 other palettes: proves palette edits are exact), and any
  isolated layer you need to fit (e.g. the edge layer alone).

AE scripting pitfalls that each cost a round:
- After a failed export AE may report "Error Code 3, restart After Effects" for every later export, including
  `saveFrameToPng`. Only a restart clears it.
- Don't wrap `renderQueue.render()` in an undo group (AE shows an "undo group mismatch" dialog that blocks scripts).
- Concatenating a point-property value into a string can throw "divide by zero"; log scalars only.
- Effect point parameters on shape layers are measured from the comp-sized layer's top-left (content origin at the
  centre).
- Set `app.beginSuppressDialogs()` sparingly: it turns catchable errors into a hard stop at `endSuppressDialogs`.

## 3. Measure the effects

**Wave Warp** is solved: `scripts/live/aewarp.py` reproduces AE to the coordinate map's own precision (sharp
checkerboard: ~3/255, <0.1% of pixels wrong). Measured conventions:
- pixels move **perpendicular** to Direction (AE angles: 0 = up, 90 = right)
- **Wave Width is half the wavelength**; phase is measured from the layer's top-left and runs **backwards** in time:
  `phase = (p·travel)/(2·width) − speed·t + phase0/360`, `displacement = −height · shape(phase)`
- shapes: sine; **uncircle** = ±(1 − semicircle) per half cycle (cusps = sharp folds); **semicircle** = one arc per
  wavelength, `1 − 2·√(1 − (2x − 1)²)`. Measure any other shape before using it.
- **Pinning: All Edges** = linear fade over the outer quarter of the layer's width × the outer quarter of its height.
- stacked warps compose (the last effect's displacement is evaluated first).

**Any other distortion**: run `scripts/ae/probe_warp.jsx` (coarse + 8 fine coordinate maps through the effect, with a
margin), then `coordmap.decode()` → `coordmap.build_grid()`. Read the docstring of `coordmap.py`; in short:
- capture with a **margin**: warps push content outside the layer (one Arc warp reached 2,000+ px beyond it)
- coverage comes from the coarse map; stripe-boundary pixels pin to the boundary (else: zig-zags)
- a **steep squeeze** (perspective/horizontal distortion) needs `log_edge_x`: one Arc squeezed 40× toward an edge;
  plain displacement grids sampled the wrong region and drew zig-zags
- **don't trust hardware bilinear on a coarse grid at hero size**: it kinks at every grid line (stair-steps along
  sharp lines). The warped-copies template Catmull-Rom-upsamples the grid 4× on the GPU once at load.
- a 32 px grid of half floats is ~30 KB; finer grids didn't help in our measurements.

## 4. Make the palette editable and exact

`scripts/live/palette.py check gray/ final/ '<5 colors>' final-ocean/ '<5 colors>'` should report ~0.4/255 on every
palette, including ones nothing was fitted to. CC Toner (Pentone) = linear between 5 colors at gray 0/.25/.5/.75/1.

If the palette isn't last in the comp, **move it last in AE** (onto the top adjustment layer) and compare the
before/after side by side. A per-mask or delta model can't make palette edits accurate once glows and contrast come after the
color: we measured 35–41% error on a held-out palette.

## 5. Build the twin, fit what's approximate, then the module

1. Write a numpy **twin** of the shader (same math, same buffer sizes): it's what you fit with and what proves the
   shader is right. The real shader in Chrome should match the twin to ~0.3–0.7/255.
2. Fit approximate effects (Deep Glow, Brightness & Contrast) by least squares against the AE `gray` pass: a 17-point
   brightness curve + edge line + edge glow + two star glows got 2.8/255 on gray, 0.9–1.4% in color.
3. Fill a template with `scripts/live/fill_template.py`: `templates/live-warp-glow.template.js` (still → warps →
   glows → palette) or `templates/live-warped-copies.template.js` (warped precomp copies, measured static warp,
   Screen blend). Both share one API: `set()`, `reset()`, `setPalette()`, `getConfig()`, `play/pause/seek/setProgress`,
   `addRipple`, `use()`/`remove()` interactions (pointer ripple, scroll fade, parallax), reduced motion, pause
   off-screen, poster fallback.

## 6. Quality at hero size (do not skip)

Everything looked clean at 960 px and still failed full-screen. Ship these, all already in the templates:
- **12-bit still** (`scripts/live/still16.py`): AE's 8-bit render repeats a gray for up to 50 px → bands. Smoothing +
  12-bit lossless PNG, decoded once on the GPU into a half-float texture. Size at the quality knee: ~16 KB at 240×135
  or 135×240 for a heavily blurred star; measure the knee for each still (`still16.make` at a few sizes vs AE).
- **Cubic B-spline** sampling of the still (no bilinear diamonds), **±1-step output dither** (no 8-bit bands),
  **2×2 supersampling** of sharp folds (no jaggies), **Catmull-Rom-upsampled** warp grids (no stair-steps).
- Check with `scripts/live/verify_chrome.py spec.json --banding` at the real hero size **on the real GPU**
  (`"gpu": true`) and look at the PNG: flat-run length catches bands, only your eyes catch stair-steps.
- Rule for sizes: best visual quality first, at the knee of the quality/KB curve. Never trade visible artifacts
  for a few KB.

## 7. Verify and report

`scripts/live/verify_chrome.py spec.json`: the real module in Chrome vs AE frames, per palette. Report mean and worst
error per palette, the payload (gzipped module + still + grids), what's fitted vs exact, and any visible difference.

## Previewing locally: the cache trap

Python's `http.server` sends no cache headers, so browsers keep old ES modules and a normal refresh shows nothing new.
Serve with `Cache-Control: no-store` from the start, and if a browser already cached a version, switch port: a new
origin has an empty cache.
