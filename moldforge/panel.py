"""Sidebar (N-panel) UI for MoldForge.

Three panels in the MoldForge tab, in the order the work happens:

``MoldForge``  the model you selected, the handful of choices that change from
              toy to toy, and the Generate button.
``Result``    appears after a build: what was made, the silicone and cast
              figures, Exploded Preview and Export.
``Advanced``  closed by default; every other setting, grouped by the part of the
              mold it shapes. Each group shows its current setting at the right of
              its header, so a closed group still tells you what it holds.

Labels are short on purpose: the sidebar is narrow and a label that does not fit
is cut off with an ellipsis, which is worse than a short word with a tooltip.
"""

import os

import bpy

from .core import constants as C
from .core import units
from .core import util as mf_util
from .properties import mold_caps

_VERSION = None


def _version():
    """The add-on version string, read once from the bundled manifest, so the panel
    can show exactly which build is loaded (Blender caches extensions, so a stale
    install otherwise looks identical to a fresh one)."""
    global _VERSION
    if _VERSION is None:
        try:
            import tomllib
            path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
            with open(path, "rb") as f:
                _VERSION = tomllib.load(f).get("version", "?")
        except Exception:
            _VERSION = "?"
    return _VERSION


def _mm_per_unit(context):
    """How many millimetres one Blender unit represents, for the volume estimates
    (unit-system aware: 1 mm with no unit system, else ``scale_length`` metres)."""
    return units.mm_per_unit(context.scene)


def _vol_row(layout, name, units_cubed, density=None, mpu=1.0):
    """One 'Name ........ 123.4 ml · 142 g' line. ``mpu`` = mm per Blender unit."""
    ml = units_cubed * (mpu ** 3) / 1000.0
    row = layout.row()
    row.label(text=name)
    sub = row.row()
    sub.alignment = 'RIGHT'
    if density:
        sub.label(text=f"{ml:,.1f} ml · {ml * density:,.0f} g")
    else:
        sub.label(text=f"{ml:,.1f} ml")


def _funnel_flags(props, caps):
    """The funnel that will actually be built: ``(neck_r, mouth_r, over, fit)``.

    ``over`` = an Oversized toggle exceeds what the mold can carry; ``fit`` = the
    typed value was auto-fitted down. Returns ``None`` without a measured mold."""
    if not caps:
        return None
    hm = caps["half_min"]
    # Oversized = fully manual: the typed value is built exactly.
    neck = (props.sprue_radius if props.big_throat
            else min(props.sprue_radius, hm * C.THROAT_CAP))
    mouth = (neck * props.sprue_flare if props.big_mouth
             else min(neck * props.sprue_flare, hm * C.MOUTH_CAP))
    mouth = max(mouth, neck)
    over = ((props.big_throat and neck > hm * C.THROAT_CAP + 1e-6)
            or (props.big_mouth and mouth > hm * C.MOUTH_CAP + 1e-6))
    fit = ((not props.big_throat and props.sprue_radius > hm * C.THROAT_CAP + 1e-6)
           or (not props.big_mouth
               and neck * props.sprue_flare > hm * C.MOUTH_CAP + 1e-6))
    return neck, mouth, over, fit


def _model_status(context, props):
    """What Generate will work on: ``(ok, text, icon)`` for the line at the top of
    the panel. The one thing a first-time user gets wrong is not having the model
    selected, so say so in words instead of a greyed-out button alone."""
    obj = context.active_object
    if props.box_style == 'TRAY' and getattr(props, "tray_mode", 'EMBED') == 'STAMP':
        if obj is not None and obj.type in ('CURVE', 'FONT'):
            return True, f"Diseño: {obj.name}", 'OUTLINER_OB_FONT'
        if props.stamp_svg:
            return True, f"Diseño: {os.path.basename(props.stamp_svg)}", 'FILE_IMAGE'
        return False, "Selecciona un Texto / Curva, o elige un SVG abajo", 'INFO'
    if obj is None or obj.type != 'MESH':
        return False, "Selecciona tu modelo en el viewport", 'RESTRICT_SELECT_OFF'
    if obj.name.startswith("MF_"):
        return False, "Eso es una pieza del molde - selecciona tu modelo", 'ERROR'
    mpu = _mm_per_unit(context)
    d = obj.dimensions
    return (True,
            f"{obj.name}   {d.x * mpu:.0f} × {d.y * mpu:.0f} × {d.z * mpu:.0f} mm",
            'MESH_DATA')


LABEL_SPLIT = 0.44    # label column share of a row: room for "Silicone thickness"
                      # on the left, "Direct Printed Mold" and Auto | X | Y on the right


def _field(layout, text):
    """A labelled row: right-aligned label on the left, the control on the right.
    Blender's own property split gives the label about 40 % of the row, which
    clips "Silicone thickness" and "Throat radius" at the sidebar's normal width
    with an ellipsis; a slightly wider label column fits the full words while the
    value side still shows the longest mold type and the three split buttons."""
    split = layout.split(factor=LABEL_SPLIT, align=True)
    lab = split.row()
    lab.alignment = 'RIGHT'
    lab.label(text=text)
    return split.row(align=True)


def _generated_parts():
    """The MoldForge collection's meshes, or an empty list."""
    mfc = bpy.data.collections.get(mf_util.COLLECTION_NAME)
    if mfc is None:
        return []
    return [o for o in mfc.objects if o.type == 'MESH']


def _parts_summary(objs, tray=False):
    """'2 shells · positive · core · skin' for the Result panel."""
    names = [o.name for o in objs]
    shells = sum(1 for n in names
                 if n.startswith("MF_Mold_") and not n.startswith(("MF_Mold_Plug",
                                                                    "MF_Mold_Base",
                                                                    "MF_Mold_Cup")))
    if tray:
        bits = ["bandeja"] if shells else []
    else:
        bits = [f"{shells} carcasa{'s' if shells != 1 else ''}"] if shells else []
    if any(n.startswith("MF_Positive") for n in names):
        bits.append("positivo")
    if any(n.startswith("Core_Master") for n in names):
        bits.append("núcleo")
    if any(n.startswith("MF_Mold_Base") for n in names):
        bits.append("base")
    if any(n.startswith("MF_Mold_Plug") for n in names):
        bits.append("plug")
    if any(n.startswith("MF_Mold_Cup") for n in names):
        bits.append("ventosa")
    if any(n.startswith("MF_Skin") for n in names):
        bits.append("piel")
    return " · ".join(bits) if bits else f"{len(objs)} piezas"


# --- header summaries ------------------------------------------------------ #
# Pure functions of the property group, each <= ~24 characters, so a COLLAPSED
# section still says what it holds. They must stay cheap: headers redraw on
# every mouse move, so nothing here may touch mesh data or run mold_caps().

def summary_shell(props):
    if props.box_style == 'POUR_BOX' and props.skin_keys:
        skin = " · guante"
    else:
        skin = ""
    if props.base_style == 'FLAT':
        return ("plana +brida" if props.base_flange else "plana") + skin
    if props.base_style == 'OPEN':
        if props.base_plate:
            return "abierta +placa" + skin
        return ("abierta +ventosa" if props.suction_cup else "base abierta") + skin
    if props.base_style == 'FOLLOW':
        return "sigue el modelo" + skin
    if props.box_style != 'POUR_BOX':
        return "no aplica a este tipo"
    out = "base de anclaje" + (" · unida" if props.lock_unite else " · separada")
    if props.dual_density:
        out = "anclaje · núcleo dual"
    return out


def summary_parting(props):
    if props.parts_count >= 3:
        out = f"{props.parts_count} cuñas"
    else:
        out = "2 piezas · " + ("contorneada" if props.contoured
                               else f"{props.key_count} llaves")
    if props.split_horizontal:
        out += " · línea-h"
    return out


def summary_wings(props):
    if not props.wings:
        return "off"
    if props.wing_keys != 'NONE':
        return props.wing_keys.lower() + " llaves"
    return f"{props.wing_width:g} mm"


def summary_pour(props):
    """Short: it sits beside the longest group title, "Funnel & Vents"."""
    if props.sprue:
        out = {'TOP': "punto alto", 'XY': "centro", 'X': "centro x", 'Y': "centro y",
               'MANUAL': "manual"}.get(props.sprue_place, props.sprue_place.lower())
        if props.big_throat or props.big_mouth:
            out += " · grande"
    else:
        out = "sin embudo"
    if props.vent_place == 'MARKERS':
        return out + " · marcadores"
    if props.vent_count > 0:
        return out + f" · {props.vent_count} respiraderos"
    return out


def summary_printer(props):
    if not props.printer_fit:
        return "off"
    if props.max_print_height <= 0:
        return "elige una impresora"
    return f"máx {props.max_print_height:.0f} mm"


def summary_export(props, mpu):
    if props.last_silicone_volume <= 0.0:
        return "genera primero"
    ml = props.last_silicone_volume * (mpu ** 3) / 1000.0
    return f"{ml:,.0f} ml · {ml * props.silicone_density:,.0f} g"


class _MFPanel:
    """Shared boilerplate for every MoldForge panel."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MoldForge"


# --- 1. MoldForge: model, recipe, the per-toy choices, Generate --------------- #

class MOLDFORGE_PT_main(_MFPanel, bpy.types.Panel):
    bl_label = "MoldForge by PLESURO"
    bl_idname = "MOLDFORGE_PT_main"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        props = context.scene.moldforge
        tray = props.box_style == 'TRAY'
        pour = props.box_style == 'POUR_BOX'
        lock = pour and props.base_style == 'LOCK'

        # What Generate will work on.
        ok, text, icon = _model_status(context, props)
        layout.label(text=text, icon=icon)

        col = layout.column()
        _field(col, "Tipo de molde").prop(props, "box_style", text="")
        if props.box_style == 'SOLID':
            _field(col, "Forma").prop(props, "solid_shape", expand=True)
        if tray:
            _field(col, "Modo").prop(props, "tray_mode", text="")
            self._tray(layout, props, context)
            self._generate(layout, context, ok)
            return

        _field(col, "Base").prop(props, "base_style", text="")
        if props.base_style == 'LOCK' and not pour:
            layout.label(text="La Base de anclaje es para la Caja de vertido de silicona",
                         icon='ERROR')
        obj = context.active_object
        if (props.base_style == 'LOCK' and obj is not None
                and obj.name.startswith("Core_Master")):
            layout.label(text="Core_Master ya tiene su cruz - usa Base: Plana",
                         icon='INFO')

        # --- sizes ----------------------------------------------------- #
        head = layout.row()
        head.active = False
        mpu = _mm_per_unit(context)
        if abs(mpu - 1.0) < 1e-9:
            head.label(text="Medidas en mm")
        else:
            head.label(text=f"Medidas en mm  (1 unidad = {mpu:g} mm)")
        col = layout.column(align=True)
        _field(col, "Grosor de silicona" if pour else "Grosor de pared").prop(
            props, "wall_thickness", text="")
        if pour:
            _field(col, "Pared de carcasa").prop(props, "shell_wall", text="")
        if props.sprue:
            row = _field(col, "Radio de garganta")
            row.prop(props, "sprue_radius", text="")
            row.prop(props, "big_throat", text="", icon='FULLSCREEN_ENTER',
                     toggle=True)
        if props.parts_count == 2:
            # No text="" here: on an expanded enum that blanks the buttons.
            _field(col, "Corte").prop(props, "split_axis", expand=True)
        else:
            _field(col, "Piezas").prop(props, "parts_count", text="")
        if props.sprue:
            flags = _funnel_flags(props, mold_caps(context))
            if flags and (flags[2] or flags[3]):
                # Two short lines: a long label is clipped in the middle with "...".
                neck, mouth, over, _fit = flags
                note = layout.column(align=True)
                note.label(text=f"Se construye: garganta Ø{2 * neck:.0f}, boca Ø{2 * mouth:.0f}",
                           icon='ERROR' if over else 'INFO')
                sub = note.row()
                sub.active = False
                sub.label(text="Sobredimensionado - revisa la carcasa" if over
                          else "Ajustado automáticamente a este molde", icon='BLANK1')

        # --- what to add ----------------------------------------------- #
        head = layout.row()
        head.active = False
        head.label(text="Añadir")
        tog = layout.column(align=True)
        tog.prop(props, "wings", text="Alas de sujeción")
        tog.prop(props, "sprue", text="Embudo de vertido arriba")
        if pour and props.base_style == 'FLAT':
            tog.prop(props, "seat_floor", text="Asentar modelo en el piso")
        if lock:
            tog.prop(props, "dual_density", text="Núcleo de densidad dual (2 vertidos)")
            if props.dual_density:
                _field(layout.column(), "Pared blanda").prop(props, "core_wall", text="")
                tog = layout.column(align=True)
            tog.prop(props, "anchor_plug", text="Plug Vac-U-Lock")
        tog.prop(props, "printer_fit", text="Cortar para mi impresora")
        if props.printer_fit and props.max_print_height <= 0:
            _field(layout.column(), "Impresora").prop(props, "printer_preset", text="")

        self._generate(layout, context, ok)

    @staticmethod
    def _tray(layout, props, context):
        """The tray / stamp settings, inline: the whole mold is this one section."""
        col = layout.column(align=True)
        if props.tray_mode == 'STAMP':
            obj = context.active_object
            if obj is None or obj.type not in ('CURVE', 'FONT'):
                _field(col, "Archivo SVG").prop(props, "stamp_svg", text="")
            _field(col, "Ancho del sello").prop(props, "stamp_width", text="")
            _field(col, "Prof. del relieve").prop(props, "stamp_relief", text="")
            _field(col, "Borde").prop(props, "tray_margin", text="")
            _field(col, "Placa").prop(props, "tray_depth", text="")
            _field(col, "Pared bandeja").prop(props, "tray_wall", text="")
            _field(col, "Fondo bandeja").prop(props, "tray_floor", text="")
            layout.prop(props, "stamp_mirror", text="Espejar el diseño")
        else:
            _field(col, "Lado del detalle").prop(props, "tray_up", text="")
            _field(col, "Contorno").prop(props, "tray_outline", text="")
            _field(col, "Borde").prop(props, "tray_margin", text="")
            _field(col, "Prof. de vertido").prop(props, "tray_depth", text="")
            _field(col, "Pared bandeja").prop(props, "tray_wall", text="")
            _field(col, "Fondo bandeja").prop(props, "tray_floor", text="")

    @staticmethod
    def _generate(layout, context, ok):
        layout.separator()
        btn = layout.column()
        btn.scale_y = 1.5
        btn.enabled = ok
        btn.operator("moldforge.generate", icon='MOD_FLUIDSIM')
        foot = layout.row()
        foot.active = False
        foot.alignment = 'RIGHT'
        foot.label(text=f"v{_version()}")


# --- 2. Result: after a build ----------------------------------------------- #

class MOLDFORGE_PT_result(_MFPanel, bpy.types.Panel):
    bl_label = "Resultado"
    bl_idname = "MOLDFORGE_PT_result"
    bl_order = 5

    @classmethod
    def poll(cls, context):
        return bool(_generated_parts())

    def draw(self, context):
        layout = self.layout
        props = context.scene.moldforge
        tray = props.box_style == 'TRAY'
        mpu = _mm_per_unit(context)
        objs = _generated_parts()

        layout.label(text=_parts_summary(objs, tray), icon='OUTLINER_COLLECTION')

        col = layout.column(align=True)
        if tray:
            _vol_row(col, "Silicona a verter", props.last_silicone_volume,
                     props.silicone_density, mpu)
            _vol_row(col, "Plástico de la bandeja", props.last_plastic_volume,
                     props.plastic_density, mpu)
            if props.tray_mode != 'FRAME':    # cast volume unknown for a real object
                _vol_row(col, "Material de colada", props.last_cavity_volume,
                         props.cast_density, mpu)
        elif props.box_style == 'POUR_BOX':
            _vol_row(col, "Piel de silicona" if props.skin_keys else "Silicona a verter",
                     props.last_silicone_volume, props.silicone_density, mpu)
            _vol_row(col, "Plástico de la caja", props.last_plastic_volume,
                     props.plastic_density, mpu)
            _vol_row(col, "Material de colada", props.last_cavity_volume,
                     props.cast_density, mpu)
        else:
            _vol_row(col, "Material del molde", props.last_silicone_volume,
                     props.silicone_density, mpu)
            _vol_row(col, "Material de colada", props.last_cavity_volume,
                     props.cast_density, mpu)

        exploded = any("mf_explode" in o for o in objs)
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("moldforge.explode",
                     text="Reensamblar" if exploded else "Vista explosionada",
                     icon='STICKY_UVS_DISABLE' if exploded else 'MOD_EXPLODE')
        row.operator("moldforge.export", text="Exportar STL...", icon='FILE_TICK')


# --- 3. Advanced: everything else, by part of the mold ----------------------- #

class MOLDFORGE_PT_advanced(_MFPanel, bpy.types.Panel):
    bl_label = "Avanzado"
    bl_idname = "MOLDFORGE_PT_advanced"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 10

    def draw(self, context):
        row = self.layout.row()
        row.active = False
        row.label(text="Ajusta cada parte del molde", icon='PREFERENCES')


class _MFSub(_MFPanel):
    """Shared boilerplate for every Advanced sub-panel."""
    bl_parent_id = "MOLDFORGE_PT_advanced"
    bl_options = {'DEFAULT_CLOSED'}

    @staticmethod
    def _summary(layout, text):
        """The current setting, dimmed, at the right end of the header."""
        if text:
            sub = layout.row()
            sub.alignment = 'RIGHT'
            sub.active = False          # dimmed: state, not a control
            sub.label(text=text)

    def _body(self):
        """Label/value columns, one setting per row: labels never get clipped."""
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        return layout


class MOLDFORGE_PT_shell(_MFSub, bpy.types.Panel):
    bl_label = "Carcasa y Base"
    bl_idname = "MOLDFORGE_PT_shell"
    bl_order = 10

    @classmethod
    def poll(cls, context):
        return context.scene.moldforge.box_style != 'TRAY'

    def draw_header_preset(self, context):
        self._summary(self.layout, summary_shell(context.scene.moldforge))

    def draw(self, context):
        layout = self._body()
        props = context.scene.moldforge

        if props.box_style == 'POUR_BOX':
            layout.prop(props, "skin_keys", text="Llaves de piel de guante")

        if props.base_style == 'FLAT':
            layout.prop(props, "base_flange", text="Brida de montaje")
            if props.base_flange:
                layout.prop(props, "flange_width", text="Ancho de brida")
        elif props.base_style == 'OPEN':
            layout.prop(props, "base_plate", text="Placa de llave")
            if props.base_plate:
                layout.prop(props, "fit_clearance", text="Holgura")
            else:
                self._cup(layout, props)
        elif props.base_style == 'LOCK' and props.box_style == 'POUR_BOX':
            col = layout.column(align=True)
            col.prop(props, "lock_height", text="Altura de la base")
            col.prop(props, "lock_margin", text="Margen")
            col.prop(props, "lock_teeth", text="Dientes")
            col.prop(props, "lock_tooth_depth", text="Prof. del diente")
            col.prop(props, "lock_tolerance", text="Tolerancia")
            layout.prop(props, "lock_unite", text="Unir con el modelo")
            if not props.lock_unite:
                layout.label(text="La base sale como MF_Mold_Base", icon='INFO')
            self._cup(layout, props)
            if props.dual_density:
                box = layout.box()
                box.label(text="Flujo de dos vertidos:", icon='INFO')
                box.label(text="1. Core_Master + Generar = molde del núcleo")
                box.label(text="2. Colada el núcleo en silicona FIRME")
                box.label(text="3. Voltea este molde, llena con BLANDO")
                box.label(text="4. Presiona el núcleo - se ancla")

    @staticmethod
    def _cup(layout, props):
        layout.prop(props, "suction_cup", text="Ventosa")
        if props.suction_cup:
            layout.prop(props, "cup_diameter", text="Ø ventosa")
            layout.prop(props, "cup_depth", text="Prof. ventosa")
            layout.prop(props, "cup_lock", text="Fijación")
            layout.label(text="Llena invertido, presiona el formador", icon='INFO')


class MOLDFORGE_PT_parting(_MFSub, bpy.types.Panel):
    bl_label = "Partición"
    bl_idname = "MOLDFORGE_PT_parting"
    bl_order = 20

    @classmethod
    def poll(cls, context):
        return context.scene.moldforge.box_style != 'TRAY'

    def draw_header_preset(self, context):
        self._summary(self.layout, summary_parting(context.scene.moldforge))

    def draw(self, context):
        layout = self._body()
        props = context.scene.moldforge
        is_block = (props.box_style == 'SOLID' and props.solid_shape == 'BLOCK')

        layout.prop(props, "parts_count", text="Piezas")
        if props.parts_count >= 3:
            if is_block or not props.wings:
                layout.prop(props, "key_count", text="Pasadores de línea")
        else:
            layout.prop(props, "split_offset", text="Desplazamiento")
            layout.prop(props, "contoured", text="Contorneada")
            if not props.contoured:
                layout.prop(props, "key_count", text="Llaves")
                if props.key_count > 0 and not props.wings:
                    layout.prop(props, "registration", text="Tipo de llave")
        layout.prop(props, "split_horizontal", text="Corte horizontal")
        if props.split_horizontal:
            layout.prop(props, "split_z_offset", text="Altura de la línea")


class MOLDFORGE_PT_wings(_MFSub, bpy.types.Panel):
    bl_label = "Alas de Sujeción"
    bl_idname = "MOLDFORGE_PT_wings"
    bl_order = 30

    @classmethod
    def poll(cls, context):
        props = context.scene.moldforge
        if props.box_style == 'TRAY':
            return False
        is_block = (props.box_style == 'SOLID' and props.solid_shape == 'BLOCK')
        return not (is_block and props.parts_count >= 3)

    def draw_header(self, context):
        self.layout.prop(context.scene.moldforge, "wings", text="")

    def draw_header_preset(self, context):
        self._summary(self.layout, summary_wings(context.scene.moldforge))

    def draw(self, context):
        layout = self._body()
        props = context.scene.moldforge
        layout.active = props.wings
        layout.prop(props, "wing_width", text="Ancho")
        if props.parts_count == 2:
            layout.prop(props, "wing_keys", text="Alineación")
            if props.wing_keys != 'NONE':
                col = layout.column(align=True)
                col.prop(props, "wing_key_size", text="Tamaño de llave")
                col.prop(props, "wing_key_height", text="Altura de llave")
                col.prop(props, "wing_key_spacing", text="Separación de llaves")
                layout.prop(props, "fit_clearance", text="Holgura")
        layout.prop(props, "bolt_diameter", text="Ø perno")
        layout.prop(props, "bolt_auto", text="Pernos automáticos")
        sub = layout.column()
        sub.active = not props.bolt_auto
        sub.prop(props, "bolt_count", text="Pernos / lado")


class MOLDFORGE_PT_pour(_MFSub, bpy.types.Panel):
    bl_label = "Embudo y Respiraderos"
    bl_idname = "MOLDFORGE_PT_pour"
    bl_order = 40

    @classmethod
    def poll(cls, context):
        return context.scene.moldforge.box_style != 'TRAY'

    def draw_header(self, context):
        self.layout.prop(context.scene.moldforge, "sprue", text="")

    def draw_header_preset(self, context):
        self._summary(self.layout, summary_pour(context.scene.moldforge))

    def draw(self, context):
        layout = self._body()
        props = context.scene.moldforge
        caps = mold_caps(context)

        col = layout.column()
        col.active = props.sprue
        col.prop(props, "sprue_radius", text="Radio de garganta")
        col.prop(props, "funnel_height", text="Altura")
        row = col.row(align=True)
        row.prop(props, "sprue_flare", text="Apertura de boca")
        row.prop(props, "big_mouth", text="", icon='FULLSCREEN_ENTER', toggle=True)
        col.prop(props, "sprue_place", text="Ubicación")
        if props.sprue_place == 'MANUAL':
            col.prop(props, "sprue_x", text="X")
            col.prop(props, "sprue_y", text="Y")
        npm = len(mf_util.marker_points("MF_PourMark"))
        row = col.row(align=True)
        sub = row.row(align=True)
        sub.enabled = npm == 0
        sub.prop(props, "sprue_count", text="Puntos de vertido")
        row.operator("moldforge.add_pour_marker", text="", icon='ADD')
        if npm:
            col.label(text=f"{npm} marcador(es) añaden vertedores extra",
                      icon='EMPTY_SINGLE_ARROW')
        # The truth rows: the funnel that will actually be built.
        flags = _funnel_flags(props, caps)
        if flags:
            neck, mouth, over, fit = flags
            note = col.column(align=True)
            note.label(text=f"Se construye: garganta Ø{2 * neck:.0f}, boca Ø{2 * mouth:.0f}",
                       icon='ERROR' if (over or fit) else 'INFO')
            if over or fit:
                sub = note.row()
                sub.active = False
                sub.label(text="Sobredimensionado - revisa la carcasa" if over
                          else "Ajustado automáticamente a este molde", icon='BLANK1')

        layout.separator()
        layout.prop(props, "vent_place", text="Respiraderos")
        if props.vent_place == 'MARKERS':
            layout.operator("moldforge.add_vent_marker", icon='ADD')
            layout.prop(props, "vent_radius", text="Radio del respiradero")
            nvm = len(mf_util.marker_points("MF_VentMark"))
            layout.label(
                text=(f"{nvm} marcador(es) - un respiradero cada uno" if nvm
                      else "Shift+Clic der. sobre el modelo, luego añade"),
                icon='EMPTY_DATA' if nvm else 'INFO')
        else:
            layout.prop(props, "vent_count", text="Respiraderos de aire")
            if props.vent_count > 0:
                layout.prop(props, "vent_radius", text="Radio del respiradero")
                if caps and props.vent_radius > caps["vent_r"] + 1e-6:
                    layout.label(text=f"Respiraderos ajustados a Ø{2 * caps['vent_r']:.1f}",
                                 icon='ERROR')


class MOLDFORGE_PT_printer(_MFSub, bpy.types.Panel):
    bl_label = "Ajuste a Impresora"
    bl_idname = "MOLDFORGE_PT_printer"
    bl_order = 50

    @classmethod
    def poll(cls, context):
        return context.scene.moldforge.box_style != 'TRAY'

    def draw_header(self, context):
        self.layout.prop(context.scene.moldforge, "printer_fit", text="")

    def draw_header_preset(self, context):
        self._summary(self.layout, summary_printer(context.scene.moldforge))

    def draw(self, context):
        layout = self._body()
        props = context.scene.moldforge
        layout.active = props.printer_fit
        layout.prop(props, "printer_preset", text="Impresora")
        sub = layout.column()
        sub.enabled = props.printer_preset == 'CUSTOM'
        sub.prop(props, "max_print_height", text="Altura máx.")
        layout.prop(props, "support_clearance", text="Soportes")
        layout.prop(props, "fit_positive", text="Cortar el positivo también")
        if props.max_print_height <= 0:
            layout.label(text="Elige una impresora o escribe su altura", icon='INFO')
        elif props.max_print_height < 60.0:
            layout.label(text=f"¿{props.max_print_height:.0f} mm?! Una Photon es 165",
                         icon='ERROR')
        else:
            layout.label(text="Las piezas altas se cortan para caber", icon='CON_SIZELIMIT')


class MOLDFORGE_PT_mesh(_MFSub, bpy.types.Panel):
    bl_label = "Preparación de Malla"
    bl_idname = "MOLDFORGE_PT_mesh"
    bl_order = 60

    def draw_header_preset(self, context):
        props = context.scene.moldforge
        bits = []
        if props.decimate:
            bits.append(f"decimar {props.decimate_ratio:g}")
        if props.voxel_safe:
            bits.append(f"remesh {props.voxel_size:g}")
        self._summary(self.layout, " · ".join(bits))

    def draw(self, context):
        layout = self._body()
        props = context.scene.moldforge
        layout.prop(props, "heal", text="Reparar malla")
        layout.prop(props, "decimate", text="Decimar")
        if props.decimate:
            layout.prop(props, "decimate_ratio", text="Proporción")
        layout.prop(props, "voxel_safe", text="Remesh seguro")
        if props.voxel_safe:
            layout.prop(props, "voxel_size", text="Tamaño de vóxel")


class MOLDFORGE_PT_export(_MFSub, bpy.types.Panel):
    bl_label = "Materiales y Exportación"
    bl_idname = "MOLDFORGE_PT_export"
    bl_order = 70

    def draw_header(self, context):
        self.layout.prop(context.scene.moldforge, "export_after", text="")

    def draw_header_preset(self, context):
        props = context.scene.moldforge
        self._summary(self.layout, summary_export(props, _mm_per_unit(context)))

    def draw(self, context):
        layout = self._body()
        props = context.scene.moldforge

        layout.prop(props, "export_dir", text="Carpeta")
        layout.label(text="Marca del encabezado = exportar tras Generar", icon='INFO')
        layout.separator()
        layout.prop(props, "silicone_preset", text="Silicona")
        layout.prop(props, "silicone_density", text="Densidad")
        layout.prop(props, "cast_preset", text="Colada")
        layout.prop(props, "cast_density", text="Densidad")
        layout.prop(props, "plastic_density", text="Resina de impresión")


# Registration order matters: a child listed before its parent breaks the add-on.
PANEL_CLASSES = (
    MOLDFORGE_PT_main,
    MOLDFORGE_PT_result,
    MOLDFORGE_PT_advanced,
    MOLDFORGE_PT_shell,
    MOLDFORGE_PT_parting,
    MOLDFORGE_PT_wings,
    MOLDFORGE_PT_pour,
    MOLDFORGE_PT_printer,
    MOLDFORGE_PT_mesh,
    MOLDFORGE_PT_export,
)
