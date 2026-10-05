"""Simple polygon clipping in plan coordinates, without optional geometry dependencies."""


def area(poly):
    return abs(sum(a[0] * b[1] - b[0] * a[1]
                   for a, b in zip(poly, poly[1:] + poly[:1]))) / 2


def strips(poly):
    """Partition a simple polygon into convex horizontal strips.

    Pair boundary edges at each band's midpoint, then evaluate those same edges at
    the band ends. Unlike clipping a concave ring directly, this preserves separate
    components when a rectangle cuts across a notch.
    """
    levels = sorted({p[1] for p in poly})
    edges = list(zip(poly, poly[1:] + poly[:1]))
    out = []
    for za, zb in zip(levels, levels[1:]):
        mid = (za + zb) / 2

        def x_at(edge, z):
            a, b = edge
            return a[0] + (z - a[1]) * (b[0] - a[0]) / (b[1] - a[1])

        active = sorted((e for e in edges if min(e[0][1], e[1][1]) < mid < max(e[0][1], e[1][1])),
                        key=lambda e: x_at(e, mid))
        for left, right in zip(active[::2], active[1::2]):
            pc = [(x_at(left, za), za), (x_at(right, za), za),
                  (x_at(right, zb), zb), (x_at(left, zb), zb)]
            pc = [p for i, p in enumerate(pc) if p != pc[i - 1]]
            if len(pc) >= 3 and area(pc) > 1e-12:
                out.append(pc)
    return out


def _clip(poly, axis, value, above):
    def inside(p):
        return p[axis] >= value if above else p[axis] <= value

    out = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        if inside(prev) != inside(cur):
            t = (value - prev[axis]) / (cur[axis] - prev[axis])
            out.append(tuple(prev[k] + t * (cur[k] - prev[k]) for k in range(2)))
        if inside(cur):
            out.append(cur)
    return out


def clip_rect(parts, bounds):
    """Intersect convex parts with [x0, z0, x1, z1]; return disjoint polygons."""
    out = []
    x0, z0, x1, z1 = bounds
    for part in parts:
        pc = part
        for axis, value, above in ((0, x0, True), (0, x1, False), (1, z0, True), (1, z1, False)):
            pc = _clip(pc, axis, value, above)
        pc = [p for i, p in enumerate(pc) if p != pc[i - 1]]
        if len(pc) >= 3 and area(pc) > 1e-12:
            out.append(pc)
    return out
