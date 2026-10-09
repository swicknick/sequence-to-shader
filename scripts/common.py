"""Shared helpers for the sequence-to-shader scripts."""
import base64
import glob
import json
import os
import re

import numpy as np
from PIL import Image

EXTS = (".png", ".jpg", ".jpeg", ".webp")


def upgrade_module_offline_support(js_source):
    """Patch an older sequence-to-shader module (from before opts.data/
    opts.images existed) to support them, in place. Needed for two
    independent reasons: (1) a module without this can't be inlined into a
    self-contained preview.html — it unconditionally fetch()es model.json,
    which is CORS-blocked under file:// in Chrome; (2) it's a genuine API
    gap in the shipped module itself, worth fixing regardless of preview.html.

    Returns (patched_source, changed). changed=False (source returned
    unmodified) if the expected anchor text isn't found — e.g. it's already
    patched, or structured differently than this skill's own template —
    rather than guessing at a different edit and risking corrupting it.
    """
    changed = False

    old_fetch = (
        "    const res = await fetch(this.o.basePath + 'model.json');\n"
        "    if (!res.ok) { this._fallback(); return false; }\n"
        "    this.meta = await res.json();\n"
        "    const K = this.K = this.meta.K;"
    )
    new_fetch = (
        "    // opts.data lets a caller supply an already-parsed model.json (e.g. a\n"
        "    // fully inlined preview page with no network/file fetch at all — plain\n"
        "    // fetch() of local files is CORS-blocked under file:// in Chrome, even\n"
        "    // for same-directory files). Falls back to fetching basePath/model.json\n"
        "    // for normal served-over-http production use.\n"
        "    if (this.o.data) {\n"
        "      this.meta = this.o.data;\n"
        "    } else {\n"
        "      const res = await fetch(this.o.basePath + 'model.json');\n"
        "      if (!res.ok) { this._fallback(); return false; }\n"
        "      this.meta = await res.json();\n"
        "    }\n"
        "    const K = this.K = this.meta.K;"
    )
    if old_fetch in js_source:
        js_source = js_source.replace(old_fetch, new_fetch)
        changed = True

    old_tex = (
        "    // Textures\n"
        "    const names = ['mean', ...Array.from({length: K}, (_, i) => 'b' + i)];\n"
        "    let imgs;\n"
        "    try {\n"
        "      imgs = await Promise.all(names.map(n => this._img(this.o.basePath + n + '.webp')));\n"
        "    } catch (e) { console.error(e); this._fallback(); return false; }"
    )
    new_tex = (
        "    // Textures. opts.images (name -> data: URI or blob: URL) skips the file\n"
        "    // fetch entirely, same reasoning as opts.data above — Image().src on a\n"
        "    // data: URI never touches the network/file layer, so it works under\n"
        "    // file:// with no CORS restriction regardless.\n"
        "    const names = ['mean', ...Array.from({length: K}, (_, i) => 'b' + i)];\n"
        "    let imgs;\n"
        "    try {\n"
        "      imgs = await Promise.all(names.map(n =>\n"
        "        this._img(this.o.images ? this.o.images[n] : this.o.basePath + n + '.webp')));\n"
        "    } catch (e) { console.error(e); this._fallback(); return false; }"
    )
    if old_tex in js_source:
        js_source = js_source.replace(old_tex, new_tex)
        changed = True

    old_setters = (
        "  /** Progress through the animation, 0..1. Useful for scroll-driven playback. */\n"
        "  setProgress(p) { this.seek(Math.max(0, Math.min(1, p)) * (this.nFrames - 1)); }\n"
        "\n"
        "  destroy() {"
    )
    new_setters = (
        "  /** Progress through the animation, 0..1. Useful for scroll-driven playback. */\n"
        "  setProgress(p) { this.seek(Math.max(0, Math.min(1, p)) * (this.nFrames - 1)); }\n"
        "\n"
        "  /** Grain amount, 0..1 (0 disables). Takes effect on the next drawn frame. */\n"
        "  setGrain(amount) { this.o.grain = amount; }\n"
        "\n"
        "  /** Whether playback restarts after the last frame. */\n"
        "  setLoop(loop) { this.o.loop = loop; }\n"
        "\n"
        "  destroy() {"
    )
    if old_setters in js_source:
        js_source = js_source.replace(old_setters, new_setters)
        changed = True

    return js_source, changed


def render_preview_html(class_name, js_source, tex_dir, meta, names, name, jsfile):
    """Build a fully self-contained preview.html: the module inlined as a
    plain script (no import) plus model.json and every texture pre-encoded
    as base64 data: URIs (no fetch()). Both are necessary, not just one —
    ES module import of an external file AND fetch() of a local file are
    independently CORS-blocked under file:// in Chrome, so a page meant to
    survive a plain double-click can use neither.

    Shared by build_handoff.py (building a package from scratch) and
    add_preview.py (backfilling one that doesn't have a preview yet) so
    there's exactly one implementation of this, not two that can drift.
    """
    from handoff_templates import PREVIEW_HTML  # local import: avoids a cycle,
    # handoff_templates.py has no reason to import common.py back.

    inline_js = js_source.replace(f"export class {class_name}", f"class {class_name}")
    inline_js = re.sub(rf"\nexport default {class_name};\s*$", "\n", inline_js)

    images = {}
    for n in names:
        raw = open(os.path.join(tex_dir, n + ".webp"), "rb").read()
        images[n] = "data:image/webp;base64," + base64.b64encode(raw).decode()
    payload = json.dumps({"meta": meta, "images": images}, separators=(",", ":"))

    return (PREVIEW_HTML
            .replace("%PAYLOAD%", payload)
            .replace("%JS%", inline_js)
            .replace("%CLASS%", class_name)
            .replace("%JSFILE%", jsfile)
            .replace("%NAME%", name)
            .replace("%SRCW%", str(meta.get("srcW", 960)))
            .replace("%SRCH%", str(meta.get("srcH", 540))))


def load_frames(d, verbose=True):
    files = sorted(f for f in glob.glob(os.path.join(d, "*"))
                   if f.lower().endswith(EXTS))
    if not files:
        raise SystemExit(f"No image files found in {d}")
    a = np.stack([np.asarray(Image.open(f).convert("RGB"), dtype=np.float32) / 255.0
                  for f in files])
    if verbose:
        print(f"loaded {a.shape[0]} frames at {a.shape[2]}x{a.shape[1]}")
    return a


def _smooth(g, r=1):
    """Tiny box blur. Radius 1 kills pixel-level grain while preserving
    few-pixel features like a horizon line — which is exactly the thing the
    detail metric needs to stay sensitive to."""
    k = 2 * r + 1
    pad = np.pad(g, r, mode="edge")
    c = np.cumsum(pad, axis=0)
    g = (c[k - 1:, :] - np.vstack([np.zeros((1, pad.shape[1])), c[:-k, :]])) / k
    c = np.cumsum(g, axis=1)
    g = (c[:, k - 1:] - np.hstack([np.zeros((g.shape[0], 1)), c[:, :-k]])) / k
    return g


def struct_detail(img, axis=0):
    """Mean absolute gradient of the smoothed luminance, along one axis.

    axis=0 -> vertical detail (sensitive to horizontal edges/streaks)
    axis=1 -> horizontal detail
    """
    return float(np.abs(np.diff(_smooth(img.mean(axis=2)), axis=axis)).mean())


def detail_ratio(recon, orig, axis=0):
    so = struct_detail(orig, axis)
    if so <= 1e-6:
        return np.nan, 0.0
    return struct_detail(recon, axis) / so, so


def weighted_ratio(pairs):
    """Combine (ratio, weight) pairs, ignoring frames with no structure."""
    r = np.array([p[0] for p in pairs], dtype=float)
    w = np.array([p[1] for p in pairs], dtype=float)
    ok = np.isfinite(r) & (w > 0)
    if not ok.any():
        return float("nan")
    return float(np.average(r[ok], weights=w[ok]))


def interpret_detail(dv):
    """Guidance text for a detail ratio. Absolute values are content-dependent —
    the number is most useful COMPARED ACROSS RESOLUTIONS for the same clip."""
    if not np.isfinite(dv):
        return "no measurable structure in the source"
    if dv < 0.50:
        return ("visibly soft — raise texture resolution on the axis holding "
                "the sharp features (see --probe-resolutions)")
    if dv < 0.62:
        return ("acceptable, but compare against --probe-resolutions; a higher "
                "value on the sharp axis may be cheap")
    return "good for smooth gradient content"
