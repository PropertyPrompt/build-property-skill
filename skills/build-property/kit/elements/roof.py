"""Gable roof over a rectangular footprint.

build(spec, pitch=..., ...) -> bundle with: roof deck (painted underside), gable-end wall triangles,
the upper wall band between ceiling and plate, boxed eave soffits + soffit returns, fascia, rake
boards, instanced shingle courses in three tones, ridge cap, K-style gutters + end caps, downpipes
(tube with a kick-back under the soffit and a kick-out at the foot) and splash blocks.

Everything hangs under a `roof` group node and uses cutaway "hide" with no collision; the splash
blocks sit on the ground (cutaway "keep"). The roof is built in a frame whose x runs along the ridge;
`ridge_axis='z'` rotates that frame (the group node carries the rotation), so the same code serves
both orientations. The footprint may sit anywhere (its min corner becomes the group position).

`geometry(spec, pitch=...)` returns the RoofGeom that siding.py needs (gable tops for battens,
soffit y).

Parameters (metres):
  pitch                rise per unit run (0.5 = 6:12). Required: read it from the elevations or
                       choose it for the style; there is no neutral default.
  plate                wall-plate height. Default spec ceilingHeight + plate_above_ceiling
                       (0.5 m: ceiling joists, top plates and a raised heel).
  overhang, rake       eave and rake overhangs (default 0.45 m; rake = overhang).
  deck_t               deck + rafter depth measured square to the slope (default 0.20 m).
  soffit_y             (bottom, top) of the boxed soffit. Default: 20 mm under the deck underside at
                       the eave edge, 16 mm thick.
  fascia_y0            fascia bottom (default 30 mm under the soffit).
  gutter_top           gutter top (default 30 mm under the fascia top).
Catalogue: pass a researched roofing `product` (see elements._common.catalogue_entry; `unit_m2` =
coverage per unit) to get a catalogue entry with an estimated quantity; without one the roof has
no catalogue entry. `siding_product_key` tags the gable triangles with siding.catalogue's key.
Hip and shed roofs are not implemented.
"""
import math
import random

from elements._common import (Bag, Face, HIDE, KEEP, M, MRX, PI, catalogue_entry, dense, footprint_rect, libmat,
                              product_keys, r4)


class RoofGeom:
    """Derived dimensions of a symmetric gable. Frame: u along the ridge (0..LU), v across (0..LV)."""

    def __init__(self, spec, pitch, ridge_axis='x', plate=None, plate_above_ceiling=0.5, ceiling=None,
                 overhang=0.45, rake=None, deck_t=0.20, soffit_y=None, fascia_y0=None, gutter_top=None,
                 wall_t=None):
        x0, z0, x1, z1 = footprint_rect(spec)
        self.rect = (x0, z0, x1, z1)
        self.ridge_axis = ridge_axis
        self.LU, self.LV = (x1 - x0, z1 - z0) if ridge_axis == 'x' else (z1 - z0, x1 - x0)
        self.pitch = pitch
        self.A = math.atan(pitch)
        self.CA, self.SA = math.cos(self.A), math.sin(self.A)
        self.CEIL = spec['ceilingHeight'] if ceiling is None else ceiling
        self.PH = self.CEIL + plate_above_ceiling if plate is None else plate
        if self.PH <= self.CEIL:
            raise ValueError('plate %.3f must be above the ceiling %.3f' % (self.PH, self.CEIL))
        self.wall_t = spec.get('wallThickness', {}).get('outer', 0.185) if wall_t is None else wall_t
        self.DECK_T = deck_t
        self.D = deck_t / self.CA                      # vertical deck thickness
        self.OV = overhang                             # eave overhang
        self.RK = overhang if rake is None else rake   # rake overhang
        self.ZR = self.LV / 2
        self.RISE = pitch * self.ZR
        self.EAVE_U = self.PH - pitch * overhang       # deck underside at the eave edge
        if soffit_y is None:
            top = round(self.EAVE_U - 0.02, 4)
            soffit_y = (round(top - 0.016, 4), top)
        self.SOF_Y0, self.SOF_Y1 = soffit_y
        if self.SOF_Y0 <= self.CEIL:
            raise ValueError('soffit %.3f is not above the ceiling %.3f: raise plate or reduce overhang/pitch'
                             % (self.SOF_Y0, self.CEIL))
        self.FAS_Y0 = round(self.SOF_Y0 - 0.03, 4) if fascia_y0 is None else fascia_y0
        self.FAS_Y1 = round(self.EAVE_U + self.D, 4)
        self.G_TOP = round(self.FAS_Y1 - 0.03, 4) if gutter_top is None else gutter_top
        self.XL, self.XR = -self.RK, self.LU + self.RK
        self.L_SLOPE = (self.ZR + overhang) / self.CA

    # ------------------------------------------------------------ frame <-> world
    def group_transform(self):
        """Position and rotation of the roof group node so frame coords become world coords."""
        x0, z0, x1, z1 = self.rect
        if self.ridge_axis == 'x':
            return [x0, 0, z0], None
        return [x1, 0, z0], [0, -PI / 2, 0]            # frame (u, y, v) -> world (x1 - v, y, z0 + u)

    def to_world(self, p):
        x0, z0, x1, z1 = self.rect
        u, y, v = p
        return [x0 + u, y, z0 + v] if self.ridge_axis == 'x' else [x1 - v, y, z0 + u]

    def yaw_world(self, yaw):
        return yaw if self.ridge_axis == 'x' else yaw - PI / 2

    # ------------------------------------------------------------ for siding
    def gable_top(self, v):
        """Top of a gable-end wall at across-coordinate v (frame)."""
        return self.PH + self.pitch * max(0.0, min(v, self.LV - v))

    def batten_top(self, face, b0, b1):
        """Highest siding point on an outer face over the along-wall span b0..b1 (world)."""
        x0, z0, x1, z1 = self.rect
        if face.axis == self.ridge_axis:                # eave side: up to the soffit
            return self.SOF_Y0
        off = z0 if self.ridge_axis == 'x' else x0      # gable side: under the rake
        return min(self.gable_top(b0 - off), self.gable_top(b1 - off)) - 0.005

    def area(self):
        return 2 * self.L_SLOPE * (self.XR - self.XL)


def geometry(spec, pitch, **kw):
    return RoofGeom(spec, pitch, **kw)


def _frame_faces(g):
    """Eave faces in the roof frame (as outer-wall Face objects): 'lo' at v=0, 'hi' at v=LV."""
    t = g.wall_t
    lo = Face({'id': 'eave-lo', 'axis': 'x', 'line_z_outer': 0.0, 'inner_z': t, 'from': 0.0, 'to': g.LU,
               'openings': []})
    hi = Face({'id': 'eave-hi', 'axis': 'x', 'line_z_outer': g.LV, 'inner_z': g.LV - t, 'from': 0.0,
               'to': g.LU, 'openings': []})
    return lo, hi


def build(spec, prefix='ex-', geom=None, materials=None, downpipes=None, shingle_seed=1,
          tone_split=(0.22, 0.40), exposure=0.143, tab=0.333, keyway=(0.01, 0.11), grade=None, product=None,
          shingle_qty=None, shingle_fit=None, siding_product_key=None, **geom_kw):
    """Return the roof bundle. Give `geom` (RoofGeom) or the RoofGeom keywords (pitch is required).
    `tab`: shingle tab width; `keyway`: (width, height) of the joints between tabs, in metres.
    `downpipes`: list of (eave 'lo'|'hi', u along the ridge from the frame origin); default 0.30 m in
    from both ends of both eaves. `product`: researched roofing product for the catalogue."""
    g = geom or RoofGeom(spec, **geom_kw)
    b = Bag(prefix)
    P = prefix
    M_TRIM, M_BLACK, M_SIDING = libmat(materials, 'trim'), libmat(materials, 'black'), libmat(materials, 'siding')
    M_CONC, M_SHINGLE = libmat(materials, 'concrete'), libmat(materials, 'shingle')
    GRADE = spec.get('grade', {}).get('y', -0.30) if grade is None else grade
    WX, WZ, OV, ZR, PH, D, RISE = g.LU, g.LV, g.OV, g.ZR, g.PH, g.D, g.RISE
    CA, SA, EAVE_U = g.CA, g.SA, g.EAVE_U
    XL, XR = g.XL, g.XR
    T = g.wall_t

    M_SH_HI = b.mat('shingle-hi', name='Shingle, light granules', color='#4a4c4f', roughness=0.95)
    M_SH_LO = b.mat('shingle-lo', name='Shingle, shadow band', color='#2a2b2d', roughness=0.97)
    SK = product_keys(P, 'shingle', product is not None)

    def zy(pts):            # profile (frame v, y) -> extrude points for a node rotated (0, +pi/2, 0)
        return [[-z, y] for z, y in pts]

    pos, rot = g.group_transform()
    b.node('roof', None, pos, 'Roof, gables and eaves', rot, container='shell')

    deck_prof = [(-OV, EAVE_U), (ZR, PH + RISE), (WZ + OV, EAVE_U),
                 (WZ + OV, EAVE_U + D), (ZR, PH + RISE + D), (-OV, EAVE_U + D)]
    g_deck = b.extrude('g-roof-deck', zy(deck_prof), XR - XL)
    b.mesh('roof-deck', 'roof', [XL, 0, 0], 'Roof deck and rafters (painted underside)', g_deck,
           [M_TRIM, M_TRIM], rot=[0, PI / 2, 0])

    # gable-end wall triangles on the wall line, above the band
    g_gable = b.extrude('g-gable', zy([(0, PH), (WZ, PH), (ZR, PH + RISE - 0.001)]), T)
    for side, x in (('w', 0.0), ('e', WX - T)):
        b.mesh(f'gable-{side}', 'roof', [x, 0, 0], f'Gable end wall ({side})', g_gable, [M_SIDING, M_SIDING],
               rot=[0, PI / 2, 0], **product_keys(P, siding_product_key))

    # upper band between ceiling and plate on the outer wall line
    BH = PH - g.CEIL
    g_band_x = b.box('g-band-x', [WX, BH, T])
    g_band_z = b.box('g-band-z', [T, BH, WZ - 2 * T])
    yb = g.CEIL + BH / 2
    b.mesh('band-n', 'roof', [WX / 2, yb, T / 2], 'Upper wall band north', g_band_x, M_SIDING)
    b.mesh('band-s', 'roof', [WX / 2, yb, WZ - T / 2], 'Upper wall band south', g_band_x, M_SIDING)
    b.mesh('band-w', 'roof', [T / 2, yb, WZ / 2], 'Upper wall band west', g_band_z, M_SIDING)
    b.mesh('band-e', 'roof', [WX - T / 2, yb, WZ / 2], 'Upper wall band east', g_band_z, M_SIDING)

    # boxed soffits, soffit returns, fascia
    SOF_Y0, SOF_Y1 = g.SOF_Y0, g.SOF_Y1
    g_sof = b.box('g-soffit', [XR - XL, SOF_Y1 - SOF_Y0, OV])
    b.mesh('soffit-n', 'roof', [(XL + XR) / 2, (SOF_Y0 + SOF_Y1) / 2, -OV / 2], 'Boxed soffit north', g_sof, M_TRIM)
    b.mesh('soffit-s', 'roof', [(XL + XR) / 2, (SOF_Y0 + SOF_Y1) / 2, WZ + OV / 2], 'Boxed soffit south', g_sof,
           M_TRIM)
    ret_n = zy([(-OV, SOF_Y1), (0, SOF_Y1), (0, PH - 0.001), (-OV, EAVE_U - 0.001)])
    ret_s = zy([(WZ, SOF_Y1), (WZ + OV, SOF_Y1), (WZ + OV, EAVE_U - 0.001), (WZ, PH - 0.001)])
    g_rn, g_rs = b.extrude('g-sofret-n', ret_n, 0.02), b.extrude('g-sofret-s', ret_s, 0.02)
    for k, x in (('w', XL), ('e', XR - 0.02)):
        b.mesh(f'sofret-n{k}', 'roof', [x, 0, 0], 'Soffit return', g_rn, [M_TRIM, M_TRIM], rot=[0, PI / 2, 0])
        b.mesh(f'sofret-s{k}', 'roof', [x, 0, 0], 'Soffit return', g_rs, [M_TRIM, M_TRIM], rot=[0, PI / 2, 0])
    g_fas = b.box('g-fascia', [XR - XL + 0.05, g.FAS_Y1 - g.FAS_Y0, 0.025])
    yf = (g.FAS_Y0 + g.FAS_Y1) / 2
    b.mesh('fascia-n', 'roof', [(XL + XR) / 2, yf, -OV - 0.0125], 'Fascia north', g_fas, M_TRIM)
    b.mesh('fascia-s', 'roof', [(XL + XR) / 2, yf, WZ + OV + 0.0125], 'Fascia south', g_fas, M_TRIM)

    # rake (barge) boards
    rake_prof = [(-OV, EAVE_U - 0.05), (ZR, PH + RISE - 0.05), (WZ + OV, EAVE_U - 0.05),
                 (WZ + OV, EAVE_U + D), (ZR, PH + RISE + D), (-OV, EAVE_U + D)]
    g_rake = b.extrude('g-rake', zy(rake_prof), 0.025)
    b.mesh('rake-w', 'roof', [XL - 0.025, 0, 0], 'Rake board west', g_rake, [M_TRIM, M_TRIM], rot=[0, PI / 2, 0])
    b.mesh('rake-e', 'roof', [XR, 0, 0], 'Rake board east', g_rake, [M_TRIM, M_TRIM], rot=[0, PI / 2, 0])

    # shingle courses, tone picked at random per course: a notched top strip (tabs with keyway joints,
    # offset half a tab on alternate courses) over a thin dark base strip that shows in the joints.
    # A course is a box frame: x along the ridge, y the slope normal, z along the slope.
    EXPO, E_RAISE, SH_T, BASE_T = exposure, 0.012, 0.010, 0.004
    SH_W, SH_L = XR - XL + 0.02, 0.144
    TAB = max(tab, SH_W / 48)       # an extrude takes at most 200 points: 4 per joint
    g_base = b.box('g-course-base', [SH_W, BASE_T, SH_L])

    def tabbed(name, offset):
        y0, y1 = -SH_L / 2, -SH_L / 2 + keyway[1]
        pts = [(-SH_W / 2, y0)]
        x = -SH_W / 2 + offset
        while x + keyway[0] / 2 < SH_W / 2 - 0.01:
            if x - keyway[0] / 2 > -SH_W / 2 + 0.01:
                pts += [(x - keyway[0] / 2, y0), (x - keyway[0] / 2, y1), (x + keyway[0] / 2, y1), (x + keyway[0] / 2, y0)]
            x += TAB
        return b.extrude(name, pts + [(SH_W / 2, y0), (SH_W / 2, SH_L / 2), (-SH_W / 2, SH_L / 2)], SH_T - BASE_T)

    g_tabs = (tabbed('g-course-a', TAB), tabbed('g-course-b', TAB / 2))

    def tab_matrix(th, x, cy, cz, eave_low):
        # Extrude XY -> course (x, z), extrude depth -> course y, eave edge at the strip's -y profile edge;
        # turned half a turn about the normal when the course's eave lies at +z.
        c, s, top = math.cos(th), math.sin(th), SH_T / 2
        cols = ([1, 0, 0], [0, -s, c], [0, -c, -s]) if eave_low else ([-1, 0, 0], [0, s, -c], [0, -c, -s])
        return [r4(v) for v in cols[0]] + [0] + [r4(v) for v in cols[1]] + [0] + [r4(v) for v in cols[2]] + [0] + \
            [r4(x), r4(cy + c * top), r4(cz + s * top), 1]

    rnd = random.Random(shingle_seed)
    inst = {(m, v): [] for m in (M_SHINGLE, M_SH_HI, M_SH_LO) for v in (0, 1)}
    base = []
    n_courses = math.ceil((g.L_SLOPE - 0.06) / EXPO)        # last course tucks under the ridge cap
    for side in ('n', 's'):
        for i in range(n_courses):
            u0 = -0.02 + i * EXPO
            u1 = u0 + EXPO
            if side == 'n':
                def deck(u):
                    return (-OV + u * CA, EAVE_U + D + u * SA)
                nrm = (-SA, CA)
            else:
                def deck(u):
                    return (WZ + OV - u * CA, EAVE_U + D + u * SA)
                nrm = (SA, CA)
            bb, t = deck(u0), deck(u1)
            B = (bb[0] + nrm[0] * (E_RAISE + 0.001), bb[1] + nrm[1] * (E_RAISE + 0.001))
            Tt = (t[0] + nrm[0] * 0.001, t[1] + nrm[1] * 0.001)
            vz, vy = Tt[0] - B[0], Tt[1] - B[1]
            if vz < 0:
                vz, vy = -vz, -vy
            th = -math.atan2(vy, vz)
            cz = (B[0] + Tt[0]) / 2 + nrm[0] * SH_T / 2
            cy = (B[1] + Tt[1]) / 2 + nrm[1] * SH_T / 2
            r = rnd.random()
            m = M_SH_HI if r < tone_split[0] else (M_SH_LO if r < tone_split[1] else M_SHINGLE)
            c, s, drop = math.cos(th), math.sin(th), (SH_T - BASE_T) / 2
            base.append(MRX(th, (XL + XR) / 2, cy - c * drop, cz - s * drop))
            inst[(m, i % 2)].append(tab_matrix(th, (XL + XR) / 2, cy, cz, side == 'n'))
    def meshes(nid, name, geom, mats, lst, **kw):
        # the server takes at most 512 instances per node
        parts = [lst[j:j + 512] for j in range(0, len(lst), 512)]
        for j, part in enumerate(parts):
            b.mesh(nid if len(parts) == 1 else f'{nid}-{j + 1}', 'roof', [0, 0, 0], name, geom, mats,
                   instances=part, **kw)

    meshes('shingles-base', 'Shingle courses, base strip', g_base, M_SH_LO, base)
    for k, ((m, v), lst) in enumerate(inst.items()):
        meshes(f'shingles-{k + 1}', f'Shingle courses tone {k // 2 + 1}', g_tabs[v], [m, m], lst, **SK)

    # ridge cap
    h0, h1, w = 0.026, 0.038, 0.16

    def offs(u, side, h):
        if side == 'n':
            z, y, n = -OV + u * CA, EAVE_U + D + u * SA, (-SA, CA)
        else:
            z, y, n = WZ + OV - u * CA, EAVE_U + D + u * SA, (SA, CA)
        return (z + n[0] * h, y + n[1] * h)

    ridge_top = PH + RISE + D
    L = g.L_SLOPE
    ridge_prof = [offs(L - w, 'n', h0), (ZR, ridge_top + h0 / CA), offs(L - w, 's', h0),
                  offs(L - w, 's', h1), (ZR, ridge_top + h1 / CA), offs(L - w, 'n', h1)]
    g_ridge = b.extrude('g-ridge', zy(ridge_prof), SH_W)
    b.mesh('ridge', 'roof', [XL - 0.01, 0, 0], 'Ridge cap shingles', g_ridge, [M_SH_LO, M_SH_LO],
           rot=[0, PI / 2, 0], **SK)

    # gutters (K-style profile) and end caps
    GW, GH, GT = 0.135, 0.11, 0.004
    G_TOP = g.G_TOP
    gut_prof = [(0, GH), (0, 0), (0.125, 0), (GW, GH), (GW - GT, GH), (0.125 - GT, GT), (GT, GT), (GT, GH)]
    g_gut = b.extrude('g-gutter', gut_prof, XR - XL + 0.05)
    b.mesh('gutter-n', 'roof', [XL - 0.025, G_TOP - GH, -OV - 0.025], 'Gutter north', g_gut, [M_BLACK, M_BLACK],
           rot=[0, PI / 2, 0])
    b.mesh('gutter-s', 'roof', [XR + 0.025, G_TOP - GH, WZ + OV + 0.025], 'Gutter south', g_gut,
           [M_BLACK, M_BLACK], rot=[0, -PI / 2, 0])
    g_cap = b.box('g-gutter-cap', [0.003, GH, 0.13])
    caps = []
    for zc in (-OV - 0.025 - 0.065, WZ + OV + 0.025 + 0.065):
        for xc in (XL - 0.025 - 0.0015, XR + 0.025 + 0.0015):
            caps.append(M(xc, G_TOP - GH / 2, zc))
    b.mesh('gutter-caps', 'roof', [0, 0, 0], 'Gutter end caps', g_cap, M_BLACK, instances=caps)

    # downpipes: gutter outlet, back under the soffit to the wall, down, kick-out; splash block below
    PIPE_R, PIPE_O = 0.035, 0.085
    GO = OV + 0.025 + 0.0675                      # gutter centre offset from the wall face
    S0 = g.SOF_Y0
    pipe_poly = [(0, G_TOP - 0.06, GO), (0, S0 - 0.09, GO), (0, S0 - 0.15, GO - 0.03),
                 (0, S0 - 0.43, PIPE_O + 0.03), (0, S0 - 0.49, PIPE_O), (0, GRADE + 0.24, PIPE_O),
                 (0, GRADE + 0.18, PIPE_O + 0.04), (0, GRADE + 0.09, 0.30)]
    g_pipe = b.tube('g-downpipe', dense(pipe_poly), PIPE_R, 96)
    g_splash = b.box('g-splash', [0.28, 0.05, 0.6])
    lo, hi = _frame_faces(g)
    if downpipes is None:
        downpipes = [('lo', 0.30), ('lo', WX - 0.30), ('hi', 0.30), ('hi', WX - 0.30)]
    for k, (eave, u) in enumerate(downpipes):
        f = lo if eave == 'lo' else hi
        b.mesh(f'downpipe-{k + 1}', 'roof', f.world(u, 0, 0), 'Downpipe', g_pipe, M_BLACK, rot=[0, f.rot, 0])
        sp = g.to_world(f.world(u, GRADE + 0.025, 0.52))
        b.mesh(f'splash-{k + 1}', None, sp, 'Splash block', g_splash, M_CONC, beh=KEEP,
               rot=[0, g.yaw_world(f.rot), 0], container='shell')

    if product is not None:
        area = g.area()
        qty = shingle_qty or (math.ceil(area * 1.15 / product['unit_m2']) if product.get('unit_m2') else 1)
        b.product(catalogue_entry(P + 'shingle', 'Exterior', product, qty,
                                  shingle_fit or f'Gable roof ~{round(area)} m2 incl. overhangs + 15% waste.'))
    return b.bundle()
