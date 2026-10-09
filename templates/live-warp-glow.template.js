/*
 * %CLASS% — live, editable WebGL2 recreation of an After Effects comp of the "warped still + glow + palette" kind:
 *   one blurred still -> Wave Warps (AE math, scripts/live/aewarp.py) -> Find Edges + Deep Glow (blur passes) ->
 *   Brightness & Contrast (17-point curve) -> palette last (CC Toner, exact) -> tone, dither, grain, interactions.
 * Template for sequence-to-shader's live path (references/live-recreation.md). Fill with scripts/live/fill_template.py:
 *   %CLASS%         class name                      %STILLKB%   size of still.png, KB
 *   %REF_W% %REF_H% reference frame (the AE passes' framing, px)   %COMP_W% %COMP_H%  the warped layer's comp size
 *   %REF_SCALE%     layer scale in the reference frame (e.g. 0.7)  %LOOP%  seconds per loop (for setProgress)
 *   %FITTED%        {curve[17], edgeLine, edgeGlow, glow, wideGlow, sigmaEdge, sigmaStar, sigmaWide} fitted to AE
 *   %STOPS% %WAVES% %PRESETS%  palette stops, AE Wave Warp settings, named palettes
 */

const MAX_STOPS = 8;
const MAX_WAVES = 4;
const MAX_RIPPLES = 12;
const REF = [%REF_W%, %REF_H%];            // reference frame (the AE passes' framing) in px
const COMP = [%COMP_W%, %COMP_H%];         // the warped layer's comp size: the space the waves live in
const REF_SCALE = %REF_SCALE%;              // layer scale within the reference frame
const GLOW = [480, 270];                   // glow buffer size
const WIDE = [120, 68];                    // wide-glow buffer size
const SHAPES = { sine: 0, uncircle: 1, semicircle: 2 };

// Fitted to After Effects renders by shader/tools/fit_wave_star_live.py (see README for the measured error).
export const FITTED = %FITTED%;

export const PRESETS = %PRESETS%;

export const DEFAULTS = {
  basePath: './',            // folder holding still.png (and poster.webp for the fallback)
  stillSrc: null,            // or a data:/blob: URL for the still (skips the fetch; for inlined pages)

  palette: { stops: %STOPS%, smooth: false },   // applied last, exactly like AE's CC Toner (dark -> light)
  waves: %WAVES%,            // AE Wave Warp units: height/width px (in the 1920x1080 comp), direction deg, speed cycles/s
  glow: {
    edgeLine: FITTED.edgeLine, edgeGlow: FITTED.edgeGlow, star: FITTED.glow, wide: FITTED.wideGlow,
    edgeSize: FITTED.sigmaEdge, starSize: FITTED.sigmaStar, wideSize: FITTED.sigmaWide,   // blur sizes, reference px
  },
  curve: FITTED.curve,       // brightness curve (17 points) ~ AE Brightness & Contrast
  accent: { color: '#ffffff', strength: 0 },     // optional extra tint on the edges, on top of the palette
  tone: { hue: 0, saturation: 1, brightness: 0, contrast: 1 },

  motion: { speed: 0.7, autoplay: true, loopSeconds: %LOOP% },   // 1 = the original AE speed
  grain: { amount: 0.5, shadowBias: 0.6, size: 1, correlation: 0.5 },
  layout: { fit: 'cover', focus: [0.5, 0.5], zoom: 1 },

  ripple: { enabled: true, strength: 7, speed: 260, width: 40, frequency: 3, lifetime: 1.6, spacing: 28, onTap: true },  // reference px
  scrollFade: { enabled: true, start: 0.1, end: 0.9 },
  parallax: { enabled: false, amount: 0.02, ease: 4 },
  reducedMotion: { respect: true, time: 0 },
  performance: { maxDPR: 2, pauseOffscreen: true },
  dither: 1,                 // +/- half an 8-bit step before the palette; hides banding (0 = off)
};

// ---------------------------------------------------------------------------------------------
// GLSL
// ---------------------------------------------------------------------------------------------

const VERT = `#version 300 es
in vec2 p; out vec2 vS;
void main(){ vS = p*0.5+0.5; vS.y = 1.0-vS.y; gl_Position = vec4(p,0.,1.); }`;


// Decodes the 12/16-bit still (PNG: red = high byte, green = low byte) once into a half-float texture.
const FRAG_DECODE = `#version 300 es
precision highp float;
uniform sampler2D uSrc; out vec4 o;
void main(){ vec3 t = texelFetch(uSrc, ivec2(gl_FragCoord.xy), 0).rgb; float v = (t.r*65280.0 + t.g*255.0)/65535.0; o = vec4(v, v, v, 1.0); }`;

// Shared by the glow and final passes: Wave Warp, the bent star, its edges, and pointer ripples.
const COMMON = `
const float TAU = 6.28318530718;
const vec2 REF = vec2(${REF[0]}.0, ${REF[1]}.0);
const vec2 COMP = vec2(${COMP[0]}.0, ${COMP[1]}.0);
uniform sampler2D uStill;
uniform float uTime;
uniform int uWaveCount;
uniform vec4 uWaveA[${MAX_WAVES}];   // height, width, direction (deg), speed
uniform vec2 uWaveB[${MAX_WAVES}];   // shape id, phase (deg)
uniform int uRipCount; uniform vec4 uRip[${MAX_RIPPLES}];   // xy centre (reference px), z age, w strength (px)
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
float waveShape(float kind, float ph){
  float x = ph - floor(ph);
  if (kind < 0.5) return sin(TAU*x);
  if (kind > 1.5) return 1.0 - 2.0*sqrt(clamp(1.0 - (2.0*x-1.0)*(2.0*x-1.0), 0.0, 1.0));   // semicircle: one arc per wavelength
  float h = fract(2.0*x), s = x < 0.5 ? 1.0 : -1.0;
  return s*(1.0 - sqrt(clamp(1.0 - (2.0*h-1.0)*(2.0*h-1.0), 0.0, 1.0)));                  // uncircle: cusps every half wavelength
}
// Where the warped layer at comp point c samples the unwarped star (AE applies the effects in order,
// so the last warp's displacement is evaluated first).
vec2 sourceCoords(vec2 c){
  for (int i = ${MAX_WAVES}-1; i >= 0; i--) {
    if (i >= uWaveCount) continue;
    vec4 a = uWaveA[i]; vec2 b = uWaveB[i];
    float th = radians(a.z);
    vec2 u = vec2(sin(th), -cos(th)), n = vec2(-u.y, u.x);
    float ph = dot(c, u)/(2.0*a.y) - a.w*uTime + b.y/360.0;
    c += n*(-a.x*waveShape(b.x, ph));
  }
  return c;
}
float body(vec2 ref){
  vec2 c = (ref - 0.5*REF)/${REF_SCALE} + 0.5*COMP;
  vec2 uv = sourceCoords(c)/COMP;
  if (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) return 0.0;   // AE Pinning: None -> empty
  return sampleStill(uv);
}
float edge(vec2 ref){   // ~ AE Find Edges: brightness slope over 1 reference px
  float gx = body(ref+vec2(1,0)) - body(ref-vec2(1,0));
  float gy = body(ref+vec2(0,1)) - body(ref-vec2(0,1));
  return length(vec2(gx, gy));
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
}`;

// Pass 1: bent star + edges into the glow buffer.
const FRAG_GLOW = `#version 300 es
precision highp float;
${COMMON}
uniform vec2 uSize;
out vec4 o;
void main(){
  vec2 ref = vec2(gl_FragCoord.x, uSize.y - gl_FragCoord.y)/uSize*REF;
  ref += ripple(ref);
  o = vec4(body(ref), edge(ref), 0.0, 1.0);
}`;

// Separable Gaussian, different sizes for R and G, clamp-to-edge.
const FRAG_BLUR = `#version 300 es
precision highp float;
uniform sampler2D uSrc; uniform vec2 uStep; uniform vec2 uSigma; uniform int uRadius;
out vec4 o;
void main(){
  vec2 uv = gl_FragCoord.xy/vec2(textureSize(uSrc, 0));   // blur passes write a target the same size as the source
  vec2 acc = vec2(0.0), wsum = vec2(0.0);
  for (int i = -uRadius; i <= uRadius; i++) {
    float f = float(i);
    vec2 w = exp(-0.5*(f*f)/max(uSigma*uSigma, vec2(1e-4)));
    acc += w*texture(uSrc, uv + uStep*f).rg; wsum += w;
  }
  o = vec4(acc/wsum, 0.0, 1.0);
}`;

// Box-average 4x4 texels of the glow buffer into the (much smaller) wide-glow buffer.
const FRAG_DOWN = `#version 300 es
precision highp float;
uniform sampler2D uSrc;
out vec4 o;
void main(){
  vec2 src = vec2(textureSize(uSrc, 0)), dst = src/4.0;
  vec2 base = floor(gl_FragCoord.xy)*4.0;
  float s = 0.0;
  for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) s += texelFetch(uSrc, ivec2(min(base + vec2(x, y), src-1.0)), 0).r;
  o = vec4(s/16.0, 0.0, 0.0, 1.0);
}`;

// Final pass at canvas resolution.
const FRAG_FINAL = `#version 300 es
precision highp float;
${COMMON}
in vec2 vS; out vec4 o;
uniform sampler2D uGlowTex, uWideTex;
uniform vec2 uScale, uOffset;
uniform float uCurve[17];
uniform float uEdgeLine, uEdgeGlow, uGlow, uWideGlow;
uniform int uStopCount; uniform float uPos[${MAX_STOPS}]; uniform vec3 uCol[${MAX_STOPS}]; uniform float uSmooth;
uniform vec3 uAccentC; uniform float uAccentS;
uniform float uHue, uSat, uBright, uContrast;
uniform float uGrain, uGrainShadow, uGrainSize, uGrainCorr, uNoiseTime, uDither;

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
void main(){
  vec2 ref = (vS*uScale + uOffset)*REF;
  ref += ripple(ref);
  float b = body(ref), e = edge(ref);
  vec2 tuv = vec2(ref.x/REF.x, 1.0 - ref.y/REF.y);
  vec2 bl = texture(uGlowTex, tuv).rg;
  float wd = texture(uWideTex, tuv).r;

  float x = clamp(b, 0.0, 1.0)*16.0; int i0 = min(int(floor(x)), 15);
  float curve = mix(uCurve[i0], uCurve[i0+1], x - float(i0));
  float edges = uEdgeLine*e + uEdgeGlow*bl.g;
  float g = clamp(curve + edges + uGlow*bl.r + uWideGlow*wd, 0.0, 1.0);
  g += uDither*(hash(gl_FragCoord.xy*1.37) - 0.5)/255.0;   // hides banding before the palette

  vec3 c = palette(g) + uAccentC*uAccentS*edges;

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

// ---------------------------------------------------------------------------------------------

function hexToRgb(h) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(h).trim());
  if (!m) throw new Error('expected #rrggbb, got ' + h);
  const n = parseInt(m[1], 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
}
const isObj = (x) => x && typeof x === 'object' && !Array.isArray(x);
function merge(into, from) {
  for (const k in from) {
    if (k === 'waves' && Array.isArray(from[k]) && Array.isArray(into[k])) {
      // waves merge per index, so set({ waves: [{ speed: 0.3 }] }) only changes the first wave's speed
      from[k].forEach((w, i) => { into[k][i] = isObj(w) ? merge(isObj(into[k][i]) ? into[k][i] : {}, w) : w; });
      into[k].length = from[k].length > into[k].length ? from[k].length : into[k].length;
    } else if (isObj(from[k]) && isObj(into[k])) merge(into[k], from[k]);
    else into[k] = Array.isArray(from[k]) ? from[k].map(v => isObj(v) ? { ...v } : v) : from[k];
  }
  return into;
}
const clone = (o) => JSON.parse(JSON.stringify(o));
function smooth(a, b, x) { const t = Math.max(0, Math.min(1, (x - a) / Math.max(b - a, 1e-6))); return t * t * (3 - 2 * t); }

// ---------------------------------------------------------------------------------------------
// Built-in interactions: { name, attach?(bg), update(bg, dt), detach?(bg) }.
// ---------------------------------------------------------------------------------------------

export class PointerRipple {
  constructor() { this.name = 'ripple'; this._last = null; }
  attach(bg) {
    this._down = (e) => {
      const c = bg.config.ripple; if (!c.enabled || !c.onTap) return;
      const p = bg._local(e); if (p) bg.addRipple(p.x, p.y, 1.6);
    };
    bg.el.addEventListener('pointerdown', this._down);
  }
  detach(bg) { bg.el.removeEventListener('pointerdown', this._down); }
  update(bg) {
    const c = bg.config.ripple, p = bg.input.pointer;
    if (!c.enabled || !p.inside) { this._last = null; return; }
    const r = bg.toReference(p.x, p.y);
    if (!this._last) { this._last = r; return; }
    if (Math.hypot(r[0] - this._last[0], r[1] - this._last[1]) >= c.spacing) {
      const speed = Math.hypot(p.vx * bg.aspect, p.vy);          // container heights per second
      bg.addRipple(p.x, p.y, Math.min(1, 0.35 + speed * 0.25));
      this._last = r;
    }
  }
}

export class ScrollFade {
  constructor() { this.name = 'scrollFade'; }
  update(bg) {
    const c = bg.config.scrollFade;
    const f = c.enabled ? smooth(c.start, c.end, bg.input.scroll) : 0;
    if (f !== bg.fx.fade) { bg.fx.fade = f; bg.canvas.style.opacity = String(1 - f); }
  }
}

export class Parallax {
  constructor() { this.name = 'parallax'; }
  update(bg, dt) {
    const c = bg.config.parallax, p = bg.input.pointer, o = bg.fx.offset;
    const tx = c.enabled && p.inside ? -(p.x - 0.5) * c.amount : 0;
    const ty = c.enabled && p.inside ? -(p.y - 0.5) * c.amount : 0;
    const k = 1 - Math.exp(-c.ease * dt);
    o[0] += (tx - o[0]) * k; o[1] += (ty - o[1]) * k;
    if (Math.abs(o[0] - tx) > 1e-5 || Math.abs(o[1] - ty) > 1e-5) bg.invalidate();
  }
}

// ---------------------------------------------------------------------------------------------

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
    this.time = 0;            // animation clock, seconds (drives the waves)
    this.noiseTime = 0;       // grain clock
    this.playing = false;
    this.visible = true;
    this.ready = false;
    this.interactions = [];
    this._dirty = true;
    this.reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  async init() {
    const gl = this.canvas.getContext('webgl2', { antialias: false, alpha: false, preserveDrawingBuffer: !!this.config.preserveDrawingBuffer });
    if (!gl) { this._fallback(); return false; }
    this.gl = gl;
    this.floatTargets = !!gl.getExtension('EXT_color_buffer_float');

    try {
      this.progs = {
        glow: this._program(FRAG_GLOW), blur: this._program(FRAG_BLUR),
        down: this._program(FRAG_DOWN), final: this._program(FRAG_FINAL),
      };
      this._stillImg = await this._img(this.config.stillSrc || this.config.basePath + 'still.png');
    } catch (e) { console.error(e); this._fallback(); return false; }

    const vb = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vb);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

    this.stillTex = this._decodeStill(this._stillImg);
    this.glowA = this._target(GLOW[0], GLOW[1]); this.glowB = this._target(GLOW[0], GLOW[1]);
    this.wideA = this._target(WIDE[0], WIDE[1]); this.wideB = this._target(WIDE[0], WIDE[1]);

    this.el.appendChild(this.canvas);
    this.ready = true;
    this._listen();
    this.use(new PointerRipple());
    this.use(new ScrollFade());
    this.use(new Parallax());

    const still = this.reducedMotion && this.config.reducedMotion.respect;
    if (still) this.time = this.config.reducedMotion.time;
    else if (this.config.motion.autoplay) this.playing = true;
    this._last = performance.now();
    this._raf = requestAnimationFrame(this._tick);
    return true;
  }

  // ---- public API ----

  /** Deep-merge any part of the config, e.g. set({ glow: { wide: 0.2 } }) or set({ waves: [{ speed: 0.3 }] }). */
  set(partial) { merge(this.config, partial); this.invalidate(); return this; }
  /** Back to the After Effects look (keeps basePath/stillSrc). */
  reset() {
    const keep = { basePath: this.config.basePath, stillSrc: this.config.stillSrc };
    this.config = merge(clone(this.defaults), keep); this.invalidate(); return this;
  }
  /** setPalette(['#000', ...]) spreads colors evenly; or pass [{ pos, color }]. */
  setPalette(stops) {
    this.config.palette.stops = stops.map((c, i) => typeof c === 'string' ? { pos: stops.length > 1 ? i / (stops.length - 1) : 0, color: c } : c);
    this.invalidate(); return this;
  }
  /** JSON of the current settings, to paste back in as config. */
  getConfig() { const { stillSrc, ...rest } = this.config; return clone(rest); }

  play() { this.playing = true; this._last = performance.now(); }
  pause() { this.playing = false; }
  /** Jump to a time in seconds. */
  seek(seconds) { this.time = seconds; this.invalidate(); }
  /** Position in the loop, 0..1 (e.g. drive it from scroll). */
  setProgress(p) { this.seek(p * this.config.motion.loopSeconds); }

  /** Ripple at container-relative x, y (0..1); strength 1 = config.ripple.strength. */
  addRipple(x, y, strength = 1) {
    if (this.reducedMotion && this.config.reducedMotion.respect) return;
    const r = this.toReference(x, y);
    this.fx.ripples.push({ x: r[0], y: r[1], age: 0, s: strength });
    if (this.fx.ripples.length > MAX_RIPPLES) this.fx.ripples.shift();
  }
  /** Container-relative (0..1) -> reference-frame pixels, through the current layout. */
  toReference(x, y) {
    const L = this._layoutParams();
    return [(x * L.sx + L.ox) * REF[0], (y * L.sy + L.oy) * REF[1]];
  }

  use(interaction) {
    if (interaction.name) this.remove(interaction.name);
    this.interactions.push(interaction);
    if (interaction.attach) interaction.attach(this);
    return () => this.remove(interaction);
  }
  remove(nameOrObj) {
    this.interactions = this.interactions.filter(it => {
      const hit = it === nameOrObj || (typeof nameOrObj === 'string' && it.name === nameOrObj);
      if (hit && it.detach) it.detach(this);
      return !hit;
    });
  }
  invalidate() { this._dirty = true; }
  get aspect() { return this.canvas.clientWidth / Math.max(1, this.canvas.clientHeight); }

  /** Draw one frame synchronously (used by the verification page). */
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
    gl.bindAttribLocation(p, 0, 'p');
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = {}, n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) {
      const name = gl.getActiveUniform(p, i).name.replace(/\[0\]$/, '');
      u[name] = gl.getUniformLocation(p, name);
    }
    return { p, u };
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

  _texture(img) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, img);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }

  _target(w, h) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    if (this.floatTargets) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, w, h, 0, gl.RGBA, gl.HALF_FLOAT, null);
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
    return { t, fb, w, h };
  }

  _img(src) {
    return new Promise((res, rej) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => rej(new Error('failed to load ' + src));
      im.src = src;
    });
  }

  _fallback() {
    if (!this.config.basePath) return;
    this.el.style.backgroundImage = `url("${this.config.basePath}poster.webp")`;
    this.el.style.backgroundSize = 'cover';
    this.el.style.backgroundPosition = 'center';
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
    if (p._nx !== undefined) {
      p.vx = dt > 0 ? (p._nx - p.x) / dt : 0; p.vy = dt > 0 ? (p._ny - p.y) / dt : 0;
      p.x = p._nx; p.y = p._ny; p._nx = undefined;
    } else { p.vx = 0; p.vy = 0; }
    const r = this.el.getBoundingClientRect();
    this.input.scroll = Math.max(0, Math.min(1, -r.top / Math.max(1, r.height)));
  }

  _layoutParams() {
    // Screen uv (0..1) -> reference uv: cover the container with the 16:9 reference frame, then zoom/focus/parallax.
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

  _common(u) {
    const gl = this.gl, c = this.config;
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.stillTex); gl.uniform1i(u.uStill, 0);
    gl.uniform1f(u.uTime, this.time);
    const waves = c.waves.slice(0, MAX_WAVES), A = new Float32Array(MAX_WAVES * 4), B = new Float32Array(MAX_WAVES * 2);
    waves.forEach((w, i) => {
      A.set([w.height, Math.max(1, w.width), w.direction, w.speed], i * 4);
      B.set([SHAPES[w.shape] ?? 0, w.phase || 0], i * 2);
    });
    gl.uniform1i(u.uWaveCount, waves.length); gl.uniform4fv(u.uWaveA, A); gl.uniform2fv(u.uWaveB, B);
    const R = this.fx.ripples, data = new Float32Array(MAX_RIPPLES * 4);
    R.forEach((r, i) => data.set([r.x, r.y, r.age, r.s * c.ripple.strength], i * 4));
    gl.uniform1i(u.uRipCount, R.length); gl.uniform4fv(u.uRip, data);
    gl.uniform1f(u.uRipSpeed, c.ripple.speed); gl.uniform1f(u.uRipWidth, c.ripple.width);
    gl.uniform1f(u.uRipFreq, c.ripple.frequency); gl.uniform1f(u.uRipLife, c.ripple.lifetime);
  }

  _blur(src, dst, tmp, sigmaR, sigmaG) {
    const gl = this.gl, { p, u } = this.progs.blur;
    gl.useProgram(p);
    const radius = Math.min(96, Math.ceil(3 * Math.max(sigmaR, sigmaG, 0.5)));
    gl.uniform1i(u.uRadius, radius); gl.uniform2f(u.uSigma, sigmaR, sigmaG); gl.uniform1i(u.uSrc, 0);
    for (const [from, to, step] of [[src, tmp, [1 / src.w, 0]], [tmp, dst, [0, 1 / src.h]]]) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, to.fb); gl.viewport(0, 0, to.w, to.h);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, from.t);
      gl.uniform2f(u.uStep, step[0], step[1]);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }
  }

  _draw() {
    const gl = this.gl, c = this.config, G = c.glow;
    this._size();
    gl.bindVertexArray(this.vao);

    // 1. bent star + edges -> glow buffer
    let pr = this.progs.glow; gl.useProgram(pr.p); this._common(pr.u);
    gl.uniform2f(pr.u.uSize, GLOW[0], GLOW[1]);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.glowA.fb); gl.viewport(0, 0, GLOW[0], GLOW[1]);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    // 2. wide glow: downsample the star, then blur
    pr = this.progs.down; gl.useProgram(pr.p); gl.uniform1i(pr.u.uSrc, 0);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.glowA.t);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.wideA.fb); gl.viewport(0, 0, WIDE[0], WIDE[1]);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    const ws = G.wideSize * WIDE[0] / REF[0];
    this._blur(this.wideA, this.wideA, this.wideB, ws, ws);

    // 3. star + edge glow blur (in place via glowB)
    const gs = GLOW[0] / REF[0];
    this._blur(this.glowA, this.glowA, this.glowB, G.starSize * gs, G.edgeSize * gs);

    // 4. final pass
    pr = this.progs.final; const u = pr.u; gl.useProgram(pr.p); this._common(u);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this.glowA.t); gl.uniform1i(u.uGlowTex, 1);
    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, this.wideA.t); gl.uniform1i(u.uWideTex, 2);
    const L = this._layoutParams();
    gl.uniform2f(u.uScale, L.sx, L.sy); gl.uniform2f(u.uOffset, L.ox, L.oy);
    gl.uniform1fv(u.uCurve, new Float32Array(c.curve));
    gl.uniform1f(u.uEdgeLine, G.edgeLine); gl.uniform1f(u.uEdgeGlow, G.edgeGlow);
    gl.uniform1f(u.uGlow, G.star); gl.uniform1f(u.uWideGlow, G.wide);
    const stops = [...c.palette.stops].sort((a, b) => a.pos - b.pos).slice(0, MAX_STOPS);
    const pos = new Float32Array(MAX_STOPS), col = new Float32Array(MAX_STOPS * 3);
    stops.forEach((s, i) => { pos[i] = s.pos; col.set(hexToRgb(s.color), i * 3); });
    gl.uniform1i(u.uStopCount, Math.max(1, stops.length)); gl.uniform1fv(u.uPos, pos); gl.uniform3fv(u.uCol, col);
    gl.uniform1f(u.uSmooth, c.palette.smooth ? 1 : 0);
    gl.uniform3fv(u.uAccentC, hexToRgb(c.accent.color)); gl.uniform1f(u.uAccentS, c.accent.strength);
    gl.uniform1f(u.uHue, c.tone.hue * Math.PI / 180); gl.uniform1f(u.uSat, c.tone.saturation);
    gl.uniform1f(u.uBright, c.tone.brightness); gl.uniform1f(u.uContrast, c.tone.contrast);
    gl.uniform1f(u.uGrain, c.grain.amount); gl.uniform1f(u.uGrainShadow, c.grain.shadowBias);
    gl.uniform1f(u.uGrainCorr, c.grain.correlation); gl.uniform1f(u.uNoiseTime, this.noiseTime);
    gl.uniform1f(u.uDither, c.dither || 0);
    gl.uniform1f(u.uGrainSize, Math.max(1, c.grain.size * Math.min(window.devicePixelRatio || 1, c.performance.maxDPR)));
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

    const animating = (this.playing && !still) || R.length > 0 || (this.config.grain.amount > 0 && !still);
    if (animating || this._dirty) this._draw();
  };
}

export default %CLASS%;
