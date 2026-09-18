"""Run with Blender --background --factory-startup --python this_file.py."""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from moldforge.core import build, util, split


def model(coll, lobes, taper, amplitude=5):
    bm = bmesh.new()
    rings = []
    n = 96
    for z, scale in ((0, 1), (2, taper), (30, taper)):
        ring = []
        for i in range(n):
            a = 2 * math.pi * i / n
            r = (20 + amplitude * math.cos(lobes * a)) * scale
            ring.append(bm.verts.new((r * math.cos(a), r * math.sin(a), z)))
        rings.append(ring)
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])
    for lower, upper in zip(rings, rings[1:]):
        for i in range(n):
            j = (i + 1) % n
            bm.faces.new((lower[i], lower[j], upper[j], upper[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return util.new_mesh_object('fixture', bm, coll)


def check(lobes, taper, height=10, wall=2, amplitude=5, margin=4):
    coll = util.ensure_collection('test')
    p = SimpleNamespace(lock_height=height, lock_margin=margin,
                        lock_tooth_depth=2, lock_teeth=3, lock_tolerance=.2,
                        shell_wall=wall, detail_voxel=1)
    master = model(coll, lobes, taper, amplitude)
    plinth, socket = build.build_locking_plinth(master, p, coll)
    mn, mx = util.world_bbox(plinth)
    skirt = build._footprint_prism(master, margin + 2 + .2 + wall,
                                  mn.z, mx.z + .2 + wall, p, coll, 'skirt')
    bpy.context.view_layer.update()
    side_tree = BVHTree.FromPolygons(
        [v.co for v in skirt.data.vertices],
        [list(f.vertices) for f in skirt.data.polygons if abs(f.normal.z) < .5])
    worst = float('inf')
    outside = 0
    # Horizontal rays at every tooth crest: test actual material between the
    # socket and the external skirt, not just mesh topology or its bounding box.
    for k in range(3):
        z = mn.z + (mx.z - mn.z) * (k + .5) / 3
        loop = build._cross_section_loop(socket, z)
        for x, y in loop:
            point = Vector((x, y, z))
            loc, normal, _, distance = side_tree.find_nearest(point)
            assert loc is not None
            signed = (loc - point).length
            if (point - loc).dot(normal) > 1e-5:
                signed = -signed
                outside += 1
            worst = min(worst, signed)
    print(f'WALL lobes={lobes} taper={taper} height={height} wall={wall}: '
          f'clearance={worst:.4f}, outside={outside}', flush=True)
    assert util.part_is_valid(skirt)[0], util.part_is_valid(skirt)
    # The toothed cavity and final split must also remain printable solids.
    util.boolean(skirt, socket, 'DIFFERENCE')
    halves = split._split_planar(skirt, 'X', coll)
    for half in halves:
        assert util.part_is_valid(half)[0], util.part_is_valid(half)
    for obj in list(coll.objects):
        util.remove_object(obj)
    assert outside == 0
    assert worst >= wall - .1, (worst, wall)
    return worst


def check_jacket():
    coll = util.ensure_collection('test')
    master = model(coll, 3, 1.4)
    p = SimpleNamespace(lock_height=10, lock_margin=4, lock_tooth_depth=2,
                        lock_teeth=3, lock_tolerance=.2, shell_wall=2,
                        silicone_gap=4, detail_voxel=1)
    plinth, socket = build.build_locking_plinth(master, p, coll)
    outer, info = build.build_locking_jacket(master, master, plinth, socket, p, coll)
    util.boolean(outer, info['cavity_cutter'], 'DIFFERENCE')
    build.cut_below_z(outer, util.world_bbox(plinth)[0].z, coll)
    for half in split._split_planar(outer, 'X', coll):
        assert util.part_is_valid(half)[0], util.part_is_valid(half)
    print('JACKET: both halves are watertight single solids', flush=True)
    for obj in list(coll.objects):
        util.remove_object(obj)


if __name__ == '__main__':
    results = [check(lobes, taper) for lobes, taper in
               ((0, 1), (3, 1), (5, 1), (3, .6), (3, 1.4))]
    results += [check(lobes, 1, amplitude=12) for lobes in (3, 5, 8)]
    results += [check(5, taper, height=h, amplitude=12)
                for h in (1, 3, 20) for taper in (.1, 2)]
    results += [check(3, 1.4, height=3, wall=.4), check(3, .6, wall=4)]
    results += [check(3, 1, margin=m) for m in (0, .01)]
    check_jacket()
