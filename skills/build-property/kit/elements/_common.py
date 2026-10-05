"""Shared helpers for the building-element generators.

Everything here is geometry bookkeeping that the element modules share: a small resource/node
accumulator that emits the same data shapes as lib/payload.py (bundles of
{"resources", "products", "nodes"}), 4x4 instance matrices, and the outer-wall "face" frame that
lets one routine work on any axis-aligned outer wall of a spec.

World convention (spec.json): metres, +x east, +z south, y up, floor top y=0.

Face frame: for an outer wall, `s` is the world coordinate along the wall axis, `y` is height and
`o` is the outward offset from the outer face. A node rotated by `face.rot` about y has local +x
running left-to-right as seen from outside and local +z pointing outward; `face.origin_end` says
which end of a run is local x=0.
"""
import math
import os
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if KIT not in sys.path:
    sys.path.insert(0, KIT)

try:                                    # shared contract (another part of the kit)
    from lib import payload as _lib     # noqa: F401
except Exception:                       # pragma: no cover - the kit's lib is optional here
    _lib = None

PI = math.pi


# ------------------------------------------------------------------ rounding
def r3(v):
    v = round(float(v) + 0.0, 3)
    return 0 if v == 0 else v


def r4(v):
    v = round(float(v) + 0.0, 4)
    return 0 if v == 0 else v


def vec(v, f=r3):
    return [f(a) for a in v]


def clean_str(s):
    """Payload strings must not contain double quotes or backslashes (writer agents mis-escape them).
    Uses lib.payload.clean_str when the shared lib is importable."""
    if _lib is not None:
        return _lib.clean_str(s)
    return ' '.join(str(s).replace('"', '').replace('\\', '').split())


# ------------------------------------------------------------------ materials
# Full standard-material record (the server's defaults, written out so materials diff cleanly).
MAT_DEFAULT = {"type": "standard", "name": "", "color": "#ffffff", "roughness": 0.75, "metalness": 0,
               "emissive": "#000000", "emissiveIntensity": 1, "opacity": 1, "transparent": False,
               "depthWrite": True, "side": "front", "toneMapped": True, "flatShading": False,
               "vertexColors": False, "bumpScale": 1, "envMapIntensity": 1, "transmission": 0,
               "ior": 1.5, "thickness": 0, "dashSize": 0.1, "gapSize": 0.1, "scale": 1}

# Shared library materials (spec "materials" block); callers may remap through `materials=`.
LIB = {'trim': 'm-trim', 'black': 'm-steel-black', 'siding': 'm-siding', 'concrete': 'm-concrete',
       'grass': 'm-grass', 'shingle': 'm-shingle', 'bulb': 'm-bulb', 'paint': 'm-paint',
       'oak': 'm-oak', 'quartz': 'm-quartz'}


def libmat(materials, key):
    return (materials or {}).get(key, LIB[key])


# ------------------------------------------------------------------ behaviours
def BEH(cut='hide', height='fixed'):
    """Exterior/finish behaviour: never collides; `cut` is hide | keep | reduce."""
    return {'height': height, 'cutaway': cut, 'cutawayHeight': 0.78, 'desktopCollision': False,
            'vrCollision': False}


HIDE, KEEP = BEH('hide'), BEH('keep')


# ------------------------------------------------------------------ accumulator
class Bag:
    """Collects resources (deduplicated by id; a re-definition must be identical), nodes and catalogue
    products, with the id prefix applied. `bundle()` returns the kit's {"resources","products","nodes"}."""

    def __init__(self, prefix='', omit_identity=True):
        self.P = prefix
        self.resources = {}
        self.nodes = []
        self.products = []
        self.omit_identity = omit_identity     # omit rotation [0,0,0] / scale [1,1,1]

    # resources ---------------------------------------------------------
    def res(self, rid, kind, data, raw_id=False):
        rid = rid if raw_id else self.P + rid
        if rid in self.resources:
            assert self.resources[rid]['data'] == data, 'conflicting resource ' + rid
        else:
            self.resources[rid] = {'id': rid, 'kind': kind, 'data': data}
        return rid

    def box(self, name, dims, raw_id=False):
        return self.res(name, 'geometry', {'type': 'box', 'dimensions': vec(dims)}, raw_id)

    def cyl(self, name, rt, rb, h, seg=24, open_=False):
        return self.res(name, 'geometry', {'type': 'cylinder', 'radiusTop': r4(rt), 'radiusBottom': r4(rb),
                                           'height': r4(h), 'radialSegments': seg, 'openEnded': open_})

    def sphere(self, name, rad, ws, hs):
        return self.res(name, 'geometry', {'type': 'sphere', 'radius': r4(rad), 'widthSegments': ws,
                                           'heightSegments': hs})

    def ico(self, name, rad, detail=1):
        return self.res(name, 'geometry', {'type': 'icosahedron', 'radius': r4(rad), 'detail': detail})

    def extrude(self, name, pts, depth, holes=()):
        return self.res(name, 'geometry', {'type': 'extrude', 'points': [vec(p, r4) for p in pts],
                                           'holes': [[vec(p, r4) for p in h] for h in holes],
                                           'depth': r4(depth), 'bevel': 0, 'bevelSegments': 1})

    def tube(self, name, pts, radius, tseg, rseg=10):
        return self.res(name, 'geometry', {'type': 'tube', 'points': [vec(p, r4) for p in pts],
                                           'radius': radius, 'tubularSegments': tseg, 'radialSegments': rseg,
                                           'closed': False, 'curveType': 'centripetal', 'tension': 0.5})

    def mat(self, mid, defaults=MAT_DEFAULT, **kw):
        d = dict(defaults)
        d.update(kw)
        return self.res('mat-' + mid, 'material', d)

    def raw_mat(self, rid, data):
        """Material with an explicit id and a minimal record (server fills the defaults)."""
        return self.res(rid, 'material', dict(data), raw_id=True)

    # nodes ---------------------------------------------------------------
    def node(self, nid, parent, pos, name, rot=None, scale=None, raw_id=False, **kw):
        n = {'id': nid if raw_id else self.P + nid,
             'parentId': (parent if raw_id else self.P + parent) if parent else None,
             'name': clean_str(name), 'position': vec(pos, r4)}
        if rot is not None and (not self.omit_identity or any(abs(a) > 1e-9 for a in rot)):
            n['rotation'] = [round(a + 0.0, 5) for a in rot]
        if scale is not None and (not self.omit_identity or any(abs(a - 1) > 1e-9 for a in scale)):
            n['scale'] = vec(scale, r4)
        n.update(kw)
        self.nodes.append(n)
        return n

    def mesh(self, nid, parent, pos, name, geom, mats, beh=HIDE, rot=None, scale=None, instances=None, **kw):
        r = {'geometry': geom, 'materials': mats if isinstance(mats, list) else [mats], 'mode': 'mesh'}
        if instances:
            r['instances'] = instances
        return self.node(nid, parent, pos, name, rot, scale, render=r, behavior=dict(beh), **kw)

    def product(self, record):
        rec = {k: (clean_str(v) if isinstance(v, str) and k not in ('url', 'specsUrl') else v)
               for k, v in record.items()}
        self.products.append(rec)
        return rec['modelKey']

    def bundle(self):
        return {'resources': list(self.resources.values()), 'products': list(self.products),
                'nodes': list(self.nodes)}


# ------------------------------------------------------------------ catalogue
# Elements never ship a product of their own. A caller that has researched one passes a product
# dict (the same fields as a products/ module's PRODUCT):
#   {"item", "name", "retailer", "url", "price", "size", "dimensions_m": [w, d, h],
#    "status" or "researched", optional unit coverage such as "unit_m2" or "unit_length_m"}
PRODUCT_REQUIRED = ('item', 'name', 'retailer', 'url', 'price')


def product_keys(prefix, key, enabled=True):
    """productKey/catalogueKey fields for a node, or {} when the element has no catalogue entry."""
    if not key or not enabled:
        return {}
    return {'productKey': prefix + key, 'catalogueKey': prefix + key}


def catalogue_entry(model_key, room, product, qty, fit=None):
    """upsert_product record from a researched product dict (raises if a required field is missing)."""
    missing = [k for k in PRODUCT_REQUIRED if k not in product]
    dims = product.get('dimensions') or product.get('dimensions_m')
    if missing or not dims or len(dims) != 3:
        raise ValueError('product for %s needs %s and dimensions_m [w, d, h]; missing %s' % (
            model_key, ', '.join(PRODUCT_REQUIRED), missing or ['dimensions_m']))
    status = product.get('status') or ('Verified ' + product['researched'] if product.get('researched')
                                       else 'Unverified')
    rec = dict(modelKey=model_key, room=room, item=product['item'], name=product['name'],
               retailer=product['retailer'], price=product['price'], qty=max(1, int(qty or 1)),
               size=product.get('size', ''), fit=fit or product.get('fit', ''), status=status,
               url=product['url'], dimensions=list(dims))
    for k in ('specsUrl', 'specsLabel'):
        if product.get(k):
            rec[k] = product[k]
    return rec


def merge(*bundles):
    """Union of bundles with last-write-wins semantics: resources by id, products by modelKey and
    nodes by id, a later definition replacing an earlier one in place, exactly like re-sending it to
    the server. (lib.payload.merge keeps identical duplicates once but rejects conflicting ones; use
    this one when a later bundle is meant to override an earlier one.)
    Other list keys concatenate (order-preserving, de-duplicated); dict keys merge."""
    res, prods, nodes, seen = {}, {}, [], {}
    extra = {}
    for b in bundles:
        if not b:
            continue
        for r in b.get('resources', []):
            res[r['id']] = r
        for p in b.get('products', []):
            prods[p['modelKey']] = p
        for n in b.get('nodes', []):
            if n['id'] in seen:
                nodes[seen[n['id']]] = n
            else:
                seen[n['id']] = len(nodes)
                nodes.append(n)
        for k, v in b.items():
            if k in ('resources', 'products', 'nodes'):
                continue
            if isinstance(v, list):
                cur = extra.setdefault(k, [])
                cur += [x for x in v if x not in cur]
            elif isinstance(v, dict):
                extra[k] = {**extra.get(k, {}), **v}
            else:
                extra[k] = v
    out = {'resources': list(res.values()), 'products': list(prods.values()), 'nodes': nodes}
    out.update(extra)
    return out


def write_payloads(bundle, out_dir, **kw):
    """Write queue payload files through the shared lib (cleaning, validation, batching)."""
    if _lib is None:
        raise RuntimeError('lib/payload.py is not importable')
    return _lib.write_payloads(bundle, out_dir, **kw)


# ------------------------------------------------------------------ matrices (column-major 4x4)
def M(tx, ty, tz, sx=1, sy=1, sz=1):
    return [r4(sx), 0, 0, 0, 0, r4(sy), 0, 0, 0, 0, r4(sz), 0, r4(tx), r4(ty), r4(tz), 1]


def MRX(th, tx, ty, tz, sx=1, sy=1, sz=1):
    c, s = math.cos(th), math.sin(th)
    return [r4(sx), 0, 0, 0, 0, r4(c * sy), r4(s * sy), 0, 0, r4(-s * sz), r4(c * sz), 0,
            r4(tx), r4(ty), r4(tz), 1]


def euler(rx, ry, rz):
    """3x3 rotation for a three.js 'XYZ' Euler (R = Rx Ry Rz) as nested lists."""
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    Rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    Ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    Rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return mm(mm(Rx, Ry), Rz)


def mm(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def mv(A, v):
    return [sum(A[i][k] * v[k] for k in range(3)) for i in range(3)]


def diag(s):
    return [[s[0], 0, 0], [0, s[1], 0], [0, 0, s[2]]]


def mat4(R, t, f=r4):
    out = []
    for c in range(3):
        out += [f(R[0][c]), f(R[1][c]), f(R[2][c]), 0]
    return out + [f(t[0]), f(t[1]), f(t[2]), 1]


def mtx(t, rot=(0, 0, 0), s=(1, 1, 1), f=r4):
    return mat4(mm(euler(*rot), diag(s)), t, f)


def euler_xyz_from(R):
    """Inverse of euler(): three.js XYZ angles of a rotation matrix."""
    ry = math.asin(max(-1.0, min(1.0, R[0][2])))
    if abs(R[0][2]) < 0.9999999:
        rx = math.atan2(-R[1][2], R[2][2])
        rz = math.atan2(-R[0][1], R[0][0])
    else:
        rx = math.atan2(R[2][1], R[1][1])
        rz = 0.0
    return [rx, ry, rz]


def dense(poly, step=0.08):
    """Resample a polyline so a centripetal tube follows its corners closely."""
    out = []
    for (a, b) in zip(poly[:-1], poly[1:]):
        n = max(1, int(math.dist(a, b) / step))
        for k in range(n):
            t = k / n
            out.append([a[i] + (b[i] - a[i]) * t for i in range(3)])
    out.append(list(poly[-1]))
    return out


# ------------------------------------------------------------------ outer-wall faces
COMPASS = {'north': 'N', 'south': 'S', 'west': 'W', 'east': 'E'}


class Face:
    """One axis-aligned outer wall of a spec (any rectilinear footprint)."""

    def __init__(self, wall):
        self.wall = wall
        self.key = COMPASS.get(wall['id'], wall['id'])
        self.axis = wall['axis']                       # 'x': wall runs along x (plane is z)
        if self.axis == 'x':
            self.plane, inner = wall['line_z_outer'], wall['inner_z']
        else:
            self.plane, inner = wall['line_x_outer'], wall['inner_x']
        self.sign = 1 if self.plane > inner else -1    # outward direction along the plane axis
        self.thickness = abs(self.plane - inner)
        self.lo, self.hi = wall['from'], wall['to']
        self.L = self.hi - self.lo
        if self.axis == 'x':
            self.out = (0, 0, self.sign)
            self.rot = 0.0 if self.sign > 0 else PI
        else:
            self.out = (self.sign, 0, 0)
            self.rot = PI / 2 if self.sign > 0 else -PI / 2
        # local +x seen from outside: S (+z out) -> +x, N -> -x, W (-x out) -> +z, E -> -z
        self.origin_end = 'lo' if (self.axis == 'x') == (self.sign > 0) else 'hi'

    def world(self, s, y, o=0.0):
        """Along-axis coordinate s, height y, outward offset o -> world [x, y, z]."""
        if self.axis == 'x':
            return [s, y, self.plane + self.sign * o]
        return [self.plane + self.sign * o, y, s]

    def origin(self, s0, s1, y=0.0):
        """World position of local x=0 for a face-local part covering along-axis [s0, s1]."""
        return self.world(s1 if self.origin_end == 'hi' else s0, y, 0.0)

    def lx(self, s, s0, s1):
        return (s1 - s) if self.origin_end == 'hi' else (s - s0)

    def yaw(self):
        return [0, self.rot, 0]


def faces(spec):
    return {f.key: f for f in (Face(w) for w in spec['outerWalls'])}


def face_of_opening(spec, oid):
    for w in spec['outerWalls']:
        for o in w['openings']:
            if o['id'] == oid:
                return Face(w), o
    raise KeyError(oid)


def find_opening(spec, oid):
    for group in ('interiorWalls', 'outerWalls'):
        for w in spec[group]:
            for o in w['openings']:
                if o['id'] == oid:
                    return w, o
    raise KeyError(oid)


def footprint_rect(spec):
    """(x0, z0, x1, z1) of the outer footprint from the outer walls (rectangular footprints)."""
    xs, zs = [], []
    for w in spec['outerWalls']:
        if w['axis'] == 'x':
            zs.append(w['line_z_outer'])
            xs += [w['from'], w['to']]
        else:
            xs.append(w['line_x_outer'])
            zs += [w['from'], w['to']]
    return min(xs), min(zs), max(xs), max(zs)


def footprint_polygon(spec):
    """Outer footprint polygon [[x, z], ...]: spec['footprintPolygon'] when present, else the rectangle."""
    if spec.get('footprintPolygon'):
        return [list(p) for p in spec['footprintPolygon']]
    x0, z0, x1, z1 = footprint_rect(spec)
    return [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]


# ------------------------------------------------------------------ rooms
def wall_faces(spec):
    """Inner wall-face coordinates: x of faces of z-axis walls, z of faces of x-axis walls."""
    fx, fz = [], []
    for w in spec['outerWalls']:
        (fz if w['axis'] == 'x' else fx).append(w['inner_z'] if w['axis'] == 'x' else w['inner_x'])
    for w in spec['interiorWalls']:
        h = w['thickness'] / 2
        if w['axis'] == 'z':
            fx += [w['center_x'] - h, w['center_x'] + h]
        else:
            fz += [w['center_z'] - h, w['center_z'] + h]
    return fx, fz


def snap(v, faces_, tol=0.007):
    best = min(faces_, key=lambda f: abs(f - v))
    return best if abs(best - v) < tol else v


def room_polygons(spec, snap_tol=0.007):
    """{room id: polygon [[x, z], ...]} with rectangle bounds snapped to the nearest wall face
    (within snap_tol). A room may instead give its own 'polygon'."""
    fx, fz = wall_faces(spec)
    out = {}
    for rm in spec['rooms']:
        if rm.get('polygon'):
            out[rm['id']] = [[snap(x, fx, snap_tol), snap(z, fz, snap_tol)] for x, z in rm['polygon']]
            continue
        x0, z0, x1, z1 = rm['bounds']
        x0, x1 = snap(x0, fx, snap_tol), snap(x1, fx, snap_tol)
        z0, z1 = snap(z0, fz, snap_tol), snap(z1, fz, snap_tol)
        out[rm['id']] = [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]
    return out


def poly_bounds(poly):
    xs = [p[0] for p in poly]
    zs = [p[1] for p in poly]
    return min(xs), min(zs), max(xs), max(zs)


def is_rect(poly):
    x0, z0, x1, z1 = poly_bounds(poly)
    return len(poly) == 4 and all(p[0] in (x0, x1) and p[1] in (z0, z1) for p in poly)


def point_in_poly(x, z, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % n]
        if (z1 > z) != (z2 > z):
            xi = x1 + (z - z1) * (x2 - x1) / (z2 - z1)
            if x < xi:
                inside = not inside
    return inside


def poly_x_intervals(poly, z):
    """Sorted x-intervals of a simple polygon on the horizontal line at z."""
    xs = []
    n = len(poly)
    for i in range(n):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % n]
        if (z1 > z) != (z2 > z):
            xs.append(x1 + (z - z1) * (x2 - x1) / (z2 - z1))
    xs.sort()
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]


def band_x_intervals(poly, za, zb):
    """x-intervals inside the polygon for the whole band za..zb (intersection of the intervals sampled
    just inside both edges and at the middle; exact for rectilinear rooms)."""
    eps = min(1e-4, (zb - za) / 4)
    res = None
    for z in (za + eps, (za + zb) / 2, zb - eps):
        iv = poly_x_intervals(poly, z)
        if res is None:
            res = iv
        else:
            nxt = []
            for a, b in res:
                for c, d in iv:
                    lo, hi = max(a, c), min(b, d)
                    if hi > lo:
                        nxt.append((lo, hi))
            res = nxt
    return res or []


def subtract(iv, cuts, min_len=0.0):
    out = [iv]
    for a, b in cuts:
        nxt = []
        for s, e in out:
            if b <= s or a >= e:
                nxt.append((s, e))
            else:
                if a > s:
                    nxt.append((s, a))
                if b < e:
                    nxt.append((b, e))
        out = nxt
    return [(s, e) for s, e in out if e - s >= min_len]
