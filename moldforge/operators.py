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
    bl_label = "Generar Molde"
    bl_description = "Construye un molde de silicona a partir de la malla activa"
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
            self.report({'ERROR'}, f"Falló la generación del molde: {exc}")
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
            self.report({'ERROR'}, f"Falló la generación del molde: {exc}")
            return {'CANCELLED'}
        context.window_manager.progress_update(frac)
        if context.workspace:
            context.workspace.status_text_set(f"MoldForge: {label}… ({frac * 100:.0f} %)")
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
                summary = (f"Molde de sello listo. Vierte ≈ {_ml(result['silicone_volume'])} "
                           f"de silicona en la bandeja; despega al curar - la placa ES "
                           f"el sello (cara en espejo, las impresiones se leen correctamente).")
            elif mode == 'FRAME':
                summary = (f"Marco listo. Silicona a verter ≈ "
                           f"{_ml(result['silicone_volume'])} alrededor de tu objeto.")
            else:
                summary = (f"Bandeja lista. Vierte ≈ {_ml(result['silicone_volume'])} de "
                           f"silicona sobre el máster insertado.")
        elif props.box_style == 'POUR_BOX':
            what = "Piel de silicona" if getattr(props, "skin_keys", False) else "Silicona a verter"
            summary = (f"Caja de vertido lista. {what} ≈ {_ml(result['silicone_volume'])} "
                       f"(MF_Skin la muestra).")
        else:
            summary = f"Molde listo. Material ≈ {_ml(result['silicone_volume'])}."

        if props.box_style != 'TRAY' and getattr(props, "parts_count", 2) >= 3:
            summary += f" Cortado en {props.parts_count} cuñas radiales."

        if props.box_style == 'POUR_BOX' and props.base_style == 'LOCK':
            summary += " Las carcasas se anclan a la base de dientes; la base se imprime abierta."
            if not getattr(props, "lock_unite", True):
                summary += " Base conservada separada (MF_Mold_Base)."
        lv = result.get("stack_levels")
        if lv:
            is_block = props.box_style == 'SOLID' and getattr(props, "solid_shape", 'HUG') == 'BLOCK'
            ring = "" if is_block else " con anillos de línea atornillados"
            summary += f" Cortado en {lv} niveles apilados{ring}"
            if getattr(props, "printer_fit", False) and getattr(props, "max_print_height", 0.0) > 0:
                summary += (f" - cada uno cabe en la altura de impresión de "
                            f"{props.max_print_height:.0f} mm con {getattr(props, 'support_clearance', 5.0):.0f} mm "
                            f"reservados para soportes")
            summary += "."
        ps = result.get("positive_sections")
        if ps:
            nk = result.get("positive_keys", 0)
            joins = (f"{nk} pasadores impresos asientan en los huecos de la siguiente sección"
                     if nk else "caras planas para encolar")
            summary += f" Positivo cortado en {ps} secciones ({joins}; añade pegamento)."

        if result.get("dual_core"):
            summary += (f" Densidad dual: molde '{result['dual_core']}' en una segunda "
                        f"corrida (Base: Plana), coládalo FIRME, luego vierte INVERTIDO: "
                        f"llena la base abierta con BLANDO y encaja el núcleo - "
                        f"los labios se fijan en las ranuras, el exceso sale por los "
                        f"cuadrantes abiertos de la cruz.")

        if any(q.name == "MF_Mold_Plug" for q in result.get("parts", [])):
            summary += (" Plug Vac-U-Lock impreso (MF_Mold_Plug) - cola invertido "
                        "(llena la base, encaja) y la base cura con "
                        "el canal de sujeción; retíralo tras desmoldar.")
            if result.get("plug_note"):
                summary += f" {result['plug_note'][0].upper()}{result['plug_note'][1:]}."

        cup_ok = (props.base_style == 'OPEN' and not getattr(props, "base_plate", False)) \
            or (props.base_style == 'LOCK' and props.box_style == 'POUR_BOX')
        if cup_ok and getattr(props, "suction_cup", False):
            summary += (" Formador de ventosa añadido (MF_Mold_Cup) - llena invertido y "
                        "presiónalo en la base.")

        notes = []
        if result.get("plug_skipped"):
            notes.append("Plug Vac-U-Lock omitido - "
                         + (result.get("plug_note")
                            or "este molde es más pequeño que el plug a tamaño original "
                               "(necesita una cavidad de unos 92 mm de alto y una base "
                               "lo bastante ancha para la campana de 55 mm)"))
        if result.get("blades_shaved"):
            notes.append(f"{result['blades_shaved']} fragmento(s) booleano(s) de papel "
                         f"(una perforación de respiradero rozando una línea o pared) "
                         f"se eliminaron automáticamente de las carcasas")
        if result.get("kept_prev"):
            notes.append(f"las {result['kept_prev']} pieza(s) de tu molde anterior "
                         f"se conservaron (renombradas Kept_MF_*) para que el molde "
                         f"principal de densidad dual sobreviva a esta construcción del "
                         f"molde de núcleo")
        if result.get("stack_floored"):
            notes.append(f"La Altura máx. de impresión de {getattr(props, 'max_print_height', 0):.0f} mm "
                         f"parece un error de tipeo (el campo está en MILÍMETROS - una "
                         f"Photon Mono 4K es 165, una Ender-3 es 250), así que los niveles "
                         f"se dimensionaron al mínimo de 25 mm; fija la altura real de tu "
                         f"impresora o elige un preset")
        if result.get("stack_capped"):
            notes.append("Ajustar a impresora limitó la pila a 8 niveles - el molde es "
                         "extremadamente alto para esta Altura máx. de impresión")
        if result.get("stack_over"):
            notes.append("un nivel apilado sigue siendo más alto que la Altura máx. de "
                         "impresión (normalmente el vertedor del embudo sobre el nivel "
                         "superior) - sube un poco el límite o baja el embudo")
        if result.get("positive_over"):
            notes.append("una pieza del positivo es más alta que la altura útil "
                         "(Cortar el positivo también está apagado, la malla estaba "
                         "demasiado desordenada para seccionarla, o las líneas se "
                         "trabaron) - imprime esa pieza inclinada")
        if result.get("base_united") is False:
            notes.append("la base de dientes de sierra no pudo fusionarse por booleana "
                         "con el positivo (malla fuente desordenada), así que queda "
                         "dentro como una segunda carcasa - las herramientas de vaciado "
                         "del slicer dejarán la base sólida; repara o remesh el modelo "
                         "para una unión limpia")
        if result.get("remeshed"):
            notes.append("la cavidad del molde directo se talló desde una copia "
                         "auto-remeshada (el detalle fino de la cavidad se suaviza); el "
                         "positivo conserva todo el detalle")
        if result.get("trimmed"):
            notes.append("se recortó un pequeño fragmento suelto (p. ej. un borde de "
                         "base abierta) para que cada mitad quede como un solo sólido")
        undercut = result.get("undercut", 0.0)
        if undercut > 0.04:
            notes.append(f"~{undercut * 100:.0f} % del modelo tiene socavones en el eje "
                         f"{result.get('axis', '?')} y puede no liberarse limpiamente "
                         f"de un molde de dos partes (prueba otro Eje de corte)")

        if props.export_after and props.export_dir:
            directory = bpy.path.abspath(props.export_dir)
            try:
                if not os.path.isdir(directory):
                    raise OSError(f"{directory!r} no es una carpeta")
                to_export = list(result["parts"])
                to_export.extend(result.get("positive_parts")   # prints too (see button)
                                 or ([result["positive"]] if result.get("positive")
                                     else []))
                written = mf_export.export_objects(to_export, directory)
                summary += f" Se exportaron {len(written)} pieza(s)."
            except Exception as exc:  # the mold built fine; don't fail on export
                notes.append(f"falló la exportación: {getattr(exc, 'strerror', None) or exc}")

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
    bl_label = "Añadir Marcador de Respiradero"
    bl_description = ("Coloca un marcador de respiradero en el cursor 3D - ajusta el "
                      "cursor sobre el modelo primero (Shift+Clic derecho). Con "
                      "Respiraderos en Manual, Generar perfora UN respiradero en cada "
                      "marcador; mueve, duplica o elimina los marcadores como cualquier "
                      "objeto")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        em = _add_marker(context, "MF_VentMark", 'SPHERE')
        self.report({'INFO'}, f"{em.name} colocado - muévelo libremente, luego Genera.")
        return {'FINISHED'}



class MOLDFORGE_OT_add_pour_marker(bpy.types.Operator):
    bl_idname = "moldforge.add_pour_marker"
    bl_label = "Añadir Marcador de Vertido"
    bl_description = ("Coloca un marcador de vertido en el cursor 3D - ajusta el "
                      "cursor sobre el modelo primero (Shift+Clic derecho). Cada "
                      "marcador añade un vertedor EXTRA cuyo canal perfora desde la "
                      "parte superior del molde TODO el camino hasta el propio "
                      "marcador, abriéndose justo ahí en el vertido; el primer "
                      "vertedor conserva la opción de Ubicación de arriba")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        em = _add_marker(context, "MF_PourMark", 'CONE')
        self.report({'INFO'}, f"{em.name} colocado - muévelo libremente, luego Genera.")
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
    bl_label = "Vista Explosionada"
    bl_description = ("Acomoda cada pieza generada en una fila sobre una misma base - "
                      "misma altura, sin superposiciones, para que veas cada "
                      "pieza (la piel y el positivo comparten el mismo espacio en "
                      "la construcción). Pulsa de nuevo para regresar todo a su "
                      "lugar. Solo visual: las exportaciones nunca se ven afectadas")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = bpy.data.collections.get(mf_util.COLLECTION_NAME)
        objs = [o for o in (coll.objects if coll else []) if o.type == 'MESH']
        if not objs:
            self.report({'ERROR'}, "No se encontraron piezas del molde. Genera un molde primero.")
            return {'CANCELLED'}
        if _restore_exploded(coll):
            self.report({'INFO'}, "Piezas reensambladas.")
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
        self.report({'INFO'}, f"{len(objs)} piezas acomodadas - pulsa de nuevo para reensamblar.")
        return {'FINISHED'}


class MOLDFORGE_OT_export(bpy.types.Operator):
    bl_idname = "moldforge.export"
    bl_label = "Exportar Piezas del Molde"
    bl_description = ("Exporta cada impresión generada como STL: las carcasas del "
                      "molde y las piezas de base/formador (MF_Mold_*) más el "
                      "positivo (MF_Positive)")
    bl_options = {'REGISTER'}

    directory: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        directory = bpy.path.abspath(self.directory or context.scene.moldforge.export_dir)
        if not directory or not os.path.isdir(directory):
            self.report({'ERROR'}, "Elige primero una carpeta de exportación válida.")
            return {'CANCELLED'}

        coll = bpy.data.collections.get(mf_util.COLLECTION_NAME)
        if _restore_exploded(coll):
            self.report({'INFO'}, "Vista explosionada reensamblada antes de exportar.")
        # Everything printable: shells/base/former (MF_Mold_*) AND the positive —
        # with a Locking Base the positive+plinth is a print too (it was silently
        # missing from the export). MF_Skin stays out: it previews the silicone.
        parts = sorted((o for o in (coll.objects if coll else [])
                        if o.type == 'MESH'
                        and (o.name.startswith("MF_Mold_")
                             or o.name.startswith("MF_Positive"))),
                       key=lambda o: o.name)
        if not parts:
            self.report({'ERROR'}, "No se encontraron piezas del molde. Genera un molde primero.")
            return {'CANCELLED'}

        try:
            written = mf_export.export_objects(parts, directory)
        except Exception as exc:
            self.report({'ERROR'}, f"Falló la exportación: {getattr(exc, 'strerror', None) or exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Se exportaron {len(written)} pieza(s) a {directory}")
        return {'FINISHED'}

    def invoke(self, context, event):
        self.directory = bpy.path.abspath(context.scene.moldforge.export_dir or "//")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
