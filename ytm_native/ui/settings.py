"""Opciones: todas las preferencias en una ventana, por temas.

Antes vivían en el menú de la app, repartidas en submenús y con su
explicación escondida en una nota al pasar el ratón. Aquí cada opción es una
fila con su nombre, una línea que dice qué hace (siempre a la vista) y su
control a la derecha: un interruptor si se enciende y se apaga, una lista si
se elige entre varias cosas, un botón si hace algo.

* A la izquierda, los temas, como la barra lateral de la ventana (con la
  misma marca de oro en el que se ve). Con el teclado: flechas para cambiar
  de tema, Tab para entrar en sus opciones, Ctrl+Tab para pasar al siguiente.
* Las filas no guardan nada por su cuenta: cada interruptor va atado a la
  acción de la ventana (``QAction``) que ya hacía ese trabajo, en los dos
  sentidos. Encender algo aquí es lo mismo que hacerlo desde la bandeja o al
  importar una copia de seguridad, y al revés.
* Lector de pantalla: cada control lleva como nombre el de su fila (los
  botones, la fila y su acción: «Cuenta de Last.fm: Conectar…») y como
  descripción su explicación; los interruptores se anuncian como casillas,
  con su estado.
* Los botones hacen su trabajo con métodos públicos de la ventana principal
  (``open_account``, ``export_backup``…), sin tocar sus partes privadas.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from ytm_native.core import i18n
from ytm_native.core.i18n import tr

from . import focus, theme
from .components import ActiveMark, ListFocusRing
from .motion import EASE_OUT, HOVER_MS, Tween


class Switch(QCheckBox):
    """Interruptor: una casilla de Qt (así lo anuncia el lector de pantalla,
    con su estado) dibujada como un carril con su botón.

    Encendido, el carril es aguamarina sólida (más de 3:1 sobre el panel); en
    el tema oscuro lleva además un filete aguamarina claro, que lo separa
    también del hover de la fila. Apagado, solo un borde con el contraste de
    un control. El botón se desliza en ``HOVER_MS``; con las animaciones
    reducidas, salta.

    Con el teclado se cambia con Espacio o con Intro: Intro no puede caer al
    diálogo y cerrarlo.
    """

    W, H = 40, 22
    _KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._knob = Tween(self, 0.0, lambda _v: self.update(), ms=HOVER_MS, curve=EASE_OUT, movement=True)
        self.toggled.connect(lambda _on: self.show_state())

    def setChecked(self, on: bool) -> None:  # noqa: N802 - API de Qt
        super().setChecked(on)
        if not self.isVisible():
            self._knob.set(1.0 if on else 0.0)

    def sync(self) -> None:
        """Pone el botón en su sitio sin animar (al abrir la ventana)."""
        self._knob.set(1.0 if self.isChecked() else 0.0)

    def show_state(self) -> None:
        """Desliza el botón hasta el estado que tiene (también tras cambiarlo
        con las señales bloqueadas)."""
        self._knob.to(1.0 if self.isChecked() else 0.0)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.isEnabled() and not event.isAutoRepeat():
                self.click()
            event.accept()            # nunca al diálogo: lo cerraría sin cambiar nada
            return
        super().keyPressEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.W + 6, self.H + 6)

    def hitButton(self, pos) -> bool:  # noqa: N802 - toda la superficie responde
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        track = QRectF(3, (self.height() - self.H) / 2, self.W, self.H)
        t = float(self._knob.value)
        radius = self.H / 2
        if t > 0.0:
            fill = QColor(theme.SOLIDO)
            fill.setAlphaF(t)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(track, radius, radius)
            if theme.is_dark():
                # Sobre el hover de la fila, el sólido oscuro no llega a 3:1:
                # el filete claro marca el contorno del control encendido.
                edge = QColor(theme.CREMA)
                edge.setAlphaF(t)
                painter.setPen(QPen(edge, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(track.adjusted(0.75, 0.75, -0.75, -0.75), radius, radius)
        border = QColor(theme.TENUE)
        border.setAlphaF(1.0 - t)
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(track.adjusted(0.75, 0.75, -0.75, -0.75), radius, radius)
        knob = self.H - 8
        x = track.left() + 4 + t * (self.W - 8 - knob)
        on_color, off_color = QColor(theme.SOBRE_SOLIDO), QColor(theme.TENUE)
        color = QColor(
            round(off_color.red() + (on_color.red() - off_color.red()) * t),
            round(off_color.green() + (on_color.green() - off_color.green()) * t),
            round(off_color.blue() + (on_color.blue() - off_color.blue()) * t),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(x, track.top() + 4, knob, knob))
        if self.hasFocus() and focus.keyboard():
            painter.setOpacity(1.0)
            painter.setPen(QPen(QColor(theme.PAPEL), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-2.5, -2.5, 2.5, 2.5), radius + 2.5, radius + 2.5)
        painter.end()


class ChoiceBox(QComboBox):
    """Lista desplegable de una opción, con el chevrón de la fuente de
    símbolos (la flecha de Fusion no es de esta casa)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("optionChoice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(theme.HUMO if self.isEnabled() else theme.TENUE)
        font = theme.icon_font(10)
        box = QRectF(self.width() - 30, 0, 22, self.height())
        if font is not None:
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, theme.glyph("chevron_down"))
        else:
            pen = QPen(color, 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            c = box.center()
            painter.drawPolyline([c + QPointF(-4, -2), c + QPointF(0, 2), c + QPointF(4, -2)])
        painter.end()


class Row(QWidget):
    """Una opción: nombre, qué hace y, a la derecha, su control.

    En las de interruptor toda la fila responde al clic (el blanco es grande y
    no hay que apuntar al carril). El hover tiñe la fila entera para que se
    vea qué nombre va con qué control.
    """

    def __init__(self, title: str, note: str = "", control: Optional[QWidget] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("optionRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.control = control
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 10)
        lay.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(3)
        # Hijas de la fila desde el principio: una etiqueta sin padre a la que
        # se le da ``setVisible`` se abre como ventana propia (se veían
        # ventanitas vacías un instante al abrir Opciones).
        self.title = QLabel(title, self)
        self.title.setObjectName("optionTitle")
        self.title.setWordWrap(True)
        text.addWidget(self.title)
        self.note = QLabel(note, self)
        self.note.setObjectName("optionNote")
        self.note.setWordWrap(True)
        self.note.setVisible(bool(note))
        text.addWidget(self.note)
        lay.addLayout(text, 1)
        for label in (self.title, self.note):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if control is not None:
            control.setParent(self)
            lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            self.sync_name()
            if note:
                control.setAccessibleDescription(note)

    def accessible_name(self) -> str:
        """El nombre del control para el lector de pantalla y el control por
        voz. Un botón ya dice lo que hace («Conectar…»): su nombre lleva la
        fila y ese texto, para que se anuncie la acción y que decir lo que se
        ve en él lo encuentre (WCAG 2.5.3). Los demás, el nombre de la fila."""
        title = self.title.text()
        if isinstance(self.control, QPushButton) and self.control.text():
            return f"{title}: {self.control.text()}"
        return title

    def sync_name(self) -> None:
        """Vuelve a poner el nombre accesible (tras cambiar el texto del botón)."""
        if self.control is not None:
            self.control.setAccessibleName(self.accessible_name())

    def set_action(self, text: str) -> None:
        """Cambia el texto del botón de la fila y, con él, su nombre accesible."""
        self.control.setText(text)
        self.sync_name()

    def set_note(self, note: str) -> None:
        self.note.setText(note)
        self.note.setVisible(bool(note))
        if self.control is not None:
            self.control.setAccessibleDescription(note)

    def set_title(self, title: str) -> None:
        self.title.setText(title)
        self.sync_name()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        control = self.control
        if (isinstance(control, Switch) and control.isEnabled()
                and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint())):
            control.toggle()
            return
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self.underMouse() or self.control is None or not self.control.isEnabled():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.RELIEVE))
        painter.drawRoundedRect(QRectF(self.rect()), theme.R_CONTROL, theme.R_CONTROL)
        painter.end()


class Page(QScrollArea):
    """Un tema de las opciones: título, una frase y sus grupos de filas."""

    def __init__(self, title: str, lead: str):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.title = title
        body = QWidget()
        self._lay = QVBoxLayout(body)
        self._lay.setContentsMargins(16, 22, 16, 24)
        self._lay.setSpacing(0)
        heading = QLabel(title)
        heading.setObjectName("optionsTitle")
        heading.setContentsMargins(12, 0, 0, 0)
        self._lay.addWidget(heading)
        if lead:
            intro = QLabel(lead)
            intro.setObjectName("optionsLead")
            intro.setWordWrap(True)
            intro.setContentsMargins(12, 0, 0, 0)
            self._lay.addSpacing(4)
            self._lay.addWidget(intro)
        self._lay.addSpacing(10)
        self._lay.addStretch(1)
        self.setWidget(body)

    def group(self, title: str = "", note: str = "") -> None:
        """Abre un grupo: su rótulo con aire encima (sin rayas entre grupos)."""
        at = self._lay.count() - 1
        if title:
            label = QLabel(title)
            label.setObjectName("optionsGroup")
            label.setContentsMargins(12, 0, 12, 0)
            self._lay.insertSpacing(at, 18)
            self._lay.insertWidget(at + 1, label)
            at += 2
            if note:
                hint = QLabel(note)
                hint.setObjectName("optionNote")
                hint.setWordWrap(True)
                hint.setContentsMargins(12, 2, 12, 0)
                self._lay.insertWidget(at, hint)
                at += 1
            self._lay.insertSpacing(at, 6)

    def add(self, row: QWidget) -> QWidget:
        self._lay.insertWidget(self._lay.count() - 1, row)
        return row


def bind_switch(action: QAction) -> Switch:
    """Interruptor atado a una acción de la ventana, en los dos sentidos.

    La acción manda: si se niega a cambiar (p. ej. Discord sin id de
    aplicación), el interruptor vuelve a su estado. También copia si está
    disponible."""
    switch = Switch()
    switch.setChecked(action.isChecked())
    switch.setEnabled(action.isEnabled())

    def from_switch(on: bool) -> None:
        if action.isChecked() != on:
            action.setChecked(on)
        if switch.isChecked() != action.isChecked():
            switch.blockSignals(True)
            switch.setChecked(action.isChecked())
            switch.blockSignals(False)
            switch.show_state()

    def from_action() -> None:
        switch.setEnabled(action.isEnabled())
        if switch.isChecked() != action.isChecked():
            switch.blockSignals(True)
            switch.setChecked(action.isChecked())
            switch.blockSignals(False)
            switch.show_state()

    switch.toggled.connect(from_switch)
    follow(action.changed, switch, from_action)
    follow(action.toggled, switch, from_action)
    return switch


class _Relay(QObject):
    """Recadero entre una señal de la ventana principal y un control de
    Opciones. Es hijo del control: cuando Opciones se cierra y el control se
    destruye, el recadero se va con él y Qt deshace la conexión. (Con una
    función suelta la conexión sobrevivía y, al cambiar después esa
    preferencia desde la bandeja o el menú, se llamaba a un control ya
    destruido y la app se cerraba.)"""

    def __init__(self, owner: QObject, step: Callable[[], None]):
        super().__init__(owner)
        self._step = step

    def fire(self, *_args) -> None:
        self._step()


def follow(signal, owner: QObject, step: Callable[[], None]) -> None:
    """Conecta ``signal`` con ``step`` mientras viva ``owner``."""
    signal.connect(_Relay(owner, step).fire)


class OptionsDialog(QDialog):
    """La ventana de opciones. ``window`` es la ventana principal: sus
    acciones son el modelo de cada interruptor y sus métodos, lo que hacen
    los botones."""

    restart_requested = Signal()

    PAGES = ("general", "playback", "account", "integrations", "maintenance")

    def __init__(self, window, start: str = "general"):
        super().__init__(window)
        self.window_ = window
        self.setObjectName("optionsDialog")
        self.setWindowTitle(tr("Opciones"))
        self.setMinimumSize(720, 500)
        self.resize(860, 640)
        self._refreshers: list[Callable[[], None]] = []
        self._rows: dict[QAction, Row] = {}
        self.restart_requested.connect(window.restart_app)

        gutter = theme.GUTTER
        outer = QHBoxLayout(self)
        outer.setContentsMargins(gutter, gutter, gutter, gutter)
        outer.setSpacing(gutter)

        rail = QFrame()
        rail.setObjectName("panel")
        rail.setFixedWidth(212)
        rail_lay = QVBoxLayout(rail)
        rail_lay.setContentsMargins(0, 18, 0, 10)
        rail_lay.setSpacing(6)
        heading = QLabel(tr("Opciones"))
        heading.setObjectName("optionsRailTitle")
        heading.setContentsMargins(18, 0, 16, 0)
        rail_lay.addWidget(heading)
        self.nav = QListWidget()
        self.nav.setObjectName("sidebar")
        self.nav.setAccessibleName(tr("Temas de las opciones"))
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rail_lay.addWidget(self.nav, 1)
        self.mark = ActiveMark(self.nav)
        self.ring = ListFocusRing(self.nav)
        outer.addWidget(rail)

        content = QFrame()
        content.setObjectName("panel")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)
        self.stack = QStackedWidget()
        content_lay.addWidget(self.stack, 1)
        foot = QHBoxLayout()
        foot.setContentsMargins(22, 10, 16, 14)
        self.hint = QLabel(tr("Los cambios se guardan al momento."))
        self.hint.setObjectName("optionNote")
        foot.addWidget(self.hint, 1)
        # Sin botón por defecto: Intro sobre un interruptor o una lista no
        # cierra la ventana. Escape la cierra; Cerrar, con Tab e Intro.
        close = QPushButton(tr("Cerrar"))
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        content_lay.addLayout(foot)
        outer.addWidget(content, 1)

        self.pages: dict[str, Page] = {}
        for key, build in (
            ("general", self._general),
            ("playback", self._playback),
            ("account", self._account),
            ("integrations", self._integrations),
            ("maintenance", self._maintenance),
        ):
            page = build()
            self.pages[key] = page
            self.stack.addWidget(page)
            item = QListWidgetItem(page.title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(0, 38))
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.show_page(start)

        for keys, step in (("Ctrl+Tab", 1), ("Ctrl+PgDown", 1), ("Ctrl+Shift+Tab", -1), ("Ctrl+PgUp", -1)):
            QShortcut(QKeySequence(keys), self, activated=lambda s=step: self._step_page(s))

    # -- navegación ---------------------------------------------------------
    def show_page(self, key: str) -> None:
        row = self.PAGES.index(key) if key in self.PAGES else 0
        self.nav.setCurrentRow(row)
        self.stack.setCurrentIndex(row)

    def current_page(self) -> str:
        return self.PAGES[self.stack.currentIndex()]

    def _step_page(self, step: int) -> None:
        self.nav.setCurrentRow((self.nav.currentRow() + step) % self.nav.count())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        for switch in self.findChildren(Switch):
            switch.sync()
        self.mark.follow()
        self.nav.setFocus()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Un ``QDialog`` sin botón por defecto elige él uno al abrirse (el
        # primer botón) y lo pulsa con Intro desde cualquier control: desde
        # la lista de temas, Intro cerraba Opciones. Aquí Intro solo pulsa el
        # botón que tiene el foco (eso lo hace el propio botón, antes de
        # llegar aquí); en lo demás no hace nada.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    def refresh(self) -> None:
        """Vuelve a leer lo que no es un interruptor (listas, cuentas,
        versiones): tras otra ventana o al importar una copia de seguridad."""
        for step in self._refreshers:
            step()

    # -- piezas ---------------------------------------------------------------
    def _switch(self, page: Page, action: QAction, title: str, note: str = "") -> Row:
        row = self._rows[action] = page.add(Row(title, note, bind_switch(action)))
        return row

    def row_for(self, action: QAction) -> Row:
        """La fila del interruptor atado a ``action``."""
        return self._rows[action]

    def _button(self, page: Page, title: str, note: str, label: str, slot: Callable[[], None]) -> Row:
        button = QPushButton(label)
        button.clicked.connect(lambda _checked=False: slot())
        return page.add(Row(title, note, button))

    def _choice(self, page: Page, title: str, note: str, options, current: str,
                on_pick: Callable[[str], None]) -> Row:
        combo = ChoiceBox()
        combo.setMinimumWidth(232)
        for value, label in options:
            combo.addItem(label, value)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        combo.currentIndexChanged.connect(lambda index: on_pick(combo.itemData(index)))
        return page.add(Row(title, note, combo))

    @staticmethod
    def _show_choice(combo: QComboBox, value: str) -> None:
        """Enseña ``value`` en la lista sin tomarlo como una elección nueva."""
        index = max(0, combo.findData(value))
        if combo.currentIndex() != index:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    # -- temas ----------------------------------------------------------------
    def _general(self) -> Page:
        w = self.window_
        page = Page(tr("General"), tr("Idioma, aspecto y qué hace la ventana al cerrarla."))
        page.group(tr("Idioma y aspecto"))
        system = i18n.NATIVE_NAMES[i18n.system_language()]
        self.language_row = self._choice(
            page, tr("Idioma"), "",
            ((i18n.SYSTEM, tr("Como Windows ({language})", language=system)),
             (i18n.SPANISH, i18n.NATIVE_NAMES[i18n.SPANISH]),
             (i18n.ENGLISH, i18n.NATIVE_NAMES[i18n.ENGLISH])),
            i18n.preference(w.db), self._pick_language)
        self.language_combo = self.language_row.control
        self.restart_button = QPushButton(tr("Reiniciar ahora"))
        self.restart_button.clicked.connect(self.restart_requested.emit)
        self.restart_row = page.add(Row(tr("Falta reiniciar"), "", self.restart_button))
        self._sync_language_note()

        modes = (("system", tr("Como Windows")), ("light", tr("Claro")), ("dark", tr("Oscuro")))
        self.theme_row = self._choice(page, tr("Tema"), tr("Colores claros u oscuros. «Como Windows» cambia con el sistema."),
                                      modes, w.theme_mode, self._pick_theme)
        # Las dos listas, del mismo ancho: sus bordes forman una columna. Es
        # un mínimo, no un ancho fijo: si con el estilo ya aplicado o con
        # otra escala de pantalla una necesita más, crece y no recorta.
        width = max(self.language_combo.sizeHint().width(), self.theme_row.control.sizeHint().width())
        for combo in (self.language_combo, self.theme_row.control):
            combo.setMinimumWidth(width)
        # Lo que cambie fuera (bandeja, copia importada) se ve aquí.
        for action in w.theme_actions.values():
            follow(action.toggled, self.theme_row, self._sync_choices)
        self._refreshers.append(self._sync_choices)

        page.group(tr("Ventana"))
        self._switch(page, w.close_to_tray_action, tr("Al cerrar, seguir sonando en la bandeja"),
                     tr("La X oculta la ventana y la música sigue; para salir, «Salir» en el menú o en la bandeja."))
        self._switch(page, w.minimize_to_tray_action, tr("Al minimizar, ir a la bandeja"),
                     tr("La ventana desaparece de la barra de tareas y queda junto al reloj."))
        page.group(tr("Al encender el equipo"))
        row = self._switch(page, w.autostart_action, tr("Iniciar con Windows"),
                           tr("La app arranca en la bandeja al iniciar sesión, sin abrir la ventana."))
        if not w.autostart_action.isEnabled():
            row.set_note(tr("No disponible en este equipo."))
        return page

    def _playback(self) -> Page:
        w = self.window_
        page = Page(tr("Reproducción"), tr("Cómo suena la música y qué se recuerda de ella."))
        page.group(tr("Al reproducir"))
        self._switch(page, w.autoplay_action, tr("Radio al terminar la cola"),
                     tr("Cuando se acaba la cola, sigue sonando música parecida."))
        self._switch(page, w.equalize_action, tr("Igualar el volumen"),
                     tr("Nivela el volumen para que ninguna canción suene mucho más fuerte que otra."))
        self._switch(page, w.resume_action, tr("Seguir donde lo dejaste"),
                     tr("Al abrir la app vuelven la cola, la canción y el segundo en que te quedaste."))
        page.group(tr("Historial"))
        self._switch(page, w.history_action, tr("Guardar el historial de escucha"),
                     tr("Anota en este equipo lo que escuchas. No se envía a ningún sitio."))
        page.group(tr("Temporizador de apagado"))
        self._switch(page, w.sleep_quit_action, tr("Cerrar la aplicación al terminar"),
                     tr("Cuando el temporizador para la música, la app también se cierra."))
        return page

    def _account(self) -> Page:
        w = self.window_
        page = Page(tr("Cuenta"), tr("Usa siempre una cuenta secundaria de YouTube Music."))
        page.group(tr("Inicio de sesión"))
        self._button(page, tr("Iniciar sesión con Firefox"),
                     tr("Inicias sesión en un Firefox aparte y la app toma de ahí las credenciales. Es lo recomendado."),
                     tr("Iniciar sesión…"), lambda: self._then(w.open_firefox_login))
        self._button(page, tr("Credenciales y modo de reproducción"),
                     tr("Cabeceras pegadas a mano, cookies.txt y cómo se obtiene el audio."),
                     tr("Abrir…"), lambda: self._then(w.open_account))
        page.group(tr("Cambios en tu cuenta de YouTube Music"),
                   tr("«Me gusta», guardar en la biblioteca y editar listas cambian tu cuenta en YouTube Music, "
                      "igual que en la web."))
        self._switch(page, w.mutations_action, tr("Permitir cambios en mi cuenta de YouTube Music"))
        history = self._switch(page, w.account_history_action,
                               tr("Registrar mis escuchas en el historial de YouTube"),
                               tr("Necesita los cambios en tu cuenta permitidos."))

        def sync_history_note() -> None:
            history.set_note("" if w.account_history_action.isEnabled()
                             else tr("Necesita los cambios en tu cuenta permitidos."))

        follow(w.account_history_action.changed, history, sync_history_note)
        sync_history_note()
        return page

    def _integrations(self) -> Page:
        w = self.window_
        page = Page(tr("Integraciones"), tr("Otros servicios que pueden saber lo que escuchas. Todo apagado de entrada."))
        page.group(tr("Discord"))
        self._switch(page, w.discord_action, tr("Mostrar en Discord lo que escucho"),
                     tr("Tu estado de Discord enseña la canción que suena."))
        page.group(tr("Last.fm"))
        scrobble = self._switch(page, w.lastfm_action, tr("Enviar mis escuchas a Last.fm"), "")
        button = QPushButton()
        button.clicked.connect(lambda _checked=False: self._then(w.manage_lastfm_account))
        account = page.add(Row(tr("Cuenta de Last.fm"), "", button))

        def sync_lastfm() -> None:
            if w.lastfm.connected:
                who = w.lastfm.user or tr("tu cuenta")
                account.set_note(tr("Conectada como {user}.", user=who))
                account.set_action(tr("Desconectar"))
                scrobble.set_note(tr("Registra en tu perfil de Last.fm cada canción que escuchas."))
            else:
                account.set_note(tr("Sin conectar. Hace falta una clave de aplicación de Last.fm."))
                account.set_action(tr("Conectar…"))
                scrobble.set_note(tr("Conecta antes una cuenta de Last.fm."))

        follow(w.lastfm_action.changed, account, sync_lastfm)
        self._refreshers.append(sync_lastfm)
        sync_lastfm()
        return page

    def _maintenance(self) -> Page:
        w = self.window_
        page = Page(tr("Mantenimiento"), tr("Actualizaciones, arreglos y tus datos."))
        page.group(tr("Actualizaciones"))
        self._switch(page, w.app_updates_action, tr("Avisar de versiones nuevas de la app"),
                     tr("Mira una vez al día si hay versión nueva. Nada se descarga solo."))
        self._button(page, tr("Versión nueva de la app"), tr("Comprueba ahora si hay una versión publicada."),
                     tr("Buscar"), lambda: w.app_updates.check(manual=True))
        self._button(page, tr("yt-dlp"), tr("Es lo que obtiene el audio de YouTube; se actualiza a menudo."),
                     tr("Buscar actualización"), w.update_ytdlp)
        self._button(page, tr("Binarios que faltan"), tr("mpv, yt-dlp y deno, si aún no están en la carpeta de la app."),
                     tr("Descargar"), w.install_binaries)
        page.group(tr("Si algo falla"))
        self._button(page, tr("Diagnóstico"), tr("Estado de los binarios, las credenciales y la conexión."),
                     tr("Ver…"), lambda: self._then(w.show_diagnostics))
        self._button(page, tr("Sincronizar todo de nuevo"),
                     tr("Vuelve a descargar todas las listas, también las que no han cambiado."),
                     tr("Sincronizar"), lambda: w.start_sync(force_all=True))
        page.group(tr("Tus datos"))
        self._button(page, tr("Exportar copia de seguridad"),
                     tr("Guarda en un archivo tus listas, el historial y las preferencias."),
                     tr("Exportar…"), lambda: self._then(w.export_backup))
        self._button(page, tr("Importar copia de seguridad"),
                     tr("Recupera lo guardado en una copia anterior."),
                     tr("Importar…"), lambda: self._then(w.import_backup))
        self._button(page, tr("Carpeta de datos"), tr("La base, los registros y las credenciales de la app."),
                     tr("Abrir"), w.open_data_folder)
        return page

    # -- respuestas -----------------------------------------------------------
    def _then(self, step: Callable[[], None]) -> None:
        """Lo que abre otra ventana: al volver, lo que muestre estado se relee."""
        step()
        self.refresh()

    def _pick_theme(self, mode: str) -> None:
        self.window_.set_theme_mode(mode)
        theme.tint_title_bar(self)

    def _sync_choices(self) -> None:
        """Las listas de idioma y tema, como están guardados ahora (una copia
        importada puede haberlos cambiado), con la nota de reinicio al día."""
        self._show_choice(self.language_combo, i18n.preference(self.window_.db))
        self._show_choice(self.theme_row.control, self.window_.theme_mode)
        self._sync_language_note()

    def _pick_language(self, value: str) -> None:
        i18n.set_preference(self.window_.db, value)
        self._sync_language_note()

    def _sync_language_note(self) -> None:
        wanted = i18n.resolve(i18n.preference(self.window_.db))
        pending = wanted != i18n.active()
        self.restart_row.setVisible(pending)
        if pending:
            self.restart_row.set_title(tr("Falta reiniciar"))
            self.restart_row.set_note(
                tr("La app se verá en {language} la próxima vez que se abra.",
                   language=i18n.NATIVE_NAMES[wanted]))
            self.language_row.set_note("")
        else:
            self.language_row.set_note(tr("Se aplica al volver a abrir la app."))
