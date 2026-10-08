"""Blender --background --factory-startup --python-exit-code 1 --python tests/test_one_face_notch.py"""
import math
import struct
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import moldforge
from moldforge.core import build, pipeline, units, util, volume
from moldforge.operators import _restore_exploded
from moldforge.panel import _parts_summary

moldforge.register()
props = bpy.context.scene.moldforge
props.box_style = 'ONE_FACE'
props.one_face_up = 'Z'
props.one_face_resolution = .2
props.voxel_safe = False
fixture = util.ensure_collection('NotchFixtures')


def model(loop):
    src = build._vertical_prism(loop, 0, 3, fixture, 'relief')
    detail = util.add_box('detail', Vector((0, 0, 4)), Vector((4, 3, 4)), fixture)
    util.boolean(src, detail, 'UNION')
    util.remove_object(detail)
    return src


def generate(src):
    return pipeline.build_mold_system(src, units.build_props(props, bpy.context.scene))


def check_notch(box, scale=1):
    center = Vector(box['mf_notch_center'])
    normal = Vector((*box['mf_notch_normal'], 0))
    tangent = Vector((-normal.y, normal.x, 0))
    wall = props.one_face_wall * scale
    reach, height = box['mf_notch_reach'], box['mf_notch_height']
    rim = box['mf_rim_z']
    fill = box['mf_fill_z']
    pocket_end = max(reach - wall * .65, reach * .3)
    lip = min(wall * .25, height * .12)
    spill = rim + lip - (lip + height * .5) * pocket_end / reach
    assert abs(fill - spill) < 1e-6 * max(scale, 1), 'Pour level exceeds the open edge'
    assert fill < rim, 'Open pocket must report its lower spill level'

    def inside(offset, z, across=0):
        probe = center + normal * offset + tangent * across
        probe.z = z
        return build._inside_solid(box, box.matrix_world @ probe)

    assert not inside(-wall * .5, center.z), 'Interior mouth is blocked'
    # The semicircular mouth continues upward as an open U, with no bridge
    # over the original wall or over any part of the outward pocket.
    rx = props.one_face_notch_width * scale * .34
    for z in (center.z + height * .2, rim - height * .08, rim + height * .08):
        for across in (0, rx * .6):
            assert not inside(-wall * .5, z, across), 'Bridge still closes the mouth above'
    assert inside(-wall * .5, rim - height - wall * .2), 'Lower wall was removed'
    # The downward-sloping pocket has an open top, curved floor and blind end.
    for fraction in (.1, .5, .9):
        offset = pocket_end * fraction
        factor = 1 - .45 * fraction
        pocket_z = center.z - height * .35 * offset / reach
        radius_z = height * .2 * factor
        radius_x = props.one_face_notch_width * scale * .34 * factor
        assert not inside(offset, pocket_z), 'Pocket is blocked before its blind end'
        assert not inside(offset, pocket_z - radius_z * .8), 'Bottom of pocket is blocked'
        assert not inside(offset, pocket_z, radius_x * .8), 'Side of pocket is blocked'
        for z in (pocket_z + radius_z + height * .04, rim - height * .08,
                  rim + height * .08):
            for across in (0, radius_x * .6):
                assert not inside(offset, z, across), 'Roof still covers the outward pocket'
        assert inside(offset, pocket_z - radius_z - height * .04), 'Pocket leaks through the floor'
    back = pocket_end + (reach - pocket_end) * .1
    assert inside(back, center.z - height * .35 * back / reach), 'Blind end opens to the exterior'
    exterior = reach - (reach - pocket_end) * .2
    assert inside(exterior, rim - height * .7), 'Outward ramp or its closed outer face is missing'
    assert util.part_is_valid(box)[0], util.part_is_valid(box)
    # The opposite rim must retain its original height, not rise all around.
    opposite = Vector((-center.x, -center.y, rim + lip * .5))
    assert not build._inside_solid(box, box.matrix_world @ opposite), 'Whole rim was raised'
    assert box['mf_one_face_notch']
    assert box['mf_notch_open_top']
    assert 'muesca' in _parts_summary([box])
    assert not any(o.name.startswith('MF_OneFaceNotch') for o in bpy.data.objects), 'Cutter leaked into export'


loops = {
    'rectangle': [(-18, -12), (18, -12), (18, 12), (-18, 12)],
    'oval': [(22 * math.cos(i * math.tau / 64), 14 * math.sin(i * math.tau / 64)) for i in range(64)],
    'concave': [(r * math.cos(i * math.pi / 6), r * math.sin(i * math.pi / 6))
                for i in range(12) for r in [22 if i % 2 == 0 else 12]],
}
for name, loop in loops.items():
    src = model(loop)
    original = [tuple(v.co) for v in src.data.vertices]
    for separate in (False, True):
        props.one_face_separate = separate
        props.one_face_notch = False
        base = generate(src)
        base_min, base_max = util.world_bbox(base['parts'][0])
        fill_height = base['parts'][0]['mf_fill_z']
        if separate:
            base_insert = volume.mesh_volume(base['positive'])
        lip = min(props.one_face_wall * .25, props.one_face_notch_height * .12)
        end = max(props.one_face_notch_depth - props.one_face_wall * .65,
                  props.one_face_notch_depth * .3)
        spill = fill_height + lip - (lip + props.one_face_notch_height * .5) * end / props.one_face_notch_depth
        # Compare capacity to an independent plain box at the same pour level,
        # rather than to a box filled above the new open pocket's spill edge.
        previous_depth = props.one_face_depth
        props.one_face_depth = previous_depth - (fill_height - spill)
        matched_base = generate(src)
        matched_silicone = matched_base['silicone_volume']
        assert abs(matched_base['parts'][0]['mf_fill_z'] - spill) < 1e-5
        props.one_face_depth = previous_depth
        props.one_face_notch = True
        for angle in (0, 90, 225):
            props.one_face_notch_angle = angle
            result = generate(src)
            box = result['parts'][0]
            _restore_exploded(bpy.data.collections[util.COLLECTION_NAME])
            check_notch(box)
            mn, mx = util.world_bbox(box)
            assert abs(mn.z - base_min.z) < 1e-5, 'Bottom thickness changed'
            assert abs(mx.z - base_max.z - lip) < 1e-5, 'Notch became a tall eyelet'
            assert abs(box['mf_rim_z'] - fill_height) < 1e-6, 'Original rim changed'
            assert abs(box['mf_fill_z'] - spill) < 1e-5, 'Incorrect spill level'
            added_silicone = result['silicone_volume'] - matched_silicone
            assert added_silicone > 0, 'Silicone grip volume was not counted'
            # At this pour level the U is filled above the centre of its curved
            # floor. Integrate each scaled lower half-ellipse and its rectangle
            # up to the spill level through the wall and along the sloped pocket.
            if name == 'rectangle' and angle in (0, 90):
                rx = props.one_face_notch_width * .34
                rz = props.one_face_notch_height * .2
                area = 64 * .5 * math.sin(math.tau / 64) * rx * rz
                above_center = spill - (fill_height - props.one_face_notch_height * .35)
                assert above_center > 0
                slope = props.one_face_notch_height * .35 / props.one_face_notch_depth
                mouth_area = area * .5 + 2 * rx * above_center
                expected = (mouth_area * props.one_face_wall
                            + area * .5 * end * (1 + .55 + .55 ** 2) / 3
                            + 2 * rx * (above_center * end * (1 + .55) / 2
                                        + slope * end ** 2 * (1 + 2 * .55) / 6))
                assert abs(added_silicone - expected) < expected * .002, (added_silicone, expected)
            assert abs(result['cavity_volume'] - base['cavity_volume']) < 1e-4, 'Relief changed'
            if separate:
                insert = result['positive']
                assert util.part_is_valid(insert)[0]
                assert abs(volume.mesh_volume(insert) - base_insert) < 1e-4, 'Insert changed'
                assert not build._inside_solid(box, box.matrix_world @ Vector((0, 0, -1))), 'Pocket was filled'
                assert build._inside_solid(box, box.matrix_world @ Vector((0, 0, -4))), 'Floor below socket was removed'
            assert [tuple(v.co) for v in src.data.vertices] == original
            assert abs(result['plastic_volume'] - volume.mesh_volume(box)
                       - (volume.mesh_volume(result['positive']) if separate else 0)) < 1e-3
            print('NOTCH', name, 'separate=', separate, 'angle=', angle, 'OK', flush=True)
    util.remove_object(src)

# Skinny walls and deep openings stay closed and preserve the actual pour level.
props.one_face_separate = False
props.one_face_wall = .4
props.one_face_notch_depth = .5
src = model(loops['rectangle'])
result = generate(src)
check_notch(result['parts'][0])
props.one_face_notch_depth = 30
result = generate(src)
check_notch(result['parts'][0])
util.remove_object(src)
props.one_face_wall = 2.5
props.one_face_notch_depth = 4

# Width/depth are lengths; angular position is unchanged in real scene units.
us = bpy.context.scene.unit_settings
for system, scale, length, factor in [('METRIC', .01, 'CENTIMETERS', .1), ('IMPERIAL', .0254, 'INCHES', 1 / 25.4)]:
    us.system, us.scale_length, us.length_unit = system, scale, length
    converted = units.build_props(props, bpy.context.scene)
    assert abs(converted.one_face_notch_width - props.one_face_notch_width * factor) < 1e-6
    assert abs(converted.one_face_notch_depth - props.one_face_notch_depth * factor) < 1e-6
    assert abs(converted.one_face_notch_height - props.one_face_notch_height * factor) < 1e-6
    assert converted.one_face_notch_angle == props.one_face_notch_angle
    src = model(loops['rectangle'])
    src.data.transform(Matrix.Diagonal((factor, factor, factor, 1)))
    result = generate(src)
    check_notch(result['parts'][0], factor)
    print('NOTCH UNITS', system, 'OK', flush=True)
    util.remove_object(src)
us.system, us.scale_length, us.length_unit = 'NONE', 1, 'ADAPTIVE'

# STL export contains only the box and its separate printable insert.
props.one_face_separate = True
src = model(loops['oval'])
result = generate(src)
with tempfile.TemporaryDirectory() as tmp:
    props.export_dir = tmp
    assert bpy.ops.moldforge.export(directory=tmp) == {'FINISHED'}
    paths = sorted(Path(tmp).glob('*.stl'))
    assert [p.stem for p in paths] == ['MF_Mold_A', 'MF_Positive']
    for p in paths:
        raw = p.read_bytes()
        assert len(raw) == 84 + struct.unpack('<I', raw[80:84])[0] * 50
props.one_face_notch_width = 1000
try:
    generate(src)
    raise AssertionError('An oversized notch must be rejected')
except ValueError as exc:
    assert 'ancho de la muesca' in str(exc)
assert not list(bpy.data.collections[util.COLLECTION_NAME].objects)
props.one_face_notch_width = 14
props.one_face_notch_height = 1000
try:
    generate(src)
    raise AssertionError('A notch reaching the floor must be rejected')
except ValueError as exc:
    assert 'altura de la muesca' in str(exc)
assert not list(bpy.data.collections[util.COLLECTION_NAME].objects)
util.remove_object(src)
print('ALL ONE-FACE NOTCH TESTS PASSED', flush=True)
