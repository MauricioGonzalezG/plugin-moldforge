"""Blender --background --factory-startup --python-exit-code 1 --python tests/test_one_face_components.py"""
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import moldforge
from moldforge.core import build, one_face, pipeline, units, util, volume

moldforge.register()
props = bpy.context.scene.moldforge
props.box_style = 'ONE_FACE'
props.one_face_up = 'Z'
props.one_face_resolution = .2
props.one_face_margin = 4
props.one_face_wall = 2.5
props.one_face_floor = 3
props.one_face_depth = 5
props.one_face_notch = False
props.voxel_safe = False
coll = util.ensure_collection('ComponentFixtures')


def generate(source):
    return pipeline.build_mold_system(source, units.build_props(props, bpy.context.scene))


def inside(obj, xyz):
    return build._inside_solid(obj, Vector(xyz))


# Joined STL shells interpenetrate but are individually closed. They must be
# fused without modifying the imported vertices or losing the small relief.
for separate in (False, True):
    props.one_face_separate = separate
    src = util.add_box('joined relief', Vector((0, 0, 1.5)), Vector((12, 10, 3)), coll)
    for x in (-3, 0, 3):
        part = util.add_box('letter', Vector((x, 0, 3.8)), Vector((1.5, 3, 2)), coll)
        util.join_meshes(src, part)
    original = [tuple(v.co) for v in src.data.vertices]
    assert util.island_count(src) == 4 and util.nonmanifold_count(src) == 0
    result = generate(src)
    pan = result['parts'][0]
    relief = result['positive'] if separate else pan
    assert util.part_is_valid(pan)[0], util.part_is_valid(pan)
    assert util.part_is_valid(relief)[0], util.part_is_valid(relief)
    # A separate insert is moved beside the pan by the normal preview mechanism.
    for x in (-3, 0, 3):
        probe = Vector((x, 0, 4)) + (relief.location if separate else Vector())
        assert inside(relief, probe), 'A joined letter was lost during fusion'
    assert [tuple(v.co) for v in src.data.vertices] == original
    assert len(bpy.data.collections[util.COLLECTION_NAME].objects) == (2 if separate else 1)
    assert not any(o.name.startswith('MF_ReliefShell') for o in bpy.data.objects)
    util.remove_object(src)
print('JOINED RELIEF / BOTH MODES / SOURCE PRESERVATION OK', flush=True)

# Disjoint shapes resting at the same height can legitimately be united by the
# common floor. Source normalization must not reject them before that step.
props.one_face_separate = False
src = util.add_box('two reliefs', Vector((-2, 0, 1)), Vector((2, 2, 2)), coll)
other = util.add_box('other', Vector((2, 0, 1)), Vector((2, 2, 2)), coll)
util.join_meshes(src, other)
pan = generate(src)['parts'][0]
assert util.part_is_valid(pan)[0], util.part_is_valid(pan)
assert inside(pan, (-2, 0, 1)) and inside(pan, (2, 0, 1))
assert not inside(pan, (0, 0, 1)), 'The space between the reliefs was filled'
util.remove_object(src)
print('DISJOINT RELIEFS CONNECTED BY FLOOR OK', flush=True)

# A recoverable solver failure must retry the *original* target. Inject a
# closed fragment like the tiny island reproduced on the user's real model.
target = util.add_box('union target', Vector((0, 0, 1)), Vector((4, 4, 2)), coll)
part = util.add_box('union part', Vector((0, 0, 2)), Vector((2, 2, 2)), coll)
original_boolean = util.boolean
solvers = []


def fragmenting_solver(obj, cutter, operation='DIFFERENCE', solver='MANIFOLD'):
    solvers.append(solver)
    original_boolean(obj, cutter, operation, solver)
    if solver == 'MANIFOLD':
        fragment = util.add_box('solver fragment', Vector((20, 0, 0)),
                                Vector((.01, .02, .01)), coll)
        util.join_meshes(obj, fragment)


util.boolean = fragmenting_solver
try:
    assert one_face._union_solid(target, part)
finally:
    util.boolean = original_boolean
assert solvers == ['MANIFOLD', 'EXACT']
assert util.part_is_valid(target)[0], util.part_is_valid(target)
assert abs(volume.mesh_volume(target) - 36) < 1e-5
assert not inside(target, (20, 0, 0))
assert inside(target, (0, 0, 2.5)), 'Exact retry lost the new relief'
util.remove_object(target)
util.remove_object(part)
print('CLOSED-FRAGMENT RETRY / ORIGINAL INPUT RESTORE OK', flush=True)

# Floating geometry must still fail; accepting or trimming it would silently
# lose a letter. Failed builds must remove all temporary and output objects.
src = util.add_box('floating detail', Vector((0, 0, 1.5)), Vector((12, 10, 3)), coll)
other = util.add_box('floating', Vector((0, 0, 5)), Vector((2, 2, 2)), coll)
util.join_meshes(src, other)
original = [tuple(v.co) for v in src.data.vertices]
try:
    generate(src)
    raise AssertionError('Floating relief must not be silently accepted')
except RuntimeError as exc:
    assert 'detalles separados' in str(exc), str(exc)
assert [tuple(v.co) for v in src.data.vertices] == original
assert not list(bpy.data.collections[util.COLLECTION_NAME].objects)
assert not src.hide_get()
util.remove_object(src)
print('FLOATING GEOMETRY REJECTED / CLEANUP OK', flush=True)
print('ALL ONE-FACE COMPONENT TESTS PASSED', flush=True)
