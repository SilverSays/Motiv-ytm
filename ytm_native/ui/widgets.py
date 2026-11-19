"""Piezas reutilizables de la GUI: tareas en segundo plano, modelo de pistas y diálogos."""

from __future__ import annotations

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

from ytm_native.core import config, credentials, paths
from ytm_native.core.db import Database, Track
from ytm_native.core.i18n import tr


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
        return tr("nunca")
    delta = max(0, time.time() - ts)
    if delta < 90:
        return tr("hace un momento")
    if delta < 3600:
        return tr("hace {int} min", int=int(delta // 60))
    if delta < 86400:
        return tr("hace {int} h", int=int(delta // 3600))
    return tr("hace {int} d", int=int(delta // 86400))


# ---------------------------------------------------------------------------
# Modelo de la tabla de pistas
# ---------------------------------------------------------------------------
class TrackModel(QAbstractTableModel):
    """Pistas de la vista. La primera columna la pinta ``TrackDelegate``
    (carátula, título y artista); las otras dos son álbum y duración.

    Una vista puede cambiar el álbum por una nota suya por pista (el historial
    pone cuándo se escuchó o cuántas veces), con su propio título de columna."""

    HEADERS = (tr("Título"), tr("Álbum"), tr("Duración"))

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._tracks: list[Track] = []
        self._rows: dict[str, list[int]] = {}      # videoId -> filas donde está
        self._current_id: Optional[str] = None
        self._notes: Optional[dict[str, str]] = None
        self._note_header = ""

    # Pasado este número de filas distintas, cambiar la lista por partes deja
    # de compensar frente a empezar de cero (otra lista, no la misma retocada).
    DIFF_LIMIT = 64

    def set_tracks(self, tracks: list[Track], notes: Optional[dict[str, str]] = None,
                   note_header: str = "") -> None:
        """``notes``: texto por ``videoId`` que ocupa la columna del álbum.

        Si la lista nueva es la de antes con pocos cambios (la cola tras
        «Reproducir a continuación», una sincronización que añade dos
        canciones), solo se insertan o quitan esas filas: la vista conserva el
        desplazamiento y la selección y no vuelve a hacer aparecer la lista."""
        header = note_header if notes is not None else ""
        changed = header != self._note_header
        tracks = list(tracks)
        if changed or not self._patch(tracks):
            self.beginResetModel()
            self._tracks = tracks
            self._index_rows()
            self.endResetModel()
        self._notes = notes
        self._note_header = header
        if changed:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 1, 1)
        elif notes is not None and self._tracks:
            self.dataChanged.emit(self.index(0, 1), self.index(len(self._tracks) - 1, 1))

    def _patch(self, new: list[Track]) -> bool:
        """Cambia solo el tramo distinto entre la lista actual y ``new``.
        Devuelve False si no se parecen lo bastante (o si no hay lista)."""
        old = self._tracks
        if not old or not new:
            return False
        old_ids = [t.video_id for t in old]
        new_ids = [t.video_id for t in new]
        head = 0
        limit = min(len(old_ids), len(new_ids))
        while head < limit and old_ids[head] == new_ids[head]:
            head += 1
        tail = 0
        while (tail < limit - head
               and old_ids[len(old_ids) - 1 - tail] == new_ids[len(new_ids) - 1 - tail]):
            tail += 1
        removed = len(old_ids) - head - tail
        added = len(new_ids) - head - tail
        if removed + added > self.DIFF_LIMIT or (removed and removed >= len(old_ids) // 2 + 1):
            return False
        if removed:
            self.beginRemoveRows(QModelIndex(), head, head + removed - 1)
            del self._tracks[head:head + removed]
            self.endRemoveRows()
        if added:
            self.beginInsertRows(QModelIndex(), head, head + added - 1)
            self._tracks[head:head] = new[head:head + added]
            self.endInsertRows()
        # Las filas que siguen ahí pueden traer datos nuevos (un título
        # corregido, la marca de no disponible): se toman y se repintan.
        self._tracks = list(new)
        self._index_rows()
        if self._tracks:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._tracks) - 1, len(self.HEADERS) - 1))
        return True

    def _index_rows(self) -> None:
        rows: dict[str, list[int]] = {}
        for row, track in enumerate(self._tracks):
            rows.setdefault(track.video_id, []).append(row)
        self._rows = rows

    def tracks(self) -> list[Track]:
        return list(self._tracks)

    def track_at(self, row: int) -> Track:
        return self._tracks[row]

    def rows_of(self, video_id: Optional[str]) -> list[int]:
        return list(self._rows.get(video_id, ())) if video_id else []

    def is_current(self, row: int) -> bool:
        return self._current_id is not None and self._tracks[row].video_id == self._current_id

    def set_current(self, video_id: Optional[str]) -> None:
        """Mueve la marca de lo que suena: solo se repintan la fila que la
        pierde y la que la gana, no la tabla entera."""
        if video_id == self._current_id:
            return
        previous, self._current_id = self._current_id, video_id
        for row in sorted(set(self.rows_of(previous)) | set(self.rows_of(video_id))):
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.HEADERS) - 1))

    def update_track(self, track: Track) -> None:
        for row in self.rows_of(track.video_id):
            self._tracks[row] = track
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.HEADERS) - 1))

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self._note_header if section == 1 and self._note_header else self.HEADERS[section]
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
                return track.album if self._notes is None else self._notes.get(track.video_id, "")
            return format_time(track.duration_s) if track.duration_s else ""
        if role == Qt.ItemDataRole.ToolTipRole:
            if track.unavailable_since is not None:
                since = datetime.fromtimestamp(track.unavailable_since).strftime("%d/%m/%Y")
                days = config.UNAVAILABLE_RECHECK_S // 86400
                return (
                    tr("No disponible desde el {since}. Se reintentará sola pasados {days} días; un doble "
                       "clic la reintenta ahora.", since=since, days=days)
                )
            if col == 0:
                return f"{track.title}\n{track.artists}" if track.artists else track.title
        return None


# ---------------------------------------------------------------------------
# Diálogo de cuenta: credenciales y modo de reproducción
# ---------------------------------------------------------------------------
BROWSER_NOTE = (
    tr("Usa Firefox para este paso. Chrome en Windows liga la sesión al dispositivo y, copiada "
    "a esta app, deja de valer en pocas horas.")
)

HEADER_STEPS = (
    tr("1. Abre una ventana privada nueva de Firefox, inicia sesión en YouTube con la cuenta "
    "secundaria y entra en https://music.youtube.com.\n"
    "2. Pulsa F12, abre la pestaña Red y escribe «browse» en el filtro.\n"
    "3. La lista empieza vacía: solo registra lo que ocurre con el panel abierto. Haz clic en "
    "Biblioteca o Explorar en la página (o recarga con F5) para que aparezca una petición.\n"
    "4. Busca una fila con método POST y dominio music.youtube.com. Las GET no sirven; si "
    "solo ves GET, sigue navegando.\n"
    "5. Clic derecho sobre esa fila → Copiar valor → Copiar cabeceras de la petición, y "
    "pégalas abajo.")
)

COOKIE_STEPS = (
    tr("1. En esa misma ventana privada ve a https://www.youtube.com/robots.txt (debe ser la "
    "única pestaña privada abierta).\n"
    "2. Exporta las cookies de youtube.com en formato Netscape con una extensión de "
    "exportación de cookies.\n"
    "3. Cierra la ventana privada sin cerrar sesión y no vuelvas a abrir esa sesión en el "
    "navegador: YouTube rota las cookies de las pestañas abiertas.")
)

CLOSE_NOTE = (
    tr("Aunque no exportes cookies, al terminar cierra la ventana privada sin cerrar sesión.")
)


def _file_status(path: Path) -> str:
    if not path.is_file():
        return tr("No configurado")
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime(tr("%d/%m/%Y %H:%M"))
    if not credentials.readable(path):
        return tr("No se puede leer ({stamp}): guárdalo de nuevo", stamp=stamp)
    sealed = tr("cifrado para tu usuario de Windows") if credentials.is_sealed_file(path) else tr("aún sin cifrar")
    return tr("Configurado ({stamp}, {sealed})", stamp=stamp, sealed=sealed)


def browser_headers_from_raw(raw: str) -> dict:
    """Las cabeceras de ytmusicapi a partir del texto copiado del navegador,
    ya saneadas y sin escribir nada en disco."""
    import json

    import ytmusicapi

    from ytm_native.services.sync import sanitize_browser_headers

    clean = sanitize_browser_headers(json.loads(ytmusicapi.setup(headers_raw=raw)))
    if "cookie" not in {k.lower() for k in clean}:
        raise ValueError(
            tr("las cabeceras no incluyen la línea «cookie»: copia la sección Request "
            "Headers completa de una petición «browse» con la sesión iniciada")
        )
    return clean


class AccountDialog(QDialog):
    credentials_changed = Signal()
    firefox_login_requested = Signal()

    def __init__(self, db: Database, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle(tr("Cuenta y reproducción"))
        # El contenido se desplaza: el diálogo cabe también en pantallas de 768 px de alto.
        self.setMinimumSize(620, 480)
        self.resize(720, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 14)
        layout.setSpacing(12)
        tabs = QTabWidget()
        tabs.addTab(self._scrolled(self._credentials_tab()), tr("Credenciales"))
        tabs.addTab(self._scrolled(self._playback_tab()), tr("Reproducción"))
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("Cerrar"))
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
            tr("Usa siempre la cuenta secundaria. Los archivos se guardan en {cred_dir} (fuera de la "
               "carpeta del proyecto).", cred_dir=paths.cred_dir())
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        lay.addWidget(note)

        browser_note = QLabel(BROWSER_NOTE)
        browser_note.setWordWrap(True)
        lay.addWidget(browser_note)
        firefox = QPushButton(tr("Iniciar sesión con Firefox (recomendado)…"))
        firefox.setToolTip(tr("Sin copiar cabeceras: inicias sesión en una ventana de Firefox aparte "
                           "y la app recoge la sesión al cerrarla."))
        firefox.clicked.connect(self._ask_firefox_login)
        lay.addWidget(firefox, alignment=Qt.AlignmentFlag.AlignLeft)

        lay.addWidget(self._heading(tr("Biblioteca (ytmusicapi)")))
        self.headers_status = QLabel()
        self.headers_status.setObjectName("mutedLabel")
        lay.addWidget(self.headers_status)
        steps = QLabel(HEADER_STEPS)
        steps.setWordWrap(True)
        lay.addWidget(steps)
        self.headers_edit = QPlainTextEdit()
        self.headers_edit.setPlaceholderText(tr("Pega aquí las cabeceras de la petición…"))
        self.headers_edit.setFixedHeight(96)
        # Tab pasa al siguiente control: las cabeceras se pegan, no se teclean
        # tabuladores a mano, y sin esto el foco quedaba atrapado en el campo.
        self.headers_edit.setTabChangesFocus(True)
        lay.addWidget(self.headers_edit)
        save = QPushButton(tr("Guardar cabeceras"))
        save.clicked.connect(self._save_headers)
        lay.addWidget(save, alignment=Qt.AlignmentFlag.AlignLeft)

        lay.addWidget(self._heading(tr("Reproducción (cookies.txt para yt-dlp)")))
        self.cookies_status = QLabel()
        self.cookies_status.setObjectName("mutedLabel")
        lay.addWidget(self.cookies_status)
        csteps = QLabel(COOKIE_STEPS)
        csteps.setWordWrap(True)
        lay.addWidget(csteps)
        pick = QPushButton(tr("Seleccionar cookies.txt…"))
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

    def _ask_firefox_login(self) -> None:
        self.firefox_login_requested.emit()
        self.accept()

    def _save_headers(self) -> None:
        raw = self.headers_edit.toPlainText().strip()
        if not raw:
            QMessageBox.information(self, tr("Cabeceras"), tr("Pega primero las cabeceras de la petición."))
            return
        try:
            import json

            # Chrome copia entradas que no deben reenviarse (content-encoding,
            # pseudo-cabeceras…): se guardan ya saneadas, y cifradas: el texto
            # en claro nunca llega al disco.
            clean = browser_headers_from_raw(raw)
            credentials.write_text(paths.browser_json(), json.dumps(clean, ensure_ascii=False, indent=1))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("Cabeceras"), tr("No se pudieron procesar las cabeceras:\n{exc}", exc=exc))
            return
        from ytm_native.services.sync import DEVICE_BOUND_WARNING, looks_device_bound

        self.headers_edit.clear()
        self._refresh_status()
        self.credentials_changed.emit()
        if looks_device_bound(raw):
            QMessageBox.warning(self, tr("Cabeceras"), tr("Cabeceras guardadas.\n\n") + DEVICE_BOUND_WARNING)
        else:
            QMessageBox.information(self, tr("Cabeceras"), tr("Cabeceras guardadas."))

    def _pick_cookies(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, tr("Seleccionar cookies.txt"), str(Path.home()), tr("Cookies (*.txt);;Todos (*.*)")
        )
        if not filename:
            return
        try:
            data = Path(filename).read_bytes()
        except OSError as exc:
            QMessageBox.warning(self, tr("Cookies"), tr("No se pudo leer el archivo:\n{exc}", exc=exc))
            return
        whole = data.decode("utf-8", errors="replace")
        head = whole[:4000]
        looks_netscape = "Netscape" in head or any(line.count("\t") >= 6 for line in head.splitlines())
        if not looks_netscape:
            QMessageBox.warning(
                self, tr("Cookies"), tr("El archivo no parece estar en formato Netscape (cookies.txt).")
            )
            return
        try:
            # Se guarda una copia cifrada; el archivo elegido no se toca.
            credentials.write_bytes(paths.cookies_txt(), data)
        except Exception as exc:  # noqa: BLE001 - p. ej. sin cifrado disponible
            QMessageBox.warning(self, tr("Cookies"), tr("No se pudieron guardar las cookies:\n{exc}", exc=exc))
            return
        self._refresh_status()
        self.credentials_changed.emit()
        from ytm_native.services.sync import DEVICE_BOUND_WARNING, looks_device_bound

        tail = (tr("\n"
                   "\n"
                   "Se guardó una copia cifrada: ya puedes borrar el archivo original ({name}).", name=Path(filename).name))
        if looks_device_bound(whole):
            QMessageBox.warning(self, tr("Cookies"), tr("Cookies guardadas.\n\n") + DEVICE_BOUND_WARNING + tail)
        else:
            QMessageBox.information(self, tr("Cookies"), tr("Cookies guardadas.") + tail)

    # -- pestaña de reproducción -------------------------------------------
    def _playback_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 16, 12, 8)
        lay.setSpacing(10)
        lay.addWidget(self._heading(tr("Modo de resolución de yt-dlp")))

        self.mode_buttons: dict[str, QRadioButton] = {}
        options = (
            ("hybrid", tr("Híbrido (recomendado): anónimo, y cookies solo ante un bloqueo anti-bot "
                       "o una restricción de edad.")),
            ("cookies", tr("Siempre con cookies de la cuenta secundaria.")),
            ("anon", tr("Siempre anónimo: nunca pasa cookies a yt-dlp.")),
        )
        current = self.db.get_setting("playback_mode", "hybrid")
        for key, text in options:
            radio = QRadioButton(text)
            radio.setChecked(key == current)
            radio.toggled.connect(lambda checked, k=key: checked and self.db.set_setting("playback_mode", k))
            self.mode_buttons[key] = radio
            lay.addWidget(radio)

        lay.addSpacing(8)
        lay.addWidget(self._heading(tr("Riesgo de la cuenta")))
        warn = QLabel(
            tr("yt-dlp advierte que usar una cuenta puede llevar a un baneo temporal o permanente "
            "de esa cuenta. La cuenta secundaria limita el impacto, pero no lo elimina, y este "
            "uso va contra los términos de servicio de YouTube. Mientras no marques la casilla, "
            "yt-dlp nunca recibirá las cookies (la app funcionará solo en modo anónimo).")
        )
        warn.setWordWrap(True)
        lay.addWidget(warn)
        self.risk_check = QCheckBox(tr("Acepto el riesgo de usar las cookies de la cuenta secundaria con yt-dlp"))
        self.risk_check.setChecked(self.db.get_bool("cookies_risk_accepted"))
        self.risk_check.toggled.connect(
            lambda checked: self.db.set_setting("cookies_risk_accepted", "1" if checked else "0")
        )
        lay.addWidget(self.risk_check)
        lay.addStretch(1)
        return tab
