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
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

# -- color -------------------------------------------------------------------
FONDO = "#c3ced2"        # la ventana, visible entre los paneles: plata azulada
PANEL = "#f3f7f7"        # superficie de los paneles
RELIEVE = "#e4ecee"      # hover, campos
RELIEVE_2 = "#d5e2e4"    # seleccionado, pulsado
LINEA = "#cddadd"        # filetes y bordes
LINEA_CLARA = "#b4c6ca"  # borde de lo que flota sobre un panel
BANDA_NEUTRA = "#d3e6e4" # banda de cabecera cuando no hay carátula: menta plateada
PAPEL = "#18262e"        # tinta principal (14,4:1 sobre PANEL)
HUMO = "#4a5d66"         # texto secundario (6,4:1 sobre PANEL)
TENUE = "#5a6e76"        # terciario: cabeceras, notas (5,0:1 sobre PANEL)
PAPEL_SUAVE = "#2d3e47"  # texto largo de lectura continua
CREMA = "#0b7470"        # tinta por defecto (sin carátula): aguamarina honda
AVISO = "#b8403a"        # errores y bloqueos
SOLIDO = "#14545a"       # superficies sólidas: reproducir, botón principal
SOLIDO_HOVER = "#1b6a71" # su hover
SOBRE_SOLIDO = "#f3f7f7" # texto e iconos sobre lo sólido y sobre los velos
ORO = "#e0ac2e"          # acento: marca de la vista activa
HALO = "#45d3d8"         # halo de lo que suena
VELO = QColor(16, 34, 42, 150)    # velo sobre una carátula para pintar encima

# Nombres antiguos con su papel de siempre: "blanco" era el hover de lo sólido
# y "tinta oscura", el texto encima.
BLANCO = SOLIDO_HOVER
TINTA_OSCURA = SOBRE_SOLIDO

# Alias que usa el resto del código.
GRAFITO, CHASIS = FONDO, PANEL
BG, RAISED, BORDER, TEXT, MUTED, ACCENT = FONDO, RELIEVE, LINEA, PAPEL, HUMO, CREMA

# -- forma y ritmo -----------------------------------------------------------
R_PANEL = 12     # paneles
R_CARD = 10      # tarjetas del inicio
R_CONTROL = 8    # filas, campos, botones rectos
R_COVER = 6      # carátulas grandes
R_THUMB = 5      # carátulas de fila
GUTTER = 8       # separación entre paneles (rejilla de 4/8)

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


QSS = f"""
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

#sidebar {{ background: transparent; border: none; padding: 8px; }}
#sidebar::item {{ padding: 6px 8px; border-radius: {R_CONTROL}px; margin: 1px 0; color: {HUMO}; }}
#sidebar::item:hover {{ background: {RELIEVE}; color: {PAPEL}; }}
#sidebar::item:selected {{ background: {RELIEVE_2}; color: {PAPEL}; }}
#sidebar::item:disabled {{ color: {TENUE}; font-size: 12px; padding: 18px 8px 4px 8px; background: transparent; }}
#sidebarFooter {{ border-top: 1px solid {LINEA}; }}

#bandTitle {{ font-family: {DISPLAY_FONT}; font-size: 46px; font-weight: 400; color: {PAPEL}; }}
#bandTitle[compact="true"] {{ font-size: 32px; }}
#bandText {{ color: rgba(24, 38, 46, 0.80); font-size: 14px; }}
#bandFact {{ color: rgba(24, 38, 46, 0.88); font-size: 13px; }}
QLineEdit#bandSearch {{ background: rgba(255, 255, 255, 0.62); border: 1px solid rgba(24, 38, 46, 0.20); border-radius: {R_CONTROL}px; padding: 9px 12px; font-size: 14px; }}
QLineEdit#bandSearch:hover {{ border-color: rgba(24, 38, 46, 0.38); }}
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
QToolTip {{ background: {PAPEL}; color: {SOBRE_SOLIDO}; border: none; padding: 5px 9px; }}
"""


def palette() -> QPalette:
    """Paleta clara para lo que el QSS no alcanza (casillas, iconos, texto de sistema)."""
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
        (QPalette.ColorRole.ToolTipText, SOBRE_SOLIDO),
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
    """Barra de título de Windows del color del fondo, en modo claro: la ventana es una sola pieza.

    Solo Windows 11 acepta el color; en Windows 10 queda la barra oscura normal
    y en cualquier otro sistema no se hace nada.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))   # modo claro

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
