"""La búsqueda: lo que se escribe, las sugerencias, el ámbito (canciones,
listas o artistas) y la alternancia entre la tabla y las tarjetas.

Es una mezcla de ``MainWindow``: sus métodos usan los controles y el estado
de la ventana (``self.model``, ``self.table``, ``self.band``, ``self.player``…).
"""

from __future__ import annotations

import logging

from ytm_native.core import config
from ytm_native.services import discover
from ytm_native.services.discover import SearchExtras
from ytm_native.services.sync import search_tracks
from ..search import ARTISTS, PLAYLISTS, SONGS
from ..widgets import run_async
from ytm_native.core.i18n import plural, tr
from .common import SEARCH_KEY, SEARCH_IDLE_NOTE

log = logging.getLogger("ytmn.ui")


class SearchView:
    """Lo de la búsqueda, como parte de ``MainWindow``."""

    def _set_search_note(self, text: str) -> None:
        self._search_note = text
        self._refresh_search_band()

    def _refresh_search_band(self) -> None:
        if self._view_key == SEARCH_KEY:
            self.band.set_facts([self._scope_note()])

    def _cards_shown(self) -> bool:
        """¿La búsqueda enseña tarjetas (listas o artistas) en vez de canciones?"""
        return self._view_key == SEARCH_KEY and self._search_scope != SONGS

    def _focus_search(self) -> None:
        self._navigate(SEARCH_KEY)
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    # ---------------------------------------------------------------- búsqueda
    def _on_search_text(self, text: str) -> None:
        """Búsqueda en vivo: sugerencias tras una pausa corta y resultados tras
        una algo más larga. Intro sigue buscando en el acto."""
        query = text.strip()
        if query:
            self._suggest_timer.start()
            if len(query) >= config.SEARCH_LIVE_MIN_CHARS:
                self._live_timer.start()
            else:
                self._live_timer.stop()
            return
        # Campo vacío: se descarta lo que venga de camino y vuelve el punto de partida.
        self._suggest_timer.stop()
        self._live_timer.stop()
        self._suggest_seq += 1
        self._search_seq += 1
        self._searched_query = ""
        self._search_tracks = []
        self._search_note = SEARCH_IDLE_NOTE
        self._extras = SearchExtras()
        self._extras_state = "idle"
        self._top_artist = None
        self._show_recent_searches()
        if self._view_key == SEARCH_KEY:
            self.model.set_tracks([])
            self._refresh_search_band()
            self._render_search_cards()
            self._sync_search_empty()
            self._update_buttons()

    def _show_recent_searches(self) -> None:
        self.suggestions.set_items(
            discover.recent_searches(self.db), lead=tr("Recientes"), glyph="history", strong=False
        )

    def _fetch_suggestions(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            return
        self._suggest_seq += 1
        seq = self._suggest_seq

        def done(texts, error) -> None:
            # Si falla no se avisa: las sugerencias son una ayuda, no la tarea.
            if seq == self._suggest_seq and error is None:
                self.suggestions.set_items(texts or [], typed=query)

        run_async(lambda: discover.search_suggestions(query), done)

    def _on_suggestion_picked(self, text: str) -> None:
        self.search_edit.setText(text)
        self.search_edit.setFocus()
        self._suggest_timer.stop()
        self._fetch_suggestions()
        self._do_search(commit=True)

    def _sync_search_empty(self) -> None:
        query = self.search_edit.text().strip()
        searched = bool(self._searched_query) and self._searched_query == query
        idle = (tr("Busca música nueva"),
                tr("Empieza a escribir: las sugerencias y los resultados aparecen solos. "
                "No hace falta iniciar sesión."))
        self._sync_artist_strip(searched)
        if self._search_scope == SONGS:
            if self.model.rowCount():
                self._set_empty("")
            elif searched and self._search_note != tr("Buscando «{query}»…", query=query):
                self._set_empty(tr("Sin resultados para «{query}»", query=query),
                                tr("Prueba con otro título o artista, o con una de las sugerencias de arriba."),
                                glyph="search")
            else:
                self._set_empty(*idle, glyph="search")
            return
        items = self._extras.playlists if self._search_scope == PLAYLISTS else self._extras.artists
        what = tr("listas de la comunidad") if self._search_scope == PLAYLISTS else tr("artistas")
        if items:
            self._set_empty("")
        elif not searched:
            self._set_empty(*idle, glyph="search")
        elif self._extras_state == "loading":
            self._set_empty(tr("Buscando {what}…", what=what), glyph="search")
        elif self._extras_state == "failed":
            self._set_empty(tr("No se pudieron buscar {what}", what=what), tr("Comprueba la conexión y pulsa Intro para reintentar."),
                            glyph="search")
        else:
            self._set_empty(tr("Sin {what} para «{query}»", what=what, query=query), tr("Prueba con otro nombre o con menos palabras."),
                            glyph="search")

    def _sync_artist_strip(self, searched: bool) -> None:
        """La fila del artista va sobre las canciones si YouTube Music dio a un
        artista como resultado principal: la búsqueda iba de él."""
        show = (self._view_key == SEARCH_KEY and self._search_scope == SONGS and searched
                and self._top_artist is not None)
        if show:
            self.artist_strip.set_card(self._top_artist)
        self.artist_strip.setVisible(show)

    def _scope_note(self) -> str:
        """La nota de la banda habla de lo que se está viendo."""
        query = self._searched_query
        if self._search_scope == SONGS or not query:
            return self._search_note
        if self._extras_state == "loading":
            return tr("Buscando «{query}»…", query=query)
        if self._extras_state == "failed":
            return tr("La búsqueda falló. Pulsa Intro para reintentar.")
        if self._search_scope == PLAYLISTS:
            count = plural(len(self._extras.playlists), "{n} lista de la comunidad", "{n} listas de la comunidad")
        else:
            count = plural(len(self._extras.artists), "{n} artista", "{n} artistas")
        return tr("{count} para «{query}»", count=count, query=query)

    def _set_search_scope(self, scope: str) -> None:
        """Canciones, listas de la comunidad o artistas. Sin animación: se cambia
        a menudo y la búsqueda ya está hecha (las tres llegan a la vez)."""
        self.scope_bar.set_scope(scope)
        if scope == self._search_scope:
            return
        self._search_scope = scope
        if self._view_key == SEARCH_KEY:
            self._refresh_search_band()
            self._render_search_cards()
            self._sync_search_empty()
            self._update_buttons()

    def _render_search_cards(self) -> None:
        if self._search_scope == PLAYLISTS:
            self.search_cards.show_playlists(self._extras.playlists)
        elif self._search_scope == ARTISTS:
            self.search_cards.show_artists(self._extras.artists)

    def _fetch_extras(self, query: str, seq: int) -> None:
        """Listas de la comunidad y artistas: aparte de las canciones, para que
        estas no esperen a la petición más lenta."""
        self._extras = SearchExtras()
        self._extras_state = "loading"
        self._refresh_extras_view()

        def done(extras, error) -> None:
            if seq != self._search_seq:
                return
            if error is not None:
                log.warning("listas y artistas de «%s»: %s", query, error)
                self._extras_state = "failed"
            else:
                self._extras = extras or SearchExtras()
                self._extras_state = "done"
            self._refresh_extras_view()

        run_async(lambda: discover.search_extras(query), done)

    def _refresh_extras_view(self) -> None:
        if self._view_key == SEARCH_KEY and self._search_scope != SONGS:
            self._refresh_search_band()
            self._render_search_cards()
            self._sync_search_empty()
            self._update_buttons()

    def _do_search(self, commit: bool = False) -> None:
        """``commit``: la búsqueda se pidió a propósito (Intro o una sugerencia),
        no por una pausa al escribir: se recuerda y sus errores se avisan."""
        query = self.search_edit.text().strip()
        self._live_timer.stop()
        if not query:
            return
        if commit:
            discover.remember_search(self.db, query)
        if query == self._searched_query:
            if self._extras_state == "failed":
                self._fetch_extras(query, self._search_seq)     # Intro reintenta lo que falló
            return                        # ya está en pantalla o de camino
        self._searched_query = query
        self._search_seq += 1
        seq = self._search_seq
        self._top_artist = None
        self._set_search_note(tr("Buscando «{query}»…", query=query))
        top: dict = {}

        def done(tracks, error) -> None:
            if seq != self._search_seq:
                return                    # se siguió escribiendo: esta respuesta ya no vale
            if error is not None:
                self._searched_query = ""          # Intro lo reintenta
                self._set_search_note(tr("La búsqueda falló. Pulsa Intro para reintentar."))
                if commit:
                    self._notice(tr("Error de búsqueda: {error}", error=error), "error")
                return
            self._top_artist = discover.artist_card_from_item(top.get("artist"))
            self._search_tracks = list(tracks or [])
            self._set_search_note(
                tr("{plural} para «{query}»", plural=plural(len(self._search_tracks), "{n} resultado", "{n} resultados"), query=query)
            )
            if self._view_key != SEARCH_KEY:
                return                    # se verán al volver a la búsqueda
            self.model.set_tracks(self._search_tracks)
            self._sync_search_empty()
            current = self.player.current_track()
            self.model.set_current(current.video_id if current else None)
            self.table.scrollToTop()
            self._update_buttons()

        run_async(lambda: search_tracks(query, limit=config.SEARCH_RESULTS, top=top), done)
        self._fetch_extras(query, seq)
