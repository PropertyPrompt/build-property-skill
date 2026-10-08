#!/usr/bin/env python3
"""PropertyPrompt payload kit: builders, validation and queue-ready payload files.

Stdlib only, Python 3.9+. Shared contract for the kit's {shell,products,
elements,measure} modules and for the modeller agents in workflow/build.js.

A *bundle* is a plain dict:
    {"resources": [...], "products": [...], "nodes": [...]}
with optional keys
    "external":        ids of nodes/resources/products that already exist on the
                       server (parents, shared geometry, catalogue entries)
    "removeNodes":     node ids to delete (remove_scene_node, written first). Prefer
                       replacing a node under its existing id: a removal can be refused
                       by the host's permissions, and it then stops the whole batch
    "removeProducts":  product keys to delete (remove_product, written last)
    "removeResources": resource ids to delete (remove_scene_resource, written last)
    "settingsPatch":   dict for update_settings (written after the nodes)
    "rooms":           complete rooms array for update_settings (needs settingsPatch;
                       write_payloads adds an empty patch when only rooms are given)
    "layerIgnore":     node ids or id prefixes excluded from the layer check

write_payloads(bundle, out_dir) cleans, rounds, validates, checks layer separation and
writes NN-<tool>.json files of the form {"tool": T, "args": A}; the queue writer adds
propertyId and expectedRevision. Everything in one bundle is meant to go through ONE
queue slot (resources and the nodes that use them together), because the server's
cleanup_unused_resources deletes resources that no node references.

Units are metres. Box dimensions are [x, y, z]; catalogue dimensions are [w, d, h].
Instance matrices are column-major with translation at indices 12, 13, 14.
"""
import json
import math
import os
import re
import sys
import unicodedata

try:
    from lib.schema_check import Validator
except ImportError:  # imported with kit/lib itself on sys.path
    from schema_check import Validator

TAU = 6.283185  # largest arc the server accepts (2*pi rounded DOWN)
MAX_NODES = 100
MAX_RESOURCES = 20
MAX_PRODUCTS = 50
MAX_FILE_BYTES = 60 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
LAYER_GAP = 0.002  # min separation of same-facing coplanar layers in one assembly
THIN = 0.01  # an axis thinner than this makes a part a "layer"
ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
FINE_KEYS = {"bias", "normalBias"}  # numbers that must keep more than 3 dp
SCHEMA_FILE = "server-schema.json"  # get_schema's "schemas", saved in the work dir (Stage 5)
HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)

# --------------------------------------------------------------------------- basics

_ASCII = {
    "\u00d7": " x ", "\u2033": " in", "\u2032": " ft", "\u2013": "-", "\u2014": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": "", "\u201d": "", "\u00b0": " deg",
    "\u00bd": " 1/2", "\u00bc": " 1/4", "\u00be": " 3/4", "\u2026": "...",
    "\u00a0": " ", "\u00ae": "", "\u2122": "", "\u00e9": "e",
}


def r3(x):
    """Round to 3 decimals; -0.0 becomes 0.0 and integral values stay compact."""
    v = round(float(x), 3)
    if v == 0:
        return 0
    return int(v) if v.is_integer() else v


def clean_str(s):
    """Remove double quotes and backslashes, transliterate to ASCII, collapse spaces.

    Writer agents retype every payload byte into a tool call; quotes (inch marks),
    backslashes and \\u escapes are where they make mistakes.
    """
    s = str(s)
    for k, v in _ASCII.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.replace('"', "").replace("\\", "")
    return re.sub(r"\s+", " ", s).strip()


def _num(v, key=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    if isinstance(v, float) and not math.isfinite(v):
        raise ValueError("non-finite number at %r" % key)
    if key in FINE_KEYS:
        v = round(float(v), 6)
        return 0 if v == 0 else v
    return r3(v)


def _clean(o, key=None):
    """Recursively clean strings and round numbers."""
    if isinstance(o, dict):
        return {k: _clean(v, k) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v, key) for v in o]
    if isinstance(o, str):
        return clean_str(o)
    return _num(o, key)


def _v3(v):
    return [r3(v[0]), r3(v[1]), r3(v[2])]


def _geo(id, data):
    return {"id": id, "kind": "geometry", "data": data}


# ------------------------------------------------------------------ resource builders

def box(id, dims):
    return _geo(id, {"type": "box", "dimensions": _v3(dims)})


def rounded_box(id, dims, radius, segments=3):
    d = _v3(dims)
    limit = math.floor(min(d) / 2 * 1000) / 1000  # server: radius <= min(dims)/2
    rad = min(r3(radius), limit)
    if rad <= 0:
        return box(id, d)
    data = {"type": "roundedBox", "dimensions": d, "radius": rad}
    if segments != 3:
        data["segments"] = int(segments)
    return _geo(id, data)


def cylinder(id, rt, rb, h, seg=32, open_ended=False):
    data = {"type": "cylinder", "radiusTop": r3(rt), "radiusBottom": r3(rb), "height": r3(h)}
    if seg != 32:
        data["radialSegments"] = int(seg)
    if open_ended:
        data["openEnded"] = True
    return _geo(id, data)


def sphere(id, r, ws=24, hs=16):
    data = {"type": "sphere", "radius": r3(r)}
    if ws != 24:
        data["widthSegments"] = int(ws)
    if hs != 16:
        data["heightSegments"] = int(hs)
    return _geo(id, data)


def torus(id, r, tube, rs=8, ts=64, arc=TAU):
    """Torus in local XY around Z. arc is clamped to <= 6.283185 (2*pi is rejected)."""
    data = {"type": "torus", "radius": r3(r), "tube": r3(tube)}
    if rs != 8:
        data["radialSegments"] = int(rs)
    if ts != 64:
        data["tubularSegments"] = int(ts)
    a = min(float(arc), TAU)
    if a < TAU - 1e-6:
        data["arc"] = r3(a)
    return _geo(id, data)


def extrude(id, points, depth, holes=(), bevel=0, bevel_segments=1):
    """Local XY polygon extruded from z=0 to depth. Rotate [-pi/2,0,0] to extrude up
    (local y then maps to world -z). Points are not recentred; no closing point."""
    pts = [[r3(x), r3(y)] for x, y in points]
    data = {"type": "extrude", "points": pts, "depth": r3(depth)}
    if holes:
        data["holes"] = [[[r3(x), r3(y)] for x, y in h] for h in holes]
    if bevel:
        data["bevel"] = r3(bevel)
        if bevel_segments != 1:
            data["bevelSegments"] = int(bevel_segments)
    return _geo(id, data)


def tube(id, points, radius, ts=64, rs=8, closed=False, curve_type="centripetal", tension=0.5):
    data = {"type": "tube", "points": [_v3(p) for p in points], "radius": r3(radius)}
    if ts != 64:
        data["tubularSegments"] = int(ts)
    if rs != 8:
        data["radialSegments"] = int(rs)
    if closed:
        data["closed"] = True
    if curve_type != "centripetal":
        data["curveType"] = curve_type
    if tension != 0.5:
        data["tension"] = r3(tension)
    return _geo(id, data)


def plane(id, w, h):
    """Plane in local XY facing +Z. Rotate [-pi/2,0,0] to lie flat facing up."""
    return _geo(id, {"type": "plane", "width": r3(w), "height": r3(h)})


def circle(id, r, seg=32):
    data = {"type": "circle", "radius": r3(r)}
    if seg != 32:
        data["segments"] = int(seg)
    return _geo(id, data)


def ring(id, inner, outer, seg=32):
    data = {"type": "ring", "innerRadius": r3(inner), "outerRadius": r3(outer)}
    if seg != 32:
        data["segments"] = int(seg)
    return _geo(id, data)


def icosahedron(id, r, detail=0):
    data = {"type": "icosahedron", "radius": r3(r)}
    if detail:
        data["detail"] = int(detail)
    return _geo(id, data)


_MAT_DEFAULTS = {
    "name": "", "color": "#ffffff", "roughness": 0.75, "metalness": 0, "emissive": "#000000",
    "emissiveIntensity": 1, "opacity": 1, "transparent": False, "depthWrite": True,
    "side": "front", "toneMapped": True, "flatShading": False, "vertexColors": False,
}


def _camel(k):
    parts = k.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def material(id, color, roughness=0.75, metalness=0.0, type="standard", **extra):
    """Material resource. extra accepts camelCase or snake_case schema keys
    (name, emissive, emissive_intensity, opacity, transparent, depth_write, side, ...).
    Fields at their schema defaults are omitted."""
    data = {"type": type, "color": color, "roughness": roughness, "metalness": metalness}
    for k, v in extra.items():
        data[_camel(k)] = v
    out = {"type": data.pop("type")}
    for k, v in data.items():
        if k in _MAT_DEFAULTS and v == _MAT_DEFAULTS[k]:
            continue
        out[k] = _num(v, k)
    return {"id": id, "kind": "material", "data": out}


# ---------------------------------------------------------------------- node helpers

def behavior(height="fixed", cutaway="keep", desktop=False, vr=False, cutaway_height=0.78):
    """Explicit behaviour. All five keys are written so the node is unambiguously in
    explicit-behaviour mode (legacy role handling never applies)."""
    return {"height": height, "cutaway": cutaway, "cutawayHeight": r3(cutaway_height),
            "desktopCollision": bool(desktop), "vrCollision": bool(vr)}


# Common presets
SOLID = behavior(desktop=True, vr=True)          # large furniture / fixtures
SEAT = behavior(desktop=False, vr=True)          # beds, sofas, chairs
DECOR = behavior()                               # no collision
HIDE = behavior(cutaway="hide")                  # roofs, ceilings, glazing


def render(geometry, materials, instances=None, mode=None):
    out = {"geometry": geometry, "materials": [materials] if isinstance(materials, str) else list(materials)}
    if instances:
        out["instances"] = instances
    if mode and mode != "mesh":
        out["mode"] = mode
    return out


def node(id, parent=None, name="", position=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1), **fields):
    """Scene node with only non-default fields plus the required id and parentId.

    fields: any nodeInput key, camelCase or snake_case (render, light, behavior, role,
    container, product_key, catalogue_key, room_id, enterable, ceiling_drop, raycast,
    cast_shadow, receive_shadow, floor_id, visible, primitive, vr_footprint ...).
    Convenience: geom=<id>, mat=<id or list>, inst=<matrices> build render for you.
    Note put_scene_nodes replaces transforms: omitted position/rotation/scale reset to
    defaults, which is exactly what this omits.
    """
    n = {"id": id, "parentId": parent}
    if name:
        n["name"] = name
    p = _v3(position)
    if p != [0, 0, 0]:
        n["position"] = p
    ro = _v3(rotation)
    if ro != [0, 0, 0]:
        n["rotation"] = ro
    s = _v3(scale)
    if s != [1, 1, 1]:
        n["scale"] = s
    geom = fields.pop("geom", None)
    mat = fields.pop("mat", None)
    inst = fields.pop("inst", None)
    if geom is not None:
        n["render"] = render(geom, mat or [], inst)
    for k, v in fields.items():
        if v is None:
            continue
        n[_camel(k)] = v
    return n


# ------------------------------------------------------------------------- matrices

def inst_t(*translations):
    """Column-major translation matrices, one per (x, y, z)."""
    return [[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, r3(x), r3(y), r3(z), 1] for x, y, z in translations]


def inst_trs(t, ry=0, s=(1, 1, 1)):
    """One column-major matrix: translate t, rotate ry about Y, scale s."""
    c, n = math.cos(ry), math.sin(ry)
    sx, sy, sz = s
    m = [c * sx, 0, -n * sx, 0, 0, sy, 0, 0, n * sz, 0, c * sz, 0, t[0], t[1], t[2], 1]
    return [r3(v) for v in m]


def mat_euler(t=(0, 0, 0), r=(0, 0, 0), s=(1, 1, 1)):
    """Column-major T * R(Euler XYZ, three.js order) * S as a flat 16-list."""
    a, b, c = r
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    # rows of Rx*Ry*Rz (three.js makeRotationFromEuler, order XYZ)
    m = [
        [cb * cc, -cb * sc, sb],
        [ca * sc + sa * sb * cc, ca * cc - sa * sb * sc, -sa * cb],
        [sa * sc - ca * sb * cc, sa * cc + ca * sb * sc, ca * cb],
    ]
    out = []
    for col in range(3):
        out += [m[0][col] * s[col], m[1][col] * s[col], m[2][col] * s[col], 0]
    return out + [t[0], t[1], t[2], 1]


def inst_euler(t, r=(0, 0, 0), s=(1, 1, 1)):
    return [r3(v) for v in mat_euler(t, r, s)]


def _mul(a, b):
    """Column-major 4x4 multiply a*b."""
    o = [0.0] * 16
    for col in range(4):
        for row in range(4):
            o[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return o


def _apply(m, p):
    x, y, z = p
    return (m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14])


# ------------------------------------------------------------------------- bundles

def bundle(resources=(), products=(), nodes=(), **extra):
    b = {"resources": list(resources), "products": list(products), "nodes": list(nodes)}
    b.update(extra)
    return b


def _dedupe(items, key):
    """Drop exact duplicates by id (a module placed twice ships its geometry once);
    keep conflicting duplicates so validate() reports them."""
    seen, out = {}, []
    for it in items:
        k = it.get(key) if isinstance(it, dict) else None
        if k is not None and k in seen and seen[k] == it:
            continue
        if k is not None and k not in seen:
            seen[k] = it
        out.append(it)
    return out


def merge(*bundles):
    """Concatenate bundles. List keys concatenate (identical resources/products with the
    same id are kept once); dict keys such as settingsPatch merge."""
    out = {"resources": [], "products": [], "nodes": []}
    for b in bundles:
        if not b:
            continue
        for k, v in b.items():
            if isinstance(v, list):
                out.setdefault(k, [])
                out[k] = out[k] + list(v)
            elif isinstance(v, dict):
                out.setdefault(k, {})
                out[k] = {**out[k], **v}
            else:
                out[k] = v
    out["resources"] = _dedupe(out["resources"], "id")
    out["products"] = _dedupe(out["products"], "modelKey")
    return out


def load_materials():
    """The shared m-* library as resource dicts (publish once per property)."""
    with open(os.path.join(KIT, "lib", "materials.json")) as f:
        return json.load(f)["resources"]


def library_ids():
    try:
        return {r["id"] for r in load_materials()}
    except (OSError, ValueError, KeyError):
        return set()


# ------------------------------------------------------------------------- geometry

def geometry_bounds(data):
    """Local AABB (min, max) of a geometry resource's data, or None if unknown."""
    t = data.get("type")
    if t in ("box", "roundedBox"):
        d = data["dimensions"]
        return (-d[0] / 2, -d[1] / 2, -d[2] / 2), (d[0] / 2, d[1] / 2, d[2] / 2)
    if t == "cylinder":
        r = max(data.get("radiusTop", 0), data["radiusBottom"])
        h = data["height"] / 2
        return (-r, -h, -r), (r, h, r)
    if t in ("sphere", "icosahedron"):
        r = data["radius"]
        return (-r, -r, -r), (r, r, r)
    if t == "torus":
        R = data["radius"] + data["tube"]
        tb = data["tube"]
        return (-R, -R, -tb), (R, R, tb)
    if t == "plane":
        w, h = data["width"] / 2, data["height"] / 2
        return (-w, -h, 0), (w, h, 0)
    if t in ("circle", "ring"):
        r = data.get("radius", data.get("outerRadius"))
        return (-r, -r, 0), (r, r, 0)
    if t == "extrude":
        b = data.get("bevel", 0)
        xs = [p[0] for p in data["points"]]
        ys = [p[1] for p in data["points"]]
        return (min(xs) - b, min(ys) - b, -b), (max(xs) + b, max(ys) + b, data["depth"] + b)
    if t == "tube":
        r = data["radius"]
        pts = data["points"]
        return (tuple(min(p[i] for p in pts) - r for i in range(3)),
                tuple(max(p[i] for p in pts) + r for i in range(3)))
    if t == "buffer":
        p = data["positions"]
        return (min(p[0::3]), min(p[1::3]), min(p[2::3])), (max(p[0::3]), max(p[1::3]), max(p[2::3]))
    return None


def _box_corners(lo, hi):
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def _xform_aabb(m, lo, hi):
    pts = [_apply(m, c) for c in _box_corners(lo, hi)]
    return (tuple(min(p[i] for p in pts) for i in range(3)),
            tuple(max(p[i] for p in pts) for i in range(3)))


def _local_matrix(n):
    return mat_euler(n.get("position", (0, 0, 0)), n.get("rotation", (0, 0, 0)), n.get("scale", (1, 1, 1)))


def _geometries(resources):
    if isinstance(resources, dict) and "resources" in resources:
        resources = resources["resources"]
    if isinstance(resources, dict):
        return resources
    return {r["id"]: r["data"] for r in (resources or []) if r.get("kind") == "geometry"}


def _node_parts(n, geos, parent_m=None):
    """[(min, max)] per drawn instance in the frame of parent_m (default: parent frame)."""
    rd = n.get("render") or {}
    g = geos.get(rd.get("geometry"))
    if g is None:
        prim = n.get("primitive")
        if prim and prim.get("shape") in ("box", "cylinder", "sphere"):
            d = prim["dimensions"]
            g = {"type": "box", "dimensions": d}
        else:
            return []
    lb = geometry_bounds(g)
    if lb is None:
        return []
    m = _local_matrix(n)
    if parent_m is not None:
        m = _mul(parent_m, m)
    inst = rd.get("instances") or [None]
    return [_xform_aabb(_mul(m, i) if i else m, *lb) for i in inst]


def aabb(node, resources=None, world_matrix=None):
    """AABB (min, max) of a node in its parent frame (or in world if world_matrix is the
    parent's world matrix), covering all instances. None if no known geometry."""
    parts = _node_parts(node, _geometries(resources), world_matrix)
    if not parts:
        return None
    return (tuple(min(p[0][i] for p in parts) for i in range(3)),
            tuple(max(p[1][i] for p in parts) for i in range(3)))


def world_matrices(nodes):
    """{id: (world matrix, root id)} for nodes whose chain is inside `nodes`; a node
    whose parent is external uses that parent as root and its frame as world."""
    by = {n["id"]: n for n in nodes}
    out = {}

    def walk(i, depth=0):
        if i in out:
            return out[i]
        if depth > 200:
            raise ValueError("parent cycle at %s" % i)
        n = by[i]
        p = n.get("parentId")
        local = _local_matrix(n)
        if p is None:
            out[i] = (local, i)
        elif p in by:
            pm, root = walk(p, depth + 1)
            out[i] = (_mul(pm, local), root)
        else:
            out[i] = (local, p)
        return out[i]

    for i in by:
        walk(i)
    return out


# ---------------------------------------------------------------------- layer check

def _ignored(nid, ignore):
    return any(nid == x or nid.startswith(x) for x in ignore)


def _node_boxes(n, geos, parent_m=None):
    """[(centre, axes, half)] per drawn instance: an oriented box in the frame of parent_m, with
    unit axes (the part's own local x, y, z) and half extents along them."""
    rd = n.get("render") or {}
    g = geos.get(rd.get("geometry"))
    if g is None:
        prim = n.get("primitive")
        if prim and prim.get("shape") in ("box", "cylinder", "sphere"):
            g = {"type": "box", "dimensions": prim["dimensions"]}
        else:
            return []
    lb = geometry_bounds(g)
    if lb is None:
        return []
    lo, hi = lb
    m = _local_matrix(n)
    if parent_m is not None:
        m = _mul(parent_m, m)
    out = []
    for i in rd.get("instances") or [None]:
        mi = _mul(m, i) if i else m
        c = _apply(mi, tuple((lo[k] + hi[k]) / 2 for k in range(3)))
        axes, half = [], []
        for k in range(3):
            col = (mi[4 * k], mi[4 * k + 1], mi[4 * k + 2])
            s = math.sqrt(sum(v * v for v in col))
            if s < 1e-12:
                break
            axes.append(tuple(v / s for v in col))
            half.append(s * (hi[k] - lo[k]) / 2)
        if len(axes) == 3:
            out.append((c, axes, half))
    return out


def _canonical(u):
    """u flipped so its first non-zero component is positive."""
    for v in u:
        if abs(v) > 1e-6:
            return u if v > 0 else tuple(-x for x in u)
    return u


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _face_overlap(A, ka, B, kb):
    """Smallest overlap of the two boxes' faces normal to their parallel axes ka / kb, measured in
    the face plane on each box's in-plane axes (separating-axis test; > 0 means they overlap)."""
    (ca, xa, ha), (cb, xb, hb) = A, B
    best = float("inf")
    for axes_src, k_skip in ((xa, ka), (xb, kb)):
        for j in range(3):
            if j == k_skip:
                continue
            u = axes_src[j]
            ra = sum(abs(_dot(u, xa[i])) * ha[i] for i in range(3) if i != ka)
            rb = sum(abs(_dot(u, xb[i])) * hb[i] for i in range(3) if i != kb)
            pa, pb = _dot(ca, u), _dot(cb, u)
            best = min(best, min(pa + ra, pb + rb) - max(pa - ra, pb - rb))
    return best


def _axis_name(u):
    for k in range(3):
        if abs(abs(u[k]) - 1) < 1e-6:
            return "xyz"[k]
    return "(%.3f, %.3f, %.3f)" % u


def check_layers(b, gap=LAYER_GAP, ignore=()):
    """Find coplanar overlapping layers inside one assembly after rounding.

    Two parts of the same root assembly z-fight when same-facing faces lie within `gap` of each
    other along a shared face normal where at least one part is thin (< THIN), and the faces overlap
    in their plane. Each part is tested as an oriented box in its own rotation, so parts turned to
    an angle (boards along an angled wall) are compared exactly, not by their bounding boxes.
    Downward faces resting on y=0 are skipped. Returns a list of issue strings (empty = OK).
    """
    nodes = _clean(b.get("nodes", []))
    geos = _geometries(_clean(b.get("resources", [])))
    ignore = tuple(ignore) + tuple(b.get("layerIgnore", ()))
    wm = world_matrices(nodes)
    items = []  # (root, label, box)
    for n in nodes:
        if _ignored(n["id"], ignore) or n.get("light"):
            continue
        m, root = wm[n["id"]]
        p = n.get("parentId")
        parent_m = wm[p][0] if p in wm else None
        parts = _node_boxes(n, geos, parent_m)
        for k, box_ in enumerate(parts):
            label = n["id"] if len(parts) == 1 else "%s[%d]" % (n["id"], k)
            items.append((root, label, box_))
    eps = 1e-4
    faces = []    # (item index, axis k, normal, side, offset, thickness)
    buckets = {}
    for idx, (root, label, (c, axes, half)) in enumerate(items):
        for k in range(3):
            # faces are measured along the rounded normal shared by every part in the bucket, so a part
            # whose rotation is a rounding error off the axis is measured like an axis-aligned one
            key = tuple(round(v, 3) + 0.0 for v in _canonical(axes[k]))
            ln = math.sqrt(_dot(key, key))
            u = tuple(v / ln for v in key)
            centre = _dot(c, u)
            ext = sum(abs(_dot(u, axes[i])) * half[i] for i in range(3))
            for side, f in (("min", centre - ext), ("max", centre + ext)):
                fi = len(faces)
                faces.append((idx, k, u, side, f, 2 * half[k]))
                buckets.setdefault((root, key, side, math.floor(f / gap)), []).append(fi)
    seen = set()
    issues = []
    for (root, key, side, cell), members in buckets.items():
        cands = []
        for dc in (-1, 0, 1):
            cands += buckets.get((root, key, side, cell + dc), [])
        for fi in members:
            for fj in cands:
                ia, ka, u, _, fa, ta = faces[fi]
                ib, kb, _, _, fb, tb = faces[fj]
                if ib <= ia or (ia, ib, key, side) in seen:
                    continue
                seen.add((ia, ib, key, side))
                if min(ta, tb) >= THIN:
                    continue
                if abs(fa - fb) >= gap - eps:
                    continue
                if side == "min" and abs(u[1] - 1) < 1e-6 and abs(fa) < gap and abs(fb) < gap:
                    continue  # bottoms resting on the floor are never seen
                if _face_overlap(items[ia][2], ka, items[ib][2], kb) <= 0.001:
                    continue
                issues.append("%s and %s (assembly %s): %s faces %.4f / %.4f along %s are closer than %.3f m"
                              % (items[ia][1], items[ib][1], root, side, fa, fb, _axis_name(u), gap))
    return issues


# ---------------------------------------------------------------------- cutaway check

CUTAWAY_HEIGHT = 0.78


def _spec_ceiling(out_dir, default=2.44):
    """ceilingHeight from the property's spec.json, found in out_dir or up to three folders above it
    (payloads live in <workDir>/payloads/<folder>); `default` if there is none."""
    d = os.path.abspath(out_dir)
    for _ in range(4):
        try:
            with open(os.path.join(d, "spec.json")) as f:
                return float(json.load(f)["ceilingHeight"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        d = os.path.dirname(d)
    return default


def check_cutaway(b, height=CUTAWAY_HEIGHT, ignore=(), ceiling_height=2.44):
    """Warn about room assemblies that float above the cutaway height but stay visible in cutaway.

    The plan view and the cutaway camera cut walls at `height`; an assembly whose lowest part is
    above it (ceiling, pendant, upper cabinet, wall shelf, art) and keeps some parts in cutaway
    covers what is under it. Floor-standing assemblies are not flagged, however tall. Only roots in
    the bundle with a roomId are checked. ceilingDrop uses settingsPatch ceiling/floor
    heights, or ceiling_height (default 2.44 m). Returns warning strings.
    """
    nodes = _clean(b.get("nodes", []))
    geos = _geometries(_clean(b.get("resources", [])))
    ignore = tuple(ignore) + tuple(b.get("layerIgnore", ()))
    # The viewer overrides local Y for ceilingDrop before applying cutaway.
    settings = b.get("settingsPatch") or {}
    ceiling_height = settings.get("ceilingHeight", ceiling_height)
    floors = {f["id"]: f["ceilingHeight"] for f in settings.get("floors") or []}
    by = {n["id"]: n for n in nodes}
    authored = world_matrices(nodes)
    for n in nodes:
        if "ceilingDrop" not in n:
            continue
        root = by.get(authored[n["id"]][1], n)
        ceiling = floors.get(root.get("floorId"), ceiling_height)
        position = list(n.get("position", (0, 0, 0)))
        position[1] = ceiling - n["ceilingDrop"]
        n["position"] = position
    wm = world_matrices(nodes)

    def chain(n):
        # the node and its ancestors: the viewer hides a whole subtree when any of them hides
        while n is not None:
            yield n
            n = by.get(n.get("parentId"))

    low, kept = {}, {}
    for n in nodes:
        if _ignored(n["id"], ignore) or n.get("light"):
            continue
        m, root = wm[n["id"]]
        r = by.get(root)
        if r is None or r.get("parentId") is not None or not (r.get("roomId") or n.get("roomId")):
            continue
        if any(a.get("role") in ("wall", "door", "glazing") for a in chain(n)):
            continue
        p = n.get("parentId")
        parts = _node_parts(n, geos, wm[p][0] if p in wm else None)
        if not parts:
            continue
        bottom = min(lo[1] for lo, _ in parts)
        low[root] = min(low.get(root, bottom), bottom)
        if ((n.get("behavior") or {}).get("cutaway", "keep") == "keep"
                and not any((a.get("behavior") or {}).get("cutaway") == "hide" for a in chain(n))):
            kept.setdefault(root, []).append(n["id"])
    return ["%s: hangs above the cutaway height (lowest part at %.2f m) but keeps %s in cutaway; use cutaway "
            "hide" % (root, low[root], ", ".join(ids[:3]) + (" and %d more" % (len(ids) - 3) if len(ids) > 3 else ""))
            for root, ids in kept.items() if low[root] > height + 0.02]


# ----------------------------------------------------------------------- validation

def _refs_in_material(data):
    return [data[k] for k in ("map", "bumpMap", "normalMap", "roughnessMap", "metalnessMap", "alphaMap") if k in data]


def _find_up(start, name, levels=4):
    """Path of `name` in `start` or up to levels-1 folders above it (payloads live in
    <workDir>/payloads/<folder>), or None."""
    d = os.path.abspath(start)
    for _ in range(levels):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
        d = os.path.dirname(d)
    return None


def load_server_schemas(start):
    """The "schemas" object of get_schema, saved as <workDir>/server-schema.json in Stage 5, or None."""
    p = _find_up(start, SCHEMA_FILE)
    if not p:
        return None
    with open(p) as f:
        data = json.load(f)
    return data.get("schemas", data)


def _schema_errors(b, schemas):
    """Check every resource, node, product and settings key against the server's own schemas."""
    errors = []

    def run(label, value, schema):
        if schema:
            errors.extend("%s: %s" % (label, e) for e in Validator(schema, optional_defaults=True).errors_for(value, schema))

    for r in b.get("resources", []):
        run(r.get("id"), r.get("data", {}), schemas.get(r.get("kind")))
    for n in b.get("nodes", []):
        run(n.get("id"), n, schemas.get("node"))
    for p in b.get("products", []):
        run("product %s" % p.get("modelKey"), p, schemas.get("product"))
    props = (schemas.get("settings") or {}).get("properties", {})
    for k, v in (b.get("settingsPatch") or {}).items():
        if k not in props:
            errors.append("settingsPatch: unknown setting %r" % k)
        else:
            run("settingsPatch", v, props[k])
    return errors


def validate(b, external=(), schemas=None):
    """Return (errors, warnings) for a cleaned bundle. `schemas` is the get_schema "schemas" object
    (load_server_schemas); without it only the kit's own reference and consistency checks run."""
    errors, warnings = [], []
    if schemas:
        errors += _schema_errors(b, schemas)
    ext = set(external) | set(b.get("external", ())) | library_ids()
    res_ids, node_ids, prod_ids = {}, {}, {}
    for kind, items, key, store in (("resource", b.get("resources", []), "id", res_ids),
                                    ("node", b.get("nodes", []), "id", node_ids),
                                    ("product", b.get("products", []), "modelKey", prod_ids)):
        for it in items:
            i = it.get(key)
            if not isinstance(i, str) or not ID_RE.match(i):
                errors.append("%s id %r is not [a-zA-Z0-9_-]{1,100}" % (kind, i))
                continue
            if i in store:
                errors.append("duplicate %s id %s" % (kind, i))
            store[i] = it
    known_res = set(res_ids) | ext
    for r in b.get("resources", []):
        d = r.get("data", {})
        if r.get("kind") == "geometry":
            t = d.get("type")
            if t == "roundedBox" and d["radius"] > min(d["dimensions"]) / 2:
                errors.append("%s: roundedBox radius exceeds half the smallest dimension" % r["id"])
            if t == "extrude":
                for poly in [d["points"]] + d.get("holes", []):
                    if len(poly) > 1 and poly[0] == poly[-1]:
                        errors.append("%s: do not repeat the closing point" % r["id"])
        elif r.get("kind") == "material":
            for ref in _refs_in_material(d):
                if ref not in known_res:
                    errors.append("%s: texture %s not found" % (r["id"], ref))
        elif r.get("kind") not in ("texture",):
            errors.append("%s: unknown resource kind %r" % (r["id"], r.get("kind")))
    known_nodes = set(node_ids) | ext
    known_prod = set(prod_ids) | ext
    for n in b.get("nodes", []):
        i = n.get("id")
        p = n.get("parentId")
        if p is not None and p not in known_nodes:
            errors.append("%s: parent %s is not in the bundle or external" % (i, p))
        if p is not None and ("container" in n or "floorId" in n):
            errors.append("%s: container/floorId only on roots" % i)
        if sum(1 for k in ("primitive", "render", "light") if n.get(k)) > 1:
            errors.append("%s: choose one of primitive, render or light" % i)
        rd = n.get("render")
        if rd:
            if rd.get("geometry") not in known_res:
                errors.append("%s: geometry %s not found" % (i, rd.get("geometry")))
            for m in rd.get("materials", []):
                if m not in known_res:
                    errors.append("%s: material %s not found" % (i, m))
            for mtx in rd.get("instances") or []:
                if len(mtx) == 16 and (mtx[3] != 0 or mtx[7] != 0 or mtx[11] != 0 or mtx[15] != 1):
                    errors.append("%s: instance matrix must be affine" % i)
                    break
        lt = n.get("light")
        if lt and lt.get("targetId") and lt["targetId"] not in known_nodes:
            errors.append("%s: light target %s not found" % (i, lt["targetId"]))
        ck = n.get("catalogueKey")
        if ck and ck not in known_prod:
            errors.append("%s: catalogueKey %s not in bundle products or external" % (i, ck))
        sc = n.get("scale")
        if sc and any(v <= 0 for v in sc):
            errors.append("%s: scale must be positive (after rounding)" % i)
        if n.get("role") == "wall" and not n.get("behavior"):
            warnings.append("%s: role wall without behavior is stretched to the ceiling; give headers and sills explicit behavior with height fixed" % i)
    if b.get("rooms") is not None and not isinstance(b.get("rooms"), list):
        errors.append("rooms must be the complete rooms array")
    return errors, warnings


# ------------------------------------------------------------------------ ordering

def _parents_first(nodes):
    by = {n["id"]: n for n in nodes}
    out, done = [], set()

    def visit(n, depth=0):
        if n["id"] in done:
            return
        if depth > 200:
            raise ValueError("parent cycle at %s" % n["id"])
        p = n.get("parentId")
        if p in by:
            visit(by[p], depth + 1)
        done.add(n["id"])
        out.append(n)

    for n in nodes:
        visit(n)
    return out


def _resources_ordered(resources):
    # textures before materials that use them, geometry and materials otherwise as given
    rank = {"image": 0, "texture": 1, "geometry": 2, "material": 2}
    return sorted(resources, key=lambda r: rank.get(r.get("kind"), 3))


def _size(tool, args):
    return len(json.dumps({"tool": tool, "args": args}, separators=(",", ":")).encode())


def _chunks(items, key, tool, limit):
    """Split items into args dicts {key: [...]} under count and byte limits."""
    out, cur = [], []
    for it in items:
        trial = cur + [it]
        if cur and (len(trial) > limit or _size(tool, {key: trial}) > MAX_FILE_BYTES):
            out.append(cur)
            cur = [it]
        else:
            cur = trial
    if cur:
        out.append(cur)
    return [{key: c} for c in out]


def plan_calls(b):
    """Ordered [(tool, args)] for a cleaned bundle."""
    calls = []
    for nid in b.get("removeNodes", []):
        calls.append(("remove_scene_node", {"nodeId": nid}))
    calls += [("put_scene_resources", a) for a in
              _chunks(_resources_ordered(b.get("resources", [])), "resources", "put_scene_resources", MAX_RESOURCES)]
    calls += [("upsert_product", a) for a in _chunks(b.get("products", []), "products", "upsert_product", MAX_PRODUCTS)]
    calls += [("put_scene_nodes", a) for a in
              _chunks(_parents_first(b.get("nodes", [])), "nodes", "put_scene_nodes", MAX_NODES)]
    if b.get("settingsPatch") is not None or b.get("rooms") is not None or b.get("authoring") is not None:
        args = {"settingsPatch": b.get("settingsPatch") or {}}  # rooms/authoring alone are refused
        if b.get("rooms") is not None:
            args["rooms"] = b["rooms"]
        if b.get("authoring") is not None:
            args["authoring"] = b["authoring"]
        calls.append(("update_settings", args))
    for pk in b.get("removeProducts", []):
        calls.append(("remove_product", {"productKey": pk, "removeInstances": True}))
    for rid in b.get("removeResources", []):
        calls.append(("remove_scene_resource", {"resourceId": rid}))
    return calls


def write_payloads(b, out_dir, start_index=1, external=(), layer_gap=LAYER_GAP, check=True, quiet=False):
    """Clean, round, validate and write queue payload files. Returns the file paths in
    write order. Raises ValueError (listing every problem) instead of writing bad files.

    external: ids that already exist on the server (parents, shared resources, products).
    The shared m-* library ids are always treated as external.
    """
    b = _clean(b)
    b["resources"] = _dedupe(b.get("resources", []), "id")
    b["products"] = _dedupe(b.get("products", []), "modelKey")
    for r in b.get("resources", []):
        d = r.get("data", {})
        if d.get("type") == "torus" and "arc" in d:
            d["arc"] = min(d["arc"], TAU)
        if d.get("type") == "roundedBox":
            d["radius"] = min(d["radius"], math.floor(min(d["dimensions"]) / 2 * 1000) / 1000)
    schemas = load_server_schemas(out_dir)
    errors, warnings = validate(b, external, schemas)
    if schemas is None:
        warnings.append("no %s above %s: checked without the server's schema" % (SCHEMA_FILE, out_dir))
    warnings += ["cutaway: " + s for s in check_cutaway(b, ceiling_height=_spec_ceiling(out_dir))]
    if check:
        errors += ["layer: " + s for s in check_layers(b, layer_gap)]
    if errors:
        raise ValueError("payload validation failed (%d):\n  %s" % (len(errors), "\n  ".join(errors[:200])))
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for k, (tool, args) in enumerate(plan_calls(b)):
        text = json.dumps({"tool": tool, "args": args}, separators=(",", ":"))
        size = len(text.encode())
        if size > MAX_REQUEST_BYTES:
            raise ValueError("%s call %d is %d bytes (> 1 MiB); split the item" % (tool, k, size))
        if size > MAX_FILE_BYTES:
            warnings.append("%s call %d is %.0f KB (> 60 KB): a single oversized item" % (tool, k, size / 1024))
        path = os.path.join(out_dir, "%02d-%s.json" % (start_index + k, tool))
        with open(path, "w") as f:
            f.write(text)
        paths.append(os.path.abspath(path))
    if not quiet:
        for w in warnings:
            print("warning:", w, file=sys.stderr)
        total = sum(os.path.getsize(p) for p in paths)
        print("wrote %d files, %.1f KB total, to %s" % (len(paths), total / 1024, out_dir), file=sys.stderr)
        if total > 150 * 1024:
            print("warning: %.0f KB total; writers retype every byte - use instances/recipes" % (total / 1024), file=sys.stderr)
    return paths


# ---------------------------------------------------------------------------- lint

def lint_dir(d, external=()):
    """Load NN-*.json payload files back into a bundle and validate (parents and
    resources not in the folder are reported as warnings, not errors)."""
    import glob
    b = {"resources": [], "products": [], "nodes": []}
    files = sorted(glob.glob(os.path.join(d, "[0-9]*-*.json")))
    for f in files:
        raw = open(f).read()
        if "\\" in raw:
            print("%s: contains a backslash" % f)
        if len(raw.encode()) > MAX_FILE_BYTES:
            print("%s: %.0f KB > 60 KB" % (f, len(raw.encode()) / 1024))
        p = json.loads(raw)
        a = p["args"]
        if p["tool"] == "put_scene_resources":
            b["resources"] += a["resources"]
        elif p["tool"] == "upsert_product":
            b["products"] += a.get("products") or [a["product"]]
        elif p["tool"] == "put_scene_nodes":
            b["nodes"] += a["nodes"]
        elif p["tool"] == "update_settings" and "settingsPatch" not in a:
            print("%s: update_settings without settingsPatch is refused" % f)
    errors, warnings = validate(_clean(b), external, load_server_schemas(d))
    for e in errors:
        print(("warning: " if "not found" in e or "not in the bundle" in e else "error: ") + e)
    for w in warnings:
        print("warning:", w)
    for s in check_layers(b):
        print("layer:", s)
    for s in check_cutaway(b, ceiling_height=_spec_ceiling(d)):
        print("cutaway:", s)
    print("%d files, %d resources, %d products, %d nodes" % (len(files), len(b["resources"]), len(b["products"]), len(b["nodes"])))


# ------------------------------------------------------------------------ selftest

def _selftest():
    import tempfile
    assert r3(1.23456) == 1.235 and r3(-0.0001) == 0 and r3(2.0) == 2
    assert clean_str('76x80" \\ bed\u00d7  2') == "76x80 bed x 2", clean_str('76x80" \\ bed\u00d7  2')
    assert torus("t", 1, 0.1, arc=6.2832)["data"].get("arc") is None
    assert torus("t", 1, 0.1, arc=3.14159)["data"]["arc"] == 3.142
    assert rounded_box("rb", (0.1, 0.02, 0.1), 0.05)["data"]["radius"] == 0.01
    m = inst_trs((1, 2, 3), math.pi / 4)
    assert m[0] == 0.707 and m[2] == -0.707 and m[8] == 0.707 and m[12:15] == [1, 2, 3]
    assert inst_euler((1, 2, 3), (0, math.pi / 4, 0)) == m
    n = node("x-a", None, "A", position=(1, 0, 0), container="furnishings", product_key="x-p", geom="g", mat="m-oak")
    assert n == {"id": "x-a", "parentId": None, "name": "A", "position": [1, 0, 0],
                 "render": {"geometry": "g", "materials": ["m-oak"]}, "container": "furnishings", "productKey": "x-p"}, n
    assert "metalness" not in material("m", "#ffffff")["data"]

    # a small assembly: frame + canvas + art layer offset 0.5 mm (collapses on rounding)
    def art(offset):
        return bundle(
            [box("x-g-canvas", (0.6, 0.8, 0.02)), plane("x-g-art", 0.5, 0.7), material("x-m-art", "#aa8866", name='Art 24" print')],
            [{"modelKey": "x-art", "room": "Living", "item": "Art", "name": 'Print 24"x36"', "retailer": "Shop",
              "price": 99, "qty": 1, "url": "https://example.com/p", "dimensions": [0.6, 0.02, 0.8]}],
            [node("x-art", None, "art", position=(2, 1.5, 0.05), container="furnishings", catalogue_key="x-art"),
             node("x-art-canvas", "x-art", position=(0, 0, 0.01), geom="x-g-canvas", mat="x-m-art", behavior=DECOR),
             node("x-art-print", "x-art", position=(0, 0, 0.02 + offset), geom="x-g-art", mat="x-m-art", behavior=DECOR)])
    tmp = tempfile.mkdtemp(prefix="payload-selftest-")
    bad = art(0.0004)
    assert check_layers(bad), "0.4 mm layer offset must be flagged"
    try:
        write_payloads(bad, tmp, quiet=True)
        raise AssertionError("expected a layer failure")
    except ValueError as e:
        assert "layer:" in str(e)
    good = art(0.003)
    assert not check_layers(good), check_layers(good)
    # parents and refs
    orphan = bundle(nodes=[node("x-b", "x-missing", geom="x-g-none", mat="m-oak")])
    errs, _ = validate(_clean(orphan))
    assert any("parent x-missing" in e for e in errs) and any("geometry x-g-none" in e for e in errs)
    assert not validate(_clean(orphan), external=("x-missing", "x-g-none"))[0]
    # splitting and ordering: 45 resources, 230 nodes with children listed before parents
    res = [box("x-g%d" % i, (0.1, 0.1, 0.1)) for i in range(45)]
    nodes = [node("x-c%d" % i, "x-r%d" % (i % 3), position=(i * 0.2, 0, 0), geom="x-g%d" % (i % 45), mat="m-oak") for i in range(227)]
    nodes += [node("x-r%d" % i, None, container="furnishings") for i in range(3)]
    paths = write_payloads(bundle(res, [], nodes, settingsPatch={"subtitle": 'A "quoted" title'}), tmp, quiet=True)
    tools = [json.load(open(p))["tool"] for p in paths]
    assert tools == ["put_scene_resources"] * 3 + ["put_scene_nodes"] * 3 + ["update_settings"], tools
    first = json.load(open(paths[3]))["args"]["nodes"]
    assert [n["id"] for n in first[:1]] == ["x-r0"], first[0]
    assert all(os.path.getsize(p) <= MAX_FILE_BYTES for p in paths)
    st = json.load(open(paths[-1]))["args"]
    assert st == {"settingsPatch": {"subtitle": "A quoted title"}}, st
    rooms_only = plan_calls(_clean(bundle(rooms=[{"id": "a"}])))
    assert rooms_only == [("update_settings", {"settingsPatch": {}, "rooms": [{"id": "a"}]})]
    # floor-contact bottoms are not flagged; adjacent planks are not flagged
    floor = bundle([box("x-g-plank", (0.18, 0.003, 1.2))], [], [
        node("x-fl", None, container="shell"),
        node("x-planks", "x-fl", position=(0, 0.0015, 0),
             render=render("x-g-plank", "m-oak", inst_t((0, 0, 0), (0.18, 0, 0), (0.36, 0, 0.6))))])
    assert not check_layers(floor), check_layers(floor)
    # boards turned 30 degrees, side by side: their bounding boxes overlap but the boards do not
    yaw = math.radians(30)
    side = (0.2 * math.cos(yaw), 0, -0.2 * math.sin(yaw))
    angled = bundle([box("x-g-board", (0.18, 0.003, 1.2))], [], [
        node("x-af", None, container="shell", rotation=(0, yaw, 0)),
        node("x-ab", "x-af", position=(0, 0.0015, 0),
             render=render("x-g-board", "m-oak", inst_t((0, 0, 0), (0.2, 0, 0)))),
        node("x-ac", None, container="shell"),
        node("x-ac1", "x-ac", position=(0, 0.0015, 0), rotation=(0, yaw, 0), geom="x-g-board", mat="m-oak"),
        node("x-ac2", "x-ac", position=(side[0], 0.0015, side[2]), rotation=(0, yaw, 0), geom="x-g-board", mat="m-oak")])
    assert not check_layers(angled), check_layers(angled)
    # ... but a turned layer 0.5 mm over another is still caught
    stacked = bundle([box("x-g-board", (0.18, 0.003, 1.2))], [], [
        node("x-st", None, container="shell", rotation=(0, yaw, 0)),
        node("x-st1", "x-st", position=(0, 0.1, 0), geom="x-g-board", mat="m-oak"),
        node("x-st2", "x-st", position=(0.05, 0.1005, 0.1), geom="x-g-board", mat="m-oak")])
    assert check_layers(stacked), "turned 0.5 mm layer must be flagged"
    # cutaway: a ceiling kept in cutaway is flagged, a hidden one and a tall wardrobe are not
    ceil = bundle([box("x-g-ceil", (3, 0.01, 3)), box("x-g-ward", (1, 2.2, 0.6))], [], [
        node("x-ce", None, container="shell", room_id="bed", position=(0, 2.44, 0), geom="x-g-ceil", mat="m-paint"),
        node("x-ch", None, container="shell", room_id="bed", position=(0, 2.44, 0), geom="x-g-ceil", mat="m-paint",
             behavior=HIDE),
        node("x-wd", None, container="furnishings", room_id="bed", position=(0, 1.1, 0), geom="x-g-ward",
             mat="m-oak", behavior=SOLID)])
    cw = check_cutaway(ceil)
    assert len(cw) == 1 and cw[0].startswith("x-ce:"), cw
    # a pendant hidden on its root hides its parts too
    lamp = bundle([box("x-g-shade", (0.3, 0.3, 0.3))], [], [
        node("x-lp", None, container="furnishings", room_id="bed", position=(0, 2.2, 0), behavior=HIDE),
        node("x-ls", "x-lp", geom="x-g-shade", mat="m-brass")])
    assert not check_cutaway(lamp), check_cutaway(lamp)
    # Resolve ceilingDrop exactly as the viewer does, including settings and floor overrides.
    lamp["nodes"][0].pop("behavior")
    lamp["nodes"][0]["position"] = [0, 0, 0]
    lamp["nodes"][0]["ceilingDrop"] = 0.3
    assert check_cutaway(lamp), "a kept ceiling-mounted shade must be flagged"
    assert lamp["nodes"][0]["position"] == [0, 0, 0], "checking must not mutate the bundle"
    assert not check_cutaway(lamp, ceiling_height=1), "low-mounted shade is below the cutaway"
    lamp["settingsPatch"] = {"ceilingHeight": 3}
    assert check_cutaway(lamp, ceiling_height=1), "settings override the fallback ceiling height"
    lamp["nodes"][0]["floorId"] = "upper"
    lamp["settingsPatch"]["floors"] = [{"id": "upper", "ceilingHeight": 1}]
    assert not check_cutaway(lamp), "use the assembly's floor ceiling height"
    lamp["settingsPatch"]["floors"][0]["ceilingHeight"] = 3
    lamp["nodes"][0]["behavior"] = HIDE
    assert not check_cutaway(lamp), "hidden ceiling-mounted assemblies stay hidden"
    # identical duplicates from placing one module twice are merged; conflicts fail
    g = box("x-g-dup", (1, 1, 1))
    assert len(merge(bundle([g]), bundle([dict(g)]))["resources"]) == 1
    try:
        write_payloads(bundle([g, box("x-g-dup", (2, 1, 1))]), tmp, quiet=True)
        raise AssertionError("expected duplicate id failure")
    except ValueError as e:
        assert "duplicate resource id x-g-dup" in str(e)
    print("payload.py selftest OK (%s)" % tmp)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif len(sys.argv) >= 3 and sys.argv[1] == "--lint":
        lint_dir(sys.argv[2], external=sys.argv[3:])
    else:
        print("usage: payload.py --selftest | --lint <payload dir> [external ids...]")
