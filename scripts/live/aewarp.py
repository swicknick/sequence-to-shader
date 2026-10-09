"""After Effects Wave Warp, reimplemented in numpy. Every convention below was measured from AE renders of
coordinate maps (scripts/ae/probe_warp.jsx), not assumed, and reproduces AE to ~1-2 px (the map's own precision):

  * pixels move perpendicular to Direction (AE angles: 0 = up, 90 = right)
  * Wave Width is HALF the wavelength (width 500 -> the wave repeats every 1000 px)
  * phase is measured from the layer's top-left corner and runs backwards in time:
        phase = (p . travel) / (2 * width) - speed * t + phase0 / 360
  * displacement = -height * shape(phase)
  * shapes: sine = sin(2 pi phase); uncircle = +/-(1 - semicircle) per half cycle (cusps at the half-cycle ends);
    semicircle = 1 - 2*sqrt(1 - (2x - 1)^2): ONE arc per wavelength (measured)
  * Pinning 'All Edges' scales the displacement by a linear fade over the outer quarter of the layer's width,
    times one over the outer quarter of its height (measured: 1.5 px fit)
  * each output pixel samples the source at p + displacement(p); stacked warps compose (the second warp's
    displacement is evaluated first, then the first warp's at the moved point)

Keep any GLSL port (templates/*.template.js) identical to this file.
"""
import numpy as np

TAU = 2 * np.pi


def shape(kind, phase):
    x = phase - np.floor(phase)
    if kind == 'sine':
        return np.sin(TAU * x)
    h = (2 * x) % 1.0                                   # position inside the current half cycle
    sign = np.where(x < 0.5, 1.0, -1.0)
    semi = np.sqrt(np.clip(1 - (2 * h - 1) ** 2, 0, 1))
    if kind == 'semicircle':
        return 1 - 2 * np.sqrt(np.clip(1 - (2 * x - 1) ** 2, 0, 1))
    if kind == 'uncircle':
        return sign * (1 - semi)
    raise ValueError(kind)


def pin_factor(x, y, w):
    if w.get('pinning', 'none') == 'none':
        return 1.0
    W, H = w['layer']                                   # 'all': fade over the outer quarter of each dimension
    fx = np.clip(np.minimum(x, W - x) / (0.25 * W), 0, 1)
    fy = np.clip(np.minimum(y, H - y) / (0.25 * H), 0, 1)
    return fx * fy


def displacement(x, y, w, t):
    th = np.deg2rad(w['direction'])
    ux, uy = np.sin(th), -np.cos(th)                    # travel direction
    nx, ny = -uy, ux                                    # displacement axis
    ph = (x * ux + y * uy) / (2 * w['width']) - w['speed'] * t + w.get('phase', 0) / 360.0
    d = -w['height'] * shape(w['kind'], ph) * pin_factor(x, y, w)
    return d * nx, d * ny


def source_coords(x, y, waves, t):
    """Where the warped image at (x, y) samples the unwarped source. waves are in AE effect order."""
    for w in reversed(waves):
        dx, dy = displacement(x, y, w, t)
        x, y = x + dx, y + dy
    return x, y


def bilinear(img, x, y):
    """Sample img (H, W, C) at pixel-centre coordinates; outside the image is black (Pinning: None)."""
    H, W = img.shape[:2]
    x = x - 0.5; y = y - 0.5
    x0 = np.floor(x).astype(int); y0 = np.floor(y).astype(int)
    fx = (x - x0)[..., None]; fy = (y - y0)[..., None]

    def at(yy, xx):
        ok = (xx >= 0) & (xx < W) & (yy >= 0) & (yy < H)
        return np.where(ok[..., None], img[np.clip(yy, 0, H - 1), np.clip(xx, 0, W - 1)], 0)
    return (at(y0, x0) * (1 - fx) * (1 - fy) + at(y0, x0 + 1) * fx * (1 - fy) +
            at(y0 + 1, x0) * (1 - fx) * fy + at(y0 + 1, x0 + 1) * fx * fy)


def warp(img, waves, t):
    H, W = img.shape[:2]
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32) + 0.5
    sx, sy = source_coords(xs, ys, waves, t)
    return bilinear(img, sx, sy)


# Example (AE "Wave Warp" effect values; 'layer' = the layer's own size, needed for pinning):
# waves = [{'kind': 'uncircle', 'height': 200, 'width': 500, 'direction': 135, 'speed': 0.2},
#          {'kind': 'semicircle', 'height': 200, 'width': 70, 'direction': 135, 'speed': 7, 'pinning': 'all', 'layer': (1080, 1920)}]
# AE Wave Type menu -> kind: 1 sine, 6 semicircle, 7 uncircle (others: measure them with probe_warp.jsx before use).
