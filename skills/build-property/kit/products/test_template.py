#!/usr/bin/env python3
"""Test for products/_template.py, and the pattern to copy for a new product module's test.

    python3 $KIT/products/test_template.py
    PROPERTY_BUILD_KIT=$KIT python3 <cache>/<room>/test_<product>.py   (a copy in the product cache)

Builds the product twice (two instances, the total qty passed on both calls), merges the bundles,
and checks: geometry and catalogue entries are shared rather than duplicated, ids carry the prefix,
the bundle validates (lib.payload.validate), no coplanar layers z-fight (check_layers), and
write_payloads writes queue files to a temporary directory. Exits non-zero on failure.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# A copy in the product cache is run with PROPERTY_BUILD_KIT=<kit folder>.
KIT = os.environ.get('PROPERTY_BUILD_KIT') or os.path.dirname(HERE)
sys.path.insert(0, KIT)
sys.path.insert(0, HERE)

from lib.payload import _clean, check_layers, merge, validate, write_payloads  # noqa: E402
import _template as product  # noqa: E402  (a new test imports its own module: import <module> as product)


def main():
    one = product.build('lv-', 'side-table-1', (1.0, 0, 2.0), rotation_y=0.0, room_id='living', qty=2)
    two = product.build('lv-', 'side-table-2', (3.0, 0, 2.0), rotation_y=1.5708, room_id='living', qty=2)
    b = merge(one, two)
    assert len(b['resources']) == len(one['resources']), 'geometry/materials must be shared between instances'
    assert len(b['products']) == 1 and b['products'][0]['qty'] == 2, b['products']
    assert all(n['id'].startswith('lv-') for n in b['nodes']), 'every id carries the prefix'
    roots = [n for n in b['nodes'] if n['parentId'] is None]
    assert len(roots) == 2 and all(n.get('catalogueKey') == 'lv-' + product.PRODUCT['key'] for n in roots)
    w, d, h = product.PRODUCT['dimensions_m']
    top = next(n for n in one['nodes'] if n['id'].endswith('-top'))
    assert abs(top['position'][1] + 0.0125 - h) < 0.002, 'top surface at the listed height'
    errors, warnings = validate(_clean(b))
    assert not errors, errors
    layers = check_layers(b)
    assert not layers, layers
    zero = product.build('lv-', 'side-table-3', (5.0, 0, 2.0), qty=0)
    assert not zero['products'], 'qty=0 emits no catalogue entry'
    custom = product.build('lv-', 'side-table-4', (6.0, 0, 2.0), materials={'top': 'm-oak'}, qty=0)
    assert custom['nodes'][1]['render']['materials'] == ['m-oak']
    out = tempfile.mkdtemp(prefix='product-test-')
    paths = write_payloads(merge(b, zero, custom), out, quiet=True)
    tools = [json.load(open(p))['tool'] for p in paths]
    assert tools[0] == 'put_scene_resources' and 'upsert_product' in tools and tools[-1] == 'put_scene_nodes', tools
    print('test_template OK: %d resources, %d products, %d nodes -> %d files in %s' % (
        len(b['resources']), len(b['products']), len(b['nodes']), len(paths), out))


if __name__ == '__main__':
    main()
