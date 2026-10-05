#!/usr/bin/env python3
"""Draw a black-wall floor-plan PNG from a spec (default: measure/spec.example.json).

    python3 make_synthetic_plan.py --out plan.png [--spec spec.json] [--no-labels]

The drawing follows the conventions measure/measure_plan.py reads: thick black outer walls,
thinner interior walls, real gaps at openings, two thin lines across each window gap, a straight
door leaf at the hinge jamb with its swing arc, and bifold zig-zags. Scale and placement come from
the spec's source block (metresPerPixel, footprintPx left/top, resolution), so the measured result
can be compared with the spec it was drawn from (see tests/run_selftest.py). Requires Pillow.
"""
import argparse
import json
import math
import os

from PIL import Image, ImageDraw, ImageFont

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLACK = 0
LEAF_PX = 4          # door leaf line width (thin: removed by the measurer's opening, seen as evidence)
LINE_PX = 2          # window lines, arcs, bifold panels


def draw_plan(spec, out, labels=True):
    src = spec['source']
    mx, mz = src['metresPerPixel']['x'], src['metresPerPixel']['z']
    L, T = src['footprintPx']['left'], src['footprintPx']['top']
    W, H = src['resolution']
    img = Image.new('L', (W, H), 255)
    d = ImageDraw.Draw(img)

    def px(x):
        return int(round(L + x / mx))

    def pz(z):
        return int(round(T + z / mz))

    def band_rect(axis, a0, a1, t0, t1):
        """Fill metres along-axis a0..a1 and across t0..t1 (edges; inclusive pixel boxes)."""
        if axis == 'x':
            d.rectangle([px(a0), pz(t0), px(a1) - 1, pz(t1) - 1], fill=BLACK)
        else:
            d.rectangle([px(t0), pz(a0), px(t1) - 1, pz(a1) - 1], fill=BLACK)

    def to_px(axis, a, t):
        """(along, across) metres -> image point."""
        return (L + a / mx, T + t / mz) if axis == 'x' else (L + t / mx, T + a / mz)

    def solid_pieces(w):
        cuts = sorted((o['from'], o['to']) for o in w['openings'])
        s, out = w['from'], []
        for a, b in cuts:
            if a > s:
                out.append((s, a))
            s = max(s, b)
        if w['to'] > s:
            out.append((s, w['to']))
        return out

    def leaf_and_arc(axis, o, face, sign):
        """Leaf at the hinge jamb standing out from `face` towards `sign`, arc to the other jamb."""
        a0, a1 = o['from'], o['to']
        width = a1 - a0
        hinge = o.get('hinge', 'from')
        ja = a0 if hinge == 'from' else a1
        jb = a1 if hinge == 'from' else a0
        # leaf: LEAF_PX wide, inside the gap against the hinge jamb
        if axis == 'x':
            x0 = px(ja) if hinge == 'from' else px(ja) - LEAF_PX
            y_face = pz(face)
            y_tip = pz(face + sign * width)
            d.rectangle([x0, min(y_face, y_tip), x0 + LEAF_PX - 1, max(y_face, y_tip) - 1], fill=BLACK)
        else:
            y0 = pz(ja) if hinge == 'from' else pz(ja) - LEAF_PX
            x_face = px(face)
            x_tip = px(face + sign * width)
            d.rectangle([min(x_face, x_tip), y0, max(x_face, x_tip) - 1, y0 + LEAF_PX - 1], fill=BLACK)
        cx, cy = to_px(axis, ja, face)
        rx, ry = width / mx, width / mz
        tip = to_px(axis, ja, face + sign * width)
        other = to_px(axis, jb, face)
        a_tip = math.degrees(math.atan2(tip[1] - cy, tip[0] - cx)) % 360
        a_oth = math.degrees(math.atan2(other[1] - cy, other[0] - cx)) % 360
        start, end = (a_tip, a_oth) if (a_oth - a_tip) % 360 <= 180 else (a_oth, a_tip)
        d.arc([cx - rx, cy - ry, cx + rx, cy + ry], start, end, fill=BLACK, width=LINE_PX)

    def bifold(axis, o, face, sign, fold_deg=30):
        a0, a1 = o['from'], o['to']
        p = (a1 - a0) / 4
        c, s = math.cos(math.radians(fold_deg)), math.sin(math.radians(fold_deg))
        for jamb, dirn in ((a0, 1), (a1, -1)):
            pts = [(jamb, face), (jamb + dirn * p * c, face + sign * p * s), (jamb + dirn * 2 * p * c, face)]
            d.line([to_px(axis, a, t) for a, t in pts], fill=BLACK, width=LINE_PX + 1)

    def window_lines(axis, o, t0, t1):
        th = t1 - t0
        for f in (0.25, 0.7):
            t = t0 + th * f
            if axis == 'x':
                y = pz(t)
                d.rectangle([px(o['from']), y, px(o['to']) - 1, y + LINE_PX - 1], fill=BLACK)
            else:
                x = px(t)
                d.rectangle([x, pz(o['from']), x + LINE_PX - 1, pz(o['to']) - 1], fill=BLACK)

    # outer walls
    for w in spec['outerWalls']:
        axis = w['axis']
        if axis == 'x':
            t0, t1 = sorted((w['line_z_outer'], w['inner_z']))
            inward = 1 if w['inner_z'] > w['line_z_outer'] else -1
        else:
            t0, t1 = sorted((w['line_x_outer'], w['inner_x']))
            inward = 1 if w['inner_x'] > w['line_x_outer'] else -1
        for a, b in solid_pieces(w):
            band_rect(axis, a, b, t0, t1)
        for o in w['openings']:
            if o['type'] == 'window':
                window_lines(axis, o, t0, t1)
            elif o['type'] == 'door':
                interior = o.get('swingSide', 'interior') == 'interior'
                sign = inward if interior else -inward
                face = (t1 if inward > 0 else t0) if interior else (t0 if inward > 0 else t1)
                leaf_and_arc(axis, o, face, sign)

    # interior walls
    for w in spec['interiorWalls']:
        axis = w['axis']
        th = w.get('thickness', spec['wallThickness']['interior'])
        c = w['center_z'] if axis == 'x' else w['center_x']
        t0, t1 = c - th / 2, c + th / 2
        for a, b in solid_pieces(w):
            band_rect(axis, a, b, t0, t1)
        for o in w['openings']:
            side = o.get('swingSide', '+' + ('z' if axis == 'x' else 'x'))
            sign = -1 if side.startswith('-') or side in ('north', 'west') else 1
            face = t0 if sign < 0 else t1
            if o['type'] == 'door':
                leaf_and_arc(axis, o, face, sign)
            elif o['type'] == 'bifold':
                bifold(axis, o, face, sign)

    if labels:
        try:
            font = ImageFont.load_default(size=11)
        except TypeError:
            font = ImageFont.load_default()
        for r in spec.get('rooms', []):
            x0, z0, x1, z1 = r['bounds']
            d.text(((px(x0) + px(x1)) / 2, (pz(z0) + pz(z1)) / 2), r.get('label', r['name']), fill=90, font=font,
                   anchor='mm')
        fw, fd = spec['footprint']
        d.text(((px(0) + px(fw)) / 2, T - 40), '%.2f m' % fw, fill=90, font=font, anchor='mm')
        d.text((L - 50, (pz(0) + pz(fd)) / 2), '%.2f m' % fd, fill=90, font=font, anchor='mm')
    img.save(out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--spec', default=os.path.join(KIT, 'measure', 'spec.example.json'))
    ap.add_argument('--out', required=True)
    ap.add_argument('--no-labels', action='store_true')
    a = ap.parse_args(argv)
    with open(a.spec) as f:
        spec = json.load(f)
    print(draw_plan(spec, a.out, labels=not a.no_labels))


if __name__ == '__main__':
    main()
