"""Constantes de comportamiento. Cada bloque responde a una observación del plan."""

from __future__ import annotations

# --- Extracción (Fase 1) ---------------------------------------------------
# Selector de formato explícito: mejor audio disponible; "best" como último
# recurso si el vídeo no ofrece pistas de solo audio.
AUDIO_FORMAT = "bestaudio/best"
MIN_DENO_VERSION = (2, 3, 0)
RESOLVE_TIMEOUT_S = 60
CONTROL_VIDEO_ID = "jNQXAC9IVRw"

# --- Taxonomía de errores (Fase 2) -----------------------------------------
NETWORK_RETRIES = 2
NETWORK_RETRY_WAIT_S = 3
UNKNOWN_RETRIES = 1  # reintentos extra antes de pasar al vídeo de control
# Backoff cuando el bloqueo anti-bot llega incluso con cookies (o sin fallback).
BOT_BACKOFF_STEPS_S = (60, 300, 900)
# "Modo pegajoso": tras un fallback anónimo -> cookies, cuánto se queda con
# cookies antes de volver a probar el modo anónimo.
COOKIES_STICKY_S = 6 * 3600
# Un "no disponible" no es permanente: se reintenta pasado este tiempo.
UNAVAILABLE_RECHECK_S = 7 * 86400
# Varios "no disponible"/fallos seguidos huelen a bloqueo, no a pistas borradas.
MAX_CONSECUTIVE_FAILURES = 3

# --- Actualización (Fase 2) ------------------------------------------------
PROACTIVE_UPDATE_COOLDOWN_S = 24 * 3600
REACTIVE_UPDATE_COOLDOWN_S = 6 * 3600
UPDATE_DRAIN_TIMEOUT_S = 20  # espera a la resolución en curso antes de matarla
UPDATE_TIMEOUT_S = 300
NIGHTLY_RELEASE_API = "https://api.github.com/repos/yt-dlp/yt-dlp-nightly-builds/releases/latest"

# --- Sincronización (Fase 3) -----------------------------------------------
SYNC_MIN_INTERVAL_S = 12 * 3600
SYNC_STARTUP_DELAY_S = (20, 45)       # retraso aleatorio al arrancar
SYNC_PAUSE_BETWEEN_LISTS_S = (2, 6)   # pausa aleatoria entre descargas de listas
FULL_SYNC_MIN_DAYS = 7
FULL_SYNC_MAX_DAYS = 14
# Si el servidor no informa el conteo de una lista, se re-descarga cada:
NO_COUNT_RESYNC_S = 2 * 86400

# --- Reproducción (Fase 4) -------------------------------------------------
DEBOUNCE_MS = 400
# Si la URL de la pista ya está en caché no hay yt-dlp que ahorrar: basta una
# espera corta para que "Siguiente x5" no cargue en mpv las pistas intermedias.
DEBOUNCE_CACHED_MS = 120
# Pistas que se resuelven por adelantado en cuanto la actual empieza a sonar
# (antes: una sola, y solo al final de la pista). Con la caché de URLs, oír la
# cola en orden cuesta las mismas resoluciones que antes.
PREFETCH_AHEAD = 2
PREFETCH_START_DELAY_MS = 1500        # no resolver por adelantado si el usuario va saltando
PREFETCH_RETRY_AFTER_S = 600          # un prefetch fallido no se repite antes de esto
PREFETCH_MAX_AGE_S = 2 * 3600
URL_CACHE_SIZE = 64                   # URLs resueltas que se recuerdan (valen ~6 h)
URL_EXPIRY_MARGIN_S = 120
PREMATURE_EOF_MARGIN_S = 5
MAX_RERESOLVE_PER_TRACK = 2
# Medido el 10-nov-2026: una URL recién resuelta devuelve HTTP 403 durante
# ~1,3-1,8 s y luego funciona. Si la apertura falla, se reintenta la MISMA URL
# con estas esperas antes de gastar otra resolución de yt-dlp.
LOAD_RETRY_DELAYS_MS = (600, 900, 1500, 2500)
# En el uso real (log del 10-nov) los dos primeros intentos fallaban siempre:
# se espera a que la URL tenga esta edad antes del primer intento, lo que
# ahorra dos peticiones rechazadas por pista sin tardar más en sonar.
FIRST_LOAD_MIN_URL_AGE_S = 1.6

# --- Rapidez al pulsar reproducir (medido el 12-nov-2026) -------------------
# Reparto de una resolución en frío con los clientes por defecto de yt-dlp
# (~4,8 s): 1,3 s arrancar yt-dlp.exe (es un ejecutable "onefile" que se
# desempaqueta cada vez), 0,85 s la página, 0,5 s las API del reproductor,
# 1,1 s el reto JS con deno y 0,2 s el manifiesto HLS. Después la URL devuelve
# 403 hasta ~3,5 s después de emitida: yt-dlp elige el cliente "tv_samsung" y
# YouTube no sirve el audio hasta que pasaría el anuncio ("Detected a 6s ad
# skippable after 5s"). No depende de cookies de visitante ni de la conexión.
#
# 1) Cliente rápido. Con "visionos" (ya está entre los que yt-dlp usa por
#    defecto, en segundo lugar) no hay reto JS ni espera del anuncio: mismo
#    Opus 251, la URL responde 206 al instante y la resolución baja a ~1,5 s.
#    Solo en modo anónimo. Si falla se repite con los clientes por defecto, y
#    tras varios fallos seguidos que el modo por defecto sí resuelve se deja
#    de intentar durante un tiempo. Cadena vacía = desactivado.
FAST_PLAYER_CLIENT = "visionos"
FAST_CLIENT_MAX_MISSES = 3
FAST_CLIENT_PAUSE_S = 6 * 3600
# 2) Un yt-dlp "precalentado" espera la URL por stdin (-a -): ahorra el arranque.
#    Solo lo usan las reproducciones; pasado este tiempo se renueva. Además el
#    resultado se lee en cuanto yt-dlp imprime el JSON, sin esperar los ~0,5 s
#    que tarda el proceso en borrar su carpeta temporal y salir.
WARM_MAX_AGE_S = 3600
# 3) No se piden manifiestos HLS/DASH (el audio 251 llega por https directo).
#    Si una pista no tuviera otro formato, se reintenta sin este recorte.
LEAN_SKIP = "skip=hls,dash,translated_subs"
# 4) Para las URL del modo por defecto (las del anuncio): en vez de esperar un
#    tiempo fijo y dejar que mpv falle, el hilo que resuelve sondea la URL
#    (1 byte) y la entrega en cuanto responde 206.
URL_PROBE_FIRST_DELAY_S = 0.6         # antes de esto siempre daba 403
URL_PROBE_INTERVAL_S = 0.15
URL_PROBE_TIMEOUT_S = 8.0
# Una URL con esta edad ya no sufre el 403 inicial: se carga sin sondear.
URL_SETTLED_AGE_S = 6.0
# El reintento de la MISMA URL solo tiene sentido si es reciente; una URL vieja
# (de la caché) que falla al abrir está caducada: se resuelve otra.
LOAD_RETRY_MAX_URL_AGE_S = 20.0

# --- Búsqueda en vivo e inicio ----------------------------------------------
# Al escribir: las sugerencias se piden tras una pausa corta y los resultados
# tras una algo más larga, para no lanzar una búsqueda por cada tecla.
SEARCH_SUGGEST_DEBOUNCE_MS = 140
SEARCH_LIVE_DEBOUNCE_MS = 400
SEARCH_LIVE_MIN_CHARS = 2
# Tope de resultados que se enseñan. Cada búsqueda son tres peticiones a la vez
# (sin filtro, canciones y vídeos), de una página cada una: tardan lo que la
# más lenta, no la suma.
SEARCH_RESULTS = 40
RECENT_SEARCHES = 8
# Las recomendaciones del inicio se guardan y se renuevan como mucho cada 12 h
# (o a mano): son unas 7 peticiones anónimas por renovación.
HOME_RECS_TTL_S = 12 * 3600
HOME_SEED_PLAYLISTS = 2               # listas principales de las que sale una estantería
HOME_SEED_ARTISTS = 3                 # artistas favoritos de los que sale otra
HOME_SHELF_MAX_CARDS = 10
HOME_SHELF_MIN_CARDS = 3
# Panel "Ahora suena": música parecida y datos del artista. Dos peticiones
# anónimas por canción nueva, solo con el panel abierto; se guardan una semana.
NOW_INFO_TTL_S = 7 * 86400
NOW_SIMILAR_TRACKS = 10
# Búsqueda: listas de la comunidad y artistas. Dos peticiones más, aparte de las
# de canciones, para que las canciones no tengan que esperarlas.
SEARCH_PLAYLISTS = 20
SEARCH_ARTISTS = 8
# Página del artista: tres o cuatro peticiones anónimas al abrirla; se guarda un día.
ARTIST_PAGE_TTL_S = 86400
ARTIST_TOP_SONGS = 10
ARTIST_SIMILAR_TRACKS = 12
ARTIST_MAX_CARDS = 12

# --- Letras (LRCLIB) y radio ------------------------------------------------
LRCLIB_BASE = "https://lrclib.net/api"
LRCLIB_USER_AGENT = "ytmn-desktop/0.1"
LYRICS_MAX_DURATION_DELTA_S = 2
LYRICS_NEGATIVE_CACHE_S = 7 * 86400   # "sin letra" se vuelve a consultar pasada una semana
RADIO_LIMIT = 25
MPV_MAX_RESTARTS_PER_MIN = 3
# Límite de tasa local: el prefetch se suspende al superarlo (no es una
# garantía de parecer "humano", solo acota el volumen).
MAX_RESOLVES_PER_HOUR = 60
DAILY_RESOLVE_SOFT_CAP = 250

# --- Criterios de la Fase 0 (definidos ANTES de correr la prueba) ----------
FASE0_MIN_DAYS = 3
FASE0_MIN_SAMPLES = 60
FASE0_OK_BLOCK_RATE = 0.02     # <= 2 % de bloqueos: el modo es viable
FASE0_BAD_BLOCK_RATE = 0.10    # > 10 % de bloqueos: el modo no es viable
