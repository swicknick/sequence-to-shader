#!/usr/bin/env python3
"""
Decide which path a sequence needs: COMPRESS (SVD basis textures, the default pipeline) or LIVE (recreate the
animation's motion in the shader; references/live-recreation.md). Run this first, on any sequence.

    python3 triage.py <frames_dir> [--fps 24]

Why two paths: SVD reconstructs every frame as a blend of a few fixed images. That's ideal for soft, drifting color
(fog, glows, gradients drifting) and fails for SHARP FEATURES THAT TRAVEL across the frame (a crisp fold, streak or
edge sliding over the image): each position of a sharp edge is effectively a new image, so a few components can only
smear it. Slowing the motion doesn't help (the edge still travels; measured: 6.32 vs 6.31/255).

Signals (all measured on the frames themselves; tuned on five real After Effects heroes, see below):
  * excess error   SVD error at k=10 (the decompose cap) MINUS the grain floor (what a 3 px blur removes). Grain is
                   restored procedurally, so only error above it counts. Over 5.1/255 (2%) fails the bar.
  * moving edges   share of the frames' strong structural edges (found on lightly blurred frames, so grain doesn't
                   count) that are in a different place in the next frame. Soft drift and spinning blobs score ~0.2
                   or less; sliding folds and streaks score ~0.95+. This is the decisive signal.

Verdict rules:
  LIVE       moving-edge share >= 0.5, or excess error > 5.1/255
  COMPRESS   moving-edge share < 0.3 and excess error <= 5.1/255
  BORDERLINE otherwise: build the compressed version, read verify.py's compare sheet; if sharp features look soft
             or smeared, go LIVE.
Calibration (excess error, moving-edge share -> correct path): soft bloom 0.11, 0.19 compress; ringed orb 0.03, 0.04
compress; warped orb 4.07, 0.19 compress; rotating wave-warped star 5.98, 0.94 live; double wave-warped star 4.96,
1.00 live.
Always confirm a COMPRESS verdict visually with verify.py's side-by-side: MAE can hide a smeared edge.
"""
import argparse, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_frames, _smooth
from decompose import svd_basis


def blur(img, r):
    return np.stack([_smooth(img[..., c], r) for c in range(img.shape[-1])], -1)


def edges(g):
    gx = np.abs(np.diff(g, axis=1))[:-1, :]; gy = np.abs(np.diff(g, axis=0))[:, :-1]
    return np.hypot(gx, gy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('frames'); ap.add_argument('--fps', type=float, default=24)
    a = ap.parse_args()
    A = load_frames(a.frames)
    T = A.shape[0]
    if A.shape[2] > 960:                                   # analysis doesn't need more than 960 px wide
        s = int(np.ceil(A.shape[2] / 960)); A = A[:, ::s, ::s]
    mean, B, coef, w = svd_basis(A, kmax=10)
    sample = range(0, T, max(1, T // 20))
    m10 = np.mean([np.abs((coef[t, :10] @ B[:10] + mean).reshape(A.shape[1:]) - A[t]).mean() for t in sample]) * 255
    noise = np.mean([np.abs(A[t] - blur(A[t], 1)).mean() for t in sample]) * 255
    excess = m10 - noise

    # moving structural edges: strong edges of lightly blurred frames that weren't there in the previous frame
    E = [edges(blur(A[t], 2).mean(-1)) for t in range(0, T, max(1, T // 40))]
    thr = np.percentile(np.concatenate([e.ravel() for e in E]), 99)
    strong = [e > thr for e in E]
    moving = sum((E[i] * (strong[i] & ~strong[i - 1])).sum() for i in range(1, len(E)))
    total = sum((E[i] * strong[i]).sum() for i in range(1, len(E)))
    share = moving / max(total, 1e-9)

    print(f'frames {T}  analysed at {A.shape[2]}x{A.shape[1]}')
    print(f'SVD error at k=10: {m10:.2f}/255; grain floor {noise:.2f}/255; excess {excess:.2f}/255 ({excess / 2.55:.2f}%)')
    print(f'moving sharp-edge share: {share:.2f}')
    if share >= 0.5 or excess > 5.1:
        verdict = 'LIVE'
        why = ('sharp edges travel across the frame; a few blended components can only smear them' if share >= 0.5
               else 'the error stays above the 2% bar')
    elif share < 0.3:
        verdict, why = 'COMPRESS', 'soft, low-rank motion: the default SVD pipeline reproduces it'
    else:
        verdict, why = 'BORDERLINE', 'build the compressed version first and judge the verify.py compare sheet'
    print(f'\nVERDICT: {verdict}: {why}.')
    if verdict != 'COMPRESS':
        print('Live path needs the After Effects project (to measure the effects): see references/live-recreation.md.')


if __name__ == '__main__':
    main()
