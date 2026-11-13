"""Piezas reutilizables de la GUI: tareas en segundo plano, modelo de pistas y diálogos."""

from __future__ import annotations

import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ytm_native.core import config, paths
from ytm_native.core.db import Database, Track


# ---------------------------------------------------------------------------
# Tareas en segundo plano
# ---------------------------------------------------------------------------
class _Task(QObject):
    """Ejecuta ``fn`` en un hilo y entrega el resultado en el hilo de la GUI."""

    _finished = Signal(object, object)

    def __init__(self, fn: Callable, on_done: Optional[Callable], registry: set):
        super().__init__()
        self._fn = fn
        self._on_done = on_done
        self._registry = registry
        self._finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        self._registry.add(self)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        result, error = None, None
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            error = exc
        self._finished.emit(result, error)

    @Slot(object, object)
    def _deliver(self, result, error) -> None:
        self._registry.discard(self)
        if self._on_done is not None:
            self._on_done(result, error)


_TASKS: set = set()


def run_async(fn: Callable, on_done: Optional[Callable] = None) -> None:
    """``on_done(resultado, error)`` se llama en el hilo de la GUI."""
    _Task(fn, on_done, _TASKS).start()


# ---------------------------------------------------------------------------
# Utilidades de formato
# ---------------------------------------------------------------------------
def format_time(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "–:––"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_ago(ts: Optional[float]) -> str:
    if not ts:
        return "nunca"
    delta = max(0, time.time() - ts)
    if delta < 90:
        return "hace un momento"
    if delta < 3600:
        return f"hace {int(delta // 60)} min"
    if delta < 86400:
        return f"hace {int(delta // 3600)} h"
    return f"hace {int(delta // 86400)} d"


# ---------------------------------------------------------------------------
# Modelo de la tabla de pistas
# ---------------------------------------------------------------------------
class TrackModel(QAbstractTableModel):
    """Pistas de la vista. La primera columna la pinta ``TrackDelegate``
    (carátula, título y artista); las otras dos son álbum y duración."""

    HEADERS = ("Título", "Álbum", "Duración")

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._tracks: list[Track] = []
        self._current_id: Optional[str] = None

    def set_tracks(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        self._tracks = list(tracks)
        self.endResetModel()

    def tracks(self) -> list[Track]:
        return list(self._tracks)

    def track_at(self, row: int) -> Track:
        return self._tracks[row]

    def is_current(self, row: int) -> bool:
        return self._current_id is not None and self._tracks[row].video_id == self._current_id

    def set_current(self, video_id: Optional[str]) -> None:
        if video_id != self._current_id:
            self._current_id = video_id
            self._repaint()

    def update_track(self, track: Track) -> None:
        for row, existing in enumerate(self._tracks):
            if existing.video_id == track.video_id:
                self._tracks[row] = track
        self._repaint()

    def _repaint(self) -> None:
        if self._tracks:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._tracks) - 1, len(self.HEADERS) - 1)
            )

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.HEADERS[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                right = section == 2
                return int((Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft)
                           | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        track = self._tracks[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return track.title
            if col == 1:
                return track.album
            return format_time(track.duration_s) if track.duration_s else ""
        if role == Qt.ItemDataRole.ToolTipRole:
            if track.unavailable_since is not None:
                since = datetime.fromtimestamp(track.unavailable_since).strftime("%d/%m/%Y")
                days = config.UNAVAILABLE_RECHECK_S // 86400
                return (
                    f"No disponible desde el {since}. Se reintentará sola pasados {days} días; "
                    "un doble clic la reintenta ahora."
                )
            if col == 0:
                return f"{track.title}\n{track.artists}" if track.artists else track.title
        return None


# ---------------------------------------------------------------------------
# Diálogo de cuenta: credenciales y modo de reproducción
# ---------------------------------------------------------------------------
BROWSER_NOTE = (
    "Usa Firefox para este paso. Chrome en Windows liga la sesión al dispositivo y, copiada "
    "a esta app, deja de valer en pocas horas."
)

HEADER_STEPS = (
    "1. Abre una ventana privada nueva de Firefox, inicia sesión en YouTube con la cuenta "
    "secundaria y entra en https://music.youtube.com.\n"
    "2. Pulsa F12, abre la pestaña Red y escribe «browse» en el filtro.\n"
    "3. La lista empieza vacía: solo registra lo que ocurre con el panel abierto. Haz clic en "
    "Biblioteca o Explorar en la página (o recarga con F5) para que aparezca una petición.\n"
    "4. Busca una fila con método POST y dominio music.youtube.com. Las GET no sirven; si "
    "solo ves GET, sigue navegando.\n"
    "5. Clic derecho sobre esa fila → Copiar valor → Copiar cabeceras de la petición, y "
    "pégalas abajo."
)

COOKIE_STEPS = (
    "1. En esa misma ventana privada ve a https://www.youtube.com/robots.txt (debe ser la "
    "única pestaña privada abierta).\n"
    "2. Exporta las cookies de youtube.com en formato Netscape con una extensión de "
    "exportación de cookies.\n"
    "3. Cierra la ventana privada sin cerrar sesión y no vuelvas a abrir esa sesión en el "
    "navegador: YouTube rota las cookies de las pestañas abiertas."
)

CLOSE_NOTE = (
    "Aunque no exportes cookies, al terminar cierra la ventana privada sin cerrar sesión."
)


def _file_status(path: Path) -> str:
    if not path.is_file():
        return "No configurado"
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
    return f"Configurado ({stamp})"


class AccountDialog(QDialog):
    credentials_changed = Signal()

    def __init__(self, db: Database, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Cuenta y reproducción")
        # El contenido se desplaza: el diálogo cabe también en pantallas de 768 px de alto.
        self.setMinimumSize(620, 480)
        self.resize(720, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 14)
        layout.setSpacing(12)
        tabs = QTabWidget()
        tabs.addTab(self._scrolled(self._credentials_tab()), "Credenciales")
        tabs.addTab(self._scrolled(self._playback_tab()), "Reproducción")
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Cerrar")
        layout.addWidget(buttons)

    @staticmethod
    def _scrolled(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    # -- pestaña de credenciales -------------------------------------------
    def _credentials_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 16, 12, 8)
        lay.setSpacing(10)

        note = QLabel(
            "Usa siempre la cuenta secundaria. Los archivos se guardan en "
            f"{paths.cred_dir()} (fuera de la carpeta del proyecto)."
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        lay.addWidget(note)

        browser_note = QLabel(BROWSER_NOTE)
        browser_note.setWordWrap(True)
        lay.addWidget(browser_note)

        lay.addWidget(self._heading("Biblioteca (ytmusicapi)"))
        self.headers_status = QLabel()
        self.headers_status.setObjectName("mutedLabel")
        lay.addWidget(self.headers_status)
        steps = QLabel(HEADER_STEPS)
        steps.setWordWrap(True)
        lay.addWidget(steps)
        self.headers_edit = QPlainTextEdit()
        self.headers_edit.setPlaceholderText("Pega aquí las cabeceras de la petición…")
        self.headers_edit.setFixedHeight(96)
        lay.addWidget(self.headers_edit)
        save = QPushButton("Guardar cabeceras")
        save.clicked.connect(self._save_headers)
        lay.addWidget(save, alignment=Qt.AlignmentFlag.AlignLeft)

        lay.addWidget(self._heading("Reproducción (cookies.txt para yt-dlp)"))
        self.cookies_status = QLabel()
        self.cookies_status.setObjectName("mutedLabel")
        lay.addWidget(self.cookies_status)
        csteps = QLabel(COOKIE_STEPS)
        csteps.setWordWrap(True)
        lay.addWidget(csteps)
        pick = QPushButton("Seleccionar cookies.txt…")
        pick.clicked.connect(self._pick_cookies)
        lay.addWidget(pick, alignment=Qt.AlignmentFlag.AlignLeft)
        close_note = QLabel(CLOSE_NOTE)
        close_note.setObjectName("mutedLabel")
        close_note.setWordWrap(True)
        lay.addWidget(close_note)
        lay.addStretch(1)
        self._refresh_status()
        return tab

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _refresh_status(self) -> None:
        self.headers_status.setText(_file_status(paths.browser_json()))
        self.cookies_status.setText(_file_status(paths.cookies_txt()))

    def _save_headers(self) -> None:
        raw = self.headers_edit.toPlainText().strip()
        if not raw:
            QMessageBox.information(self, "Cabeceras", "Pega primero las cabeceras de la petición.")
            return
        try:
            import ytmusicapi

            paths.cred_dir().mkdir(parents=True, exist_ok=True)
            ytmusicapi.setup(filepath=str(paths.browser_json()), headers_raw=raw)
            # Chrome copia entradas que no deben reenviarse (content-encoding,
            # pseudo-cabeceras…): se guardan ya saneadas.
            import json

            from ytm_native.services.sync import sanitize_browser_headers

            target = paths.browser_json()
            clean = sanitize_browser_headers(json.loads(target.read_text(encoding="utf-8")))
            lowered = {k.lower() for k in clean}
            if "cookie" not in lowered:
                target.unlink(missing_ok=True)
                raise ValueError(
                    "las cabeceras no incluyen la línea «cookie»: copia la sección Request "
                    "Headers completa de una petición «browse» con la sesión iniciada"
                )
            target.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Cabeceras", f"No se pudieron procesar las cabeceras:\n{exc}")
            return
        from ytm_native.services.sync import DEVICE_BOUND_WARNING, looks_device_bound

        self.headers_edit.clear()
        self._refresh_status()
        self.credentials_changed.emit()
        if looks_device_bound(raw):
            QMessageBox.warning(self, "Cabeceras", "Cabeceras guardadas.\n\n" + DEVICE_BOUND_WARNING)
        else:
            QMessageBox.information(self, "Cabeceras", "Cabeceras guardadas.")

    def _pick_cookies(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar cookies.txt", str(Path.home()), "Cookies (*.txt);;Todos (*.*)"
        )
        if not filename:
            return
        try:
            head = Path(filename).read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError as exc:
            QMessageBox.warning(self, "Cookies", f"No se pudo leer el archivo:\n{exc}")
            return
        looks_netscape = "Netscape" in head or any(line.count("\t") >= 6 for line in head.splitlines())
        if not looks_netscape:
            QMessageBox.warning(
                self, "Cookies", "El archivo no parece estar en formato Netscape (cookies.txt)."
            )
            return
        paths.cred_dir().mkdir(parents=True, exist_ok=True)
        shutil.copyfile(filename, paths.cookies_txt())
        self._refresh_status()
        self.credentials_changed.emit()
        from ytm_native.services.sync import DEVICE_BOUND_WARNING, looks_device_bound

        try:
            whole = Path(filename).read_text(encoding="utf-8", errors="replace")
        except OSError:
            whole = head
        if looks_device_bound(whole):
            QMessageBox.warning(self, "Cookies", "Cookies guardadas.\n\n" + DEVICE_BOUND_WARNING)

    # -- pestaña de reproducción -------------------------------------------
    def _playback_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 16, 12, 8)
        lay.setSpacing(10)
        lay.addWidget(self._heading("Modo de resolución de yt-dlp"))

        self.mode_buttons: dict[str, QRadioButton] = {}
        options = (
            ("hybrid", "Híbrido (recomendado): anónimo, y cookies solo ante un bloqueo anti-bot "
                       "o una restricción de edad."),
            ("cookies", "Siempre con cookies de la cuenta secundaria."),
            ("anon", "Siempre anónimo: nunca pasa cookies a yt-dlp."),
        )
        current = self.db.get_setting("playback_mode", "hybrid")
        for key, text in options:
            radio = QRadioButton(text)
            radio.setChecked(key == current)
            radio.toggled.connect(lambda checked, k=key: checked and self.db.set_setting("playback_mode", k))
            self.mode_buttons[key] = radio
            lay.addWidget(radio)

        lay.addSpacing(8)
        lay.addWidget(self._heading("Riesgo de la cuenta"))
        warn = QLabel(
            "yt-dlp advierte que usar una cuenta puede llevar a un baneo temporal o permanente "
            "de esa cuenta. La cuenta secundaria limita el impacto, pero no lo elimina, y este "
            "uso va contra los términos de servicio de YouTube. Mientras no marques la casilla, "
            "yt-dlp nunca recibirá las cookies (la app funcionará solo en modo anónimo)."
        )
        warn.setWordWrap(True)
        lay.addWidget(warn)
        self.risk_check = QCheckBox("Acepto el riesgo de usar las cookies de la cuenta secundaria con yt-dlp")
        self.risk_check.setChecked(self.db.get_bool("cookies_risk_accepted"))
        self.risk_check.toggled.connect(
            lambda checked: self.db.set_setting("cookies_risk_accepted", "1" if checked else "0")
        )
        lay.addWidget(self.risk_check)
        lay.addStretch(1)
        return tab
