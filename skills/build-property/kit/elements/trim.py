"""Interior trim: door casings (both faces), doorway floor infills, baseboards, ceilings, accent wall.

openings(spec, ...) -> [(opening id, [(side sign, room id | 'exterior')], infill material | None)]
    Doors and bifolds from the spec with the room on each face found by probing 50 mm off the wall
    (side -1 = the lower-coordinate face). Infill material by floor kinds: plank|plank -> plank
    material, plank|tile -> saddle, tile|tile -> tile, exterior -> none.
casings_and_baseboards(spec, ops, ...) -> bundle
    Colonial-profile casing legs + heads on both faces of every opening (heavier 1.4x on the exterior
    face, head 8 mm higher there so an out-swinging leaf clears it), doorway infills, and colonial
    baseboards along every room wall face minus casings and `skip` runs (built-ins, tubs, machines).
ceilings(spec, rooms, ...) -> bundle   one flat 10 mm ceiling per room (cutaway hide).
accent_wall(spec, room, side, ...) -> bundle   painted wall layer around the openings on that wall
    plus a board-and-batten wainscot with cap rail (cutaway reduce, head piece hide).
catalogue(prefix, products, stats) -> bundle   catalogue entries for the researched products you pass
    ({'paint-walls': {...}, 'paint-accent': {...}, 'baseboard': {...}, 'door-casing': {...}}).

Nodes carry productKey/catalogueKey only for the keys you name (baseboard_key, casing_key,
paint_key), so a build without researched trim products still validates.
"""
import math

from elements._common import (BEH, Bag, catalogue_entry, faces, footprint_polygon, is_rect, point_in_poly, poly_bounds,
                              product_keys, r4,
                              room_polygons, subtract)
from elements.floor_finish import FLAT, PLANK_L, PLANK_W, Y_TOP_LAYER, mat_scale_t, r

CAS_W, CAS_T = 0.083, 0.0175
CAS_PROFILE = [[0, 0], [0.083, 0], [0.083, 0.0155], [0.0815, 0.0172], [0.079, 0.0175], [0.072, 0.0175],
               [0.069, 0.016], [0.0665, 0.0135], [0.06, 0.0125], [0.004, 0.011], [0.0015, 0.0105], [0, 0.009]]
BASE_PROFILE = [[0, 0.003], [0.0143, 0.003], [0.0143, 0.112], [0.0125, 0.118], [0.0095, 0.121],
                [0.0095, 0.126], [0.007, 0.131], [0.004, 0.1345], [0, 0.136]]
BB_T, BB_INSET, BB_H = 0.0143, 0.0143, 0.136

def det3(a, b, c):
    return (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]))


def basis_matrix(ex, ey, ez, sx, sy, sz, t):
    assert det3(ex, ey, ez) > 0
    cols = [[v * sx for v in ex], [v * sy for v in ey], [v * sz for v in ez]]
    m = []
    for c in cols:
        m += [r(c[0]), r(c[1]), r(c[2]), 0]
    return m + [r4(t[0]), r4(t[1]), r4(t[2]), 1]


def extrude_run(ex, ey, along, a0, a1, base_pt, sx=1, sy=1):
    """Instance matrix for a depth-1 extrude whose profile spans (ex, ey) and whose extrusion runs along
    world axis `along` between a0 < a1. base_pt(a) is the world anchor for along-coordinate a."""
    ez, start = along, a0
    if det3(ex, ey, ez) < 0:
        ez, start = [-v for v in along], a1
    return basis_matrix(ex, ey, ez, sx, sy, a1 - a0, base_pt(start))


def wall_geom(spec, w):
    """(axis of the wall run 'x'|'z', centre line, lower face, upper face)."""
    if 'center_z' in w:
        h = w['thickness'] / 2
        return 'x', w['center_z'], w['center_z'] - h, w['center_z'] + h
    if 'center_x' in w:
        h = w['thickness'] / 2
        return 'z', w['center_x'], w['center_x'] - h, w['center_x'] + h
    f = faces(spec)
    face = next(x for x in f.values() if x.wall is w)
    inner = w['inner_z'] if face.axis == 'x' else w['inner_x']
    lo, hi = sorted((inner, face.plane))
    return face.axis, (lo + hi) / 2, lo, hi


def _room_at(polys, fp, x, z):
    for rid, p in polys.items():
        if point_in_poly(x, z, p):
            return rid
    return None if point_in_poly(x, z, fp) else 'exterior'


def openings(spec, plank_rooms, tile_rooms, plank_mat, saddle_mat='m-quartz', tile_mat=None, types=('door', 'bifold'),
             polys=None, probe=0.05):
    polys = polys or room_polygons(spec)
    fp = footprint_polygon(spec)
    kind = {rm: 'plank' for rm in plank_rooms}
    kind.update({rm: 'tile' for rm in tile_rooms})
    out = []
    for group in ('interiorWalls', 'outerWalls'):
        for w in spec[group]:
            axis, c, flo, fhi = wall_geom(spec, w)
            for o in w['openings']:
                if o['type'] not in types:
                    continue
                mid = (o['from'] + o['to']) / 2
                sides = []
                for sign, face in ((-1, flo), (1, fhi)):
                    p = face + sign * probe
                    room = _room_at(polys, fp, mid, p) if axis == 'x' else _room_at(polys, fp, p, mid)
                    if room:
                        sides.append((sign, room))
                ks = sorted(kind.get(rm, 'exterior' if rm == 'exterior' else 'other') for _, rm in sides)
                if 'exterior' in ks or 'other' in ks or len(ks) < 2:
                    infill = None
                elif ks == ['plank', 'plank']:
                    infill = plank_mat
                elif ks == ['tile', 'tile']:
                    infill = tile_mat
                else:
                    infill = saddle_mat
                out.append((o['id'], sides, infill))
    return out


def _find(spec, oid):
    for group in ('interiorWalls', 'outerWalls'):
        for w in spec[group]:
            for o in w['openings']:
                if o['id'] == oid:
                    return w, o
    raise KeyError(oid)


def casings_and_baseboards(spec, ops, rooms, prefix='fl-', trim_mat='m-trim', skip=None, accent_offset=None,
                           door_head=None, exterior_scale=1.4, exterior_head_lift=0.008, polys=None, stats=None,
                           baseboard_key=None, casing_key=None):
    """ops: openings(); rooms: rooms that get baseboards (ordered). skip: {(room, 'N'|'S'|'W'|'E'):
    [(a, b)]} along-wall runs without baseboard. accent_offset: {(room, side): layer thickness}."""
    b = Bag('')
    P = prefix
    polys = polys or room_polygons(spec)
    skip = skip or {}
    accent_offset = accent_offset or {}
    stats = {} if stats is None else stats
    G_BASE = b.res(P + 'g-baseboard', 'geometry', {'type': 'extrude', 'points': BASE_PROFILE, 'depth': 1}, raw_id=True)
    G_CAS = b.res(P + 'g-casing', 'geometry', {'type': 'extrude', 'points': CAS_PROFILE, 'depth': 1}, raw_id=True)
    G_PLANK = b.res(P + 'g-plank', 'geometry', {'type': 'box', 'dimensions': [PLANK_L, 0.002, PLANK_W]}, raw_id=True)
    x0f, z0f, x1f, z1f = poly_bounds(footprint_polygon(spec))

    def room_extent(room, axis):
        if room == 'exterior':
            return (x0f, x1f) if axis == 'x' else (z0f, z1f)
        x0, z0, x1, z1 = poly_bounds(polys[room])
        return (x0, x1) if axis == 'x' else (z0, z1)

    casing_cut, leg_inst, head_inst, infill_inst = {}, [], [], {}
    cas_len = 0.0
    for oid, sides, infill in ops:
        w, o = _find(spec, oid)
        axis, c, flo, fhi = wall_geom(spec, w)
        head = o.get('head', door_head or spec['heights']['door'])
        rev = 0.004 if o['type'] == 'bifold' else 0.0
        along = [1, 0, 0] if axis == 'x' else [0, 0, 1]
        for sign, room in sides:
            face = flo if sign < 0 else fhi
            n = [0, 0, sign] if axis == 'x' else [sign, 0, 0]
            ext_lo, ext_hi = room_extent(room, axis)
            sy = exterior_scale if room == 'exterior' else 1.0
            head_y = head + (exterior_head_lift if room == 'exterior' else 0.0)
            widths = []
            for end, dirn in ((o['from'] - rev, -1), (o['to'] + rev, 1)):
                outer = end + dirn * CAS_W
                lo, hi = (outer, end) if dirn < 0 else (end, outer)
                lo, hi = max(lo, ext_lo), min(hi, ext_hi)
                wdt = hi - lo
                if wdt < 0.03:
                    widths.append(0)
                    continue
                widths.append(wdt)
                ex = [v * dirn for v in along]

                def pt_leg(y, end=end, face=face):
                    return [end, y, face] if axis == 'x' else [face, y, end]
                y0, y1 = 0.003, head_y
                if det3(ex, n, [0, 1, 0]) > 0:
                    m = basis_matrix(ex, n, [0, 1, 0], wdt / CAS_W, sy, y1 - y0, pt_leg(y0))
                else:
                    m = basis_matrix(ex, n, [0, -1, 0], wdt / CAS_W, sy, y1 - y0, pt_leg(y1))
                leg_inst.append(m)
                cas_len += y1 - y0
            a0 = o['from'] - rev - widths[0]
            a1 = o['to'] + rev + widths[1]

            def pt_head(a, face=face, head_y=head_y):
                return [a, head_y, face] if axis == 'x' else [face, head_y, a]
            head_inst.append(extrude_run([0, 1, 0], n, along, a0, a1, pt_head, sy=sy))
            cas_len += a1 - a0
            if room != 'exterior':
                casing_cut.setdefault((room, axis, round(face, 4)), []).append((a0, a1))
        if infill:
            span = o['to'] - o['from']
            if axis == 'x':
                m = mat_scale_t(span / PLANK_L, 1, (fhi - flo) / PLANK_W, ((o['from'] + o['to']) / 2, 0, c))
            else:
                m = mat_scale_t((fhi - flo) / PLANK_L, 1, span / PLANK_W, (c, 0, (o['from'] + o['to']) / 2))
            infill_inst.setdefault(infill, []).append(m)

    # baseboards
    base_nodes, bb_len = {}, 0.0
    for room in rooms:
        x0, z0, x1, z1 = poly_bounds(polys[room])
        sides = {'N': ('x', z0, [0, 0, 1], (x0, x1)), 'S': ('x', z1, [0, 0, -1], (x0, x1)),
                 'W': ('z', x0, [1, 0, 0], (z0 + BB_INSET, z1 - BB_INSET)),
                 'E': ('z', x1, [-1, 0, 0], (z0 + BB_INSET, z1 - BB_INSET))}
        # x-runs start clear of an accent layer on the N/S walls
        for key in ('W', 'E'):
            ax_, f_, n_, (a_, b_) = sides[key]
            a_ += accent_offset.get((room, 'N'), 0)
            b_ -= accent_offset.get((room, 'S'), 0)
            sides[key] = (ax_, f_, n_, (a_, b_))

        def corner_inset(zface, xface, room=room):
            cuts = casing_cut.get((room, 'x', round(zface, 4)), [])
            return 0.019 if any(a <= xface + 0.001 and bb >= xface - 0.001 for a, bb in cuts) else 0
        for key, xf in (('W', x0), ('E', x1)):
            ax_, f_, n_, (a_, b_) = sides[key]
            a_ = max(a_, z0 + corner_inset(z0, xf))
            b_ = min(b_, z1 - corner_inset(z1, xf))
            sides[key] = (ax_, f_, n_, (a_, b_))
        inst = []
        for side, (axis, face, n, (a, bb)) in sides.items():
            off = accent_offset.get((room, side), 0)
            fpos = face + off * (n[0] + n[2])
            cuts = list(casing_cut.get((room, axis, round(face, 4)), [])) + skip.get((room, side), [])
            along = [1, 0, 0] if axis == 'x' else [0, 0, 1]
            for s, e in subtract((a, bb), cuts, 0.04):
                def pt(v, fpos=fpos, axis=axis):
                    return [v, 0, fpos] if axis == 'x' else [fpos, 0, v]
                inst.append(extrude_run(n, [0, 1, 0], along, s, e, pt))
                bb_len += e - s
        base_nodes[room] = inst
    stats.update(baseboard_length=bb_len, casing_length=cas_len)

    b.nodes.append(dict({'id': P + 'bb', 'parentId': None, 'name': 'Baseboards', 'container': 'shell'},
                        **product_keys(P, baseboard_key)))
    for room, inst in base_nodes.items():
        if inst:
            b.nodes.append({'id': P + 'bb-' + room, 'parentId': P + 'bb', 'name': 'Baseboard (%s)' % room,
                            'render': {'geometry': G_BASE, 'materials': [trim_mat], 'instances': inst},
                            'behavior': BEH('keep'), 'raycast': False, 'roomId': room})
    b.nodes.append(dict({'id': P + 'cas', 'parentId': None, 'name': 'Door casings', 'container': 'shell'},
                        **product_keys(P, casing_key)))
    for nid, nm, inst in (('cas-legs', 'Casing legs', leg_inst), ('cas-heads', 'Casing heads', head_inst)):
        b.nodes.append({'id': P + nid, 'parentId': P + 'cas', 'name': nm,
                        'render': {'geometry': G_CAS, 'materials': [trim_mat], 'instances': inst},
                        'behavior': BEH('hide'), 'raycast': False})
    b.nodes.append({'id': P + 'door-infill', 'parentId': None, 'name': 'Doorway floor infills and saddles',
                    'container': 'shell'})
    for i, (mat, inst) in enumerate(infill_inst.items()):
        b.nodes.append({'id': '%sdoor-infill-%d' % (P, i + 1), 'parentId': P + 'door-infill',
                        'name': 'Doorway infill (%s)' % mat, 'position': [0, Y_TOP_LAYER, 0],
                        'render': {'geometry': G_PLANK, 'materials': [mat], 'instances': inst},
                        'behavior': BEH('keep'), 'raycast': False})
    return b.bundle()


def ceilings(spec, rooms, prefix='fl-', paint='m-paint', height=None, polys=None, paint_key=None):
    b = Bag('')
    P = prefix
    polys = polys or room_polygons(spec)
    CEIL = spec['ceilingHeight'] if height is None else height
    G = b.res(P + 'g-ceiling', 'geometry', {'type': 'box', 'dimensions': [1, 0.01, 1]}, raw_id=True)
    b.nodes.append(dict({'id': P + 'ceil', 'parentId': None, 'name': 'Ceilings', 'container': 'shell'},
                        **product_keys(P, paint_key)))
    for room in rooms:
        poly = polys[room]
        if not is_rect(poly):     # L-shaped or angled room: a flat slab in the room's exact shape
            g = b.extrude(P + 'g-ceiling-' + room, [(x, -z) for x, z in poly], 0.01)
            b.nodes.append({'id': P + 'ceil-' + room, 'parentId': P + 'ceil', 'name': 'Ceiling (%s)' % room,
                            'position': [0, r4(CEIL), 0],
                            'render': {'geometry': g, 'materials': [paint, paint], 'instances': [FLAT]},
                            'behavior': BEH('hide'), 'raycast': False, 'roomId': room})
            continue
        x0, z0, x1, z1 = poly_bounds(poly)
        b.nodes.append({'id': P + 'ceil-' + room, 'parentId': P + 'ceil', 'name': 'Ceiling (%s)' % room,
                        'position': [0, r4(CEIL + 0.005), 0],
                        'render': {'geometry': G, 'materials': [paint],
                                   'instances': [mat_scale_t(x1 - x0, 1, z1 - z0, ((x0 + x1) / 2, 0, (z0 + z1) / 2))]},
                        'behavior': BEH('hide'), 'raycast': False, 'roomId': room})
    return b.bundle()


def accent_wall(spec, room, side='N', prefix='fl-', color='#b4b69f', name='Accent paint, eggshell',
                title='Accent wall: board-and-batten', layer_t=0.004, battens=10, bat_w=0.07, bat_t=0.012,
                wainscot=(0.136, 0.72), cap=(0.72, 0.78), cap_t=0.022, height=None, polys=None, ids=None,
                paint_key=None):
    """Painted layer + board-and-batten wainscot on the room's N or S wall, cut around that wall's
    openings (window sills/heads get their own pieces). Returns the bundle; the layer thickness is
    what casings_and_baseboards(accent_offset=...) needs."""
    assert side in ('N', 'S'), 'accent walls on x-axis walls only'
    b = Bag('')
    P = prefix
    polys = polys or room_polygons(spec)
    CEIL = spec['ceilingHeight'] if height is None else height
    x0, z0, x1, z1 = poly_bounds(polys[room])
    zf = z0 if side == 'N' else z1
    sgn = 1 if side == 'N' else -1
    M_ACC = b.raw_mat(P + 'accent-paint', {'type': 'standard', 'name': name, 'color': color, 'roughness': 0.85})
    wins = []
    for group in ('outerWalls', 'interiorWalls'):
        for w in spec[group]:
            if w['axis'] != 'x':
                continue
            lo, hi = (min(w['inner_z'], w['line_z_outer']), max(w['inner_z'], w['line_z_outer'])) if 'inner_z' in w \
                else (w['center_z'] - w['thickness'] / 2, w['center_z'] + w['thickness'] / 2)
            if abs(lo - zf) < 0.002 or abs(hi - zf) < 0.002:
                wins += [o for o in w['openings'] if o['to'] > x0 and o['from'] < x1]
    wins.sort(key=lambda o: o['from'])
    pieces = []          # (id, a, b, y0, y1, cutaway)
    full = subtract((x0, x1), [(o['from'], o['to']) for o in wins])
    names = ids or {}
    for i, (a, c) in enumerate(full):
        default = ('west' if i == 0 else 'east') if len(full) == 2 else str(i + 1)
        pieces.append((P + 'acc-' + names.get(i, default), a, c, 0, CEIL, 'reduce'))
    for j, o in enumerate(wins):
        sfx = '' if len(wins) == 1 else '-%d' % (j + 1)
        if o.get('sill', 0) > 0:
            pieces.append((P + 'acc-sill' + sfx, o['from'], o['to'], 0, o['sill'], 'reduce'))
        pieces.append((P + 'acc-head' + sfx, o['from'], o['to'], o['head'], CEIL, 'hide'))
    b.nodes.append(dict({'id': P + 'acc', 'parentId': None, 'name': title, 'container': 'shell', 'roomId': room},
                        **product_keys(P, paint_key)))
    for pid, a, c, y0, y1, cut in pieces:
        gid = b.res(P + 'g-acc-%d-%d' % (round((c - a) * 1000), round((y1 - y0) * 1000)), 'geometry',
                    {'type': 'box', 'dimensions': [r4(c - a), r4(y1 - y0), layer_t]}, raw_id=True)
        b.nodes.append({'id': pid, 'parentId': P + 'acc', 'name': 'Accent paint layer',
                        'position': [r4((a + c) / 2), r4((y0 + y1) / 2), r4(zf + sgn * layer_t / 2)],
                        'render': {'geometry': gid, 'materials': [M_ACC]}, 'behavior': BEH(cut), 'raycast': False})
    BY0, BY1 = wainscot
    G_BAT = b.res(P + 'g-batten', 'geometry', {'type': 'box', 'dimensions': [bat_w, r(BY1 - BY0), bat_t]}, raw_id=True)
    G_CAP = b.res(P + 'g-caprail', 'geometry', {'type': 'box', 'dimensions': [r4(x1 - x0), r(cap[1] - cap[0]), cap_t]}, raw_id=True)
    bx = [x0 + bat_w / 2 + i * ((x1 - x0 - bat_w) / (battens - 1)) for i in range(battens)]
    zb = zf + sgn * (layer_t + bat_t / 2)
    b.nodes.append({'id': P + 'acc-battens', 'parentId': P + 'acc', 'name': 'Battens',
                    'render': {'geometry': G_BAT, 'materials': [M_ACC],
                               'instances': [mat_scale_t(1, 1, 1, (x, (BY0 + BY1) / 2, zb)) for x in bx]},
                    'behavior': BEH('keep'), 'raycast': False})
    b.nodes.append({'id': P + 'acc-cap', 'parentId': P + 'acc', 'name': 'Cap rail',
                    'position': [r4((x0 + x1) / 2), r4((cap[0] + cap[1]) / 2), r4(zf + sgn * (layer_t + cap_t / 2))],
                    'render': {'geometry': G_CAP, 'materials': [M_ACC]}, 'behavior': BEH('keep'), 'raycast': False})
    return b.bundle()


def catalogue(prefix='fl-', products=None, stats=None, rooms=None, fits=None):
    """Catalogue records for researched trim products: products = {key: product dict} with keys such
    as 'paint-walls', 'paint-accent', 'baseboard', 'door-casing' (model key '<prefix><key>').
    With `stats` from casings_and_baseboards and the product's `unit_length_m`, baseboard pieces
    (+10%) and casing lengths (+5%) are computed; otherwise the product's `qty` (default 1) is used.
    `rooms` / `fits` override the room label and fit note per key."""
    b = Bag('')
    for k, prod in (products or {}).items():
        qty = prod.get('qty', 1)
        unit = prod.get('unit_length_m')
        if stats and unit and k == 'baseboard':
            qty = math.ceil(stats['baseboard_length'] * 1.1 / unit)
        if stats and unit and k == 'door-casing':
            qty = math.ceil(stats['casing_length'] * 1.05 / unit)
        b.product(catalogue_entry(prefix + k, (rooms or {}).get(k, prod.get('room', 'Whole house')), prod, qty,
                                  (fits or {}).get(k)))
    return b.bundle()
