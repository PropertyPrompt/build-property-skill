#!/usr/bin/env python3
"""Compare generated payloads node-by-node with a reference build.

    python3 compare_payloads.py GENERATED_DIR --ref DIR [DIR ...] [--prefix so- --prefix si-] [--tol 0.001]

Reference directories are applied in order like the queue did: a later
put_scene_nodes / put_scene_resources for the same id replaces the earlier one,
remove_scene_node deletes. Only ids with the given prefixes are compared
(default: everything in the generated payloads). For each node it checks
parent, container, role, roomId, behaviour, shadow/raycast flags, position,
rotation, scale, geometry DATA (ids may differ), materials and instance
matrices, numbers within --tol. Names are reported but never fail.
Exit status 1 when anything differs.
"""
import argparse
import glob
import json
import math
import os
import sys


def load(dirs):
    nodes, res = {}, {}
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, '[0-9]*-*.json'))):
            p = json.load(open(f))
            a = p.get('args', {})
            if p['tool'] == 'put_scene_nodes':
                for n in a['nodes']:
                    nodes[n['id']] = n
            elif p['tool'] == 'put_scene_resources':
                for r in a['resources']:
                    res[r['id']] = r
            elif p['tool'] == 'remove_scene_node':
                nodes.pop(a.get('nodeId'), None)
    return nodes, res


def numdiff(a, b, path=''):
    """Max numeric difference between two JSON values, or None if structure differs."""
    if isinstance(a, bool) or isinstance(b, bool):
        return 0 if a == b else None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return None
        m = 0
        for x, y in zip(a, b):
            d = numdiff(x, y)
            if d is None:
                return None
            m = max(m, d)
        return m
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return None
        m = 0
        for k in a:
            d = numdiff(a[k], b[k])
            if d is None:
                return None
            m = max(m, d)
        return m
    return 0 if a == b else None


def angdiff(a, b):
    m = 0
    for x, y in zip(a, b):
        d = (x - y + math.pi) % (2 * math.pi) - math.pi
        m = max(m, abs(d))
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('generated')
    ap.add_argument('--ref', nargs='+', required=True)
    ap.add_argument('--prefix', action='append')
    ap.add_argument('--tol', type=float, default=0.001)
    ap.add_argument('--show-names', action='store_true')
    a = ap.parse_args()
    gn, gr = load([a.generated])
    rn, rr = load(a.ref)
    pref = tuple(a.prefix) if a.prefix else None

    def keep(i):
        return pref is None or i.startswith(pref)

    gids = {i for i in gn if keep(i)}
    rids = {i for i in rn if keep(i)}
    problems = []
    for i in sorted(rids - gids):
        problems.append((i, 'missing in generated'))
    for i in sorted(gids - rids):
        problems.append((i, 'extra in generated'))
    worst = {'position': 0, 'rotation': 0, 'instances': 0, 'geometry': 0}
    names = []
    for i in sorted(gids & rids):
        g, r = gn[i], rn[i]
        for k in ('parentId', 'container', 'role', 'roomId', 'raycast', 'castShadow', 'receiveShadow', 'visible'):
            if g.get(k) != r.get(k):
                problems.append((i, '%s: %r vs ref %r' % (k, g.get(k), r.get(k))))
        if numdiff(g.get('behavior'), r.get('behavior')) not in (0,):
            problems.append((i, 'behavior differs: %s vs ref %s' % (g.get('behavior'), r.get('behavior'))))
        d = numdiff(g.get('position', [0, 0, 0]), r.get('position', [0, 0, 0]))
        worst['position'] = max(worst['position'], d)
        if d > a.tol:
            problems.append((i, 'position %s vs ref %s (%.4f)' % (g.get('position'), r.get('position'), d)))
        d = angdiff(g.get('rotation', [0, 0, 0]), r.get('rotation', [0, 0, 0]))
        worst['rotation'] = max(worst['rotation'], d)
        if d > a.tol + 1e-4:
            problems.append((i, 'rotation %s vs ref %s (%.4f rad)' % (g.get('rotation'), r.get('rotation'), d)))
        if numdiff(g.get('scale', [1, 1, 1]), r.get('scale', [1, 1, 1])) > a.tol:
            problems.append((i, 'scale %s vs ref %s' % (g.get('scale'), r.get('scale'))))
        if g.get('name') != r.get('name'):
            names.append((i, g.get('name'), r.get('name')))
        gR, rR = g.get('render'), r.get('render')
        if bool(gR) != bool(rR):
            problems.append((i, 'render present %s vs ref %s' % (bool(gR), bool(rR))))
            continue
        if not gR:
            continue
        if gR.get('mode', 'mesh') != rR.get('mode', 'mesh'):
            problems.append((i, 'render.mode %s vs %s' % (gR.get('mode'), rR.get('mode'))))
        if gR['materials'] != rR['materials']:
            problems.append((i, 'materials %s vs ref %s' % (gR['materials'], rR['materials'])))
        gd = gr.get(gR['geometry'], {}).get('data')
        rd = rr.get(rR['geometry'], {}).get('data')
        if gd is None or rd is None:
            problems.append((i, 'geometry data unavailable (%s / %s)' % (gR['geometry'], rR['geometry'])))
        else:
            d = numdiff(gd, rd)
            if d is None:
                problems.append((i, 'geometry structure differs: %s vs ref %s' % (gd, rd)))
            else:
                worst['geometry'] = max(worst['geometry'], d)
                if d > a.tol:
                    problems.append((i, 'geometry %s vs ref %s differ by %.4f' % (gR['geometry'], rR['geometry'], d)))
        if gR['geometry'] != rR['geometry']:
            names.append((i, 'geometry id ' + gR['geometry'], rR['geometry']))
        gi = gR.get('instances') or []
        ri = rR.get('instances') or []
        d = numdiff(gi, ri)
        if d is None:
            problems.append((i, 'instances: %d vs ref %d (or structure differs)' % (len(gi), len(ri))))
        else:
            worst['instances'] = max(worst['instances'], d)
            if d > a.tol:
                problems.append((i, 'instances differ by %.4f' % d))
    print('compared %d nodes (generated %d, reference %d)' % (len(gids & rids), len(gids), len(rids)))
    print('max differences within matched nodes: ' + ', '.join('%s %.4f' % kv for kv in worst.items()))
    for i, msg in problems:
        print('DIFF  %-28s %s' % (i, msg))
    if a.show_names:
        for i, gname, rname in names:
            print('name  %-28s %s | ref %s' % (i, gname, rname))
    else:
        print('%d name/geometry-id differences (not failures; --show-names to list)' % len(names))
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
