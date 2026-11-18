"""Tema "Plata".

Un chasis de luz fría: la ventana es gris plata azulado y los paneles flotan
sobre ella casi blancos, con la tinta en un pizarra profundo en lugar de negro.
Lo principal (reproducir, el botón sólido de la banda) va en aguamarina honda;
el oro aparece solo como acento pequeño (la marca de la vista activa) y el cian
claro solo como halo (lo que suena). Cada color tiene un único trabajo.

El color de la música sigue mandando: la cabecera de cada lista es una banda
pastel del tono de su carátula y la "tinta" de la pista (ese mismo tono, hondo)
tiñe el progreso, la fila actual y la letra.

Tipografía: Bahnschrift, la DIN variable de Windows. Su corte ligero y
semicondensado hace de titular; el regular y el seminegro, de texto. Una sola
familia con anchos distintos: la personalidad la da el ancho, no una segunda
fuente. Los valores de este archivo son los únicos que deben usar los
componentes: nada de colores sueltos en cada pantalla.

Variante nocturna: la misma disciplina sobre pizarra y carbón fríos. La
tinta pasa a ser clara, la aguamarina sigue siendo lo sólido, el oro sigue
marcando la vista activa y el cian, lo que suena; la banda de cada lista toma
el tono de su carátula, pero hondo, con el texto claro encima.

El tema se elige con ``use_scheme`` ("light" o "dark"); en modo "system"
sigue a Windows (``AppsUseLightTheme``). Al cambiar, los nombres de este
módulo pasan a valer los del otro esquema: quien lee ``theme.PANEL`` al
pintar ya ve el nuevo color, y quien lo guardó antes tiene un ``retheme()``
al que llama la ventana (ver ``MainWindow._apply_theme``).
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

# -- color -------------------------------------------------------------------
LIGHT = {
    "FONDO": "#c3ced2",        # la ventana, visible entre los paneles: plata azulada
    "PANEL": "#f3f7f7",        # superficie de los paneles
    "RELIEVE": "#e4ecee",      # hover, campos
    "RELIEVE_2": "#d5e2e4",    # seleccionado, pulsado
    "LINEA": "#cddadd",        # filetes y bordes
    "LINEA_CLARA": "#b4c6ca",  # borde de lo que flota sobre un panel
    "BANDA_NEUTRA": "#d3e6e4", # banda de cabecera cuando no hay carátula: menta plateada
    "PAPEL": "#18262e",        # tinta principal (14,4:1 sobre PANEL)
    "HUMO": "#4a5d66",         # texto secundario (6,4:1 sobre PANEL)
    "TENUE": "#52666e",        # terciario: cabeceras, notas, desactivado (5,6:1 sobre PANEL, 4,5:1 sobre RELIEVE_2)
    "PAPEL_SUAVE": "#2d3e47",  # texto largo de lectura continua
    "CREMA": "#0b7470",        # tinta por defecto (sin carátula): aguamarina honda
    "AVISO": "#b8403a",        # errores y bloqueos
    "SOLIDO": "#14545a",       # superficies sólidas: reproducir, botón principal
    "SOLIDO_HOVER": "#1b6a71", # su hover
    "SOBRE_SOLIDO": "#f3f7f7", # texto e iconos sobre lo sólido y sobre los velos
    "ORO": "#e0ac2e",          # acento: marca de la vista activa
    "HALO": "#45d3d8",         # halo de lo que suena
    "VELO": QColor(16, 34, 42, 150),   # velo sobre una carátula para pintar encima
    # Texto sobre la banda de la cabecera (pastel claro): la tinta pizarra.
    "BANDA_TINTA": (24, 38, 46),
    "BANDA_CAMPO": "rgba(255, 255, 255, 0.62)",   # campo de búsqueda sobre la banda
    "AROS": (255, 255, 255),   # anillos de luz de la banda
}

DARK = {
    "FONDO": "#11181c",        # carbón frío entre los paneles
    "PANEL": "#182228",        # gris pizarra oscuro
    "RELIEVE": "#222f36",
    "RELIEVE_2": "#2b3a42",
    "LINEA": "#27343b",
    "LINEA_CLARA": "#3a4b54",
    "BANDA_NEUTRA": "#1c3533", # menta honda
    "PAPEL": "#f0f4f6",        # 14,6:1 sobre PANEL
    "HUMO": "#a3b4bc",         # 7,6:1 sobre PANEL
    "TENUE": "#94a8b1",        # 6,5:1 sobre PANEL, 4,7:1 sobre RELIEVE_2
    "PAPEL_SUAVE": "#d0dade",
    "CREMA": "#5fcac3",        # aguamarina clara: la tinta sin carátula
    "AVISO": "#f08a82",
    "SOLIDO": "#15625f",       # aguamarina profunda para lo sólido
    "SOLIDO_HOVER": "#1a6f6b",
    "SOBRE_SOLIDO": "#f3f7f7",
    "ORO": "#e0ac2e",
    "HALO": "#45d3d8",
    "VELO": QColor(6, 12, 16, 165),
    "BANDA_TINTA": (240, 244, 246),
    "BANDA_CAMPO": "rgba(10, 18, 22, 0.45)",
    "AROS": (210, 240, 242),
}

SCHEMES = {"light": LIGHT, "dark": DARK}
_scheme = "light"

FONDO = PANEL = RELIEVE = RELIEVE_2 = LINEA = LINEA_CLARA = BANDA_NEUTRA = ""
PAPEL = HUMO = TENUE = PAPEL_SUAVE = CREMA = AVISO = SOLIDO = SOLIDO_HOVER = ""
SOBRE_SOLIDO = ORO = HALO = ""
VELO = QColor()
BANDA_TINTA = (0, 0, 0)
BANDA_CAMPO = ""
AROS = (255, 255, 255)
BLANCO = TINTA_OSCURA = GRAFITO = CHASIS = BG = RAISED = BORDER = TEXT = MUTED = ACCENT = ""
QSS = ""


# Al cambiar de esquema, un color guardado se traduce por su papel: el primero
# de esta lista cuyo valor coincida (los de texto antes que los de superficie).
_ROLE_ORDER = ("PAPEL", "HUMO", "TENUE", "PAPEL_SUAVE", "AVISO", "CREMA", "PANEL", "RELIEVE", "RELIEVE_2",
               "LINEA", "LINEA_CLARA", "FONDO", "SOLIDO", "SOLIDO_HOVER", "SOBRE_SOLIDO", "BANDA_NEUTRA",
               "ORO", "HALO")


def translate(color: "str | QColor", previous: str) -> QColor:
    """El color del esquema en uso que hace el papel que ``color`` hacía en
    el esquema ``previous``. Uno que no es de la paleta (la tinta de una
    carátula) se devuelve igual. Conserva la transparencia."""
    value = QColor(color)
    old = SCHEMES.get(previous)
    if old is None or previous == _scheme:
        return value
    for role in _ROLE_ORDER:
        if QColor(old[role]).rgb() == value.rgb():
            new = QColor(globals()[role])
            new.setAlpha(value.alpha())
            return new
    return value


def scheme() -> str:
    """"light" o "dark": el esquema en uso."""
    return _scheme


def is_dark() -> bool:
    return _scheme == "dark"


def _set_globals(values: dict) -> None:
    g = globals()
    for name, value in values.items():
        g[name] = QColor(value) if isinstance(value, QColor) else value
    # Nombres antiguos con su papel de siempre: "blanco" era el hover de lo
    # sólido y "tinta oscura", el texto encima.
    g["BLANCO"] = values["SOLIDO_HOVER"]
    g["TINTA_OSCURA"] = values["SOBRE_SOLIDO"]
    # Alias que usa el resto del código.
    g["GRAFITO"], g["CHASIS"] = values["FONDO"], values["PANEL"]
    g["BG"], g["RAISED"], g["BORDER"] = values["FONDO"], values["RELIEVE"], values["LINEA"]
    g["TEXT"], g["MUTED"], g["ACCENT"] = values["PAPEL"], values["HUMO"], values["CREMA"]


def use_scheme(name: str) -> bool:
    """Cambia los colores del módulo al esquema ``name``. Devuelve si cambió."""
    global _scheme, QSS
    name = "dark" if name == "dark" else "light"
    changed = name != _scheme or not QSS
    _scheme = name
    _set_globals(SCHEMES[name])
    QSS = build_qss()
    return changed


# -- tema del sistema --------------------------------------------------------
MODES = ("system", "light", "dark")
_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
WM_SETTINGCHANGE = 0x001A


def normalize_mode(mode: object) -> str:
    return mode if mode in MODES else "system"


def _read_apps_use_light(registry=None) -> bool:
    """``AppsUseLightTheme`` del usuario (1 = claro). Sin el valor, claro."""
    if registry is None:
        if sys.platform != "win32":
            return True
        import winreg as registry
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, _PERSONALIZE) as key:
            value, _kind = registry.QueryValueEx(key, "AppsUseLightTheme")
        return bool(value)
    except OSError:
        return True


def system_prefers_dark() -> bool:
    return not _read_apps_use_light()


def resolve_mode(mode: str) -> str:
    """El esquema que toca para el ajuste ``mode`` ("system", "light", "dark")."""
    mode = normalize_mode(mode)
    if mode == "system":
        return "dark" if system_prefers_dark() else "light"
    return mode


def is_color_set_change(lparam: int) -> bool:
    """¿Este ``WM_SETTINGCHANGE`` avisa de un cambio de tema claro/oscuro?

    Windows lo anuncia con el texto "ImmersiveColorSet" en ``lParam``.
    """
    if not lparam or sys.platform != "win32":
        return False
    try:
        import ctypes

        # ``lParam`` es NULL o un texto terminado en cero (así lo documenta Windows).
        return ctypes.wstring_at(lparam) == "ImmersiveColorSet"
    except (OSError, ValueError):
        return False

# -- forma y ritmo -----------------------------------------------------------
R_PANEL = 12     # paneles
R_CARD = 10      # tarjetas del inicio
R_CONTROL = 8    # filas, campos, botones rectos
R_COVER = 6      # carátulas grandes
R_THUMB = 5      # carátulas de fila
GUTTER = 8       # separación entre paneles (rejilla de 4/8)

MIN_TEXT_CONTRAST = 4.5  # WCAG AA para texto normal


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(color: "str | QColor") -> float:
    """Luminancia relativa de WCAG 2 (0 = negro, 1 = blanco)."""
    c = QColor(color)
    return 0.2126 * _linear(c.redF()) + 0.7152 * _linear(c.greenF()) + 0.0722 * _linear(c.blueF())


def composite(fg: "str | QColor", bg: "str | QColor") -> QColor:
    """El color que se ve al pintar ``fg`` (con su transparencia) sobre ``bg`` opaco."""
    top, base = QColor(fg), QColor(bg)
    a = top.alphaF()
    return QColor.fromRgbF(
        top.redF() * a + base.redF() * (1 - a),
        top.greenF() * a + base.greenF() * (1 - a),
        top.blueF() * a + base.blueF() * (1 - a),
    )


def contrast_ratio(fg: "str | QColor", bg: "str | QColor") -> float:
    """Contraste WCAG entre un texto (que puede ser translúcido) y su fondo opaco."""
    a = relative_luminance(composite(fg, bg))
    b = relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


UI_FAMILIES = ["Bahnschrift", "Segoe UI Variable Text", "Segoe UI"]
DISPLAY_FAMILIES = ["Bahnschrift Light SemiCondensed", "Bahnschrift", "Segoe UI Variable Display"]
UI_FONT = '"Bahnschrift", "Segoe UI Variable Text", "Segoe UI", sans-serif'
DISPLAY_FONT = '"Bahnschrift Light SemiCondensed", "Bahnschrift", sans-serif'


def ui_font(px: int, weight: QFont.Weight = QFont.Weight.Normal, tabular: bool = False) -> QFont:
    """Fuente de interfaz para lo que se pinta a mano (fuera del alcance del QSS)."""
    font = QFont()
    font.setFamilies(UI_FAMILIES)
    font.setPixelSize(px)
    font.setWeight(weight)
    if tabular:
        # Cifras tabulares: los tiempos no bailan al cambiar de dígito.
        try:
            font.setFeature(QFont.Tag("tnum"), 1)
        except (AttributeError, TypeError):
            pass
    return font


def display_font(px: int) -> QFont:
    font = QFont()
    font.setFamilies(DISPLAY_FAMILIES)
    font.setPixelSize(px)
    return font


# El menú de la app cuando no cabe en la pantalla: las mismas filas, más
# apretadas. Va como hoja propia del menú (manda sobre la de la aplicación) y
# solo toca medidas, así que vale igual con el tema claro y con el oscuro.
MENU_COMPACT_QSS = (
    "QMenu#appMenu::item { padding: 4px 34px 4px 12px; } "
    "#menuSection { padding-top: 6px; padding-bottom: 2px; } "
    '#menuSection[first="true"] { padding-top: 0px; }'
)


def build_qss() -> str:
    """La hoja de estilos con los colores del esquema en uso."""
    ink = ", ".join(str(c) for c in BANDA_TINTA)
    return f"""
* {{ font-family: {UI_FONT}; font-size: 13px; color: {PAPEL}; outline: none; }}
QMainWindow, #root {{ background: {FONDO}; }}
QDialog {{ background: {PANEL}; }}
QFrame#panel {{ background: {PANEL}; border-radius: {R_PANEL}px; }}
QSplitter::handle {{ background: transparent; }}

QMenu {{ background: {PANEL}; border: 1px solid {LINEA_CLARA}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; color: {PAPEL}; }}
QMenu::item:selected {{ background: {RELIEVE}; }}
QMenu::item:disabled {{ color: {TENUE}; }}
QMenu::separator {{ height: 1px; background: {LINEA}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 10px; }}
/* El menú de la app y sus submenús: filas con más aire, todas de la misma
   altura (con casilla o sin ella), y rótulos de grupo con hueco encima. En
   una pantalla baja el menú se aprieta (MENU_COMPACT_QSS) antes que partirse en columnas. */
QMenu#appMenu {{ padding: 8px 6px; }}
QMenu#appMenu::item {{ padding: 7px 34px 7px 12px; min-height: 17px; }}
QMenu#appMenu::separator {{ margin: 7px 10px; }}
#menuSectionBox {{ background: transparent; }}
#menuSection {{ color: {TENUE}; font-size: 12px; font-weight: 600; background: transparent; padding: 11px 0 4px 0; }}
#menuSection[first="true"] {{ padding-top: 2px; }}
#menuNote {{ color: {HUMO}; font-size: 12px; background: transparent; }}

#sidebar {{ background: transparent; border: none; padding: 8px; }}
#sidebar::item {{ padding: 6px 8px; border-radius: {R_CONTROL}px; margin: 1px 0; color: {HUMO}; }}
#sidebar::item:hover {{ background: {RELIEVE}; color: {PAPEL}; }}
#sidebar::item:selected {{ background: {RELIEVE_2}; color: {PAPEL}; }}
#sidebar::item:disabled {{ color: {TENUE}; font-size: 12px; padding: 18px 8px 4px 8px; background: transparent; }}
#sidebarFooter {{ border-top: 1px solid {LINEA}; }}

#bandTitle {{ font-family: {DISPLAY_FONT}; font-size: 46px; font-weight: 400; color: {PAPEL}; }}
#bandTitle[compact="true"] {{ font-size: 32px; }}
#bandText {{ color: rgba({ink}, 0.80); font-size: 14px; }}
#bandFact {{ color: rgba({ink}, 0.88); font-size: 13px; }}
QLineEdit#bandSearch {{ background: {BANDA_CAMPO}; border: 1px solid rgba({ink}, 0.20); border-radius: {R_CONTROL}px; padding: 9px 12px; font-size: 14px; }}
QLineEdit#bandSearch:hover {{ border-color: rgba({ink}, 0.38); }}
QLineEdit#bandSearch:focus {{ border: 2px solid {PAPEL}; padding: 8px 11px; }}

#homeTitle {{ font-family: {DISPLAY_FONT}; font-size: 46px; font-weight: 400; color: {PAPEL}; }}
#homeFact {{ color: {HUMO}; font-size: 13px; }}
#shelfTitle {{ font-size: 17px; font-weight: 600; color: {PAPEL}; }}
#shelfNote {{ color: {HUMO}; font-size: 13px; }}
#homeStatus {{ color: {HUMO}; font-size: 13px; }}

#nowTitle {{ font-size: 19px; font-weight: 600; color: {PAPEL}; }}
#nowHeading {{ font-size: 14px; font-weight: 600; color: {PAPEL}; }}
#nowBio {{ color: {PAPEL_SUAVE}; font-size: 13px; }}
#nowFactLabel {{ color: {HUMO}; font-size: 13px; }}
#nowFactValue {{ color: {PAPEL}; font-size: 13px; }}

#mutedLabel {{ color: {HUMO}; }}
#sectionTitle {{ font-weight: 600; font-size: 14px; }}
#panelTitle {{ font-weight: 600; font-size: 13px; color: {HUMO}; }}
#emptyTitle {{ font-weight: 600; font-size: 15px; }}
#emptyHint {{ color: {HUMO}; font-size: 13px; }}
#bannerText {{ color: {PAPEL}; }}
QFrame#banner {{ background: {RELIEVE}; border: 1px solid {LINEA}; border-radius: 10px; }}

QPushButton {{ background: {RELIEVE}; border: 1px solid {LINEA}; border-radius: {R_CONTROL}px; padding: 8px 14px; }}
QPushButton:hover {{ background: {RELIEVE_2}; }}
QPushButton:pressed {{ background: {LINEA}; }}
QPushButton:focus {{ border-color: {PAPEL}; }}
QPushButton:disabled {{ color: {TENUE}; }}

QLineEdit, QPlainTextEdit, QComboBox {{ background: {PANEL}; border: 1px solid {LINEA}; border-radius: {R_CONTROL}px; padding: 8px 10px; selection-background-color: {SOLIDO}; selection-color: {SOBRE_SOLIDO}; }}
QLineEdit:hover, QPlainTextEdit:hover {{ border-color: {TENUE}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {PAPEL}; }}

QTableView {{ background: transparent; border: none; gridline-color: transparent; selection-background-color: transparent; selection-color: {PAPEL}; }}
QTableView::item {{ border: none; background: transparent; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {TENUE}; border: none; border-bottom: 1px solid {LINEA}; padding: 8px 10px; font-size: 12px; }}

#lyricsList {{ background: transparent; border: none; padding: 4px 8px 12px 8px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINEA_CLARA}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {HUMO}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {LINEA}; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 9px 16px; color: {HUMO}; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {PAPEL}; }}
QTabBar::tab:selected {{ color: {PAPEL}; border-bottom-color: {ORO}; }}
QTabBar::tab:focus {{ border-bottom-color: {PAPEL}; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QToolTip {{ background: {PAPEL}; color: {PANEL}; border: none; padding: 5px 9px; }}
"""


def palette() -> QPalette:
    """Paleta del esquema en uso para lo que el QSS no alcanza (casillas, iconos, texto de sistema)."""
    pal = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, PANEL),
        (QPalette.ColorRole.WindowText, PAPEL),
        (QPalette.ColorRole.Base, PANEL),
        (QPalette.ColorRole.AlternateBase, PANEL),
        (QPalette.ColorRole.Text, PAPEL),
        (QPalette.ColorRole.Button, RELIEVE),
        (QPalette.ColorRole.ButtonText, PAPEL),
        (QPalette.ColorRole.ToolTipBase, PAPEL),
        (QPalette.ColorRole.ToolTipText, PANEL),
        (QPalette.ColorRole.Highlight, SOLIDO),
        (QPalette.ColorRole.HighlightedText, SOBRE_SOLIDO),
        (QPalette.ColorRole.PlaceholderText, TENUE),
        (QPalette.ColorRole.Link, CREMA),
    ):
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(TENUE))
    return pal


def tint_title_bar(widget) -> None:
    """Barra de título de Windows del color del fondo, en el modo (claro u
    oscuro) del esquema: la ventana es una sola pieza.

    Solo Windows 11 acepta el color; en Windows 10 queda la barra normal del
    modo y en cualquier otro sistema no se hace nada.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1 if is_dark() else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))   # modo claro u oscuro

        def colorref(name: str):
            color = QColor(name)
            return ctypes.c_int(color.red() | (color.green() << 8) | (color.blue() << 16))

        caption, text = colorref(FONDO), colorref(PAPEL)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))  # color de la barra
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))        # color del título
    except Exception:  # noqa: BLE001 - detalle cosmético: nunca debe impedir abrir la ventana
        pass


# Glifos de "Segoe Fluent Icons" / "Segoe MDL2 Assets" (mismos códigos) con
# texto de respaldo por si ninguna de las dos fuentes existe.
GLYPHS = {
    "play": ("\uE768", "▶"),
    "pause": ("\uE769", "❚❚"),
    "previous": ("\uE892", "⏮"),
    "next": ("\uE893", "⏭"),
    "shuffle": ("\uE8B1", "⤮"),
    "repeat_all": ("\uE8EE", "⟳"),
    "repeat_one": ("\uE8ED", "⟳1"),
    "volume": ("\uE995", "◕"),
    "volume_mid": ("\uE994", "◑"),
    "volume_low": ("\uE993", "◔"),
    "mute": ("\uE74F", "×"),
    "search": ("\uE721", "⌕"),
    "queue": ("\uE8FD", "≡"),
    "more": ("\uE712", "⋯"),
    "sync": ("\uE895", "⟳"),
    "close": ("\uE711", "×"),
    "music": ("\uEC4F", "♪"),
    "check": ("\uE73E", "✓"),
    "home": ("\uE80F", "⌂"),
    "back": ("\uE72B", "←"),
    "history": ("\uE81C", "↺"),
    "add": ("\uE710", "+"),
    "pane_close": ("\uE89F", "«"),
    "pane_open": ("\uE8A0", "»"),
    "expand": ("\uE740", "⤢"),
    "chevron_down": ("\uE70D", "▾"),
    "chevron_up": ("\uE70E", "▴"),
    "save": ("\uE74E", "↓"),
    "mini": ("\uEE49", "⧉"),
    "window": ("\uE8A7", "↗"),
    "sleep": ("\uE708", "☾"),
    "heart": ("\uEB51", "♡"),
    "heart_fill": ("\uEB52", "♥"),
    "folder": ("\uE8B7", "▤"),
}

_icon_family: str | None = None
_icon_checked = False


def icon_font(size: int = 14) -> QFont | None:
    global _icon_family, _icon_checked
    if not _icon_checked:
        _icon_checked = True
        families = set(QFontDatabase.families())
        for candidate in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
            if candidate in families:
                _icon_family = candidate
                break
    if _icon_family is None:
        return None
    font = QFont(_icon_family)
    font.setPixelSize(size)
    return font


def glyph(name: str) -> str:
    icon, fallback = GLYPHS[name]
    return icon if icon_font() is not None else fallback


use_scheme("light")
