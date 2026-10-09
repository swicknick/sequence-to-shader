"""The palette step of a live recreation: AE's CC Toner (and any gradient map applied LAST) reproduced exactly.

Measured: CC Toner "Pentone" is plain linear interpolation between its five colors at gray 0, .25, .5, .75, 1
(Duotone/Tritone: 2 or 3 evenly spaced stops). Checked against AE renders of three different palettes with nothing
fitted: ~0.4/255 error. So when the comp's color step is the LAST effect, the shader can apply any palette exactly.

If the color step sits earlier (before glows, blurs, contrast), palette edits cannot be reproduced accurately: blur and
glow happen in RGB after the colors. Fix it at the source: move the toner/gradient map to the end of the stack in AE
(e.g. onto the top adjustment layer) and re-render. That trade (glow computed in gray, then colored) was signed off once
it was shown side by side; it's what makes the palette editable AND exact.

    python palette.py check <gray_frames_dir> <final_frames_dir> '#05051a,#1b3bff,#7a5cff,#ff6ec7,#ffe8f5' [...more dirs+palettes]
"""
import glob, os, sys
import numpy as np
from PIL import Image


def hexrgb(h):
    h = h.strip().lstrip('#')
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255


def stops(colors, positions=None):
    pos = positions or list(np.linspace(0, 1, len(colors)))
    return pos, np.array([hexrgb(c) for c in colors], np.float32)


def toner(gray, colors, positions=None):
    """gray (any shape) -> RGB, linear between stops, clamped at the ends. Identical to the templates' palette()."""
    pos, cols = stops(colors, positions)
    v = np.clip(gray, pos[0], pos[-1])
    out = np.zeros(np.shape(gray) + (3,), np.float32)
    for i in range(1, len(pos)):
        a, b = pos[i - 1], pos[i]
        m = (v >= a) & ((v < b) if i < len(pos) - 1 else (v <= b))
        t = ((v[m] - a) / max(b - a, 1e-6))[:, None]
        out[m] = cols[i - 1] * (1 - t) + cols[i] * t
    return out


def check(gray_dir, runs, step=4):
    """runs: [(final_dir, [hex colors])]. Prints palette(gray) vs each AE render: proves the color step is exact."""
    G = sorted(glob.glob(os.path.join(gray_dir, '*.png')))
    for final_dir, colors in runs:
        F = sorted(glob.glob(os.path.join(final_dir, '*.png')))
        errs = []
        for t in range(0, min(len(G), len(F)), step):
            g = np.asarray(Image.open(G[t]).convert('L'), np.float32) / 255
            f = np.asarray(Image.open(F[t]).convert('RGB'), np.float32) / 255
            errs.append(np.abs(toner(g, colors) - f).mean() * 255)
        print(f'{os.path.basename(final_dir.rstrip("/")):20s} palette(gray) vs AE: {np.mean(errs):.3f}/255 ({np.mean(errs) / 2.55:.2f}%)')


if __name__ == '__main__':
    if len(sys.argv) < 5 or sys.argv[1] != 'check':
        sys.exit(__doc__)
    args = sys.argv[3:]
    check(sys.argv[2], [(args[i], args[i + 1].split(',')) for i in range(0, len(args), 2)])
