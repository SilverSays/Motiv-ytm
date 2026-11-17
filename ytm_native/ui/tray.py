"""Bandeja del sistema e inicio con Windows.

* El icono de la bandeja deja la música sonando con la ventana cerrada: su
  menú lleva los controles, la canción que suena y la salida de verdad.
* "Iniciar con Windows" es una entrada en ``HKCU\\...\\Run`` (solo el usuario
  actual, sin permisos de administrador) que abre la app con ``--minimized``:
  arranca directamente en la bandeja.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from ytm_native.core.db import Track

from . import theme
from .chrome import rounded_menu

log = logging.getLogger("ytmn.tray")

APP_NAME = "YTMusicNativo"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MINIMIZED_ARG = "--minimized"
TIP_LIMIT = 120          # Windows corta la descripción emergente de la bandeja en 127


# ---------------------------------------------------------------------------
# Icono de la aplicación
# ---------------------------------------------------------------------------
def _icon_pixmap(px: int) -> QPixmap:
    """Una funda sólida con la nota. Desde 24 px la nota va dentro de un aro
    fino con un arco del oro de los aros de la cabecera; más pequeño, el aro
    sería ruido y la nota ocupa todo el sitio."""
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    inset = max(0.5, px / 32)
    rect = QRectF(inset, inset, px - 2 * inset, px - 2 * inset)
    radius = px * 0.22
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(theme.SOLIDO))
    painter.drawRoundedRect(rect, radius, radius)
    ringed = px >= 24
    if ringed:
        ring = rect.adjusted(px * 0.17, px * 0.17, -px * 0.17, -px * 0.17)
        faint = QColor(theme.SOBRE_SOLIDO)
        faint.setAlphaF(0.22)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(faint, max(1.0, px / 26)))
        painter.drawEllipse(ring)
        arc = QPen(QColor(theme.ORO), max(1.5, px / 13))
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(ring, 35 * 16, 120 * 16)
    font = theme.icon_font(round(px * (0.38 if ringed else 0.72)))
    if font is None:
        font = theme.ui_font(round(px * (0.42 if ringed else 0.75)))
    painter.setFont(font)
    painter.setPen(QColor(theme.SOBRE_SOLIDO))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, theme.glyph("music"))
    painter.end()
    return out


_ICON: Optional[QIcon] = None


def app_icon() -> QIcon:
    """El icono de la app, dibujado a cada tamaño (no escalado de uno grande:
    a 16 px una reducción emborrona la nota)."""
    global _ICON
    if _ICON is None:
        icon = QIcon()
        for px in (16, 20, 24, 32, 40, 48, 64, 128, 256):
            icon.addPixmap(_icon_pixmap(px))
        _ICON = icon
    return _ICON


# ---------------------------------------------------------------------------
# Inicio con Windows
# ---------------------------------------------------------------------------
def launch_command() -> str:
    """La orden que abre esta misma instalación en la bandeja.

    Con ``pythonw`` (sin consola) y el ``__main__.py`` del paquete por ruta:
    al iniciar sesión no hay carpeta de trabajo de la que fiarse."""
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return f'"{exe}" {MINIMIZED_ARG}'
    windowed = exe.with_name("pythonw.exe")
    if exe.name.lower() == "python.exe" and windowed.is_file():
        exe = windowed
    main = Path(__file__).resolve().parent.parent / "__main__.py"
    return f'"{exe}" "{main}" {MINIMIZED_ARG}'


def _default_registry():
    if sys.platform != "win32":
        return None
    import winreg

    return winreg


class Autostart:
    """La entrada de la app en ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``.

    ``registry`` es el módulo ``winreg`` (o uno simulado en las pruebas). Un
    fallo del registro nunca rompe nada: se anota y se devuelve ``False``."""

    def __init__(self, registry=None, name: str = APP_NAME):
        self._reg = registry if registry is not None else _default_registry()
        self._name = name

    @property
    def supported(self) -> bool:
        return self._reg is not None

    def command(self) -> Optional[str]:
        if self._reg is None:
            return None
        reg = self._reg
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
                value, _kind = reg.QueryValueEx(key, self._name)
        except OSError:
            return None
        return value if isinstance(value, str) else None

    def is_enabled(self) -> bool:
        return self.command() is not None

    def enable(self, command: Optional[str] = None) -> bool:
        if self._reg is None:
            return False
        reg = self._reg
        command = command or launch_command()
        try:
            with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                reg.SetValueEx(key, self._name, 0, reg.REG_SZ, command)
        except OSError:
            log.warning("no se pudo escribir el inicio con Windows", exc_info=True)
            return False
        return True

    def disable(self) -> bool:
        if self._reg is None:
            return False
        reg = self._reg
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key, self._name)
        except FileNotFoundError:
            return True                       # ya no estaba
        except OSError:
            log.warning("no se pudo quitar el inicio con Windows", exc_info=True)
            return False
        return True

    def sync(self, enabled: bool) -> bool:
        """Deja el registro como dice el ajuste. Activado, reescribe la orden
        si cambió (la carpeta del proyecto se movió, otro Python)."""
        if not enabled:
            return self.disable() if self.is_enabled() else True
        command = launch_command()
        return True if self.command() == command else self.enable(command)


# ---------------------------------------------------------------------------
# Icono de la bandeja
# ---------------------------------------------------------------------------
class TrayIcon(QObject):
    """El icono y su menú. No sabe nada del reproductor: pide cosas con señales."""

    show_requested = Signal()
    toggle_requested = Signal()
    next_requested = Signal()
    previous_requested = Signal()
    mini_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._tray = QSystemTrayIcon(app_icon(), self)
        self._tray.setToolTip("YT Music Nativo")
        self._tray.activated.connect(self._on_activated)
        self._playing = False
        # El menú no cuelga de la ventana: con ella oculta debe seguir abriéndose.
        menu = self.menu = rounded_menu(QMenu())
        self.track_action = QAction("Nada en reproducción", self)
        self.track_action.triggered.connect(self.show_requested.emit)
        menu.addAction(self.track_action)
        menu.addSeparator()
        self.play_action = self._add("Reproducir", self.toggle_requested)
        self.next_action = self._add("Siguiente", self.next_requested)
        self.previous_action = self._add("Anterior", self.previous_requested)
        menu.addSeparator()
        self._add("Mostrar la ventana", self.show_requested)
        self._add("Mini reproductor", self.mini_requested)
        menu.addSeparator()
        self._add("Salir definitivamente", self.quit_requested)
        self._tray.setContextMenu(menu)
        self.set_track(None)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _add(self, text: str, signal) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda _checked=False: signal.emit())
        self.menu.addAction(action)
        return action

    def _on_activated(self, reason) -> None:
        # Un clic o un doble clic devuelven la ventana; el derecho abre el menú.
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_requested.emit()

    # -- estado ---------------------------------------------------------------
    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def is_visible(self) -> bool:
        return self._tray.isVisible()

    def set_track(self, track: Optional[Track], cover: Optional[QPixmap] = None) -> None:
        if track is None:
            self.track_action.setText("Nada en reproducción")
            self.track_action.setIcon(QIcon())
            self.track_action.setEnabled(False)
            self._tray.setToolTip("YT Music Nativo")
        else:
            label = f"{track.title} — {track.artists}" if track.artists else track.title
            self.track_action.setText(label)
            self.track_action.setEnabled(True)
            self.track_action.setIcon(QIcon(cover) if cover is not None and not cover.isNull() else QIcon())
            tip = label if len(label) <= TIP_LIMIT else label[: TIP_LIMIT - 1] + "…"
            self._tray.setToolTip(tip)
        has_track = track is not None
        for action in (self.play_action, self.next_action, self.previous_action):
            action.setEnabled(has_track)

    def set_cover(self, cover: Optional[QPixmap]) -> None:
        if self.track_action.isEnabled() and cover is not None and not cover.isNull():
            self.track_action.setIcon(QIcon(cover))

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.play_action.setText("Pausar" if playing else "Reproducir")

    def notify(self, title: str, text: str) -> None:
        if self._tray.isVisible() and QSystemTrayIcon.supportsMessages():
            self._tray.showMessage(title, text, app_icon(), 6000)

    def close(self) -> None:
        self._tray.hide()
        self.menu.deleteLater()
