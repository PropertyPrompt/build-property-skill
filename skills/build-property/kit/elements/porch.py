"""Covered entrance porch and simple door stoops.

layout(spec, door_id, ...) -> PorchLayout: every porch dimension, derived from the door and a few
    parameters, plus helpers the other elements need:
      .batten_exclusion()  battens stop under the ledger         -> (face key, (s0, s1, y0, y1))
      .plinth_gap()        plinth is interrupted by the slab      -> (face key, (s0, s1))
      .ceiling_mount(off)  world position/rotation for a flush light under the sloped porch ceiling
      .front_edge()        along-wall centre and outward distance of the step nosing (path start)
build(spec, layout, ...) -> bundle: concrete slab, step to grade, ledger, roof framing with a painted
    ceiling, standing-seam metal skin + seams, fascia with a lip that hides the seam ends, side rake
    boards, a wood beam, two wood posts with black bases. Roof/trim parts use cutaway "hide"; slab,
    step, posts and beam keep. Nothing collides (walkability comes from settings, see walk_settings()).
walk_settings(spec, layout, path_end, ...) -> {"floors": [...], "stairs": [...]} settings snippet
    that makes the slab, step and front path walkable: the ground floor polygon is extended over the
    slab, a second floor at grade covers the path, and a stair joins them.
    TRADE-OFF: a property with a `floors` list is multi-floor, and named camera presets are hidden on
    multi-floor properties. Only add this when walking out to the street matters more than presets.
stoop(spec, door_id, ...) -> (bundle, plinth gap): landing at floor level plus one step to grade at
    a secondary door.

The porch is built in the door face's frame: the `porch` group node is rotated to face the wall, so
any outer wall works.

Layout defaults (metres), all overridable:
  width       door width + 2.0 (at least 2.4); centred on the door unless `center` is given
  depth       1.8
  skin_top    roof skin height at the wall: spec ceilingHeight + 0.20, which keeps it under a main
              roof soffit built with elements.roof defaults. `clearance` (beam underside to slab)
              is computed; check it clears the door head.
  slope       1:6 fall away from the wall
"""
import math

from elements._common import Bag, HIDE, KEEP, MRX, PI, euler, euler_xyz_from, face_of_opening, libmat, mm, mv, r3


class PorchLayout:
    def __init__(self, spec, door_id, center=None, width=None, depth=1.8, slab_y=-0.02, grade=None,
                 roof_margin=0.10, roof_front=0.15, ledger_gap=0.04, skin_top=None, slope=1 / 6,
                 skin_t=0.02, framing_t=0.12, beam_inset=0.154, beam_margin=0.05, beam_w=0.15, beam_d=0.20,
                 post=0.14, post_inset=0.11, step_width=1.5, step_center=None, step_depth=0.31,
                 seams=8, lip=0.035):
        self.face, self.door = face_of_opening(spec, door_id)
        f = self.face
        self.dir = 1 if f.origin_end == 'lo' else -1          # local x = dir * along
        self.PZ = f.sign * f.plane                            # local z of the wall face
        self.GRADE = spec.get('grade', {}).get('y', -0.30) if grade is None else grade
        c = (self.door['from'] + self.door['to']) / 2 if center is None else center
        if width is None:
            width = max(2.4, self.door['to'] - self.door['from'] + 2.0)
        if skin_top is None:
            skin_top = spec['ceilingHeight'] + 0.20
        self.center = c
        self.PX0, self.PX1 = c - width / 2, c + width / 2     # slab, along-wall (world)
        self.depth = depth
        self.PZ1 = self.PZ + depth                            # slab front (local z)
        self.SLAB_Y = slab_y
        self.RX0, self.RX1 = self.PX0 - roof_margin, self.PX1 + roof_margin
        self.RZ0, self.RZ1 = self.PZ + ledger_gap, self.PZ1 + roof_front
        self.S0, self.SLP = skin_top, slope
        self.SKIN_T, self.STR_T = skin_t, framing_t
        self.B_ANG = math.atan(slope)
        sc = c if step_center is None else step_center
        self.STX0, self.STX1 = sc - step_width / 2, sc + step_width / 2
        self.STZ1 = self.PZ1 + step_depth
        self.ST_Y = (slab_y + self.GRADE) / 2
        self.BZ = self.PZ1 - beam_inset
        self.BW, self.BD = beam_w, beam_d
        self.BX0, self.BX1 = self.PX0 - beam_margin, self.PX1 + beam_margin
        self.POST = post
        self.posts = (self.PX0 + post_inset, self.PX1 - post_inset)
        self.seams = seams
        self.LIP = lip
        self.beam_top = self.U(self.BZ + beam_w / 2) - 0.001
        self.clearance = self.beam_top - beam_d - slab_y

    # sloped roof: skin top S(z) and framing underside U(z), z local
    def S(self, z):
        return self.S0 - (z - self.RZ0) * self.SLP

    def U(self, z):
        return self.S(z) - self.SKIN_T - self.STR_T

    def lx(self, s):
        return self.dir * s

    def lxr(self, a, b):
        x0, x1 = sorted((self.lx(a), self.lx(b)))
        return x0, x1

    def to_world(self, p):
        R = euler(0, self.face.rot, 0)
        return mv(R, p)

    # helpers for other elements --------------------------------------------------
    def batten_exclusion(self):
        return self.face.key, (self.RX0, self.RX1, self.S0 - 0.18, 99)

    def plinth_gap(self):
        return self.face.key, (self.PX0, self.PX1)

    def ceiling_mount(self, offset, along=None):
        """(position, rotation) for a fixture hung under the porch ceiling `offset` m out from the wall."""
        z = self.PZ + offset
        s = self.center if along is None else along
        pos = self.to_world([self.lx(s), self.U(z) - 0.002, z])
        R = mm(euler(0, self.face.rot, 0), euler(self.B_ANG, 0, 0))
        return pos, euler_xyz_from(R)

    def front_edge(self):
        return self.center, self.STZ1 - self.PZ

    def world_point(self, along, out, y=0.0):
        return self.face.world(along, y, out)


def layout(spec, door_id, **kw):
    return PorchLayout(spec, door_id, **kw)


def build(spec, L, prefix='ex-', materials=None, rake_geometry_id='g-porch-rake'):
    b = Bag(prefix)
    M_TRIM, M_BLACK, M_CONC = libmat(materials, 'trim'), libmat(materials, 'black'), libmat(materials, 'concrete')
    M_METAL = b.mat('standing-seam', name='Black standing-seam metal', color='#26282a', roughness=0.45,
                    metalness=0.55)
    M_WOOD = b.mat('porch-wood', name='Stained wood', color='#9a6a3e', roughness=0.7)
    GRADE, PZ, PZ1, SLAB_Y = L.GRADE, L.PZ, L.PZ1, L.SLAB_Y
    S, U = L.S, L.U
    RZ0, RZ1 = L.RZ0, L.RZ1

    def zy(pts):
        return [[-z, y] for z, y in pts]

    rot = L.face.yaw() if L.face.rot else None
    b.node('porch', None, [0, 0, 0], 'Covered front porch', rot, container='shell')
    px0, px1 = L.lxr(L.PX0, L.PX1)
    g_slab = b.box('g-porch-slab', [px1 - px0, SLAB_Y - GRADE, PZ1 - PZ])
    b.mesh('porch-slab', 'porch', [(px0 + px1) / 2, (GRADE + SLAB_Y) / 2, (PZ + PZ1) / 2], 'Porch slab', g_slab,
           M_CONC, beh=KEEP)
    sx0, sx1 = L.lxr(L.STX0, L.STX1)
    g_step = b.box('g-porch-step', [sx1 - sx0, L.ST_Y - GRADE, L.STZ1 - PZ1])
    b.mesh('porch-step', 'porch', [(sx0 + sx1) / 2, (GRADE + L.ST_Y) / 2, (PZ1 + L.STZ1) / 2], 'Porch step',
           g_step, M_CONC, beh=KEEP)

    rx0, rx1 = L.lxr(L.RX0, L.RX1)
    g_ledger = b.box('g-ledger', [rx1 - rx0, 0.18, 0.04])
    b.mesh('porch-ledger', 'porch', [(rx0 + rx1) / 2, L.S0 - 0.09, PZ + 0.02], 'Porch ledger', g_ledger, M_TRIM)
    str_prof = [(RZ0, U(RZ0)), (RZ1, U(RZ1)), (RZ1, S(RZ1) - L.SKIN_T), (RZ0, S(RZ0) - L.SKIN_T)]
    skin_prof = [(RZ0, S(RZ0) - L.SKIN_T), (RZ1, S(RZ1) - L.SKIN_T), (RZ1, S(RZ1)), (RZ0, S(RZ0))]
    g_str = b.extrude('g-porch-ceiling', zy(str_prof), rx1 - rx0)
    g_skin = b.extrude('g-porch-skin', zy(skin_prof), rx1 - rx0)
    b.mesh('porch-ceiling', 'porch', [rx0, 0, 0], 'Porch roof framing and beadboard ceiling', g_str,
           [M_TRIM, M_TRIM], rot=[0, PI / 2, 0])
    b.mesh('porch-skin', 'porch', [rx0, 0, 0], 'Porch roof standing-seam metal', g_skin, [M_METAL, M_METAL],
           rot=[0, PI / 2, 0])
    rib_len = (RZ1 - RZ0) / math.cos(L.B_ANG)
    g_rib = b.box('g-seam', [0.02, 0.03, rib_len])
    zc = (RZ0 + RZ1) / 2
    yc = S(zc) + 0.015 / math.cos(L.B_ANG)
    n = L.seams
    ribs = [MRX(L.B_ANG, rx0 + 0.12 + k * (rx1 - rx0 - 0.24) / (n - 1), yc, zc) for k in range(n)]
    b.mesh('porch-seams', 'porch', [0, 0, 0], 'Standing seams', g_rib, M_METAL, instances=ribs)

    # fascia: 200 mm board scaled up by the lip so it rises above the seam ends
    g_pf = b.box('g-porch-fascia', [rx1 - rx0, 0.20, 0.025])
    fas_h = 0.20 + L.LIP
    fas_bot = S(RZ1) - 0.20
    b.mesh('porch-fascia', 'porch', [(rx0 + rx1) / 2, fas_bot + fas_h / 2, RZ1 + 0.0125],
           'Porch fascia (with lip over seams)', g_pf, M_TRIM, scale=[1, fas_h / 0.20, 1])
    side_prof = [(RZ0, U(RZ0) - 0.06), (RZ1 + 0.025, U(RZ1 + 0.025) - 0.06), (RZ1 + 0.025, S(RZ1 + 0.025) + L.LIP),
                 (RZ0, S(RZ0) + L.LIP)]
    g_ps = b.extrude(rake_geometry_id, zy(side_prof), 0.025)
    b.mesh('porch-rake-w', 'porch', [rx0 - 0.025, 0, 0], 'Porch rake board', g_ps, [M_TRIM, M_TRIM],
           rot=[0, PI / 2, 0])
    b.mesh('porch-rake-e', 'porch', [rx1, 0, 0], 'Porch rake board', g_ps, [M_TRIM, M_TRIM], rot=[0, PI / 2, 0])

    bx0, bx1 = L.lxr(L.BX0, L.BX1)
    g_beam = b.box('g-porch-beam', [bx1 - bx0, L.BD, L.BW])
    b.mesh('porch-beam', 'porch', [(bx0 + bx1) / 2, L.beam_top - L.BD / 2, L.BZ], 'Porch beam', g_beam, M_WOOD)
    post_y0 = SLAB_Y + 0.012
    post_h = L.beam_top - L.BD - post_y0
    g_post = b.box('g-porch-post', [L.POST, post_h, L.POST])
    g_base = b.box('g-post-base', [0.18, 0.012, 0.18])
    for k, s in zip(('w', 'e'), sorted(L.posts, key=L.lx)):
        x = L.lx(s)
        b.mesh(f'porch-post-{k}', 'porch', [x, post_y0 + post_h / 2, L.BZ], 'Porch post', g_post, M_WOOD)
        b.mesh(f'porch-base-{k}', 'porch', [x, SLAB_Y + 0.006, L.BZ], 'Post base', g_base, M_BLACK)
    return b.bundle()


def _insert_notch(poly, face, a0, a1, depth):
    """Insert a rectangular bump (along a0..a1, `depth` outward) into the footprint polygon edge on `face`."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        out.append(list(p))
        ax = 0 if face.axis == 'x' else 1           # along-wall coordinate index in [x, z]
        pa = 1 - ax
        if abs(p[pa] - face.plane) < 1e-6 and abs(q[pa] - face.plane) < 1e-6:
            lo, hi = sorted((p[ax], q[ax]))
            if lo <= a0 and a1 <= hi:
                ends = (a0, a1) if p[ax] < q[ax] else (a1, a0)
                for k, s in enumerate((ends[0], ends[0], ends[1], ends[1])):
                    o = depth if k in (1, 2) else 0.0
                    w = face.world(s, 0, o)
                    out.append([r3(w[0]), r3(w[2])])
    return out


def walk_settings(spec, L, path_end, path_width=1.2, ground_entrance=None, ground_name='Ground floor',
                  ceiling=None, garden_id='garden', garden_name='Front path', footprint=None, along=None):
    """Settings snippet (floors + stairs) for a walkable porch and a straight path from the step to
    `path_end` (outward distance from the wall face). The stair runs from 0.22 m inside the slab edge
    to 0.22 m beyond the step; the path floor starts at the step. Coordinates on the walking axis are
    rounded to 1-2 decimals so the settings stay readable. `along` defaults to the door centre. Ground floor id is 'ground'. See the module docstring for the preset trade-off."""
    from elements._common import footprint_polygon
    f = L.face
    ceil = spec['ceilingHeight'] if ceiling is None else ceiling
    poly = footprint or footprint_polygon(spec)
    poly = _insert_notch([[r3(x), r3(z)] for x, z in poly], f, L.PX0, L.PX1, L.depth)
    c = r3((L.door['from'] + L.door['to']) / 2 if along is None else along)

    def wpt(s, o, nd):
        w = f.world(s, 0, o)
        i = 2 if f.axis == 'x' else 0                     # world index of the walking axis
        w[i] = round(w[i], nd)
        return [r3(w[0]), r3(w[2])]

    out0, step_out = L.depth, L.STZ1 - L.PZ
    path = []
    for o, y in ((out0 - 0.22, 0.0), ((out0 + step_out) / 2, L.ST_Y), (step_out + 0.22, L.GRADE)):
        x, z = wpt(c, o, 2)
        path.append([x, r3(y), z])
    g0 = (out0 + step_out) / 2
    hw = path_width / 2
    corners = [wpt(c - hw, g0, 1), wpt(c + hw, g0, 1), wpt(c + hw, path_end, 3), wpt(c - hw, path_end, 3)]
    yaw = round(f.rot, 5)                                  # facing the house from outside
    if ground_entrance is None:
        ground_entrance = wpt(c, -0.8, 2)
    return {
        'floors': [
            {'id': 'ground', 'name': ground_name, 'elevation': 0, 'ceilingHeight': ceil,
             'entrance': ground_entrance, 'entranceYaw': yaw, 'floorPolygons': [poly]},
            {'id': garden_id, 'name': garden_name, 'elevation': L.GRADE, 'ceilingHeight': ceil,
             'entrance': wpt(c, path_end - 0.5, 1), 'entranceYaw': yaw, 'floorPolygons': [corners]},
        ],
        'stairs': [{'id': 'porch-step', 'fromFloorId': 'ground', 'toFloorId': garden_id,
                    'width': path_width, 'path': path}],
    }


def stoop(spec, door_id, prefix='ex-', materials=None, depth=1.2, margin=0.25, span=None, slab_y=-0.02,
          step_depth=0.30, step_inset=0.10, grade=None, ids=('back-landing', 'back-step'),
          names=('Back door landing', 'Back step')):
    """Landing at a door plus one step to grade. Returns (bundle, (face key, plinth gap))."""
    b = Bag(prefix)
    M_CONC = libmat(materials, 'concrete')
    f, o = face_of_opening(spec, door_id)
    GRADE = spec.get('grade', {}).get('y', -0.30) if grade is None else grade
    if span is None:
        span = (round((o['from'] - margin) * 20) / 20, round((o['to'] + margin) * 20) / 20)
    a0, a1 = span
    st_y = (slab_y + GRADE) / 2

    def wbox(nid, name, s0, s1, o0, o1, y0, y1):
        p0, p1 = f.world(s0, y0, o0), f.world(s1, y1, o1)
        lo = [min(p0[i], p1[i]) for i in range(3)]
        hi = [max(p0[i], p1[i]) for i in range(3)]
        g = b.box('g-' + nid, [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]])
        b.mesh(nid, None, [(lo[i] + hi[i]) / 2 for i in range(3)], name, g, M_CONC, beh=KEEP, container='shell')

    wbox(ids[0], names[0], a0, a1, 0, depth, GRADE, slab_y)
    wbox(ids[1], names[1], a0 + step_inset, a1 - step_inset, depth, depth + step_depth, GRADE, st_y)
    return b.bundle(), (f.key, (a0, a1))
