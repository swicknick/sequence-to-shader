"""Templates for build_handoff.py. Kept separate from the generator logic
because these are large verbatim JS/HTML blocks, not code that branches.

Every JS/HTML template uses %TOKEN% placeholders (not str.format or f-strings)
because the templates are full of literal { } from JS/GLSL — braces would
collide with .format(). Substitute with .replace().
"""

# The drop-in ES module. %CLASS% is the only thing that varies per asset.
# Keep this byte-for-byte the logic verified in the sequence-to-shader skill:
# color = mean + sum(coefficient_i * basis_i), plus procedural grain fitted
# from the source residual. See references/integration.md for usage.
MODULE_JS = r'''/*
 * %CLASS% — WebGL2 animated background.
 *
 * Reconstructs a rendered frame sequence from %K% basis textures plus
 * per-frame coefficients:  color = mean + Σ coefficient_i(t) · basis_i
 *
 * No dependencies. ~10 KB of JS + ~%TEXKB% KB of textures.
 *
 *   import { %CLASS% } from './%JSFILE%';
 *   const bg = new %CLASS%(el, { basePath: './textures/' });
 *   await bg.init();
 *
 * See README.md for integration notes.
 */

const VERT = `#version 300 es
in vec2 p; out vec2 uv;
void main(){ uv = p*0.5+0.5; uv.y = 1.0-uv.y; gl_Position = vec4(p,0.,1.); }`;

function fragSource(K) {
  let acc = '';
  for (let i = 0; i < K; i++) {
    acc += `  c += uC[${i}] * dec(texture(uB[${i}], uv).rgb, uBR[${i}]);\n`;
  }
  return `#version 300 es
precision highp float;
in vec2 uv; out vec4 o;
uniform sampler2D uMean;
uniform sampler2D uB[${K}];
uniform vec2 uMeanR;
uniform vec2 uBR[${K}];
uniform float uC[${K}];
uniform float uGrain, uTime, uGBase, uGSlope, uGCorr;

vec3 dec(vec3 t, vec2 r){ return t*(r.y-r.x)+r.x; }
float hash(vec2 q){ return fract(sin(dot(q, vec2(127.1,311.7))) * 43758.5453); }

void main(){
  vec3 c = dec(texture(uMean, uv).rgb, uMeanR);
${acc}  c = clamp(c, 0.0, 1.0);

  if (uGrain > 0.0) {
    // Amplitude fitted from the source residual: stronger in shadow,
    // weaker in highlight. Channels partially correlated (film, not digital).
    float luma = dot(c, vec3(0.2126, 0.7152, 0.0722));
    float amp  = max(uGBase + uGSlope * luma, 0.0) * uGrain;
    vec2  sd   = gl_FragCoord.xy + vec2(uTime*61.0, uTime*37.0);
    float gC   = hash(sd) - 0.5;
    vec3  gI   = vec3(hash(sd+11.3), hash(sd+27.7), hash(sd+43.1)) - 0.5;
    c += amp * (sqrt(uGCorr)*gC + sqrt(1.0-uGCorr)*gI);
  }
  o = vec4(clamp(c, 0.0, 1.0), 1.0);
}`;
}

export class %CLASS% {
  /**
   * @param {HTMLElement} container  positioned element to fill
   * @param {object} opts
   *   basePath   {string}  where textures/ live            default './textures/'
   *   loop       {boolean} restart after the last frame    default true
   *   autoplay   {boolean} start on init                   default true
   *   playOnce   {boolean} play once when scrolled in,
   *                        then hold the final frame       default false
   *   grain      {number}  0 disables, 1 = fitted amount   default 1
   *   maxDPR     {number}  device pixel ratio cap          default 2
   *   speed      {number}  playback rate multiplier        default 1
   *   data       {object}  pre-parsed model.json — skips fetching
   *                        basePath + 'model.json'          default none
   *   images     {object}  name -> data:/blob: URI ('mean', 'b0'...) —
   *                        skips fetching basePath + name + '.webp'.
   *                        For a fully offline/inlined page.  default none
   */
  constructor(container, opts = {}) {
    this.el = container;
    this.o = Object.assign({
      basePath: './textures/', loop: true, autoplay: true, playOnce: false,
      grain: 1, maxDPR: 2, speed: 1,
    }, opts);

    this.canvas = document.createElement('canvas');
    Object.assign(this.canvas.style, {
      position: 'absolute', inset: '0', width: '100%', height: '100%',
      display: 'block',
    });

    this.frame = 0;
    this.playing = false;
    this.visible = true;
    this.ready = false;
    this._raf = null;
    this._played = false;

    this.reducedMotion =
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  async init() {
    const gl = this.canvas.getContext('webgl2', { antialias: false, alpha: false });
    if (!gl) { this._fallback(); return false; }
    this.gl = gl;

    // opts.data lets a caller supply an already-parsed model.json (e.g. a
    // fully inlined preview page with no network/file fetch at all — plain
    // fetch() of local files is CORS-blocked under file:// in Chrome, even
    // for same-directory files). Falls back to fetching basePath/model.json
    // for normal served-over-http production use.
    if (this.o.data) {
      this.meta = this.o.data;
    } else {
      const res = await fetch(this.o.basePath + 'model.json');
      if (!res.ok) { this._fallback(); return false; }
      this.meta = await res.json();
    }
    const K = this.K = this.meta.K;
    this.coef = this.meta.coef;
    this.nFrames = this.coef.length;
    this.fps = this.meta.fps || 25;

    // Build program
    const vs = this._shader(gl.VERTEX_SHADER, VERT);
    const fs = this._shader(gl.FRAGMENT_SHADER, fragSource(K));
    const prog = gl.createProgram();
    gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.error(gl.getProgramInfoLog(prog)); this._fallback(); return false;
    }
    gl.useProgram(prog);
    this.prog = prog;

    // Fullscreen triangle
    const vb = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vb);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]),
                  gl.STATIC_DRAW);
    const pl = gl.getAttribLocation(prog, 'p');
    gl.enableVertexAttribArray(pl);
    gl.vertexAttribPointer(pl, 2, gl.FLOAT, false, 0, 0);

    // Textures. opts.images (name -> data: URI or blob: URL) skips the file
    // fetch entirely, same reasoning as opts.data above — Image().src on a
    // data: URI never touches the network/file layer, so it works under
    // file:// with no CORS restriction regardless.
    const names = ['mean', ...Array.from({length: K}, (_, i) => 'b' + i)];
    let imgs;
    try {
      imgs = await Promise.all(names.map(n =>
        this._img(this.o.images ? this.o.images[n] : this.o.basePath + n + '.webp')));
    } catch (e) { console.error(e); this._fallback(); return false; }
    imgs.forEach((im, i) => this._tex(im, i));

    gl.uniform1i(gl.getUniformLocation(prog, 'uMean'), 0);
    gl.uniform2f(gl.getUniformLocation(prog, 'uMeanR'),
                 this.meta.mean.lo, this.meta.mean.hi);
    for (let i = 0; i < K; i++) {
      gl.uniform1i(gl.getUniformLocation(prog, `uB[${i}]`), i + 1);
      gl.uniform2f(gl.getUniformLocation(prog, `uBR[${i}]`),
                   this.meta.layers[i].lo, this.meta.layers[i].hi);
    }
    const g = this.meta.grain || { base: 0, slope: 0, chanCorr: 0.4 };
    gl.uniform1f(gl.getUniformLocation(prog, 'uGBase'), g.base);
    gl.uniform1f(gl.getUniformLocation(prog, 'uGSlope'), g.slope);
    gl.uniform1f(gl.getUniformLocation(prog, 'uGCorr'), g.chanCorr);

    this.uC = gl.getUniformLocation(prog, 'uC');
    this.uGrain = gl.getUniformLocation(prog, 'uGrain');
    this.uTime = gl.getUniformLocation(prog, 'uTime');

    this.el.appendChild(this.canvas);
    this.ready = true;

    // Pause the render loop while off-screen.
    if ('IntersectionObserver' in window) {
      this._io = new IntersectionObserver(([e]) => {
        this.visible = e.isIntersecting;
        if (this.visible && this.o.playOnce && !this._played && !this.reducedMotion) {
          this._played = true; this.frame = 0; this.playing = true;
        }
      }, { threshold: 0.01 });
      this._io.observe(this.el);
    }

    if (this.reducedMotion) {
      this.frame = this.nFrames - 1;   // hold the final, fully-bloomed state
      this.playing = false;
      this._renderOnce();
    } else if (this.o.autoplay && !this.o.playOnce) {
      this.play();
    } else {
      this._renderOnce();
    }

    this._last = performance.now();
    this._raf = requestAnimationFrame(this._tick);
    return true;
  }

  play()  { if (this.ready) { this.playing = true;  this._last = performance.now(); } }
  pause() { this.playing = false; }

  /** Jump to a frame (0 .. nFrames-1). Pauses playback. */
  seek(f) {
    this.frame = Math.max(0, Math.min(this.nFrames - 1, f));
    this.playing = false;
    this._renderOnce();
  }

  /** Progress through the animation, 0..1. Useful for scroll-driven playback. */
  setProgress(p) { this.seek(Math.max(0, Math.min(1, p)) * (this.nFrames - 1)); }

  /** Grain amount, 0..1 (0 disables). Takes effect on the next drawn frame. */
  setGrain(amount) { this.o.grain = amount; }

  /** Whether playback restarts after the last frame. */
  setLoop(loop) { this.o.loop = loop; }

  destroy() {
    if (this._raf) cancelAnimationFrame(this._raf);
    if (this._io) this._io.disconnect();
    const lose = this.gl && this.gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
    if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
    this.ready = false;
  }

  // ---- internals ----

  _shader(type, src) {
    const gl = this.gl, s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(s));
    }
    return s;
  }

  _img(src) {
    return new Promise((res, rej) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => rej(new Error('failed to load ' + src));
      im.src = src;
    });
  }

  _tex(img, unit) {
    const gl = this.gl, t = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB8, gl.RGB, gl.UNSIGNED_BYTE, img);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }

  _fallback() {
    // No WebGL2 (or assets failed): show the poster still.
    this.el.style.backgroundImage =
      `url("${this.o.basePath.replace(/textures\/$/, '')}poster.webp")`;
    this.el.style.backgroundSize = 'cover';
    this.el.style.backgroundPosition = 'center';
  }

  _coefAt(f) {
    const a = Math.max(0, Math.min(this.nFrames - 1, Math.floor(f)));
    const b = Math.min(this.nFrames - 1, a + 1);
    const m = f - a, out = new Float32Array(this.K);
    for (let i = 0; i < this.K; i++) {
      out[i] = this.coef[a][i] * (1 - m) + this.coef[b][i] * m;
    }
    return out;
  }

  _size() {
    const d = Math.min(window.devicePixelRatio || 1, this.o.maxDPR);
    const w = Math.round(this.canvas.clientWidth * d);
    const h = Math.round(this.canvas.clientHeight * d);
    if (w > 0 && h > 0 && (this.canvas.width !== w || this.canvas.height !== h)) {
      this.canvas.width = w; this.canvas.height = h;
      this.gl.viewport(0, 0, w, h);
    }
  }

  _draw(now) {
    const gl = this.gl;
    this._size();
    gl.uniform1fv(this.uC, this._coefAt(this.frame));
    gl.uniform1f(this.uGrain, this.o.grain);
    gl.uniform1f(this.uTime, (now || 0) * 0.001);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  _renderOnce() { if (this.ready) this._draw(performance.now()); }

  _tick = (now) => {
    this._raf = requestAnimationFrame(this._tick);
    if (!this.ready) return;
    const dt = (now - this._last) / 1000;
    this._last = now;
    if (!this.visible) return;            // off-screen: skip GPU work entirely

    if (this.playing) {
      this.frame += dt * this.fps * this.o.speed;
      if (this.frame >= this.nFrames - 1) {
        if (this.o.loop && !this.o.playOnce) {
          this.frame = 0;
        } else {
          this.frame = this.nFrames - 1;
          this.playing = false;
        }
      }
    } else if (this.o.grain <= 0) {
      return;                             // static and grainless: nothing changes
    }
    this._draw(now);
  };
}

export default %CLASS%;
'''

INDEX_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%NAME% background — integration example</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, -apple-system, sans-serif; }

  /* The container the background fills.
     position: relative  — the canvas is absolutely positioned inside it.
     isolation: isolate  — contains any blend modes to this subtree.
     poster.webp as the CSS background covers the paint before the WebGL
     textures decode (fast, but not instant) and doubles as the no-WebGL2
     fallback the JS falls back to — same image, same purpose either way. */
  .hero {
    position: relative;
    isolation: isolate;
    min-height: 100svh;
    display: grid;
    place-items: center;
    background: #111 url('./poster.webp') center/cover;
    overflow: hidden;
  }

  /* Content sits above the canvas. */
  .hero__content {
    position: relative;
    z-index: 1;
    text-align: center;
    padding: 2rem;
    color: #fff;
  }
  .hero__content h1 {
    font-size: clamp(2rem, 7vw, 4.5rem);
    margin: 0 0 .5rem;
    letter-spacing: -.02em;
  }
  .hero__content p { margin: 0; opacity: .85; }

  /* Brightness/contrast typically shifts a lot over the animation. If light
     text sits low in the frame, add a scrim rather than hoping contrast
     holds at every frame. */
  .hero::after {
    content: "";
    position: absolute;
    inset: 0;
    z-index: 0;
    pointer-events: none;
    background: linear-gradient(to bottom, rgba(0,0,0,.35), transparent 45%);
  }

  .after { padding: 4rem 2rem; max-width: 60ch; margin: 0 auto; line-height: 1.6; }
</style>
</head>
<body>

<section class="hero" id="hero">
  <div class="hero__content">
    <h1>Your headline</h1>
    <p>Background is a WebGL reconstruction of the source sequence.</p>
  </div>
</section>

<div class="after">
  <p>Scroll back up — the render loop pauses automatically while the hero is
     off-screen, so it costs nothing down here.</p>
</div>

<script type="module">
  import { %CLASS% } from './%JSFILE%';

  const bg = new %CLASS%(document.getElementById('hero'), {
    basePath: './textures/',
    loop: true,        // set false + playOnce:true for a one-shot bloom on entry
    autoplay: true,
    grain: 1,          // 0 disables; 1 is the amount fitted from the source
  });

  bg.init().then(ok => {
    if (!ok) console.warn('WebGL2 unavailable — poster fallback shown.');
  });

  // Scroll-driven alternative (instead of autoplay):
  //
  // const bg = new %CLASS%(el, { autoplay: false, basePath: './textures/' });
  // await bg.init();
  // addEventListener('scroll', () => {
  //   const r = el.getBoundingClientRect();
  //   bg.setProgress(1 - (r.bottom / (innerHeight + r.height)));
  // }, { passive: true });
</script>

</body>
</html>
'''

# Fully self-contained preview: no import, no fetch — the module (%JS%) is
# inlined as a plain classic script, and %PAYLOAD% is model.json + every
# texture pre-encoded as base64 data: URIs. This is deliberately not the
# same code path as index.html: ES module import of an external file, and
# fetch() of model.json, are BOTH CORS-blocked under file:// in Chrome, so a
# double-clickable preview cannot use either. Verify any change to this
# template against headless Chrome with software WebGL enabled
# (--use-angle=swiftshader --enable-unsafe-swiftshader) — plain
# --disable-gpu reports WebGL2 as unavailable and silently shows the poster
# fallback instead, which looks fine on screen but proves nothing.
PREVIEW_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%NAME% — handoff preview</title>
<style>
  :root{color-scheme:light dark;--bg:#f4f4f4;--fg:#1a1a1a;--muted:#666;--line:rgba(0,0,0,.15)}
  @media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0b0b0c;--fg:#eee;--muted:#8a8a8a;--line:rgba(255,255,255,.18)}}
  html,body{margin:0;background:var(--bg);color:var(--fg);font-family:ui-monospace,"SF Mono",Menlo,monospace}
  .wrap{max-width:1000px;margin:0 auto;padding:20px 16px 40px}
  h1{font-size:14px;font-weight:600;margin:0 0 2px}
  .sub{color:var(--muted);font-size:11.5px;margin:0 0 14px;line-height:1.55}
  .stage{position:relative;width:100%;aspect-ratio:%SRCW%/%SRCH%;overflow:hidden;border-radius:6px;background:#000}
  .bar{display:flex;align-items:center;gap:10px;margin-top:10px;flex-wrap:wrap}
  button{font:inherit;font-size:11.5px;padding:5px 11px;border-radius:5px;cursor:pointer;background:transparent;color:var(--fg);border:1px solid var(--line)}
  button:hover{border-color:var(--fg)}
  input[type=range]{flex:1;min-width:160px}
  .t{font-variant-numeric:tabular-nums;color:var(--muted);font-size:11.5px;min-width:74px}
  label{font-size:11.5px;color:var(--muted);display:flex;align-items:center;gap:5px}
  .note{color:var(--muted);font-size:11px;margin-top:14px;line-height:1.6}
  code{background:var(--line);padding:1px 5px;border-radius:3px}
</style>
</head>
<body>
<div class="wrap">
  <h1>%NAME% — handoff preview</h1>
  <p class="sub" id="sub">Loading…</p>
  <div class="stage" id="stage"></div>
  <div class="bar">
    <button id="play">Pause</button>
    <input type="range" id="scrub" min="0" step="0.01" value="0">
    <span class="t" id="tlabel">frame 0</span>
    <label><input type="checkbox" id="grain" checked> grain</label>
    <label><input type="checkbox" id="loopc" checked> loop</label>
  </div>
  <p class="note">
    Fully self-contained — no network/file fetch, safe to open by
    double-click in any browser. This is <code>%JSFILE%</code> inlined
    verbatim (same class, same math) driven with this asset's actual
    textures, not a reimplementation. For the real drop-in module + separate
    cacheable textures, see <code>%JSFILE%</code> / <code>index.html</code>.
    See <code>README.md</code> for integration notes.
  </p>
</div>

<script id="payload" type="application/json">%PAYLOAD%</script>
<script>
%JS%

const PAYLOAD = JSON.parse(document.getElementById('payload').textContent);

const stage = document.getElementById('stage');
const sub = document.getElementById('sub');
const btn = document.getElementById('play');
const scrub = document.getElementById('scrub');
const tlabel = document.getElementById('tlabel');
const gchk = document.getElementById('grain');
const lchk = document.getElementById('loopc');

const bg = new %CLASS%(stage, {
  data: PAYLOAD.meta,
  images: PAYLOAD.images,
});

bg.init().then(ok => {
  if (!ok) {
    sub.textContent = 'WebGL2 unavailable in this browser.';
    return;
  }
  sub.textContent =
    `${bg.nFrames} frames . ${bg.fps}fps rebuilt from ${bg.K} basis textures (+ mean). ` +
    `Grain and loop toggle live; drag the scrub bar to seek.`;
  scrub.max = bg.nFrames - 1;

  btn.onclick = () => {
    if (bg.playing) { bg.pause(); btn.textContent = 'Play'; }
    else { bg.play(); btn.textContent = 'Pause'; }
  };
  scrub.oninput = () => {
    bg.seek(parseFloat(scrub.value));
    btn.textContent = 'Play';
  };
  gchk.onchange = () => bg.setGrain(gchk.checked ? 1 : 0);
  lchk.onchange = () => bg.setLoop(lchk.checked);

  (function syncUI() {
    requestAnimationFrame(syncUI);
    if (bg.playing) {
      scrub.value = bg.frame;
    } else if (btn.textContent === 'Pause') {
      btn.textContent = 'Play';
    }
    tlabel.textContent = 'frame ' + Math.round(bg.frame);
  })();
});
</script>
</body>
</html>
'''

README_MD = r'''# %NAME% Background — developer handoff

An animated background reproducing a rendered %NAME% sequence, as a WebGL2
canvas. No dependencies, no video, no image sequence.

**~%TOTALKB% KB total** (%KPLUS1% WebP textures + coefficients + ~10 KB of JS),
replacing a %SRCMB% source PNG sequence. Measured mean error vs the source
render: **%MAEPCT%%**.

---

## Files

- `%JSFILE%` — drop-in ES module, no dependencies
- `index.html` — production integration example, start here
- `preview.html` — self-contained, open-by-double-click preview with
  play/pause, scrub, grain and loop controls; drives the real module and
  real textures, not a mockup
- `poster.webp` — static fallback (also the reduced-motion still)
- `textures/model.json` — coefficients, decode ranges, fps, grain params
- `textures/mean.webp`, `textures/b0.webp` … `textures/b%KLAST%.webp` — basis images

Serve `textures/` (and everything except `preview.html`) as static assets.
Nothing needs a build step. `preview.html` is fully self-contained — open it
directly, no server needed — everything else needs to be served over
http(s), since `fetch()` and ES module `import` are both CORS-blocked under
`file://` in Chrome.

## Quick start

```html
<section class="hero" id="hero"></section>

<script type="module">
  import { %CLASS% } from './%JSFILE%';
  const bg = new %CLASS%(document.getElementById('hero'), {
    basePath: './textures/'
  });
  await bg.init();
</script>
```

```css
.hero { position: relative; isolation: isolate; overflow: hidden; }
```

The container **must** be positioned — the canvas is absolutely positioned
inside it and fills it.

## Options

| option | default | notes |
|---|---|---|
| `basePath` | `'./textures/'` | where the assets live |
| `loop` | `true` | restart after the last frame |
| `autoplay` | `true` | begin on init |
| `playOnce` | `false` | play once when scrolled into view, then hold final frame |
| `grain` | `1` | `0` disables; `1` is the amount fitted from the source |
| `speed` | `1` | playback rate multiplier |
| `maxDPR` | `2` | device pixel ratio cap |
| `data` | none | pre-parsed `model.json`, skips the fetch (offline use) |
| `images` | none | `{name: dataURI}` map, skips fetching textures (offline use) |

## API

```js
bg.play();  bg.pause();
bg.seek(frameIndex);        // 0 .. %NFRAMESMAX%
bg.setProgress(0..1);       // for scroll-driven playback
bg.setGrain(0..1);          // change grain amount after init
bg.setLoop(true|false);     // change loop behavior after init
bg.destroy();               // removes canvas, releases GL context
```

## What it does automatically

- **Pauses off-screen.** An IntersectionObserver stops GPU work when the
  container isn't visible.
- **Respects `prefers-reduced-motion`.** Holds the final, fully-bloomed frame
  instead of animating.
- **Falls back.** If WebGL2 is unavailable or assets fail to load, sets
  `poster.webp` as a cover background on the container.

## Things worth knowing

**Frame rate is %FPS%fps, %NFRAMES% frames (~%DURATION%s).** This was supplied
as an input (not recoverable from a PNG sequence itself) and lives in
`model.json`. If the animation reads too fast or slow, that value is the
first thing to check.

**Grain is procedural, by design.** The source's film grain is stochastic, so
it is regenerated per-frame rather than stored. It will not match the
original frame-for-frame, and shouldn't — amplitude and channel correlation
were fitted from the source residual (%GRAINNOTE%). It also dithers the
gradient, which suppresses 8-bit banding, so prefer keeping it on.

**Contrast may change a lot over the animation.** Check the first and last
frames before placing text over it, or add a scrim (the example has one).

**Isolate the blend context.** `isolation: isolate` on the container keeps any
blend modes from escaping into the rest of the page.

**Textures are cacheable.** They're separate files deliberately. Serve with
long cache headers; they never change.

## How it works

Each frame is reconstructed per-pixel in a fragment shader:

```
color = mean + Σ coefficient_i(t) · basis_i
```

The source sequence was decomposed with an SVD across time; %K% components
capture %VARPCT%% of the variance (the remaining error is film grain — no
smooth low-rank model can represent stochastic noise, so this is the correct
place for the fit to plateau, not a fitting failure). Texture resolution
(%TEXW%×%TEXH%) was chosen anisotropically based on where the source's actual
detail lives — see `references/troubleshooting.md` in the skill if
regenerating with different content. Coefficients are interpolated between
frames from the table in `model.json`.

Measured against the actual shipped (WebP, quantized) textures: **%MAEPCT%%
mean absolute error** (%MAE255%/255), worst single frame %WORSTPCT%% (frame
%WORSTFRAME% of %NFRAMES%).

Because it's a shader rather than a video, it renders at whatever resolution
the canvas is — no fixed resolution, no decode cost, no scaling artifacts.

## Regenerating

If the source animation changes, re-export a PNG sequence (960px wide is
plenty) and re-run the `sequence-to-shader` skill's pipeline, ending with
`build_handoff.py`, to produce a fresh version of everything in this folder.
'''
