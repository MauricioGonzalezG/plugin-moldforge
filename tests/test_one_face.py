"""Blender --background --factory-startup --python-exit-code 1 --python tests/test_one_face.py"""
import math
import struct
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import moldforge
from moldforge.core import build, export, one_face, pipeline, units, util, volume
from moldforge.operators import _restore_exploded
from moldforge.panel import PANEL_CLASSES, _parts_summary

moldforge.register()
props = bpy.context.scene.moldforge
props.box_style = 'ONE_FACE'
props.one_face_up = 'Z'
props.one_face_resolution = .2
coll = util.ensure_collection('OneFaceFixtures')


def at(obj, x, y, z):
    return build._inside_solid(obj, Vector((x, y, z)))


def reset_settings():
    props.one_face_notch = False
    props.one_face_up = 'Z'
    props.one_face_separate = False
    props.one_face_margin = 4
    props.one_face_wall = 2.5
    props.one_face_floor = 3
    props.one_face_depth = 5
    props.one_face_seat_depth = 2
    props.one_face_clearance = .2
    props.voxel_safe = False


def make_model(loop, name):
    base = build._vertical_prism(loop, 0, 3, coll, name)
    detail = util.add_box('detail', Vector((0, 0, 4)), Vector((4, 3, 4)), coll)
    util.boolean(base, detail, 'UNION')
    util.remove_object(detail)
    return base


def generate(src):
    return pipeline.build_mold_system(src, units.build_props(props, bpy.context.scene))


# Check the distance algorithm against a brute-force Euclidean distance.
mask = np.zeros((9, 11), dtype=bool)
mask[2, 3] = mask[6, 8] = True
dist = one_face._distance(mask)
for y in range(9):
    for x in range(11):
        expected = min(math.hypot(x - 3, y - 2), math.hypot(x - 8, y - 6))
        assert abs(dist[y, x] - expected) < 1e-9
print('DISTANCE FIELD OK', flush=True)

loops = {
    'triangle': [(-18, -12), (18, -12), (0, 20)],
    'snowflake': [(r * math.cos(i * math.pi / 6), r * math.sin(i * math.pi / 6))
                  for i in range(12) for r in [22 if i % 2 == 0 else 12]],
    'oval': [(22 * math.cos(i * math.tau / 80), 14 * math.sin(i * math.tau / 80))
             for i in range(80)],
}

for name, loop in loops.items():
    reset_settings()
    src = make_model(loop, name)
    original = [tuple(v.co) for v in src.data.vertices]
    result = generate(src)
    box = result['parts'][0]
    assert result['positive'] is None
    assert util.part_is_valid(box)[0], util.part_is_valid(box)
    assert at(box, 0, 0, 4), 'Integrated relief is missing'
    assert not at(box, 9, 0, 5), 'Top should remain open around the relief'
    assert result['silicone_volume'] > 0
    assert [tuple(v.co) for v in src.data.vertices] == original
    assert len(bpy.data.collections[util.COLLECTION_NAME].objects) == 1
    # Test a concavity between snowflake arms: a convex hull would occupy this point.
    if name == 'snowflake':
        direction = Vector((math.cos(math.pi / 6), math.sin(math.pi / 6)))
        p = direction * 15
        assert not at(box, p.x, p.y, 4), 'Snowflake concavity was filled'
        p = direction * 18
        assert at(box, p.x, p.y, 5), 'Concave wall must follow the arm gap'
    print('INTEGRATED', name, 'OK', flush=True)

    props.one_face_separate = True
    result = generate(src)
    box, insert = result['parts'][0], result['positive']
    assert insert is not None
    assert util.part_is_valid(box)[0], util.part_is_valid(box)
    assert util.part_is_valid(insert)[0], util.part_is_valid(insert)
    assert 'mf_explode' in insert and insert.location.x > box.location.x
    _restore_exploded(bpy.data.collections[util.COLLECTION_NAME])
    assert (box.location - insert.location).length < 1e-6
    assert not at(box, 0, 0, -.5), 'Pocket should be empty'
    assert not at(box, 0, 0, 4), 'Separate mode must remove the main relief'
    assert at(insert, 0, 0, -1), 'Insert locating base is missing'
    assert at(box, 0, 0, -3), 'Floor below the pocket is missing'
    assert abs(util.world_bbox(box)[0].z + 5) < 1e-5
    assert abs(util.world_bbox(insert)[0].z + 2) < 1e-5
    probe = util.duplicate_object(insert, 'collision', coll)
    util.boolean(probe, box, 'INTERSECT')
    assert not probe.data.polygons or volume.mesh_volume(probe) < 1e-4
    util.remove_object(probe)
    assert abs(result['plastic_volume'] - volume.mesh_volume(box)
               - volume.mesh_volume(insert)) < 1e-3
    assert 'figura separable' in _parts_summary([box, insert])
    with tempfile.TemporaryDirectory() as tmp:
        props.export_dir = tmp
        bpy.ops.moldforge.export(directory=tmp)
        paths = sorted(Path(tmp).glob('*.stl'))
        assert [p.stem for p in paths] == ['MF_Mold_A', 'MF_Positive']
        for p in paths:
            raw = p.read_bytes()
            assert len(raw) == 84 + struct.unpack('<I', raw[80:84])[0] * 50
    print('SEPARATE / SOCKET / STL', name, 'OK', flush=True)
    util.remove_object(src)

# The silhouette must include a ledge above the middle section, and must ignore
# interior holes when creating the exterior box wall.
reset_settings()
src = util.add_box('overhang', Vector((0, 0, 2)), Vector((24, 20, 4)), coll)
ledge = util.add_box('ledge', Vector((11, 0, 4)), Vector((10, 14, 2)), coll)
util.boolean(src, ledge, 'UNION')
util.remove_object(ledge)
hole = util.add_cone('hole', Vector((0, 0, 2)), 3, 3, 20, 'Z', coll)
util.boolean(src, hole, 'DIFFERENCE')
util.remove_object(hole)
field, origin, step = one_face._projected_field(src, 8, .2)
outline = one_face._outline(field, origin, step, 4)
assert max(p[0] for p in outline) > 19.5, 'Upper overhang was omitted'
assert one_face._contains(outline, (0, 0)), 'Inner hole became an exterior border'
result = generate(src)
assert util.part_is_valid(result['parts'][0])[0]
assert not at(result['parts'][0], 17.5, 0, 8), 'Wall must enclose the upper ledge'
print('FULL PROJECTION / HOLES OK', flush=True)
util.remove_object(src)

# Capture evaluated modifiers and respect negative face selection/transforms.
reset_settings()
src = make_model(loops['triangle'], 'transformed')
src.matrix_world = Matrix.Translation((75, -25, 12)) @ Matrix.Rotation(math.pi / 2, 4, 'Y')
props.one_face_up = 'X'
props.one_face_separate = True
result = generate(src)
assert util.part_is_valid(result['positive'])[0]
_restore_exploded(bpy.data.collections[util.COLLECTION_NAME])
bpy.context.view_layer.update()
assert abs(util.world_bbox(result['positive'])[1].z - 15) < 1e-4, util.world_bbox(result['positive'])
props.one_face_up = '-X'
result = generate(src)
assert util.part_is_valid(result['parts'][0])[0]
assert util.part_is_valid(result['positive'])[0]
util.remove_object(src)
reset_settings()
src = util.add_box('modifiers', Vector((0, 0, 1)), Vector((20, 16, 2)), coll)
mod = src.modifiers.new('visible size', 'BEVEL')
mod.width = .5
mod.segments = 3
expected_volume = volume.mesh_volume(src)
props.voxel_safe = True   # the installed default must not coarsen a closed relief
result = generate(src)
assert abs(result['cavity_volume'] - expected_volume) < 1e-3
assert not result['remeshed']
assert src.modifiers.get('visible size') == mod
print('FACE / TRANSFORM / MODIFIERS OK', flush=True)
util.remove_object(src)

# Scene-unit conversion, actual generated thickness, and automatic STL export.
reset_settings()
props.one_face_separate = True
us = bpy.context.scene.unit_settings
for system, scale, length, factor in [('NONE', 1, 'ADAPTIVE', 1),
        ('METRIC', .01, 'CENTIMETERS', .1), ('IMPERIAL', .0254, 'INCHES', 1 / 25.4)]:
    us.system, us.scale_length, us.length_unit = system, scale, length
    converted = units.build_props(props, bpy.context.scene)
    for key in ('one_face_wall', 'one_face_floor', 'one_face_margin',
                'one_face_depth', 'one_face_seat_depth', 'one_face_clearance',
                'one_face_resolution', 'one_face_notch_width', 'one_face_notch_depth'):
        assert abs(getattr(converted, key) - getattr(props, key) * factor) < 1e-6
    src = make_model(loops['triangle'], 'units')
    src.data.transform(Matrix.Diagonal((factor, factor, factor, 1)))
    result = generate(src)
    assert util.part_is_valid(result['parts'][0])[0]
    assert util.part_is_valid(result['positive'])[0]
    _restore_exploded(bpy.data.collections[util.COLLECTION_NAME])
    assert abs(util.world_bbox(result['parts'][0])[0].z + 5 * factor) < 1e-5
    print('UNITS', system, 'OK', flush=True)
    util.remove_object(src)
us.system, us.scale_length, us.length_unit = 'NONE', 1, 'ADAPTIVE'
with tempfile.TemporaryDirectory() as tmp:
    src = make_model(loops['oval'], 'auto export')
    bpy.context.view_layer.objects.active = src
    props.export_after = True
    props.export_dir = tmp
    assert bpy.ops.moldforge.generate() == {'FINISHED'}
    assert len(list(Path(tmp).glob('*.stl'))) == 2
    assert not any('mf_explode' in o for o in bpy.data.collections[util.COLLECTION_NAME].objects)
    props.one_face_separate = False
    for path in Path(tmp).glob('*.stl'):
        path.unlink()
    assert bpy.ops.moldforge.generate() == {'FINISHED'}
    assert len(list(Path(tmp).glob('*.stl'))) == 1
    util.remove_object(src)
props.export_after = False
for cls in PANEL_CLASSES:
    if cls.bl_idname in {'MOLDFORGE_PT_shell', 'MOLDFORGE_PT_parting',
                          'MOLDFORGE_PT_wings', 'MOLDFORGE_PT_pour', 'MOLDFORGE_PT_printer'}:
        assert not cls.poll(bpy.context)
print('AUTO EXPORT / REGENERATION / PANELS OK', flush=True)

# Fail cleanly instead of silently dropping disconnected geometry or piercing a
# wall with a pocket larger than the configured border.
reset_settings()
src = make_model(loops['triangle'], 'invalid clearance')
props.one_face_separate = True
props.one_face_margin = .1
props.one_face_clearance = .3
try:
    generate(src)
    raise AssertionError('Impossible pocket clearance should be rejected')
except ValueError as exc:
    assert 'holgura' in str(exc)
assert not list(bpy.data.collections[util.COLLECTION_NAME].objects)
assert not src.hide_get()
util.remove_object(src)
reset_settings()
src = util.add_box('islands', Vector((-20, 0, 2)), Vector((6, 6, 4)), coll)
other = util.add_box('island', Vector((20, 0, 2)), Vector((6, 6, 4)), coll)
util.join_meshes(src, other)
try:
    generate(src)
    raise AssertionError('Disconnected outlines should be rejected')
except RuntimeError as exc:
    assert 'partes separadas' in str(exc)
assert not list(bpy.data.collections[util.COLLECTION_NAME].objects)
util.remove_object(src)
print('ERROR CLEANUP OK', flush=True)
print('ALL ONE-FACE BOX TESTS PASSED', flush=True)
