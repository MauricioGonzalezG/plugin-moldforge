"""Blender --background --factory-startup --python-exit-code 1 --python tests/test_round_funnel.py"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import moldforge
from moldforge.core import constants as C, sprue, units, util
from moldforge.panel import _funnel_flags
from moldforge.properties import mold_caps

moldforge.register()
scene = bpy.context.scene
p = scene.moldforge
p.box_style = 'POUR_BOX'
p.funnel_style = 'ROUND'
p.big_throat = p.big_mouth = False
p.sprue_flare = 2.35
p.funnel_height = 7.37
p.sprue_place = 'MANUAL'
p.sprue_x = p.sprue_y = 0
p.sprue_count = 1
p.vent_count = 0
coll = util.ensure_collection('RoundFunnelFixtures')
master = util.add_cone('model', Vector((0, 0, 20)), 12, 12, 40, 'Z', coll)
bpy.context.view_layer.objects.active = master
master.select_set(True)
bpy.context.view_layer.update()

# The live field, panel and builder agree above both old percentage caps.
for radius in (20, 25.54, 35, 70):
    p.sprue_radius = radius
    assert abs(p.sprue_radius - radius) < 1e-5, 'Round input was clamped'
    flags = _funnel_flags(p, mold_caps(bpy.context))
    assert abs(flags[0] - radius) < 1e-5
    assert abs(flags[1] - radius * p.sprue_flare) < 1e-4
    assert not flags[3], 'Round panel claims an automatic size reduction'
    assert _funnel_flags(p, None) == flags, 'Hidden-source panel lost the round dimensions'
    work = units.build_props(p, scene)
    neck, mouth = sprue.funnel_radii(work, 18, wall=2, edge=4)
    assert abs(neck - radius) < 1e-5
    assert abs(mouth - radius * p.sprue_flare) < 1e-4
print('ROUND FIELD / PANEL / PERCENTAGE AND EDGE LIMITS OK', flush=True)

# Measure the actual generated outer circle and bored mouth at the rim. This
# also catches cutter overshoot shrinking the displayed mouth dimensions.
diameters = []
for radius in (12, 20, 25.54, 35):
    p.sprue_radius = radius
    work = units.build_props(p, scene)
    stock = util.add_cone('mold', Vector((0, 0, 24)), 18, 18, 48, 'Z', coll)
    funnels = sprue.add_funnel_spouts(stock, master, work, coll)
    assert len(funnels) == 1
    f = funnels[0]
    assert abs(f['sprue_r'] - radius) < 1e-5
    assert abs(f['mouth_r'] - radius * p.sprue_flare) < 1e-4
    sprue.bore_funnels_and_vents(stock, master, work, funnels, coll)
    assert util.part_is_valid(stock)[0], util.part_is_valid(stock)
    ring = [math.hypot(v.co.x - f['x'], v.co.y - f['y'])
            for v in stock.data.vertices if abs(v.co.z - f['apex_z']) < 1e-4]
    assert ring, 'Generated funnel has no rim'
    assert abs(min(ring) - f['mouth_r']) < .01, (min(ring), f['mouth_r'])
    assert abs(max(ring) - f['mouth_out']) < .01, (max(ring), f['mouth_out'])
    diameters.append(2 * min(ring))
    util.remove_object(stock)
assert all(b > a for a, b in zip(diameters, diameters[1:])), diameters
print('MEASURED ROUND MOUTH DIAMETERS', diameters, flush=True)

# Near-top pour markers follow the same round radius rule.
p.sprue_radius = 25.54
stock = util.add_cone('marker mold', Vector((0, 0, 24)), 18, 18, 48, 'Z', coll)
f = sprue._marker_funnel_at(stock, master, Vector((0, 0, 44)), units.build_props(p, scene))
assert f and not f.get('runner')
assert abs(f['sprue_r'] - p.sprue_radius) < 1e-5
assert abs(f['mouth_r'] - p.sprue_radius * p.sprue_flare) < 1e-4
util.remove_object(stock)
print('ROUND TOP MARKER OK', flush=True)

# Existing semi-rectangular automatic fitting and manual overrides are retained.
p.funnel_style = 'SEMI_RECT'
p.sprue_radius = 70
caps = mold_caps(bpy.context)
assert abs(p.sprue_radius - caps['sprue_r']) < 1e-5
work = units.build_props(p, scene)
neck, mouth = sprue.funnel_radii(work, 18, wall=2, edge=18, edge_long=18)
assert neck <= 18 * C.THROAT_CAP + 1e-5
assert mouth <= max(neck, 18 / work.sprue_rect_len - 2) + 1e-5
p.big_throat = p.big_mouth = True
p.sprue_radius = 25.54
work = units.build_props(p, scene)
neck, mouth = sprue.funnel_radii(work, 18, wall=2, edge=4, edge_long=4)
assert abs(neck - p.sprue_radius) < 1e-5
assert abs(mouth - p.sprue_radius * p.sprue_flare) < 1e-4
print('SEMI-RECTANGULAR FIT AND OVERRIDES OK', flush=True)

# Conversion to scene units must not reintroduce round limits.
p.funnel_style = 'ROUND'
p.big_throat = p.big_mouth = False
scene.unit_settings.system = 'METRIC'
scene.unit_settings.scale_length = .01
scene.unit_settings.length_unit = 'CENTIMETERS'
p.sprue_radius = 25.54
work = units.build_props(p, scene)
assert abs(work.sprue_radius - 2.554) < 1e-5
neck, mouth = sprue.funnel_radii(work, 1.8, edge=.4, wall=.2)
assert abs(neck * 10 - p.sprue_radius) < 1e-5
assert abs(mouth * 10 - p.sprue_radius * p.sprue_flare) < 1e-4
print('ROUND UNIT CONVERSION OK', flush=True)
print('ALL ROUND FUNNEL TESTS PASSED', flush=True)
