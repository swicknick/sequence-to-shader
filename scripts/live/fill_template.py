#!/usr/bin/env python3
"""Fill a live-shader template: replace every %KEY% with values from a JSON file (objects/lists are JSON-encoded).

    python fill_template.py ../../templates/live-warp-glow.template.js values.json out/my-hero-background.js

Fails if any %KEY% is left unfilled, so a template change can't silently ship a broken module."""
import json, re, sys

tpl, values, out = sys.argv[1:4]
s = open(tpl).read()
for k, v in json.load(open(values)).items():
    s = s.replace(f'%{k}%', v if isinstance(v, str) else json.dumps(v))
left = sorted(set(re.findall(r'%([A-Z_0-9]+)%', s)))
if left:
    sys.exit(f'unfilled placeholders: {left}')
open(out, 'w').write(s)
print(f'wrote {out}')
