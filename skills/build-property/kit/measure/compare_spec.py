#!/usr/bin/env python3
"""Compare a measured draft spec with a reference spec (e.g. a hand-corrected spec.json).

    python3 compare_spec.py draft-spec.json spec.json [--open-tol 0.05] [--wall-tol 0.03]

Outer walls are matched by id (north/south/west/east), openings by overlap,
interior walls by axis + nearest centreline with overlapping extents. Prints
markdown tables; exit status 1 when anything is outside tolerance.
Wall ends are compared after snapping both to the face of the wall they meet
(a reference that stops at a crossing wall's centreline differs by half a
thickness otherwise); raw differences are shown too.
"""
import argparse
import json
import sys


def overlap(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('draft')
    ap.add_argument('ref')
    ap.add_argument('--open-tol', type=float, default=0.05)
    ap.add_argument('--wall-tol', type=float, default=0.03)
    a = ap.parse_args()
    d, r = json.load(open(a.draft)), json.load(open(a.ref))
    bad = 0

    def mark(v, tol):
        nonlocal bad
        if v is None or abs(v) > tol:
            bad += 1
            return 'FAIL'
        return 'ok'

    print('| quantity | draft | reference | diff |')
    print('|---|---|---|---|')
    for k in ('outer', 'interior'):
        dv, rv = d['wallThickness'][k], r['wallThickness'][k]
        print('| %s wall thickness | %.3f | %.3f | %+.3f |' % (k, dv, rv, dv - rv))
    for k in ('x', 'z'):
        dv, rv = d['source']['metresPerPixel'][k], r['source']['metresPerPixel'][k]
        print('| m/px %s | %.6f | %.6f | %+.6f |' % (k, dv, rv, dv - rv))
    print()

    # ---- openings
    rows = []
    allr = [(w, o) for w in r['outerWalls'] + r['interiorWalls'] for o in w['openings']]
    used = set()

    def ref_wall_key(w):
        return w['id'] if 'line_z_outer' in w or 'line_x_outer' in w else None

    dwalls = {w['id']: w for w in d['outerWalls']}
    # map interior walls first (needed for openings)
    imap = {}
    for dw in d['interiorWalls']:
        ck = 'center_z' if dw['axis'] == 'x' else 'center_x'
        best = None
        for rw in r['interiorWalls']:
            if rw['axis'] != dw['axis']:
                continue
            ov = overlap(dw['from'], dw['to'], rw['from'], rw['to'])
            if ov <= 0:
                continue
            dc = abs(dw[ck] - rw[ck])
            if dc < 0.15 and (best is None or dc < best[0]):
                best = (dc, rw)
        imap[dw['id']] = best[1] if best else None

    print('| opening (draft) | reference | type d/r | from d/r | to d/r | d from | d to | hinge d/r | |')
    print('|---|---|---|---|---|---|---|---|---|')
    for dw in d['outerWalls'] + d['interiorWalls']:
        rw_match = None
        if dw['id'] in ('north', 'south', 'west', 'east'):
            rw_match = next((w for w in r['outerWalls'] if w['id'] == dw['id']), None)
        else:
            rw_match = imap.get(dw['id'])
        for o in dw['openings']:
            cand = None
            if rw_match:
                for ro in rw_match['openings']:
                    if overlap(o['from'], o['to'], ro['from'], ro['to']) > 0:
                        cand = ro
            if not cand:
                bad += 1
                print('| %s | (none) | %s | %.3f | %.3f | | | | FAIL |' % (o['id'], o['type'], o['from'], o['to']))
                continue
            used.add(cand['id'])
            df, dt = o['from'] - cand['from'], o['to'] - cand['to']
            hinge = '%s/%s' % (o.get('swing', '').split('hinge on ')[-1].replace(' jamb', '') if o.get('hinge') else '-',
                               _hinge_word(cand))
            st = 'ok' if abs(df) <= a.open_tol and abs(dt) <= a.open_tol and o['type'] == cand['type'] else 'FAIL'
            if st == 'FAIL':
                bad += 1
            print('| %s | %s | %s/%s | %.3f/%.3f | %.3f/%.3f | %+.3f | %+.3f | %s | %s |' % (
                o['id'], cand['id'], o['type'], cand['type'], o['from'], cand['from'], o['to'], cand['to'],
                df, dt, hinge, st))
    for w, o in allr:
        if o['id'] not in used:
            bad += 1
            print('| (missed) | %s | -/%s | | %.3f | %.3f | | | | FAIL |' % (o['id'], o['type'], o['from'], o['to']))
    print()

    # ---- walls
    print('| wall (draft) | reference | centre d/r | d centre | from d/r (snapped) | to d/r (snapped) | d from | d to | |')
    print('|---|---|---|---|---|---|---|---|---|')
    for dw in d['outerWalls']:
        rw = next((w for w in r['outerWalls'] if w['id'] == dw['id']), None)
        k = 'inner_z' if dw['axis'] == 'x' else 'inner_x'
        dc = dw[k] - rw[k]
        st = mark(dc, a.wall_tol)
        print('| %s (inner face) | %s | %.3f/%.3f | %+.3f | | | | | %s |' % (dw['id'], rw['id'], dw[k], rw[k], dc, st))
    for dw in d['interiorWalls']:
        rw = imap.get(dw['id'])
        if not rw:
            bad += 1
            print('| %s | (none) | | | | | | | FAIL |' % dw['id'])
            continue
        ck = 'center_z' if dw['axis'] == 'x' else 'center_x'
        dc = dw[ck] - rw[ck]
        rf, rt = snap(r, rw)
        df_, dt_ = snap(d, dw)
        st = 'ok' if abs(dc) <= a.wall_tol and abs(df_ - rf) <= a.open_tol and abs(dt_ - rt) <= a.open_tol else 'FAIL'
        if st == 'FAIL':
            bad += 1
        print('| %s | %s | %.3f/%.3f | %+.3f | %.3f/%.3f | %.3f/%.3f | %+.3f | %+.3f | %s |' % (
            dw['id'], rw['id'], dw[ck], rw[ck], dc, df_, rf, dt_, rt, df_ - rf, dt_ - rt, st))
    matched = {id(v) for v in imap.values() if v}
    for rw in r['interiorWalls']:
        if id(rw) not in matched:
            bad += 1
            print('| (missed) | %s | | | | | | | FAIL |' % rw['id'])
    print()
    print('outside tolerance: %d' % bad)
    sys.exit(1 if bad else 0)


def _hinge_word(o):
    s = o.get('swing', '')
    for w in ('north', 'south', 'east', 'west'):
        if 'hinge on %s' % w in s or 'hinge at %s' % w in s or 'hinge at the %s' % w in s:
            return w
    return '-'


def snap(spec, w):
    """Wall ends snapped to the face of the wall they meet (same rule as shell.py)."""
    ot = spec['wallThickness']['outer']
    W, D = spec['footprint']
    lo, hi = w['from'], w['to']
    c = w['center_z'] if w['axis'] == 'x' else w['center_x']
    lim = (ot, W - ot) if w['axis'] == 'x' else (ot, D - ot)
    lo, hi = max(lo, lim[0]) if lo <= lim[0] + 0.01 else lo, min(hi, lim[1]) if hi >= lim[1] - 0.01 else hi
    for p in spec['interiorWalls']:
        if p['axis'] == w['axis']:
            continue
        pc = p['center_x'] if p['axis'] == 'z' else p['center_z']
        ht = p['thickness'] / 2
        if not (p['from'] - 0.01 <= c <= p['to'] + 0.01):
            continue
        if pc - ht - 0.01 <= lo <= pc + ht + 0.01:
            lo = pc + ht
        if pc - ht - 0.01 <= hi <= pc + ht + 0.01:
            hi = pc - ht
    return round(lo, 3), round(hi, 3)


if __name__ == '__main__':
    main()
