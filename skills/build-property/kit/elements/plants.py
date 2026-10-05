"""Generic, low-payload plant shapes: shrubs, ornamental grass clumps and trees.

Each builder follows the products/ module contract

    build(prefix, inst_id, position, rotation_y=0.0, room_id=None, materials=None, qty=1, **options) -> bundle

so landscape.plants() can place them, and so can any researched plant module you write yourself.
They are shapes, not products: no catalogue entry is emitted unless you pass `product` (a researched
product dict, see elements._common.catalogue_entry) and optionally `product_key` (default the shape
name), in which case the root is tagged with it. Pass the total count as `qty` on every call (or the
total on the first and qty=0 on the rest).

shrub(..., radius=0.35, height=0.6, colour='#3f6b35', lobes=5, seed=None)
    a rounded mound of overlapping icosahedron lobes (one instanced mesh).
grass(..., radius=0.3, height=0.8, colour='#8a9a5b', blades=24, seed=None)
    a clump of thin tapered blades leaning outwards (one instanced mesh).
tree(..., height=5.0, crown=2.4, trunk=0.12, colour='#4f7a3a', bark='#5a4632', lobes=6, seed=None)
    a trunk cylinder and a crown of instanced icosahedron lobes; the trunk collides.

Geometry ids are shared per prefix (unit shapes scaled by instance matrices), so a hundred shrubs
ship two resources. Materials are keyed by colour: '<prefix>mat-plant-<hex>'. `materials` may map
'foliage' / 'bark' to an existing material id instead. Origin = ground point at the plant's centre.
"""
import math
import random

from elements._common import BEH, Bag, catalogue_entry, mtx, product_keys

DECOR = BEH('keep')
TRUNK = {'height': 'fixed', 'cutaway': 'keep', 'cutawayHeight': 0.78, 'desktopCollision': True, 'vrCollision': True}


def _foliage(b, materials, colour, roughness=0.9):
    m = (materials or {}).get('foliage')
    if m:
        return m
    return b.mat('plant-' + colour.lstrip('#').lower(), name='Foliage ' + colour, color=colour, roughness=roughness)


def _root(b, inst_id, position, rotation_y, room_id, name, product, qty, key, fit=None):
    b.node(inst_id, None, position, name, rot=[0, rotation_y, 0], container='furnishings',
           **({'roomId': room_id} if room_id else {}), **product_keys(b.P, key, product is not None))
    if product is not None and qty:
        b.product(catalogue_entry(b.P + key, 'Exterior', product, qty, fit))


def shrub(prefix, inst_id, position, rotation_y=0.0, room_id=None, materials=None, qty=1, radius=0.35,
          height=0.6, colour='#3f6b35', lobes=5, seed=None, product=None, product_key=None, fit=None):
    rnd = random.Random(seed if seed is not None else inst_id)
    b = Bag(prefix)
    g = b.ico('g-plant-lobe', 1.0, 1)
    m = _foliage(b, materials, colour)
    _root(b, inst_id, position, rotation_y, room_id, 'Shrub', product, qty, product_key or 'shrub', fit)
    inst = [mtx([0, height * 0.5, 0], s=(radius * 0.85, height * 0.5, radius * 0.85))]
    for k in range(lobes):
        a = 2 * math.pi * (k + rnd.random() * 0.5) / lobes
        r = radius * rnd.uniform(0.35, 0.55)
        s = radius * rnd.uniform(0.45, 0.6)
        inst.append(mtx([math.cos(a) * r, height * rnd.uniform(0.35, 0.6), math.sin(a) * r],
                        s=(s, height * rnd.uniform(0.35, 0.45), s)))
    b.mesh(inst_id + '-leaves', inst_id, [0, 0, 0], 'Shrub foliage', g, m, beh=DECOR, instances=inst)
    return b.bundle()


def grass(prefix, inst_id, position, rotation_y=0.0, room_id=None, materials=None, qty=1, radius=0.3,
          height=0.8, colour='#8a9a5b', blades=24, seed=None, product=None, product_key=None, fit=None):
    rnd = random.Random(seed if seed is not None else inst_id)
    b = Bag(prefix)
    g = b.cyl('g-plant-blade', 0.0, 0.006, 1.0, seg=4)
    m = _foliage(b, materials, colour, 0.85)
    _root(b, inst_id, position, rotation_y, room_id, 'Grass clump', product, qty, product_key or 'grass', fit)
    inst = []
    for k in range(blades):
        a = 2 * math.pi * k / blades + rnd.uniform(-0.2, 0.2)
        r = radius * 0.35 * rnd.random()
        h = height * rnd.uniform(0.7, 1.0)
        lean = rnd.uniform(0.12, 0.35)
        x, z = math.cos(a) * r, math.sin(a) * r
        # lean outwards: rotate about the horizontal axis perpendicular to the blade's direction
        rot = (math.sin(a) * lean, 0, -math.cos(a) * lean)
        inst.append(mtx([x, h / 2, z], rot, (1.5, h, 1.5)))
    b.mesh(inst_id + '-blades', inst_id, [0, 0, 0], 'Grass blades', g, m, beh=DECOR, instances=inst)
    return b.bundle()


def tree(prefix, inst_id, position, rotation_y=0.0, room_id=None, materials=None, qty=1, height=5.0, crown=2.4,
         trunk=0.12, colour='#4f7a3a', bark='#5a4632', lobes=6, seed=None, product=None, product_key=None, fit=None):
    rnd = random.Random(seed if seed is not None else inst_id)
    b = Bag(prefix)
    g_lobe = b.ico('g-plant-lobe', 1.0, 1)
    g_trunk = b.cyl('g-plant-trunk', 0.7, 1.0, 1.0, seg=10)
    m_leaf = _foliage(b, materials, colour)
    m_bark = (materials or {}).get('bark') or b.mat('plant-bark-' + bark.lstrip('#').lower(), name='Bark ' + bark,
                                                    color=bark, roughness=0.95)
    _root(b, inst_id, position, rotation_y, room_id, 'Tree', product, qty, product_key or 'tree', fit)
    t_h = height - crown * 0.6
    b.mesh(inst_id + '-trunk', inst_id, [0, t_h / 2, 0], 'Trunk', g_trunk, m_bark, beh=TRUNK,
           scale=[trunk, t_h, trunk])
    cy, cr = height - crown / 2, crown / 2
    inst = [mtx([0, cy, 0], s=(cr * 0.8, cr * 0.75, cr * 0.8))]
    for k in range(lobes):
        a = 2 * math.pi * (k + rnd.random() * 0.5) / lobes
        r = cr * rnd.uniform(0.35, 0.55)
        s = cr * rnd.uniform(0.45, 0.6)
        inst.append(mtx([math.cos(a) * r, cy + cr * rnd.uniform(-0.25, 0.25), math.sin(a) * r], s=(s, s * 0.85, s)))
    b.mesh(inst_id + '-crown', inst_id, [0, 0, 0], 'Crown', g_lobe, m_leaf, beh=DECOR, instances=inst)
    return b.bundle()


BUILDERS = {'shrub': shrub, 'grass': grass, 'tree': tree}
