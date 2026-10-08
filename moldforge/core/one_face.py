"""One-face pour boxes built from the complete XY silhouette, including concavities.

Rasterize every projected triangle, then offset its 2D signed distance field.
Taking one horizontal mesh section or a convex hull would miss overhangs or
fill the spaces between a snowflake's arms. Interior holes belong to the relief,
not the surrounding wall. NumPy ships with Blender; no external packages needed.
"""

import math

import bpy
import bmesh
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


def _notch_station(loop, angle):
    """Outermost ray/contour intersection and its local outward wall normal.

    Rays work even when the bounds centre lies outside a concave outline. At
    a contour vertex, use the bisector so a notch on a tip remains centred.
    """
    center = Vector(((min(p[0] for p in loop) + max(p[0] for p in loop)) * .5,
                     (min(p[1] for p in loop) + max(p[1] for p in loop)) * .5))
    theta = math.radians(angle)
    ray = Vector((math.cos(theta), math.sin(theta)))
    cross = lambda a, b: a.x * b.y - a.y * b.x
    signed_area = sum(a[0] * b[1] - b[0] * a[1]
                      for a, b in zip(loop, loop[1:] + loop[:1]))
    sign = 1 if signed_area > 0 else -1

    def normal(i):
        edge = Vector(loop[(i + 1) % len(loop)]) - Vector(loop[i])
        return Vector((edge.y * sign, -edge.x * sign)).normalized()

    hits = []
    for i, a in enumerate(loop):
        edge = Vector(loop[(i + 1) % len(loop)]) - Vector(a)
        delta = Vector(a) - center
        denominator = cross(ray, edge)
        if abs(denominator) < 1e-12:
            continue
        distance, t = cross(delta, edge) / denominator, cross(delta, ray) / denominator
        if distance >= 0 and -1e-7 <= t <= 1 + 1e-7:
            outward = normal(i)
            if t < 1e-6:
                outward = (outward + normal((i - 1) % len(loop))).normalized()
            elif t > 1 - 1e-6:
                outward = (outward + normal((i + 1) % len(loop))).normalized()
            hits.append((distance, center + ray * distance, outward))
    if not hits:
        raise ValueError("No se encontró una pared para colocar la muesca en ese lado")
    _, point, outward = max(hits, key=lambda hit: hit[0])
    return point, outward


def _cut_notch(pan, inner_outline, outer, rim, props, coll):
    """An outward grip pocket open on top, with a curved floor and closed sides."""
    width, reach = props.one_face_notch_width, props.one_face_notch_depth
    height = props.one_face_notch_height
    span = min(max(p[i] for p in outer) - min(p[i] for p in outer) for i in (0, 1))
    if width >= span:
        raise ValueError("Reduce el ancho de la muesca: debe ser menor que el ancho de la caja")
    if height >= rim:
        raise ValueError("Reduce la altura de la muesca: debe quedar por encima del piso")
    point, normal = _notch_station(outer, props.one_face_notch_angle)
    tangent = Vector((-normal.y, normal.x))
    wall = props.one_face_wall
    lip = min(wall * .25, height * .12)
    inward = -wall * 1.5 - width * .25
    temporary = []

    def mesh(name, rings):
        bm = bmesh.new()
        try:
            verts = [[bm.verts.new((point.x + normal.x * n + tangent.x * x,
                                   point.y + normal.y * n + tangent.y * x, z))
                      for x, n, z in ring] for ring in rings]
            count = len(verts[0])
            for a, b in zip(verts, verts[1:]):
                for i in range(count):
                    j = (i + 1) % count
                    bm.faces.new((a[i], a[j], b[j], b[i]))
            bm.faces.new(list(reversed(verts[0])))
            bm.faces.new(verts[-1])
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
            obj = util.new_mesh_object(name, bm, coll)
            bm = None
            temporary.append(obj)
            return obj
        finally:
            if bm is not None:
                bm.free()

    try:
        # Broad sloping outer face and closed side cheeks. The far end is
        # lower/narrower, so the whole feature reads as a low outward ramp.
        hood = mesh("MF_OneFaceNotchHood", [
            [(-width * .5, inward, rim - height),
             (width * .5, inward, rim - height),
             (width * .5, inward, rim + lip),
             (-width * .5, inward, rim + lip)],
            [(-width * .5, 0.0, rim - height),
             (width * .5, 0.0, rim - height),
             (width * .5, 0.0, rim + lip),
             (-width * .5, 0.0, rim + lip)],
            [(-width * .4, reach, rim - height * .9),
             (width * .4, reach, rim - height * .9),
             (width * .4, reach, rim - height * .5),
             (-width * .4, reach, rim - height * .5)],
        ])
        # Trim the hood to the real interior outline before union, leaving
        # the relief and the existing open cavity untouched even at a bend.
        interior = build._vertical_prism(inner_outline, rim - height - wall,
                                         rim + height + wall, coll,
                                         "MF_OneFaceNotchInner")
        temporary.append(interior)
        util.boolean(hood, interior, 'DIFFERENCE')
        util.boolean(pan, hood, 'UNION')
        pocket_z = rim - height * .35
        pocket_end = max(reach - wall * .65, reach * .3)
        # A U section runs from the inner mouth to the end of the pocket.
        # Its floor follows the former ellipse; its top crosses the entire
        # hood so no roof or bridge remains above the useful cavity.
        rings = []
        for offset, factor in ((inward, 1.0), (0.0, 1.0), (pocket_end, .55)):
            center_z = pocket_z - height * .35 * max(offset, 0.0) / reach
            profile = [(width * .34 * factor * math.cos(t),
                        center_z + height * .2 * factor * math.sin(t))
                       for t in (math.pi + i * math.pi / 32 for i in range(33))]
            top = rim + height + wall
            profile += [(width * .34 * factor, top), (-width * .34 * factor, top)]
            rings.append([(x, offset, z) for x, z in profile])
        opening = mesh("MF_OneFaceNotchOpening", rings)
        # The lowest open edge is where the pocket meets the outer end wall.
        # Report capacity at this spill level rather than above the open rim.
        fill_z = rim + lip - (lip + height * .5) * pocket_end / reach
        filled_opening = util.duplicate_object(opening, "MF_OneFaceNotchFill", coll)
        temporary.append(filled_opening)
        util.boolean(filled_opening, pan, 'INTERSECT')
        mn, mx = util.world_bbox(pan)
        fill_clip = util.add_box(
            "MF_OneFaceNotchFillClip",
            Vector(((mn.x + mx.x) * .5, (mn.y + mx.y) * .5,
                    (mn.z - wall + fill_z) * .5)),
            Vector((mx.x - mn.x + wall * 2, mx.y - mn.y + wall * 2,
                    fill_z - mn.z + wall)), coll)
        temporary.append(fill_clip)
        util.boolean(filled_opening, fill_clip, 'INTERSECT')
        added_silicone = volume.mesh_volume(filled_opening)
        util.boolean(pan, opening, 'DIFFERENCE')
    finally:
        for obj in reversed(temporary):
            util.remove_object(obj)
    pan["mf_one_face_notch"] = True
    pan["mf_notch_center"] = [point.x, point.y, pocket_z]
    pan["mf_notch_normal"] = [normal.x, normal.y]
    pan["mf_notch_reach"] = reach
    pan["mf_notch_height"] = height
    pan["mf_notch_open_top"] = True
    pan["mf_rim_z"] = rim
    return added_silicone, fill_z


def _replace_mesh(obj, mesh):
    old = obj.data
    obj.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)


def _union_solid(target, part):
    """Unite baked geometry, retrying from the untouched input on bad output.

    MANIFOLD can leave closed slivers even with watertight inputs. Do not trim
    those faces or remesh the relief: EXACT resolves the same surfaces instead.
    A valid no-op must also cover the operand's bounds, or detail could be lost.
    """
    amin, amax = util.world_bbox(target)
    bmin, bmax = util.world_bbox(part)
    low = Vector(tuple(min(amin[k], bmin[k]) for k in range(3)))
    high = Vector(tuple(max(amax[k], bmax[k]) for k in range(3)))
    tolerance = max((high - low).length * 1e-6, 1e-7)
    backup = target.data.copy()
    try:
        for solver in ('MANIFOLD', 'EXACT'):
            try:
                util.boolean(target, part, 'UNION', solver=solver)
                ok, _ = util.part_is_valid(target)
                mn, mx = util.world_bbox(target)
                if ok and all(mn[k] <= low[k] + tolerance
                              and mx[k] >= high[k] - tolerance for k in range(3)):
                    return True
            except RuntimeError:
                pass
            target.modifiers.clear()
            _replace_mesh(target, backup.copy())
        return False
    finally:
        if backup.users == 0:
            bpy.data.meshes.remove(backup)


def _merge_overlapping_shells(master, coll):
    """Resolve joined, overlapping closed relief shells on the working copy.

    Whole-object booleans do not necessarily unite overlaps *inside* an operand.
    Unite edge-connected shells individually, starting with the largest body.
    Truly disjoint groups are kept: the box floor or locating base may connect
    them later, and the finished part must still pass the single-solid check.
    """
    bm = bmesh.new()
    bm.from_mesh(master.data)
    temporary = []
    try:
        components = util._face_components(bm)
        if len(components) < 2:
            return
        for faces in components:
            piece = bmesh.new()
            try:
                verts = {v: piece.verts.new(v.co)
                         for v in {v for f in faces for v in f.verts}}
                for face in faces:
                    piece.faces.new(tuple(verts[v] for v in face.verts))
                obj = util.new_mesh_object('MF_ReliefShell', piece, coll)
                obj.matrix_world = master.matrix_world.copy()
                temporary.append(obj)
            finally:
                if piece.is_valid:
                    piece.free()
        def bbox_volume(obj):
            mn, mx = util.world_bbox(obj)
            size = mx - mn
            return size.x * size.y * size.z
        pending = sorted(temporary, key=bbox_volume, reverse=True)
        groups = []
        while pending:
            target = pending.pop(0)
            while True:
                mn, mx = util.world_bbox(target)
                for part in pending:
                    pmn, pmx = util.world_bbox(part)
                    if any(pmn[k] > mx[k] or pmx[k] < mn[k] for k in range(3)):
                        continue
                    if _union_solid(target, part):
                        pending.remove(part)
                        temporary.remove(part)
                        util.remove_object(part)
                        break
                else:
                    break
            groups.append(target)
        _replace_mesh(master, groups[0].data.copy())
        for group in groups[1:]:
            util.join_meshes(master, group)
            temporary.remove(group)
    finally:
        bm.free()
        for obj in temporary:
            util.remove_object(obj)


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
    _merge_overlapping_shells(master, coll)
    mn, mx = util.world_bbox(master)
    height = mx.z - mn.z
    overlap = min(floor * .25, height * .1, step)
    master.data.transform(Matrix.Translation((0, 0, -mn.z - (0 if separate else overlap))))
    master.data.update()
    fill_rim = height - (0 if separate else overlap) + above
    # The outward hood is built in the upper wall at this pour level.
    rim = fill_rim
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
            if not _union_solid(master, seat):
                raise RuntimeError("No se pudo unir la figura con la base del encaje. "
                                   "Comprueba si hay detalles separados de la figura.")
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
        if not _union_solid(pan, master):
            raise RuntimeError("No se pudo unir la figura con el fondo en un solo sólido. "
                               "Comprueba si hay detalles separados de la base.")
    insert_volume = volume.mesh_volume(master) if separate else 0.0
    silicone = max(stock_volume - volume.mesh_volume(pan) - insert_volume, 0.0)
    if props.one_face_notch:
        added_silicone, fill_rim = _cut_notch(pan, inner, outer, rim, props, coll)
        fill_space = build._vertical_prism(inner, -depth, fill_rim, coll,
                                            "MF_OneFaceFillSpace")
        try:
            util.boolean(fill_space, pan, 'DIFFERENCE')
            if separate:
                util.boolean(fill_space, master, 'DIFFERENCE')
            silicone = volume.mesh_volume(fill_space) + added_silicone
        finally:
            util.remove_object(fill_space)
    plastic = volume.mesh_volume(pan)
    pan["mf_one_face"] = True
    pan["mf_separate_figure"] = separate
    pan["mf_fill_z"] = fill_rim
    return pan, {
        "cast_volume": cast_volume,
        "plastic_volume": plastic + insert_volume,
        "silicone_volume": silicone,
        "contour_step": step,
    }
