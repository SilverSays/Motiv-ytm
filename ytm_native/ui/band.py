"""Bandas y filas de cabecera: la banda de cada vista (carátula, título,
notas y acciones), la fila de sugerencias del buscador que lleva dentro y,
desde la Tarea 10.6, cada fila del inicio (``ShelfSection``: su título, el
porqué y sus tarjetas).
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QElapsedTimer, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .art import ArtLoader, Tint, neutral_tint
from .buttons import Chip, PillButton
from .cards import CardGrid, CoverLabel, SleeveCard
from .motion import EASE, EASE_IN_OUT, EASE_OUT, FRAME_MS, REFLOW_MS, SPIN_MS, FADE_MS, Tween, reduced_motion
from .primitives import _alpha, fade_visible, window_settling
from ytm_native.core.i18n import tr


# ---------------------------------------------------------------------------
# Banda de cabecera
# ---------------------------------------------------------------------------
class PlaylistBand(QWidget):
    """Cabecera de la vista: banda de color plano con carátula, título y notas.

    El color no salta de una lista a otra: se funde. En ventanas bajas la banda
    se encoge (``set_compact``) para dejarle sitio a la lista.
    """

    COVER = 172
    COVER_COMPACT = 112
    FACT_GAP = 26
    HEIGHT = 28 + 172 + 8 + 26
    HEIGHT_COMPACT = 20 + 112 + 6 + 18
    # La búsqueda añade la fila de sugerencias bajo el campo.
    SEARCH_HEIGHT = 262
    SEARCH_HEIGHT_COMPACT = 186

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._compact = False
        self._show_cover = True
        self._cover_target = self.COVER     # 0: sin carátula
        self._cover_shadow = 8
        self._has_description = False
        self._searching = False
        self._color = Tween(self, QColor(neutral_tint().band), self._on_color, ms=FADE_MS + 60, curve=EASE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._init_rings()

        outer = QHBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 26)
        outer.setSpacing(28)

        self.cover = CoverLabel(loader, self.COVER, radius=theme.R_COVER, shadow=8)
        # Al estrechar la ventana la carátula se encoge o se va poco a poco, y
        # el texto se desliza a ocupar su sitio.
        self._cover_px = Tween(self, float(self.COVER), self._apply_cover, ms=REFLOW_MS,
                               curve=EASE_IN_OUT, movement=True)
        outer.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = QLabel()
        self.title.setObjectName("bandTitle")
        self.title.setWordWrap(False)
        self.title.setMinimumWidth(60)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        text.addWidget(self.title)

        self.description = QLabel()
        self.description.setObjectName("bandText")
        self.description.setWordWrap(True)
        self.description.setMaximumWidth(620)
        self.description.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.description.setMaximumHeight(56)
        self.description.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text.addSpacing(6)
        text.addWidget(self.description)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("bandSearch")
        self.search_edit.setPlaceholderText(tr("Canción, artista o álbum"))
        self.search_edit.setAccessibleName(tr("Buscar música"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMaximumWidth(520)
        self.search_edit.setMinimumHeight(42)
        # Las sugerencias van pegadas al campo: es donde está la vista al escribir.
        self.suggestions = SuggestionStrip(on_band=True)
        self._search_box = QWidget()
        search_lay = QVBoxLayout(self._search_box)
        search_lay.setContentsMargins(0, 0, 0, 0)
        search_lay.setSpacing(10)
        search_lay.addWidget(self.search_edit)
        search_lay.addWidget(self.suggestions)
        text.addSpacing(10)
        text.addWidget(self._search_box)

        text.addStretch(1)
        self._facts_row = QHBoxLayout()
        self._facts_row.setSpacing(self.FACT_GAP)
        self._fact_labels: list[QLabel] = []
        self._facts: list[str] = []
        text.addLayout(self._facts_row)
        text.addSpacing(14)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.play_btn = PillButton(tr("Reproducir"), "primary", glyph="play")
        self.shuffle_btn = PillButton(tr("Aleatorio"), "ghost", glyph="shuffle")
        self.sync_btn = PillButton(tr("Sincronizar lista"), "ghost", glyph="sync")
        self.sync_btn.setToolTip(tr("Descarga esta lista completa aunque el conteo no haya cambiado"))
        # Botón de la vista: "Opciones" en una lista local, "Guardar como lista
        # local" en una ajena. La ventana decide texto y qué hace.
        self.extra_btn = PillButton("", "ghost")
        self.extra_btn.hide()
        self._actions = (self.play_btn, self.shuffle_btn, self.sync_btn, self.extra_btn)
        for button in self._actions:
            actions.addWidget(button)
        # En la búsqueda la nota ("12 resultados para…") comparte fila con los
        # botones: su sitio de siempre lo ocupan las sugerencias.
        self.search_note = QLabel()
        self.search_note.setObjectName("bandFact")
        self.search_note.setMinimumHeight(PillButton.HEIGHT)
        self.search_note.hide()
        actions.addWidget(self.search_note)
        actions.addStretch(1)
        # Solo en las listas ajenas a la biblioteca: no tienen fila en la barra
        # lateral, así que el camino de vuelta tiene que estar a la vista.
        self.back_btn = PillButton(tr("Volver"), "ghost", glyph="back")
        self.back_btn.setToolTip(tr("Volver a la vista anterior (Alt+Izquierda)"))
        self.back_btn.hide()
        actions.addWidget(self.back_btn)
        text.addLayout(actions)
        outer.addLayout(text, 1)
        self.setFixedHeight(self.HEIGHT)
        self._full_title = ""

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API de Qt
        # El ancho no lo impone el contenido: la banda se adapta a lo que le den
        # (carátula más pequeña o fuera, botones en solo icono, notas que no
        # caben ocultas). Si dependiera de lo que muestra, el mínimo cambiaría
        # al arrastrar los paneles y el divisor se trabaría o daría saltos.
        return QSize(0, super().minimumSizeHint().height())

    # -- contenido ----------------------------------------------------------
    def set_content(self, title: str, description: str, facts: list[str], cover_url: str,
                    show_search: bool = False, show_sync: bool = True, show_cover: bool = True,
                    show_back: bool = False, extra: Optional[tuple[str, str]] = None,
                    round_cover: bool = False) -> None:
        self._show_cover = show_cover
        self.cover.set_round(round_cover)
        self.back_btn.setVisible(show_back)
        if extra is not None:
            self.extra_btn.set_label(*extra)
        self.extra_btn.setVisible(extra is not None)
        self._full_title = title
        self._elide_title()
        self.title.setToolTip(title)
        self.description.setText(description)
        # Solo líneas enteras: con la columna estrecha no asoma media línea sobre las notas.
        self.description.ensurePolished()
        self.description.setMaximumHeight(QFontMetrics(self.description.font()).lineSpacing() * 3)
        self._has_description = bool(description)
        self._searching = show_search
        self.description.setVisible(self._has_description and not show_search and not self._compact)
        self._search_box.setVisible(show_search)
        self.search_note.setVisible(show_search)
        self.sync_btn.setVisible(show_sync)
        self.cover.set_url(cover_url)
        self._apply_height()
        self.set_facts(facts)
        self._fit_width(animate=False)      # otra vista: llega ya colocada

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        margins = (24, 20, 24, 18) if compact else (32, 28, 32, 26)
        self.layout().setContentsMargins(*margins)
        self.layout().setSpacing(22 if compact else 28)
        self.title.setStyleSheet("font-size: 28px;" if compact else "")
        self.search_edit.setMinimumHeight(38 if compact else 42)
        self.description.setVisible(self._has_description and not self._searching and not compact)
        self._apply_height()
        self._fit_width()

    # Ancho que necesita la columna de texto junto a cada tamaño de carátula.
    TEXT_MIN_FULL = 340
    TEXT_MIN_SMALL = 230
    ACTION_GAP = 10
    # Margen con el que se recoge cada cosa: al estrechar la ventana arrastrando,
    # la animación termina antes de que el sitio falte de verdad.
    SLACK = 40

    def _text_width(self) -> int:
        """Ancho de la columna de texto cuando la carátula llegue a su tamaño."""
        margins = self.layout().contentsMargins()
        width = self.width() - margins.left() - margins.right()
        if self._cover_target:
            width -= self._cover_target + self._cover_shadow + self.layout().spacing()
        return width

    def _apply_cover(self, value) -> None:
        px, target = float(value), self._cover_target
        if target and abs(px - target) < 0.5:
            self.cover.set_size(target, self._cover_shadow)
            self.cover.setVisible(True)
        elif px < 4:
            self.cover.hide()
        else:
            self.cover.setVisible(True)
            # Al irse, además de encogerse se funde; entre dos tamaños, no.
            self.cover.set_display_size(px, presence=min(1.0, px / self.COVER_COMPACT))

    def _fit_width(self, animate: bool = True) -> None:
        """Reparte el ancho: primero encoge la carátula, luego la quita; los
        botones que no caben con su texto se quedan en solo icono. Con
        ``animate`` (cambios de ancho) todo eso se ve pasar; al cambiar de
        vista llega ya colocado."""
        margins = self.layout().contentsMargins()
        inner = self.width() - margins.left() - margins.right()
        spacing = self.layout().spacing()
        big = self.COVER_COMPACT if self._compact else self.COVER
        if inner - big - 8 - spacing >= self.TEXT_MIN_FULL + self.SLACK:
            size, fits = big, True
        elif inner - self.COVER_COMPACT - 6 - spacing >= self.TEXT_MIN_SMALL + self.SLACK:
            size, fits = self.COVER_COMPACT, True
        else:
            size, fits = big, False
        target = size if (self._show_cover and fits) else 0
        if target:
            self._cover_shadow = 6 if size == self.COVER_COMPACT else 8
        self._cover_target = target
        if animate and self.cover.isVisible() != bool(target) and not self.cover.isVisible():
            self._cover_px.set(0.0)          # aparece desde nada, no desde su último tamaño
        if animate:
            self._cover_px.to(float(target))
        else:
            self._cover_px.set(float(target))
            self._apply_cover(float(target))
        self._fit_actions(self._text_width(), animate)
        self._elide_title()
        self._fit_facts(animate)

    def _fit_actions(self, available: int, animate: bool = True) -> None:
        shown = [b for b in self._actions if b.isVisibleTo(self)]
        if self.back_btn.isVisibleTo(self):
            shown.append(self.back_btn)
        if not shown:
            return
        gap = self.ACTION_GAP
        # La nota de la búsqueda comparte fila con los botones: se le guarda sitio.
        if self.search_note.isVisibleTo(self):
            available -= min(160, self.search_note.sizeHint().width()) + gap
        gaps = gap * (len(shown) - 1)
        full = sum(b.full_width() for b in shown) + gaps
        available -= self.SLACK
        secondary_icons = full > available
        primary_icon = shown[0].full_width() + PillButton.HEIGHT * (len(shown) - 1) + gaps > available
        for i, button in enumerate(shown):
            button.set_icon_only(primary_icon if i == 0 else secondary_icons, animate)

    def _apply_height(self) -> None:
        if self._searching:
            # En ventanas bajas el título cede su sitio: la barra lateral ya dice dónde se está.
            self.title.setVisible(not self._compact)
            self.setFixedHeight(self.SEARCH_HEIGHT_COMPACT if self._compact else self.SEARCH_HEIGHT)
        else:
            self.title.setVisible(True)
            self.setFixedHeight(self.HEIGHT_COMPACT if self._compact else self.HEIGHT)

    def set_facts(self, facts: list[str]) -> None:
        """Datos de la lista como notas separadas (sin puntos ni viñetas)."""
        if self._searching:
            self._facts = []
            self.search_note.setText(facts[0] if facts else "")
            self._fit_facts()
            return
        self._facts = list(facts)
        while len(self._fact_labels) < len(facts):
            label = QLabel()
            label.setObjectName("bandFact")
            label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._fact_labels.append(label)
            self._facts_row.insertWidget(len(self._fact_labels) - 1, label)
        if self._facts_row.count() == len(self._fact_labels):
            self._facts_row.addStretch(1)
        for i, label in enumerate(self._fact_labels):
            if i < len(facts):
                label.setText(facts[i])
        self._fit_facts()

    def _fit_facts(self, animate: bool = False) -> None:
        """Muestra solo las notas que caben enteras; ninguna se corta a media
        palabra. Al cambiar el ancho, las que sobran se funden."""
        available = self._text_width()
        used = 0
        for i, label in enumerate(self._fact_labels):
            if i >= len(self._facts):
                fade_visible(label, False, animate=False)
                continue
            needed = label.sizeHint().width() + (self.FACT_GAP if i else 0)
            fits = i == 0 or used + needed <= available
            fade_visible(label, fits, animate)
            if fits:
                used += needed

    def set_tint(self, tint: Tint) -> None:
        self._color.to(QColor(tint.band))

    def _on_color(self, color) -> None:
        self.cover.set_shadow_color(QColor(color).darker(128))
        self.update()

    def _elide_title(self) -> None:
        self.title.ensurePolished()
        metrics = QFontMetrics(self.title.font())
        # Se mide con el ancho que tendrá la columna, no con el de ahora: durante
        # un arrastre el layout aún no ha recolocado el título.
        width = max(40, min(self._text_width(), self.title.maximumWidth()))
        self.title.setText(metrics.elidedText(self._full_title, Qt.TextElideMode.ElideRight, width))

    def resizeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().resizeEvent(event)
        self._layout_rings()
        # Con un panel deslizándose o al cambiar de vista, el ancho cambia en
        # cada fotograma: animar cada paso haría competir decenas de tweens.
        self._fit_width(animate=not window_settling(self))

    # -- aros de luz --------------------------------------------------------
    # Dos anillos que salen por la esquina, con tramos de oro: la única
    # decoración de la interfaz. Mientras suena música los tramos giran (el de
    # fuera a SPIN_OUTER grados por segundo, el de dentro a SPIN_INNER) y los
    # anillos respiran; la cadencia sigue el reloj de la pista (se para con
    # ella y salta con ella al buscar), no un pulso inventado. En pausa
    # frenan con EASE_OUT hasta quedarse quietos, y entonces no queda ningún
    # temporizador en marcha.
    RING_INNER, RING_OUTER = 0.55, 0.78     # radios, en altos de banda
    RING_CENTER = (0.08, -0.05)              # el centro, fuera de la esquina
    BREATH = 0.012                           # cuánto crece y mengua el radio
    BREATH_S = 7.0                           # periodo de la respiración
    SPIN_OUTER, SPIN_INNER = 14.0, 23.0      # grados por segundo (X e Y)
    CADENCE = 0.22                           # cuánto acelera en lo alto de cada ciclo
    CADENCE_S = 4.0                          # ciclo de la cadencia, en segundos de pista
    ARC_OUTER, ARC_INNER = 24, 16            # largo de cada tramo de oro, en grados
    REST_OUTER, REST_INNER = 214.0, 236.0    # dónde quedan en reposo
    ARCS = 4                                 # tramos por anillo: siempre asoma alguno
    RING_PEN = 1.5
    GOLD_PEN = 3.0

    def retheme(self, previous: str) -> None:
        """Los aros toman la luz del tema (en el oscuro, más suave)."""
        self._pen_inner.setColor(QColor(*theme.AROS, 120))
        self._pen_outer.setColor(QColor(*theme.AROS, 120 if theme.is_dark() else 170))
        self.update()

    def _init_rings(self) -> None:
        self._playing = False
        self._window_shown = True
        self._angles = [self.REST_OUTER, self.REST_INNER]
        self._breath = (0.0, 0.0)
        self._breath_t = 0.0
        self._level = 0.0                    # 0 quietos, 1 a toda velocidad
        self._level_from = self._level_to = 0.0
        self._ramp_at = 0
        self._song_pos = 0.0
        self._song_pos_at = 0
        self._ring_clock = QElapsedTimer()
        self._ring_clock.start()
        self._last_tick = 0
        self._ring_timer = QTimer(self)
        self._ring_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._ring_timer.setInterval(FRAME_MS)
        self._ring_timer.timeout.connect(self._ring_tick)
        # Lo que no cambia de un fotograma a otro se prepara una vez.
        self._pen_inner = QPen(QColor(*theme.AROS, 120), self.RING_PEN)
        self._pen_outer = QPen(QColor(*theme.AROS, 170 if not theme.is_dark() else 120), self.RING_PEN)
        self._pen_gold = QPen(QColor(theme.ORO), self.GOLD_PEN)
        self._pen_gold.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._band_path = QPainterPath()
        self._ring_center = QPointF()
        self._radii = (0.0, 0.0)
        self._rings_rect = QRect()
        self._layout_rings()

    def _layout_rings(self) -> None:
        """Geometría de la banda y de los aros: solo cambia con el tamaño."""
        rect = QRectF(self.rect())
        radius = float(theme.R_PANEL)
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + radius)
        path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
        path.lineTo(rect.right() - radius, rect.top())
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        self._band_path = path
        h = rect.height()
        dx, dy = self.RING_CENTER
        self._ring_center = QPointF(rect.right() + h * dx, rect.top() + h * dy)
        self._radii = (h * self.RING_OUTER, h * self.RING_INNER)
        # Lo único que se repinta en cada fotograma: el cuadro que encierra el
        # anillo de fuera en su punto más grande, con el grosor del oro.
        reach = self._radii[0] * (1 + self.BREATH) + self.GOLD_PEN + 2
        left = math.floor(self._ring_center.x() - reach)
        bottom = math.ceil(self._ring_center.y() + reach)
        self._rings_rect = QRect(left, 0, self.width() - left, bottom).intersected(self.rect())

    def rings_rect(self) -> QRect:
        return QRect(self._rings_rect)

    def rings_running(self) -> bool:
        return self._ring_timer.isActive()

    def set_playing(self, playing: bool) -> None:
        """Con música, los tramos cogen velocidad; sin ella, frenan."""
        if playing == self._playing:
            return
        self._playing = playing
        if reduced_motion():
            return                       # quietos siempre: ni temporizador ni repintados
        now = self._ring_clock.elapsed()
        self._level_from = self._level_at(now)
        self._level_to = 1.0 if playing else 0.0
        self._ramp_at = now
        if playing:
            self._song_pos_at = now
        self._sync_rings()

    def set_position(self, seconds: float) -> None:
        """Segundo de la pista: marca la cadencia del giro."""
        self._song_pos = max(0.0, float(seconds))
        self._song_pos_at = self._ring_clock.elapsed()

    def set_window_shown(self, shown: bool) -> None:
        """La ventana se minimizó o volvió: minimizada no se gasta nada."""
        self._window_shown = shown
        self._sync_rings()

    def showEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().showEvent(event)
        self._sync_rings()

    def hideEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().hideEvent(event)
        self._sync_rings()

    def _level_at(self, now: int) -> float:
        progress = (now - self._ramp_at) / SPIN_MS
        if progress >= 1.0:
            return self._level_to
        eased = EASE_OUT.valueForProgress(max(0.0, progress))
        return self._level_from + (self._level_to - self._level_from) * eased

    def _sync_rings(self) -> None:
        moving = self._playing or self._level > 0.0 or self._level_to > 0.0
        run = (moving and self._window_shown and self.isVisible() and not reduced_motion())
        if run and not self._ring_timer.isActive():
            self._last_tick = self._ring_clock.elapsed()
            self._ring_timer.start()
        elif not run and self._ring_timer.isActive():
            self._ring_timer.stop()

    def _ring_tick(self) -> None:
        now = self._ring_clock.elapsed()
        # Tras un tropiezo (la ventana ocupada) el giro sigue donde iba: no salta.
        dt = min(0.05, max(0.0, (now - self._last_tick) / 1000.0))
        self._last_tick = now
        level = self._level = self._level_at(now)
        song = self._song_pos + ((now - self._song_pos_at) / 1000.0 if self._playing else 0.0)
        cadence = 0.5 - 0.5 * math.cos(math.tau * song / self.CADENCE_S)
        speed = level * (1.0 + self.CADENCE * cadence) * dt
        self._angles[0] = (self._angles[0] - self.SPIN_OUTER * speed) % 360.0
        self._angles[1] = (self._angles[1] - self.SPIN_INNER * speed) % 360.0
        self._breath_t += dt
        phase = math.tau * self._breath_t / self.BREATH_S
        amount = self.BREATH * level
        self._breath = (amount * math.sin(phase), amount * math.sin(phase + math.pi / 2))
        self.update(self._rings_rect)
        if level <= 0.0 and self._level_to <= 0.0 and not self._playing:
            self._level = 0.0
            self._ring_timer.stop()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API de Qt
        # Color plano, sin degradados; solo las esquinas de arriba siguen al panel.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(self._band_path, QColor(self._color.value))
        # Los aros no llegan a las esquinas redondeadas: basta el recorte del widget.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        center = self._ring_center
        outer = self._radii[0] * (1.0 + self._breath[0])
        inner = self._radii[1] * (1.0 + self._breath[1])
        painter.setPen(self._pen_inner)
        painter.drawEllipse(center, inner, inner)
        painter.setPen(self._pen_outer)
        painter.drawEllipse(center, outer, outer)
        painter.setPen(self._pen_gold)
        step = 360.0 / self.ARCS
        for radius, angle, span in ((outer, self._angles[0], self.ARC_OUTER),
                                    (inner, self._angles[1], self.ARC_INNER)):
            box = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
            for k in range(self.ARCS):
                painter.drawArc(box, round((angle + k * step) * 16), span * 16)


class SuggestionStrip(QWidget):
    """Fila de sugerencias bajo el buscador.

    Ocupa siempre el mismo alto, haya o no sugerencias, para que los
    resultados no den saltos mientras se escribe. Las que no caben enteras se
    ocultan. Al escribir, los textos cambian en el sitio y sin animación (se
    repite a cada tecla); solo la primera aparición se funde.
    """

    picked = Signal(str)
    GAP = 8

    def __init__(self, on_band: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._on_band = on_band
        self._inset = 0
        self._chips: list[Chip] = []
        self._count = 0
        self._lead = ""
        self._lead_font = theme.ui_font(12)
        self.setFixedHeight(Chip.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setEnabled(False)
        self.setGraphicsEffect(self._effect)
        self._fade = Tween(self, 1.0, self._apply_fade, ms=FADE_MS, curve=EASE)

    def _apply_fade(self, value) -> None:
        opacity = float(value)
        self._effect.setOpacity(opacity)
        self._effect.setEnabled(opacity < 1.0)   # en reposo se pinta sin capa intermedia

    def set_items(self, texts: list[str], typed: str = "", lead: str = "",
                  glyph: Optional[str] = None, strong: bool = True) -> None:
        was_empty = self._count == 0
        while len(self._chips) < len(texts):
            chip = Chip(self, self._on_band)
            chip.clicked.connect(lambda _checked=False, c=chip: self.picked.emit(c.suggestion()))
            self._chips.append(chip)
        self._count = len(texts)
        self._lead = lead if texts else ""
        self.setAccessibleName(lead or tr("Sugerencias de búsqueda"))
        for chip, text in zip(self._chips, texts):
            chip.set_suggestion(text, typed, glyph, strong)
        self._fit()
        self.update()
        if was_empty and texts:
            self._fade.set(0.0)
            self._fade.to(1.0)

    def shown(self) -> list[str]:
        """Las sugerencias que caben y se ven (en orden)."""
        return [c.suggestion() for c in self._chips[: self._count] if not c.isHidden()]

    def _lead_width(self) -> int:
        if not self._lead:
            return 0
        return QFontMetrics(self._lead_font).horizontalAdvance(self._lead) + 12

    def _fit(self) -> None:
        x = self._inset + self._lead_width()
        fits = True
        for i, chip in enumerate(self._chips):
            fits = fits and i < self._count and x + chip.width() <= self.width()
            chip.setVisible(fits)
            if fits:
                chip.move(x, 0)
                x += chip.width() + self.GAP

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._lead:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self._lead_font)
        painter.setPen(_alpha(theme.PAPEL, 0.66) if self._on_band else QColor(theme.HUMO))
        painter.drawText(
            QRect(self._inset, 0, self._lead_width(), self.height() - 1),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._lead,
        )


# ---------------------------------------------------------------------------
# Filas del inicio
# ---------------------------------------------------------------------------
class ShelfSection(QWidget):
    """Una fila del inicio: título, el porqué en una nota y sus tarjetas."""

    def __init__(self, loader: ArtLoader, max_rows: int = 1, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QVBoxLayout()
        # Las tarjetas llevan 10 px de aire alrededor de la carátula: el texto
        # se alinea con la carátula, no con el borde de la tarjeta.
        head.setContentsMargins(SleeveCard.PAD, 0, SleeveCard.PAD, 0)
        head.setSpacing(2)
        self.title = QLabel()
        self.title.setObjectName("shelfTitle")
        self.note = QLabel()
        self.note.setObjectName("shelfNote")
        for label in (self.title, self.note):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            head.addWidget(label)
        lay.addLayout(head)
        lay.addSpacing(6)
        self.grid = CardGrid(loader, max_rows)
        lay.addWidget(self.grid)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setEnabled(False)
        self.setGraphicsEffect(self._effect)
        self._fade = Tween(self, 1.0, self._apply_fade, ms=FADE_MS + 40, curve=EASE)

    def set_head(self, title: str, note: str = "") -> None:
        self.title.setText(title)
        self.title.setToolTip(title)
        self.note.setText(note)
        self.note.setVisible(bool(note))

    def _apply_fade(self, value) -> None:
        opacity = float(value)
        self._effect.setOpacity(opacity)
        self._effect.setEnabled(opacity < 1.0)

    def appear(self) -> None:
        """Entrada de una fila que acaba de llegar de la red: solo se funde."""
        self._fade.set(0.0)
        self._fade.to(1.0)
