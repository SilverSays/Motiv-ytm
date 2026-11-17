"""Movimiento: curvas, duraciones y un valor animado que se puede interrumpir.

Reglas que sigue toda la interfaz:

- Nada se anima porque sí. Cada animación responde a una de estas razones:
  confirmar una pulsación, indicar un estado, o evitar un cambio brusco.
- Lo que se repite cientos de veces al día (teclas, navegar la lista, el hover
  de las filas) no se anima.
- Nada dura más de 300 ms y lo que entra usa una curva que arranca rápido.
  La única excepción es un movimiento continuo y lento ligado a lo que suena
  (los aros de la cabecera): gira solo mientras hay música, cambia de ritmo en
  ``SPIN_MS`` y se detiene del todo en reposo. Lo mismo vale para el título
  del mini reproductor que no cabe: se desliza despacio, descansa en cada
  vuelta y no se mueve si el texto cabe o la ventana no se ve.
- Salir es más rápido que entrar.
- Si Windows tiene desactivadas las animaciones, el movimiento desaparece y solo
  quedan los fundidos de color y opacidad.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget


def _bezier(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


# Las curvas de serie son demasiado blandas: estas arrancan con decisión.
EASE_OUT = _bezier(0.23, 1.0, 0.32, 1.0)        # lo que entra o responde al usuario
EASE_IN_OUT = _bezier(0.77, 0.0, 0.175, 1.0)    # lo que se desplaza por la pantalla
EASE = _bezier(0.25, 0.1, 0.25, 1.0)            # color y hover
EASE_DRAWER = _bezier(0.32, 0.72, 0.0, 1.0)     # paneles que se abren o se cierran de lado

PRESS_MS = 100      # hundir un botón
RELEASE_MS = 160    # soltarlo
HOVER_MS = 140      # aparecer o desaparecer un hover
FADE_MS = 200       # fundidos de imagen, texto y color
ENTER_MS = 260      # entrada de un aviso
EXIT_MS = 150       # salida de un aviso
SCROLL_MS = 300     # desplazamiento de la letra
PANE_OPEN_MS = 280  # un panel lateral que se abre
PANE_CLOSE_MS = 220 # y que se cierra (salir es más rápido que entrar)
FLIGHT_MS = 300     # la carátula que viaja del reproductor al panel
REFLOW_MS = 220     # lo que se encoge, se oculta o se recoloca al cambiar el ancho
REVEAL_MS = 300     # una fila o una tarjeta que aparece barriendo de izquierda a derecha
ROW_STAGGER = 28    # retraso entre una fila y la siguiente
CARD_STAGGER = 40   # y entre una tarjeta y la de su derecha
STAGGER_CAP = 14    # pasado este número, las demás llegan a la vez que la última
SPIN_MS = 300       # los aros de la cabecera cogen velocidad o frenan hasta pararse
FRAME_MS = 16       # un fotograma a 60 por segundo, para lo que se mueve sin parar
MARQUEE_PX_S = 30   # velocidad del texto que no cabe y se desliza
MARQUEE_REST_MS = 1800   # pausa al principio de cada vuelta, para poder leerlo quieto

_reduced: Optional[bool] = None


def reduced_motion() -> bool:
    """¿El usuario pidió menos animaciones? (Windows: "Efectos de animación")."""
    global _reduced
    if _reduced is None:
        override = os.environ.get("YTMN_REDUCED_MOTION")
        if override is not None:
            _reduced = override not in ("", "0")
        elif sys.platform == "win32":
            try:
                import ctypes

                enabled = ctypes.c_int(1)
                ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
                _reduced = not bool(enabled.value)
            except Exception:  # noqa: BLE001
                _reduced = False
        else:
            _reduced = False
    return _reduced


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """Color intermedio entre ``a`` (t=0) y ``b`` (t=1)."""
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


class Tween(QObject):
    """Un valor (número o color) que viaja hacia su destino.

    Se comporta como una transición de CSS y no como un fotograma clave: si el
    destino cambia a mitad de camino, sigue desde donde está en vez de volver a
    empezar. Con ``movement=True`` el cambio es instantáneo cuando el sistema
    pide menos animaciones.
    """

    def __init__(self, parent: QObject, value, on_change: Callable[[object], None], ms: int = FADE_MS,
                 curve: QEasingCurve = EASE_OUT, movement: bool = False):
        super().__init__(parent)
        self._value = value
        self._target = value
        self._on_change = on_change
        self._ms = ms
        self._curve = curve
        self._movement = movement
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._step)

    @property
    def value(self):
        return self._value

    @property
    def target(self):
        return self._target

    def running(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running

    def to(self, target, ms: Optional[int] = None, curve: Optional[QEasingCurve] = None) -> None:
        if target == self._target:
            return
        duration = self._ms if ms is None else ms
        if self._movement and reduced_motion():
            duration = 0
        parent = self.parent()
        if isinstance(parent, QWidget) and not parent.isVisible():
            duration = 0          # nadie lo ve: no se gasta una animación
        if duration <= 0:
            self.set(target)
            return
        self._anim.stop()
        self._target = target
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(target)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(curve or self._curve)
        self._anim.start()

    def stop(self) -> None:
        """Se queda donde está, sin llegar al destino."""
        self._anim.stop()
        self._target = self._value

    def set(self, value) -> None:
        self._anim.stop()
        self._target = value
        self._value = value
        self._on_change(value)

    def _step(self, value) -> None:
        if value is None:
            return
        self._value = value
        self._on_change(value)
