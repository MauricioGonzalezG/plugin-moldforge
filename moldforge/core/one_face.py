"""One-face pour boxes built from the complete XY silhouette, including concavities.

Rasterize every projected triangle, then offset its 2D signed distance field.
Taking one horizontal mesh section or a convex hull would miss overhangs or
fill the spaces between a snowflake's arms. Interior holes belong to the relief,
not the surrounding wall. NumPy ships with Blender; no external packages needed.
"""

import math

import numpy as np
from mathutils import Matrix, Vector

from . import build, util, volume


def _projected_field(master, padding, resolution):
    mn, mx = util.world_bbox(master)
    span = max(mx.x - mn.x, mx.y - mn.y) + 2.0 * padding
    step = max(resolution, span / 1024.0)
    origin = (mn.x - padding - 3 * step, mn.y - padding - 3 * step)
    nx = int(math.ceil((mx.x + padding - origin[0]) / step)) + 4
    ny = int(math.ceil((mx.y + padding - origin[1]) / step)) + 4
    mask = np.zeros((ny, nx), dtype=bool)
    me = master.data
    me.calc_loop_triangles()
    coords = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get('co', coords)
    coords = coords.reshape(-1, 3)[:, :2]
    indices = np.empty(len(me.loop_triangles) * 3, dtype=np.int32)
    me.loop_triangles.foreach_get('vertices', indices)
    # Conservatively include cells touched by a triangle. This also retains thin
    # projected rims that a grid of centre-point rays could miss entirely.
    for ids in indices.reshape(-1, 3):
        tri = coords[ids]
        a, b, c = tri
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(cross) < step * step * 1e-10:
            continue
        if cross < 0:
            tri = tri[::-1]
        low = np.floor((tri.min(axis=0) - origin) / step - 1).astype(int)
        high = np.ceil((tri.max(axis=0) - origin) / step + 1).astype(int)
        x0, y0 = max(low[0], 0), max(low[1], 0)
        x1, y1 = min(high[0] + 1, nx), min(high[1] + 1, ny)
        xx = origin[0] + np.arange(x0, x1)[None, :] * step
        yy = origin[1] + np.arange(y0, y1)[:, None] * step
        inside = np.ones((y1 - y0, x1 - x0), dtype=bool)
        for p, q in zip(tri, np.roll(tri, -1, axis=0)):
            dx, dy = q - p
            # Half a pixel's square support along the edge normal.
            allowance = 0.5 * step * (abs(dx) + abs(dy))
            inside &= dx * (yy - p[1]) - dy * (xx - p[0]) >= -allowance
        mask[y0:y1, x0:x1] |= inside
    if not mask.any():
        raise RuntimeError("No se pudo obtener una silueta con área de la figura")
    field = (_distance(mask) - _distance(~mask)) * step
    return field, origin, step


def _distance(mask):
    """Exact Euclidean distance to a True pixel in O(width * height)."""
    height, width = mask.shape
    xx = np.arange(width)[None, :]
    left = np.maximum.accumulate(np.where(mask, xx, -2 * (height + width)), axis=1)
    right = np.minimum.accumulate(
        np.where(mask, xx, 2 * (height + width))[:, ::-1], axis=1)[:, ::-1]
    f = np.minimum(xx - left, right - xx).astype(np.float64) ** 2
    out = np.empty_like(f)
    # Lower envelope of parabolas, one column at a time (squared distances).
    for x in range(width):
        values = f[:, x]
        sites = [0]
        bounds = [-math.inf, math.inf]
        for q in range(1, height):
            p = sites[-1]
            s = ((values[q] + q * q) - (values[p] + p * p)) / (2 * (q - p))
            while s <= bounds[-2]:
                sites.pop()
                bounds.pop(-2)
                p = sites[-1]
                s = ((values[q] + q * q) - (values[p] + p * p)) / (2 * (q - p))
            sites.append(q)
            bounds.insert(-1, s)
        k = 0
        for q in range(height):
            while bounds[k + 1] < q:
                k += 1
            p = sites[k]
            out[q, x] = (q - p) ** 2 + values[p]
    return np.sqrt(out)


def _area(loop):
    return abs(sum(a[0] * b[1] - b[0] * a[1]
                   for a, b in zip(loop, loop[1:] + loop[:1]))) * 0.5


def _contains(loop, point):
    x, y = point
    inside = False
    for a, b in zip(loop, loop[1:] + loop[:1]):
        if ((a[1] > y) != (b[1] > y)
                and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]):
            inside = not inside
    return inside


def _simplify(loop, tolerance):
    """Remove grid artefacts while bounding the contour's displacement."""
    def rdp(points):
        keep = {0, len(points) - 1}
        pending = [(0, len(points) - 1)]
        while pending:
            first, last = pending.pop()
            a, b = Vector(points[first]), Vector(points[last])
            d = b - a
            best, error = first, tolerance
            for k in range(first + 1, last):
                v = Vector(points[k]) - a
                t = max(0.0, min(1.0, v.dot(d) / max(d.length_squared, 1e-30)))
                distance = (v - t * d).length
                if distance > error:
                    best, error = k, distance
            if best != first:
                keep.add(best)
                pending.extend(((first, best), (best, last)))
        return [points[k] for k in sorted(keep)]

    mid = len(loop) // 2
    return rdp(loop[:mid + 1])[:-1] + rdp(loop[mid:] + loop[:1])[:-1]


def _outline(field, origin, step, offset):
    """Marching squares; keep the exterior loop, discard only enclosed holes."""
    level = offset + step * 1e-7     # avoid crossings exactly at grid vertices
    a, b = field[:-1, :-1], field[:-1, 1:]
    c, d = field[1:, 1:], field[1:, :-1]
    cases = ((a < level).astype(np.uint8) + 2 * (b < level)
             + 4 * (c < level) + 8 * (d < level))
    pairs = {1: [(3, 0)], 2: [(0, 1)], 3: [(3, 1)], 4: [(1, 2)],
             6: [(0, 2)], 7: [(3, 2)], 8: [(2, 3)], 9: [(2, 0)],
             11: [(2, 1)], 12: [(1, 3)], 13: [(1, 0)], 14: [(0, 3)]}
    coords, adjacency = {}, {}
    ys, xs = np.nonzero((cases != 0) & (cases != 15))
    for y, x in zip(ys.tolist(), xs.tolist()):
        case = int(cases[y, x])
        if case in (5, 10):
            centre_inside = (a[y, x] + b[y, x] + c[y, x] + d[y, x]) * .25 < level
            segments = ([(0, 1), (2, 3)] if (case == 5) == centre_inside
                        else [(3, 0), (1, 2)])
        else:
            segments = pairs[case]
        keys = [(0, y, x), (1, y, x + 1), (0, y + 1, x), (1, y, x)]
        for e0, e1 in segments:
            for edge in (e0, e1):
                key = keys[edge]
                if key in coords:
                    continue
                vertical, py, px = key
                qy, qx = py + vertical, px + (1 - vertical)
                v0, v1 = field[py, px], field[qy, qx]
                t = (level - v0) / (v1 - v0)
                coords[key] = (origin[0] + (px + t * (qx - px)) * step,
                               origin[1] + (py + t * (qy - py)) * step)
            k0, k1 = keys[e0], keys[e1]
            adjacency.setdefault(k0, []).append(k1)
            adjacency.setdefault(k1, []).append(k0)
    loops, seen = [], set()
    for start in adjacency:
        if start in seen:
            continue
        loop, current, previous = [], start, None
        while current not in seen:
            seen.add(current)
            loop.append(coords[current])
            neighbours = adjacency[current]
            if len(neighbours) != 2:
                raise RuntimeError("El contorno de la figura no está cerrado")
            nxt = neighbours[0] if neighbours[0] != previous else neighbours[1]
            previous, current = current, nxt
        if current != start:
            raise RuntimeError("El contorno de la figura se cruza")
        if len(loop) >= 3:
            loops.append(loop)
    if not loops:
        raise RuntimeError("No se encontró un borde exterior cerrado")
    outer = max(loops, key=_area)
    if any(loop is not outer and not _contains(outer, loop[0]) for loop in loops):
        raise RuntimeError("La silueta tiene partes separadas: aumenta la separación "
                           "del borde o une la figura sobre una base continua. "
                           "El inserto separable necesita una base continua")
    return _simplify(outer, step * .1)


def build_box(master, props, coll):
    """Build a contour pan and optionally add a flat locating base to the insert.

    The seated figure starts at Z=0. A separate insert's vertical-sided locating
    base occupies -seat_depth..0, and its pocket grows laterally by clearance.
    Floor thickness is measured BELOW the pocket, so it can never pierce the box.
    """
    wall, floor = props.one_face_wall, props.one_face_floor
    margin, above = props.one_face_margin, props.one_face_depth
    separate = props.one_face_separate
    depth = props.one_face_seat_depth if separate else 0.0
    clear = props.one_face_clearance if separate else 0.0
    resolution = min(props.one_face_resolution, wall / 8.0)
    if separate and clear > margin:
        raise ValueError("La separación del borde debe ser al menos la holgura del encaje")
    field, origin, step = _projected_field(master, margin + wall + clear, resolution)
    inner = _outline(field, origin, step, margin)
    outer = _outline(field, origin, step, margin + wall)
    mn, mx = util.world_bbox(master)
    height = mx.z - mn.z
    overlap = min(floor * .25, height * .1, step)
    master.data.transform(Matrix.Translation((0, 0, -mn.z - (0 if separate else overlap))))
    master.data.update()
    rim = height - (0 if separate else overlap) + above
    bottom = -depth - floor
    cast_volume = volume.mesh_volume(master)
    pan = build._vertical_prism(outer, bottom, rim, coll, "MF_Mold_A")
    stock_volume = volume.mesh_volume(pan)
    cutter = build._vertical_prism(inner, 0.0, rim + floor, coll, "MF_OneFaceCavity")
    try:
        util.boolean(pan, cutter, 'DIFFERENCE')
    finally:
        util.remove_object(cutter)
    if separate:
        footprint = _outline(field, origin, step, 0.0)
        seat = build._vertical_prism(footprint, -depth, overlap, coll, "MF_OneFaceSeat")
        try:
            util.boolean(master, seat, 'UNION')
        finally:
            util.remove_object(seat)
        pocket = build._vertical_prism(_outline(field, origin, step, clear),
                                       -depth, max(overlap, step), coll, "MF_OneFacePocket")
        try:
            util.boolean(pan, pocket, 'DIFFERENCE')
        finally:
            util.remove_object(pocket)
        master.name = "MF_Positive"
    else:
        util.boolean(pan, master, 'UNION')
    plastic = volume.mesh_volume(pan)
    insert_volume = volume.mesh_volume(master) if separate else 0.0
    pan["mf_one_face"] = True
    pan["mf_separate_figure"] = separate
    return pan, {
        "cast_volume": cast_volume,
        "plastic_volume": plastic + insert_volume,
        "silicone_volume": max(stock_volume - plastic - insert_volume, 0.0),
        "contour_step": step,
    }
