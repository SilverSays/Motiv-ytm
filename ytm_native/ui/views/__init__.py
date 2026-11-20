"""Vistas de la ventana principal, cada una en su módulo (Tarea 10.6).

Son mezclas de ``MainWindow``: la ventana coordina (barra lateral, paneles,
reproductor, avisos) y cada vista aporta los métodos de lo suyo.
"""

from .queue_view import QueueView
from .search_view import SearchView
from .history_view import HistoryView
from .local_view import LocalView
from .playlist_view import PlaylistView

__all__ = ["QueueView", "SearchView", "HistoryView", "LocalView", "PlaylistView"]
