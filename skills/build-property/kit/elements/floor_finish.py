"""Floor finishes by room polygon: staggered plank flooring and offset-bond tile, 3 mm deep, no
collision, not raycast (the walk surface stays the shell slab).

planks(spec, rooms, rng, ...) -> bundle   one root per room ('<prefix>pk-<room>') with a joint-shadow
                                          underlay and one instanced mesh per board tone.
tiles(spec, rooms, rng, ...)  -> bundle   one root per room ('<prefix>tl-<room>') with a grout bed and
                                          one instanced mesh per tile tone.
catalogue(...)                -> bundle   plank and tile catalogue entries from researched products
                                          (boxes from area + waste).

Rooms come from spec rooms (rectangle bounds snapped to wall faces, or a 'polygon'). Rows run along x;
each row is clipped to the room polygon, so L-shaped and other rectilinear rooms work. Along an angled
edge, individual boards and tiles are clipped to the room, preserving joints and disconnected pieces
around notches. The underlay follows the room polygon, so diagonal walls get a clean line. The random
generator is passed in so a build can be reproduced (e.g. random.Random(1), planks first, then tiles).

Layering (kit layer check, >= 2 mm between same-facing faces): the underlay/grout bed is a 2 mm box
centred on y=0 (top at y=0.001), the boards/tiles span y 0.001-0.003.

Plank defaults: one light oak base with light and warm boards within about +-4% lightness, weights
0.4/0.3/0.3, long boards 1.2-2.4 m, joints >= 0.3 m from the previous row's joints and no offcut
under 0.3 m (except in rooms narrower than 0.9 m). Pass `mats` (and size constants) to match the
flooring you researched. Room roots carry productKey/catalogueKey only when `product_key` is given.
"""
import math

from elements._common import (Bag, BEH, band_x_intervals, catalogue_entry, is_rect, poly_bounds,
                              product_keys, room_polygons, r4)

from lib.polygons import area as _area, clip_rect, strips

PLANK_L, PLANK_W, PLANK_T = 1.2, 0.188, 0.002
PITCH, GAP, EDGE = 0.19, 0.002, 0.001
TILE_L, TILE_D = 0.6, 0.3
Y_TOP_LAYER = 0.002          # boards / tiles: y 0.001-0.003
Y_BED = 0.0                  # underlay / grout: 2 mm box centred on 0 -> top 0.001
BED_T = 0.002

PLANK_LENGTHS = [1.2, 1.5, 1.8, 1.8, 2.1, 2.4]
PLANK_WEIGHTS = [0.4, 0.3, 0.3]


def r(v, n=3):
    v = round(v + 0.0, n)
    if v == 0:
        return 0
    return int(v) if v == int(v) else v


# An extrude laid flat: rotate -pi/2 about x as an instance matrix. Its entries are exactly 0 and +-1,
# where a node rotation of -pi/2 would round to -1.571 and tilt the piece 0.2 mm per metre from the origin.
FLAT = [1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1]


def mat_scale_t(sx, sy, sz, t):
    return [r(sx), 0, 0, 0, 0, r(sy), 0, 0, 0, 0, r(sz), 0, r4(t[0]), r4(t[1]), r4(t[2]), 1]


def oak_materials(b, prefix='fl-'):
    """Light oak tones (base, warm, light)."""
    return [b.raw_mat(prefix + 'oak-base', {'type': 'standard', 'name': 'Oak plank, base board', 'color': '#ddc9a8', 'roughness': 0.62}),
            b.raw_mat(prefix + 'oak-warm', {'type': 'standard', 'name': 'Oak plank, warm board', 'color': '#d8c29f', 'roughness': 0.62}),
            b.raw_mat(prefix + 'oak-pale', {'type': 'standard', 'name': 'Oak plank, light board', 'color': '#e2d0b0', 'roughness': 0.62})]


def tile_materials(b, prefix='fl-'):
    return [b.raw_mat(prefix + 'tile-beige', {'type': 'standard', 'name': 'Beige matte porcelain tile', 'color': '#d8ccb6', 'roughness': 0.78}),
            b.raw_mat(prefix + 'tile-mottle', {'type': 'standard', 'name': 'Beige porcelain tile, mottled face', 'color': '#cbbea6', 'roughness': 0.8})]


def _angled(poly):
    """True if any edge of the polygon runs at an angle to the x and z axes."""
    return any(abs(a[0] - c[0]) > 1e-6 and abs(a[1] - c[1]) > 1e-6
               for a, c in zip(poly, poly[1:] + poly[:1]))


def _floor_piece(parts, bounds):
    """Clip one board/tile, retaining disconnected pieces and millimetre-safe vertices."""
    pieces = clip_rect(parts, bounds)
    x0, z0, x1, z1 = bounds
    # Keep ordinary pieces instanced, even if a strip boundary crosses the piece.
    if abs(sum(_area(pc) for pc in pieces) - (x1 - x0) * (z1 - z0)) < 1e-9:
        return None
    out = []
    for pc in pieces:
        pc = [(round(x, 3), round(z, 3)) for x, z in pc]
        pc = [p for i, p in enumerate(pc) if p != pc[i - 1]]
        if len(pc) >= 3 and _area(pc) >= 1e-4:
            out.append(pc)
    return out


def _edge_node(b, prefix, root, n, name, mat, poly, y0, depth):
    """One extruded cut piece lying flat from y0 to y0 + depth (extrude points are (x, -z))."""
    g = b.extrude(prefix + 'g-%s-e%d' % (root[len(prefix):], n), [(x, -z) for x, z in poly], depth)
    b.nodes.append({'id': '%s-e%d' % (root, n), 'parentId': root, 'name': name, 'position': [0, r(y0, 4), 0],
                    'render': {'geometry': g, 'materials': [mat, mat], 'instances': [FLAT]},
                    'behavior': BEH('keep'), 'raycast': False})


def _bed(b, prefix, root, name, mat, poly, x0, z0, x1, z1):
    if _angled(poly):     # one flat slab in the room's exact shape, centred on y=0 like the box
        g = b.extrude(prefix + 'g-%s-%s' % (root[len(prefix):], name[0]), [(x, -z) for x, z in poly], BED_T)
        b.nodes.append({'id': root + '-' + name[0], 'parentId': root, 'name': name[1],
                        'position': [0, r(Y_BED - BED_T / 2, 4), 0],
                        'render': {'geometry': g, 'materials': [mat, mat], 'instances': [FLAT]}, 'behavior': BEH('keep'), 'raycast': False})
        return
    g = b.res(prefix + 'g-underlay', 'geometry', {'type': 'box', 'dimensions': [1, BED_T, 1]}, raw_id=True)
    if is_rect(poly):
        inst = [mat_scale_t(x1 - x0, 1, z1 - z0, ((x0 + x1) / 2, 0, (z0 + z1) / 2))]
    else:
        inst = []
        zs = sorted({p[1] for p in poly})
        for za, zb in zip(zs[:-1], zs[1:]):
            for a, c in band_x_intervals(poly, za, zb):
                inst.append(mat_scale_t(c - a, 1, zb - za, ((a + c) / 2, 0, (za + zb) / 2)))
    n = {'id': root + '-' + name[0], 'parentId': root, 'name': name[1], 'render': {'geometry': g, 'materials': [mat], 'instances': inst},
         'behavior': BEH('keep'), 'raycast': False}
    if Y_BED:
        n['position'] = [0, Y_BED, 0]
    b.nodes.append(n)


def _lay_row(rng, x0, x1, prev, lengths, min_stagger, min_piece, narrow):
    for _try in range(200):
        off = rng.uniform(0, 1.2)
        s = x0 + EDGE - off
        pieces, joints = [], []
        while s < x1 - EDGE:
            L = rng.choice(lengths)
            a, c = max(s, x0 + EDGE), min(s + L - GAP, x1 - EDGE)
            if c - a >= 0.05:
                pieces.append((a, c))
            s += L
            if s < x1 - EDGE - 0.02:
                joints.append(s - GAP / 2)
        ok = all(abs(j - p) >= min_stagger for j in joints for p in prev)
        short = any(c - a < min_piece for a, c in pieces) and (x1 - x0) > narrow
        if ok and not short:
            return pieces, joints
    return pieces, joints


def planks(spec, rooms, rng, prefix='fl-', mats=None, weights=PLANK_WEIGHTS, lengths=PLANK_LENGTHS, min_stagger=0.3,
           min_piece=0.3, narrow=0.9, joint_material=None, product_key=None, polygons=None, stats=None):
    """Plank floors for `rooms` (ids, in order: the order drives the random sequence). `mats`: board
    material ids (default: oak_materials); resources for the defaults are included. narrow=0 applies
    the min_piece rule everywhere. product_key: 'floor-plank' when catalogue() gets a plank product."""
    b = Bag('')
    polys = polygons or room_polygons(spec)
    mats = mats or oak_materials(b, prefix)
    M_JOINT = joint_material or b.raw_mat(prefix + 'oak-joint', {'type': 'standard', 'name': 'Plank bevel joint shadow', 'color': '#7e6a52', 'roughness': 0.9})
    G_PLANK = b.res(prefix + 'g-plank', 'geometry', {'type': 'box', 'dimensions': [PLANK_L, PLANK_T, PLANK_W]}, raw_id=True)
    stats = {} if stats is None else stats
    for room in rooms:
        poly = polys[room]
        x0, z0, x1, z1 = poly_bounds(poly)
        root = prefix + 'pk-' + room
        b.nodes.append(dict({'id': root, 'parentId': None, 'name': 'Plank floor (%s)' % room,
                             'container': 'shell', 'roomId': room}, **product_keys(prefix, product_key)))
        _bed(b, prefix, root, ('u', 'Plank joint underlay'), M_JOINT, poly, x0, z0, x1, z1)
        by_mat = {m: [] for m in mats}
        prev, n_rows, z, area, cuts = [], 0, z0 + EDGE, 0.0, []
        angled = _angled(poly)
        parts = strips(poly) if angled else []
        while z < z1 - EDGE - 0.03:
            za, zb = z, min(z + PLANK_W, z1 - EDGE)
            row_joints = []
            for ia, ib in ([(x0, x1)] if angled else band_x_intervals(poly, za, zb)):
                pieces, joints = _lay_row(rng, ia, ib, prev, lengths, min_stagger, min_piece, narrow)
                row_joints += joints
                for a, c in pieces:
                    m = rng.choices(mats, weights)[0]
                    clipped = _floor_piece(parts, (a, za, c, zb)) if angled else None
                    if clipped is None:
                        by_mat[m].append(mat_scale_t((c - a) / PLANK_L, 1, (zb - za) / PLANK_W, ((a + c) / 2, 0, (za + zb) / 2)))
                        area += (c - a) * (zb - za)
                    else:
                        cuts += [(pc, m) for pc in clipped]
            prev = row_joints
            z += PITCH
            n_rows += 1
        for i, m in enumerate(mats):
            if by_mat[m]:
                assert len(by_mat[m]) <= 512, room
                b.nodes.append({'id': '%s-%d' % (root, i + 1), 'parentId': root, 'name': 'Planks (%s)' % m,
                                'position': [0, Y_TOP_LAYER, 0],
                                'render': {'geometry': G_PLANK, 'materials': [m], 'instances': by_mat[m]},
                                'behavior': BEH('keep'), 'raycast': False})
        for k, (pc, m) in enumerate(cuts):
            _edge_node(b, prefix, root, k + 1, 'Plank cut to the wall', m, pc, Y_TOP_LAYER - PLANK_T / 2, PLANK_T)
            area += _area(pc)
        stats[room] = {'rows': n_rows, 'pieces': sum(len(v) for v in by_mat.values()) + len(cuts), 'area': area}
    return b.bundle()


def tiles(spec, rooms, rng, prefix='fl-', mats=None, p_first=0.65, grout_material=None, joint=0.005,
          offset_fraction=1 / 3, product_key=None, polygons=None, stats=None):
    """Offset running-bond tile (600 x 300) per room with a grout bed showing through `joint`."""
    b = Bag('')
    polys = polygons or room_polygons(spec)
    mats = mats or tile_materials(b, prefix)
    M_GROUT = grout_material or b.raw_mat(prefix + 'grout', {'type': 'standard', 'name': 'Warm grey floor grout', 'color': '#9e9483', 'roughness': 0.95})
    G_TILE = b.res(prefix + 'g-tile', 'geometry', {'type': 'box', 'dimensions': [TILE_L, 0.002, TILE_D]}, raw_id=True)
    TPX, TPZ = TILE_L + joint, TILE_D + joint
    stats = {} if stats is None else stats
    for room in rooms:
        poly = polys[room]
        x0, z0, x1, z1 = poly_bounds(poly)
        root = prefix + 'tl-' + room
        b.nodes.append(dict({'id': root, 'parentId': None, 'name': 'Tile floor (%s)' % room,
                             'container': 'shell', 'roomId': room}, **product_keys(prefix, product_key)))
        _bed(b, prefix, root, ('g', 'Grout bed'), M_GROUT, poly, x0, z0, x1, z1)
        by_mat = {m: [] for m in mats}
        i, z, area, cuts = 0, z0 + 0.002, 0.0, []
        angled = _angled(poly)
        parts = strips(poly) if angled else []
        while z < z1 - 0.022:
            za, zb = z, min(z + TILE_D, z1 - 0.002)
            for ia, ib in ([(x0, x1)] if angled else band_x_intervals(poly, za, zb)):
                s = ia + 0.002 - (i % 3) * (TPX * offset_fraction)
                while s < ib - 0.022:
                    a, c = max(s, ia + 0.002), min(s + TILE_L, ib - 0.002)
                    if c - a >= 0.02:
                        m = mats[0] if rng.random() < p_first else mats[1]
                        clipped = _floor_piece(parts, (a, za, c, zb)) if angled else None
                        if clipped is None:
                            by_mat[m].append(mat_scale_t((c - a) / TILE_L, 1, (zb - za) / TILE_D, ((a + c) / 2, 0, (za + zb) / 2)))
                            area += (c - a) * (zb - za)
                        else:
                            cuts += [(pc, m) for pc in clipped]
                    s += TPX
            z += TPZ
            i += 1
        for j, m in enumerate(mats):
            if not by_mat[m]:
                continue
            b.nodes.append({'id': '%s-%d' % (root, j + 1), 'parentId': root, 'name': 'Tiles (%s)' % m,
                            'position': [0, Y_TOP_LAYER, 0],
                            'render': {'geometry': G_TILE, 'materials': [m], 'instances': by_mat[m]},
                            'behavior': BEH('keep'), 'raycast': False})
        for k, (pc, m) in enumerate(cuts):
            _edge_node(b, prefix, root, k + 1, 'Tile cut to the wall', m, pc, Y_TOP_LAYER - 0.001, 0.002)
            area += _area(pc)
        stats[room] = {'rows': i, 'pieces': sum(len(v) for v in by_mat.values()) + len(cuts), 'area': area}
    return b.bundle()


def room_area(spec, rooms, polygons=None):
    polys = polygons or room_polygons(spec)
    tot = 0.0
    for rm in rooms:
        p = polys[rm]
        tot += abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))) / 2
    return tot


def catalogue(spec, plank_rooms, tile_rooms, prefix='fl-', plank_product=None, tile_product=None, waste=0.10,
              plank_room_label=None, tile_room_label=None, plank_fit=None, tile_fit=None):
    """Catalogue entries '<prefix>floor-plank' / '<prefix>floor-tile' for researched products. With a
    product's `unit_m2` (coverage per box) the quantity is area + waste in whole boxes."""
    b = Bag('')
    for P, rooms, key, label, fit, default in (
            (plank_product, plank_rooms, 'floor-plank', plank_room_label, plank_fit,
             'Staggered rows, three tonal boards; modelled 3 mm thick at floor level.'),
            (tile_product, tile_rooms, 'floor-tile', tile_room_label, tile_fit,
             '1/3-offset running bond with a 5 mm grout joint.')):
        if not rooms or P is None:
            continue
        area = room_area(spec, rooms)
        qty = math.ceil(area * (1 + waste) / P['unit_m2']) if P.get('unit_m2') else 1
        b.product(catalogue_entry(prefix + key, label or ', '.join(rooms), P, qty,
                                  fit or '%.1f m2 + %d%% waste = %d units. %s' % (area, round(waste * 100), qty, default)))
    return b.bundle()
