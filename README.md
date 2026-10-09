# Sequence to Shader

**A Claude skill that turns an animated gradient into a live WebGL background: a fraction of the weight, matched to the original frame by frame.**

Animated gradients are everywhere on the web: hero backgrounds, product launches, AI and SaaS landing pages, ambient glows behind headlines. They are usually designed in After Effects, Blender or Cinema 4D, and then they hit the same wall on the way to production:

- **Ship it as video**, and a few seconds of soft color costs megabytes. It is the heaviest thing on the page and the last thing to load.
- **Rebuild it by hand** in CSS or a shader, and it never quite matches. The colors in the project file are not the colors on screen: grading, glows and blend modes change them after the fact.

Sequence to Shader takes a third route. It **measures the finished render** and rebuilds it as a small, dependency-free WebGL component, then checks the result against the original before anything ships.

**Typical result:** a few seconds of rendered frames (tens of MB) becomes about 20–150 KB, under 1% mean error, sharp at any screen size, with no video to decode.

**Live demo:** [five gradient heroes rebuilt with it](https://nicholasrode.com/work/sequence-to-shader/).

## One skill, two paths

Every animation is triaged first, and the skill picks the right rebuild for it. A page with several heroes can mix both.

| | **Compress** | **Live** |
|---|---|---|
| **Best for** | Soft, drifting gradients, glows and auroras | Sharp features that travel across the frame: folds, streaks, warped edges |
| **How** | A handful of base images, mixed over time, reproduce every frame | The motion is recomputed every frame from one still plus the measured After Effects effects |
| **Needs** | The rendered frames | The rendered frames and the After Effects project |
| **After launch** | Colors and motion are fixed, like the original | Palettes, speed and motion stay editable, and it can react to the cursor |

## What you get

Give Claude the rendered frames and the frame rate. It hands back a package a dev team can drop in:

- **A drop-in ES module** with no dependencies. It pauses itself off-screen, honors `prefers-reduced-motion`, and falls back to a still image on browsers without WebGL2.
- **An integration example** and a **self-contained preview** you can open with a double-click.
- **A poster image** rendered from what the shader actually draws.
- **A README with measured numbers** for that asset: mean error, worst frame and payload. No "looks close."

## Why it holds up

- **It measures the render, never the project file.** Authored colors are pre-grade; the screen is what counts. The live path reads which effects the comp uses, then measures how each one actually behaves from After Effects renders.
- **Verified, not eyeballed.** Results are checked against the source using the real exported textures, or the real module running in Chrome, with side-by-side comparison sheets.
- **Detail goes where it lives.** A light streak is sharp on one axis and soft on the other, so resolution is chosen per axis, guided by a detail measure that error scores alone miss.
- **Grain that reads as real.** Film grain is measured and rebuilt separately: stronger in shadows, partially correlated across color channels.

## Install

Clone it into your Claude skills folder and install the two Python dependencies:

```bash
git clone https://github.com/swicknick/sequence-to-shader ~/.claude/skills/sequence-to-shader
pip install -r ~/.claude/skills/sequence-to-shader/requirements.txt
```

Then ask Claude to put an animated background on a page, or to slim down one that is too heavy. The skill triggers on requests like "this hero video is too big" or "rebuild this After Effects background as a shader."

> **On Apple Silicon:** use the pinned numpy in `requirements.txt`. The default macOS build can return silently wrong matrix results at the sizes this uses. See [`references/troubleshooting.md`](references/troubleshooting.md).

## Run it without Claude

The scripts work on their own. The compress path:

```bash
python3 scripts/triage.py        <frames>                       # COMPRESS, LIVE or BORDERLINE, with reasons
python3 scripts/decompose.py     <frames> --out <model>         # pick the number of components
python3 scripts/decompose.py     <frames> --out <model> --probe-resolutions
python3 scripts/fit_grain.py     <frames> <model>               # measure the grain
python3 scripts/verify.py        <frames> <model> --compare compare.png
python3 scripts/build_handoff.py <frames> <model> --out <dir> --name "Aurora"
```

The live path is a guided process rather than one command; [`references/live-recreation.md`](references/live-recreation.md) walks through it. [`SKILL.md`](SKILL.md) covers every step and how to read the numbers, and [`references/integration.md`](references/integration.md) covers dropping the result into a real site.

## Requirements

- Python 3.9+ with numpy and Pillow
- Google Chrome, for the live path's in-browser verification (set `CHROME` if it isn't in the default macOS location)
- Adobe After Effects, for the live path only
- Output runs in any browser with WebGL2

Triage is tuned on real After Effects renders. On unusually grainy footage it can lean toward BORDERLINE or LIVE; if a soft gradient gets LIVE, try the compress path and check the compare sheet.

## License

[MIT](LICENSE). Made by [Nicholas Rode](https://nicholasrode.com).
