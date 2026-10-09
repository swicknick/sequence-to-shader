#!/usr/bin/env python3
"""
Build the complete developer handoff package from a decomposed model:
drop-in JS module, production integration example, a fully self-contained
double-click preview, poster frame, README with real measured numbers, and
the textures folder. This is the last step of the pipeline and the one that
actually goes to a dev team — get it from here, not from build_shader.py's
single-file demo (that one's for quick iteration during steps 2-4, before
you've picked a final rank/resolution).

    python3 build_handoff.py <frames_dir> <model_dir> --out <dir> --name "Aurora"

Writes:
    <out>/<slug>-background.js   drop-in ES module
    <out>/index.html             production integration example
    <out>/preview.html           self-contained, open-by-double-click preview
    <out>/poster.webp            static fallback / reduced-motion still
    <out>/README.md              real measured numbers, not placeholders
    <out>/textures/              model.json + mean.webp + b0..bN.webp
"""
import argparse, json, os, re, shutil, sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_frames, render_preview_html
from handoff_templates import MODULE_JS, INDEX_HTML, README_MD


def slugify(name):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return s or "sequence"


def classify(name):
    words = [w for w in re.split(r"[^a-zA-Z0-9]+", name.strip()) if w]
    cls = "".join(w[:1].upper() + w[1:] for w in words) or "Sequence"
    if not cls.endswith("Background"):
        cls += "Background"
    return cls


def load_tex(model, name, lo, hi, W, H):
    """Same upsampling approach as verify.py: decode the shipped webp back
    to its true float range, then bicubic-resize to the source resolution
    so error/poster are measured against what actually ships, not an
    idealized float version of the basis."""
    a = np.asarray(Image.open(os.path.join(model, name + ".webp"))
                    .convert("RGB")).astype(np.float32) / 255.0
    a = a * (hi - lo) + lo
    n = (a - a.min()) / max(a.max() - a.min(), 1e-9)
    up = np.asarray(Image.fromarray((n * 255).astype(np.uint8))
                     .resize((W, H), Image.BICUBIC))
    return up.astype(np.float32) / 255.0 * (a.max() - a.min()) + a.min()


def measure(frames_dir, model_dir, meta):
    A = load_frames(frames_dir, verbose=False)
    T, H, W, _ = A.shape
    K = meta["K"]
    coef = np.array(meta["coef"], dtype=np.float32)

    md = load_tex(model_dir, "mean", meta["mean"]["lo"], meta["mean"]["hi"], W, H)
    Bd = np.stack([
        load_tex(model_dir, f"b{i}", meta["layers"][i]["lo"],
                 meta["layers"][i]["hi"], W, H) for i in range(K)])

    errs = []
    sq_err = 0.0
    last_recon = None
    Amean = A.mean(axis=0)
    for t in range(T):
        r = np.clip(md + np.tensordot(coef[t, :K], Bd, axes=(0, 0)), 0, 1)
        errs.append(float(np.abs(r - A[t]).mean()))
        sq_err += float(np.sum((r - A[t]) ** 2))
        last_recon = r
    tot_var = float(np.sum((A - Amean) ** 2))
    var_pct = 100.0 * (1.0 - sq_err / max(tot_var, 1e-9))

    mae = float(np.mean(errs)) * 255
    worst = float(np.max(errs)) * 255
    worst_frame = int(np.argmax(errs))
    return {
        "mae255": mae, "mae_pct": mae / 2.55,
        "worst255": worst, "worst_pct": worst / 2.55, "worst_frame": worst_frame,
        "var_pct": var_pct,
        "poster": (last_recon * 255).astype(np.uint8),
    }


def build_preview(class_name, js_source, model_dir, meta, names, name, jsfile):
    return render_preview_html(class_name, js_source, model_dir, meta, names, name, jsfile)


def human_mb(nbytes):
    mb = nbytes / (1024 * 1024)
    return f"{mb:.0f} MB" if mb >= 1 else f"{nbytes/1024:.0f} KB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frames")
    ap.add_argument("model")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True,
                     help='Asset name, e.g. "Aurora" -> AuroraBackground, '
                          'aurora-background.js')
    a = ap.parse_args()

    meta = json.load(open(os.path.join(a.model, "model.json")))
    K = meta["K"]
    names = ["mean"] + [f"b{i}" for i in range(K)]

    slug = slugify(a.name)
    cls = classify(a.name)
    jsfile = f"{slug}-background.js"

    os.makedirs(a.out, exist_ok=True)
    tex_out = os.path.join(a.out, "textures")
    os.makedirs(tex_out, exist_ok=True)
    for n in names:
        shutil.copy(os.path.join(a.model, n + ".webp"), os.path.join(tex_out, n + ".webp"))
    shutil.copy(os.path.join(a.model, "model.json"), os.path.join(tex_out, "model.json"))

    print("measuring against source frames (same math as verify.py)...")
    m = measure(a.frames, a.model, meta)

    Image.fromarray(m["poster"]).save(os.path.join(a.out, "poster.webp"), "WEBP", quality=90)

    js_source = (MODULE_JS
                 .replace("%CLASS%", cls)
                 .replace("%K%", str(K))
                 .replace("%KPLUS1%", str(K + 1))
                 .replace("%JSFILE%", jsfile)
                 .replace("%TEXKB%", f"{sum(os.path.getsize(os.path.join(tex_out, n + '.webp')) for n in names)/1024:.0f}"))
    open(os.path.join(a.out, jsfile), "w").write(js_source)

    index_html = (INDEX_HTML
                  .replace("%CLASS%", cls)
                  .replace("%JSFILE%", jsfile)
                  .replace("%NAME%", a.name))
    open(os.path.join(a.out, "index.html"), "w").write(index_html)

    preview_html = build_preview(cls, js_source, tex_out, meta, names, a.name, jsfile)
    open(os.path.join(a.out, "preview.html"), "w").write(preview_html)

    frames_count = meta.get("frames", len(meta["coef"]))
    fps = meta.get("fps", 25)
    src_bytes = sum(
        os.path.getsize(os.path.join(a.frames, f))
        for f in os.listdir(a.frames)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
    total_bytes = sum(
        os.path.getsize(os.path.join(root, f))
        for root, _, files in os.walk(a.out) for f in files
        if not f == "preview.html")  # preview duplicates textures inline; don't double-count

    g = meta.get("grain", {"base": 0, "slope": 0, "chanCorr": 0.4})
    grain_note = (f"stronger in shadows, ~{g['chanCorr']:.2f} channel correlation"
                  if g.get("slope", 0) < 0 else
                  f"~{g.get('chanCorr', 0.4):.2f} channel correlation")

    readme = (README_MD
              .replace("%NAME%", a.name)
              .replace("%TOTALKB%", f"{total_bytes/1024:.0f}")
              .replace("%KPLUS1%", str(K + 1))
              .replace("%SRCMB%", human_mb(src_bytes))
              .replace("%MAEPCT%", f"{m['mae_pct']:.2f}")
              .replace("%JSFILE%", jsfile)
              .replace("%KLAST%", str(K - 1))
              .replace("%CLASS%", cls)
              .replace("%NFRAMESMAX%", str(frames_count - 1))
              .replace("%FPS%", f"{fps:g}")
              .replace("%NFRAMES%", str(frames_count))
              .replace("%DURATION%", f"{frames_count / fps:.1f}")
              .replace("%GRAINNOTE%", grain_note)
              .replace("%K%", str(K))
              .replace("%VARPCT%", f"{m['var_pct']:.1f}")
              .replace("%TEXW%", str(meta["texW"]))
              .replace("%TEXH%", str(meta["texH"]))
              .replace("%MAE255%", f"{m['mae255']:.3f}")
              .replace("%WORSTPCT%", f"{m['worst_pct']:.2f}")
              .replace("%WORSTFRAME%", str(m["worst_frame"])))
    open(os.path.join(a.out, "README.md"), "w").write(readme)

    print(f"\nwrote handoff package to {a.out}/")
    print(f"  {jsfile}, index.html, preview.html, poster.webp, README.md, textures/")
    print(f"  MAE {m['mae255']:.3f}/255 ({m['mae_pct']:.2f}%), "
          f"worst frame {m['worst_pct']:.2f}% (frame {m['worst_frame']})")
    print(f"  total payload: {total_bytes/1024:.0f} KB vs {human_mb(src_bytes)} source")
    if m["mae_pct"] > 2.0:
        print("  -> over 2%: see references/troubleshooting.md before shipping this.")


if __name__ == "__main__":
    main()
