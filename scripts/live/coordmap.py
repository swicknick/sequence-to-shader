"""Measure a static After Effects warp (Warp/Arc, Mesh Warp, Bezier Warp, CC Bend It, Bulge...) with coordinate maps,
and turn it into a small grid the shader can sample. Used by the live-recreation path (references/live-recreation.md).

How the maps work (rendered by scripts/ae/probe_warp.jsx and probe_warp_fine.jsx):
  * coarse map: red = source x / layer width, green = source y / layer height, run through the effect. Reading a
    pixel tells you where AE sampled it from, to ~4-7 px (8-bit).
  * fine maps: N maps whose ramps each cover 1/N of the layer (clamped outside), so each 8-bit step is ~W/N/255 px.
    The coarse map says which stripe a pixel is in; the fine map gives the exact position.
  * render the probe comp with a MARGIN around the layer: warps often push content beyond the layer's own rectangle.

Lessons baked in (each one cost a debugging round):
  * a pixel exactly on a stripe boundary reads 1.0 in the stripe before and 0.0 in the stripe after; treat that as
    the boundary itself, never as "missing" (missing pixels fall back to the coarse map and jitter by a few px,
    which shows up as zig-zags along every boundary).
  * coverage (where the warped layer exists at all) must come from the coarse map, not from "source outside the
    layer": forward/mesh warps leave empty areas that no displacement can express.
  * where the warp squeezes the layer hard (perspective-like distortion), store log(edge - x) instead of dx: a coarse
    grid can follow it; plain displacement can't (it samples the wrong part of the layer and draws zig-zags).
  * the shader must NOT rely on hardware bilinear filtering of a coarse grid at hero sizes: linear blending kinks at
    every grid line (visible stair-steps along sharp features). Upsample once on the GPU with Catmull-Rom
    (see templates/live-warped-copies.template.js, FRAG_ARC_UP).
"""
import numpy as np
from PIL import Image

EPS = 0.002


def rd(path):
    return np.asarray(Image.open(path).convert('RGB'), np.float32) / 255


def decode(coarse_png, fine_pngs, layer_size):
    """-> float32 (H, W, 2): source x, y in layer px for every pixel of the probe frame; NaN where nothing covers it.
    fine_pngs: list of N paths in stripe order (may be empty: coarse precision only)."""
    W, H = layer_size
    wide = rd(coarse_png)
    valid = (wide.sum(-1) > 0.01) & (wide[..., 0] < .997) & (wide[..., 1] < .997)
    cx, cy = wide[..., 0] * W, wide[..., 1] * H
    if fine_pngs:
        fine = np.stack([rd(p) for p in fine_pngs]); N = len(fine_pngs)
        sx = _decode_axis(cx, fine[..., 0], W / N, N)
        sy = _decode_axis(cy, fine[..., 1], H / N, N)
        sx = np.where(np.isnan(sx), cx, sx); sy = np.where(np.isnan(sy), cy, sy)
    else:
        sx, sy = cx, cy
    out = np.stack([sx, sy], -1).astype(np.float32)
    out[~valid] = np.nan
    return out


def _decode_axis(coarse, fine, span, N):
    out = np.full(coarse.shape, np.nan, np.float32)
    k0 = np.clip(np.floor(coarse / span).astype(int), 0, N - 1)
    for dk in (0, -1, 1, -2, 2):                              # the stripe the coarse map points at, then neighbours
        k = np.clip(k0 + dk, 0, N - 1)
        v = np.take_along_axis(fine, k[None], 0)[0]
        inside = np.isnan(out) & (v > EPS) & (v < 1 - EPS)
        out[inside] = (k[inside] + v[inside]) * span
    for b in range(1, N):                                     # exactly on a stripe boundary
        on = np.isnan(out) & (fine[b - 1] >= 1 - EPS) & (fine[b] <= EPS) & (np.abs(coarse - b * span) < 2 * span)
        out[on] = b * span
    out[np.isnan(out) & (fine[0] <= EPS) & (coarse < span)] = 0.0
    out[np.isnan(out) & (fine[N - 1] >= 1 - EPS) & (coarse > (N - 1) * span)] = N * span
    return out


def build_grid(field, margin, window, step=32, log_edge_x=None):
    """Compact grid of the measured warp, every `step` px over `window` = (x0, y0, x1, y1) in layer space.
    field: decode() output; the probe frame's top-left is layer point (-margin, -margin).
    Channels: [x-channel, source y - y, signed distance to the covered area (px, + inside)], float16-safe.
      x-channel = log(log_edge_x - source x) when log_edge_x is given (steep squeeze toward that x), else source x - x.
    Empty cells are filled by diffusion so sampling near the outline stays smooth."""
    x0, y0, x1, y1 = window
    sub = field[y0 + margin:y1 + margin, x0 + margin:x1 + margin]
    ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32) + 0.5
    valid = np.isfinite(sub[..., 0])
    SX, SY = np.nan_to_num(sub[..., 0]), np.nan_to_num(sub[..., 1])
    QX = np.log(np.clip(log_edge_x - SX, 1e-3, None)) if log_edge_x is not None else SX - xs
    QY = SY - ys
    gw, gh = (x1 - x0) // step + 1, (y1 - y0) // step + 1
    gx = np.zeros((gh, gw), np.float32); gy = np.zeros((gh, gw), np.float32); gv = np.zeros((gh, gw), bool)
    hs = step // 2
    for j in range(gh):
        for i in range(gw):
            sl = (slice(max(0, j * step - hs), j * step + hs), slice(max(0, i * step - hs), i * step + hs))
            m = valid[sl]
            if m.sum() > step:
                gx[j, i], gy[j, i], gv[j, i] = QX[sl][m].mean(), QY[sl][m].mean(), True
    known = gv.copy()
    for _ in range(max(gw, gh)):
        if known.all():
            break
        for g in (gx, gy):
            p = np.pad(np.where(known, g, 0), 1, mode='edge'); c = np.pad(known.astype(np.float32), 1, mode='edge')
            s = p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]; n = c[:-2, 1:-1] + c[2:, 1:-1] + c[1:-1, :-2] + c[1:-1, 2:]
            fill = ~known & (n > 0); g[fill] = (s / np.maximum(n, 1))[fill]
        known = known | (np.pad(known, 1)[:-2, 1:-1] | np.pad(known, 1)[2:, 1:-1] | np.pad(known, 1)[1:-1, :-2] | np.pad(known, 1)[1:-1, 2:])
    v4 = valid[::4, ::4]; pad = np.pad(v4, 1, mode='edge')
    edge = v4 & ~(pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:])
    by, bx = np.nonzero(edge); bx = bx * 4 + 2.0; by = by * 4 + 2.0
    gys, gxs = np.mgrid[0:gh, 0:gw].astype(np.float32) * step
    sdf = np.full((gh, gw), 4000, np.float32)
    for k in range(0, len(bx), 1500):
        sdf = np.minimum(sdf, np.hypot(gxs[..., None] - bx[k:k + 1500], gys[..., None] - by[k:k + 1500]).min(-1))
    inside = valid[np.clip(gys.astype(int), 0, valid.shape[0] - 1), np.clip(gxs.astype(int), 0, valid.shape[1] - 1)]
    sdf = np.where(inside, sdf, -sdf)
    return np.stack([gx, gy, sdf], -1).astype(np.float16).astype(np.float32)


def sample(grid, x, y, window, step=32, log_edge_x=None):
    """-> (source x, source y, signed distance) at layer points (x, y): the python twin of the shader lookup."""
    from aewarp import bilinear
    g = bilinear(grid, (x - window[0]) / step + 0.5, (y - window[1]) / step + 0.5)
    sx = log_edge_x - np.exp(g[..., 0]) if log_edge_x is not None else x + g[..., 0]
    return sx, y + g[..., 1], g[..., 2]


def save_bin(grid, path):
    """Half-float little-endian, rows top to bottom (the shader uploads it as RGB16F). Some hosts (claude.ai pages)
    don't serve .bin: write save_json() too and point the module's arcSrc at the .json."""
    open(path, 'wb').write(grid.astype('<f2').tobytes())


def save_json(grid, path):
    import base64, json
    json.dump({'b64': base64.b64encode(grid.astype('<f2').tobytes()).decode()}, open(path, 'w'))
