#!/usr/bin/env python3
"""
Build a self-contained WebGL2 page from a decomposed model.

    python3 build_shader.py <model_dir> --out page.html [--inline] [--title "..."]

--inline base64-embeds textures for a single-file deliverable.
Without it, textures are copied next to the HTML for normal caching.
"""
import argparse, base64, json, os, shutil

TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{color-scheme:light dark;--bg:#f4f4f4;--fg:#1a1a1a;--muted:#666;--line:rgba(0,0,0,.15)}
  @media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0b0b0c;--fg:#eee;--muted:#8a8a8a;--line:rgba(255,255,255,.18)}}
  :root[data-theme="dark"]{--bg:#0b0b0c;--fg:#eee;--muted:#8a8a8a;--line:rgba(255,255,255,.18)}
  html,body{margin:0;background:var(--bg);color:var(--fg);font-family:ui-monospace,"SF Mono",Menlo,monospace}
  .wrap{max-width:1000px;margin:0 auto;padding:20px 16px 40px}
  h1{font-size:14px;font-weight:600;margin:0 0 2px}
  .sub{color:var(--muted);font-size:11.5px;margin:0 0 14px;line-height:1.55}
  .stage{position:relative;width:100%;overflow:hidden;border-radius:6px;background:#000}
  canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
  .bar{display:flex;align-items:center;gap:10px;margin-top:10px;flex-wrap:wrap}
  button{font:inherit;font-size:11.5px;padding:5px 11px;border-radius:5px;cursor:pointer;background:transparent;color:var(--fg);border:1px solid var(--line)}
  button:hover{border-color:var(--fg)}
  input[type=range]{flex:1;min-width:160px}
  .t{font-variant-numeric:tabular-nums;color:var(--muted);font-size:11.5px;min-width:74px}
  label{font-size:11.5px;color:var(--muted);display:flex;align-items:center;gap:5px}
</style>
</head>
<body>
<div class="wrap">
  <h1>__TITLE__</h1>
  <p class="sub">__SUB__</p>
  <div class="stage" id="stage"><canvas id="c"></canvas></div>
  <div class="bar">
    <button id="play">Pause</button>
    <input type="range" id="scrub" min="0" step="0.01" value="0">
    <span class="t" id="tlabel">frame 0</span>
    <label><input type="checkbox" id="grain" checked> grain</label>
    <label><input type="checkbox" id="loopc" checked> loop</label>
  </div>
</div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const P=JSON.parse(document.getElementById('payload').textContent);
const META=P.meta, URIS=P.uris, K=META.K, COEF=META.coef, NF=COEF.length;
const FPS=META.fps||25;
document.getElementById('stage').style.aspectRatio=(META.srcW||16)+'/'+(META.srcH||9);
const cv=document.getElementById('c');
const gl=cv.getContext('webgl2',{antialias:false,alpha:false});
if(!gl){document.getElementById('stage').innerHTML='<div style="color:#fff;padding:14px;font:12px monospace">WebGL2 not supported.</div>';}

const VS=`#version 300 es
in vec2 p; out vec2 uv;
void main(){uv=p*0.5+0.5; uv.y=1.0-uv.y; gl_Position=vec4(p,0.,1.);}`;

const FS=`#version 300 es
precision highp float;
in vec2 uv; out vec4 o;
uniform sampler2D uMean;
uniform sampler2D uB[${K}];
uniform vec2 uMeanR;
uniform vec2 uBR[${K}];
uniform float uC[${K}];
uniform float uGrain,uTime,uGBase,uGSlope,uGCorr;
vec3 dec(vec3 t,vec2 r){return t*(r.y-r.x)+r.x;}
float hash(vec2 q){return fract(sin(dot(q,vec2(127.1,311.7)))*43758.5453);}
void main(){
  vec3 c=dec(texture(uMean,uv).rgb,uMeanR);
__ACCUM__
  c=clamp(c,0.0,1.0);
  if(uGrain>0.0){
    float luma=dot(c,vec3(0.2126,0.7152,0.0722));
    float amp=max(uGBase+uGSlope*luma,0.0)*uGrain;
    vec2 sd=gl_FragCoord.xy+vec2(uTime*61.0,uTime*37.0);
    float gC=hash(sd)-0.5;
    vec3 gI=vec3(hash(sd+11.3),hash(sd+27.7),hash(sd+43.1))-0.5;
    c+=amp*(sqrt(uGCorr)*gC+sqrt(1.0-uGCorr)*gI);
  }
  o=vec4(clamp(c,0.0,1.0),1.0);
}`;
function sh(t,s){const x=gl.createShader(t);gl.shaderSource(x,s);gl.compileShader(x);
 if(!gl.getShaderParameter(x,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(x));return x;}
const prog=gl.createProgram();
gl.attachShader(prog,sh(gl.VERTEX_SHADER,VS));
gl.attachShader(prog,sh(gl.FRAGMENT_SHADER,FS));
gl.linkProgram(prog);
if(!gl.getProgramParameter(prog,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(prog));
gl.useProgram(prog);
const vb=gl.createBuffer();
gl.bindBuffer(gl.ARRAY_BUFFER,vb);
gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
const pl=gl.getAttribLocation(prog,'p');
gl.enableVertexAttribArray(pl);gl.vertexAttribPointer(pl,2,gl.FLOAT,false,0,0);
function mkTex(img,unit){const t=gl.createTexture();
 gl.activeTexture(gl.TEXTURE0+unit);gl.bindTexture(gl.TEXTURE_2D,t);
 gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB8,gl.RGB,gl.UNSIGNED_BYTE,img);
 gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);
 gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
 gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
 gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);return t;}
function load(src){return new Promise((res,rej)=>{const im=new Image();
 im.onload=()=>res(im);im.onerror=rej;im.src=src;});}
const uC=gl.getUniformLocation(prog,'uC');
const uGrain=gl.getUniformLocation(prog,'uGrain');
const uTime=gl.getUniformLocation(prog,'uTime');
let ready=false;
(async function(){
  const names=['mean'].concat(Array.from({length:K},(_,i)=>'b'+i));
  const imgs=await Promise.all(names.map(n=>load(URIS[n])));
  imgs.forEach((im,i)=>mkTex(im,i));
  gl.uniform1i(gl.getUniformLocation(prog,'uMean'),0);
  gl.uniform2f(gl.getUniformLocation(prog,'uMeanR'),META.mean.lo,META.mean.hi);
  for(let i=0;i<K;i++){
    gl.uniform1i(gl.getUniformLocation(prog,`uB[${i}]`),i+1);
    gl.uniform2f(gl.getUniformLocation(prog,`uBR[${i}]`),META.layers[i].lo,META.layers[i].hi);
  }
  const g=META.grain||{base:0,slope:0,chanCorr:0.4};
  gl.uniform1f(gl.getUniformLocation(prog,'uGBase'),g.base);
  gl.uniform1f(gl.getUniformLocation(prog,'uGSlope'),g.slope);
  gl.uniform1f(gl.getUniformLocation(prog,'uGCorr'),g.chanCorr);
  ready=true;
})();
function coefAt(f){const a=Math.max(0,Math.min(NF-1,Math.floor(f)));
 const b=Math.min(NF-1,a+1),m=f-a,out=new Float32Array(K);
 for(let i=0;i<K;i++)out[i]=COEF[a][i]*(1-m)+COEF[b][i]*m;return out;}
const RM=matchMedia('(prefers-reduced-motion: reduce)').matches;
let playing=!RM,frame=RM?NF-1:0,last=performance.now();
const btn=document.getElementById('play'),scrub=document.getElementById('scrub'),
 tl=document.getElementById('tlabel'),gchk=document.getElementById('grain'),
 lchk=document.getElementById('loopc');
scrub.max=NF-1;
if(RM){playing=false;btn.textContent='Play';scrub.value=NF-1;}
btn.onclick=()=>{playing=!playing;btn.textContent=playing?'Pause':'Play';
 if(playing&&frame>=NF-1)frame=0;last=performance.now();};
scrub.oninput=()=>{playing=false;btn.textContent='Play';frame=parseFloat(scrub.value);};
function size(){const d=Math.min(devicePixelRatio||1,2);
 const w=Math.round(cv.clientWidth*d),h=Math.round(cv.clientHeight*d);
 if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;gl.viewport(0,0,w,h);}}
function tick(now){requestAnimationFrame(tick);
 if(!ready)return;
 const dt=(now-last)/1000;last=now;
 if(playing){frame+=dt*FPS;
  if(frame>=NF-1){if(lchk.checked)frame=0;else{frame=NF-1;playing=false;btn.textContent='Play';}}
  scrub.value=frame;}
 tl.textContent='frame '+Math.round(frame);
 size();
 gl.uniform1fv(uC,coefAt(frame));
 gl.uniform1f(uGrain,gchk.checked?1.0:0.0);
 gl.uniform1f(uTime,now*0.001);
 gl.drawArrays(gl.TRIANGLES,0,3);}
requestAnimationFrame(tick);
</script>
</body>
</html>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--out", default="page.html")
    ap.add_argument("--inline", action="store_true")
    ap.add_argument("--title", default="Shader reconstruction")
    a = ap.parse_args()

    meta = json.load(open(os.path.join(a.model, "model.json")))
    K = meta["K"]
    names = ["mean"] + [f"b{i}" for i in range(K)]

    outdir = os.path.dirname(os.path.abspath(a.out)) or "."
    os.makedirs(outdir, exist_ok=True)

    if a.inline:
        uris = {}
        for n in names:
            raw = open(os.path.join(a.model, n + ".webp"), "rb").read()
            uris[n] = "data:image/webp;base64," + base64.b64encode(raw).decode()
    else:
        uris = {}
        for n in names:
            shutil.copy(os.path.join(a.model, n + ".webp"),
                        os.path.join(outdir, n + ".webp"))
            uris[n] = n + ".webp"

    payload = json.dumps({"meta": meta, "uris": uris}, separators=(",", ":"))

    # emit exactly K accumulation lines
    lines = "".join(
        f"  c += uC[{i}] * dec(texture(uB[{i}],uv).rgb, uBR[{i}]);\n"
        for i in range(K))
    body = TEMPLATE.replace("__ACCUM__", lines.rstrip("\n"))

    sub = (f"{meta.get('frames','?')} frames rebuilt from {K+1} basis textures "
           f"({meta['texW']}&times;{meta['texH']}) plus animated coefficients. "
           f"Payload {len(payload)/1024:.0f}&nbsp;KB. Grain is procedural.")
    body = (body.replace("__PAYLOAD__", payload)
                .replace("__TITLE__", a.title)
                .replace("__SUB__", sub))
    open(a.out, "w").write(body)
    print(f"wrote {a.out} ({len(body)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
