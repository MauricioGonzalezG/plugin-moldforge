"""Blender --background --factory-startup --python-exit-code 1 --python tests/test_wings.py"""
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy
from mathutils import Vector
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import moldforge
from moldforge.core import build, util, split, units, pipeline

LOOP = [(-15, 0), (15, 0), (15, 10), (7, 22), (14, 35),
        (12, 50), (-12, 50), (-14, 35), (-7, 22), (-15, 10)]

def distance(point):
    p = Vector(point)
    best = float('inf')
    for a, b in zip(LOOP, LOOP[1:] + LOOP[:1]):
        a, b = Vector(a), Vector(b)
        d = b-a
        t = max(0, min(1, (p-a).dot(d)/d.length_squared))
        best = min(best, (p-a-t*d).length)
    return best

def check(axis, width, per_half):
    coll = util.ensure_collection('WingTests')
    ai = axis
    axes = [i for i in range(3) if i != ai]
    mold = build._vertical_prism(LOOP, -17, 23, coll, 'mold')
    for v in mold.data.vertices:
        co = v.co.copy()
        v.co[axes[0]], v.co[axes[1]], v.co[ai] = co.x, co.y, co.z
    mold.data.update()
    from moldforge.core import meshprep
    meshprep.ensure_outward_normals(mold)
    band = build._contour_flange_stock(mold, ai, 3, width, 2*per_half, coll)
    lo, hi = util.world_bbox(band)
    assert abs(lo[ai] - (3-per_half)) < 1e-4
    assert abs(hi[ai] - (3+per_half)) < 1e-4
    errors = []
    for v in band.data.vertices:
        x, y = v.co[axes[0]], v.co[axes[1]]
        if ai != 2 and not width+1 < y < 49-width:
            continue  # intentionally clipped flush at the base and mouth
        errors.append(abs(distance((x,y)) - width))
    assert errors and max(errors) < .1, max(errors)
    assert util.part_is_valid(band)[0], util.part_is_valid(band)
    if ai != 2:
        util.boolean(mold, band, 'UNION')
        for half in split._split_planar(mold, 'XY'[ai], coll):
            assert util.part_is_valid(half)[0], util.part_is_valid(half)
            wing_coords = [v.co[ai] for v in half.data.vertices
                           if distance((v.co[axes[0]], v.co[axes[1]])) > width-.1
                           and 10 < v.co[axes[1]] < 40]
            assert wing_coords
            assert abs(max(wing_coords)-min(wing_coords)-per_half) < .01
    print('WING', axis, width, per_half, 'max width error', max(errors), flush=True)
    for o in list(coll.objects): util.remove_object(o)

moldforge.register()
for ai in (0, 1, 2):
    for width, thick in ((4.17, 1.2), (8, 4)):
        check(ai, width, thick)

for n in (3, 4):
    coll = util.ensure_collection('WingTests')
    mold = util.add_cone('radial', Vector((0, 0, 15)), 15, 15, 30, 'Z', coll)
    props = SimpleNamespace(bolt_auto=False, bolt_count=0, key_count=0, wings=True)
    center = build.add_radial_wings(mold, coll, mold, 2, 4.17, 6, 1.5, props, n)
    assert util.world_bbox(mold)[1].x > 19, 'Radial wings were rolled back'
    for half in split._split_radial(mold, None, props, coll, 2, n, center):
        assert util.part_is_valid(half)[0], util.part_is_valid(half)
    for o in list(coll.objects): util.remove_object(o)
    print('RADIAL', n, 'OK', flush=True)

coll = util.ensure_collection('WingTests')
mold = util.add_cone('horizontal', Vector((0, 0, 15)), 15, 15, 30, 'Z', coll)
build.add_horizontal_flange(mold, coll, mold, 2, 4.17, 6, 1.5,
                            SimpleNamespace(), 15, [])
assert util.world_bbox(mold)[1].x > 19, 'Horizontal flange was rolled back'
for half in split.cut_horizontal([mold], coll, 15):
    assert util.part_is_valid(half)[0], util.part_is_valid(half)
for o in list(coll.objects): util.remove_object(o)
print('HORIZONTAL OK', flush=True)

coll = util.ensure_collection('WingTests')
mold = util.add_box('offset', Vector((0, 0, 0)), Vector((30, 4, 30)), coll)
props = SimpleNamespace(split_offset=1, bolt_auto=False, bolt_count=0,
                        key_count=0, wings=True, contoured=False, wing_keys='NONE')
assert build.add_wings(mold, 'Y', coll, mold, 2, 4.17, 10, 1.5, props) == []
for half in split.split(mold, None, 'Y', props, coll, 2):
    coords = [v.co.y for v in half.data.vertices if v.co.x > 18]
    assert coords and abs(max(coords)-min(coords)-5) < .01
for o in list(coll.objects): util.remove_object(o)
print('OFFSET AND THICK WINGS OK', flush=True)

p = bpy.context.scene.moldforge
p.wing_width = 4.17
p.wing_thickness = 2.3
for system, scale, length, factor in [('NONE', 1, 'ADAPTIVE', 1),
        ('METRIC', .01, 'CENTIMETERS', .1), ('IMPERIAL', .0254, 'INCHES', 1/25.4)]:
    u=bpy.context.scene.unit_settings
    u.system, u.scale_length, u.length_unit = system, scale, length
    converted = units.build_props(p, bpy.context.scene)
    assert abs(converted.wing_thickness-2.3*factor) < 1e-5
    assert abs(converted.wing_width-4.17*factor) < 1e-5
    coll = util.ensure_collection('WingTests')
    mold = util.add_box('units', Vector((0, 0, 0)), Vector((40, 40, 50))*factor, coll)
    derived = pipeline._derive_sizes(mold, converted)
    assert abs(derived.wing_thickness-4.6*factor) < 1e-5
    band = build._contour_flange_stock(mold, 1, 0, derived.wing_width,
                                       derived.wing_thickness, coll)
    lo, hi = util.world_bbox(band)
    assert abs((hi.x/factor-20)-4.17) < .05
    assert abs((hi.y-lo.y)/factor-4.6) < .001
    for o in list(coll.objects): util.remove_object(o)
print('UNITS OK', flush=True)
