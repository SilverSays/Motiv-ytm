"""Diálogos de la cuenta: el inicio de sesión guiado con Firefox."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ytm_native.services.auth_assistant import FirefoxLogin
from ytm_native.core.i18n import tr

STEPS = (
    tr("1. Pulsa «Abrir Firefox». Se abre una ventana de Firefox aparte, vacía: no ve tus "
    "pestañas, cuentas ni extensiones.\n"
    "2. Inicia sesión en YouTube Music con la cuenta secundaria y espera a ver tu biblioteca.\n"
    "3. Cierra esa ventana de Firefox. La app recoge la sesión, comprueba que funciona, la "
    "guarda cifrada para tu usuario de Windows y borra del disco todo lo de esa ventana.")
)

WHY_NOT_CHROME = (
    tr("¿Por qué Firefox y no Chrome? Las versiones recientes de Chrome ligan la sesión al "
    "dispositivo (Device Bound Session Credentials: cookies que caducan en minutos y solo "
    "ese Chrome puede renovar) y cifran sus cookies con una clave que solo Chrome puede usar "
    "(cifrado «app-bound» de DPAPI v20). Una sesión sacada de Chrome deja de valer en pocas "
    "horas fuera de él.")
)

NO_FIREFOX = (
    tr("No se encontró Firefox en este equipo. Instálalo desde mozilla.org (no hace falta que "
    "sea tu navegador habitual) o usa la importación manual de cabeceras.")
)


class FirefoxLoginDialog(QDialog):
    """Explica el proceso, lanza Firefox y dice en qué punto está."""

    credentials_changed = Signal()
    manual_requested = Signal()

    def __init__(self, login: Optional[FirefoxLogin] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Iniciar sesión con Firefox"))
        self.setMinimumWidth(560)
        self.login = login or FirefoxLogin(parent=self)
        self.login.waiting.connect(self._on_waiting)
        self.login.checking.connect(self._on_checking)
        self.login.finished.connect(self._on_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        title = QLabel(tr("Inicia sesión sin copiar cabeceras"))
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        layout.addWidget(steps)
        why = QLabel(WHY_NOT_CHROME)
        why.setObjectName("mutedLabel")
        why.setWordWrap(True)
        layout.addWidget(why)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.open_btn = QPushButton(tr("Abrir Firefox"))
        self.open_btn.setDefault(True)
        self.open_btn.clicked.connect(self._start)
        self.manual_btn = QPushButton(tr("Importar cabeceras a mano…"))
        self.manual_btn.setToolTip(tr("Para usuarios avanzados o equipos sin Firefox."))
        self.manual_btn.clicked.connect(self._manual)
        self.close_btn = QPushButton(tr("Cerrar"))
        self.close_btn.clicked.connect(self.reject)
        row.addWidget(self.open_btn)
        row.addWidget(self.manual_btn)
        row.addStretch(1)
        row.addWidget(self.close_btn)
        layout.addLayout(row)

        if not self.login.available:
            self.open_btn.setEnabled(False)
            self.status.setText(NO_FIREFOX)
            self.manual_btn.setDefault(True)

    # -- pasos ------------------------------------------------------------
    def _start(self) -> None:
        self.status.setText(tr("Abriendo Firefox…"))
        self.login.start()

    def _on_waiting(self) -> None:
        self.open_btn.setEnabled(False)
        self.manual_btn.setEnabled(False)
        self.close_btn.setText(tr("Cancelar"))
        self.status.setText(tr("Inicia sesión en la ventana de Firefox y ciérrala cuando veas tu biblioteca. "
                            "Cerrar este cuadro cancela el inicio de sesión."))

    def _on_checking(self) -> None:
        self.close_btn.setEnabled(False)
        self.status.setText(tr("Comprobando la sesión…"))

    def _on_finished(self, ok: bool, message: str) -> None:
        self.status.setText(message)
        self.open_btn.setEnabled(self.login.available)
        self.manual_btn.setEnabled(True)
        self.close_btn.setEnabled(True)
        self.close_btn.setText(tr("Cerrar"))
        if ok:
            self.open_btn.setText(tr("Volver a iniciar sesión"))
            self.credentials_changed.emit()

    def _manual(self) -> None:
        self.manual_requested.emit()
        self.accept()

    # -- cierre ------------------------------------------------------------
    def reject(self) -> None:
        # Cerrar o pulsar Escape a mitad cancela: Firefox se cierra y su
        # perfil se borra. Mientras se comprueba la sesión, se espera.
        if self.login.busy:
            if not self.close_btn.isEnabled():
                return
            self.login.cancel()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        if self.login.busy and not self.close_btn.isEnabled():
            event.ignore()
            return
        if self.login.busy:
            self.login.cancel()
        super().closeEvent(event)
