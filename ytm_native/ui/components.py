"""Piezas visuales: botones, carátula, banda de cabecera, filas, letra y avisos.

Todo lo que responde al ratón se pinta a mano para poder darle lo que el QSS
no sabe hacer: hundirse al pulsar, fundir el hover y enseñar el anillo de foco
solo cuando se navega con el teclado.

Desde la Tarea 10.6 cada familia vive en su módulo (``buttons``, ``cards``,
``band``, ``table``, ``sidebar``, ``notices``, ``lyrics_panel`` y las
``primitives`` que comparten); este módulo los reúne para que quien importe
de ``components`` siga funcionando igual.
"""

from __future__ import annotations

from .primitives import (  # noqa: F401 - se reexporta
    glyph_pixmap,
    paint_reveal,
    Reveal,
    fade_visible,
    window_settling,
    window_sliding,
    _alpha,
    _draw_glyph,
    ElidedLabel,
)
from .buttons import (  # noqa: F401 - se reexporta
    _Pressable,
    PillButton,
    IconButton,
    PaneButton,
    PlayButton,
    SeekSlider,
    Chip,
)
from .cards import (  # noqa: F401 - se reexporta
    CoverLabel,
    note_launch,
    take_launch,
    fit_aspect,
    CoverFlight,
    ClickableCover,
    CardSpec,
    SleeveCard,
    CardGrid,
    ArtistStrip,
)
from .band import (  # noqa: F401 - se reexporta
    PlaylistBand,
    SuggestionStrip,
    ShelfSection,
)
from .table import (  # noqa: F401 - se reexporta
    TrackTable,
    TrackDelegate,
    FilterBox,
    EmptyState,
)
from .sidebar import (  # noqa: F401 - se reexporta
    ActiveMark,
    ListFocusRing,
    SidebarFooter,
)
from .notices import (  # noqa: F401 - se reexporta
    Banner,
    Toast,
    ToastHost,
)
from .lyrics_panel import (  # noqa: F401 - se reexporta
    _LyricsDelegate,
    LyricsPanel,
)

__all__ = [
    "glyph_pixmap",
    "paint_reveal",
    "Reveal",
    "fade_visible",
    "window_settling",
    "window_sliding",
    "_alpha",
    "_draw_glyph",
    "ElidedLabel",
    "_Pressable",
    "PillButton",
    "IconButton",
    "PaneButton",
    "PlayButton",
    "SeekSlider",
    "Chip",
    "CoverLabel",
    "note_launch",
    "take_launch",
    "fit_aspect",
    "CoverFlight",
    "ClickableCover",
    "CardSpec",
    "SleeveCard",
    "CardGrid",
    "ArtistStrip",
    "PlaylistBand",
    "SuggestionStrip",
    "ShelfSection",
    "TrackTable",
    "TrackDelegate",
    "FilterBox",
    "EmptyState",
    "ActiveMark",
    "ListFocusRing",
    "SidebarFooter",
    "Banner",
    "Toast",
    "ToastHost",
    "_LyricsDelegate",
    "LyricsPanel",
]
