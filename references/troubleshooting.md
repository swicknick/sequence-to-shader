# Troubleshooting

## numpy on Apple Silicon silently gives wrong results

Check this **before** trusting any `decompose.py` output on a Mac, not after
something looks off — it produces plausible-looking numbers, not a crash.

`pip install numpy` on macOS arm64 (numpy ≥1.26/2.x) links against Apple's
Accelerate BLAS by default, which has a real correctness bug in matrix
multiplication at the sizes this skill uses (`decompose.py`'s `Xc @ Xc.T` is
frames × pixels, easily large enough to trigger it). Symptom: `RuntimeWarning:
divide by zero / overflow / invalid value encountered in matmul` during
`decompose.py`. Confirmed independently during development of this skill —
Accelerate's `@` disagreed with `np.einsum` on the same inputs by ~9% relative
error at problem size, not a rounding-level difference, with zero exception
raised.

Fix: pin numpy to a version whose macOS wheel uses OpenBLAS instead of
Accelerate — `pip install "numpy==1.26.4"` (or any numpy build reporting
`openblas`/`openblas64`, not `accelerate`, from `numpy.show_config()`). Do
this once per environment (a venv is the easy way — see the workflow's
first step), then confirm:

```python
import numpy as np, warnings
rng = np.random.default_rng(0)
X = rng.random((250, 1_500_000)).astype(np.float32)
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    G1 = X @ X.T
    assert not w, f"BLAS warnings: {[str(x.message) for x in w]}"
G2 = np.einsum('ij,kj->ik', X, X)
rel = np.max(np.abs(G1 - G2)) / np.max(np.abs(G2))
assert rel < 1e-3, f"relative diff {rel} — BLAS is still wrong"
```

No warnings and a relative diff on the order of 1e-4 or smaller (ordinary
float32 accumulation error) means it's safe. Don't skip this on a fresh
environment — it's silent otherwise.

## The reconstruction looks soft / "lossy"

Diagnose before changing anything. Softness has two independent causes and they
need different fixes.

Run `verify.py` and read the **vertical detail ratio**. Then:

**Cause A — texture resolution too low on the sharp axis.**
If the detail ratio is low *relative to what --probe-resolutions shows is
achievable*, the basis textures are averaging away a sharp feature. Do not just
scale both axes up. Find which axis holds the detail: a horizon line, light
streak or hard horizontal edge is smooth horizontally and sharp vertically, so it
needs *vertical* resolution.

Run `decompose.py --probe-resolutions` and compare anisotropic options. In a real
960x540 case, vertical detail went 55% (240x135) -> 61% (240x540) at the same
width, while doubling width instead (480x270) reached only 59%. It then
saturated: 480x540 gave 62% for 2.4x the bytes of 240x540.

Absolute values are content-dependent — read the column, not a fixed threshold.

**Cause B — missing grain.**
The detail ratio never reaches 100% no matter the resolution, because the
low-rank model cannot represent stochastic noise and the source's grain counts as
detail in the denominator. That missing high-frequency energy also reads as
softness on screen. Fix it with `fit_grain.py`, not with more components — adding
components past the error plateau fits noise and causes shimmer.

If the ratio has saturated but it still looks wrong, the grain parameters are
probably inverted — see below.

## Grain looks like digital noise, not film grain

Three common errors, all fixed by `fit_grain.py`:

1. **Amplitude scaling inverted.** Film grain is usually stronger in shadows.
   Scaling grain *up* with brightness is very visible and wrong.
2. **Fully correlated channels.** Adding the same noise value to R, G and B
   produces flat luminance noise. Real grain is partially correlated (~0.3-0.6).
3. **Amplitude guessed.** Measure it from the residual instead.

## Banding / posterisation in smooth areas

The basis textures are 8-bit, and the first component's coefficient can be large
(several hundred), so one 8-bit step gets amplified. Check the amplified step
size; if it approaches 1/255 it will band on large smooth gradients.

Fixes, in order of preference:
- Keep grain enabled — dithering from grain masks banding effectively.
- Raise texture bit depth or store the dominant component at higher resolution.
- Reduce the coefficient range by normalising the basis differently.

## Shimmer or jitter during playback

Coefficients should vary smoothly frame to frame. Print each coefficient's
max frame-to-frame jump against its total range. If a coefficient jumps
erratically, either the source sequence has a real discontinuity (a cut), or too
many components are being used and later ones are fitting noise. Lower k.

## Error plateaus above ~2% and detail stays low

Run `scripts/triage.py`. If it says LIVE, the content has sharp features that travel across the frame; no smooth
low-rank model can represent them, and the fixes people reach for first all failed on real cases:
- more components: 10 -> 15 -> 30 lowered the error but the edges stayed smeared
- a residual texture sequence: the residual is moving sharp detail, nearly as heavy as a video
- slowing the motion in AE: the edge still travels (6.32 -> 6.31/255)

Recreate it live instead: `references/live-recreation.md`. If the After Effects project isn't available, say so
plainly and recommend an optimised video loop; shipping a smeared reconstruction is not an answer.

## Banding or stair-steps at full-screen size (live path)

Looks clean at 960 px, banded or stair-stepped at hero size. Causes and fixes, all in the live templates:
8-bit still (12-bit still via `scripts/live/still16.py`), bilinear still sampling (cubic B-spline), 8-bit output
(dither), coarse warp grid blended linearly by the GPU (Catmull-Rom upsample at load), single-sample sharp folds
(2x2 supersample). Check with `scripts/live/verify_chrome.py spec.json --banding` at the real size on the real GPU.

## Browser shows an old version after a rebuild

`python3 -m http.server` sends no cache headers; browsers keep the old ES modules. Serve with
`Cache-Control: no-store`, and switch to a new port if a browser already cached the old files.

## WebGL2 unavailable

The generated page checks and shows a message. For production, add a poster
frame fallback: export the mean texture (or a representative frame) as a static
image and show it when `getContext('webgl2')` returns null.
