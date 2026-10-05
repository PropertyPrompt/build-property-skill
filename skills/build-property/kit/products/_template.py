"""Template product module: a made-up side table, to copy when you add a real product.

Copy this file to products/<room>/<product_name>.py (or products/<product_name>.py), replace the
PRODUCT values with the researched ones, and rewrite build() to model the real silhouette. Keep the
signature, the id scheme and the catalogue behaviour; products/test_template.py shows how to test it.

Everything below about the product itself is a PLACEHOLDER: there is no such product.

Modelling conventions (see products/README.md):
- metres; origin = the item's floor centre (y = 0 on the floor); +z is the item's front;
  rotation_y turns the whole item about its origin.
- geometry ids are shared by every instance under one prefix ('<prefix>g-<product>-<part>'), so
  placing it twice ships the geometry once.
- repeated parts (legs) are one mesh with render.instances, not one node each.
- thin parts that touch keep same-facing faces >= 2-3 mm apart (the kit's layer check fails < 2 mm).
- collision is honest: a table you would walk around collides in desktop and VR; a rug would not.
"""
import hashlib
import json
import os
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if KIT not in sys.path:
    sys.path.insert(0, KIT)

from lib.payload import SOLID, bundle, cylinder, inst_t, material, node, rounded_box  # noqa: E402

PRODUCT = {
    'key': 'example-side-table',          # model key suffix: catalogue key = prefix + key
    'item': 'Side table',                 # what it is, in the catalogue's words
    'name': 'Example side table (placeholder, not a real product)',
    'retailer': '<retailer>',
    'url': '<product page url>',          # the verified product page itself, never a search or category page
    'price': None,                        # number in `currency`, as listed on the page
    'currency': '<ISO currency>',
    'dimensions_m': [0.50, 0.50, 0.55],   # [w, d, h] from the page, converted to metres
    'size': '50 x 50 x 55 cm',            # as the retailer writes it (ASCII only: 20 in, not 20")
    'finish': '<finish / colour name>',
    'construction': 'square top on four round legs with a lower shelf',
    'researched': None,                   # 'YYYY-MM-DD' once the page, price and dimensions are verified
    'status': 'Unverified',               # 'Verified YYYY-MM-DD' once checked
    'notes': 'Template only. Replace every value with researched data.',
}

# Default materials per role; build(materials={role: 'm-oak'}) uses an existing id instead, and
# build(materials={role: {...}}) merges fields into the default.
MATERIALS = {
    'top': {'color': '#c8a77e', 'roughness': 0.55, 'name': 'Example table top, light wood'},
    'legs': {'color': '#2b2b2b', 'roughness': 0.5, 'metalness': 0.6, 'name': 'Example table legs, black metal'},
}


def _materials(prefix, materials):
    """Resolve role -> material id, returning (ids, resources to publish)."""
    ids, res = {}, []
    for role, default in MATERIALS.items():
        given = (materials or {}).get(role)
        if isinstance(given, str):
            ids[role] = given                         # existing material (library m-* or published)
            continue
        data = dict(default, **(given or {}))
        mid = '%smat-%s-%s' % (prefix, PRODUCT['key'], role)
        if given:                                     # an override is a different material
            mid += '-' + hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()[:6]
        color = data.pop('color')
        rough = data.pop('roughness', 0.75)
        res.append(material(mid, color, rough, **data))
        ids[role] = mid
    return ids, res


def build(prefix, inst_id, position, rotation_y=0.0, room_id=None, materials=None, qty=1,
          top_thickness=0.025, shelf=True):
    """Bundle for one side table at `position` (floor centre, world metres) turned `rotation_y`.

    qty: the TOTAL number of these tables in the property, passed on every call (identical catalogue
    entries merge into one), or the total on the first call and 0 on the rest (qty=0 emits no
    catalogue entry). Extra keyword options (top_thickness, shelf) are product variants.
    """
    w, d, h = PRODUCT['dimensions_m']
    key = prefix + PRODUCT['key']
    g = prefix + 'g-' + PRODUCT['key']
    m, mat_res = _materials(prefix, materials)
    leg_r, inset = 0.018, 0.04
    leg_h = h - top_thickness

    res = [rounded_box(g + '-top', (w, top_thickness, d), 0.008),
           cylinder(g + '-leg', leg_r, leg_r * 0.85, leg_h, seg=16)]
    lx, lz = w / 2 - inset, d / 2 - inset
    legs = inst_t((-lx, leg_h / 2, -lz), (lx, leg_h / 2, -lz), (-lx, leg_h / 2, lz), (lx, leg_h / 2, lz))

    root = node(prefix + inst_id, None, PRODUCT['item'], position=position, rotation=(0, rotation_y, 0),
                container='furnishings', product_key=key, catalogue_key=key, room_id=room_id)
    parts = [
        node(root['id'] + '-top', root['id'], 'Top', position=(0, h - top_thickness / 2, 0),
             geom=g + '-top', mat=m['top'], behavior=SOLID),
        node(root['id'] + '-legs', root['id'], 'Legs', geom=g + '-leg', mat=m['legs'], inst=legs, behavior=SOLID),
    ]
    if shelf:
        # the shelf sits between the legs (touching, not passing through them), its top 0.15 m up
        res.append(rounded_box(g + '-shelf', (w - 2 * inset - 2 * leg_r, 0.018, d - 2 * inset - 2 * leg_r), 0.004))
        parts.append(node(root['id'] + '-shelf', root['id'], 'Lower shelf', position=(0, 0.15 - 0.009, 0),
                          geom=g + '-shelf', mat=m['top'], behavior=SOLID))

    products = []
    if qty:
        products.append({
            'modelKey': key, 'room': room_id or 'Living', 'item': PRODUCT['item'], 'name': PRODUCT['name'],
            'retailer': PRODUCT['retailer'], 'price': PRODUCT['price'], 'qty': qty, 'size': PRODUCT['size'],
            'fit': PRODUCT['finish'], 'status': PRODUCT['status'], 'url': PRODUCT['url'],
            'dimensions': list(PRODUCT['dimensions_m'])})
    return bundle(res + mat_res, products, [root] + parts)
