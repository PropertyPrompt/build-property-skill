"""Board-and-batten siding details for the outer walls of a spec.

casings(spec, ...)  -> (bundle, exclusions): black exterior casing for every outer-wall opening
                       (picture frame with a sill band for windows, U-frame for doors), a drip cap
                       over each, and a sill nose under each window. `exclusions` are the batten
                       cut-outs {face key: [(s0, s1, y0, y1)]} the casings need.
battens(spec, roof_geom, exclusions, ...) -> bundle: one instanced batten mesh per outer face at
                       ~406 mm (16 in) centres, cut around every exclusion, up to the soffit on eave
                       faces and up under the rake on gable faces (RoofGeom.batten_top).
plinth(spec, gaps, ...) -> bundle: 40 mm concrete plinth from grade to floor on every face, split
                       around porch slabs / landings given in `gaps`.
catalogue(prefix, siding_product, batten_product, ...) -> bundle with the siding-panel and batten
                       catalogue entries, from products the caller researched (either may be None).

Faces are keyed N/S/W/E for walls named north/south/west/east, otherwise by wall id. Any
axis-aligned outer wall list works for casings and battens; plinth() assumes a rectangle.
Dimensions are typical board-and-batten values; override the module constants or arguments to match
the product you chose.
"""
import math

from elements._common import Bag, HIDE, KEEP, M, catalogue_entry, faces, footprint_rect, libmat, product_keys

TRIM = 0.09                      # casing width
CAS_D = 0.025                    # casing projection
BAT_W, BAT_D = 0.0635, 0.019     # batten 63.5 x 19 mm (2.5 x 0.75 in)
BAT_PITCH = 0.406                # 406 mm (16 in) centres


def casings(spec, prefix='ex-', materials=None, openings=None):
    """Exterior casings for outer-wall openings (all of them, or the ids in `openings`)."""
    b = Bag(prefix)
    M_BLACK = libmat(materials, 'black')
    F = faces(spec)
    excl = {k: [] for k in F}
    b.node('casings', None, [0, 0, 0], 'Exterior window and door casings', container='shell')
    for f in F.values():
        for o in f.wall['openings']:
            if openings is not None and o['id'] not in openings:
                continue
            a, c = o['from'], o['to']
            W = c - a
            s0, s1 = a - TRIM, c + TRIM
            oid = o['id']
            window = o['type'] == 'window'
            if window:
                sill, head = o['sill'], o['head']
                outer = [(0, sill - 0.05), (W + 2 * TRIM, sill - 0.05), (W + 2 * TRIM, head + TRIM), (0, head + TRIM)]
                hole = [(TRIM, sill), (TRIM, head), (TRIM + W, head), (TRIM + W, sill)]
                g = b.extrude(f'g-cas-{int(round(W * 1000))}x{int(round((head - sill) * 1000))}', outer, CAS_D, [hole])
                top, bot = head + TRIM, sill - 0.05
            else:
                h1 = o['head'] + 0.01
                pts = [(0, 0), (TRIM, 0), (TRIM, h1), (TRIM + W, h1), (TRIM + W, 0), (W + 2 * TRIM, 0),
                       (W + 2 * TRIM, h1 + TRIM), (0, h1 + TRIM)]
                g = b.extrude(f'g-cas-door-{int(round(W * 1000))}', pts, CAS_D)
                top, bot = h1 + TRIM, 0.0
            root = f'cas-{oid}'
            b.mesh(root, 'casings', f.origin(s0, s1), f'Casing {oid}', g, [M_BLACK, M_BLACK], rot=f.yaw())
            Wt = W + 2 * TRIM
            g_drip = b.box(f'g-drip-{int(round(Wt * 1000))}', [Wt + 0.04, 0.02, 0.04])
            b.mesh(root + '-drip', root, [Wt / 2, top + 0.01, 0.02], 'Drip cap', g_drip, M_BLACK)
            y_lo = -1.0
            if window:
                g_sn = b.box(f'g-sill-{int(round(Wt * 1000))}', [Wt + 0.04, 0.035, 0.045])
                b.mesh(root + '-sill', root, [Wt / 2, bot - 0.0175, 0.0225], 'Sill nose', g_sn, M_BLACK)
                y_lo = bot - 0.035
            excl[f.key].append((s0 - 0.02, s1 + 0.02, y_lo, top + 0.02))
    return b.bundle(), excl


def battens(spec, roof_geom, exclusions, prefix='ex-', materials=None, pitch=BAT_PITCH, min_len=0.06, stats=None,
            product_key=None):
    """Instanced battens per outer face. `exclusions`: {face key: [(s0, s1, y0, y1)]} in world
    along-wall coordinates (casings(), wall lights, house numbers, porch ledgers ...). Pass a dict as
    `stats` to receive the batten count and total length (for catalogue()). `product_key` ('batten'
    when catalogue() gets a batten product) tags the batten nodes."""
    b = Bag(prefix)
    P = prefix
    M_SIDING = libmat(materials, 'siding')
    g_bat = b.box('g-batten', [BAT_W, 1, BAT_D])
    stats = {} if stats is None else stats
    stats.update(count=0, length=0.0)
    for key, f in faces(spec).items():
        n_int = round((f.L - BAT_W) / pitch)
        step = (f.L - BAT_W) / n_int
        inst = []
        for k in range(n_int + 1):
            c = f.lo + BAT_W / 2 + k * step
            b0, b1 = c - BAT_W / 2, c + BAT_W / 2
            segs = [(0.0, roof_geom.batten_top(f, b0, b1))]
            for (e0, e1, y0, y1) in exclusions.get(key, []):
                if e1 <= b0 or e0 >= b1:
                    continue
                new = []
                for (a, bb) in segs:
                    if y1 <= a or y0 >= bb:
                        new.append((a, bb))
                        continue
                    if y0 > a:
                        new.append((a, y0))
                    if y1 < bb:
                        new.append((y1, bb))
                segs = new
            for (a, bb) in segs:
                if bb - a < min_len:
                    continue
                inst.append(M(f.lx(c, f.lo, f.hi), (a + bb) / 2, BAT_D / 2, 1, bb - a, 1))
                stats['count'] += 1
                stats['length'] += bb - a
        b.mesh(f'battens-{key.lower()}', None, f.origin(f.lo, f.hi), f'Board-and-batten battens ({key})', g_bat,
               M_SIDING, rot=f.yaw(), instances=inst, container='shell', **product_keys(P, product_key))
    return b.bundle()


def plinth(spec, gaps=None, prefix='ex-', materials=None, thickness=0.04, grade=None):
    """Concrete plinth on a rectangular footprint. `gaps`: {face key: [(s0, s1)]} where a slab or
    landing meets the wall. North/south runs wrap the corners; west/east runs stop at them."""
    b = Bag(prefix)
    M_CONC = libmat(materials, 'concrete')
    GRADE = spec.get('grade', {}).get('y', -0.30) if grade is None else grade
    PL = thickness
    x0, z0, x1, z1 = footprint_rect(spec)
    gaps = gaps or {}
    b.node('plinth', None, [0, 0, 0], 'Concrete plinth', container='shell')
    for key, f in faces(spec).items():
        if f.axis == 'x':
            run = (x0 - PL, x1 + PL)
        else:
            run = (z0, z1)
        pieces, s = [], run[0]
        for g0, g1 in sorted(gaps.get(key, [])):
            pieces.append((s, g0))
            s = g1
        pieces.append((s, run[1]))
        for i, (a, c) in enumerate(pieces):
            pid = key.lower() + (str(i + 1) if len(pieces) > 1 else '')
            o0, o1 = (f.plane, f.plane + f.sign * PL)
            lo_o, hi_o = min(o0, o1), max(o0, o1)
            if f.axis == 'x':
                bb = (a, c, GRADE, 0, lo_o, hi_o)
            else:
                bb = (lo_o, hi_o, GRADE, 0, a, c)
            g = b.box(f'g-plinth-{pid}', [bb[1] - bb[0], bb[3] - bb[2], bb[5] - bb[4]])
            b.mesh(f'plinth-{pid}', 'plinth', [(bb[0] + bb[1]) / 2, (bb[2] + bb[3]) / 2, (bb[4] + bb[5]) / 2],
                   'Plinth', g, M_CONC, beh=KEEP)
    return b.bundle()


def wall_area(spec, roof_geom, net=True):
    """Outer-wall siding area: walls to the plate plus gable triangles, less openings when `net`."""
    x0, z0, x1, z1 = footprint_rect(spec)
    per = 2 * ((x1 - x0) + (z1 - z0))
    area = per * roof_geom.PH + roof_geom.LV * roof_geom.RISE
    if net:
        for w in spec['outerWalls']:
            for o in w['openings']:
                area -= (o['to'] - o['from']) * (o['head'] - o.get('sill', 0))
    return area


def catalogue(prefix='ex-', siding_product=None, batten_product=None, siding_qty=None, batten_qty=None,
              siding_fit=None, batten_fit=None, spec=None, roof_geom=None, batten_length=None):
    """Catalogue entries for researched products (see elements._common.catalogue_entry). Model keys
    are '<prefix>siding-panel' and '<prefix>batten'. Quantities default to estimates: siding = wall
    area + 10% over the product's `unit_m2`; battens = modelled run + 10% over `unit_length_m`."""
    b = Bag(prefix)
    if siding_product is not None:
        if siding_qty is None and spec is not None and roof_geom is not None and siding_product.get('unit_m2'):
            siding_qty = math.ceil(wall_area(spec, roof_geom) * 1.1 / siding_product['unit_m2'])
        b.product(catalogue_entry(prefix + 'siding-panel', 'Exterior', siding_product, siding_qty,
                                  siding_fit or 'Outer wall faces, upper band and gable triangles.'))
    if batten_product is not None:
        if batten_qty is None and batten_length and batten_product.get('unit_length_m'):
            batten_qty = math.ceil(batten_length * 1.1 / batten_product['unit_length_m'])
        b.product(catalogue_entry(prefix + 'batten', 'Exterior', batten_product, batten_qty,
                                  batten_fit or 'Regular centres on every outer face, cut around casings and fittings.'))
    return b.bundle()
