# Integrating into a real site

`scripts/build_handoff.py` already produces a real integration example
(`index.html`) and a drop-in module applying everything below by default —
positioned container, isolated blend context, scrim guidance, off-screen
pause, reduced-motion, poster fallback. Reach for this document when
customizing beyond those defaults (a different playback trigger, a layout
the generated example doesn't cover) or explaining *why* a default exists,
not to hand-build what `build_handoff.py` already gives you. The step 5
"quick look" demo page (`build_shader.py`) is a different thing entirely —
a throwaway harness for iterating on rank/resolution, never a handoff — so
if you're starting from that one instead, the manual extraction described
below is exactly what `build_handoff.py` is for.

## Sizing

The demo sets `aspect-ratio` on the stage. In production the background usually
fills a section instead:

```css
.hero { position: relative; isolation: isolate; }
.hero canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
```

`isolation: isolate` matters if anything in the stack uses blend modes — without
it, blending escapes into the rest of the page.

## Content on top

The reconstruction's brightness changes over the animation. Do not assume
contrast holds for text placed over it — check the darkest and brightest frames,
or add a scrim.

## Playback

Decide whether it loops, plays once on entry, or is scroll-driven. For a
play-once bloom, drive `frame` from an IntersectionObserver rather than
autoplaying off-screen.

Pause the render loop when off-screen:

```js
const io = new IntersectionObserver(([e]) => { running = e.isIntersecting; });
io.observe(canvas);
```

## Reduced motion

The generated page honours `prefers-reduced-motion` by holding the final frame.
Keep that. A static final frame is usually the right still, but confirm which
frame reads best.

## Poster / fallback

Export a representative frame as WebP for:
- WebGL2-unavailable fallback
- the paint before textures decode

Textures are small, so decode is fast, but a poster still avoids a flash.

## Caching

Build without `--inline` for production so the textures are separate cacheable
files. Use `--inline` only for a single-file handoff or a demo.

## Frame rate

`fps` in model.json drives playback speed. It is an input, not something
recovered from the frames — if the animation feels fast or slow, that is the
first thing to check.
