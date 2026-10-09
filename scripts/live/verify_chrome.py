#!/usr/bin/env python3
"""Verify a live shader module the only way that counts: run the REAL module in Chrome and compare its frames with
After Effects renders. Also measures banding at hero size, optionally on the machine's real GPU.

    python verify_chrome.py spec.json            # frames vs AE: mean/worst error per run, side-by-side sheet
    python verify_chrome.py spec.json --banding  # render big, report the longest runs of one flat color

spec.json (paths relative to "root", which is served over http — ES modules don't load from file://):
{
  "root": "/path/to/project",
  "module": "shader/my-hero/my-hero-background.js",
  "class": "MyHeroBackground",
  "config": {"basePath": "shader/my-hero/"},              // merged with: grain 0, no ripple/scroll fade, paused
  "size": [960, 540],                                      // render size (match the AE reference frames)
  "gpu": false,                                            // true: Metal/real GPU (what users see); false: SwiftShader
  "runs": [
    {"name": "default", "set": {}, "frames": [{"t": 0, "ref": "renders/final/final_00000.png"}, ...]},
    {"name": "ocean", "set": {"palette": {"stops": [...]}}, "frames": [...]}
  ],
  "banding": {"size": [3024, 1964], "t": 0, "set": {"layout": {"zoom": 1}}}
}
Lessons: plain --disable-gpu reports no WebGL2 and silently shows the poster (proves nothing) — use SwiftShader or the
real GPU. Software and Metal agreed to 0.01/255 on our heroes, but stair-steps and banding only appear at hero size:
always run --banding at the size of a real full-screen hero (e.g. 3024x1964 for a Retina laptop) and LOOK at it.
"""
import base64, functools, http.server, io, json, os, re, socket, subprocess, sys, threading
import numpy as np
from PIL import Image

CHROME = os.environ.get('CHROME', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')

PAGE = r'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>html,body{margin:0}#s{position:relative;width:%Wpx;height:%Hpx}</style></head>
<body><div id="s"></div><pre id="out">pending</pre><script type="module">
import { %CLASS% } from '/%MODULE%';
const SPEC = %SPEC%;
const merge = (a, b) => { for (const k in b) a[k] = (b[k] && typeof b[k] === 'object' && !Array.isArray(b[k]) && a[k] && typeof a[k] === 'object') ? merge(a[k], b[k]) : b[k]; return a; };
const base = merge({ preserveDrawingBuffer: true, grain: { amount: 0 }, ripple: { enabled: false }, scrollFade: { enabled: false },
  motion: { autoplay: false }, performance: { maxDPR: 1, pauseOffscreen: false }, reducedMotion: { respect: false } }, SPEC.config);
const bg = new %CLASS%(document.getElementById('s'), base);
const res = { ok: false, frames: {} };
try {
  res.ok = await bg.init();
  for (const r of SPEC.runs) {
    if (r.set) bg.set(r.set);
    r.frames.forEach((f, i) => { bg.seek(f.t); bg.renderNow(); res.frames[r.name + '@' + i] = bg.canvas.toDataURL('image/png'); });
    bg.reset(); bg.set(base);
  }
} catch (e) { res.error = String(e); }
document.getElementById('out').textContent = JSON.stringify(res);
</script></body></html>'''


def serve(root):
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=root)
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def render(spec, runs, size):
    root = spec['root']
    page = (PAGE.replace('%W', str(size[0])).replace('%H', str(size[1])).replace('%CLASS%', spec['class'])
            .replace('%MODULE%', spec['module']).replace('%SPEC%', json.dumps({'config': spec.get('config', {}), 'runs': runs})))
    name = '.s2s_verify.html'
    open(os.path.join(root, name), 'w').write(page)
    srv, port = serve(root)
    try:
        gpu = ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'] if spec.get('gpu') else ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']
        out = subprocess.run([CHROME, '--headless=new', *gpu, f'--window-size={size[0]},{size[1] + 200}', '--virtual-time-budget=120000',
                              '--dump-dom', f'http://127.0.0.1:{port}/{name}'], capture_output=True, text=True, timeout=900)
    finally:
        srv.shutdown(); os.remove(os.path.join(root, name))
    m = re.search(r'<pre id="out">(.*?)</pre>', out.stdout, re.S)
    if not m:
        sys.exit('no output from Chrome:\n' + out.stderr[-1500:])
    res = json.loads(m.group(1).replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>'))
    if not res.get('ok'):
        sys.exit(f'module failed in Chrome: {res.get("error", "init returned false (no WebGL2?)")}')
    return {k: np.asarray(Image.open(io.BytesIO(base64.b64decode(v.split(',', 1)[1]))).convert('RGB'), np.float32) / 255
            for k, v in res['frames'].items()}


def compare(spec):
    frames = render(spec, spec['runs'], spec.get('size', [960, 540]))
    tiles, summary = [], {}
    for r in spec['runs']:
        errs = []
        for i, f in enumerate(r['frames']):
            shader = frames[f"{r['name']}@{i}"]
            ref = np.asarray(Image.open(os.path.join(spec['root'], f['ref'])).convert('RGB'), np.float32) / 255
            errs.append(float(np.abs(shader - ref).mean() * 255))
            if f is r['frames'][len(r['frames']) // 2]:
                tiles.append(np.asarray(Image.fromarray((np.concatenate([ref, shader], 0) * 255).astype(np.uint8)).resize((480, 540))))
        summary[r['name']] = {'mae255': round(np.mean(errs), 3), 'worst255': round(max(errs), 3)}
        print(f"{r['name']:12s} real shader vs AE: {np.mean(errs):.2f}/255 ({np.mean(errs) / 2.55:.2f}%), worst {max(errs):.2f}/255")
    out = os.path.join(spec['root'], spec.get('compare', 'shader_vs_ae.png'))
    Image.fromarray(np.concatenate(tiles, 1)).save(out)
    print(f'wrote {out} (top = After Effects, bottom = the real shader)')
    return summary


def banding(spec):
    b = spec.get('banding', {}); size = b.get('size', [3024, 1964])
    img = render(spec, [{'name': 'band', 'set': b.get('set', {}), 'frames': [{'t': b.get('t', 0)}]}], size)['band@0']
    a = (img * 255).astype(int)
    longest = []
    for y in range(size[1] // 10, size[1] * 9 // 10, max(1, size[1] // 15)):
        row = a[y, size[0] // 20: -size[0] // 20]
        ch = np.flatnonzero(np.any(np.diff(row, axis=0) != 0, axis=1))
        longest.append(int(np.diff(np.concatenate([[0], ch + 1, [len(row)]])).max()))
    out = os.path.join(spec['root'], spec.get('banding_png', 'banding_check.png'))
    Image.fromarray(a.astype(np.uint8)).save(out)
    print(f'longest run of one flat color per row at {size[0]}x{size[1]}: median {int(np.median(longest))} px, max {max(longest)} px'
          f'  (we measured 21/33 px when banding was visible, 6/8 px after the fix)')
    print(f'wrote {out}: open it and look along sharp lines for stair-steps (flat-run length does not catch those)')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    spec = json.load(open(sys.argv[1]))
    banding(spec) if '--banding' in sys.argv else compare(spec)
