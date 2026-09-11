"""MoldForge operators: generate the mold, and export the parts."""

import os

import bpy
from mathutils import Vector
from bpy.props import StringProperty

from .core import pipeline
from .core import units
from .core import export as mf_export
from .core import util as mf_util


class MOLDFORGE_OT_generate(bpy.types.Operator):
    bl_idname = "moldforge.generate"
    bl_label = "Generate Mold"
    bl_description = "Build a silicone mold from the active mesh"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _gen = None

    @classmethod
    def poll(cls, context):
        p = getattr(context.scene, "moldforge", None)
        if (p is not None and p.box_style == 'TRAY'
                and getattr(p, "tray_mode", 'EMBED') == 'STAMP'):
            return True          # stamp: Text/Curve selection or an SVG file
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    # Scripts / tests / EXEC context build synchronously.
    def execute(self, context):
        props = context.scene.moldforge
        try:
            # Size fields are millimetres; convert to Blender units for this scene.
            work = units.build_props(props, context.scene)
            result = pipeline.build_mold_system(context.active_object, work)
        except Exception as exc:  # surface a clean message instead of a traceback
            self.report({'ERROR'}, f"Mold generation failed: {exc}")
            return {'CANCELLED'}
        return self._finalize(context, props, result)

    # From the UI: drive the build phase-by-phase so progress shows and a heavy build
    # doesn't look frozen — wait cursor + per-phase status + a progress bar.
    def invoke(self, context, event):
        props = context.scene.moldforge
        for warning in pipeline.prebuild_warnings(context.active_object, props):
            self.report({'WARNING'}, warning)
        # Size fields are millimetres; convert to Blender units for this scene.
        self._gen = pipeline.staged_build(
            context.active_object, units.build_props(props, context.scene))
        wm = context.window_manager
        context.window.cursor_set('WAIT')
        wm.progress_begin(0.0, 1.0)
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}
        try:
            frac, label = next(self._gen)
        except StopIteration as done:
            self._cleanup(context)
            return self._finalize(context, context.scene.moldforge, done.value)
        except Exception as exc:   # all recovery exhausted / bad input
            self._cleanup(context)
            self.report({'ERROR'}, f"Mold generation failed: {exc}")
            return {'CANCELLED'}
        context.window_manager.progress_update(frac)
        if context.workspace:
            context.workspace.status_text_set(f"MoldForge: {label}… ({frac * 100:.0f}%)")
        return {'RUNNING_MODAL'}

    def _cleanup(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        self._gen = None
        wm.progress_end()
        context.window.cursor_set('DEFAULT')
        if context.workspace:
            context.workspace.status_text_set(None)

    def _finalize(self, context, props, result):
        props.last_cavity_volume = result["cavity_volume"]
        props.last_silicone_volume = result["silicone_volume"]
        props.last_plastic_volume = result.get("plastic_volume", 0.0)

        # Report what to mix in the units a caster actually measures.
        mpu = units.mm_per_unit(context.scene)

        def _ml(v):
            ml = units.to_ml(v, mpu)
            return f"{ml:,.0f} ml ({ml * props.silicone_density:,.0f} g)"

        if props.box_style == 'TRAY':
            mode = getattr(props, "tray_mode", 'EMBED')
            if mode == 'STAMP':
                summary = (f"Stamp mold ready. Pour ≈ {_ml(result['silicone_volume'])} "
                           f"of silicone in the pan; peel when cured - the slab IS "
                           f"the stamp (face mirrored, imprints read correctly).")
            elif mode == 'FRAME':
                summary = (f"Frame ready. Silicone to pour ≈ "
                           f"{_ml(result['silicone_volume'])} around your object.")
            else:
                summary = (f"Tray ready. Pour ≈ {_ml(result['silicone_volume'])} of "
                           f"silicone over the embedded master.")
        elif props.box_style == 'POUR_BOX':
            what = "Silicone skin" if getattr(props, "skin_keys", False) else "Silicone to pour"
            summary = (f"Pour box ready. {what} ≈ {_ml(result['silicone_volume'])} "
                       f"(MF_Skin shows it).")
        else:
            summary = f"Mold ready. Material ≈ {_ml(result['silicone_volume'])}."

        if props.box_style != 'TRAY' and getattr(props, "parts_count", 2) >= 3:
            summary += f" Split into {props.parts_count} radial wedges."

        if props.box_style == 'POUR_BOX' and props.base_style == 'LOCK':
            summary += " Shells lock onto the sawtooth base; bottom prints open."
            if not getattr(props, "lock_unite", True):
                summary += " Base kept separate (MF_Mold_Base)."
        lv = result.get("stack_levels")
        if lv:
            is_block = props.box_style == 'SOLID' and getattr(props, "solid_shape", 'HUG') == 'BLOCK'
            ring = "" if is_block else " with bolted seam rings"
            summary += f" Cut into {lv} stacked levels{ring}"
            if getattr(props, "printer_fit", False) and getattr(props, "max_print_height", 0.0) > 0:
                summary += (f" - each fits the {props.max_print_height:.0f} mm print "
                            f"height with {getattr(props, 'support_clearance', 5.0):.0f} mm "
                            f"kept for supports")
            summary += "."
        ps = result.get("positive_sections")
        if ps:
            nk = result.get("positive_keys", 0)
            joins = (f"{nk} printed pegs seat into the next section's sockets"
                     if nk else "plain glue faces")
            summary += f" Positive cut into {ps} sections ({joins}; add glue)."

        if result.get("dual_core"):
            summary += (f" Dual density: mold '{result['dual_core']}' in a second "
                        f"run (Bottom: Flat), cast it FIRM, then pour INVERTED: "
                        f"fill the open base with SOFT and click the core in - "
                        f"the lips clamp in the grooves, excess burps out the "
                        f"cross's open quadrants.")

        if any(q.name == "MF_Mold_Plug" for q in result.get("parts", [])):
            summary += (" Vac-U-Lock plug printed (MF_Mold_Plug) - cast inverted "
                        "(fill the base, click it in) and the base cures with "
                        "the attachment channel; pull it after demolding.")
            if result.get("plug_note"):
                summary += f" {result['plug_note'][0].upper()}{result['plug_note'][1:]}."

        cup_ok = (props.base_style == 'OPEN' and not getattr(props, "base_plate", False)) \
            or (props.base_style == 'LOCK' and props.box_style == 'POUR_BOX')
        if cup_ok and getattr(props, "suction_cup", False):
            summary += (" Suction-cup former added (MF_Mold_Cup) - fill inverted and "
                        "press it into the base.")

        notes = []
        if result.get("plug_skipped"):
            notes.append("Vac-U-Lock Plug skipped - "
                         + (result.get("plug_note")
                            or "this mold is smaller than the original-size plug "
                               "(it needs a cavity about 92 mm tall and a base "
                               "wide enough for the 55 mm bell)"))
        if result.get("blades_shaved"):
            notes.append(f"{result['blades_shaved']} paper-thin boolean shard(s) "
                         f"(vent bore grazing a seam or wall) were auto-removed "
                         f"from the shells")
        if result.get("kept_prev"):
            notes.append(f"your previous mold's {result['kept_prev']} part(s) "
                         f"were kept (renamed Kept_MF_*) so the dual-density "
                         f"main mold survives this core-mold build")
        if result.get("stack_floored"):
            notes.append(f"Max Print Height {getattr(props, 'max_print_height', 0):.0f} mm "
                         f"looks like a typo (the field is MILLIMETRES - a Photon "
                         f"Mono 4K is 165, an Ender-3 is 250), so levels were sized "
                         f"to the 25 mm minimum instead; set your printer's real "
                         f"height or pick a preset")
        if result.get("stack_capped"):
            notes.append("Printer Fit capped the stack at 8 levels - the mold is "
                         "extremely tall for this Max Print Height")
        if result.get("stack_over"):
            notes.append("one stacked level is still taller than Max Print Height "
                         "(usually the funnel spout riding the top level) - raise "
                         "the limit a little or lower the funnel")
        if result.get("positive_over"):
            notes.append("a positive piece is taller than the usable height "
                         "(Cut Positive Too is off, the mesh was too messy to "
                         "section, or seams were clamped) - print that piece tilted")
        if result.get("base_united") is False:
            notes.append("the sawtooth base could not be boolean-united into the "
                         "positive (messy source mesh), so it sits inside it as a "
                         "second shell - slicer hollowing tools will leave the base "
                         "solid; heal or remesh the model for a clean union")
        if result.get("remeshed"):
            notes.append("the direct mold's cavity was carved from an auto-remeshed "
                         "copy (fine cavity detail is smoothed); the positive keeps "
                         "full detail")
        if result.get("trimmed"):
            notes.append("a small severed fragment was trimmed (e.g. an open-bottom "
                         "rim) to keep each half one solid")
        undercut = result.get("undercut", 0.0)
        if undercut > 0.04:
            notes.append(f"~{undercut * 100:.0f}% of the model is undercut on the "
                         f"{result.get('axis', '?')} axis and may not release cleanly "
                         f"from a two-part mold (try another Split Axis)")

        if props.export_after and props.export_dir:
            directory = bpy.path.abspath(props.export_dir)
            try:
                if not os.path.isdir(directory):
                    raise OSError(f"{directory!r} is not a folder")
                to_export = list(result["parts"])
                to_export.extend(result.get("positive_parts")   # prints too (see button)
                                 or ([result["positive"]] if result.get("positive")
                                     else []))
                written = mf_export.export_objects(to_export, directory)
                summary += f" Exported {len(written)} part(s)."
            except Exception as exc:  # the mold built fine; don't fail on export
                notes.append(f"export failed: {getattr(exc, 'strerror', None) or exc}")

        if notes:
            self.report({'WARNING'}, summary + " (" + "; ".join(notes) + ".)")
        else:
            self.report({'INFO'}, summary)
        return {'FINISHED'}


def _add_marker(context, base_name, draw_type):
    """Drop a marker empty at the 3D cursor into the markers collection (created
    and scene-linked on first use). Markers are ordinary objects: move (G),
    duplicate (Shift+D) and delete (X) them freely - regeneration keeps them."""
    coll = bpy.data.collections.get(mf_util.MARKER_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(mf_util.MARKER_COLLECTION)
    root = context.scene.collection
    if coll is not root and coll.name not in root.children:
        try:
            root.children.link(coll)
        except RuntimeError:
            pass
    em = bpy.data.objects.new(base_name, None)
    em.empty_display_type = draw_type
    em.empty_display_size = 4.0
    em.location = context.scene.cursor.location.copy()
    coll.objects.link(em)
    try:
        for o in context.selected_objects:
            o.select_set(False)
        em.select_set(True)
        context.view_layer.objects.active = em
    except RuntimeError:
        pass
    return em


class MOLDFORGE_OT_add_vent_marker(bpy.types.Operator):
    bl_idname = "moldforge.add_vent_marker"
    bl_label = "Add Vent Marker"
    bl_description = ("Drop a vent marker at the 3D cursor - snap the cursor onto "
                      "the model first (Shift+Right-Click). With Vents set to "
                      "Manual, Generate drills ONE vent at every marker; move, "
                      "duplicate or delete markers like any object")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        em = _add_marker(context, "MF_VentMark", 'SPHERE')
        self.report({'INFO'}, f"{em.name} placed - move it freely, then Generate.")
        return {'FINISHED'}



class MOLDFORGE_OT_add_pour_marker(bpy.types.Operator):
    bl_idname = "moldforge.add_pour_marker"
    bl_label = "Add Pour Marker"
    bl_description = ("Drop a pour marker at the 3D cursor - snap the cursor onto "
                      "the model first (Shift+Right-Click). Each marker adds an "
                      "EXTRA spout whose channel bores from the mold top ALL the "
                      "way down to the marker itself, opening into the pour right "
                      "there; the first spout keeps the Placement setting above")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        em = _add_marker(context, "MF_PourMark", 'CONE')
        self.report({'INFO'}, f"{em.name} placed - move it freely, then Generate.")
        return {'FINISHED'}


def _restore_exploded(coll):
    """Put every exploded part back exactly where the build left it.

    The stored value is the HOME location, not the offset: Blender keeps
    ``location`` in float32, so adding an offset and subtracting it again does
    not land on the same number, and the parts would creep a few microns each
    time. Writing the remembered position back is exact."""
    n = 0
    for o in (coll.objects if coll else []):
        if "mf_explode" in o:
            o.location = Vector(o["mf_explode"])
            del o["mf_explode"]
            n += 1
    return n


class MOLDFORGE_OT_explode(bpy.types.Operator):
    bl_idname = "moldforge.explode"
    bl_label = "Exploded Preview"
    bl_description = ("Lay every generated part out in a row on one baseline - "
                      "same height, nothing overlapping, so you can see each "
                      "piece (the skin and the positive share the same space in "
                      "the build). Press again to snap everything back. Display "
                      "only: exports are never affected")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = bpy.data.collections.get(mf_util.COLLECTION_NAME)
        objs = [o for o in (coll.objects if coll else []) if o.type == 'MESH']
        if not objs:
            self.report({'ERROR'}, "No mold parts found. Generate a mold first.")
            return {'CANCELLED'}
        if _restore_exploded(coll):
            self.report({'INFO'}, "Parts reassembled.")
            return {'FINISHED'}

        boxes = {o.name: mf_util.world_bbox(o) for o in objs}
        centers = {n: (b[0] + b[1]) * 0.5 for n, b in boxes.items()}

        # A parts LAYOUT, not a vertical explosion: every piece is laid out in
        # one row on a common baseline, so nothing overlaps (the skin and the
        # positive occupy the same space in the build) and every part is seen
        # at the same height, the way they would sit on the print bed.
        pos = next((o for o in objs if o.name.startswith("MF_Positive")), None)
        anchor = centers[pos.name] if pos is not None else \
            sum(centers.values(), Vector()) / len(centers)
        base_z = min(boxes[o.name][0].z for o in objs)

        def _rank(name):
            if name.startswith("MF_Mold_Cup") or name.startswith("MF_Mold_Plug"):
                return 4                       # printed accessories, last
            if name.startswith("Core_Master"):
                return 5
            if name.startswith("MF_Mold_Base"):
                return 1
            if name.startswith("MF_Mold_"):
                return 0                       # the shells lead
            if name.startswith("MF_Positive"):
                return 2
            if name == "MF_Skin":
                return 3
            return 6

        row = sorted(objs, key=lambda o: (_rank(o.name), o.name))
        widths = [boxes[o.name][1].x - boxes[o.name][0].x for o in row]
        gap = max(0.12 * max(widths), 10.0)
        total = sum(widths) + gap * (len(row) - 1)

        x = anchor.x - total * 0.5
        for o, w in zip(row, widths):
            mn, mx = boxes[o.name]
            offv = Vector((x + w * 0.5 - (mn.x + mx.x) * 0.5,
                           anchor.y - (mn.y + mx.y) * 0.5,
                           base_z - mn.z))
            o["mf_explode"] = list(o.location)     # home, for an exact restore
            o.location = o.location + offv
            x += w + gap
        self.report({'INFO'}, f"{len(objs)} parts laid out - press again to reassemble.")
        return {'FINISHED'}


class MOLDFORGE_OT_export(bpy.types.Operator):
    bl_idname = "moldforge.export"
    bl_label = "Export Mold Parts"
    bl_description = ("Export every generated print as STL: the mold shells and "
                      "base/former parts (MF_Mold_*) plus the positive (MF_Positive)")
    bl_options = {'REGISTER'}

    directory: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        directory = bpy.path.abspath(self.directory or context.scene.moldforge.export_dir)
        if not directory or not os.path.isdir(directory):
            self.report({'ERROR'}, "Choose a valid export folder first.")
            return {'CANCELLED'}

        coll = bpy.data.collections.get(mf_util.COLLECTION_NAME)
        if _restore_exploded(coll):
            self.report({'INFO'}, "Exploded preview reassembled before export.")
        # Everything printable: shells/base/former (MF_Mold_*) AND the positive —
        # with a Locking Base the positive+plinth is a print too (it was silently
        # missing from the export). MF_Skin stays out: it previews the silicone.
        parts = sorted((o for o in (coll.objects if coll else [])
                        if o.type == 'MESH'
                        and (o.name.startswith("MF_Mold_")
                             or o.name.startswith("MF_Positive"))),
                       key=lambda o: o.name)
        if not parts:
            self.report({'ERROR'}, "No mold parts found. Generate a mold first.")
            return {'CANCELLED'}

        try:
            written = mf_export.export_objects(parts, directory)
        except Exception as exc:
            self.report({'ERROR'}, f"Export failed: {getattr(exc, 'strerror', None) or exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Exported {len(written)} part(s) to {directory}")
        return {'FINISHED'}

    def invoke(self, context, event):
        self.directory = bpy.path.abspath(context.scene.moldforge.export_dir or "//")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
