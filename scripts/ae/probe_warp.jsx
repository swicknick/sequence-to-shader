// sequence-to-shader live path: measure an After Effects distortion with coordinate maps.
//
// Copies the chosen effects (with their current values) from a layer onto a coordinate map the same size as that
// layer, and renders it with a margin around it (warps often push content outside the layer's own rectangle):
//   <OUT>/coarse/coarse_#####.png   red = source x / layer width, green = source y / layer height (~4-7 px precision)
//   <OUT>/fine/k_#####.png          FINE_STRIPES maps, each ramping across 1/N of the layer (~0.5-1 px precision)
// Decode with scripts/live/coordmap.py (static warps) or compare with scripts/live/aewarp.py (Wave Warp).
//
// Edit the CONFIG block, then run it in AE (File > Scripts > Run Script File, or osascript DoScriptFile).
// Needs an Output Module template named "PNG Sequence". Scripts can't select the PNG format themselves; set one
// render-queue item's Output Module Format to "PNG Sequence" by hand once, then:
//   app.project.renderQueue.item(1).outputModule(1).saveAsTemplate('PNG Sequence');
// Run this inside a project you've saved: outputs go next to the .aep.

// ---------------- CONFIG ----------------
var COMP = 'My Comp';               // comp containing the layer
var LAYER = 'Star';                 // layer whose effects to measure
var EFFECTS = ['ADBE Wave Warp'];   // matchNames to copy, in order (e.g. 'ADBE WRPMESH' = Warp/Arc, 'ADBE Bulge')
var FRAMES = 12;                    // animated effects: frames to render (static warps: 1)
var MARGIN = 1500;                  // px around the layer (static warps can reach far: check the coarse map's coverage)
var FINE_STRIPES = 8;               // 0 = coarse map only
var OUT = 'probe-warp';             // folder next to the .aep
// ----------------------------------------

var LOG = new File(app.project.file.parent.fsName + '/' + OUT + '_log.txt'); LOG.open('w');
function L(s) { LOG.writeln(s); }
function findItem(name, type) {
    for (var i = 1; i <= app.project.numItems; i++) { var it = app.project.item(i); if (it.name == name && it instanceof type) return it; }
    return null;
}
function copyEffects(fromLayer, toLayer) {
    var src = fromLayer.property('ADBE Effect Parade'), dst = toLayer.property('ADBE Effect Parade');
    for (var e = 0; e < EFFECTS.length; e++) {
        for (var i = 1; i <= src.numProperties; i++) {
            var fx = src.property(i); if (fx.matchName != EFFECTS[e]) continue;
            var c = dst.addProperty(fx.matchName);
            for (var p = 1; p <= fx.numProperties; p++) {
                var sp = fx.property(p);
                try { if (sp.propertyValueType != PropertyValueType.NO_VALUE && sp.propertyValueType != PropertyValueType.CUSTOM_VALUE) c.property(p).setValue(sp.value); } catch (err) {}
            }
        }
    }
}
function ramp(comp, name, w, h, horiz, x0, x1, color) {
    var l = comp.layers.addSolid([0, 0, 0], name, w, h, 1); l.source.parentFolder = folder;
    var g = l.property('ADBE Effect Parade').addProperty('ADBE Ramp');
    g.property('Start of Ramp').setValue(horiz ? [x0, h / 2] : [w / 2, x0]); g.property('Start Color').setValue([0, 0, 0]);
    g.property('End of Ramp').setValue(horiz ? [x1, h / 2] : [w / 2, x1]); g.property('End Color').setValue(color);
    return l;
}
function mapComp(name, w, h, k, n) {            // k < 0: coarse (whole layer); else stripe k of n
    var m = app.project.items.addComp(name, w, h, 1, FRAMES / 24, 24); m.parentFolder = folder;
    var x0 = k < 0 ? 0 : k * w / n, x1 = k < 0 ? w : (k + 1) * w / n, y0 = k < 0 ? 0 : k * h / n, y1 = k < 0 ? h : (k + 1) * h / n;
    ramp(m, 'X', w, h, true, x0, x1, [1, 0, 0]);
    ramp(m, 'Y', w, h, false, y0, y1, [0, 1, 0]).blendingMode = BlendingMode.ADD;
    return m;
}
var rq = app.project.renderQueue, mine = [];
try {
    // keep all probe comps and solids in one folder, cleared on every run
    var folder = findItem('sequence-to-shader probes', FolderItem);
    if (folder) { while (folder.numItems > 0) folder.item(1).remove(); } else folder = app.project.items.addFolder('sequence-to-shader probes');
    var comp = findItem(COMP, CompItem); if (!comp) throw new Error('comp not found: ' + COMP);
    var layer = comp.layer(LAYER); if (!layer) throw new Error('layer not found: ' + LAYER);
    var w = layer.source ? layer.source.width : comp.width, h = layer.source ? layer.source.height : comp.height;
    if (layer instanceof ShapeLayer || layer instanceof TextLayer) { w = comp.width; h = comp.height; }   // continuously rasterized
    L('layer size ' + w + 'x' + h + ', margin ' + MARGIN);
    var jobs = [['coarse', -1]];
    for (var k = 0; k < FINE_STRIPES; k++) jobs.push([String(k), k]);
    for (var j = 0; j < jobs.length; j++) {
        var m = mapComp('probe map ' + jobs[j][0], w, h, jobs[j][1], FINE_STRIPES);
        var c = app.project.items.addComp('probe ' + jobs[j][0], w + 2 * MARGIN, h + 2 * MARGIN, 1, FRAMES / 24, 24); c.parentFolder = folder;
        var ml = c.layers.add(m); copyEffects(layer, ml);
        var dir = new Folder(app.project.file.parent.fsName + '/' + OUT + '/' + (jobs[j][1] < 0 ? 'coarse' : 'fine'));
        if (!dir.exists) dir.create();
        var item = rq.items.add(c); mine.push(item);
        item.outputModule(1).applyTemplate('PNG Sequence');
        item.outputModule(1).file = new File(dir.fsName + '/' + jobs[j][0] + '_[#####].png');
    }
    rq.render();
    for (var i = 0; i < mine.length; i++) L(mine[i].comp.name + ': ' + (mine[i].status == RQItemStatus.DONE ? 'DONE' : mine[i].status));
    for (var i = mine.length - 1; i >= 0; i--) if (mine[i].status == RQItemStatus.DONE) mine[i].remove();
} catch (e) { L('ERR ' + e.message + ' (line ' + e.line + ')'); }
L('FINISHED'); LOG.close();
