"""Site landscaping around a footprint.

lawn(spec, ...)            lawn slab at grade with the footprint cut out (extruded polygon, so any
                           simple footprint polygon works), cutaway keep, not raycast.
path(spec, face, ...)      broom-finish concrete path panels running straight out from a step.
beds(spec, face, ...)      mulch beds along a wall plus black steel edging on the lawn side.
plants(prefix, items, ...) planting: each item names a kind; `builders` maps kinds to callables with
                           the products/ module contract (elements.plants shapes by default: shrub,
                           grass, tree), so researched plant modules drop straight in.
trees(prefix, items, ...)  the same, placed at grade instead of on the mulch.
site(spec, sidewalk, driveway, ...)
                           driveway with joints and apron, public sidewalk, grass verge, kerb and
                           street with a dashed centre line, parallel to the +z (south) side.
"""
import math

from elements._common import (Bag, KEEP, M, PI, faces, footprint_polygon, footprint_rect, libmat, mtx)


def _grade(spec, grade):
    return spec.get('grade', {}).get('y', -0.30) if grade is None else grade


def lawn(spec, prefix='ex-', materials=None, margin=8.0, extent=None, grade=None, thickness=0.02):
    """`extent` (x0, z0, x1, z1) of the lawn; default the footprint rectangle grown by `margin`."""
    b = Bag(prefix)
    G = _grade(spec, grade)
    x0, z0, x1, z1 = footprint_rect(spec)
    LW0, LZ0, LW1, LZ1 = extent or (x0 - margin, z0 - margin, x1 + margin, z1 + margin)
    outer = [(LW0, -LZ0), (LW1, -LZ0), (LW1, -LZ1), (LW0, -LZ1)]
    fp = footprint_polygon(spec)
    hole = [fp[0]] + fp[1:][::-1]
    hole = [(x, -z) for x, z in hole]
    g = b.extrude('g-lawn', outer, thickness, [hole])
    M_GRASS = libmat(materials, 'grass')
    b.mesh('lawn', None, [0, G - thickness, 0], 'Lawn', g, [M_GRASS, M_GRASS], beh=KEEP, rot=[-PI / 2, 0, 0],
           container='shell', raycast=False)
    return b.bundle()


def path(spec, face_key, along, start_out, end_out, prefix='ex-', materials=None, width=1.2, panels=6,
         grade=None, node_id='path'):
    """Path panels from outward distance start_out to end_out (from the wall face), centred on `along`."""
    b = Bag(prefix)
    G = _grade(spec, grade)
    f = faces(spec)[face_key]
    plen = (end_out - start_out) / panels
    x_axis = f.axis == 'x'
    dims = [width, 0.02, plen - 0.01] if x_axis else [plen - 0.01, 0.02, width]
    g = b.box('g-path-panel', dims)
    inst = []
    for k in range(panels):
        c = f.plane + f.sign * (start_out + (k + 0.5) * plen)
        inst.append(M(0, 0, c) if x_axis else M(c, 0, 0))
    pos = [along, G + 0.01, 0] if x_axis else [0, G + 0.01, along]
    b.mesh(node_id, None, pos, 'Front path', g, libmat(materials, 'concrete'), beh=KEEP, container='shell',
           instances=inst)
    return b.bundle()


def beds(spec, face_key, runs, out0=0.04, out1=1.0, prefix='ex-', materials=None, grade=None, edging=True,
         edge_h=0.08, edge_t=0.005):
    """Mulch beds along a wall. runs: [(id suffix, s0, s1)] along the wall; the bed spans outward
    distance out0..out1 (out0 = plinth thickness). Steel edging on the front edge and both ends."""
    b = Bag(prefix)
    G = _grade(spec, grade)
    f = faces(spec)[face_key]
    x_axis = f.axis == 'x'
    M_MULCH = b.mat('mulch', name='Dark bark mulch', color='#3b2a1e', roughness=1)
    o0, o1 = sorted((f.plane + f.sign * out0, f.plane + f.sign * out1))
    front = f.plane + f.sign * out1
    for k, s0, s1 in runs:
        dims = [s1 - s0, 0.03, o1 - o0] if x_axis else [o1 - o0, 0.03, s1 - s0]
        g = b.box(f'g-bed-{k}', dims)
        c = [(s0 + s1) / 2, G + 0.015, (o0 + o1) / 2] if x_axis else [(o0 + o1) / 2, G + 0.015, (s0 + s1) / 2]
        b.mesh(f'bed-{k}', None, c, 'Mulch bed', g, M_MULCH, beh=KEEP, container='shell')
    if edging:
        M_EDGE = b.mat('steel-edging', name='Black steel landscape edging', color='#1d1d1d', roughness=0.6,
                       metalness=0.4)
        b.node('bed-edging', None, [0, 0, 0], 'Steel bed edging', container='shell')
        ed = []
        yr = 0 if x_axis else PI / 2
        fo = front + f.sign * edge_t / 2
        for _k, s0, s1 in runs:
            p = [(s0 + s1) / 2, G + 0.02, fo] if x_axis else [fo, G + 0.02, (s0 + s1) / 2]
            ed.append(mtx(p, (0, yr, 0), (s1 - s0 + edge_t, 1, 1)))
            for se in (s0 - edge_t / 2, s1 + edge_t / 2):
                p = [se, G + 0.02, (o0 + o1) / 2] if x_axis else [(o0 + o1) / 2, G + 0.02, se]
                ed.append(mtx(p, (0, PI / 2 - yr, 0), (o1 - o0, 1, 1)))
        g = b.box('g-edging', [1, edge_h, edge_t])
        b.mesh('bed-edging-strips', 'bed-edging', [0, 0, 0], 'Edging strips', g, M_EDGE, beh=KEEP, instances=ed)
    return b.bundle()


def _place(prefix, items, builders, y_default, fits):
    from elements._common import merge
    reg = dict(DEFAULT_BUILDERS)
    reg.update(builders or {})
    counts, seen, out = {}, {}, []
    for it in items:
        counts[it['kind']] = counts.get(it['kind'], 0) + 1
    for it in items:
        kind = it['kind']
        if kind not in reg:
            raise KeyError('no builder for plant kind %r (pass builders={%r: module.build})' % (kind, kind))
        seen[kind] = seen.get(kind, 0) + 1
        iid = it.get('id') or f'{kind}-{seen[kind]}'
        kw = dict(it.get('options') or {})
        for k in ('seed', 'product'):
            if it.get(k) is not None:
                kw[k] = it[k]
        if (fits or {}).get(kind):
            kw['fit'] = fits[kind]
        y = it.get('y', y_default)
        out.append(reg[kind](prefix, iid, [it['x'], y, it['z']], it.get('yaw', 0.0), qty=counts[kind], **kw))
    return merge(*out)


def _default_builders():
    from elements import plants as _p
    return dict(_p.BUILDERS)


DEFAULT_BUILDERS = _default_builders()


def plants(prefix, items, builders=None, y=None, spec=None, bed_depth=0.03, fits=None):
    """items: [dict(kind, x, z, yaw=0, id=None, seed=None, product=None, options={...})].
    builders: {kind: callable(prefix, inst_id, position, rotation_y, qty=..., **options) -> bundle};
    the elements.plants shapes ('shrub', 'grass', 'tree') are always available, and a researched
    plant module's build function can be registered under any kind name. Placed on the mulch
    (grade + bed_depth) unless `y` is given. Ids default to '<kind>-<n>' numbered per kind; each
    call gets qty = the count of its kind (identical catalogue entries merge into one)."""
    if y is None:
        y = _grade(spec or {}, None) + bed_depth
    return _place(prefix, items, builders, y, fits)


def trees(prefix, items, builders=None, spec=None, grade=None, fits=None):
    """Like plants() but at grade; kind defaults to 'tree'."""
    items = [dict(it, kind=it.get('kind', 'tree')) for it in items]
    return _place(prefix, items, builders, _grade(spec or {}, grade), fits)


def site(spec, sidewalk, driveway, prefix='ex-', materials=None, grade=None, lot_x=None, verge=1.2, kerb=0.15,
         road=7.0, joints=4, panel=1.5, dash=(3.0, 6.0), margin=8.0):
    """Driveway, sidewalk, verge, kerb and street parallel to the south (+z) side of the lot.
    sidewalk=(z0, z1) world z of the public sidewalk; driveway=(x0, x1, z start) runs from z start to
    the back of the sidewalk, and an apron ramps from the sidewalk down to the street over the verge
    (`verge` m wide) and kerb (`kerb` m). The street is `road` m wide. Both positions come from the
    site plan or are proposed additions: there is no default."""
    verge_end = sidewalk[1] + verge
    kerb_end = verge_end + kerb
    road_end = kerb_end + road
    b = Bag(prefix)
    G = _grade(spec, grade)
    M_CONC, M_GRASS = libmat(materials, 'concrete'), libmat(materials, 'grass')
    M_ASPHALT = b.mat('asphalt', name='Street asphalt', color='#45474a', roughness=0.95)
    M_DRIVE = b.mat('driveway', name='Broom-finish concrete driveway', color='#c9c6bf', roughness=0.92)
    M_PAINT = b.mat('road-paint', name='Road line paint', color='#e3c45a', roughness=0.7)
    x0, z0, x1, z1 = footprint_rect(spec)
    LX0, LX1 = lot_x or (x0 - margin, x1 + margin)
    SW0, SW1 = sidewalk
    VG1, KB1, ROAD1 = verge_end, kerb_end, road_end
    DX0, DX1, DZ0 = driveway
    ROAD_TOP = G - 0.12
    # joints stand 3 mm proud of the slab top (the layer rule needs >= 2 mm)
    b.node('site', None, [0, 0, 0], 'Driveway, sidewalk and street', container='shell')
    g = b.box('g-driveway', [DX1 - DX0, 0.02, SW1 - DZ0])
    b.mesh('driveway', 'site', [(DX0 + DX1) / 2, G + 0.01, (DZ0 + SW1) / 2], 'Concrete driveway', g, M_DRIVE,
           beh=KEEP, raycast=False)
    g = b.box('g-drive-joint', [DX1 - DX0, 0.003, 0.012])
    b.mesh('driveway-joints', 'site', [0, 0, 0], 'Driveway control joints', g, M_ASPHALT, beh=KEEP, raycast=False,
           instances=[mtx([(DX0 + DX1) / 2, G + 0.0215, DZ0 + (SW1 - DZ0) * (k + 1) / (joints + 1)])
                      for k in range(joints)])
    ap = [(SW1, G + 0.02), (SW1, G - 0.02), (KB1, ROAD_TOP - 0.02), (KB1, ROAD_TOP)]
    g = b.extrude('g-drive-apron', [[-z, y] for z, y in ap], DX1 - DX0)
    b.mesh('driveway-apron', 'site', [DX0, 0, 0], 'Driveway apron', g, [M_DRIVE, M_DRIVE], beh=KEEP,
           rot=[0, PI / 2, 0], raycast=False)
    pw = panel - 0.01
    g = b.box('g-sidewalk-panel', [pw, 0.02, SW1 - SW0])
    sw = []
    for xa, xb in ((LX0, DX0), (DX1, LX1)):
        n = max(1, round((xb - xa) / panel))
        w = (xb - xa) / n
        for i in range(n):
            sw.append(mtx([xa + (i + 0.5) * w, 0, 0], s=(w / pw - 0.01 / pw, 1, 1)))
    b.mesh('sidewalk', 'site', [0, G + 0.01, (SW0 + SW1) / 2], 'Public sidewalk', g, M_CONC, beh=KEEP,
           raycast=False, instances=sw)
    gv = b.box('g-verge', [1, 0.02, VG1 - SW1])
    gk = b.box('g-kerb', [1, 0.27, KB1 - VG1])
    vg, kb = [], []
    for xa, xb in ((LX0, DX0), (DX1, LX1)):
        vg.append(mtx([(xa + xb) / 2, G - 0.01, (SW1 + VG1) / 2], s=(xb - xa, 1, 1)))
        kb.append(mtx([(xa + xb) / 2, G - 0.135, (VG1 + KB1) / 2], s=(xb - xa, 1, 1)))
    b.mesh('verge', 'site', [0, 0, 0], 'Grass verge', gv, M_GRASS, beh=KEEP, raycast=False, instances=vg)
    b.mesh('kerb', 'site', [0, 0, 0], 'Concrete kerb', gk, M_CONC, beh=KEEP, raycast=False, instances=kb)
    g = b.box('g-road', [LX1 - LX0, 0.05, ROAD1 - KB1])
    b.mesh('street', 'site', [(LX0 + LX1) / 2, ROAD_TOP - 0.025, (KB1 + ROAD1) / 2], 'Street', g, M_ASPHALT,
           beh=KEEP, raycast=False)
    dl, dp = dash
    g = b.box('g-road-dash', [dl, 0.004, 0.12])
    b.mesh('street-line', 'site', [0, ROAD_TOP + 0.002, (KB1 + ROAD1) / 2 + 0.8], 'Centre line', g, M_PAINT,
           beh=KEEP, raycast=False,
           instances=[mtx([LX0 + dl / 2 + i * dp, 0, 0]) for i in range(int((LX1 - LX0 - dl) // dp) + 1)])
    return b.bundle()
