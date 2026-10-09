#!/usr/bin/env python3
"""
Fit procedural grain parameters from the low-rank residual.

    python3 fit_grain.py <frames_dir> <model_dir>

The residual (original minus the smooth reconstruction) is the stochastic noise
the model cannot represent. Rather than eyeballing a grain amplitude, measure it.

Fits two things that are commonly got wrong:
  * amplitude vs luminance — film grain is usually STRONGER IN SHADOWS.
  * channel correlation — real grain is partially correlated across R/G/B.
    Identical noise on all three reads as flat digital noise; fully independent
    noise reads as chroma fizz.

Updates model.json in place.
"""
import argparse, glob, json, os, sys
import numpy as np
from PIL import Image

BINS = [(0, .1), (.1, .2), (.2, .3), (.3, .45), (.45, .6), (.6, .8), (.8, 1.01)]


def load_frames(d):
    files = sorted(
        f for f in glob.glob(os.path.join(d, "*"))
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    )
    return np.stack([
        np.asarray(Image.open(f).convert("RGB"), dtype=np.float32) / 255.0
        for f in files
    ])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("frames")
    p.add_argument("model")
    a = p.parse_args()

    A = load_frames(a.frames)
    T, H, W, _ = A.shape
    meta = json.load(open(os.path.join(a.model, "model.json")))
    K = meta["K"]
    coef = np.array(meta["coef"], dtype=np.float32)

    # Rebuild full-precision basis from the frames so the residual reflects the
    # MODEL's limits, not texture quantization.
    X = A.reshape(T, -1)
    mean = X.mean(axis=0)
    Xc = X - mean
    G = Xc @ Xc.T
    w, V = np.linalg.eigh(G)
    i = np.argsort(w)[::-1]
    V = V[:, i]
    B = (V[:, :K].T @ Xc).astype(np.float32)
    B /= np.linalg.norm(B, axis=1, keepdims=True)
    cf = (Xc @ B.T).astype(np.float32)

    xs, ys, ccs = [], [], []
    step = max(1, T // 15)
    for t in range(0, T, step):
        R = (cf[t] @ B + mean).reshape(H, W, 3)
        res = A[t] - R
        lum = np.clip(R.mean(axis=2), 0, 1)
        for lo, hi in BINS:
            m = (lum >= lo) & (lum < hi)
            if m.sum() > 4000:
                xs.append((lo + hi) / 2)
                ys.append(res[m].std())
        ccs.append(np.corrcoef(res[:, :, 0].ravel()[::53],
                               res[:, :, 2].ravel()[::53])[0, 1])

    if len(xs) < 3:
        print("Not enough luminance spread to fit; using flat grain.")
        slope, base = 0.0, float(np.mean(ys)) if ys else 0.0
    else:
        slope, base = np.polyfit(np.array(xs), np.array(ys), 1)
    corr = float(np.clip(np.nanmean(ccs), 0.0, 1.0)) if np.isfinite(ccs).any() else float('nan')
    if not np.isfinite(corr):
        # a channel with no variance (e.g. a saturated or empty one) makes corrcoef NaN; NaN in model.json is invalid
        # JSON and breaks the page. AE's Noise effect (color) is uncorrelated across channels, so 0 is the safe value.
        print('channel correlation undefined (a channel has no variance) -> using 0.0')
        corr = 0.0

    u = np.sqrt(12.0)  # a uniform hash in [-0.5,0.5] has std 1/sqrt(12)
    meta["grain"] = {"base": round(float(base * u), 5),
                     "slope": round(float(slope * u), 5),
                     "chanCorr": round(corr, 3)}
    json.dump(meta, open(os.path.join(a.model, "model.json"), "w"))

    print(f"grain std at luma 0.0 : {base*255:.2f}/255")
    print(f"grain std at luma 1.0 : {(base+slope)*255:.2f}/255")
    print(f"channel correlation   : {corr:.3f}")
    if slope < 0:
        print("  -> stronger in shadows (typical film grain)")
    else:
        print("  -> stronger in highlights (unusual; verify visually)")
    print(f"\nwrote grain params to {a.model}/model.json")


if __name__ == "__main__":
    main()
