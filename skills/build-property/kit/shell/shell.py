#!/usr/bin/env python3
"""Generic shell generator: spec.json -> queue payloads for the building shell.

    python3 shell.py spec.json --out payload_dir [--prefix-outer so- --prefix-inner si-]
                     [--only outer|inner] [--bifold-max-projection 0.15] [--emit-glass-material]

Builds, from any spec in the measure/spec.schema.json format:
  outer walls   6-slot box materials (+X,-X,+Y,-Y,+Z,-Z): local -Z = exterior (m-siding),
                local +Z and opening reveals = interior (m-paint); corner ends m-siding.
                Real gaps at openings; headers (fixed height, hidden in cutaway); sill
                walls under windows; windows with black frames, mullions, glass panes
                (m-glass, role glazing, castShadow false) and an interior stool; front/back
                exterior doors drawn open on the hinge side, with thresholds.
  joints        pieces meeting end to end overlap by JOINT_OV (2 mm): corners, partition ends
                at solid walls, headers and sill walls into the pieces beside them.
  interior      partitions snapped to the faces of the walls they meet, real gaps,
                headers, hinged leaves drawn open on the hinge side (role door, no
                collision; shaker or flat slab, leaf and hardware materials from the opening
                or spec.interiorDoors) and bifold pairs folded close to the jambs.
  not covered   angledWalls (walls at an angle to x and z): hand-model them, see README.md.

Coordinates: metres, origin outside NW corner, +x east, +z south, y up (spec.axes).
See README.md for how hinge/swing strings are interpreted.
Returns a lib.payload bundle (build_shell) and writes it with lib.payload.write_payloads.
"""
import argparse
import json
import math
import os
import re
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if KIT not in sys.path:
    sys.path.insert(0, KIT)
from lib.payload import behavior, merge, node, r3, write_payloads  # noqa: E402

PI = math.pi
EPS = 1e-6

# ---------------------------------------------------------------- constants
# outer windows
WIN_F = 0.05        # frame member
WIN_MUL = 0.05      # mullion / meeting rail
WIN_FD = 0.07       # frame depth (centred in the wall)
WIN_UNIT = 1.0      # one sash per started metre of width (minus 5 cm tolerance)
# outer doors
OD_T = 0.045        # leaf thickness
OD_Y = 0.008        # leaf bottom above floor
OD_INSIDE = 0.019   # inward leaves hinge this far inside the inner face
# interior doors
LT, LY = 0.035, 0.005                       # leaf thickness, bottom clearance
STILE, TOP, MID, BOT = 0.12, 0.12, 0.16, 0.20
MID_Y = 0.94                                # bottom of the mid rail (leaf-local)
PT = 0.018                                  # recessed panel thickness
KNOB_X, KNOB_Y = 0.065, 0.91                # knob from the latch edge, height
# bifolds
BF_T, BF_H, BF_Y = 0.025, 2.0, 0.015
BF_MAX_FOLD = 45.0
# wall joints: pieces that meet end to end overlap by this much, because boxes that only touch leave
# hairline cracks along the joint (the server never reports wall-wall overlaps as intersections)
JOINT_OV = 0.002

WALL_B = behavior('ceiling', 'reduce', True, True)
HEAD_B = behavior('fixed', 'hide', True, True)
SILL_B = behavior('fixed', 'keep', True, True)
HIDE_B = behavior('fixed', 'hide')
KEEP_B = behavior('fixed', 'keep')

COMPASS = {'north': ('z', -1), 'south': ('z', 1), 'west': ('x', -1), 'east': ('x', 1)}


def I4(x=0, y=0, z=0):
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]


def yaw_m(theta, t, sy=1.0):
    """Column-major matrix: rotate theta about Y, scale y by sy, translate t."""
    c, s = math.cos(theta), math.sin(theta)
    return [c, 0, -s, 0, 0, sy, 0, 0, s, 0, c, 0, t[0], t[1], t[2], 1]


def rx90(t, sx=1.0, sl=1.0):
    """Column-major matrix turning a Y-axis cylinder onto Z (radius scale sx, length sl)."""
    return [sx, 0, 0, 0, 0, 0, sl, 0, 0, -sx, 0, 0, t[0], t[1], t[2], 1]


def mm(v):
    return str(int(round(v * 1000)))


class Acc:
    """Resource/node accumulator with deterministic geometry ids."""

    def __init__(self, prefix):
        self.p = prefix
        self.res = {}
        self.nodes = []

    def geom(self, name, data):
        rid = self.p + name
        if rid in self.res:
            assert self.res[rid]['data'] == data, rid
        else:
            self.res[rid] = {'id': rid, 'kind': 'geometry', 'data': data}
        return rid

    def box(self, dims, prefix):
        d = [r3(x) for x in dims]
        return self.geom(prefix + '-' + '-'.join(mm(x) for x in d), {'type': 'box', 'dimensions': d})

    def add(self, nid, parent=None, **kw):
        n = node(self.p + nid if not nid.startswith(self.p) else nid,
                 (self.p + parent) if parent and not parent.startswith(self.p) else parent, **kw)
        self.nodes.append(n)
        return n

    def bundle(self):
        return {'resources': list(self.res.values()), 'products': [], 'nodes': self.nodes}


# ---------------------------------------------------------------- spec interpretation
def room_side(spec, room_id, axis, centre):
    """+1 / -1: which side of a wall line (centre, perpendicular to axis) a room lies on."""
    for rm in spec.get('rooms', []):
        if rm['id'] == room_id and rm.get('bounds'):
            x1, z1, x2, z2 = rm['bounds']
            c = (z1 + z2) / 2 if axis == 'x' else (x1 + x2) / 2
            return 1 if c > centre else -1
    return None


DOOR_STYLES = ('shaker', 'slab')


def door_opt(spec, o, key, default):
    """Interior door option: the opening's own value, else spec.interiorDoors, else the default."""
    return o.get(key, (spec.get('interiorDoors') or {}).get(key, default))


def resolve_room(spec, o):
    """Room id an interior opening serves: o.room, else a room id/name named in the text."""
    if o.get('room'):
        return o['room']
    text = ' '.join(str(o.get(k, '')) for k in ('swing', 'note')).lower()
    best = None
    for rm in spec.get('rooms', []):
        for key in (rm['id'], rm.get('name', '')):
            k = key.lower().replace('-', ' ')
            if k and (k in text.replace('-', ' ')) and (best is None or len(k) > best[0]):
                best = (len(k), rm['id'])
    return best[1] if best else None


def hinge_end(o, axis):
    """'from' or 'to' (the jamb the leaf hangs on) from o.hinge or 'hinge on/at the <dir> jamb'."""
    if o.get('hinge') in ('from', 'to'):
        return o['hinge']
    m = re.search(r'hinge(?:d)?\s+(?:on|at)\s+(?:the\s+)?(north|south|east|west)\s+jamb', o.get('swing', ''), re.I)
    if not m:
        raise ValueError('%s: no hinge given ("hinge": "from"|"to", or "hinge on the <dir> jamb")' % o['id'])
    ax, sgn = COMPASS[m.group(1).lower()]
    want = 'x' if axis == 'x' else 'z'
    if ax != want:
        raise ValueError('%s: hinge jamb %s is not on a %s-axis wall' % (o['id'], m.group(1), axis))
    return 'to' if sgn > 0 else 'from'


def swing_sign(spec, o, axis, centre):
    """Interior openings: +1 if the leaf/bifold opens toward +z (x-axis wall) or +x (z-axis wall)."""
    ss = o.get('swingSide')
    if ss in ('+x', '+z'):
        return 1
    if ss in ('-x', '-z'):
        return -1
    if ss in COMPASS:
        return COMPASS[ss][1]
    rid = resolve_room(spec, o)
    if rid:
        s = room_side(spec, rid, axis, centre)
        if s:
            return s
    text = ' '.join(str(o.get(k, '')) for k in ('swing', 'note')).lower()
    m = re.search(r'(?:into|facing|to the|opening)\D*?\b(north|south|east|west)\b', text)
    if m:
        ax, sgn = COMPASS[m.group(1)]
        if (ax == 'z') == (axis == 'x'):
            return sgn
    raise ValueError('%s: cannot tell which side it opens to (set "room" or "swingSide")' % o['id'])


def outer_swing_inward(o):
    ss = o.get('swingSide')
    if ss in ('interior', 'exterior'):
        return ss == 'interior'
    s = o.get('swing', '').lower()
    if 'outward' in s or 'out-swing' in s or 'outswing' in s:
        return False
    if 'inward' in s or 'in-swing' in s or 'inswing' in s:
        return True
    raise ValueError('%s: outer door needs "inward"/"outward" in swing or swingSide interior|exterior' % o['id'])


# ---------------------------------------------------------------- outer shell
class OuterWall:
    def __init__(self, w, spec):
        self.w = w
        self.id = w['id']
        self.axis = w['axis']
        if self.axis == 'x':
            out, inn = w['line_z_outer'], w['inner_z']
        else:
            out, inn = w['line_x_outer'], w['inner_x']
        self.outer, self.inner = out, inn
        self.sgn = 1 if inn > out else -1           # inward direction on the perpendicular axis
        self.T = abs(inn - out)
        self.c = (out + inn) / 2
        if self.axis == 'x':
            self.theta = 0.0 if self.sgn > 0 else PI
            self.dir = math.cos(self.theta)          # local +x along world +x?
        else:
            self.theta = PI / 2 if self.sgn > 0 else -PI / 2
            self.dir = -math.sin(self.theta)
        self.dir = 1 if self.dir > 0 else -1
        # z-axis walls stop at the inner face of the x-axis walls they meet (corners belong to x walls)
        lo, hi = w['from'], w['to']
        self.lo_trim = self.hi_trim = False
        if self.axis == 'z':
            for o2 in spec['outerWalls']:
                if o2['axis'] != 'x' or not (o2['from'] - EPS <= self.outer <= o2['to'] + EPS):
                    continue
                b0, b1 = sorted([o2['line_z_outer'], o2['inner_z']])
                if b0 - EPS <= lo <= b1 + EPS and lo < b1:
                    lo, self.lo_trim = b1, True
                if b0 - EPS <= hi <= b1 + EPS and hi > b0:
                    hi, self.hi_trim = b0, True
        self.lo, self.hi = lo, hi

    def world(self, s, wv):
        """s along the wall axis, wv offset from the centre line toward the interior -> [x, z]."""
        if self.axis == 'x':
            return [s, self.c + self.sgn * wv]
        return [self.c + self.sgn * wv, s]

    def origin_s(self, a, b):
        return a if self.dir > 0 else b

    def rot(self):
        return [0, self.theta, 0]

    def along(self):
        return (1, 0) if self.axis == 'x' else (0, 1)

    def normal_in(self):
        return (0, self.sgn) if self.axis == 'x' else (self.sgn, 0)


def build_outer(spec, prefix='so-', opts=None):
    opts = opts or {}
    A = Acc(prefix)
    H = spec['ceilingHeight']
    walls = [OuterWall(w, spec) for w in spec['outerWalls']]

    def wall_piece(nid, W, a, b, y0, y1, beh, name):
        L = b - a
        g = A.box([L, y1 - y0, W.T], 'wb')
        x, z = W.world((a + b) / 2, 0)

        def end_mat(sign):
            e = (a + b) / 2 + sign * W.dir * L / 2
            corner = (abs(e - W.w['from']) < EPS and not W.lo_trim) or (abs(e - W.w['to']) < EPS and not W.hi_trim)
            return 'm-siding' if corner else 'm-paint'

        mats = [end_mat(+1), end_mat(-1), 'm-paint', 'm-paint', 'm-paint', 'm-siding']
        A.add(nid, name=name, position=[x, (y0 + y1) / 2, z], rotation=W.rot(), container='shell', role='wall',
              render={'geometry': g, 'materials': mats}, behavior=beh)

    openings = []
    for W in walls:
        ops = sorted(W.w['openings'], key=lambda o: o['from'])
        # a trimmed end runs into the wall it meets; headers and sill walls run into the pieces beside them
        cur, k = W.lo - (JOINT_OV if W.lo_trim else 0), 1
        for j, o in enumerate(ops + [None]):
            end = o['from'] if o else W.hi + (JOINT_OV if W.hi_trim else 0)
            if end - cur > EPS:
                wall_piece('w-%s-%d' % (W.id, k), W, cur, end, 0, H, WALL_B, '%s wall %d' % (W.id.title(), k))
                k += 1
            if o:
                openings.append((W, o))
                oid = o['id']
                nxt = ops[j + 1]['from'] if j + 1 < len(ops) else W.hi
                a = o['from'] - (JOINT_OV if o['from'] - max(cur, W.lo) > EPS or W.lo_trim and j == 0 else 0)
                b = o['to'] + (JOINT_OV if nxt - o['to'] > EPS or W.hi_trim and j + 1 == len(ops) else 0)
                head = o.get('head', spec['heights']['door'] if o['type'] != 'window' else spec['heights']['windowHead'])
                wall_piece('hd-' + oid, W, a, b, head, H, HEAD_B, 'Header ' + oid)
                if o['type'] == 'window':
                    wall_piece('sl-' + oid, W, a, b, 0, sill_of(spec, o), SILL_B, 'Sill wall ' + oid)
                cur = o['to']

    for W, o in openings:
        if o['type'] == 'window':
            window(A, spec, W, o)
    for W, o in openings:
        if o['type'] == 'door':
            outer_door(A, spec, W, o)
    return A.bundle()


def sill_of(spec, o):
    return o.get('sill', spec['heights']['windowSill'])


def window(A, spec, W, o):
    oid = o['id']
    w = o['to'] - o['from']
    sill = sill_of(spec, o)
    h = o.get('head', spec['heights']['windowHead']) - sill
    n_units = max(1, math.ceil(w / WIN_UNIT - 0.05))
    uw = (w - 2 * WIN_F - (n_units - 1) * WIN_MUL) / n_units
    hh = (h - 2 * WIN_F - WIN_MUL) / 2
    holes, centres = [], []
    for i in range(n_units):
        x0 = WIN_F + i * (uw + WIN_MUL)
        for j in range(2):
            y0 = WIN_F + j * (hh + WIN_MUL)
            holes.append([[r3(x0), r3(y0)], [r3(x0), r3(y0 + hh)], [r3(x0 + uw), r3(y0 + hh)], [r3(x0 + uw), r3(y0)]])
            centres.append((x0 + uw / 2, y0 + hh / 2))
    gframe = A.geom('wf-%sx%s' % (mm(w), mm(h)), {
        'type': 'extrude', 'points': [[0, 0], [r3(w), 0], [r3(w), r3(h)], [0, r3(h)]],
        'holes': holes, 'depth': WIN_FD, 'bevel': 0, 'bevelSegments': 1})
    gglass = A.box([uw - 0.004, hh - 0.004, 0.006], 'gl')
    zi, zh = WIN_FD / 2, W.T / 2 + 0.04          # stool: frame inner face to 40 mm proud of the wall
    gstool = A.box([w, 0.025, zh - zi], 'st')
    gx, gz = W.world(W.origin_s(o['from'], o['to']), 0)
    root = 'win-' + oid
    A.add(root, name='Window ' + oid, position=[gx, 0, gz], rotation=W.rot(), container='shell')
    A.add(root + '-fr', root, name='Frame', position=[0, sill, -WIN_FD / 2], role='glazing',
          render={'geometry': gframe, 'materials': ['m-steel-black', 'm-steel-black']}, behavior=HIDE_B)
    A.add(root + '-gl', root, name='Glass', position=[0, sill, 0], role='glazing',
          render={'geometry': gglass, 'materials': ['m-glass'], 'instances': [I4(cx, cy) for cx, cy in centres]},
          behavior=HIDE_B, raycast=False, castShadow=False, receiveShadow=True)
    A.add(root + '-st', root, name='Stool', position=[w / 2, sill + 0.0125, (zi + zh) / 2], role='fixture',
          render={'geometry': gstool, 'materials': ['m-trim']}, behavior=KEEP_B)


def outer_door(A, spec, W, o):
    oid = o['id']
    name = oid[5:] if oid.startswith('door-') else oid
    head = o.get('head', spec['heights']['door'])
    DH = r3(head - 0.002)
    LW = r3(o['to'] - o['from'] - 0.01)
    hend = hinge_end(o, W.axis)
    inward = outer_swing_inward(o)
    al = W.along()
    c = al if hend == 'from' else (-al[0], -al[1])           # closed leaf direction (into the opening)
    s_h = o[hend] + (c[0] + c[1]) * (0.003 + OD_T / 2)
    if inward:
        perp = W.inner + W.sgn * OD_INSIDE
        hinge = [s_h, perp] if W.axis == 'x' else [perp, s_h]
        n = W.normal_in()
    else:
        hinge = [s_h, W.outer] if W.axis == 'x' else [W.outer, s_h]
        n = (-W.normal_in()[0], -W.normal_in()[1])
    phi = math.radians(o.get('openDeg', 90))
    d = (c[0] * math.cos(phi) + n[0] * math.sin(phi), c[1] * math.cos(phi) + n[1] * math.sin(phi))
    theta = math.atan2(-d[1], d[0])
    main = o.get('entrance', 'front' in oid)
    leaf_mat = o.get('leafMaterial', 'm-wood-walnut' if main else 'm-trim')
    th_mat = o.get('thresholdMaterial', 'm-oak' if main else 'm-steel')

    root = 'door-' + name
    label = o.get('label') or ('%s door' % name.replace('-', ' ').capitalize())
    A.add(root, name=label, position=[hinge[0], OD_Y, hinge[1]], rotation=[0, theta, 0], container='shell')
    gx0, gx1, gy0, gy1 = 0.13, LW - 0.13, 1.07, DH - 0.13
    gl = A.geom('dl-' + mm(LW), {
        'type': 'extrude', 'points': [[0, 0], [r3(LW), 0], [r3(LW), DH], [0, DH]],
        'holes': [[[gx0, gy0], [gx0, r3(gy1)], [r3(gx1), r3(gy1)], [r3(gx1), gy0]]],
        'depth': OD_T, 'bevel': 0, 'bevelSegments': 1})
    A.add(root + '-lf', root, name='Leaf', position=[0, 0, -OD_T / 2], role='door',
          render={'geometry': gl, 'materials': [leaf_mat, leaf_mat]}, behavior=KEEP_B)
    gwid, ghgt = gx1 - gx0, gy1 - gy0
    gg = A.box([gwid - 0.004, ghgt - 0.004, 0.006], 'gl')
    mid = [(gx0 + gx1) / 2, (gy0 + gy1) / 2, 0]
    A.add(root + '-gl', root, name='Lite', position=mid, role='door',
          render={'geometry': gg, 'materials': ['m-glass']}, behavior=KEEP_B, raycast=False,
          castShadow=False, receiveShadow=True)
    zm = 0.003 + 0.006                                       # 2x2 muntins on both faces of the lite
    gv = A.box([0.022, ghgt - 0.004, 0.012], 'mu')
    gh = A.box([gwid - 0.004, 0.022, 0.012], 'mu')
    for sfx, g in (('-mv', gv), ('-mh', gh)):
        A.add(root + sfx, root, name='Muntins', position=mid, role='door',
              render={'geometry': g, 'materials': [leaf_mat], 'instances': [I4(0, 0, s * zm) for s in (1, -1)]},
              behavior=KEEP_B)
    pw = (LW - 0.26 - 0.1) / 2                               # two raised lower panels per face
    gp = A.box([pw, 0.78, 0.012], 'dp')
    pin = [I4(cx, 0, s * (OD_T / 2 + 0.006)) for s in (1, -1) for cx in (0.13 + pw / 2, LW - 0.13 - pw / 2)]
    A.add(root + '-pn', root, name='Panels', position=[0, 0.15 + 0.39, 0], role='door',
          render={'geometry': gp, 'materials': [leaf_mat], 'instances': pin}, behavior=KEEP_B)
    hx = LW - 0.065                                          # black hardware, both faces
    gros = A.geom('ros', {'type': 'cylinder', 'radiusTop': 0.028, 'radiusBottom': 0.028, 'height': 0.012,
                          'radialSegments': 24, 'openEnded': False})
    ros = [rx90((hx, y, s * (OD_T / 2 + 0.006))) for s in (1, -1) for y in (0.95, 1.12)]
    A.add(root + '-rs', root, name='Rosettes', role='door',
          render={'geometry': gros, 'materials': ['m-steel-black'], 'instances': ros}, behavior=KEEP_B)
    glev = A.geom('lever', {'type': 'roundedBox', 'dimensions': [0.12, 0.018, 0.02], 'radius': 0.006, 'segments': 2})
    ghinge = A.geom('hinge', {'type': 'cylinder', 'radiusTop': 0.008, 'radiusBottom': 0.008, 'height': 0.09,
                              'radialSegments': 12, 'openEnded': False})
    ks, kl = 1.125, r3(0.05 / 0.09)                          # neck = hinge barrel scaled to r 9 mm, 50 mm
    lev = [I4(hx - 0.045, 0.95, s * (OD_T / 2 + 0.012 + 0.05 + 0.01)) for s in (1, -1)]
    nk = [rx90((hx, 0.95, s * (OD_T / 2 + 0.012 + 0.025)), ks, kl) for s in (1, -1)]
    A.add(root + '-lv', root, name='Levers', role='door',
          render={'geometry': glev, 'materials': ['m-steel-black'], 'instances': lev}, behavior=KEEP_B)
    A.add(root + '-nk', root, name='Lever necks', role='door',
          render={'geometry': ghinge, 'materials': ['m-steel-black'], 'instances': nk}, behavior=KEEP_B)
    A.add(root + '-hb', root, name='Hinges', role='door',
          render={'geometry': ghinge, 'materials': ['m-steel-black'],
                  'instances': [I4(-0.009, y, 0) for y in (0.25, 1.05, 1.8)]}, behavior=KEEP_B)
    # threshold across the full wall thickness
    g = A.box([o['to'] - o['from'], 0.015, W.T], 'th')
    x, z = W.world((o['from'] + o['to']) / 2, 0)
    A.add('th-' + name, name='Threshold ' + name, position=[x, 0.003 + 0.0075, z], rotation=W.rot(),
          container='shell', role='fixture', render={'geometry': g, 'materials': [th_mat]}, behavior=KEEP_B)


# ---------------------------------------------------------------- interior shell
def wall_rects(spec):
    """Every wall as an xz rectangle (x0, x1, z0, z1): outer bands and interior bands (no gaps)."""
    out = []
    for w in spec['outerWalls']:
        if w['axis'] == 'x':
            z0, z1 = sorted([w['line_z_outer'], w['inner_z']])
            out.append((w['from'], w['to'], z0, z1))
        else:
            x0, x1 = sorted([w['line_x_outer'], w['inner_x']])
            out.append((x0, x1, w['from'], w['to']))
    return out


def inner_extent(spec, w):
    """Partition ends snapped to the near face of whatever wall they meet: (a, b, a_solid, b_solid),
    where *_solid says the wall met there is solid (no opening across the partition's thickness)."""
    ht_default = spec['wallThickness']['interior'] / 2
    ht = w.get('thickness', 2 * ht_default) / 2
    c = w['center_z'] if w['axis'] == 'x' else w['center_x']
    a, b = w['from'], w['to']
    a_solid = b_solid = False
    tol = 0.01

    def solid(met):
        return not any(o['from'] < c + ht and o['to'] > c - ht for o in met.get('openings', []))

    # outer walls: snap to the inner face when the end reaches into (or within tol of) the band
    for o in spec['outerWalls']:
        if o['axis'] == w['axis']:
            continue
        if not (o['from'] - tol <= c <= o['to'] + tol):
            continue
        out = o['line_z_outer'] if o['axis'] == 'x' else o['line_x_outer']
        inn = o['inner_z'] if o['axis'] == 'x' else o['inner_x']
        if inn > out and a <= inn + tol:
            a, a_solid = inn, solid(o)
        if inn < out and b >= inn - tol:
            b, b_solid = inn, solid(o)
    for p in spec['interiorWalls']:
        if p['axis'] == w['axis'] or p is w:
            continue
        pc = p['center_z'] if p['axis'] == 'x' else p['center_x']
        pht = p.get('thickness', 2 * ht_default) / 2
        if not (p['from'] - tol <= c <= p['to'] + tol):
            continue
        if pc - pht - tol <= a <= pc + pht + tol:
            a, a_solid = pc + pht, solid(p)
        if pc - pht - tol <= b <= pc + pht + tol:
            b, b_solid = pc - pht, solid(p)
    return a, b, a_solid, b_solid


def _poly_rect_overlap(poly, rect, eps=0.002):
    """Separating-axis test: convex polygon (list of (x, z)) vs axis-aligned rect, with eps clearance."""
    x0, x1, z0, z1 = rect
    xs, zs = [p[0] for p in poly], [p[1] for p in poly]
    if max(xs) <= x0 + eps or min(xs) >= x1 - eps or max(zs) <= z0 + eps or min(zs) >= z1 - eps:
        return False
    corners = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    for i in range(len(poly)):
        ax = (-(poly[(i + 1) % len(poly)][1] - poly[i][1]), poly[(i + 1) % len(poly)][0] - poly[i][0])
        pa = [ax[0] * p[0] + ax[1] * p[1] for p in poly]
        pb = [ax[0] * p[0] + ax[1] * p[1] for p in corners]
        ln = math.hypot(*ax) or 1
        if max(pa) <= min(pb) + eps * ln or max(pb) <= min(pa) + eps * ln:
            return False
    return True


def build_inner(spec, prefix='si-', opts=None):
    opts = opts or {}
    A = Acc(prefix)
    CEIL = spec['ceilingHeight']
    rects = wall_rects(spec)
    gaps = []

    # ---- partitions, headers
    for w in spec['interiorWalls']:
        T = w.get('thickness', spec['wallThickness']['interior'])
        HT = T / 2
        a, b, a_solid, b_solid = inner_extent(spec, w)
        cuts = sorted((o['from'], o['to'], o) for o in w['openings'])
        segs, cur = [], a - (JOINT_OV if a_solid else 0)
        for f, t, o in cuts:
            segs.append((cur, f))
            cur = t
        segs.append((cur, b + (JOINT_OV if b_solid else 0)))
        name = w['id'].replace('-wall', '')
        segs = [(s0, s1) for s0, s1 in segs if s1 - s0 > 0.005]
        for i, (s0, s1) in enumerate(segs):
            L = s1 - s0
            if L < 0.05:
                print('warning: %s segment %d is only %.3f m long' % (w['id'], i + 1, L), file=sys.stderr)
            sid = 'w-' + name + ('-%d' % (i + 1) if len(segs) > 1 else '')
            if w['axis'] == 'x':
                dims, pos = [L, CEIL, T], [(s0 + s1) / 2, CEIL / 2, w['center_z']]
                rects.append((s0, s1, w['center_z'] - HT, w['center_z'] + HT))
            else:
                dims, pos = [T, CEIL, L], [w['center_x'], CEIL / 2, (s0 + s1) / 2]
                rects.append((w['center_x'] - HT, w['center_x'] + HT, s0, s1))
            A.add(sid, name='%s %d' % (w['id'], i + 1), position=pos, container='shell', role='wall',
                  render={'geometry': A.box(dims, 'g-box'), 'materials': ['m-paint']}, behavior=WALL_B)
        for j, (f, t, o) in enumerate(cuts):
            gaps.append((w, o))
            head = o.get('head', spec['heights']['door'])
            # run into the partition pieces beside the opening (not into a neighbouring opening)
            prev = cuts[j - 1][1] if j else a
            nxt = cuts[j + 1][0] if j + 1 < len(cuts) else b
            f2 = f - (JOINT_OV if f - prev > 0.005 else 0)
            t2 = t + (JOINT_OV if nxt - t > 0.005 else 0)
            hh, L = CEIL - head, t2 - f2
            if w['axis'] == 'x':
                dims, pos = [L, hh, T], [(f2 + t2) / 2, head + hh / 2, w['center_z']]
            else:
                dims, pos = [T, hh, L], [w['center_x'], head + hh / 2, (f2 + t2) / 2]
            A.add('h-' + o['id'], name=o['id'] + ' header', position=pos, container='shell', role='wall',
                  render={'geometry': A.box(dims, 'g-box'), 'materials': ['m-paint']}, behavior=HEAD_B)

    # ---- hinged doors
    A.geom('g-knob', {'type': 'sphere', 'radius': 0.026, 'widthSegments': 16, 'heightSegments': 12})
    A.geom('g-rose', {'type': 'cylinder', 'radiusTop': 0.03, 'radiusBottom': 0.03, 'height': 0.012,
                      'radialSegments': 20})
    A.geom('g-stem', {'type': 'cylinder', 'radiusTop': 0.009, 'radiusBottom': 0.009, 'height': 0.045,
                      'radialSegments': 12})
    leaves = []
    for w, o in gaps:
        if o['type'] != 'door':
            continue
        T = w.get('thickness', spec['wallThickness']['interior'])
        HT = T / 2
        width = o['to'] - o['from']
        lw = round(width - 0.01, 3)
        if abs(lw - 0.76) < 0.005:
            lw = 0.76
        LH = r3(min(2.025, o.get('head', spec['heights']['door']) - 0.007))
        hend = hinge_end(o, w['axis'])
        centre = w['center_z'] if w['axis'] == 'x' else w['center_x']
        nsign = swing_sign(spec, o, w['axis'], centre)
        if w['axis'] == 'x':
            along, n = (1, 0), (0, nsign)
            Hp = (o[hend], centre + nsign * HT)
        else:
            along, n = (0, 1), (nsign, 0)
            Hp = (centre + nsign * HT, o[hend])
        c = along if hend == 'from' else (-along[0], -along[1])
        root = (Hp[0] + c[0] * (LT / 2 + 0.003) + n[0] * 0.005, Hp[1] + c[1] * (LT / 2 + 0.003) + n[1] * 0.005)
        kx = lw - KNOB_X
        reach = LT / 2 + 0.012 + 0.045 + 0.02 + 0.026

        def footprint(ang):
            phi = math.radians(ang)
            d = (c[0] * math.cos(phi) + n[0] * math.sin(phi), c[1] * math.cos(phi) + n[1] * math.sin(phi))
            th = math.atan2(-d[1], d[0])
            zd = (math.sin(th), math.cos(th))

            def tw(p):
                return (root[0] + p[0] * d[0] + p[1] * zd[0], root[1] + p[0] * d[1] + p[1] * zd[1])
            leaf = [tw(p) for p in ((0.05, -LT / 2), (lw, -LT / 2), (lw, LT / 2), (0.05, LT / 2))]
            knob = [tw(p) for p in ((kx - 0.03, -reach), (kx + 0.03, -reach), (kx + 0.03, reach), (kx - 0.03, reach))]
            return d, th, leaf, knob

        if 'openDeg' in o:
            ang = o['openDeg']
        else:   # widest angle <= 90 whose leaf and knobs clear every wall
            ang = 90
            while ang > 30:
                _, _, leaf, knob = footprint(ang)
                if not any(_poly_rect_overlap(p, r) for p in (leaf, knob) for r in rects):
                    break
                ang -= 1
            if ang != 90:
                print('note: %s opens %d deg (clears the walls)' % (o['id'], ang), file=sys.stderr)
        d, theta, _, _ = footprint(ang)
        leaves.append(o['id'])
        style = door_opt(spec, o, 'leafStyle', 'shaker')
        if style not in DOOR_STYLES:
            raise ValueError('%s: leafStyle %r is not one of %s' % (o['id'], style, ', '.join(DOOR_STYLES)))
        leaf_mat = door_opt(spec, o, 'leafMaterial', 'm-trim')
        hw_mat = door_opt(spec, o, 'hardwareMaterial', 'm-brass')
        if style == 'slab':
            fid = A.geom('g-slab-' + mm(lw), {'type': 'box', 'dimensions': [r3(lw), LH, LT]})
        else:
            x0, x1 = STILE, lw - STILE
            holes = [[[x0, BOT], [x0, MID_Y], [x1, MID_Y], [x1, BOT]],
                     [[x0, MID_Y + MID], [x0, LH - TOP], [x1, LH - TOP], [x1, MID_Y + MID]]]
            fid = A.geom('g-leaf-' + mm(lw), {'type': 'extrude', 'points': [[0, 0], [r3(lw), 0], [r3(lw), LH], [0, LH]],
                                              'holes': [[[r3(p[0]), r3(p[1])] for p in h] for h in holes], 'depth': LT})
            pid = A.geom('g-panel-' + mm(lw), {'type': 'box', 'dimensions': [r3(lw - 2 * STILE), 1, PT]})
        did = 'd-' + o['id'].replace('door-', '')
        room = resolve_room(spec, o)
        A.add(did, name='%s leaf (open, hinged %s jamb)' % (o['id'], jamb_word(w['axis'], hend)),
              position=[root[0], 0, root[1]], rotation=[0, theta, 0], container='shell', role='door',
              roomId=room, behavior=KEEP_B)
        zr, zs, zk = LT / 2 + 0.006, LT / 2 + 0.012 + 0.0225, LT / 2 + 0.012 + 0.045 + 0.02
        if style == 'slab':
            kids = [('-frame', 'flat slab leaf', [lw / 2, LY + LH / 2, 0], {'geometry': fid, 'materials': [leaf_mat]})]
        else:
            kids = [
                ('-frame', 'shaker frame', [0, LY, -LT / 2], {'geometry': fid, 'materials': [leaf_mat]}),
                ('-panels', 'recessed panels', [0, 0, 0], {'geometry': pid, 'materials': [leaf_mat], 'instances': [
                    yaw_m(0, (lw / 2, LY + (BOT + MID_Y) / 2, 0), MID_Y - BOT),
                    yaw_m(0, (lw / 2, LY + (MID_Y + MID + LH - TOP) / 2, 0), LH - TOP - MID_Y - MID)]})]
        kids += [
            ('-rose', 'knob rose', [0, 0, 0], {'geometry': A.p + 'g-rose', 'materials': [hw_mat],
                                               'instances': [rx90((kx, KNOB_Y, zr)), rx90((kx, KNOB_Y, -zr))]}),
            ('-stem', 'knob stem', [0, 0, 0], {'geometry': A.p + 'g-stem', 'materials': [hw_mat],
                                               'instances': [rx90((kx, KNOB_Y, zs)), rx90((kx, KNOB_Y, -zs))]}),
            ('-knob', 'brass knob' if hw_mat == 'm-brass' else 'door knob', [0, 0, 0],
             {'geometry': A.p + 'g-knob', 'materials': [hw_mat], 'instances': [I4(kx, KNOB_Y, zk), I4(kx, KNOB_Y, -zk)]}),
        ]
        for sfx, nm, pos, rend in kids:
            A.add(did + sfx, did, name=nm, position=pos, render=rend, behavior=KEEP_B)

    # ---- bifolds: two pairs, each folded from its jamb into the room
    max_proj = opts.get('bifold_max_projection', 0.15)
    for w, o in gaps:
        if o['type'] != 'bifold':
            continue
        T = w.get('thickness', spec['wallThickness']['interior'])
        HT = T / 2
        width = o['to'] - o['from']
        p = round((width - 0.012) / 4, 3)
        if 'foldDeg' in o:
            fold_deg = o['foldDeg']
        else:   # moderate fold: each panel reaches at most max_proj into the room
            fold_deg = min(BF_MAX_FOLD, math.floor(math.degrees(math.asin(min(1.0, max_proj / p)))))
        fold = math.radians(fold_deg)
        centre_line = w['center_z'] if w['axis'] == 'x' else w['center_x']
        nsign = swing_sign(spec, o, w['axis'], centre_line)
        gid = A.box([p, BF_H, BF_T], 'g-box')
        if w['axis'] == 'x':
            along, n, face = (1, 0), (0, nsign), w['center_z'] + nsign * HT
            J0, J1 = (o['from'], face), (o['to'], face)
            centre = ((o['from'] + o['to']) / 2, face)
        else:
            along, n, face = (0, 1), (nsign, 0), w['center_x'] + nsign * HT
            J0, J1 = (face, o['from']), (face, o['to'])
            centre = (face, (o['from'] + o['to']) / 2)
        inst = []
        cA, sA = math.cos(fold), math.sin(fold)
        off = BF_T / 2 + 0.004
        for J, t in ((J0, along), (J1, (-along[0], -along[1]))):
            J = (J[0] + t[0] * 0.006, J[1] + t[1] * 0.006)
            for k, sgn in ((0, +1), (1, -1)):
                dd = (t[0] * cA + sgn * n[0] * sA, t[1] * cA + sgn * n[1] * sA)
                start = (J[0] + t[0] * k * p * cA + n[0] * k * p * sA, J[1] + t[1] * k * p * cA + n[1] * k * p * sA)
                ctr = (start[0] + dd[0] * p / 2 + n[0] * off, start[1] + dd[1] * p / 2 + n[1] * off)
                th = math.atan2(-dd[1], dd[0])
                inst.append(yaw_m(th, (ctr[0] - centre[0], BF_Y + BF_H / 2, ctr[1] - centre[1])))
        bid = 'b-' + o['id'].replace('bifold-', '')
        A.add(bid, name='%s doors (folded %d deg)' % (o['id'], fold_deg), position=[centre[0], 0, centre[1]],
              container='shell', role='door', roomId=resolve_room(spec, o),
              render={'geometry': gid, 'materials': ['m-trim'], 'instances': inst}, behavior=KEEP_B)
    return A.bundle()


def jamb_word(axis, hend):
    return {('x', 'from'): 'west', ('x', 'to'): 'east', ('z', 'from'): 'north', ('z', 'to'): 'south'}[(axis, hend)]


GLASS = {"type": "standard", "name": "Window glass", "color": "#cfe3ea", "roughness": 0.15, "metalness": 0,
         "opacity": 0.22, "transparent": True, "depthWrite": False, "side": "double", "envMapIntensity": 0.3}


def build_shell(spec, prefix_outer='so-', prefix_inner='si-', only=None, **opts):
    parts = []
    if only in (None, 'outer'):
        parts.append(build_outer(spec, prefix_outer, opts))
    if only in (None, 'inner'):
        parts.append(build_inner(spec, prefix_inner, opts))
    b = merge(*parts)
    if opts.get('emit_glass_material'):
        b['resources'].append({'id': 'm-glass', 'kind': 'material', 'data': dict(GLASS)})
    return b


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('spec')
    ap.add_argument('--out', required=True, help='payload directory (emptied of NN-*.json first)')
    ap.add_argument('--prefix-outer', default='so-')
    ap.add_argument('--prefix-inner', default='si-')
    ap.add_argument('--only', choices=['outer', 'inner'])
    ap.add_argument('--bifold-max-projection', type=float, default=0.15,
                    help='default bifold fold: each panel reaches at most this far into the room (m)')
    ap.add_argument('--emit-glass-material', action='store_true',
                    help='also publish m-glass with the default values (roughness 0.15, envMapIntensity 0.3)')
    ap.add_argument('--no-layer-check', action='store_true')
    a = ap.parse_args(argv)
    spec = json.load(open(a.spec))
    if spec.get('draft'):
        print('warning: spec is a measure_plan.py DRAFT; resolve its review list first', file=sys.stderr)
    b = build_shell(spec, a.prefix_outer, a.prefix_inner, a.only,
                    bifold_max_projection=a.bifold_max_projection, emit_glass_material=a.emit_glass_material)
    os.makedirs(a.out, exist_ok=True)
    for f in os.listdir(a.out):
        if re.match(r'^\d+-.*\.json$', f):
            os.remove(os.path.join(a.out, f))
    paths = write_payloads(b, a.out, check=not a.no_layer_check)
    print('%d resources, %d nodes -> %d files in %s' % (len(b['resources']), len(b['nodes']), len(paths), a.out))
    return b


if __name__ == '__main__':
    main()
