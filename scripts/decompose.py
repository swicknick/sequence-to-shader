#!/usr/bin/env python3
"""
Decompose a rendered frame sequence into basis textures + per-frame coefficients.

    python3 decompose.py <frames_dir> --out <model_dir> [--k 6] [--tex 240x540]
    python3 decompose.py <frames_dir> --out <model_dir> --probe-resolutions

Writes <model_dir>/{mean,b0..bN}.webp and model.json.
"""
import argparse, io, json, os, sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_frames, detail_ratio, weighted_ratio

KMAX = 10


def svd_basis(A, kmax=KMAX):
    """Return (mean, basis[kmax], coef[T,kmax]) via SVD across the time axis."""
    T = A.shape[0]
    X = A.reshape(T, -1)
    mean = X.mean(axis=0)
    Xc = X - mean
    G = Xc @ Xc.T
    w, V = np.linalg.eigh(G)
    i = np.argsort(w)[::-1]
    w, V = np.clip(w[i], 0, None), V[:, i]
    k = min(kmax, T - 1)
    B = (V[:, :k].T @ Xc).astype(np.float32)
    B /= np.linalg.norm(B, axis=1, keepdims=True)
    coef = (Xc @ B.T).astype(np.float32)
    return mean, B, coef, w


def report_rank(A, mean, B, coef, w):
    T = A.shape[0]
    tot = w.sum()
    print("\n rank   variance      MAE(/255)")
    cum = 0.0
    prev = None
    for k in range(1, B.shape[0] + 1):
        cum += w[k - 1]
        err = 0.0
        for t in range(0, T, max(1, T // 25)):
            r = coef[t, :k] @ B[:k] + mean
            err += np.abs(r.reshape(A.shape[1:]) - A[t]).mean()
        err = err / len(range(0, T, max(1, T // 25))) * 255
        flag = ""
        if prev is not None and prev - err < 0.05:
            flag = "  <- plateau (noise floor)"
        print(f"  k={k:<3d} {100*cum/tot:9.5f}%   {err:7.3f}{flag}")
        prev = err
    print("\nPick k at the elbow. The plateau is the grain/noise floor —")
    print("restore it with fit_grain.py rather than adding components.")


def roundtrip(img, tw, th, H, W, quality=95):
    """Downsample -> webp -> upsample, returning (reconstructed, bytes)."""
    lo, hi = float(img.min()), float(img.max())
    q = ((img - lo) / (hi - lo) * 255).clip(0, 255).astype(np.uint8)
    sm = Image.fromarray(q).resize((tw, th), Image.LANCZOS)
    buf = io.BytesIO()
    sm.save(buf, "WEBP", quality=quality, method=6)
    nbytes = buf.tell()
    buf.seek(0)
    up = np.asarray(Image.open(buf).convert("RGB").resize((W, H), Image.BICUBIC))
    return up.astype(np.float32) / 255.0 * (hi - lo) + lo, nbytes


def probe(A, mean, B, coef, k):
    T, H, W, _ = A.shape
    meanI = mean.reshape(H, W, 3)
    Bk = B[:k].reshape(k, H, W, 3)
    cands = [
        (W // 8, H // 8), (W // 4, H // 4), (W // 4, H // 2),
        (W // 4, H), (W // 3, H), (W // 2, H // 2), (W // 2, H),
    ]
    print("\n  texture     MAE(/255)   detail-V  detail-H   size")
    for tw, th in cands:
        tot = 0
        md, nb = roundtrip(meanI, tw, th, H, W); tot += nb
        Bd = []
        for i in range(k):
            b, nb = roundtrip(Bk[i], tw, th, H, W); Bd.append(b); tot += nb
        Bd = np.stack(Bd)
        errs, dv, dh = [], [], []
        for t in range(0, T, max(1, T // 12)):
            r = np.clip(md + np.tensordot(coef[t, :k], Bd, axes=(0, 0)), 0, 1)
            errs.append(np.abs(r - A[t]).mean())
            dv.append(detail_ratio(r, np.asarray(A[t]), axis=0))
            dh.append(detail_ratio(r, np.asarray(A[t]), axis=1))
        print(f"  {tw:4d}x{th:<4d}  {np.mean(errs)*255:8.3f}   "
              f"{weighted_ratio(dv)*100:6.1f}%  {weighted_ratio(dh)*100:6.1f}%"
              f"   {tot/1024:6.1f} KB")
    print("\nDetail ratio matters as much as MAE: a soft reconstruction can score")
    print("well on MAE and still look blurry. Match texture aspect to where the")
    print("sharp features are — a horizontal streak needs vertical resolution.")


def export(A, mean, B, coef, k, tw, th, outdir, fps):
    T, H, W, _ = A.shape
    os.makedirs(outdir, exist_ok=True)
    meanI = mean.reshape(H, W, 3)
    Bk = B[:k].reshape(k, H, W, 3)
    meta = {"K": k, "texW": tw, "texH": th, "fps": fps, "srcW": W, "srcH": H,
            "frames": int(T)}

    def save(img, name):
        lo, hi = float(img.min()), float(img.max())
        q = ((img - lo) / (hi - lo) * 255).clip(0, 255).astype(np.uint8)
        Image.fromarray(q).resize((tw, th), Image.LANCZOS).save(
            os.path.join(outdir, name + ".webp"), quality=95, method=6)
        return {"lo": lo, "hi": hi}

    meta["mean"] = save(meanI, "mean")
    meta["layers"] = [save(Bk[i], f"b{i}") for i in range(k)]
    meta["coef"] = [[round(float(coef[t, i]), 3) for i in range(k)]
                    for t in range(T)]
    meta.setdefault("grain", {"base": 0.0, "slope": 0.0, "chanCorr": 0.4})
    json.dump(meta, open(os.path.join(outdir, "model.json"), "w"))
    tot = sum(os.path.getsize(os.path.join(outdir, f)) for f in os.listdir(outdir))
    print(f"\nwrote {outdir}  ({tot/1024:.1f} KB total)")
    print("next: fit_grain.py, then build_shader.py, then verify.py")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("frames")
    p.add_argument("--out", default="model")
    p.add_argument("--k", type=int, default=None)
    p.add_argument("--tex", default=None, help="WxH, e.g. 240x540")
    p.add_argument("--fps", type=float, default=25)
    p.add_argument("--probe-resolutions", action="store_true")
    p.add_argument("--kmax", type=int, default=KMAX, help="components to analyse (default 10; the shader needs k+1 <= 15 texture units)")
    a = p.parse_args()

    A = load_frames(a.frames)
    mean, B, coef, w = svd_basis(A, kmax=max(a.kmax, a.k or 0))
    report_rank(A, mean, B, coef, w)

    k = a.k or 6
    if a.probe_resolutions:
        probe(A, mean, B, coef, k)
        return

    H, W = A.shape[1], A.shape[2]
    tw, th = (map(int, a.tex.split("x")) if a.tex else (W // 4, H))
    export(A, mean, B, coef, k, tw, th, a.out, a.fps)


if __name__ == "__main__":
    main()
