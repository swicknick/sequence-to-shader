---
name: sequence-to-shader
description: Convert a rendered frame sequence (After Effects, Blender, Cinema 4D, or any PNG/JPG sequence) into a tiny, measurably faithful, editable WebGL shader. It triages each animation automatically and picks the path: soft, drifting motion is compressed with SVD into basis textures plus animated coefficients; animations with sharp features that travel across the frame (folds, streaks, warped edges) are recreated live from one still plus the After Effects effect math (Wave Warp, static warps, glow, palette), with editable palettes and motion. Use this whenever the user wants to put an animated gradient, light effect, aurora, glow, atmospheric background, or motion-graphics loop onto a web page without shipping a huge video or image sequence — and especially when they say the file is too big, is blowing up load time, needs to be "rebuilt in shaders," needs to be recreated in code, or needs to match an existing render faithfully. Also use it when a previous hand-authored CSS gradient, Figma recreation, or procedural shader attempt failed to match the original closely enough. Trigger even if the user does not say the words "skill," "SVD," or "shader" — a request to get an After Effects background onto a site at a reasonable file size qualifies.
---

# Sequence to Shader

Turn a rendered frame sequence into a self-contained WebGL page that reproduces it
to a **measured** error, at a tiny fraction of the payload. One skill, two paths, and
it decides which one each animation needs (step 0):

- **Compress** (default): SVD basis textures + coefficients. Soft, drifting motion.
- **Live**: recreate the motion in the shader from one still + measured effect math.
  Sharp features that travel. Needs the After Effects project. Also the path when the
  user wants palettes or motion editable after launch.

Typical result: a 4-second 4K-sourced sequence (tens of MB of PNGs) becomes
~20-150 KB of inlined textures plus coefficients, reconstructed at under 1% mean
absolute error, resolution-independent, with no video decode.

## The core idea

Do **not** try to re-author the effect procedurally. Do **not** read gradient or
effect parameters out of the source project file.

Instead: treat the sequence as a matrix (frames x pixels) and take its SVD.
Smooth motion-graphics content is overwhelmingly low-rank — typically 4-8
components capture >99.8% of the variance. Every frame is then reconstructed as:

```
frame(t) = mean + Σ coefficient_i(t) * basis_i
```

The basis images are smooth, so they downsample and compress hard. The
coefficients are a small table. A fragment shader does the sum per-pixel.

This works regardless of how many layers, blend modes, blurs, distortions or
effects produced the original — it captures the *composited output*, not the
construction.

## Critical: never derive from source-file parameters

A recurring failure mode is reading gradient stops or effect values out of the
`.aep` / `.blend` / project file and rebuilding from those. **Those values are
pre-grade.** Downstream color grading, LUTs, hue/sat, glows and blend modes mean
the on-screen color is not the authored color. In one real case the authored ramp
was `#4A012C -> #FCA583` while the rendered output measured `#30041C -> #F5B99B`.

Any method that starts from authored parameters is reproducing a color that never
appears on screen. Always sample rendered output.

The live path follows the same rule: it reads the comp's *structure* (which effects, in
which order) but measures every effect's actual behaviour from AE renders (coordinate maps,
palette re-renders), fits whatever is approximate to the rendered output, and verifies the
real shader against AE frames.

## 0. Triage: pick the path (always run first)

```bash
python3 scripts/triage.py <frames_dir>
```

It prints a verdict with its reasons: **COMPRESS**, **LIVE** or **BORDERLINE**. The decisive
signal is the *moving sharp-edge share*: soft drift scores ~0.2 or less, sliding folds and
streaks ~0.95+. It also checks the SVD error above the grain floor against the 2% bar.

- **COMPRESS** → continue with the workflow below (steps 1-7), and still confirm visually
  with verify.py's compare sheet.
- **LIVE** → follow `references/live-recreation.md`. Ask for the After Effects project if
  you don't have it. Do **not** try more components, a residual sequence, or slowing the
  motion first: on real cases all three failed (slowing changed the error from 6.32 to
  6.31/255).
- **BORDERLINE** → run the compress path, read the compare sheet; if sharp features look
  soft or smeared, switch to LIVE.
- The user wants palettes or motion **editable after launch** → LIVE regardless (compressed
  heroes have colors and motion baked in). Say so; it's the honest scope of each path.

A page of several heroes can mix both paths; each hero gets its own verdict.

## Workflow (compress path)

### 1. Get the sequence

Ask for a rendered PNG/JPG sequence from the comp that is the actual deliverable.

- **640-960px wide is plenty.** Decomposition does not benefit from 4K and it
  keeps upload and memory sane.
- Ask for the **frame rate** — it is needed for playback timing and is not
  recoverable from the files.
- Full sequence preferred. If the user can only supply a handful of frames, the
  decomposition still works but cannot reproduce motion between them.

### 2. Decompose and pick the rank

**On macOS, first confirm numpy isn't silently wrong.** A fresh `pip install
numpy` on Apple Silicon links Apple's Accelerate BLAS by default, which has a
real matrix-multiply correctness bug at the sizes this script uses —
`RuntimeWarning: invalid value/overflow in matmul` is the symptom, but even
without warnings the numbers can be off by ~9% with no exception raised. See
`references/troubleshooting.md#numpy-on-apple-silicon-silently-gives-wrong-results`
for the fix (pin to an OpenBLAS-backed numpy build) and a quick check to
confirm it's actually fixed. Do this once per environment, before trusting
any output below.

```bash
python3 scripts/decompose.py <frames_dir> --out <model_dir>
```

This prints reconstruction error (MAE in /255 units) for a range of component
counts. **Pick k at the elbow where error plateaus.**

The plateau is meaningful: it is the film grain / stochastic noise floor. No
smooth low-rank model can represent noise, so error stopping at e.g. 1.3/255 is
not a fitting failure — it is the point where everything deterministic has been
captured. Restore the noise separately as procedural grain (step 4).

### 3. Choose texture resolution anisotropically

Do not default to a square-ish downsample. Check **where the detail lives**:

```bash
python3 scripts/decompose.py <frames_dir> --out <model_dir> --probe-resolutions
```

This reports MAE *and* a **detail ratio** (reconstructed high-frequency energy vs
the original) for several resolutions, separately for the horizontal and vertical
axes.

A horizon line, light streak or hard edge is sharp along one axis and smooth along
the other. In one real 960x540 case the probe showed vertical detail rising
52% -> 55% -> 61% for 120x67 -> 240x135 -> 240x540, while MAE barely moved
(1.78 -> 1.57 -> 1.48). Going wider instead (480x270) gained almost nothing on
the vertical axis. Match texture aspect to the content's frequency content, not
to the frame's aspect.

**MAE alone will not reveal this.** A blurred reconstruction can score well on
MAE and still look obviously soft — MAE is dominated by large smooth areas.
Always read the detail ratio too.

Detail-ratio values are content-dependent, so treat them **comparatively across
resolutions for the same clip**, not as an absolute grade. Expect the ratio to
saturate; once extra resolution stops moving it, stop paying for pixels. Note
also that the ratio never approaches 100%, because grain in the source counts as
detail the smooth model deliberately does not reproduce.

### 4. Fit the grain — do not eyeball it

```bash
python3 scripts/fit_grain.py <frames_dir> <model_dir>
```

Measures the residual (what the low-rank model cannot represent) and fits:

- **amplitude vs luminance** — film grain is usually *stronger in shadows and
  weaker in highlights*. Getting this backwards is a common and very visible
  error.
- **channel correlation** — real grain is partially correlated across R/G/B
  (often ~0.3-0.6). Adding identical noise to all three channels reads as flat
  digital noise; fully independent noise reads as chroma fizz.

### 5. Quick look while iterating

```bash
python3 scripts/build_shader.py <model_dir> --out page.html [--inline]
```

A throwaway single-file demo page (scrub bar, play/pause, grain/loop
checkboxes) for eyeballing a candidate rank/resolution fast, before you've
committed to final values. This is **not** the handoff deliverable — it has
no drop-in module, no integration example, no README. Don't hand this to a
dev team; it exists purely so you don't have to build the real package every
time you tweak `--k` or `--tex`.

### 6. Verify before shipping

```bash
python3 scripts/verify.py <frames_dir> <model_dir> --compare compare.png
```

Reports final MAE using the **actual exported, quantized, lossy textures** —
not idealized float data — and writes a side-by-side original/reconstruction
sheet. Always look at the side-by-side. Always report the error number to the
user rather than claiming it "looks close."

### 7. Build the handoff package — this is the actual deliverable

```bash
python3 scripts/build_handoff.py <frames_dir> <model_dir> --out <handoff_dir> --name "AssetName"
```

**Always finish here.** A page.html demo is not a handoff; a dev team needs a
drop-in module they can actually integrate. This produces, in one shot:

- `<slug>-background.js` — a generic, reusable, drop-in ES module (no
  dependencies) supporting `play/pause/seek/setProgress/setGrain/setLoop`,
  auto-pause off-screen via `IntersectionObserver`, `prefers-reduced-motion`,
  and a poster fallback
- `index.html` — a real integration example (positioned container, isolated
  blend context, scrim guidance) — the production, served-over-http path
- `preview.html` — a **fully self-contained** demo (module and textures
  inlined, no `fetch()`/`import` of external files) with the same
  play/pause/scrub/grain/loop controls as step 5's quick-look page. This is
  the one that must survive a plain double-click: `fetch()` and ES module
  `import` are both CORS-blocked under `file://` in Chrome, so anything that
  relies on either will silently fail (or silently show the poster and look
  fine while proving nothing) when opened that way. If you hand-edit
  anything here, re-verify with headless Chrome using
  `--use-angle=swiftshader --enable-unsafe-swiftshader` — plain
  `--disable-gpu` reports WebGL2 as unavailable and masks real bugs behind
  the poster fallback.
- `poster.webp` — reconstructed from the actual shipped (quantized) textures
  at the final frame, not lifted from the pristine source — it should look
  like what the shader draws, not like an idealized version of it
- `README.md` — the real measured MAE/worst-frame/payload numbers for *this*
  asset, not templated placeholders
- `textures/` — `model.json` + the basis/mean WebPs

`--name` drives the class name and filename (`"Aurora"` →
`AuroraBackground`, `aurora-background.js`). Re-running this after a source
change regenerates every file from the current model — nothing here is
meant to be hand-maintained after the fact.

## Reporting to the user

State the measured error, the payload, and what was assumed. Be explicit about:

- mean absolute error vs the source sequence, in % or /255
- total payload vs the original sequence size
- the assumed frame rate (it is an input, and wrong fps is the most likely
  silent defect)
- that grain is procedural and will not match frame-for-frame by design

If error plateaus above ~2% and the detail ratio stays low, say so plainly rather
than shipping it — see `references/troubleshooting.md` for the fallback.

## Reference files

- `references/troubleshooting.md` — softness, banding, shimmer, high residual,
  and when to fall back to a residual texture sequence
- `references/integration.md` — dropping the result into a real site, isolating
  blend context, responsive sizing, poster frames, reduced-motion

## Live path at a glance

Full method in `references/live-recreation.md`. Tools:

- `scripts/ae/probe_warp.jsx` — coordinate maps through any AE distortion (coarse + fine)
- `scripts/live/aewarp.py` — AE Wave Warp, measured (shapes, pinning, phase, time)
- `scripts/live/coordmap.py` — decode coordinate maps; compact warp grid (log-encoded squeezes, coverage outline)
- `scripts/live/palette.py` — CC Toner/gradient map applied last: exact palettes; `check` proves it on AE renders
- `scripts/live/still16.py` — 12-bit still (no banding), sized at the quality/KB knee
- `scripts/live/fill_template.py` + `templates/live-warp-glow.template.js`, `templates/live-warped-copies.template.js`
- `scripts/live/verify_chrome.py` — the real module in Chrome vs AE frames; `--banding` at hero size on the real GPU

Report the same way as the compress path: error per palette (measured on the real shader), payload, what's exact
vs fitted, and anything visibly different.
