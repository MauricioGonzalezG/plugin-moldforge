"""User-facing parameters for MoldForge, stored on the Scene.

All size fields are in MILLIMETRES, whatever the scene's unit system (Metric, Imperial,
or none). The build converts mm to Blender units with the scene's unit scale
(``core.units.mm_per_unit``), so a 3 mm wall is 3 mm whether the scene is set to
millimetres, inches, or the bare "1 unit = 1 mm" convention. Sensible defaults suit
print-scale models (~20-200 mm); the sprue/vents are auto-capped so they can't blow out
a small mold.
"""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from mathutils import Vector

from .core import constants as C
from .core import units


def _dist(name, default, desc, mn=0.0, soft=None, update=None):
    # A size field, in MILLIMETRES. A plain float (not a Blender 'DISTANCE'/LENGTH unit)
    # so the number the user types is always millimetres, independent of the scene's unit
    # system; the build converts mm -> Blender units via core.units.mm_per_unit.
    kw = dict(name=name, description=desc + " (mm)", default=default, min=mn)
    if soft is not None:
        kw["soft_max"] = soft
    if update is not None:
        kw["update"] = update
    return FloatProperty(**kw)


def mold_caps(context):
    """The funnel/vent size caps the active model implies (same formulas the
    builder uses), or None when no model is active to measure. Lets the UI clamp
    input live and show the true effective sizes instead of silently capping at
    build time.

    Uses the object's CACHED bounding box (8 corners) rather than iterating every
    vertex — this runs on every panel redraw, so a per-vertex scan here makes
    Blender crawl on heavy meshes."""
    obj = getattr(context, "active_object", None)
    if obj is None or obj.type != 'MESH' or obj.name.startswith("MF_"):
        return None
    mw = obj.matrix_world
    xs = []
    ys = []
    for c in obj.bound_box:                 # 8 local-space corners, cached
        w = mw @ Vector((c[0], c[1], c[2]))
        xs.append(w.x)
        ys.append(w.y)
    if not xs:
        return None
    p = context.scene.moldforge
    # Sizes are in millimetres; the model's footprint is in Blender units. Work the caps
    # out in millimetres (convert the footprint with the scene's unit scale) so they match
    # the mm size fields in any unit system.
    mpu = units.mm_per_unit(context.scene)
    offset = p.wall_thickness + (p.shell_wall if p.box_style == 'POUR_BOX' else 0.0)
    footprint_mm = min(max(xs) - min(xs), max(ys) - min(ys)) * mpu
    half_min = footprint_mm * 0.5 + offset
    if half_min <= 0.0:
        return None
    return {"sprue_r": C.THROAT_CAP * half_min, "mouth_r": C.MOUTH_CAP * half_min,
            "vent_r": C.VENT_CAP * half_min, "half_min": half_min}


def _clamp_sprue_radius(self, context):
    if getattr(self, "big_throat", False):
        return                                   # Oversized Throat = fully manual, no clamp
    caps = mold_caps(context)
    if not caps:
        return
    cap = caps["half_min"] * C.THROAT_CAP
    if self.sprue_radius > cap:
        self["sprue_radius"] = cap               # dict-set: no update recursion


def _clamp_vent_radius(self, context):
    caps = mold_caps(context)
    if caps and self.vent_radius > caps["vent_r"]:
        self["vent_radius"] = caps["vent_r"]


# Typical densities (g/ml) for the weight estimate. Picking a preset just fills the
# density field below; you can still type a custom number afterwards (that flips the
# dropdown back to Custom on its own next redraw — the value is what matters).
_SILICONE_PRESETS = {
    'DRAGONSKIN': 1.07,    # Smooth-On Dragon Skin platinum series
    'MOLDSTAR': 1.18,      # Smooth-On Mold Star
    'OOMOO': 1.42,         # Smooth-On Oomoo tin-cure
    'ECOFLEX': 1.07,       # Smooth-On Ecoflex
    'MOLDMAX': 1.42,       # Smooth-On Mold Max tin-cure
    'PLATSIL': 1.12,       # Polytek / generic platinum RTV
}
_CAST_PRESETS = {
    'SILICONE': 1.10,      # casting silicone (platinum ~1.07, tin RTV ~1.2)
    'URETHANE': 1.05,      # Smooth-On Smooth-Cast urethane resin
    'EPOXY': 1.15,         # generic epoxy casting resin
    'POLYESTER': 1.10,     # polyester casting resin
    'PLASTER': 1.80,       # plaster of Paris / gypsum
    'WAX': 0.90,           # casting / candle wax
    'CONCRETE': 2.40,      # cement / GFRC
}


_PRINTER_HEIGHTS = {
    # Z build height in mm, from the manufacturers' published build volumes.
    # Anycubic
    'PHOTON_MONO4K': 165.0, 'PHOTON_MONOX': 245.0, 'PHOTON_M3': 180.0,
    'PHOTON_M3PLUS': 245.0, 'PHOTON_M3MAX': 300.0, 'PHOTON_M5S': 200.0,
    'PHOTON_M7': 230.0, 'PHOTON_M7PRO': 230.0, 'PHOTON_M7MAX': 300.0,
    # Elegoo
    'MARS3': 175.0, 'MARS_ULTRA': 165.0, 'SATURN2': 250.0, 'SATURN3ULTRA': 260.0,
    'SATURN4': 220.0, 'JUPITER': 300.0,
    # Phrozen
    'PHROZEN_MINI8KS': 180.0, 'PHROZEN_MIGHTY': 235.0, 'PHROZEN_MEGA8KS': 300.0,
    'PHROZEN_MEGA8K': 400.0,
    # Creality, UniFormation, Formlabs, Peopoly
    'HALOT_ONE': 160.0, 'HALOT_MAGE': 230.0, 'GKTWO': 245.0, 'GK3ULTRA': 300.0,
    'FORM4': 210.0, 'PHENOM_FORGE': 350.0,
    # FDM
    'ENDER3': 250.0, 'PRUSA_MK3S': 210.0, 'BAMBU_A1MINI': 180.0, 'BAMBU_X1P1': 256.0,
}


def _apply_printer_preset(self, context):
    h = _PRINTER_HEIGHTS.get(self.printer_preset)
    if h is not None:
        self.max_print_height = h


def _apply_silicone_preset(self, context):
    d = _SILICONE_PRESETS.get(self.silicone_preset)
    if d is not None:
        self.silicone_density = d


def _apply_cast_preset(self, context):
    d = _CAST_PRESETS.get(self.cast_preset)
    if d is not None:
        self.cast_density = d


class MoldForgeProperties(bpy.types.PropertyGroup):
    # --- Mold type ------------------------------------------------------ #
    box_style: EnumProperty(
        name="Tipo de molde",
        description="Lo que MoldForge genera — las dos funciones realmente distintas",
        items=[
            ('POUR_BOX', "Caja de vertido de silicona",
             "Carcasa impresa en la que viertes silicona líquida — la silicona es el "
             "molde. Para el flujo de guante/contra-molde, ajusta un espacio fino y "
             "activa Llaves de piel de guante"),
            ('SOLID', "Molde impreso directo",
             "Las piezas impresas SON el molde — colar resina/cera/yeso directamente "
             "dentro. Forma: ajustada (menos material) o bloque (la más fácil de "
             "sujetar)"),
            ('TRAY', "Bandeja / vertido abierto",
             "Bandeja abierta de una parte para objetos PLANOS o en relieve — texto, "
             "logos, monedas, medallones. El objeto se asienta en el fondo y la parte "
             "superior queda abierta: insértalo y vierte silicona encima para un sello, "
             "talla el fondo para una colada directa, o imprime solo el marco para un "
             "objeto real. Sin corte, alas ni embudo"),
        ],
        default='POUR_BOX',
    )
    solid_shape: EnumProperty(
        name="Forma",
        description="Forma exterior de un molde impreso directo",
        items=[
            ('HUG', "Ajustado", "Las piezas siguen la forma del modelo — menos material"),
            ('BLOCK', "Bloque", "Bloque rectangular — el más fácil de sujetar y de "
             "mantener de pie"),
        ],
        default='HUG',
    )
    skin_keys: BoolProperty(
        name="Llaves de piel de guante",
        description="Flujo de guante / contra-molde: genera resaltes de registro en la "
                    "piel de silicona que asientan en cavidades de la carcasa rígida, "
                    "para que una piel fina no se desplace ni se hunda (ajusta el "
                    "espacio de silicona al grosor de la piel, p. ej. 3 mm)",
        default=False,
    )

    # --- Tray / open pour (flat & relief objects) ----------------------- #
    tray_mode: EnumProperty(
        name="Modo de bandeja",
        description="Qué hace la bandeja impresa con tu objeto",
        items=[
            ('EMBED', "Insertar → sello de silicona",
             "Fusiona el objeto en el fondo de la bandeja y vierte SILICONA encima. La "
             "silicona curada es un sello/molde negativo flexible en el que colas"),
            ('STAMP', "Sello desde SVG / Texto",
             "Crea un SELLO de tinta de silicona real desde un diseño: el arte (un "
             "archivo SVG, o el objeto Texto/Curva seleccionado) se graba en el fondo "
             "de la bandeja. Vierte silicona, cura, despega: la placa lleva el diseño "
             "en relieve y en espejo, así las impresiones del sello se leen "
             "correctamente - pégala a un bloque acrílico"),
            ('FRAME', "Solo marco (objeto real)",
             "Imprime solo la caja abierta en la huella del objeto — coloca tu objeto "
             "REAL dentro y vierte silicona alrededor"),
        ],
        default='EMBED',
    )
    tray_up: EnumProperty(
        name="Cara de captura",
        description="Hacia dónde apunta la cara detallada del objeto — el lado abierto "
                    "de vertido",
        items=[
            ('AUTO', "Auto", "Recuesta el objeto en su lado más plano, con el detalle "
             "hacia arriba"),
            ('Z', "+Z arriba", "La cara +Z del objeto es el lado de detalle / vertido"),
            ('X', "+X arriba", "La cara +X del objeto es el lado de detalle / vertido"),
            ('Y', "+Y arriba", "La cara +Y del objeto es el lado de detalle / vertido"),
        ],
        default='AUTO',
    )
    tray_outline: EnumProperty(
        name="Contorno",
        description="Forma de la bandeja alrededor del objeto",
        items=[
            ('RECT', "Rectangular", "Una bandeja rectangular alrededor de la huella del "
             "objeto — la más simple y resistente"),
            ('HUG', "Ajustada (redondeada)", "Las paredes siguen el contorno del objeto "
             "con esquinas redondeadas — usa menos silicona y plástico, sobre todo en "
             "formas redondas o irregulares"),
        ],
        default='RECT',
    )
    tray_wall: _dist("Pared de la bandeja", 2.5,
                     "Grosor de las paredes de la bandeja impresa", mn=0.4, soft=8.0)
    tray_floor: _dist("Fondo de la bandeja", 3.0,
                      "Grosor del fondo de la bandeja impresa", mn=0.4, soft=15.0)
    tray_margin: _dist("Borde", 6.0,
                       "Espacio entre el objeto y la pared de la bandeja — el borde de "
                       "silicona alrededor de tu objeto", mn=0.0, soft=30.0)
    tray_depth: _dist("Profundidad de vertido", 5.0,
                      "Cuánta silicona queda sobre el punto más alto del objeto "
                      "(el grosor de la placa)", mn=0.0, soft=40.0)

    # --- Sizes (absolute, scene units / mm) ----------------------------- #
    wall_thickness: _dist("Grosor de silicona / pared", 4.0,
                          "Grosor de silicona (espacio de vertido / piel de guante, o "
                          "la pared del molde directo)", mn=0.1)
    shell_wall: _dist("Pared de la carcasa impresa", 2.0,
                      "Grosor de la pared de la carcasa de vertido impresa", mn=0.4)
    sprue_radius: _dist("Radio de garganta", 10.0,
                        "Radio de la parte estrecha (BASE) del embudo — el agujero por "
                        "donde entra al molde. La boca (arriba) es este valor x Apertura "
                        "de boca. Si escribes más de lo que el molde admite, se ajusta "
                        "al máximo que cabe — salvo que Garganta sobredimensionada esté "
                        "activa, que usa exactamente lo que escribes. El panel muestra "
                        "el embudo que se construye",
                        mn=0.3, soft=60.0, update=_clamp_sprue_radius)
    big_throat: BoolProperty(
        name="Garganta sobredimensionada",
        description="Garganta totalmente manual: usa EXACTAMENTE el Radio de garganta "
                    "escrito, sin ningún límite de ajuste automático (normalmente se "
                    "limita a ≈30 % del semiancho del molde). Una garganta muy ancha "
                    "deja poca carcasa alrededor del agujero — el panel advierte, y el "
                    "resultado es tuyo",
        default=False,
        update=_clamp_sprue_radius,
    )
    funnel_height: _dist("Altura del embudo", 5.0,
                         "Cuánto sobresale el embudo de vertido por encima de la parte "
                         "superior del molde",
                         mn=1.0, soft=60.0)

    # --- Base ----------------------------------------------------------- #
    base_style: EnumProperty(
        name="Base",
        description="Cómo se remata la base del molde — las tres funciones realmente "
                    "distintas",
        items=[
            ('FLAT', "Plana (cerrada)",
             "Piso plano cerrado sobre el que se apoya el molde (añade una Brida de "
             "montaje para atornillarlo a una tabla)"),
            ('OPEN', "Base abierta",
             "Abierta en la base del máster — el máster se apoya en la placa de "
             "impresión y viertes desde arriba (añade una Placa de llave desmontable "
             "para una base con llave separada)"),
            ('FOLLOW', "Sigue el modelo",
             "La base sigue la forma del modelo (sin corte plano)"),
            ('LOCK', "Base de anclaje",
             "(Caja de vertido) Fusiona un zócalo base de dientes de sierra en el "
             "positivo de alta resolución; las carcasas impresas reciben un hueco a "
             "juego que abraza el zócalo con tolerancia de ajuste, de modo que se "
             "anclan a la base y no resbalan. La base se imprime abierta"),
        ],
        default='LOCK',
    )
    base_flange: BoolProperty(
        name="Brida de montaje",
        description="Añade un faldón atornillable hacia afuera alrededor de la base "
                    "plana — sujeta el molde a una tabla",
        default=True,
    )
    base_plate: BoolProperty(
        name="Placa de llave desmontable",
        description="Cierra la base abierta con una placa impresa separada: el modelo "
                    "se registra en una cavidad, y una lengüeta anular en el borde de "
                    "la carcasa cae en una ranura alrededor del cuello de la placa — "
                    "se autoalinea en todo el perímetro y sella el vertido",
        default=False,
    )
    suction_cup: BoolProperty(
        name="Formador de ventosa",
        description="También imprime un formador de ventosa (MF_Mold_Cup): una cúpula "
                    "lisa de alta resolución sobre una placa con cuatro patas que se "
                    "asientan sobre la base abierta (Base abierta o Base de anclaje - "
                    "el formador sigue la altura y el ancho reales de la base), con "
                    "pestañas que abrazan la pared exterior. Colada con el molde "
                    "invertido: llena, presiona el formador - el material cura alrededor "
                    "de la cúpula y deja una campana de ventosa en la base de la pieza. "
                    "Retira el formador tras el curado",
        default=False,
    )
    cup_diameter: _dist("Diámetro de la ventosa", 0.0,
                        "Diámetro de la cúpula del formador de ventosa. 0 = automático "
                        "(aprox. 70 % de la abertura de la base del modelo). Un tamaño "
                        "explícito se usa tal cual - puede ser más ancho que la abertura "
                        "de la pieza (la campana se trunca en la abertura) y solo se "
                        "limita a lo que cabe por la base de las carcasas. Con Base de "
                        "anclaje ese límite es el hueco de dientes: sube Margen de base "
                        "para una campana más ancha",
                        mn=0.0, soft=120.0)
    cup_depth: _dist("Profundidad de la ventosa", 8.0,
                     "Cuán profundo presiona la cúpula del formador en el vertido - la "
                     "profundidad de la campana de la ventosa terminada", mn=1.0, soft=30.0)
    cup_lock: EnumProperty(
        name="Fijación",
        description="Cómo se fija el formador a las carcasas - el vertido lo HACE "
                    "FLOTAR (flotabilidad), así que hay que sujetarlo",
        items=[
            ('PIN', "Anclaje con pasador (seguro para resina)",
             "No requiere flexión: el formador se desliza libre, luego un pasador de "
             "~2 mm (palillo, filamento de 1.75 mm, clavo) atraviesa el canal de cada "
             "gancho hacia la ranura de la carcasa - corte puro, seguro para resina "
             "frágil"),
            ('SNAP', "Anclaje a presión (filamento flexible)",
             "Perlas impresas encajan con un clic en ranuras de las carcasas - rápido "
             "y sin herramientas, para filamentos flexibles (PLA/PETG/ABS). NO para "
             "resina frágil"),
            ('BAND', "Bandas elásticas",
             "Los labios de los ganchos sujetan bandas elásticas tensadas sobre el "
             "formador - no se corta nada en las carcasas"),
        ],
        default='PIN',
    )
    fit_clearance: _dist("Holgura de ajuste", 0.2,
                         "Espacio POR CARA entre las piezas impresas que acoplan (la "
                         "ranura de la placa de llave vs. la lengüeta de la carcasa, y "
                         "la cavidad del modelo). Auméntala si tus impresiones quedan "
                         "demasiado apretadas para ensamblar", mn=0.0, soft=1.0)
    flange_width: _dist("Ancho de la brida", 6.0,
                        "Cuánto se extiende la brida de base más allá del molde")

    # --- Locking base (sawtooth plinth) --------------------------------- #
    lock_height: _dist("Altura de la base", 10.0,
                       "Qué tan alto es el zócalo base de dientes de sierra, debajo de "
                       "la base del modelo",
                       mn=1.0)
    lock_margin: _dist("Margen de base", 4.0,
                       "Cuánto se extiende el zócalo más allá de la huella del modelo "
                       "(un labio)",
                       mn=0.0)
    lock_teeth: IntProperty(
        name="Dientes de sierra",
        description="Cantidad de crestas de dientes de sierra en el costado del zócalo "
                    "(el zigzag antideslizante en el que encajan las carcasas)",
        default=3, min=1, max=12,
    )
    lock_tooth_depth: _dist("Profundidad del diente", 2.0,
                            "Cuánto sobresale cada cresta de dientes de sierra", mn=0.2)
    lock_tolerance: _dist("Tolerancia de anclaje", 0.2,
                          "Holgura entre el hueco de la carcasa impresa y la base, para "
                          "que las carcasas se deslicen y anclen sin trabarse (por cara)",
                          mn=0.0, soft=1.0)
    lock_unite: BoolProperty(
        name="Unir base con el modelo",
        description="Fusiona la base de dientes de sierra en el positivo para que "
                    "máster + base sean una sola pieza. Desmarca para mantener la base "
                    "como pieza MF_Mold_Base separada (se exporta con las carcasas) y "
                    "dejar tu modelo intacto - p. ej. para imprimir la base por "
                    "separado y fijar el máster a ella",
        default=True,
    )

    # --- Clamp wings ---------------------------------------------------- #
    wings: BoolProperty(
        name="Alas de sujeción",
        description="Añade bridas de sujeción de altura completa a lo largo de la(s) "
                    "línea(s) de partición, con agujeros para pernos, para apretar las "
                    "piezas entre sí. Abrazan el perfil del modelo de arriba abajo; con "
                    "3+ piezas radiales cada línea recibe un par de bridas atornilladas",
        default=True,
    )
    wing_width: _dist("Ancho de las alas", 8.0,
                      "Cuánto se extienden las bridas de sujeción más allá de los "
                      "costados")
    wing_keys: EnumProperty(
        name="Alineación de alas",
        description="Llaves de alineación en las caras de acople de las alas: una "
                    "llave en relieve en una mitad asienta en un hueco a juego en la "
                    "otra (crecido por la Holgura de ajuste), para que las mitades "
                    "atornilladas no se corten lateralmente. Colocadas entre los "
                    "agujeros de perno y dimensionadas según el labio del ala",
        items=[
            ('NONE', "Ninguna", "Sin llaves de alineación en las alas (solo los pernos "
             "alinean)"),
            ('CONE', "Cono", "Pasadores cónicos apuntados - se auto-centran, los más "
             "fáciles de asentar"),
            ('DOME', "Media esfera", "Resaltes de cúpula - acople y salida suaves"),
            ('FRUSTUM', "Medio cono", "Plataformas de cono truncado - robustas, "
             "resistentes al corte"),
        ],
        default='NONE',
    )
    wing_key_size: _dist("Tamaño de llave", 6.0,
                         "Diámetro de las llaves de alineación del ala (su huella en la "
                         "cara del ala)",
                         mn=1.0, soft=20.0)
    wing_key_height: _dist("Altura de llave", 0.0,
                           "Cuánto sobresalen las llaves de la cara de acople. 0 = "
                           "automático: proporcional al Tamaño de llave pero limitado "
                           "por el labio del ala para que el hueco nunca perfore el ala. "
                           "Ponlo más alto para llaves más altas - pasado el labio, el "
                           "hueco atraviesa el ala de acople como agujero (igual alinea "
                           "e imprime bien). Media esfera se limita a una semiesfera "
                           "completa para que siempre pueda ensamblarse",
                           mn=0.0, soft=12.0)
    wing_key_spacing: _dist("Separación de llaves", 40.0,
                            "Distancia entre llaves de alineación del ala a lo largo de "
                            "la línea de corte - las llaves se reparten uniformemente a "
                            "esta distancia (un molde alto recibe toda una fila), "
                            "esquivando los agujeros de perno",
                            mn=5.0, soft=120.0)
    bolt_diameter: _dist("Diámetro de perno", 3.0,
                         "Diámetro de los agujeros de perno de sujeción/brida", mn=0.5)
    bolt_auto: BoolProperty(
        name="Pernos automáticos",
        description="Coloca los agujeros de perno automáticamente según la altura de "
                    "la brida; desactivado por defecto - las alas se sujetan con clips "
                    "o bandas salvo que pidas agujeros aquí o fijes una cantidad por "
                    "lado",
        default=False,
    )
    bolt_count: IntProperty(
        name="Pernos / lado",
        description="Agujeros de perno exactos por ala de sujeción / línea de corte "
                    "cuando Pernos automáticos está apagado — 0 significa ningún "
                    "agujero de perno",
        default=0, min=0, max=10,
    )

    # --- Split / keys --------------------------------------------------- #
    split_axis: EnumProperty(
        name="Eje de corte",
        description="Dirección en que se separan las dos mitades",
        items=[
            ('AUTO', "Auto",
             "Elige el eje por el que el modelo se libera mejor (menos socavones), "
             "recurriendo a la huella más ancha cuando empatan"),
            ('X', "X", "Corte izquierda/derecha"),
            ('Y', "Y", "Corte adelante/atrás"),
        ],
        default='AUTO',
    )
    seat_floor: BoolProperty(
        name="Asentar modelo en el piso",
        description="Baja la cavidad hasta que el punto más bajo del modelo "
                    "apoye directo en el piso (sin grosor de silicona debajo), "
                    "así el modelo se asienta solo y no flota ni se mueve al "
                    "verter la silicona. La piel (MF_Skin) queda entera, sin "
                    "hueco en la parte de abajo. Solo Para Caja de Vertido con "
                    "base Plana",
        default=False,
    )
    stamp_svg: StringProperty(
        name="Archivo SVG", subtype='FILE_PATH', default="",
        description="Arte para el sello. Se usa cuando no hay objeto Texto/Curva "
                    "seleccionado; las formas deben ser trazos RELLENOS (convierte "
                    "primero los contornos a trazos en Inkscape)")
    stamp_width: FloatProperty(
        name="Ancho del sello", default=60.0, min=5.0, soft_max=200.0,
        description="El diseño se escala (uniformemente) a este ancho (mm)")
    stamp_relief: FloatProperty(
        name="Profundidad del relieve", default=2.0, min=0.5, soft_max=5.0,
        description="Cuánto sobresale el diseño de la cara del sello (mm). "
                    "1.5-2.5 es el punto ideal: menos profundo emborrona la tinta "
                    "del fondo, más profundo deja flojas las líneas finas")
    stamp_mirror: BoolProperty(
        name="Espejar diseño", default=False,
        description="Voltea el diseño izquierda-derecha. Déjalo APAGADO para un "
                    "sello de tinta normal (el vertido lo espeja una vez y el "
                    "sellado lo espeja de vuelta, así las impresiones se leen "
                    "correctamente). Actívalo solo si quieres que la CARA del "
                    "sello se lea correctamente")
    split_offset: FloatProperty(
        name="Desplazamiento de partición", default=0.0,
        description="Desplaza el plano de partición del centro a lo largo del eje de "
                    "corte, en mm (ajustado automáticamente para que ninguna mitad "
                    "desaparezca)")
    split_horizontal: BoolProperty(
        name="Corte horizontal",
        description="También corta la carcasa horizontalmente — para moldes XL: cada "
                    "pieza se imprime más corta, y la línea horizontal recibe un anillo "
                    "de brida atornillado en todo el perímetro. Dimensiona los agujeros "
                    "para tus insertos roscados con Diámetro de perno (insertos en el "
                    "labio inferior, tornillos desde arriba)",
        default=False,
    )
    split_z_offset: FloatProperty(
        name="Altura de la línea", default=0.0,
        description="Desplaza la línea horizontal arriba/abajo desde la mitad de la "
                    "altura, en mm (ajustado automáticamente para que ninguna pila "
                    "desaparezca)")
    dual_density: BoolProperty(
        name="Densidad dual (2 vertidos)", default=False,
        description="Kit de colada núcleo-firme / capa-blanda (solo Base de anclaje). "
                    "Añade Core_Master: tu modelo reducido HACIA ADENTRO por Pared "
                    "blanda, sin base + CRUZ DE HUECO tallada del mismo zócalo de "
                    "dientes que el positivo - los labios SON los dientes. Moldea en "
                    "una SEGUNDA corrida (esa corrida conserva este molde), coládala "
                    "FIRME, y luego vierte INVERTIDO: llena la base abierta con BLANDO "
                    "y encaja el núcleo - los labios se fijan en las ranuras y el "
                    "exceso sale por los cuadrantes abiertos; las capas se adhieren "
                    "al curar")
    core_wall: FloatProperty(
        name="Pared blanda", default=5.0, min=0.5, soft_max=20.0,
        description="Grosor de la capa exterior BLANDA (mm). El núcleo firme es "
                    "el modelo reducido HACIA ADENTRO exactamente este valor en "
                    "todas partes - un desplazamiento real, así la capa blanda "
                    "queda pareja por dentro de una curva, por fuera, arriba y "
                    "en los flancos por igual")
    anchor_plug: BoolProperty(
        name="Plug Vac-U-Lock", default=False,
        description="También imprime MF_Mold_Plug: el formador de Vac-U-Lock + "
                    "campana de ventosa, SIEMPRE a su tamaño original, en su "
                    "propia cruz de hueco (los labios de dientes se fijan en las "
                    "ranuras de las carcasas). Colada invertida como el núcleo "
                    "dual - llena la base, encaja - y la base del juguete curado "
                    "lleva el canal de sujeción dentro de una campana de ventosa; "
                    "si el molde es muy pequeño para el plug real, se omite con "
                    "una nota")
    printer_fit: BoolProperty(
        name="Ajustar a impresora",
        description="Divide todo lo que supere la altura de impresión de tu impresora "
                    "en piezas que quepan en la placa: carcasas en niveles apilados "
                    "atornillados, el positivo en secciones para encolar que se "
                    "autoalinean con pasadores impresos. Elige tu impresora o escribe "
                    "su altura abajo",
        default=False,
    )
    printer_preset: EnumProperty(
        name="Impresora",
        description="Elige tu impresora para rellenar su altura de impresión, o "
                    "Personalizado para escribirla tú. Verifica el número contra tu "
                    "máquina - las ediciones varían",
        items=[
            ('CUSTOM', "Personalizado", "Escribe tú la altura máxima de impresión "
                                  "(0 = Ajustar a impresora apagado)"),
            None,
            ('PHOTON_M7', "Anycubic Photon Mono M7 (230 mm)", "Anycubic Photon Mono M7"),
            ('PHOTON_M7PRO', "Anycubic Photon Mono M7 Pro (230 mm)",
             "Anycubic Photon Mono M7 Pro"),
            ('PHOTON_M7MAX', "Anycubic Photon Mono M7 Max (300 mm)",
             "Anycubic Photon Mono M7 Max"),
            ('PHOTON_M5S', "Anycubic Photon Mono M5s / M5s Pro (200 mm)",
             "Anycubic Photon Mono M5s / M5s Pro"),
            ('PHOTON_M3', "Anycubic Photon M3 (180 mm)", "Anycubic Photon M3"),
            ('PHOTON_M3PLUS', "Anycubic Photon M3 Plus (245 mm)", "Anycubic Photon M3 Plus"),
            ('PHOTON_M3MAX', "Anycubic Photon M3 Max (300 mm)", "Anycubic Photon M3 Max"),
            ('PHOTON_MONOX', "Anycubic Photon Mono X / X 6K (245 mm)",
             "Anycubic Photon Mono X / X 6K"),
            ('PHOTON_MONO4K', "Anycubic Photon Mono 4K (165 mm)", "Anycubic Photon Mono 4K"),
            None,
            ('MARS3', "Elegoo Mars 3 / 4 (175 mm)", "Elegoo Mars 3 / Mars 4"),
            ('MARS_ULTRA', "Elegoo Mars 4 Ultra / 5 Ultra (165 mm)",
             "Elegoo Mars 4 Ultra / Mars 5 Ultra"),
            ('SATURN2', "Elegoo Saturn 2 / 3 (250 mm)", "Elegoo Saturn 2 / Saturn 3"),
            ('SATURN3ULTRA', "Elegoo Saturn 3 Ultra (260 mm)", "Elegoo Saturn 3 Ultra"),
            ('SATURN4', "Elegoo Saturn 4 / 4 Ultra (220 mm)", "Elegoo Saturn 4 / Saturn 4 Ultra"),
            ('JUPITER', "Elegoo Jupiter / Jupiter SE (300 mm)", "Elegoo Jupiter / Jupiter SE"),
            None,
            ('PHROZEN_MINI8KS', "Phrozen Sonic Mini 8K S (180 mm)", "Phrozen Sonic Mini 8K S"),
            ('PHROZEN_MIGHTY', "Phrozen Sonic Mighty 8K / 12K (235 mm)",
             "Phrozen Sonic Mighty 8K / Mighty 12K"),
            ('PHROZEN_MEGA8KS', "Phrozen Sonic Mega 8K S (300 mm)", "Phrozen Sonic Mega 8K S"),
            ('PHROZEN_MEGA8K', "Phrozen Sonic Mega 8K (400 mm)", "Phrozen Sonic Mega 8K"),
            None,
            ('HALOT_ONE', "Creality Halot-One (160 mm)", "Creality Halot-One"),
            ('HALOT_MAGE', "Creality Halot-Mage / Mage Pro (230 mm)",
             "Creality Halot-Mage / Halot-Mage Pro / Mage S"),
            ('GKTWO', "UniFormation GKtwo (245 mm)", "UniFormation GKtwo"),
            ('GK3ULTRA', "UniFormation GK3 Ultra (300 mm)", "UniFormation GK3 Ultra"),
            ('FORM4', "Formlabs Form 4 (210 mm)", "Formlabs Form 4"),
            ('PHENOM_FORGE', "Peopoly Phenom Forge (350 mm)", "Peopoly Phenom Forge"),
            None,
            ('ENDER3', "Familia Ender-3 (250 mm)", "Creality Ender-3 / V2 / S1"),
            ('PRUSA_MK3S', "Prusa MK3S/MK4 (210 mm)", "Original Prusa i3 MK3S / MK4"),
            ('BAMBU_A1MINI', "Bambu A1 mini (180 mm)", "Bambu Lab A1 mini"),
            ('BAMBU_X1P1', "Bambu X1/P1 (256 mm)", "Serie Bambu Lab X1 / P1"),
        ],
        default='CUSTOM',
        update=_apply_printer_preset,
    )
    max_print_height: FloatProperty(
        name="Altura máx. de impresión", default=0.0, min=0.0, soft_max=400.0,
        description="Ajustar a impresora: la altura máxima de impresión de tu "
                    "impresora en mm (0 = apagado). Toda carcasa más alta se corta "
                    "en niveles apilados - cada uno con un anillo de línea "
                    "atornillado en todo el perímetro - y un positivo demasiado "
                    "alto en secciones para encolar que se autoalinean con "
                    "pasadores impresos (nada sobre su superficie de colada), para "
                    "que cada pieza quepa en la placa de impresión")
    fit_positive: BoolProperty(
        name="Cortar el positivo también", default=True,
        description="Ajustar a impresora también corta un positivo demasiado alto "
                    "en secciones que se autoalinean: pasadores impresos en cada "
                    "cara asientan en huecos de la siguiente (nunca se añade nada "
                    "a su superficie de colada). Desmarca para mantener siempre el "
                    "positivo entero - imprímelo inclinado o córtalo tú")
    support_clearance: FloatProperty(
        name="Altura de soportes", default=5.0, min=0.0, soft_max=20.0,
        description="Ajustar a impresora: altura con la que los soportes/raft del "
                    "slicer levantan la impresión de la placa (las impresiones de "
                    "resina rara vez asientan planas). Se reserva de la Altura máx. "
                    "de impresión, para que cada nivel siga cabiendo CON sus "
                    "soportes")
    contoured: BoolProperty(
        name="Partición contorneada",
        description="La superficie de partición sigue el perfil medio del modelo "
                    "(se autorregistra) en lugar de un plano plano; recurre a plana "
                    "si no puede producir mitades limpias",
        default=True,
    )
    key_count: IntProperty(
        name="Llaves de alineación",
        description="Elementos de registro en la cara de partición (usados con "
                    "partición plana + sin alas; una partición contorneada se "
                    "autorregistra)",
        default=2, min=0, max=4,
    )
    parts_count: IntProperty(
        name="Piezas del molde",
        description="En cuántas piezas se divide el molde. 2 es un corte normal en "
                    "dos partes; 3-4 lo divide en cuñas radiales alrededor del eje "
                    "vertical, para que un modelo con socavones en todos lados "
                    "pueda liberarse (cada cuña sale recta)",
        default=2, min=2, max=4,
    )
    registration: EnumProperty(
        name="Registro",
        description="Cómo son los elementos de alineación (partición plana)",
        items=[
            ('KEYS', "Llaves cónicas", "Pasadores cónicos que asientan en huecos"),
            ('TEETH', "Dientes enclavados", "Una fila almenada a lo largo de la "
             "línea de corte"),
        ],
        default='KEYS',
    )

    # --- Sprue / vents -------------------------------------------------- #
    sprue: BoolProperty(
        name="Embudo de vertido",
        description="Talla un embudo desde la parte superior hacia la cavidad para "
                    "verter",
        default=True,
    )
    sprue_flare: FloatProperty(
        name="Apertura del embudo", default=1.0, min=1.0, max=8.0,
        description="Ancho de boca como múltiplo del radio de la garganta — 1.0 es "
                    "un tubo recto (mejor cuando un cono ancho no cabe en la forma), "
                    "mayor es un embudo receptor más ancho (limitado automáticamente "
                    "al molde)",
    )
    funnel_style: EnumProperty(
        name="Estilo de embudo",
        description="Forma de la sección del embudo de vertido",
        items=[
            ('ROUND', "Redondo",
             "Embudo clásico de sección circular (garganta y boca redondas)"),
            ('SEMI_RECT', "Semirectangular",
             "Sección estadio (rectángulo redondeado) alargada a lo largo del eje "
             "con más sitio del molde — boca y canal anchos para figuras de copa "
             "estrecha. Usa Apertura de boca para el ancho y Largo semirectangular "
             "para la elongación"),
        ],
        default='SEMI_RECT',
    )
    sprue_rect_len: _dist("Largo semirectangular", 3.0,
                          "Relación largo/ancho de la sección del embudo "
                          "semirectangular (1.0 = círculo redondo)",
                          mn=1.0, soft=4.0)
    big_mouth: BoolProperty(
        name="Boca sobredimensionada",
        description="Boca totalmente manual: exactamente garganta x Apertura del "
                    "embudo, sin límite de ajuste automático (normalmente la boca se "
                    "limita a ≈45 % del semiancho del molde y se mantiene en el borde "
                    "del molde). Puede entonces sobresalir del molde — el panel "
                    "advierte. Úsala cuando un embudo receptor ancho no cabe de otra "
                    "forma en la forma",
        default=False,
    )
    sprue_count: IntProperty(
        name="Puntos de vertido",
        description="Cantidad de embudos de vertido (más ayuda a llenar figuras "
                    "altas)",
        default=1, min=1, max=4,
    )
    sprue_place: EnumProperty(
        name="Ubicación del embudo",
        description="Dónde se ubica el embudo de vertido en el modelo",
        items=[
            ('XY', "Centro XY", "Centra el embudo en la huella del modelo "
             "(tanto X como Y) — directo al medio"),
            ('X', "Centro X", "Centra en X; sigue el punto más alto del modelo a lo "
             "largo de Y"),
            ('Y', "Centro Y", "Centra en Y; sigue el punto más alto del modelo a lo "
             "largo de X"),
            ('TOP', "Punto más alto", "Pone el embudo en el punto más alto del "
             "modelo — la mejor ventilación para figuras altas, pero descentrado "
             "en un modelo inclinado"),
            ('MANUAL', "Manual X/Y", "Escribe tú la posición del embudo como "
             "desplazamiento X/Y desde el centro de la huella del modelo"),
        ],
        default='TOP',
    )
    sprue_x: FloatProperty(
        name="Embudo X", default=0.0,
        description="Desplazamiento X manual del embudo desde el centro de la huella "
                    "del modelo, en mm (0 = centro); se usa cuando Ubicación del "
                    "embudo es Manual. Limitado a la huella para que el embudo quede "
                    "sobre el modelo")
    sprue_y: FloatProperty(
        name="Embudo Y", default=0.0,
        description="Desplazamiento Y manual del embudo desde el centro de la huella "
                    "del modelo, en mm (0 = centro); se usa cuando Ubicación del "
                    "embudo es Manual. Limitado a la huella para que el embudo quede "
                    "sobre el modelo")
    vent_place: EnumProperty(
        name="Ubicación de respiraderos",
        description="Dónde van los respiraderos de aire",
        items=[
            ('AUTO', "Auto (puntos altos)",
             "Perfora respiraderos en los puntos más altos del modelo, espaciados - "
             "donde el aire realmente se atrapa"),
            ('MARKERS', "Manual (marcadores)",
             "Perfora UN respiradero en cada Marcador de respiradero colocado. "
             "Ajusta el cursor 3D a la superficie (Shift+Clic derecho), pulsa "
             "Añadir marcador de respiradero, y luego mueve/duplica/elimina los "
             "marcadores libremente"),
        ],
        default='AUTO',
    )
    vent_count: IntProperty(
        name="Respiraderos de aire",
        description="Canales finos desde los puntos altos de la cavidad hacia el "
                    "exterior",
        default=0, min=0, max=8,
    )
    vent_radius: _dist("Radio del respiradero", 3.01,
                       "Radio de cada canal de respiradero; si escribes más de lo que "
                       "el molde admite, vuelve al máximo que cabe",
                       mn=0.2, soft=4.0, update=_clamp_vent_radius)

    # --- Mesh prep ------------------------------------------------------ #
    heal: BoolProperty(
        name="Reparar malla",
        description="Primero fusiona duplicados, descarta geometría suelta y "
                    "recalcula normales",
        default=True,
    )
    decimate: BoolProperty(name="Decimar", default=False)
    decimate_ratio: FloatProperty(name="Proporción", default=0.5, min=0.1, max=1.0, subtype='FACTOR')
    voxel_safe: BoolProperty(
        name="Remesh seguro",
        description="Remesh voxel de todo el modelo primero — para mallas "
                    "desordenadas o no manifold",
        default=True,
    )
    voxel_size: _dist("Vóxel de remesh", 1.0,
                      "Tamaño de vóxel para Remesh seguro (menor = más fino, más "
                      "lento)", mn=0.05)

    # --- Materials (for the weight estimate) ---------------------------- #
    silicone_preset: EnumProperty(
        name="Material del molde",
        description="Elige un material de molde común para rellenar su densidad, o "
                    "Personalizado para escribir la tuya",
        items=[
            ('CUSTOM', "Personalizado", "Escribe tú la densidad"),
            ('DRAGONSKIN', "Dragon Skin", "Smooth-On Dragon Skin (platino) ≈ 1.07"),
            ('MOLDSTAR', "Mold Star", "Smooth-On Mold Star (platino) ≈ 1.18"),
            ('OOMOO', "Oomoo", "Smooth-On Oomoo (curado por estaño) ≈ 1.42"),
            ('ECOFLEX', "Ecoflex", "Smooth-On Ecoflex (platino suave) ≈ 1.07"),
            ('MOLDMAX', "Mold Max", "Smooth-On Mold Max (curado por estaño) ≈ 1.42"),
            ('PLATSIL', "RTV de platino", "RTV genérico de curado por platino ≈ 1.12"),
        ],
        default='CUSTOM',
        update=_apply_silicone_preset,
    )
    cast_preset: EnumProperty(
        name="Material de colada",
        description="Elige un material de colada común para rellenar su densidad, o "
                    "Personalizado para escribir la tuya",
        items=[
            ('CUSTOM', "Personalizado", "Escribe tú la densidad"),
            ('SILICONE', "Silicona", "Silicona de colada ≈ 1.10 (platino ~1.07, "
             "estaño ~1.2)"),
            ('URETHANE', "Resina de uretano", "Resina de uretano Smooth-Cast ≈ 1.05"),
            ('EPOXY', "Resina epóxica", "Resina epóxica de colada genérica ≈ 1.15"),
            ('POLYESTER', "Resina de poliéster", "Resina de poliéster de colada ≈ 1.10"),
            ('PLASTER', "Yeso", "Yeso de París / gypsum ≈ 1.80"),
            ('WAX', "Cera", "Cera de colada / velas ≈ 0.90"),
            ('CONCRETE', "Concreto", "Cemento / GFRC ≈ 2.40"),
        ],
        default='CUSTOM',
        update=_apply_cast_preset,
    )
    silicone_density: FloatProperty(
        name="Silicona g/ml", default=1.15, min=0.1, max=5.0,
        description="Densidad de la silicona de vertido / material del molde sólido "
                    "(silicona RTV ≈ 1.1–1.2)")
    cast_density: FloatProperty(
        name="Colada g/ml", default=1.10, min=0.1, max=5.0,
        description="Densidad de lo que colas (resina ≈ 1.1, yeso ≈ 1.8, cera ≈ 0.9)")
    plastic_density: FloatProperty(
        name="Impresión g/ml", default=1.24, min=0.1, max=5.0,
        description="Densidad del plástico impreso (PLA ≈ 1.24, PETG ≈ 1.27)")

    # --- Export --------------------------------------------------------- #
    export_after: BoolProperty(
        name="Exportar tras generar", default=False,
        description="Tras generar, escribe cada impresión como STL en la Carpeta de "
                    "exportación: las carcasas del molde, las piezas de "
                    "base/formador y el positivo")
    export_dir: StringProperty(name="Carpeta de exportación", subtype='DIR_PATH', default="//")

    # --- Results (read-only display) ------------------------------------ #
    last_cavity_volume: FloatProperty(name="Volumen de la cavidad", default=0.0)
    last_silicone_volume: FloatProperty(name="Volumen de silicona", default=0.0)
    last_plastic_volume: FloatProperty(name="Volumen de plástico de la caja", default=0.0)
