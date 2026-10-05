#!/usr/bin/env python3
"""Kit self-test: everything that can be checked without the PropertyPrompt MCP.

    python3 $KIT/tests/run_selftest.py [--keep]

Steps (PASS/FAIL each; exit status 1 if any fails):
  plan       draw the synthetic example plan (tests/make_synthetic_plan.py) into a temp dir
  measure    measure/measure_plan.py on it with the known outside dimensions
  compare    measure/compare_spec.py: detected walls/openings vs measure/spec.example.json
  spec       new_property.check_spec on the example spec (schema + geometry checks)
  shell      shell/shell.py build_shell on the example spec
  elements   every elements/ module on the example spec, with placeholder products
  payload    merge shell + elements; lib.payload validate, check_layers and write_payloads
  angled     a living room with a cut corner (angledWalls, room polygon): spec check, plank, tile and
             ceiling layers and areas; slab interior doors with steel hardware
  polygons   L-shaped room wall checks and invalid angled openings
  floor-edges concave, shallow diagonal and narrow diagonal floors: coverage, piece sizes, payloads
  products   every products/**/test_*.py (the template's test included)
  payload.py lib/payload.py --selftest
  workflows  workflow/*.js parse (module body wrapped in an async Function, `export` stripped)

Requires Python 3.9+, Pillow and Node. Writes only to a temporary directory (--keep keeps it).
"""
import glob
import copy
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import traceback

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
EXAMPLE = os.path.join(KIT, 'measure', 'spec.example.json')
PY = sys.executable

results = []


def step(name):
    def wrap(fn):
        def run(*a, **kw):
            try:
                detail = fn(*a, **kw)
                results.append((name, True, detail or ''))
                print('PASS  %-10s %s' % (name, detail or ''))
                return True
            except Exception as e:  # noqa: BLE001 - report every failure and carry on
                msg = str(e).strip().splitlines()
                results.append((name, False, msg[0] if msg else type(e).__name__))
                print('FAIL  %-10s %s' % (name, type(e).__name__))
                print('      ' + '\n      '.join((traceback.format_exc() if not msg else str(e)).strip().splitlines()[:40]))
                return False
        return run
    return wrap


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError('%s exited %d\n%s%s' % (' '.join(os.path.basename(c) for c in cmd[:2]), r.returncode,
                                                   r.stdout[-3000:], r.stderr[-3000:]))
    return r


def placeholder(item, **extra):
    """An obviously fake product record, standing in for a researched one."""
    p = {'item': item, 'name': 'Placeholder ' + item.lower(), 'retailer': '<retailer>', 'url': '<product page url>',
         'price': None, 'size': '<size>', 'dimensions_m': [1.0, 0.1, 0.01], 'status': 'Unverified'}
    p.update(extra)
    return p


def main():
    keep = '--keep' in sys.argv
    tmp = tempfile.mkdtemp(prefix='property-build-selftest-')
    spec = json.load(open(EXAMPLE))
    plan = os.path.join(tmp, 'synthetic-plan.png')
    draft = os.path.join(tmp, 'draft-spec.json')
    built = {}

    @step('plan')
    def s_plan():
        sh([PY, os.path.join(KIT, 'tests', 'make_synthetic_plan.py'), '--out', plan])
        return os.path.basename(plan)

    @step('measure')
    def s_measure():
        W, D = spec['footprint']
        r = sh([PY, os.path.join(KIT, 'measure', 'measure_plan.py'), plan, '--width-m', str(W), '--depth-m', str(D),
                '--out', draft, '--debug-png', os.path.join(tmp, 'overlay.png')])
        return r.stdout.strip().splitlines()[-1].split('->')[0].strip()

    @step('compare')
    def s_compare():
        r = sh([PY, os.path.join(KIT, 'measure', 'compare_spec.py'), draft, EXAMPLE])
        d = json.load(open(draft))
        n = sum(len(w['openings']) for w in d['outerWalls'] + d['interiorWalls'])
        return '%d walls, %d openings within tolerance (%s)' % (
            len(d['outerWalls']) + len(d['interiorWalls']), n, r.stdout.strip().splitlines()[-1])

    @step('spec')
    def s_spec():
        import new_property
        schema = json.load(open(os.path.join(KIT, 'measure', 'spec.schema.json')))
        errors, warns = new_property.check_spec(spec, schema)
        assert not errors, errors
        return 'example spec valid, %d warning(s)' % len(warns)

    @step('shell')
    def s_shell():
        from shell.shell import build_shell
        b = build_shell(spec)
        built['shell'] = b
        return '%d resources, %d nodes' % (len(b['resources']), len(b['nodes']))

    @step('elements')
    def s_elements():
        from elements import floor_finish, landscape, plants, porch, roof, siding, trim
        parts = {}
        # roof and siding
        g = roof.geometry(spec, pitch=0.5)
        parts['roof'] = roof.build(spec, geom=g, product=placeholder('Roofing shingles', unit_m2=3.0),
                                   siding_product_key='siding-panel')
        cas, excl = siding.casings(spec)
        L = porch.layout(spec, 'door-front')
        assert L.clearance > spec['heights']['door'], 'porch beam clears the door head (%.3f)' % L.clearance
        k, ex = L.batten_exclusion()
        excl.setdefault(k, []).append(ex)
        stats = {}
        parts['casings'] = cas
        parts['battens'] = siding.battens(spec, g, excl, stats=stats, product_key='batten')
        stoop, (sk, sgap) = porch.stoop(spec, 'door-back')
        pk, pgap = L.plinth_gap()
        gaps = {}
        gaps.setdefault(pk, []).append(pgap)
        gaps.setdefault(sk, []).append(sgap)
        parts['plinth'] = siding.plinth(spec, gaps)
        parts['siding-cat'] = siding.catalogue(
            'ex-', placeholder('Siding panels', unit_m2=2.9), placeholder('Siding battens', unit_length_m=3.6),
            spec=spec, roof_geom=g, batten_length=stats['length'])
        parts['porch'] = porch.build(spec, L)
        parts['stoop'] = stoop
        walk = porch.walk_settings(spec, L, path_end=5.0)
        assert [f['id'] for f in walk['floors']] == ['ground', 'garden'] and walk['stairs']
        # landscape
        parts['lawn'] = landscape.lawn(spec, extent=(-8.0, -8.0, 18.0, 13.0))
        along, out = L.front_edge()
        parts['path'] = landscape.path(spec, 'S', along, out, 5.0)
        parts['beds'] = landscape.beds(spec, 'S', [('s1', 0.3, L.PX0 - 0.1), ('s2', L.PX1 + 0.1, 9.7)])
        z_bed = spec['footprint'][1] + 0.55
        parts['plants'] = landscape.plants('ex-', [
            {'kind': 'shrub', 'x': 0.6, 'z': z_bed}, {'kind': 'shrub', 'x': 4.6, 'z': z_bed},
            {'kind': 'grass', 'x': 5.6, 'z': z_bed}, {'kind': 'grass', 'x': 6.6, 'z': z_bed},
            {'kind': 'shrub', 'x': 8.8, 'z': z_bed, 'product': placeholder('Shrub', dimensions_m=[0.7, 0.7, 0.6])},
            {'kind': 'planter', 'x': 9.3, 'z': z_bed}],
            builders={'planter': lambda p, i, pos, ry, qty=1, **o: plants.shrub(p, i, pos, ry, qty=qty, radius=0.2,
                                                                                    height=0.4, colour='#5d7d3b')},
            spec=spec)
        parts['trees'] = landscape.trees('ex-', [{'x': -3.0, 'z': 10.0}, {'x': 13.0, 'z': -3.0, 'options': {'height': 7}}],
                                         spec=spec)
        parts['site'] = landscape.site(spec, sidewalk=(13.0, 14.5), driveway=(6.5, 9.5, 8.5))
        # floors and trim
        rng = random.Random(1)
        plank_rooms = ['bedroom-1', 'bedroom-2', 'living', 'closet']
        tile_rooms = ['bathroom']
        parts['planks'] = floor_finish.planks(spec, plank_rooms, rng, product_key='floor-plank')
        parts['tiles'] = floor_finish.tiles(spec, tile_rooms, rng, product_key='floor-tile')
        parts['floor-cat'] = floor_finish.catalogue(spec, plank_rooms, tile_rooms,
                                                    plank_product=placeholder('Plank flooring', unit_m2=2.0),
                                                    tile_product=placeholder('Floor tile', unit_m2=1.2))
        ops = trim.openings(spec, plank_rooms, tile_rooms, 'fl-oak-base', tile_mat='fl-tile-beige')
        tstats = {}
        acc_t = 0.004
        parts['trim'] = trim.casings_and_baseboards(spec, ops, plank_rooms + tile_rooms, stats=tstats,
                                                    accent_offset={('bedroom-1', 'N'): acc_t},
                                                    skip={('bathroom', 'N'): [(4.07, 5.93)]},
                                                    baseboard_key='baseboard', casing_key='door-casing')
        parts['ceilings'] = trim.ceilings(spec, plank_rooms + tile_rooms, paint_key='paint-walls')
        parts['accent'] = trim.accent_wall(spec, 'bedroom-1', 'N', layer_t=acc_t, paint_key='paint-accent')
        parts['trim-cat'] = trim.catalogue('fl-', {
            'paint-walls': placeholder('Wall and ceiling paint', qty=6),
            'paint-accent': placeholder('Accent paint'),
            'baseboard': placeholder('Baseboard', unit_length_m=2.4),
            'door-casing': placeholder('Door casing', unit_length_m=2.1)}, stats=tstats)
        built['elements'] = parts
        n = sum(len(b['nodes']) for b in parts.values())
        return '%d element bundles, %d nodes (porch clearance %.2f m)' % (len(parts), n, L.clearance)

    @step('payload')
    def s_payload():
        from lib.payload import _clean, check_layers, merge, validate, write_payloads
        if 'shell' not in built or 'elements' not in built:
            raise RuntimeError('shell or elements step failed')
        b = merge(built['shell'], *built['elements'].values())
        errors, warnings = validate(_clean(b))
        assert not errors, '\n'.join(errors[:30])
        layers = check_layers(b)
        assert not layers, '\n'.join(layers[:30])
        out = os.path.join(tmp, 'payloads')
        paths = write_payloads(b, out, quiet=True)
        kb = sum(os.path.getsize(p) for p in paths) / 1024
        return '%d resources, %d products, %d nodes -> %d files, %.0f KB' % (
            len(b['resources']), len(b['products']), len(b['nodes']), len(paths), kb)

    @step('angled')
    def s_angled():
        import new_property
        from elements import floor_finish, trim
        from lib.payload import _clean, check_layers, merge, validate
        from shell.shell import build_shell
        x1, z1, x2, z2 = next(r['bounds'] for r in spec['rooms'] if r['id'] == 'living')
        poly = [[x1, z1], [x2, z1], [x2, z2 - 1.2], [x2 - 1.2, z2], [x1, z2]]
        sp = copy.deepcopy(spec)
        sp['angledWalls'] = [{'id': 'wall-living-corner', 'kind': 'interior', 'start': [x2 - 1.25, z2 + 0.05],
                              'end': [x2 + 0.05, z2 - 1.25], 'thickness': 0.1, 'openings': []}]
        next(r for r in sp['rooms'] if r['id'] == 'living')['polygon'] = poly
        sp['interiorDoors'] = {'leafStyle': 'slab', 'hardwareMaterial': 'm-steel'}
        schema = json.load(open(os.path.join(KIT, 'measure', 'spec.schema.json')))
        errors, _ = new_property.check_spec(sp, schema)
        assert not errors, errors
        polys = {'living': poly}
        st_p, st_t = {}, {}
        floors = [floor_finish.planks(sp, ['living'], random.Random(1), polygons=polys, stats=st_p),
                  floor_finish.tiles(sp, ['living'], random.Random(1), prefix='ft-', polygons=polys, stats=st_t),
                  trim.ceilings(sp, ['living'], polys=polys)]
        want = (x2 - x1) * (z2 - z1) - 1.2 * 1.2 / 2
        for name, st in (('planks', st_p), ('tiles', st_t)):
            got = st['living']['area']
            assert 0.9 * want < got <= want, '%s cover %.2f of %.2f m2' % (name, got, want)
        shell = build_shell(sp)
        leaves = [n for n in shell['nodes'] if n['id'].startswith('si-d-')]
        assert leaves and not any(n['id'].endswith('-panels') for n in leaves), 'slab doors have no panels'
        assert any('m-steel' in (n.get('render') or {}).get('materials', ()) for n in leaves), 'steel hardware'
        b = merge(shell, *floors)
        errors, _ = validate(_clean(b))
        assert not errors, '\n'.join(errors[:30])
        layers = check_layers(b)
        assert not layers, '\n'.join(layers[:30])
        return 'cut corner: planks %.2f, tiles %.2f of %.2f m2; %d slab door nodes' % (
            st_p['living']['area'], st_t['living']['area'], want, len(leaves))

    @step('polygons')
    def s_polygons():
        import new_property
        schema = json.load(open(os.path.join(KIT, 'measure', 'spec.schema.json')))
        sp = copy.deepcopy(spec)
        room = next(r for r in sp['rooms'] if r['id'] == 'living')
        x0, z0, x1, z1 = room['bounds']
        x, z = x0 + 2, z0 + 1
        room['polygon'] = [[x0, z0], [x - .05, z0], [x - .05, z + .05],
                           [x1, z + .05], [x1, z1], [x0, z1]]
        sp['interiorWalls'] += [
            {'id': 'l-horizontal', 'axis': 'x', 'center_z': z, 'from': x, 'to': x1,
             'thickness': .1, 'openings': []},
            {'id': 'l-vertical', 'axis': 'z', 'center_x': x, 'from': z0, 'to': z,
             'thickness': .1, 'openings': []}]
        errors, _ = new_property.check_spec(sp, schema)
        assert not errors, errors
        # A polygon that actually crosses the walls must still fail.
        room['polygon'] = [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]
        errors, _ = new_property.check_spec(sp, schema)
        assert any('overlaps wall l-' in e for e in errors), errors
        sp = copy.deepcopy(spec)
        sp['angledWalls'] = [{'id': 'angle', 'kind': 'interior', 'start': [1, 1], 'end': [4, 4],
                              'thickness': .1, 'openings': [
            {'id': 'angle-window', 'type': 'window', 'from': .2, 'to': 2, 'sill': 2.2, 'head': 1,
             'needsReview': True},
            {'id': 'angle-door', 'type': 'door', 'from': 1, 'to': 3, 'head': 4}]}]
        errors, warnings = new_property.check_spec(sp, schema)
        assert any('sill' in e for e in errors), errors
        assert any('above the ceiling' in e for e in errors), errors
        assert any('openings angle-window and angle-door overlap' in e for e in errors), errors
        assert any('needsReview' in w for w in warnings), warnings
        sp['angledWalls'][0]['openings'] = [
            {'id': 'angle-door', 'type': 'door', 'from': .2, 'to': 1, 'head': 2}]
        errors, _ = new_property.check_spec(sp, schema)
        assert not errors, errors
        # A room that an angled wall cuts through must fail; one that stops at its face must pass.
        sp = copy.deepcopy(spec)
        room = next(r for r in sp['rooms'] if r['id'] == 'living')
        x0, z0, x1, z1 = room['bounds']
        sp['angledWalls'] = [{'id': 'cut', 'kind': 'interior', 'start': [x1 - 1.5, z1], 'end': [x1, z1 - 1.5],
                              'thickness': .1, 'openings': []}]
        errors, _ = new_property.check_spec(sp, schema)
        assert any('overlaps angled wall cut' in e for e in errors), errors
        off = .05 * 2 ** .5      # half the thickness, measured along x
        room['polygon'] = [[x0, z0], [x1, z0], [x1, z1 - 1.5 - off], [x1 - 1.5 - off, z1], [x0, z1]]
        errors, _ = new_property.check_spec(sp, schema)
        assert not any('angled wall' in e for e in errors), errors
        # write_payloads reads the ceiling height for ceilingDrop from the property's spec.json
        from lib.payload import _spec_ceiling
        work = os.path.join(tmp, 'ceiling-work')
        os.makedirs(os.path.join(work, 'payloads', 'area'))
        json.dump({'ceilingHeight': 2.9}, open(os.path.join(work, 'spec.json'), 'w'))
        assert _spec_ceiling(os.path.join(work, 'payloads', 'area')) == 2.9
        assert _spec_ceiling(tmp) == 2.44
        return 'L room accepted; wall penetration, angled-wall penetration and invalid angled openings rejected'

    @step('floor-edges')
    def s_floor_edges():
        from elements import floor_finish
        from lib.payload import write_payloads, _clean
        from lib.polygons import area, clip_rect, strips
        cases = {
            'notch': [[0, 0], [4, 0], [4, 4], [3, 4], [2, 2], [1, 4], [0, 4]],
            'shallow': [[0, 0], [4, 0], [4, 3], [0, 2.8]],
            'narrow': [[0, 0], [.5, 0], [4.5, 4], [4, 4]],
        }
        for label, poly in cases.items():
            for fn, length, width in ((floor_finish.planks, max(floor_finish.PLANK_LENGTHS), floor_finish.PLANK_W),
                                      (floor_finish.tiles, floor_finish.TILE_L, floor_finish.TILE_D)):
                stats = {}
                b = fn(spec, ['test'], random.Random(1), polygons={'test': poly}, stats=stats)
                assert .9 * area(poly) < stats['test']['area'] <= area(poly) + .005, stats
                cuts = [r['data'] for r in _clean(b)['resources'] if '-e' in r['id']]
                assert cuts, (label, fn.__name__)
                for geo in cuts:
                    xs, zs = zip(*geo['points'])
                    assert max(xs) - min(xs) <= length + .001, geo
                    assert max(zs) - min(zs) <= width + .001, geo
                # Writes must pass the real layer check, without layerIgnore.
                write_payloads(b, os.path.join(tmp, label + '-' + fn.__name__), quiet=True)
        # A single tile can cross both arms of a notch: clipping must not join them.
        poly = [[0, 0], [.6, 0], [.6, .3], [.4, .3], [.3, .1], [.2, .3], [0, .3]]
        parts = clip_rect(strips(poly), (0, .2, .6, .3))
        assert len(parts) == 2, parts
        assert abs(sum(area(pc) for pc in parts) - .045) < 1e-9, parts
        return 'six floor bundles write; cut pieces respect board/tile sizes and disconnected regions'

    @step('products')
    def s_products():
        tests = sorted(glob.glob(os.path.join(KIT, 'products', '**', 'test_*.py'), recursive=True))
        assert tests, 'no product tests found'
        for t in tests:
            sh([PY, t])
        return '%d product test(s)' % len(tests)

    @step('payload.py')
    def s_payload_selftest():
        r = sh([PY, os.path.join(KIT, 'lib', 'payload.py'), '--selftest'])
        return r.stdout.strip().split(' (')[0]

    @step('workflows')
    def s_workflows():
        files = sorted(glob.glob(os.path.join(KIT, 'workflow', '*.js')))
        js = ("const fs = require('fs'); const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;"
              "for (const f of process.argv.slice(1)) { const src = fs.readFileSync(f, 'utf8').replace(/^export\\s+/gm, '');"
              " new AsyncFunction('args', src); }")
        sh(['node', '-e', js] + files)
        return ', '.join(os.path.basename(f) for f in files) + ' parse'

    for fn in (s_plan, s_measure, s_compare, s_spec, s_shell, s_elements, s_payload, s_angled,
               s_polygons, s_floor_edges, s_products,
               s_payload_selftest, s_workflows):
        fn()

    failed = [n for n, ok, _ in results if not ok]
    print('\n%d/%d steps passed%s' % (len(results) - len(failed), len(results),
                                      '' if not failed else ': FAILED ' + ', '.join(failed)))
    if keep or failed:
        print('outputs kept in %s' % tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
