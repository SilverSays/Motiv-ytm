"""Las listas: las de la biblioteca (locales y de la cuenta), las ajenas que
se abren desde el inicio y el filtro y orden de la tabla (Tarea 10.3).

Es una mezcla de ``MainWindow``: sus métodos usan los controles y el estado
de la ventana (``self.model``, ``self.table``, ``self.band``, ``self.player``…).
"""

from __future__ import annotations

import logging
import random
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from ytm_native.services import discover
from ytm_native.services.discover import PlaylistCard, RemotePlaylist
from ytm_native.core.db import Track, is_local_playlist
from ..art import sized_url
from ..widgets import format_ago, run_async
from ytm_native.core.i18n import plural, tr
from .common import SEARCH_KEY, QUEUE_KEY, HISTORY_KEY, REMOTE_PREFIX, format_total

log = logging.getLogger("ytmn.ui")


class PlaylistView:
    """Lo de las listas y su filtro, como parte de ``MainWindow``."""

    def _refresh_library_view(self, key: str) -> None:
        """La lista a la vista tras sincronizar: filas nuevas o quitadas sin
        volver arriba, y la cabecera solo si cambió."""
        playlist = self.db.get_playlist(key)
        tracks = self.db.playlist_tracks(key)
        self.model.set_tracks(tracks)
        current = self.player.current_track()
        self.model.set_current(current.video_id if current else None)
        description = playlist.description.strip()
        if len(description) > 230:
            description = description[:228].rstrip() + "…"
        art = sized_url(self.db.playlist_cover(playlist), 400)
        facts = self._playlist_facts(playlist, tracks)
        header = (playlist.title, description, art)
        if header != (self.band._full_title, self.band.description.text(), self._band_art):
            self._band_art = art
            self.band.set_content(
                playlist.title, description, facts, art, show_sync=not playlist.local,
                extra=(tr("Opciones"), "more") if playlist.local else None,
            )
            self._apply_band_tint()
        else:
            self.band.set_facts(facts)
        if not playlist.local:
            self._set_empty("" if tracks else tr("Esta lista aún no tiene pistas guardadas"),
                            tr("Sincronízala para verlas."))
        self._update_buttons()

    def _create_local_playlist(self, tracks: tuple = (), suggested: str = "") -> Optional[str]:
        """Pregunta el nombre y crea la lista. Devuelve su id (o ``None``)."""
        name = self._ask_text(
            tr("Nueva lista"),
            tr("Nombre de la lista. Se guarda solo en esta app, no en tu cuenta."),
            suggested, ok=tr("Crear"),
        )
        if not name:
            return None
        playlist_id = self.db.create_local_playlist(name, tracks)
        self._reload_sidebar()
        if tracks:
            self._notice(tr("Lista «{name}» creada con {plural}.", name=name, plural=plural(len(tracks), "{n} canción", "{n} canciones")))
        else:
            self._navigate(playlist_id)
        return playlist_id

    def _add_local_actions(self, menu: QMenu, playlist_id: str) -> None:
        menu.addAction(tr("Cambiar el nombre…"), lambda: self._rename_local_playlist(playlist_id))
        menu.addAction(tr("Eliminar la lista…"), lambda: self._delete_local_playlist(playlist_id))
        if self.account.enabled:
            menu.addAction(tr("Copiar en tu cuenta de YouTube Music…"), lambda: self._copy_to_account(playlist_id))

    def _rename_local_playlist(self, playlist_id: str) -> None:
        playlist = self.db.get_playlist(playlist_id)
        if playlist is None:
            return
        name = self._ask_text(tr("Cambiar el nombre"), tr("Nombre de la lista:"), playlist.title, ok=tr("Guardar"))
        if name and name != playlist.title:
            self.db.rename_playlist(playlist_id, name)
            self._reload_sidebar()

    def _delete_local_playlist(self, playlist_id: str) -> None:
        playlist = self.db.get_playlist(playlist_id)
        if playlist is None:
            return
        if not self._confirm(
            tr("Eliminar la lista"),
            tr("«{title}» se eliminará de esta app. Sus canciones no se borran de ningún otro sitio. No se "
               "puede deshacer.", title=playlist.title),
            tr("Eliminar"),
        ):
            return
        self.db.delete_local_playlist(playlist_id)
        self._reload_sidebar()            # si era la vista actual, vuelve al inicio
        self._notice(tr("Lista «{title}» eliminada.", title=playlist.title))

    def _add_to_local_playlist(self, playlist_id: str, track: Track) -> None:
        playlist = self.db.get_playlist(playlist_id)
        if playlist is None:
            return
        if self.db.add_to_local_playlist(playlist_id, track):
            self._notice(tr("«{title}» añadida a «{title2}».", title=track.title, title2=playlist.title))
            self._after_local_change(playlist_id)
        else:
            self._notice(tr("«{title}» ya estaba en «{title2}».", title=track.title, title2=playlist.title))

    def _add_tracks_to_local_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        playlist = self.db.get_playlist(playlist_id)
        if playlist is None or not tracks:
            return
        added = self.db.add_tracks_to_local_playlist(playlist_id, tracks)
        if added:
            self._notice(plural(added, "{n} canción añadida a «{title}».", "{n} canciones añadidas a «{title}».",
                                title=playlist.title))
            self._after_local_change(playlist_id)
        else:
            self._notice(tr("Ya estaban todas en «{title}».", title=playlist.title))

    def _remove_from_local_playlist(self, playlist_id: str, track: Track) -> None:
        if self.db.remove_from_local_playlist(playlist_id, track.video_id):
            self._notice(tr("«{title}» quitada de la lista.", title=track.title))
            self._after_local_change(playlist_id)

    def _remove_tracks_from_local_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        """Quita varias de una vez (Supr o el menú de una selección). Con más
        de una se pregunta antes: un Supr de más no debe vaciar una lista."""
        if len(tracks) <= 1:
            for track in tracks:
                self._remove_from_local_playlist(playlist_id, track)
            return
        playlist = self.db.get_playlist(playlist_id)
        if playlist is None or not self._confirm(
            tr("Quitar de la lista"),
            tr("¿Quitar {plural} de «{title}»? No se borran de ningún otro sitio.",
               plural=plural(len(tracks), "{n} canción", "{n} canciones"), title=playlist.title),
            tr("Quitar"),
        ):
            return
        removed = self.db.remove_tracks_from_local_playlist(playlist_id, [t.video_id for t in tracks])
        if removed:
            self._notice(plural(removed, "{n} canción quitada de la lista.", "{n} canciones quitadas de la lista."))
            self._after_local_change(playlist_id)

    def _after_local_change(self, playlist_id: str) -> None:
        """La lista cambió: se refresca donde se esté viendo (y su carátula)."""
        scroll = self.table.verticalScrollBar().value()
        self._reload_sidebar()
        if self._view_key == playlist_id:
            self.table.verticalScrollBar().setValue(scroll)

    def _playlist_facts(self, playlist, tracks: list[Track]) -> list[str]:
        facts = [plural(len(tracks), "{n} canción", "{n} canciones")]
        total = playlist.duration_s or sum(t.duration_s or 0 for t in tracks)
        if format_total(total):
            facts.append(format_total(total))
        if playlist.author:
            facts.append(tr("De {author}", author=playlist.author))
        if playlist.year:
            facts.append(playlist.year)
        if playlist.local:
            facts.append(tr("Lista local"))          # vive solo en esta app: no hay nada que sincronizar
            return facts
        facts.append(tr("Sincronizada {format_ago}", format_ago=format_ago(playlist.last_full_sync)))
        if playlist.last_error:
            facts.append(tr("Error: {value}", value=playlist.last_error[:60]))
        return facts

    # ------------------------------------------------- filtro y orden de la lista
    def _on_filter_text(self, text: str) -> None:
        self.proxy.set_filter_text(text)
        self._sync_list_tools()

    def _filter_to_table(self) -> None:
        """Flecha abajo en el filtro: a la primera fila que queda."""
        if self.proxy.rowCount():
            self.table.setFocus(Qt.FocusReason.TabFocusReason)
            self.table.selectRow(0)

    def _sync_list_tools(self) -> None:
        """El filtro a la vista donde hay una lista que filtrar, el aviso si
        no queda ninguna fila y el arrastre solo con la lista tal cual es."""
        key = self._view_key
        filtering = self.proxy.filter_text_active()
        listed = key != SEARCH_KEY and (self.model.rowCount() > 0 or filtering)
        self.list_filter.setVisible(listed)
        nothing = listed and filtering and self.proxy.rowCount() == 0
        if nothing:
            self.filter_note.setText(tr("Ninguna canción coincide con «{text}».", text=self.list_filter.text().strip()))
        self.filter_note.setVisible(nothing)
        self.tools_box.setVisible(listed or key == HISTORY_KEY)
        # Solo la cola y las listas locales se ordenan arrastrando (el orden de
        # una lista de la cuenta, un álbum o una búsqueda no es de esta app), y
        # solo sin filtro ni orden por columna: si no, las filas que se ven no
        # son las posiciones que se guardan.
        own_order = key == QUEUE_KEY or is_local_playlist(key)
        self.table.set_reorderable(own_order and self.proxy.is_natural())
        self._update_buttons()

    def _play_library(self, playlist_id: str) -> None:
        """Disco de una tarjeta de «Tus listas»: reproduce sin abrir la lista."""
        tracks = self.db.playlist_tracks(playlist_id)
        if not tracks:
            self._notice(tr("Esa lista aún no tiene pistas guardadas: sincronízala primero."))
            return
        if self._missing_binaries_notice():
            return
        self.db.note_playlist_played(playlist_id)
        self.player.play_tracks(tracks, random.randrange(len(tracks)) if self.player.shuffle else 0)

    def _open_remote(self, card: PlaylistCard) -> None:
        self._remote_cards[card.playlist_id] = card
        self._remote_failed.discard(card.playlist_id)    # abrirla de nuevo es reintentar
        self._navigate(REMOTE_PREFIX + card.playlist_id)

    def _play_remote(self, card: PlaylistCard) -> None:
        """Disco de una recomendación: la descarga (si hace falta) y la reproduce."""
        if self._missing_binaries_notice():
            return
        self._remote_cards[card.playlist_id] = card

        def play(data: Optional[RemotePlaylist]) -> None:
            if data is None:
                return
            if not data.tracks:
                self._notice(tr("«{title}» no tiene canciones que se puedan reproducir.", title=data.title))
                return
            for track in data.tracks:
                self.db.upsert_track(track)
            start = random.randrange(len(data.tracks)) if self.player.shuffle else 0
            self.player.play_tracks(data.tracks, start)

        if card.playlist_id not in self._remote:
            self._notice(tr("Cargando «{title}»…", title=card.title))
            self._remote_failed.discard(card.playlist_id)
        self._fetch_remote(card.playlist_id, play)

    def _fetch_remote(self, playlist_id: str, then: Optional[Callable] = None) -> None:
        """Descarga una lista ajena una sola vez por sesión. ``then`` recibe la
        lista (o ``None`` si falló)."""
        data = self._remote.get(playlist_id)
        if data is not None:
            if then is not None:
                then(data)
            return
        waiting = self._remote_waiting.get(playlist_id)
        if waiting is not None:          # ya está en camino: solo se apunta el interesado
            if then is not None:
                waiting.append(then)
            return
        self._remote_waiting[playlist_id] = [then] if then is not None else []

        def done(result, error) -> None:
            callbacks = self._remote_waiting.pop(playlist_id, [])
            if error is not None or result is None:
                log.warning("no se pudo abrir la lista %s: %s", playlist_id, error)
                self._remote_failed.add(playlist_id)
                self._notice(tr("No se pudo abrir la lista. Comprueba la conexión."), "error")
            else:
                self._remote[playlist_id] = result
            if self._view_key == REMOTE_PREFIX + playlist_id:
                self._show_view(self._view_key, force=True)
            for callback in callbacks:
                callback(result if error is None else None)

        run_async(lambda: discover.fetch_playlist(playlist_id), done)

    def _show_remote(self, playlist_id: str) -> None:
        data = self._remote.get(playlist_id)
        card = self._remote_cards.get(playlist_id)
        if data is None:
            loading = playlist_id in self._remote_waiting
            self.model.set_tracks([])
            self._band_art = sized_url(card.thumb_url, 400) if card else ""
            self.band.set_content(
                card.title if card else tr("Lista"), "", [card.note] if card and card.note else [],
                self._band_art, show_sync=False, show_back=True,
            )
            if playlist_id in self._remote_failed and not loading:
                self._set_empty(tr("No se pudo abrir la lista"),
                                tr("Comprueba la conexión y vuelve a abrirla desde el inicio."))
            else:
                self._set_empty(tr("Cargando la lista…"))
                self._fetch_remote(playlist_id)
            return
        self.model.set_tracks(data.tracks)
        description = data.description.strip()
        if len(description) > 230:
            description = description[:228].rstrip() + "…"
        facts = [plural(len(data.tracks), "{n} canción", "{n} canciones")]
        total = data.duration_s or sum(t.duration_s or 0 for t in data.tracks)
        if format_total(total):
            facts.append(format_total(total))
        if data.author:
            facts.append(tr("De {author}", author=data.author))
        if data.year:
            facts.append(data.year)
        self._band_art = sized_url(data.thumb_url or (card.thumb_url if card else ""), 400)
        self.band.set_content(data.title, description, facts, self._band_art,
                              show_sync=False, show_back=True,
                              extra=(tr("Guardar como lista local"), "save") if data.tracks else None)
        self._set_empty("" if data.tracks else tr("Esta lista no tiene canciones que se puedan reproducir"))

    def _on_rows_delete(self, rows: list) -> None:
        """Supr en la tabla: en una lista local quita las filas seleccionadas."""
        key = self._view_key
        if not is_local_playlist(key):
            return
        self._remove_tracks_from_local_playlist(key, self._tracks_at(rows))
