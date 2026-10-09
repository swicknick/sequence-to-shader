#!/usr/bin/env python3
"""
Verify a built model against the source sequence.

    python3 verify.py <frames_dir> <model_dir> [--compare compare.png]

Measures error using the ACTUAL exported, quantized, lossy textures — not
idealized float data — so the number reported is the number that ships.
Also writes a side-by-side sheet. Always look at it; MAE alone can hide softness.
"""
import argparse, json, os, sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_frames, detail_ratio, weighted_ratio, interpret_detail


def load_tex(model, name, lo, hi, W, H):
    a = np.asarray(Image.open(os.path.join(model, name + ".webp")
                              ).convert("RGB")).astype(np.float32) / 255.0
    a = a * (hi - lo) + lo
    n = (a - a.min()) / max(a.max() - a.min(), 1e-9)
    up = np.asarray(Image.fromarray((n * 255).astype(np.uint8)
                                    ).resize((W, H), Image.BICUBIC))
    return up.astype(np.float32) / 255.0 * (a.max() - a.min()) + a.min()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("frames")
    p.add_argument("model")
    p.add_argument("--compare", default="compare.png")
    a = p.parse_args()

    A = load_frames(a.frames, verbose=False)
    T, H, W, _ = A.shape
    meta = json.load(open(os.path.join(a.model, "model.json")))
    K = meta["K"]
    coef = np.array(meta["coef"], dtype=np.float32)

    md = load_tex(a.model, "mean", meta["mean"]["lo"], meta["mean"]["hi"], W, H)
    Bd = np.stack([
        load_tex(a.model, f"b{i}", meta["layers"][i]["lo"],
                 meta["layers"][i]["hi"], W, H) for i in range(K)])

    errs, dvs = [], []
    for t in range(T):
        r = np.clip(md + np.tensordot(coef[t, :K], Bd, axes=(0, 0)), 0, 1)
        errs.append(float(np.abs(r - A[t]).mean()))
        o = np.asarray(A[t])
        dvs.append(detail_ratio(r, o, axis=0))

    mae = np.mean(errs) * 255
    # Detail ratio is only meaningful on frames that HAVE detail. Near-empty
    # frames are grain-dominated, and grain is added procedurally at runtime
    # rather than modelled, so including them understates the ratio badly.
    dv = weighted_ratio(dvs)
    print(f"MAE (shipped textures) : {mae:.3f}/255  ({mae/2.55:.2f}%)")
    print(f"worst frame            : {max(errs)*255:.3f}/255 "
          f"(frame {int(np.argmax(errs))})")
    print(f"vertical detail ratio  : {dv*100:.1f}%  "
          f"(structure only, grain filtered out)")
    print(f"  -> {interpret_detail(dv)}")
    if mae > 5.1:
        print("  -> over 2%: see references/troubleshooting.md before shipping.")

    idxs = [int(x) for x in np.linspace(0, T - 1, 4)]
    tiles = []
    for t in idxs:
        r = np.clip(md + np.tensordot(coef[t, :K], Bd, axes=(0, 0)), 0, 1)
        pair = np.concatenate([np.asarray(A[t]), r], axis=0)
        tiles.append(np.asarray(Image.fromarray((pair * 255).astype(np.uint8)
                                                ).resize((300, 338))))
    Image.fromarray(np.concatenate(tiles, axis=1)).save(a.compare)
    print(f"\nwrote {a.compare}  (top row = original, bottom = reconstruction)")
    print(f"frames shown: {idxs}")


if __name__ == "__main__":
    main()
