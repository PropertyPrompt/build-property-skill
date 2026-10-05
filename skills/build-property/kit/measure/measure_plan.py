#!/usr/bin/env python3
"""Measure a raster floor plan into a DRAFT spec.json.

    python3 measure_plan.py plan.png --width-ft 36 --depth-ft 26 \
        [--crop x0,y0,x1,y1] [--threshold 60] [--open 13] \
        --out draft-spec.json [--debug-png overlay.png]

Pipeline (pure Pillow; numpy is not needed):
  1. grayscale, dark = value < --threshold.
  2. morphological opening (MinFilter(--open) then MaxFilter(--open)) removes
     everything thinner than the kernel: door arcs, furniture, text, window
     double lines. Thick walls survive.
  3. the building is the largest connected blob plus every blob near it; the
     footprint is its pixel bounding box. metres/px per axis = labelled
     length / pixel span (agreement between the axes is reported).
  4. horizontal wall pixels = row runs >= min-run px; vertical wall pixels =
     column runs >= min-run px (junctions belong to both). Connected pieces
     become rectangles; collinear rectangles merge into wall runs and the gaps
     between them become openings.
  5. every gap is classified from the ORIGINAL drawing (thin lines, not the
     opened mask): parallel lines across an outer-wall gap = window; a straight
     leaf line standing at one jamb = hinged door (hinge jamb + swing side);
     other marks close to an interior gap = bifold; nothing = plain opening.
     All of this is heuristic and lands in the "review" list.

Pixel convention: pixel p covers [p, p+1). A band of dark pixels t0..t1 has
edges t0 and t1+1. X = (px_edge - footprint.left) * mx, Z likewise.

Rooms, drawn items, hinge sides you disagree with, heights and room ids are
left for a human/assistant (placeholders + review entries).
"""
import argparse
import json
import math
import re
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FT = 0.3048
RUN = re.compile(rb'\xff+')


# ---------------------------------------------------------------- raster utils
def binarize(gray, thr):
    return gray.point(lambda v: 255 if v < thr else 0)


def opening(mask, k):
    if k <= 1:
        return mask
    if k % 2 == 0:
        k += 1
    return mask.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))


def line_runs(mask):
    """Row runs of a 0/255 'L' image: list per row of (start, end) inclusive."""
    w, h = mask.size
    data = mask.tobytes()
    out = []
    for y in range(h):
        row = data[y * w:(y + 1) * w]
        out.append([(m.start(), m.end() - 1) for m in RUN.finditer(row)])
    return out


def components(runs, min_len=1):
    """4-connected components of runs (line index, start, end). Returns lists of runs."""
    flat, idx_by_line = [], []
    for li, rs in enumerate(runs):
        ids = []
        for s, e in rs:
            if e - s + 1 >= min_len:
                ids.append(len(flat))
                flat.append((li, s, e))
        idx_by_line.append(ids)
    parent = list(range(len(flat)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for li in range(1, len(runs)):
        prev, cur = idx_by_line[li - 1], idx_by_line[li]
        j = 0
        for i in cur:
            _, s, e = flat[i]
            # two-pointer over sorted runs of the previous line
            while j < len(prev) and flat[prev[j]][2] < s:
                j += 1
            k = j
            while k < len(prev) and flat[prev[k]][1] <= e:
                a, b = find(i), find(prev[k])
                if a != b:
                    parent[a] = b
                k += 1
    groups = {}
    for i, r in enumerate(flat):
        groups.setdefault(find(i), []).append(r)
    return list(groups.values())


def comp_bbox(comp):
    lines = [r[0] for r in comp]
    return (min(r[1] for r in comp), min(lines), max(r[2] for r in comp), max(lines))


def comp_area(comp):
    return sum(e - s + 1 for _, s, e in comp)


def median(vals, weights=None):
    if not vals:
        return 0
    if weights is None:
        weights = [1] * len(vals)
    pairs = sorted(zip(vals, weights))
    half = sum(weights) / 2.0
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v
    return pairs[-1][0]


class Evidence:
    """Counts of 'ink' pixels (original drawing, looser threshold) in boxes."""

    def __init__(self, gray, thr):
        self.img = binarize(gray, thr)
        self.w, self.h = self.img.size

    def count(self, x0, y0, x1, y1):
        """Inclusive pixel box."""
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(self.w - 1, int(x1)), min(self.h - 1, int(y1))
        if x1 < x0 or y1 < y0:
            return 0, 1
        n = (x1 - x0 + 1) * (y1 - y0 + 1)
        return self.img.crop((x0, y0, x1 + 1, y1 + 1)).histogram()[255], n

    def frac(self, *box):
        c, n = self.count(*box)
        return c / n


# ---------------------------------------------------------------- wall bands
def band_rects(comp, axis, max_shift):
    """Split one component of long runs into straight rectangles.

    For axis 'x' (horizontal walls) a run is (row, x0, x1): along = x, thick = row.
    For axis 'z' (vertical walls) runs come from the transposed mask: along = y,
    thick = x. Returns dicts with a0, a1 (along, inclusive), t0, t1 (inclusive).
    """
    lo, hi = {}, {}
    for t, s, e in comp:
        for a in range(s, e + 1):
            if a not in lo or t < lo[a]:
                lo[a] = t
            if a not in hi or t > hi[a]:
                hi[a] = t
    rects, cur = [], None
    for a in sorted(lo):
        c = (lo[a] + hi[a]) / 2.0
        if cur and a == cur['a1'] + 1 and abs(c - cur['c0']) <= max_shift:
            cur['a1'] = a
            cur['ts'].append((lo[a], hi[a]))
        else:
            if cur:
                rects.append(cur)
            cur = {'a0': a, 'a1': a, 'c0': c, 'ts': [(lo[a], hi[a])]}
    if cur:
        rects.append(cur)
    out = []
    for r in rects:
        t0 = median([t[0] for t in r['ts']])
        t1 = median([t[1] for t in r['ts']])
        out.append({'axis': axis, 'a0': r['a0'], 'a1': r['a1'], 't0': t0, 't1': t1,
                    'len': r['a1'] - r['a0'] + 1, 'thick': t1 - t0 + 1})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plan')
    ap.add_argument('--width-ft', type=float, help='labelled outside width (plan x)')
    ap.add_argument('--depth-ft', type=float, help='labelled outside depth (plan y)')
    ap.add_argument('--width-m', type=float)
    ap.add_argument('--depth-m', type=float)
    ap.add_argument('--crop', help='x0,y0,x1,y1 region (original pixels) to analyse')
    ap.add_argument('--threshold', type=int, default=60, help='dark threshold for walls (0-255)')
    ap.add_argument('--evidence-threshold', type=int, default=160,
                    help='threshold for thin-line evidence in gaps (windows, leaves, bifolds)')
    ap.add_argument('--open', type=int, default=13, help='opening kernel (px, odd)')
    ap.add_argument('--min-run', type=int, default=0,
                    help='minimum run length for a wall pixel (default: 1.4 x thickest wall)')
    ap.add_argument('--min-opening-m', type=float, default=0.30,
                    help='gaps narrower than this are treated as joints, not openings')
    ap.add_argument('--max-interior-opening-m', type=float, default=1.6,
                    help='interior gaps wider than this split the run into two walls')
    ap.add_argument('--ceiling', type=float, default=2.44)
    ap.add_argument('--door-head', type=float, default=2.032)
    ap.add_argument('--window-sill', type=float, default=0.914)
    ap.add_argument('--window-head', type=float, default=2.032)
    ap.add_argument('--property-id', default='TODO-property-id')
    ap.add_argument('--source-file', help='path recorded in source.file (default: the plan path)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--debug-png')
    a = ap.parse_args(argv)

    width_m = a.width_m or (a.width_ft * FT if a.width_ft else None)
    depth_m = a.depth_m or (a.depth_ft * FT if a.depth_ft else None)
    if not width_m and not depth_m:
        ap.error('give --width-ft/--width-m and/or --depth-ft/--depth-m (a labelled outside dimension)')

    src = Image.open(a.plan)
    full_w, full_h = src.size
    gray_full = src.convert('L')
    ox = oy = 0
    gray = gray_full
    if a.crop:
        x0, y0, x1, y1 = [int(v) for v in a.crop.split(',')]
        gray = gray_full.crop((x0, y0, x1, y1))
        ox, oy = x0, y0

    review = []
    dark = binarize(gray, a.threshold)
    walls_mask = opening(dark, a.open)
    rows = line_runs(walls_mask)
    cols = line_runs(walls_mask.transpose(Image.Transpose.TRANSPOSE))

    # ---- building blob and footprint
    blobs = components(rows)
    blobs = [b for b in blobs if comp_area(b) >= a.open * a.open * 2]
    if not blobs:
        sys.exit('no walls survived the opening; lower --open or raise --threshold')
    blobs.sort(key=comp_area, reverse=True)
    bx0, by0, bx1, by1 = comp_bbox(blobs[0])
    # Blobs join the building while they lie within 35 % of its current size
    # (outer walls are cut into separate blobs by wide windows and doors).
    changed = True
    used = {0}
    while changed:
        changed = False
        for i, b in enumerate(blobs):
            if i in used:
                continue
            x0, y0, x1, y1 = comp_bbox(b)
            margin = 0.35 * max(bx1 - bx0, by1 - by0)
            if x1 >= bx0 - margin and x0 <= bx1 + margin and y1 >= by0 - margin and y0 <= by1 + margin:
                used.add(i)
                bx0, by0, bx1, by1 = min(bx0, x0), min(by0, y0), max(bx1, x1), max(by1, y1)
                changed = True
    ignored = [comp_bbox(b) for i, b in enumerate(blobs) if i not in used]
    if ignored:
        review.append('Ignored %d dark blob(s) away from the building (title, legend?): %s' % (
            len(ignored), [[x0 + ox, y0 + oy, x1 + ox, y1 + oy] for x0, y0, x1, y1 in ignored[:6]]))
    L, T, R, B = bx0, by0, bx1, by1          # inclusive pixel bounds (crop coords)
    wpx, hpx = R - L + 1, B - T + 1
    mx = width_m / wpx if width_m else None
    mz = depth_m / hpx if depth_m else None
    if mx is None:
        mx = mz
        width_m = wpx * mx
        review.append('Width not given: x scale copied from z; footprint width %.3f m is derived.' % width_m)
    if mz is None:
        mz = mx
        depth_m = hpx * mz
        review.append('Depth not given: z scale copied from x; footprint depth %.3f m is derived.' % depth_m)
    agree = abs(mx - mz) / ((mx + mz) / 2)
    scale_note = 'x %.6f m/px, z %.6f m/px, differ by %.2f%%' % (mx, mz, agree * 100)
    if agree > 0.02:
        review.append('Scale disagreement between axes > 2%% (%s): check the labelled dimensions, or the '
                      'plan is stretched; cross-check with a labelled room.' % scale_note)

    def X(px_edge):
        return round((px_edge - L) * mx, 3)

    def Z(py_edge):
        return round((py_edge - T) * mz, 3)

    # ---- wall rectangles
    shorts = sorted(e - s + 1 for rs in rows for s, e in rs if a.open <= e - s + 1 <= 80)
    t_max = shorts[int(len(shorts) * 0.9)] if shorts else 30
    min_run = a.min_run or int(round(1.4 * t_max))
    hcomps = components(rows, min_run)
    vcomps = components(cols, min_run)
    rects = []
    for c in hcomps:
        rects += band_rects(c, 'x', max(3, t_max * 0.35))
    for c in vcomps:
        rects += band_rects(c, 'z', max(3, t_max * 0.35))
    rects = [r for r in rects if r['len'] >= min_run * 0.6 or r['thick'] >= a.open]
    tol = 2

    def is_outer(r):
        if r['axis'] == 'x':
            return 'north' if r['t0'] <= T + tol else ('south' if r['t1'] >= B - tol else None)
        return 'west' if r['t0'] <= L + tol else ('east' if r['t1'] >= R - tol else None)

    outer_rects = {'north': [], 'south': [], 'west': [], 'east': []}
    inner_rects = []
    for r in rects:
        side = is_outer(r)
        if side:
            outer_rects[side].append(r)
        else:
            inner_rects.append(r)
    ot_vals, ot_w = [], []
    for side, rs in outer_rects.items():
        for r in rs:
            ot_vals.append(r['thick'])
            ot_w.append(r['len'])
    ot_px = median(ot_vals, ot_w) if ot_vals else t_max
    OTx, OTz = round(ot_px * mx, 3), round(ot_px * mz, 3)
    OT = round((OTx + OTz) / 2, 3)

    min_gap_px = {'x': a.min_opening_m / mx, 'z': a.min_opening_m / mz}
    max_gap_px = {'x': a.max_interior_opening_m / mx, 'z': a.max_interior_opening_m / mz}

    # ---- group collinear rectangles into runs with gaps
    def group_runs(rs, interior):
        rs = sorted(rs, key=lambda r: ((r['t0'] + r['t1']) / 2.0, r['a0']))
        lines = []
        for r in rs:
            c = (r['t0'] + r['t1']) / 2.0
            for ln in lines:
                if ln['axis'] == r['axis'] and abs(ln['c'] - c) <= max(3, 0.35 * r['thick']) \
                        and abs(ln['thick'] - r['thick']) <= max(4, 0.3 * r['thick']):
                    ln['rects'].append(r)
                    break
            else:
                lines.append({'axis': r['axis'], 'c': c, 'thick': r['thick'], 'rects': [r]})
        runs = []
        for ln in lines:
            pieces = sorted(ln['rects'], key=lambda r: r['a0'])
            cur = None
            for r in pieces:
                if cur is None:
                    cur = {'axis': ln['axis'], 'pieces': [r], 'gaps': []}
                    continue
                last = cur['pieces'][-1]
                gap = r['a0'] - last['a1'] - 1
                if gap <= 0 or gap < min_gap_px[ln['axis']]:
                    if gap > 2:
                        review.append('Short gap of %d px ignored in a %s-axis wall at px %d (joint, not an '
                                      'opening?).' % (gap, ln['axis'], last['a1'] + 1 + (ox if ln['axis'] == 'x' else oy)))
                    last['a1'] = max(last['a1'], r['a1'])
                    last['len'] = last['a1'] - last['a0'] + 1
                elif interior and gap > max_gap_px[ln['axis']]:
                    runs.append(cur)
                    cur = {'axis': ln['axis'], 'pieces': [r], 'gaps': []}
                else:
                    cur['gaps'].append((last['a1'] + 1, r['a0']))   # edge coords [g0, g1)
                    cur['pieces'].append(r)
            runs.append(cur)
        for run in runs:
            ps = run['pieces']
            run['a0'] = min(p['a0'] for p in ps)
            run['a1'] = max(p['a1'] for p in ps)
            run['t0'] = median([p['t0'] for p in ps], [p['len'] for p in ps])
            run['t1'] = median([p['t1'] for p in ps], [p['len'] for p in ps])
        return runs

    ev = Evidence(gray, a.evidence_threshold)

    # ---- gap classification
    def classify(axis, t0, t1, g0, g1, outer_side=None):
        """g0, g1: gap edge coords along the wall (pixels g0..g1-1 are open).
        t0..t1: wall band (inclusive). Returns dict with type, hinge, swingSide, evidence."""
        glen = g1 - g0

        def box(a0, a1, s0, s1):  # along/thick inclusive -> x0,y0,x1,y1
            return (a0, s0, a1, s1) if axis == 'x' else (s0, a0, s1, a1)

        # 1. continuous lines across the gap inside the band (window glazing lines)
        line_rows = []
        for t in range(t0, t1 + 1):
            if ev.frac(*box(g0 + 2, g1 - 3, t, t)) >= 0.8:
                line_rows.append(t)
        groups = 0
        prev = None
        for t in line_rows:
            if prev is None or t > prev + 1:
                groups += 1
            prev = t
        # 2. a straight leaf at one jamb, on either side of the wall
        depth = int(glen * 0.75)
        best = (0.0, None, None)
        for side, (s0, s1) in (('-', (t0 - depth, t0 - 3)), ('+', (t1 + 3, t1 + depth))):
            for jamb, a_rng in (('from', range(g0 - 8, g0 + 12)), ('to', range(g1 - 12, g1 + 8))):
                for av in a_rng:
                    f = ev.frac(*box(av, av + 2, s0, s1))
                    if f > best[0]:
                        best = (f, side, jamb)
        # 3. other marks close to the gap (bifold zig-zags, swing arcs)
        near = int(min(glen, 0.45 / (mx if axis == 'x' else mz)))
        dens = {}
        for side, (s0, s1) in (('-', (t0 - near, t0 - 3)), ('+', (t1 + 3, t1 + near))):
            dens[side] = ev.frac(*box(g0 + 3, g1 - 4, s0, s1))
        res = {'lines': groups, 'leaf': round(best[0], 2), 'marks': {k: round(v, 3) for k, v in dens.items()}}
        if outer_side and groups >= 2:
            res['type'] = 'window'
        elif best[0] >= 0.65:
            res.update(type='door', hinge=best[2], side=best[1])
        elif not outer_side and max(dens.values()) > 0.01:
            res.update(type='bifold', side=max(dens, key=dens.get))
        elif outer_side and groups == 1:
            res['type'] = 'door'
        else:
            res['type'] = 'opening'
        return res

    compass = {'x': {'from': 'west', 'to': 'east', '-': 'north', '+': 'south'},
               'z': {'from': 'north', 'to': 'south', '-': 'west', '+': 'east'}}
    side_axis = {'x': {'-': '-z', '+': '+z'}, 'z': {'-': '-x', '+': '+x'}}

    def opening_entry(oid, axis, g0, g1, cls, outer_side, conv):
        o = {'id': oid, 'type': cls['type'], 'from': conv(g0), 'to': conv(g1)}
        if cls['type'] == 'window':
            o.update(sill=a.window_sill, head=a.window_head)
        else:
            o['head'] = a.door_head
        o['room'] = None
        if cls['type'] == 'door' and cls.get('hinge'):
            jamb = compass[axis][cls['hinge']]
            if outer_side:
                inward = (cls['side'] == '+') == (outer_side in ('north', 'west'))
                o['swing'] = '%s, hinge on %s jamb' % ('inward' if inward else 'outward', jamb)
                o['swingSide'] = 'interior' if inward else 'exterior'
            else:
                o['swing'] = 'into the room to the %s, hinge on %s jamb' % (compass[axis][cls['side']], jamb)
                o['swingSide'] = side_axis[axis][cls['side']]
            o['hinge'] = cls['hinge']
        elif cls['type'] == 'bifold':
            o['note'] = 'bifold doors opening to the %s' % compass[axis][cls['side']]
            o['swingSide'] = side_axis[axis][cls['side']]
        o['detected'] = {k: v for k, v in cls.items() if k in ('lines', 'leaf', 'marks')}
        o['needsReview'] = True
        return o

    # ---- outer walls
    spec_outer = []
    oid_n = {}
    for side in ('north', 'south', 'west', 'east'):
        rs = outer_rects[side]
        axis = 'x' if side in ('north', 'south') else 'z'
        conv = (lambda e: X(e)) if axis == 'x' else (lambda e: Z(e))
        if not rs:
            review.append('No %s outer wall detected.' % side)
            continue
        runs = group_runs(rs, False)
        runs.sort(key=lambda r: -(r['a1'] - r['a0']))
        run = runs[0]
        for extra in runs[1:]:
            review.append('Extra %s-side outer band at along px %d-%d (step in the facade?)' % (
                side, extra['a0'], extra['a1']))
        w = {'id': side, 'axis': axis}
        if side == 'north':
            w.update(line_z_outer=0.0, inner_z=OTz)
        elif side == 'south':
            w.update(line_z_outer=round(depth_m, 3), inner_z=round(depth_m - OTz, 3))
        elif side == 'west':
            w.update(line_x_outer=0.0, inner_x=OTx)
        else:
            w.update(line_x_outer=round(width_m, 3), inner_x=round(width_m - OTx, 3))
        w.update({'from': 0.0, 'to': round(width_m if axis == 'x' else depth_m, 3), 'openings': []})
        lo_edge, hi_edge = (L, R + 1) if axis == 'x' else (T, B + 1)
        if run['a0'] - lo_edge > 2 or hi_edge - (run['a1'] + 1) > 2:
            review.append('%s outer wall ends short of the footprint corner (%d / %d px): open corner?' % (
                side, run['a0'] - lo_edge, hi_edge - run['a1'] - 1))
        for g0, g1 in run['gaps']:
            cls = classify(axis, run['t0'], run['t1'], g0, g1, side)
            kind = {'window': 'win', 'door': 'door', 'opening': 'open'}.get(cls['type'], cls['type'])
            oid_n[(kind, side)] = oid_n.get((kind, side), 0) + 1
            oid = '%s-%s-%d' % (kind, side, oid_n[(kind, side)])
            o = opening_entry(oid, axis, g0, g1, cls, side, conv)
            if cls['type'] == 'opening':
                review.append('%s: gap with no window lines or door leaf: open archway, or missing ink?' % oid)
            elif cls['type'] == 'door' and not cls.get('hinge'):
                review.append('%s: door without a clear leaf line: set hinge and swing by hand.' % oid)
            w['openings'].append(o)
        w['pixels'] = {'band': [run['t0'] + (oy if axis == 'x' else ox), run['t1'] + (oy if axis == 'x' else ox)]}
        spec_outer.append(w)

    # ---- interior walls
    inner_runs = group_runs(inner_rects, True)
    perp_bands = []   # (axis, t0, t1, a0, a1, outer?)
    for side, rs in outer_rects.items():
        for r in rs:
            perp_bands.append((r['axis'], r['t0'], r['t1'], r['a0'], r['a1'], side))
    for r in inner_runs:
        perp_bands.append((r['axis'], r['t0'], r['t1'], r['a0'], r['a1'], None))
    outer_face = {'north': T + ot_px, 'south': B + 1 - ot_px, 'west': L + ot_px, 'east': R + 1 - ot_px}

    def snap_end(run, end):
        """Return (edge coord, what) for one end: the near face of the wall it meets, or its own edge."""
        v = run['a0'] if end == 'lo' else run['a1']
        c = (run['t0'] + run['t1']) / 2.0
        best = None
        for axis, t0, t1, a0, a1, side in perp_bands:
            if axis == run['axis']:
                continue
            if not (t0 - tol <= v <= t1 + tol and a0 - tol <= c <= a1 + tol):
                continue
            if side:
                face = outer_face[side]
            else:
                face = (t1 + 1) if end == 'lo' else t0
            if best is None or (side and not best[2]):
                best = (face, 'outer:' + side if side else 'interior', side)
        if best:
            return best[0], best[1]
        return (v if end == 'lo' else v + 1), 'free'

    spec_inner = []
    inner_runs.sort(key=lambda r: (r['axis'], (r['t0'] + r['t1']) / 2.0, r['a0']))
    for i, run in enumerate(inner_runs, 1):
        axis = run['axis']
        wid = 'iw-%d' % i
        lo, lo_what = snap_end(run, 'lo')
        hi, hi_what = snap_end(run, 'hi')
        tpx = run['t1'] - run['t0'] + 1
        if axis == 'x':
            cz = Z((run['t0'] + run['t1'] + 1) / 2.0)
            w = {'id': wid, 'axis': 'x', 'center_z': cz, 'from': X(lo), 'to': X(hi),
                 'thickness': round(tpx * mz, 3)}
            conv = X
        else:
            cx = X((run['t0'] + run['t1'] + 1) / 2.0)
            w = {'id': wid, 'axis': 'z', 'center_x': cx, 'from': Z(lo), 'to': Z(hi),
                 'thickness': round(tpx * mx, 3)}
            conv = Z
        w['ends'] = [lo_what, hi_what]
        w['openings'] = []
        n = {}
        for g0, g1 in run['gaps']:
            cls = classify(axis, run['t0'], run['t1'], g0, g1, None)
            kind = cls['type']
            n[kind] = n.get(kind, 0) + 1
            oid = '%s-%s-%d' % (kind, wid, n[kind])
            o = opening_entry(oid, axis, g0, g1, cls, None, conv)
            if kind == 'opening':
                review.append('%s: interior gap without a leaf or bifold marks: cased opening, sliding or '
                              'pocket door? check the drawing.' % oid)
            w['openings'].append(o)
        if 'free' in w['ends']:
            review.append('%s has a free end (%s): stub, or a junction the opening removed?' % (wid, w['ends']))
        w['pixels'] = {'band': [run['t0'] + (oy if axis == 'x' else ox), run['t1'] + (oy if axis == 'x' else ox)],
                       'along': [run['a0'] + (ox if axis == 'x' else oy), run['a1'] + (ox if axis == 'x' else oy)]}
        spec_inner.append(w)

    it_vals = [w['thickness'] for w in spec_inner]
    IT = round(median(it_vals), 3) if it_vals else OT
    review += [
        'Hinged doors: confirm hinge jamb and swing side against the drawing (leaf line = hinge side). '
        'For each interior door set "room" (the room it swings into).',
        'Bifolds: confirm the side they open to and which room they serve.',
        'Fill rooms (id, name, bounds [x1,z1,x2,z2] in metres, label) from the plan labels; cross-check one '
        'labelled interior dimension against the measured walls.',
        'Heights are defaults (ceiling %.2f, door head %.3f, window sill %.3f / head %.3f): set real values, '
        'e.g. a higher sill over a sink.' % (a.ceiling, a.door_head, a.window_sill, a.window_head),
        'Rename wall/opening ids (iw-N, win-north-1 ...) to meaningful ids before building.',
    ]

    spec = {
        'propertyId': a.property_id,
        'draft': True,
        'source': {
            'file': a.source_file or a.plan,
            'resolution': [full_w, full_h],
            'crop': [ox, oy, ox + gray.size[0], oy + gray.size[1]] if a.crop else None,
            'footprintPx': {'left': L + ox, 'top': T + oy, 'right': R + ox, 'bottom': B + oy,
                            'widthPx': wpx, 'heightPx': hpx},
            'metresPerPixel': {'x': round(mx, 6), 'z': round(mz, 6)},
            'scaleAgreement': scale_note,
            'conversion': 'X=(px-%d)*%.6f, Z=(py-%d)*%.6f (original-resolution pixel edges)' % (L + ox, mx, T + oy, mz),
            'measuredWith': 'measure_plan.py threshold=%d open=%d minRun=%d evidenceThreshold=%d' % (
                a.threshold, a.open, min_run, a.evidence_threshold),
        },
        'axes': 'origin = outside north-west corner; +x east (plan right), +z south (plan down), y up; metres',
        'footprint': [round(width_m, 3), round(depth_m, 3)],
        'ceilingHeight': a.ceiling,
        'wallThickness': {'outer': OT, 'interior': IT,
                          'note': 'Drawn thicknesses (outer %d px, interior median %.0f px).' % (
                              ot_px, IT / ((mx + mz) / 2))},
        'heights': {'door': a.door_head, 'windowSill': a.window_sill, 'windowHead': a.window_head, 'floorTop': 0.0},
        'outerWalls': spec_outer,
        'interiorWalls': spec_inner,
        'rooms': [],
        'drawnItems': {},
        'review': review,
    }
    with open(a.out, 'w') as f:
        json.dump(spec, f, indent=1)

    if a.debug_png:
        draw_debug(a.debug_png, gray, spec, L, T, mx, mz, ox, oy)
    n_open = sum(len(w['openings']) for w in spec_outer + spec_inner)
    print('footprint px %d x %d at (%d,%d); %s' % (wpx, hpx, L + ox, T + oy, scale_note))
    print('outer thickness %.3f m, interior %.3f m; %d outer walls, %d interior walls, %d openings; '
          '%d review items -> %s' % (OT, IT, len(spec_outer), len(spec_inner), n_open, len(review), a.out))


def draw_debug(path, gray, spec, L, T, mx, mz, ox, oy):
    img = gray.convert('RGB').point(lambda v: 150 + v * 105 // 255)
    d = ImageDraw.Draw(img, 'RGBA')
    try:
        font = ImageFont.load_default(size=max(12, int(0.16 / mx)))
    except TypeError:
        font = ImageFont.load_default()

    def px(xm):
        return L + xm / mx

    def pz(zm):
        return T + zm / mz

    colours = {'window': (0, 170, 220, 200), 'door': (230, 40, 40, 200), 'bifold': (200, 0, 200, 200),
               'opening': (255, 150, 0, 200)}
    fw, fd = spec['footprint']
    d.rectangle([px(0), pz(0), px(fw), pz(fd)], outline=(0, 0, 255, 255), width=2)
    for w in spec['outerWalls']:
        if w['axis'] == 'x':
            z0, z1 = sorted([w['line_z_outer'], w['inner_z']])
            d.rectangle([px(w['from']), pz(z0), px(w['to']), pz(z1)], fill=(0, 0, 255, 70))
            for o in w['openings']:
                d.rectangle([px(o['from']), pz(z0) - 4, px(o['to']), pz(z1) + 4], fill=colours[o['type']])
                d.text((px(o['from']), pz(z1) + 6 if w['id'] == 'north' else pz(z0) - 26), o['id'],
                       fill=(0, 0, 0, 255), font=font)
        else:
            x0, x1 = sorted([w['line_x_outer'], w['inner_x']])
            d.rectangle([px(x0), pz(w['from']), px(x1), pz(w['to'])], fill=(0, 0, 255, 70))
            for o in w['openings']:
                d.rectangle([px(x0) - 4, pz(o['from']), px(x1) + 4, pz(o['to'])], fill=colours[o['type']])
                d.text((px(x1) + 6 if w['id'] == 'west' else px(x0) - 160, pz(o['from'])), o['id'],
                       fill=(0, 0, 0, 255), font=font)
    for w in spec['interiorWalls']:
        h = w['thickness'] / 2
        if w['axis'] == 'x':
            box = [px(w['from']), pz(w['center_z'] - h), px(w['to']), pz(w['center_z'] + h)]
            lab = (px((w['from'] + w['to']) / 2), pz(w['center_z'] + h) + 2)
        else:
            box = [px(w['center_x'] - h), pz(w['from']), px(w['center_x'] + h), pz(w['to'])]
            lab = (px(w['center_x'] + h) + 3, pz((w['from'] + w['to']) / 2))
        d.rectangle(box, fill=(0, 160, 0, 110), outline=(0, 110, 0, 255))
        d.text(lab, w['id'], fill=(0, 100, 0, 255), font=font)
        for o in w['openings']:
            if w['axis'] == 'x':
                ob = [px(o['from']), pz(w['center_z'] - h) - 4, px(o['to']), pz(w['center_z'] + h) + 4]
            else:
                ob = [px(w['center_x'] - h) - 4, pz(o['from']), px(w['center_x'] + h) + 4, pz(o['to'])]
            d.rectangle(ob, fill=colours[o['type']])
            d.text((ob[0], ob[3] + 2), o['id'], fill=(120, 0, 0, 255), font=font)
    img.save(path)


if __name__ == '__main__':
    main()
