#!/usr/bin/env python3
"""
Add preview.html to an already-built handoff package that doesn't have one
yet — e.g. one built before this skill had preview.html, or supplied/copied
in from elsewhere. Introspects the existing module and textures/model.json
rather than requiring the original frames_dir or a full rebuild.

    python3 add_preview.py <handoff_dir> [--name "Display Name"]

Finds the *-background.js file automatically (errors if there's more than
one or none — point it at one package at a time). --name defaults to the
title already in index.html if there is one, else the class name with
"Background" stripped.
"""
import argparse, glob, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import render_preview_html, upgrade_module_offline_support


def find_module(handoff_dir):
    candidates = glob.glob(os.path.join(handoff_dir, "*-background.js"))
    if len(candidates) != 1:
        raise SystemExit(
            f"expected exactly one *-background.js in {handoff_dir}, found: {candidates}")
    return candidates[0]


def find_class_name(js_source):
    m = re.search(r"export class (\w+)", js_source)
    if not m:
        raise SystemExit("couldn't find 'export class X' in the module — "
                          "is this actually a sequence-to-shader module?")
    return m.group(1)


def guess_name(handoff_dir, class_name):
    idx_path = os.path.join(handoff_dir, "index.html")
    if os.path.exists(idx_path):
        m = re.search(r"<title>(.*?)\s+background", open(idx_path).read())
        if m:
            return m.group(1)
    return re.sub(r"Background$", "", class_name) or class_name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("handoff_dir")
    ap.add_argument("--name", default=None)
    a = ap.parse_args()

    js_path = find_module(a.handoff_dir)
    jsfile = os.path.basename(js_path)
    js_source = open(js_path).read()
    class_name = find_class_name(js_source)
    name = a.name or guess_name(a.handoff_dir, class_name)

    if "this.o.data" not in js_source:
        patched, changed = upgrade_module_offline_support(js_source)
        if changed:
            open(js_path, "w").write(patched)
            js_source = patched
            print(f"  upgraded {jsfile} in place: added opts.data/opts.images "
                  f"offline support + setGrain/setLoop (it predated these)")
        else:
            print(f"  WARNING: {jsfile} doesn't support opts.data/opts.images and "
                  f"doesn't match the expected pattern to patch it automatically — "
                  f"preview.html will be generated but will likely fail under "
                  f"file:// (fetch() is CORS-blocked there). Inspect {jsfile} by hand.")

    tex_dir = os.path.join(a.handoff_dir, "textures")
    model_json = os.path.join(tex_dir, "model.json")
    if not os.path.exists(model_json):
        raise SystemExit(f"no textures/model.json in {a.handoff_dir} — "
                          "is this a complete handoff package?")
    meta = json.load(open(model_json))
    K = meta["K"]
    names = ["mean"] + [f"b{i}" for i in range(K)]
    missing = [n for n in names if not os.path.exists(os.path.join(tex_dir, n + ".webp"))]
    if missing:
        raise SystemExit(f"model.json says K={K} but missing texture(s): {missing}")

    html = render_preview_html(class_name, js_source, tex_dir, meta, names, name, jsfile)

    out = os.path.join(a.handoff_dir, "preview.html")
    open(out, "w").write(html)
    print(f"wrote {out} ({len(html)/1024:.1f} KB)")
    print(f"  class={class_name}  module={jsfile}  name={name!r}  K={K}")


if __name__ == "__main__":
    main()
