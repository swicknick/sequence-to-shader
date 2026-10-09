/*
 * %CLASS% — live, editable WebGL2 recreation of an After Effects comp of the "warped copies" kind:
 *   a precomp holding a turning, wave-warped, palette-colored star (shape layer: transform BEFORE effects), placed as
 *   N copies in the main comp, each bent by a static warp measured from AE (coordinate maps -> scripts/live/coordmap.py,
 *   log-encoded x, Catmull-Rom upsampled on the GPU at load), Screen-blended over a background.
 * Template for sequence-to-shader's live path (references/live-recreation.md). Fill with scripts/live/fill_template.py:
 *   %CLASS%  %STILLKB%  %ARCKB%
 *   %REF_W% %REF_H%     reference frame px (the main comp as framed for the web, e.g. 960x540 = 50%)
 *   %DEMO_W% %DEMO_H%   main comp size        %G1_W% %G1_H%   the precomp (star layer) size
 *   %REF_SCALE%         main comp scale in the reference frame    %LOG_X%  log-edge x used by coordmap.build_grid
 *   %ARC%   {w, h, step, window: [x0, y0, x1, y1]}   %STOPS% %WAVE% %COPIES% %PRESETS%
 */

const MAX_STOPS = 8;
const MAX_RIPPLES = 12;
const MAX_COPIES = 4;
const REF = [%REF_W%, %REF_H%];            // reference frame: the main comp as framed for the web
const DEMO = [%DEMO_W%, %DEMO_H%];
const G1 = [%G1_W%, %G1_H%];               // the precomp holding the star
const SHAPES = { sine: 0, uncircle: 1, semicircle: 2 };

export const ARC = %ARC%;                  // measured Arc warp grid: { w, h, step, window: [x0, y0, x1, y1] }
export const PRESETS = %PRESETS%;

export const DEFAULTS = {
  basePath: './',            // folder holding still.png, arc.bin (and poster.webp for the fallback)
  stillSrc: null,            // or data:/blob: URLs (skip the fetches; for inlined pages)
  arcSrc: null,

  palette: { stops: %STOPS%, smooth: false },   // applied like AE's CC Toner (dark -> light)
  background: null,          // null = the darkest palette stop (as in the AE comp)
  wave: %WAVE%,              // AE Wave Warp units (px in the 1080x1920 star comp, deg, cycles/s); pinning 'all' | 'none'
  spin: 60,                  // star rotation, degrees per second
  copies: %COPIES%,          // each: position (px in the 1920x1080 comp) + rotation (deg); Screen-blended, bottom first
  edgeSoftness: 90,          // feather (px, in the 1920x1080 comp) where a bent copy ends; 0 = AE's hard edge
  tone: { hue: 0, saturation: 1, brightness: 0, contrast: 1 },

  motion: { speed: 0.04, autoplay: true, loopSeconds: 6, timeOffset: 2 },   // 1 = the original AE speed
  grain: { amount: 0.5, shadowBias: 0.6, size: 1, correlation: 0.5 },
  layout: { fit: 'cover', focus: [0.5, 0.5], zoom: 1 },

  ripple: { enabled: true, strength: 7, speed: 260, width: 40, frequency: 3, lifetime: 1.6, spacing: 28, onTap: true },
  scrollFade: { enabled: true, start: 0.1, end: 0.9 },
  parallax: { enabled: false, amount: 0.02, ease: 4 },
  reducedMotion: { respect: true, time: 0 },
  performance: { maxDPR: 2, pauseOffscreen: true, supersample: 2 },   // supersample n -> n x n samples per pixel (smooth fold lines)
  dither: 1,                 // +/- half an 8-bit step before the palette; hides banding (0 = off)
};

// Decodes the 12/16-bit still (PNG: red = high byte, green = low byte) once into a half-float texture.
const FRAG_DECODE = `#version 300 es
precision highp float;
uniform sampler2D uSrc; out vec4 o;
void main(){ vec3 t = texelFetch(uSrc, ivec2(gl_FragCoord.xy), 0).rgb; float v = (t.r*65280.0 + t.g*255.0)/65535.0; o = vec4(v, v, v, 1.0); }`;

// Smoothly resamples the Arc grid (Catmull-Rom, no kinks) into a 4x finer texture once at load. Linear blending of
// the coarse grid kinked at every grid line, which drew stair-steps along the fold lines at hero sizes.
const ARC_UPSAMPLE = 4;
const FRAG_ARC_UP = `#version 300 es
precision highp float;
uniform sampler2D uSrc; uniform float uScale; out vec4 o;
vec3 tap(ivec2 i){ ivec2 s = textureSize(uSrc, 0); return texelFetch(uSrc, clamp(i, ivec2(0), s - 1), 0).rgb; }
vec4 cr(float f){ float f2 = f*f, f3 = f2*f; return vec4(-0.5*f3 + f2 - 0.5*f, 1.5*f3 - 2.5*f2 + 1.0, -1.5*f3 + 2.0*f2 + 0.5*f, 0.5*f3 - 0.5*f2); }
void main(){
  vec2 t = gl_FragCoord.xy/uScale - 0.5, i0 = floor(t), f = t - i0;
  vec4 wx = cr(f.x), wy = cr(f.y);
  vec3 acc = vec3(0.0);
  for (int j = 0; j < 4; j++) {
    vec3 row = vec3(0.0);
    for (int i = 0; i < 4; i++) row += wx[i]*tap(ivec2(i0) + ivec2(i - 1, j - 1));
    acc += wy[j]*row;
  }
  o = vec4(acc, 1.0);
}`;

const VERT = `#version 300 es
in vec2 p; out vec2 vS;
void main(){ vS = p*0.5+0.5; vS.y = 1.0-vS.y; gl_Position = vec4(p,0.,1.); }`;

const FRAG = `#version 300 es
precision highp float;
const float TAU = 6.28318530718;
const vec2 REF = vec2(${REF[0]}.0, ${REF[1]}.0);
const vec2 DEMO = vec2(${DEMO[0]}.0, ${DEMO[1]}.0);
const vec2 G1 = vec2(${G1[0]}.0, ${G1[1]}.0);
in vec2 vS; out vec4 o;
uniform sampler2D uStill, uArc;
uniform vec4 uArcWin; uniform vec2 uArcGrid; uniform float uArcStep; uniform float uEdgeSoft;
uniform float uTime, uSpin, uDither; uniform vec2 uPix; uniform int uSS;
uniform vec4 uWaveA; uniform vec3 uWaveB;          // height, width, direction, speed | shape, phase, pinning
uniform int uCopyCount; uniform vec3 uCopy[${MAX_COPIES}];   // position xy, rotation (deg)
uniform vec2 uScale, uOffset;
uniform vec3 uBg;
uniform int uStopCount; uniform float uPos[${MAX_STOPS}]; uniform vec3 uCol[${MAX_STOPS}]; uniform float uSmooth;
uniform float uHue, uSat, uBright, uContrast;
uniform float uGrain, uGrainShadow, uGrainSize, uGrainCorr, uNoiseTime;
uniform int uRipCount; uniform vec4 uRip[${MAX_RIPPLES}];
uniform float uRipSpeed, uRipWidth, uRipFreq, uRipLife;

// Cubic B-spline sampling of the still (4 bilinear taps): smooth at any magnification, no bilinear diamonds.
float sampleStill(vec2 uv){
  vec2 size = vec2(textureSize(uStill, 0));
  vec2 p = uv*size - 0.5, i = floor(p), f = p - i;
  vec2 w0 = (1.0-f)*(1.0-f)*(1.0-f)/6.0, w1 = (3.0*f*f*f - 6.0*f*f + 4.0)/6.0;
  vec2 w2 = (-3.0*f*f*f + 3.0*f*f + 3.0*f + 1.0)/6.0, w3 = f*f*f/6.0;
  vec2 g0 = w0 + w1, g1 = w2 + w3;
  vec2 h0 = (i - 0.5 + w1/g0)/size, h1 = (i + 1.5 + w3/g1)/size;
  return g0.y*(g0.x*texture(uStill, vec2(h0.x, h0.y)).r + g1.x*texture(uStill, vec2(h1.x, h0.y)).r)
       + g1.y*(g0.x*texture(uStill, vec2(h0.x, h1.y)).r + g1.x*texture(uStill, vec2(h1.x, h1.y)).r);
}
vec2 rot(vec2 v, float deg){ float a = radians(deg), c = cos(a), s = sin(a); return vec2(c*v.x - s*v.y, s*v.x + c*v.y); }
float waveShape(float kind, float ph){
  float x = ph - floor(ph);
  if (kind < 0.5) return sin(TAU*x);
  if (kind > 1.5) return 1.0 - 2.0*sqrt(clamp(1.0 - (2.0*x-1.0)*(2.0*x-1.0), 0.0, 1.0));
  float h = fract(2.0*x), s = x < 0.5 ? 1.0 : -1.0;
  return s*(1.0 - sqrt(clamp(1.0 - (2.0*h-1.0)*(2.0*h-1.0), 0.0, 1.0)));
}
// The precomp at point g: the star is turned first (shape layers are continuously rasterized), then
// wave-warped in comp space, then colored.
float starGray(vec2 g){
  float th = radians(uWaveA.z);
  vec2 u = vec2(sin(th), -cos(th)), n = vec2(-u.y, u.x);
  float ph = dot(g, u)/(2.0*uWaveA.y) - uWaveA.w*uTime + uWaveB.y/360.0;
  float pin = 1.0;
  if (uWaveB.z > 0.5) {
    vec2 e = min(g, G1 - g)/(0.25*G1);
    pin = clamp(e.x, 0.0, 1.0)*clamp(e.y, 0.0, 1.0);
  }
  vec2 s = g + n*(-uWaveA.x*waveShape(uWaveB.x, ph)*pin);
  vec2 l = rot(s - 0.5*G1, -uSpin*uTime) + 0.5*G1;
  vec2 uv = l/G1;
  if (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) return 0.0;
  return sampleStill(uv);
}
float hash(vec2 q){ return fract(sin(dot(q, vec2(127.1,311.7))) * 43758.5453); }
vec3 palette(float v){
  v = clamp(v, uPos[0], uPos[uStopCount-1]);
  vec3 c = uCol[0];
  for (int i = 1; i < ${MAX_STOPS}; i++) {
    if (i >= uStopCount) break;
    float a = uPos[i-1], b = uPos[i];
    float t = clamp((v-a)/max(b-a, 1e-5), 0.0, 1.0);
    t = mix(t, t*t*(3.0-2.0*t), uSmooth);
    if (v >= a) c = mix(uCol[i-1], uCol[i], t);
  }
  return c;
}
vec3 hueRotate(vec3 c, float a){
  const mat3 toYIQ = mat3(0.299,0.596,0.211, 0.587,-0.274,-0.523, 0.114,-0.322,0.312);
  const mat3 toRGB = mat3(1.0,1.0,1.0, 0.956,-0.272,-1.106, 0.621,-0.647,1.703);
  vec3 y = toYIQ*c; float h = atan(y.z,y.y)+a, ch = length(y.yz);
  return toRGB*vec3(y.x, ch*cos(h), ch*sin(h));
}
vec2 ripple(vec2 ref){
  vec2 d = vec2(0.0);
  for (int i = 0; i < ${MAX_RIPPLES}; i++) {
    if (i >= uRipCount) break;
    vec4 r = uRip[i];
    vec2 dv = ref - r.xy; float dist = length(dv);
    float x = (dist - r.z*uRipSpeed)/uRipWidth;
    float env = exp(-x*x) * (1.0 - smoothstep(0.0, uRipLife, r.z)) * r.w;
    d += (dist > 1e-3 ? dv/dist : vec2(0.0)) * sin(x*uRipFreq*3.14159) * env;
  }
  return d;
}
void main(){
  vec3 acc = vec3(0.0);
  for (int sy = 0; sy < uSS; sy++) for (int sx = 0; sx < uSS; sx++) {
  vec2 sub = (vec2(sx, sy) + 0.5)/float(uSS) - 0.5;                 // sub-pixel offset (screen px)
  vec2 ref = ((vS + sub*uPix)*uScale + uOffset)*REF;
  ref += ripple(ref);
  vec2 D = (ref - 0.5*REF)/%REF_SCALE% + 0.5*DEMO;           // main comp pixel
  vec3 c = uBg;
  for (int k = 0; k < ${MAX_COPIES}; k++) {
    if (k >= uCopyCount) break;
    vec2 P = rot(D - uCopy[k].xy, -uCopy[k].z) + 0.5*G1;          // the copy's own (precomp) pixel
    // Arc: log(1081 - source x), source y - y, distance to the coverage edge. The log follows the Arc's steep
    // perspective squeeze toward the layer's right edge, which a plain displacement grid can't.
    vec3 a = texture(uArc, ((P - uArcWin.xy)/uArcStep + 0.5)/uArcGrid).rgb;
    vec2 g = vec2(%LOG_X% - exp(a.x), P.y + a.y);
    float edge = uEdgeSoft > 0.5 ? smoothstep(0.0, uEdgeSoft, a.z) : clamp(a.z + 0.5, 0.0, 1.0);   // a.z: px inside the bent copy
    float cover = edge * step(0.0, g.x) * step(0.0, g.y) * step(g.x, G1.x) * step(g.y, G1.y);
    float d = uDither*(hash(gl_FragCoord.xy*1.37 + vec2(sx, sy)*17.1 + float(k)*5.3) - 0.5)/255.0;
    vec3 col = palette(starGray(g) + d);
    c = mix(c, 1.0 - (1.0 - c)*(1.0 - col), cover);          // Screen
  }
  acc += c;
  }
  vec3 c = acc/float(uSS*uSS);
  c = hueRotate(c, uHue);
  float l = dot(c, vec3(0.2126,0.7152,0.0722));
  c = mix(vec3(l), c, uSat);
  c = clamp((c-0.5)*uContrast + 0.5 + uBright, 0.0, 1.0);
  // Output dither (+/- 1 step, triangular): keeps smooth gradients from showing 8-bit bands on screen.
  vec2 fq = gl_FragCoord.xy;
  c += uDither*(vec3(hash(fq + 0.13), hash(fq + 0.57), hash(fq + 0.91)) + vec3(hash(fq*1.7 + 3.1), hash(fq*1.7 + 5.3), hash(fq*1.7 + 7.7)) - 1.0)/255.0;
  if (uGrain > 0.0) {
    float lum = dot(c, vec3(0.2126,0.7152,0.0722));
    float amp = uGrain*(6.0/255.0)*mix(1.0, 1.0-lum, uGrainShadow)*3.4641;
    vec2 sd = floor(gl_FragCoord.xy/uGrainSize) + vec2(uNoiseTime*61.0, uNoiseTime*37.0);
    float gC = hash(sd)-0.5;
    vec3 gI = vec3(hash(sd+11.3), hash(sd+27.7), hash(sd+43.1))-0.5;
    c += amp*(sqrt(uGrainCorr)*gC + sqrt(1.0-uGrainCorr)*gI);
  }
  o = vec4(clamp(c, 0.0, 1.0), 1.0);
}`;

function hexToRgb(h) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(h).trim());
  if (!m) throw new Error('expected #rrggbb, got ' + h);
  const n = parseInt(m[1], 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
}
const isObj = (x) => x && typeof x === 'object' && !Array.isArray(x);
function merge(into, from) {
  for (const k in from) {
    if (k === 'copies' && Array.isArray(from[k]) && Array.isArray(into[k])) {
      from[k].forEach((w, i) => { into[k][i] = isObj(w) ? merge(isObj(into[k][i]) ? into[k][i] : {}, w) : w; });
    } else if (isObj(from[k]) && isObj(into[k])) merge(into[k], from[k]);
    else into[k] = Array.isArray(from[k]) ? from[k].map(v => isObj(v) ? { ...v } : v) : from[k];
  }
  return into;
}
const clone = (o) => JSON.parse(JSON.stringify(o));
function smooth(a, b, x) { const t = Math.max(0, Math.min(1, (x - a) / Math.max(b - a, 1e-6))); return t * t * (3 - 2 * t); }

// ---- interactions (same contract as the live-warp-glow template): { name, attach?(bg), update(bg, dt), detach?(bg) } ----

export class PointerRipple {
  constructor() { this.name = 'ripple'; this._last = null; }
  attach(bg) {
    this._down = (e) => { const c = bg.config.ripple; if (!c.enabled || !c.onTap) return; const p = bg._local(e); if (p) bg.addRipple(p.x, p.y, 1.6); };
    bg.el.addEventListener('pointerdown', this._down);
  }
  detach(bg) { bg.el.removeEventListener('pointerdown', this._down); }
  update(bg) {
    const c = bg.config.ripple, p = bg.input.pointer;
    if (!c.enabled || !p.inside) { this._last = null; return; }
    const r = bg.toReference(p.x, p.y);
    if (!this._last) { this._last = r; return; }
    if (Math.hypot(r[0] - this._last[0], r[1] - this._last[1]) >= c.spacing) {
      bg.addRipple(p.x, p.y, Math.min(1, 0.35 + Math.hypot(p.vx * bg.aspect, p.vy) * 0.25));
      this._last = r;
    }
  }
}
export class ScrollFade {
  constructor() { this.name = 'scrollFade'; }
  update(bg) {
    const c = bg.config.scrollFade, f = c.enabled ? smooth(c.start, c.end, bg.input.scroll) : 0;
    if (f !== bg.fx.fade) { bg.fx.fade = f; bg.canvas.style.opacity = String(1 - f); }
  }
}
export class Parallax {
  constructor() { this.name = 'parallax'; }
  update(bg, dt) {
    const c = bg.config.parallax, p = bg.input.pointer, o = bg.fx.offset;
    const tx = c.enabled && p.inside ? -(p.x - 0.5) * c.amount : 0, ty = c.enabled && p.inside ? -(p.y - 0.5) * c.amount : 0;
    const k = 1 - Math.exp(-c.ease * dt);
    o[0] += (tx - o[0]) * k; o[1] += (ty - o[1]) * k;
    if (Math.abs(o[0] - tx) > 1e-5 || Math.abs(o[1] - ty) > 1e-5) bg.invalidate();
  }
}

export class %CLASS% {
  constructor(container, config = {}) {
    this.el = container;
    this.defaults = clone(DEFAULTS);
    this.config = merge(clone(DEFAULTS), config);
    this.canvas = document.createElement('canvas');
    Object.assign(this.canvas.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', display: 'block' });
    this.canvas.setAttribute('aria-hidden', 'true');
    this.input = { pointer: { x: 0.5, y: 0.5, vx: 0, vy: 0, inside: false }, scroll: 0 };
    this.fx = { ripples: [], offset: [0, 0], fade: 0 };
    this.time = 0; this.noiseTime = 0;
    this.playing = false; this.visible = true; this.ready = false;
    this.interactions = []; this._dirty = true;
    this.reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  async init() {
    const gl = this.canvas.getContext('webgl2', { antialias: false, alpha: false, preserveDrawingBuffer: !!this.config.preserveDrawingBuffer });
    if (!gl) { this._fallback(); return false; }
    this.gl = gl;
    try {
      this.prog = this._program(FRAG);
      const [img, arc] = await Promise.all([
        this._img(this.config.stillSrc || this.config.basePath + 'still.png'),
        fetch(this.config.arcSrc || this.config.basePath + 'arc.bin').then(async r => {
          if (!r.ok) throw new Error('arc data ' + r.status);
          if (!/\.json($|\?)/.test(r.url)) return r.arrayBuffer();
          const b = atob((await r.json()).b64), u = new Uint8Array(b.length);   // { "b64": ... } for hosts that don't serve .bin
          for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
          return u.buffer;
        }),
      ]);
      this._stillImg = img;
      this._arcRaw = this._texture(gl.RGB16F, gl.RGB, gl.HALF_FLOAT, new Uint16Array(arc), ARC.w, ARC.h);
    } catch (e) { console.error(e); this._fallback(); return false; }

    const vb = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vb);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    this.vao = gl.createVertexArray(); gl.bindVertexArray(this.vao);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    this.floatTargets = !!gl.getExtension('EXT_color_buffer_float');
    this.stillTex = this._decodeStill(this._stillImg);
    this.arcTex = this._upsampleArc(this._arcRaw);

    this.el.appendChild(this.canvas);
    this.ready = true;
    this._listen();
    this.use(new PointerRipple()); this.use(new ScrollFade()); this.use(new Parallax());
    if (this.reducedMotion && this.config.reducedMotion.respect) this.time = this.config.reducedMotion.time;
    else if (this.config.motion.autoplay) this.playing = true;
    this._last = performance.now();
    this._raf = requestAnimationFrame(this._tick);
    return true;
  }

  // ---- public API (same as the live-warp-glow template) ----
  set(partial) { merge(this.config, partial); this.invalidate(); return this; }
  reset() {
    const keep = { basePath: this.config.basePath, stillSrc: this.config.stillSrc, arcSrc: this.config.arcSrc };
    this.config = merge(clone(this.defaults), keep); this.invalidate(); return this;
  }
  setPalette(stops) {
    this.config.palette.stops = stops.map((c, i) => typeof c === 'string' ? { pos: stops.length > 1 ? i / (stops.length - 1) : 0, color: c } : c);
    this.invalidate(); return this;
  }
  getConfig() { const { stillSrc, arcSrc, ...rest } = this.config; return clone(rest); }
  play() { this.playing = true; this._last = performance.now(); }
  pause() { this.playing = false; }
  seek(seconds) { this.time = seconds; this.invalidate(); }
  setProgress(p) { this.seek(p * this.config.motion.loopSeconds); }
  addRipple(x, y, strength = 1) {
    if (this.reducedMotion && this.config.reducedMotion.respect) return;
    const r = this.toReference(x, y);
    this.fx.ripples.push({ x: r[0], y: r[1], age: 0, s: strength });
    if (this.fx.ripples.length > MAX_RIPPLES) this.fx.ripples.shift();
  }
  toReference(x, y) { const L = this._layoutParams(); return [(x * L.sx + L.ox) * REF[0], (y * L.sy + L.oy) * REF[1]]; }
  use(it) { if (it.name) this.remove(it.name); this.interactions.push(it); if (it.attach) it.attach(this); return () => this.remove(it); }
  remove(nameOrObj) {
    this.interactions = this.interactions.filter(it => {
      const hit = it === nameOrObj || (typeof nameOrObj === 'string' && it.name === nameOrObj);
      if (hit && it.detach) it.detach(this);
      return !hit;
    });
  }
  invalidate() { this._dirty = true; }
  get aspect() { return this.canvas.clientWidth / Math.max(1, this.canvas.clientHeight); }
  renderNow() { if (this.ready) this._draw(); }
  destroy() {
    if (this._raf) cancelAnimationFrame(this._raf);
    for (const it of [...this.interactions]) this.remove(it);
    if (this._io) this._io.disconnect();
    for (const [t, ev, fn, o] of this._listeners || []) t.removeEventListener(ev, fn, o);
    const lose = this.gl && this.gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
    if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
    this.ready = false;
  }

  // ---- internals ----
  _program(frag) {
    const gl = this.gl;
    const sh = (type, src) => {
      const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, VERT)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, frag));
    gl.bindAttribLocation(p, 0, 'p'); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = {}, n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) { const name = gl.getActiveUniform(p, i).name.replace(/\[0\]$/, ''); u[name] = gl.getUniformLocation(p, name); }
    return { p, u };
  }
  _texture(internal, format, type, src, w, h) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    if (w) gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, type, src);
    else gl.texImage2D(gl.TEXTURE_2D, 0, internal, format, type, src);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }
  _upsampleArc(raw) {
    const gl = this.gl, w = ARC.w * ARC_UPSAMPLE, h = ARC.h * ARC_UPSAMPLE;
    gl.bindTexture(gl.TEXTURE_2D, raw);
    for (const p of [gl.TEXTURE_MIN_FILTER, gl.TEXTURE_MAG_FILTER]) gl.texParameteri(gl.TEXTURE_2D, p, gl.NEAREST);
    // 32-bit float where it can be filtered (keeps the log-encoded coordinates exact), else half float
    const f32 = this.floatTargets && gl.getExtension('OES_texture_float_linear');
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    if (f32) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, w, h, 0, gl.RGBA, gl.FLOAT, null);
    else if (this.floatTargets) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, w, h, 0, gl.RGBA, gl.HALF_FLOAT, null);
    else { gl.bindTexture(gl.TEXTURE_2D, raw); for (const p of [gl.TEXTURE_MIN_FILTER, gl.TEXTURE_MAG_FILTER]) gl.texParameteri(gl.TEXTURE_2D, p, gl.LINEAR); return raw; }
    for (const [p, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, p, v);
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
    const prog = this._program(FRAG_ARC_UP);
    gl.useProgram(prog.p); gl.bindVertexArray(this.vao); gl.viewport(0, 0, w, h);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, raw);
    gl.uniform1i(prog.u.uSrc, 0); gl.uniform1f(prog.u.uScale, ARC_UPSAMPLE);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.deleteFramebuffer(fb); gl.deleteProgram(prog.p); gl.deleteTexture(raw);
    return t;
  }

  _decodeStill(img) {
    // The still is stored at 12-bit precision (no 8-bit banding) and decoded on the GPU into a half-float texture.
    const gl = this.gl, src = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, src);
    gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, img);
    for (const p of [gl.TEXTURE_MIN_FILTER, gl.TEXTURE_MAG_FILTER]) gl.texParameteri(gl.TEXTURE_2D, p, gl.NEAREST);
    const dst = this._target(img.width, img.height), prog = this._program(FRAG_DECODE);
    gl.useProgram(prog.p); gl.bindVertexArray(this.vao);
    gl.bindFramebuffer(gl.FRAMEBUFFER, dst.fb); gl.viewport(0, 0, dst.w, dst.h);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, src); gl.uniform1i(prog.u.uSrc, 0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.deleteTexture(src); gl.deleteFramebuffer(dst.fb); gl.deleteProgram(prog.p);
    return dst.t;
  }

  _target(w, h) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    if (this.floatTargets) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, w, h, 0, gl.RGBA, gl.HALF_FLOAT, null);
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    for (const [p, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, p, v);
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
    return { t, fb, w, h };
  }
  _img(src) {
    return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => rej(new Error('failed to load ' + src)); im.src = src; });
  }
  _fallback() {
    if (!this.config.basePath) return;
    this.el.style.backgroundImage = `url("${this.config.basePath}poster.webp")`;
    this.el.style.backgroundSize = 'cover'; this.el.style.backgroundPosition = 'center';
  }
  _listen() {
    const L = this._listeners = [];
    const on = (t, ev, fn, o) => { t.addEventListener(ev, fn, o); L.push([t, ev, fn, o]); };
    const p = this.input.pointer;
    on(window, 'pointermove', (e) => { const q = this._local(e); p.inside = !!q; if (q) { p._nx = q.x; p._ny = q.y; } }, { passive: true });
    on(document, 'pointerleave', () => { p.inside = false; });
    on(document, 'visibilitychange', () => { this._last = performance.now(); });
    if (this.config.performance.pauseOffscreen && 'IntersectionObserver' in window) {
      this._io = new IntersectionObserver(([e]) => { this.visible = e.isIntersecting; this._last = performance.now(); }, { threshold: 0 });
      this._io.observe(this.el);
    }
  }
  _local(e) {
    const r = this.el.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? { x, y } : null;
  }
  _updateInput(dt) {
    const p = this.input.pointer;
    if (p._nx !== undefined) { p.vx = dt > 0 ? (p._nx - p.x) / dt : 0; p.vy = dt > 0 ? (p._ny - p.y) / dt : 0; p.x = p._nx; p.y = p._ny; p._nx = undefined; }
    else { p.vx = 0; p.vy = 0; }
    const r = this.el.getBoundingClientRect();
    this.input.scroll = Math.max(0, Math.min(1, -r.top / Math.max(1, r.height)));
  }
  _layoutParams() {
    const L = this.config.layout, a = this.aspect, t = REF[0] / REF[1];
    let sx = 1, sy = 1;
    if (L.fit === 'cover') { if (a > t) sy = t / a; else sx = a / t; }
    sx /= L.zoom; sy /= L.zoom;
    let ox = (1 - sx) * L.focus[0] + this.fx.offset[0], oy = (1 - sy) * L.focus[1] + this.fx.offset[1];
    ox = Math.max(Math.min(ox, 1 - sx), Math.min(0, 1 - sx)); oy = Math.max(Math.min(oy, 1 - sy), Math.min(0, 1 - sy));
    return { sx, sy, ox, oy };
  }
  _size() {
    const d = Math.min(window.devicePixelRatio || 1, this.config.performance.maxDPR);
    const w = Math.round(this.canvas.clientWidth * d), h = Math.round(this.canvas.clientHeight * d);
    if (w > 0 && h > 0 && (this.canvas.width !== w || this.canvas.height !== h)) { this.canvas.width = w; this.canvas.height = h; }
  }
  _draw() {
    const gl = this.gl, u = this.prog.u, c = this.config;
    this._size();
    gl.useProgram(this.prog.p); gl.bindVertexArray(this.vao);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.stillTex); gl.uniform1i(u.uStill, 0);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this.arcTex); gl.uniform1i(u.uArc, 1);
    gl.uniform4f(u.uArcWin, ...ARC.window); gl.uniform2f(u.uArcGrid, ARC.w, ARC.h); gl.uniform1f(u.uArcStep, ARC.step);
    gl.uniform1f(u.uEdgeSoft, c.edgeSoftness || 0);
    gl.uniform1f(u.uDither, c.dither || 0);
    gl.uniform1i(u.uSS, Math.max(1, Math.min(4, Math.round(c.performance.supersample || 1))));
    gl.uniform2f(u.uPix, 1 / this.canvas.width, 1 / this.canvas.height);
    gl.uniform1f(u.uTime, this.time + c.motion.timeOffset); gl.uniform1f(u.uSpin, c.spin);
    const w = c.wave;
    gl.uniform4f(u.uWaveA, w.height, Math.max(1, w.width), w.direction, w.speed);
    gl.uniform3f(u.uWaveB, SHAPES[w.shape] ?? 2, w.phase || 0, w.pinning === 'all' ? 1 : 0);
    const copies = c.copies.slice(0, MAX_COPIES), cd = new Float32Array(MAX_COPIES * 3);
    copies.forEach((k, i) => cd.set([k.position[0], k.position[1], k.rotation], i * 3));
    gl.uniform1i(u.uCopyCount, copies.length); gl.uniform3fv(u.uCopy, cd);
    const L = this._layoutParams();
    gl.uniform2f(u.uScale, L.sx, L.sy); gl.uniform2f(u.uOffset, L.ox, L.oy);
    const stops = [...c.palette.stops].sort((a, b) => a.pos - b.pos).slice(0, MAX_STOPS);
    const pos = new Float32Array(MAX_STOPS), col = new Float32Array(MAX_STOPS * 3);
    stops.forEach((s, i) => { pos[i] = s.pos; col.set(hexToRgb(s.color), i * 3); });
    gl.uniform1i(u.uStopCount, Math.max(1, stops.length)); gl.uniform1fv(u.uPos, pos); gl.uniform3fv(u.uCol, col);
    gl.uniform1f(u.uSmooth, c.palette.smooth ? 1 : 0);
    gl.uniform3fv(u.uBg, hexToRgb(c.background || stops[0].color));
    gl.uniform1f(u.uHue, c.tone.hue * Math.PI / 180); gl.uniform1f(u.uSat, c.tone.saturation);
    gl.uniform1f(u.uBright, c.tone.brightness); gl.uniform1f(u.uContrast, c.tone.contrast);
    gl.uniform1f(u.uGrain, c.grain.amount); gl.uniform1f(u.uGrainShadow, c.grain.shadowBias);
    gl.uniform1f(u.uGrainCorr, c.grain.correlation); gl.uniform1f(u.uNoiseTime, this.noiseTime);
    gl.uniform1f(u.uGrainSize, Math.max(1, c.grain.size * Math.min(window.devicePixelRatio || 1, c.performance.maxDPR)));
    const R = this.fx.ripples, rd = new Float32Array(MAX_RIPPLES * 4);
    R.forEach((r, i) => rd.set([r.x, r.y, r.age, r.s * c.ripple.strength], i * 4));
    gl.uniform1i(u.uRipCount, R.length); gl.uniform4fv(u.uRip, rd);
    gl.uniform1f(u.uRipSpeed, c.ripple.speed); gl.uniform1f(u.uRipWidth, c.ripple.width);
    gl.uniform1f(u.uRipFreq, c.ripple.frequency); gl.uniform1f(u.uRipLife, c.ripple.lifetime);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    this._dirty = false;
  }
  _tick = (now) => {
    this._raf = requestAnimationFrame(this._tick);
    if (!this.ready) return;
    const dt = Math.min(0.1, (now - this._last) / 1000);
    this._last = now;
    if (!this.visible || document.hidden) return;
    this._updateInput(dt);
    for (const it of this.interactions) it.update(this, dt);
    if (this.fx.fade >= 1) return;
    const still = this.reducedMotion && this.config.reducedMotion.respect;
    if (this.playing && !still) this.time += dt * this.config.motion.speed;
    if (!still) this.noiseTime += dt;
    const R = this.fx.ripples;
    for (const r of R) r.age += dt;
    while (R.length && R[0].age > this.config.ripple.lifetime) R.shift();
    if ((this.playing && !still) || R.length > 0 || (this.config.grain.amount > 0 && !still) || this._dirty) this._draw();
  };
}

export default %CLASS%;
