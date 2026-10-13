"""Blender --background --factory-startup --python-exit-code 1 --python tests/test_wing_waist.py"""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from moldforge.core import build,util,volume

coll=util.ensure_collection('WaistFixtures')
p=SimpleNamespace(lock_height=10,lock_margin=4,lock_tooth_depth=2,
    lock_teeth=3,lock_tolerance=.2,silicone_gap=4,shell_wall=2,detail_voxel=1)

# A flat-footed hourglass with a wide upper overhang has no underside pocket.
# The old expanded column sampled the overhang and cut its external waist wing.
bm=bmesh.new()
rows=[]
for z,radius in ((0,20),(4,20),(20,11),(40,24)):
    rows.append([bm.verts.new((radius*math.cos(i*math.tau/96),
                              radius*math.sin(i*math.tau/96),z)) for i in range(96)])
bm.faces.new(rows[0])
bm.faces.new(rows[-1])
for lower,upper in zip(rows,rows[1:]):
    for i in range(96):
        j=(i+1)%96
        bm.faces.new((lower[i],lower[j],upper[j],upper[i]))
bmesh.ops.recalc_face_normals(bm,faces=bm.faces[:])
master=util.new_mesh_object('hourglass',bm,coll)
original=[tuple(v.co) for v in master.data.vertices]
plinth,socket=build.build_locking_plinth(master,p,coll)
under=build.socket_pocket_cutter(master,plinth,p,coll)
assert under is not None
for x in (-19.3,-18.5,18.5,19.3):
    assert not build._inside_solid(under,Vector((x,0,20))), 'Waist exterior was mistaken for an underside pocket'
outer,info=build.build_locking_jacket(master,master,plinth,socket,p,coll)
mn,mx=util.world_bbox(outer)
build._profile_flange(outer,master,1,0,(mn+mx)*.5,mn,mx,6,8,5,p,coll,
                      cavity=info['cavity_cutter'],under=under)
util.boolean(outer,info['cavity_cutter'],'DIFFERENCE')
assert util.part_is_valid(outer)[0],util.part_is_valid(outer)
for y in (-2.4,0,2.4):
    for sign in (-1,1):
        for radius in (17.8,18.5,19.3,20.5,22):
            assert build._inside_solid(outer,Vector((sign*radius,y,20))), 'Flange-to-waist attachment has a hole'
assert [tuple(v.co) for v in master.data.vertices]==original
print('OVERHANG / WAIST CONTACT THROUGH FULL WING THICKNESS OK',flush=True)
for obj in list(coll.objects):util.remove_object(obj)

# A true closed model with a recessed underside still needs the pocket cutter:
# otherwise clamp material would bridge its open central base.
master=util.add_cone('hollow underside',Vector((0,0,10)),20,20,20,'Z',coll,segments=96)
cavity=util.add_cone('underside cavity',Vector((0,0,5.5)),16,16,13,'Z',coll)
util.boolean(master,cavity,'DIFFERENCE',solver='EXACT')
util.remove_object(cavity)
assert util.part_is_valid(master)[0],util.part_is_valid(master)
plinth,socket=build.build_locking_plinth(master,p,coll)
under=build.socket_pocket_cutter(master,plinth,p,coll)
assert under is not None and volume.mesh_volume(under)>0
assert build._inside_solid(under,Vector((0,0,6))), 'True underside pocket is no longer cleared'
assert not build._inside_solid(under,Vector((20.5,0,6))), 'Pocket cut extended outside the base footprint'
print('TRUE UNDERSIDE POCKET PRESERVED OK',flush=True)
for obj in list(coll.objects):util.remove_object(obj)
print('ALL WING WAIST TESTS PASSED',flush=True)
