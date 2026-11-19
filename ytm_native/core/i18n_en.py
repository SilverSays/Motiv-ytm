"""Catálogo inglés: cada texto de la interfaz, tal como está en el código
(en español), con su traducción.

Los huecos (``{n}``, ``{title}``…) llevan el mismo nombre en los dos
idiomas. Un texto que falte aquí sale en español: ``tests/test_i18n.py``
comprueba que todo lo que pasa por ``tr`` en el código tiene su entrada.
"""

CATALOG = {
    # -- app.py ----------------------------------------------------------------
    "Ocurrió un error inesperado:\n\n{name__}: {exc}\n\nLos detalles están en {value}":
        "An unexpected error occurred:\n\n{name__}: {exc}\n\nDetails are in {value}",

    # -- core ------------------------------------------------------------------
    "{name} no se puede descifrar: se guardó con otro usuario de Windows, en otro equipo o está dañado":
        "{name} can't be decrypted: it was saved by another Windows user, on another computer, or it is damaged",
    "el cifrado de Windows (DPAPI) no está disponible en este sistema":
        "Windows encryption (DPAPI) isn't available on this system",
    "Windows no pudo cifrar los datos (error {get_last_error})":
        "Windows couldn't encrypt the data (error {get_last_error})",
    "no hay datos cifrados": "there is no encrypted data",
    "Windows no pudo descifrar los datos (error {get_last_error})":
        "Windows couldn't decrypt the data (error {get_last_error})",
    "los datos no están cifrados con la cabecera de la aplicación":
        "the data isn't encrypted with the app's header",
    "La base de datos lleva más de {LOCK_TIMEOUT_S:g} s ocupada por otra operación":
        "The database has been busy with another operation for more than {LOCK_TIMEOUT_S:g} s",
    "YouTube pidió verificación anti-bot": "YouTube asked for an anti-bot check",
    "Sin conexión": "No connection",
    "Falta el runtime de JavaScript (deno)": "The JavaScript runtime (deno) is missing",
    "La pista requiere iniciar sesión": "This track requires signing in",
    "Pista no disponible": "Track unavailable",
    "yt-dlp no pudo extraer el audio": "yt-dlp couldn't extract the audio",
    "Error desconocido de yt-dlp": "Unknown yt-dlp error",
    "Cancelado": "Cancelled",
    "Interrumpido por una actualización": "Interrupted by an update",
    "Límite local de resoluciones por hora": "Local limit of resolutions per hour",
    "Falta yt-dlp en la carpeta bin": "yt-dlp is missing from the bin folder",

    # -- playback/bootstrap.py ------------------------------------------------
    "La descarga de {label} se cortó ({exc}); reintento {value} de {attempts}…":
        "The {label} download was interrupted ({exc}); retry {value} of {attempts}…",
    "Descargando {label}… {pct} %": "Downloading {label}… {pct}%",
    "Descarga incompleta: {done} de {total} bytes": "Incomplete download: {done} of {total} bytes",
    "Descarga interrumpida: {exc!r}": "Download interrupted: {exc!r}",
    "{name}: se extrajeron {size} bytes de {expected_size}.": "{name}: extracted {size} of {expected_size} bytes.",
    "Aviso: no se pudo obtener la suma SHA-256 de {filename}.":
        "Warning: couldn't get the SHA-256 checksum of {filename}.",
    "Aviso: la suma SHA-256 de {filename} no está en el listado.":
        "Warning: the SHA-256 checksum of {filename} isn't in the list.",
    "La suma SHA-256 de {filename} no coincide; descarga descartada.":
        "The SHA-256 checksum of {filename} doesn't match; download discarded.",
    "{filename}: suma SHA-256 verificada.": "{filename}: SHA-256 checksum verified.",
    "El zip de deno no contiene deno.exe.": "The deno zip doesn't contain deno.exe.",
    "El zip de deno está dañado: {exc}": "The deno zip is damaged: {exc}",
    "No se pudo consultar la última versión de mpv.": "Couldn't check the latest version of mpv.",
    "No se encontró el paquete mpv-x86_64 en la última versión publicada.":
        "The mpv-x86_64 package wasn't found in the latest release.",
    "La suma SHA-256 de mpv no coincide; descarga descartada.":
        "The SHA-256 checksum of mpv doesn't match; download discarded.",
    "mpv: suma SHA-256 verificada.": "mpv: SHA-256 checksum verified.",
    "Extrayendo mpv…": "Extracting mpv…",
    "No se pudo ejecutar tar para extraer mpv: {exc}": "Couldn't run tar to extract mpv: {exc}",
    "No se pudo extraer el .7z de mpv con tar. Extrae a mano {archive} y copia mpv.exe a {bin_dir}. Detalle: {value}":
        "Couldn't extract the mpv .7z with tar. Extract {archive} by hand and copy mpv.exe to {bin_dir}. "
        "Details: {value}",
    "{name}: ya está instalado.": "{name}: already installed.",
    "{name}: instalado en {parent}.": "{name}: installed in {parent}.",
    "ERROR con {name}: {exc}": "ERROR with {name}: {exc}",

    # -- playback/extractor.py ------------------------------------------------
    "ERROR: no se pudo ejecutar {value}: {exc}": "ERROR: couldn't run {value}: {exc}",
    "Las cookies guardadas no se pueden descifrar: vuelve a importarlas o inicia sesión otra vez. Mientras, se "
    "reproduce sin cuenta.":
        "The saved cookies can't be decrypted: import them again or sign in again. Meanwhile, playback continues "
        "without an account.",
    "Las cookies de la cuenta ya no son válidas: renueva cookies.txt.":
        "The account cookies are no longer valid: renew cookies.txt.",
    "{detail} (el vídeo de control sí funciona)": "{detail} (the control video does work)",
    "{detail} (el vídeo de control también falla)": "{detail} (the control video fails too)",
    "Bloqueo anti-bot en modo anónimo: se usarán las cookies de la cuenta secundaria durante {value} h.":
        "Anti-bot block in anonymous mode: the secondary account's cookies will be used for {value} h.",
    "Revisa el estado de cookies.txt.": "Check the state of cookies.txt.",
    "No hay cookies utilizables para el fallback.": "There are no usable cookies for the fallback.",
    "Bloqueo anti-bot. Cola en pausa {value} min. {hint}": "Anti-bot block. Queue paused for {value} min. {hint}",
    "No se encontró {name} en la carpeta bin.": "{name} wasn't found in the bin folder.",
    "deno no se pudo ejecutar.": "deno couldn't run.",
    "deno {text} es anterior a la versión mínima {minimum}.": "deno {text} is older than the minimum version {minimum}.",
    "Falta yt-dlp en bin.": "yt-dlp is missing from bin.",
    "La app se está cerrando.": "The app is closing.",
    "falló tras una actualización sin verificar": "failed after an unverified update",
    "yt-dlp {unverified} falla: se restauró la versión anterior.":
        "yt-dlp {unverified} fails: the previous version was restored.",
    "No se pudo restaurar yt-dlp tras una actualización fallida; se reintentará al abrir la app.":
        "Couldn't restore yt-dlp after a failed update; it will be retried when the app opens.",
    "Hay una actualización anterior sin deshacer y el respaldo no se pudo restaurar.":
        "A previous update wasn't undone and the backup couldn't be restored.",
    "La última versión ({latest}) ya se descartó; se espera una más nueva.":
        "The latest version ({latest}) was already discarded; waiting for a newer one.",
    "No se pudo crear el respaldo: {error}": "Couldn't create the backup: {error}",
    "la versión anterior": "the previous version",
    "No se pudo preparar la actualización: {exc}": "Couldn't prepare the update: {exc}",
    "yt-dlp -U falló: {detail}": "yt-dlp -U failed: {detail}",
    "yt-dlp -U falló ({detail}) y no se pudo restaurar el respaldo.":
        "yt-dlp -U failed ({detail}) and the backup couldn't be restored.",
    "El binario nuevo no arranca.": "The new binary doesn't start.",
    "El binario nuevo no arranca y no se pudo restaurar el respaldo.":
        "The new binary doesn't start and the backup couldn't be restored.",
    "Se instaló {new_version}, ya descartada, y no se pudo restaurar el respaldo.":
        "{new_version} was installed although it had been discarded, and the backup couldn't be restored.",
    "yt-dlp actualizado a {new_version}.": "yt-dlp updated to {new_version}.",
    "yt-dlp ya está al día ({old_version}).": "yt-dlp is up to date ({old_version}).",
    "Actualización omitida por el cooldown.": "Update skipped: checked too recently.",
    "{downloaded} lista(s) actualizadas de {seen}.": "{downloaded} of {seen} playlist(s) updated.",
    "{n} con errores.": "{n} with errors.",
    "{where} (reproducciones)": "{where} (plays)",
    "Actualizado a {new_version}; la verificación quedó pendiente ({get}).":
        "Updated to {new_version}; verification is still pending ({get}).",
    "La versión nueva no encontró un runtime JS compatible y no se pudo restaurar el respaldo.":
        "The new version didn't find a compatible JS runtime and the backup couldn't be restored.",
    "La versión nueva no encontró un runtime JS compatible; revisa deno.":
        "The new version didn't find a compatible JS runtime; check deno.",
    "La versión {new_version} no pasó el smoke test y no se pudo restaurar el respaldo.":
        "Version {new_version} failed the smoke test and the backup couldn't be restored.",
    "yt-dlp {new_version} no pasó la prueba: se restauró {restored}.":
        "yt-dlp {new_version} failed the test: {restored} was restored.",
    "La versión {new_version} no pasó el smoke test; se restauró {restored}.":
        "Version {new_version} failed the smoke test; {restored} was restored.",
    "copia incompleta de {name}": "incomplete copy of {name}",

    # -- playback/mpv_ipc.py, player.py --------------------------------------
    "mpv no pudo iniciarse.": "mpv couldn't start.",
    "mpv se cerró varias veces seguidas; no se reiniciará solo.":
        "mpv closed several times in a row; it won't restart on its own.",
    "mpv se cerró de forma inesperada; reiniciando…": "mpv closed unexpectedly; restarting…",
    "No se pudo conectar con mpv por IPC.": "Couldn't connect to mpv over IPC.",
    "No se pudo cargar la radio: {error}": "Couldn't load the radio: {error}",
    "La radio no devolvió pistas nuevas.": "The radio returned no new tracks.",
    "El archivo ya no está en el disco.": "The file is no longer on disk.",
    "archivo local": "local file",
    "Más de {DAILY_RESOLVE_SOFT_CAP} resoluciones en 24 h: considera bajar el ritmo para no exponer la cuenta.":
        "More than {DAILY_RESOLVE_SOFT_CAP} resolutions in 24 h: consider slowing down so the account isn't exposed.",
    "Sin conexión. La cola quedó en pausa.": "No connection. The queue is paused.",
    "No disponible: {title}": "Unavailable: {title}",
    "No se pudo reproducir «{title}»: {message}": "Couldn't play “{title}”: {message}",
    "Varias pistas seguidas fallaron: puede ser un bloqueo temporal. La cola quedó en pausa.":
        "Several tracks in a row failed: it may be a temporary block. The queue is paused.",
    "mpv rechazó la orden de carga": "mpv rejected the load command",
    "fin prematuro": "ended too early",
    "No se pudo reproducir «{title}» ({detail}).": "Couldn't play “{title}” ({detail}).",

    # -- services/account_actions.py -----------------------------------------
    "Los cambios en la cuenta están desactivados.": "Changes to the account are turned off.",
    "La sesión de YouTube Music caducó: vuelve a iniciar sesión.":
        "The YouTube Music session expired: sign in again.",
    "YouTube Music pidió esperar un poco antes de más cambios.":
        "YouTube Music asked to wait a bit before more changes.",
    "Sin conexión.": "No connection.",
    "YouTube Music no aceptó el cambio.": "YouTube Music didn't accept the change.",
    "los cambios en la cuenta están desactivados": "changes to the account are turned off",
    "Las canciones del disco no están en YouTube Music.": "Songs from your disk aren't on YouTube Music.",
    "Inicia sesión en YouTube Music para cambiar tu cuenta.": "Sign in to YouTube Music to change your account.",
    "valoración desconocida: {rating!r}": "unknown rating: {rating!r}",
    "YouTube Music no permite guardar esta canción en la biblioteca.":
        "YouTube Music doesn't allow saving this song to the library.",
    "YouTube Music no permite quitar esta canción de la biblioteca.":
        "YouTube Music doesn't allow removing this song from the library.",
    "YouTube Music no procesó el cambio en la biblioteca.": "YouTube Music didn't process the library change.",
    "el registro de escuchas en YouTube está desactivado": "sending plays to YouTube history is turned off",
    "La lista necesita un nombre.": "The playlist needs a name.",
    "El nombre de la lista no puede llevar < ni >.": "The playlist name can't contain < or >.",
    "YouTube Music no creó la lista.": "YouTube Music didn't create the playlist.",
    "YouTube Music no añadió las canciones a la lista.": "YouTube Music didn't add the songs to the playlist.",
    "No se sabe qué copia de la canción quitar: sincroniza la lista.":
        "It's unclear which copy of the song to remove: sync the playlist.",
    "YouTube Music no quitó la canción de la lista.": "YouTube Music didn't remove the song from the playlist.",
    "Activa «Permitir cambios en mi cuenta» para cambiar tu cuenta de YouTube Music.":
        "Turn on “Allow changes to my account” to change your YouTube Music account.",
    "el me gusta de": "the like on",
    "la biblioteca con": "the library with",
    "No se pudo actualizar {what} «{title}». {describe_error}": "Couldn't update {what} “{title}”. {describe_error}",
    "No se pudo crear la lista «{title}». {describe_error}": "Couldn't create the playlist “{title}”. {describe_error}",
    "Lista «{title}» creada en tu cuenta de YouTube Music.": "Playlist “{title}” created in your YouTube Music account.",
    "No se pudo quitar «{title}» de la lista. {describe_error}":
        "Couldn't remove “{title}” from the playlist. {describe_error}",

    # -- services/auth_assistant.py ------------------------------------------
    "Firefox no guardó ninguna cookie: inicia sesión antes de cerrar la ventana.":
        "Firefox didn't save any cookies: sign in before closing the window.",
    "No se pudieron leer las cookies de Firefox.": "Couldn't read the Firefox cookies.",
    "No se encontró la sesión de Google en Firefox (falta {names}). Inicia sesión en YouTube Music y espera a "
    "ver tu biblioteca antes de cerrar la ventana.":
        "The Google session wasn't found in Firefox (missing {names}). Sign in to YouTube Music and wait until "
        "you see your library before closing the window.",
    "YouTube Music no aceptó la sesión: vuelve a intentarlo.": "YouTube Music didn't accept the session: try again.",
    "YouTube Music no reconoce una cuenta con esa sesión.": "YouTube Music doesn't recognise an account with that session.",
    "No se encontró Firefox en este equipo.": "Firefox wasn't found on this computer.",
    "No se pudo preparar la ventana de Firefox.": "Couldn't prepare the Firefox window.",
    "Inicio de sesión cancelado.": "Sign-in cancelled.",
    "No se pudo abrir Firefox.": "Couldn't open Firefox.",
    " como {account}": " as {account}",
    "Sesión iniciada{who}. Las credenciales se guardaron cifradas.": "Signed in{who}. The credentials were saved encrypted.",
    "No se pudo guardar la sesión de Firefox.": "Couldn't save the Firefox session.",

    # -- services/backup.py ---------------------------------------------------
    "{count} con {count2}, {count3} y {count4}": "{count} with {count2}, {count3} and {count4}",
    "{n} lista": "{n} playlist",
    "{n} listas": "{n} playlists",
    "{n} canción": "{n} song",
    "{n} canciones": "{n} songs",
    "{n} escucha": "{n} play",
    "{n} escuchas": "{n} plays",
    "{n} preferencia": "{n} preference",
    "{n} preferencias": "{n} preferences",
    "{n} lista nueva": "{n} new playlist",
    "{n} listas nuevas": "{n} new playlists",
    "{n} lista completada": "{n} playlist completed",
    "{n} listas completadas": "{n} playlists completed",
    "{n} canción añadida": "{n} song added",
    "{n} canciones añadidas": "{n} songs added",
    "listas": "playlists",
    "canciones": "songs",
    "No se pudo abrir el archivo: {value}": "Couldn't open the file: {value}",
    "El archivo es demasiado grande para ser una copia de esta app.":
        "The file is too large to be a backup from this app.",
    "El archivo no es texto JSON.": "The file isn't JSON text.",
    "El archivo no es JSON válido (línea {lineno}).": "The file isn't valid JSON (line {lineno}).",
    "No se pudo leer el archivo: {value}": "Couldn't read the file: {value}",
    "{where}: se esperaba texto.": "{where}: text was expected.",
    "{where}: se esperaba un número.": "{where}: a number was expected.",
    "{where}: identificador de canción no válido.": "{where}: invalid song id.",
    "El archivo no es una copia de seguridad de YT Music Nativo.": "The file isn't a YT Music Nativo backup.",
    "La copia no dice su versión.": "The backup doesn't state its version.",
    "La copia es de una versión más nueva de la app: actualízala para importarla.":
        "The backup is from a newer version of the app: update the app to import it.",
    "Fecha de la copia": "Backup date",
    "Canciones: se esperaba un objeto.": "Songs: an object was expected.",
    "Canción {video_id!r}": "Song {video_id!r}",
    "{where}: se esperaba un objeto.": "{where}: an object was expected.",
    "{where} (duración)": "{where} (duration)",
    "{where} (título)": "{where} (title)",
    "{where} (álbum)": "{where} (album)",
    "{where} (carátula)": "{where} (cover)",
    "Listas: se esperaba una lista.": "Playlists: a list was expected.",
    "Lista {index}": "Playlist {index}",
    "{where}: identificador de lista local no válido.": "{where}: invalid local playlist id.",
    "{where}: el identificador {playlist_id!r} aparece dos veces.": "{where}: the id {playlist_id!r} appears twice.",
    "{where}: sus canciones deben ser una lista.": "{where}: its songs must be a list.",
    "{where}, canción {position}": "{where}, song {position}",
    "{where} (posición)": "{where} (position)",
    "Lista sin nombre": "Untitled playlist",
    "{where} (descripción)": "{where} (description)",
    "{where} (creación)": "{where} (creation)",
    "{where} (última escucha)": "{where} (last play)",
    "Historial: se esperaba una lista.": "History: a list was expected.",
    "Historial, escucha {index}": "History, play {index}",
    "{where}: se esperaba [canción, fecha].": "{where}: [song, date] was expected.",
    "{where}: fecha no válida.": "{where}: invalid date.",
    "Preferencias: se esperaba un objeto.": "Preferences: an object was expected.",
    "Preferencia {key!r}: valor no válido.": "Preference {key!r}: invalid value.",
    "estrategia desconocida: {strategy}": "unknown strategy: {strategy}",

    # -- services/discord_rpc.py ----------------------------------------------
    "mensaje de Discord demasiado grande": "Discord message too large",
    "mensaje de Discord ilegible": "unreadable Discord message",
    "Sin título": "Untitled",
    "Artista desconocido": "Unknown artist",
    "{artist} (en pausa)": "{artist} (paused)",
    "solo en Windows": "Windows only",
    "Discord no está abierto": "Discord isn't open",
    "la tubería de Discord se cerró": "the Discord pipe closed",
    "Discord no responde": "Discord isn't responding",
    "Discord rechazó la conexión: {get}": "Discord refused the connection: {get}",
    "Discord cerró la conexión": "Discord closed the connection",
    "Discord no confirmó la actividad": "Discord didn't confirm the activity",
    "el id de la aplicación de Discord son solo cifras": "the Discord application id is digits only",

    # -- services/discover.py -------------------------------------------------
    "De {author}": "By {author}",
    "De {value}": "By {value}",
    ", de {artists}": ", by {artists}",
    "Parecidas a «{title}»": "Similar to “{title}”",
    "Elegidas a partir de «{title}»{by}": "Picked from “{title}”{by}",
    "una canción": "one song",
    "{songs} canciones": "{songs} songs",
    "Está en {count} de tus listas": "In {count} across your playlists",
    "Lo has escuchado hace poco": "You listened to it recently",
    "Porque escuchas a {name}": "Because you listen to {name}",
    "Listas de la comunidad con {name}": "Community playlists with {name}",
    "Para empezar": "To get started",
    # Datos de "Ahora suena" y del artista: se guardan en español y se
    # traducen al enseñarlos (now.py, artist.py).
    "Audio oficial": "Official audio",
    "Vídeo musical oficial": "Official music video",
    "Subido por un usuario": "Uploaded by a user",
    "Publicado por el artista": "Published by the artist",
    "Oyentes mensuales": "Monthly listeners",
    "Suscriptores": "Subscribers",
    "Reproducciones en total": "Total plays",
    "Álbum": "Album",
    "Año": "Year",
    "Reproducciones": "Plays",
    "Tipo": "Type",
    "Lista": "Playlist",
    "{subscribers} suscriptores": "{subscribers} subscribers",
    "Artista": "Artist",
    "{kind} de {year}": "{kind} from {year}",

    # -- services/lastfm.py ---------------------------------------------------
    "sin conexión con Last.fm ({name__})": "no connection to Last.fm ({name__})",
    "respuesta inesperada de Last.fm (HTTP {status_code})": "unexpected response from Last.fm (HTTP {status_code})",
    "faltan la clave y el secreto de la aplicación de Last.fm": "the Last.fm application key and secret are missing",
    "no hay sesión de Last.fm": "there is no Last.fm session",
    "error {code} de Last.fm": "Last.fm error {code}",
    "Last.fm no devolvió un token": "Last.fm didn't return a token",
    "Last.fm no devolvió la sesión": "Last.fm didn't return the session",
    "no hay ninguna autorización pendiente": "there is no pending authorization",
    "Last.fm ya no acepta la sesión: vuelve a conectar la cuenta. Tus escuchas esperan en la cola.":
        "Last.fm no longer accepts the session: connect the account again. Your plays are waiting in the queue.",
    "Last.fm rechazó la clave de la aplicación: revisa la clave y el secreto.":
        "Last.fm rejected the application key: check the key and the secret.",

    # -- services/lyrics.py, sync.py, updater.py -----------------------------
    "la búsqueda de la letra superó {budget_s:g} s": "the lyrics search took more than {budget_s:g} s",
    "respuesta cortada: {exc}": "truncated response: {exc}",
    "Esta sesión viene de un navegador que la liga al dispositivo (Chrome en Windows). Fuera de ese navegador deja "
    "de valer en pocas horas. Para una sesión duradera, repite el proceso en una ventana privada de Firefox.":
        "This session comes from a browser that binds it to the device (Chrome on Windows). Outside that browser it "
        "stops working within a few hours. For a lasting session, repeat the process in a Firefox private window.",
    "las credenciales guardadas no se pueden descifrar": "the saved credentials can't be decrypted",
    "las credenciales guardadas no tienen el formato esperado": "the saved credentials don't have the expected format",
    "Ya hay una sincronización en curso.": "A sync is already running.",
    "Faltan las cabeceras de ytmusicapi (browser.json).": "The ytmusicapi headers (browser.json) are missing.",
    "la sesión está cerrada (logged out)": "the session is closed (logged out)",
    "La biblioteca remota llegó vacía; no se borró nada. Puede que la sesión haya caducado: revisa las credenciales.":
        "The remote library came back empty; nothing was deleted. The session may have expired: check the "
        "credentials.",
    "la respuesta no trae la lista": "the response doesn't include the playlist",
    "llegaron {received} de las {expected} canciones que dice tener; no se borró nada y se reintentará":
        "{received} of the {expected} songs it claims to have arrived; nothing was deleted and it will be retried",
    "llegó vacía y tenía {had} canciones; no se borró nada y se reintentará":
        "it came back empty and had {had} songs; nothing was deleted and it will be retried",
    "La cuenta devolvió {n} listas de las {n2} guardadas; no se borró ninguna. Si las quitaste tú, desaparecerán en "
    "la próxima sincronización.":
        "The account returned {n} of the {n2} saved playlists; none were deleted. If you removed them, they'll "
        "disappear on the next sync.",
    "La sesión caducó: renueva las credenciales de la cuenta secundaria.":
        "The session expired: renew the secondary account's credentials.",
    "Error de sincronización: {exc}": "Sync error: {exc}",
    "GitHub respondió {status} al preguntar por versiones nuevas": "GitHub answered {status} when asked for new versions",
    "No se pudo comprobar si hay una versión nueva.": "Couldn't check for a new version.",

    # -- ui/artist.py ---------------------------------------------------------
    "Ver menos": "Show less",
    "Ver todo ({total})": "Show all ({total})",
    "Reintentar": "Retry",
    "Canciones populares": "Popular songs",
    "Todas las canciones": "All songs",
    "Abrir la lista con todas sus canciones": "Open the playlist with all their songs",
    "Álbumes": "Albums",
    "Sencillos y EP": "Singles and EPs",
    "Listas": "Playlists",
    "Música parecida": "Similar music",
    "Artistas parecidos": "Similar artists",
    "Sobre el artista": "About the artist",
    "Cargando el artista…": "Loading the artist…",
    "No se pudo cargar el artista. Comprueba la conexión.": "Couldn't load the artist. Check the connection.",
    "Del artista y de la comunidad, con «{name}» en el título": "From the artist and the community, with “{name}” in the title",
    "Lo que suena en la radio de {name}, sin sus canciones": "What plays on {name}'s radio, without their own songs",
    "Sobre {name}": "About {name}",
    "YouTube Music no tiene más información sobre este artista.": "YouTube Music has no more information about this artist.",

    # -- ui/components.py -----------------------------------------------------
    "Reproducir o pausar (Espacio)": "Play or pause (Space)",
    "Reproducir o pausar": "Play or pause",
    "Canción, artista o álbum": "Song, artist or album",
    "Buscar música": "Search music",
    "Reproducir": "Play",
    "Aleatorio": "Shuffle",
    "Sincronizar lista": "Sync playlist",
    "Descarga esta lista completa aunque el conteo no haya cambiado":
        "Downloads this whole playlist even if the count hasn't changed",
    "Volver": "Back",
    "Volver a la vista anterior (Alt+Izquierda)": "Back to the previous view (Alt+Left)",
    "Sugerencias de búsqueda": "Search suggestions",
    "Menú de la aplicación": "App menu",
    "Versión {version} disponible: abre su página en el navegador": "Version {version} available: open its page in the browser",
    "Versión {version} disponible": "Version {version} available",
    "Ver la canción que suena": "See the song that's playing",
    "Letra": "Lyrics",
    "Ocultar la letra (Ctrl+L)": "Hide the lyrics (Ctrl+L)",

    # -- ui/dialogs.py --------------------------------------------------------
    "1. Pulsa «Abrir Firefox». Se abre una ventana de Firefox aparte, vacía: no ve tus pestañas, cuentas ni "
    "extensiones.\n2. Inicia sesión en YouTube Music con la cuenta secundaria y espera a ver tu biblioteca.\n3. Cierra "
    "esa ventana de Firefox. La app recoge la sesión, comprueba que funciona, la guarda cifrada para tu usuario de "
    "Windows y borra del disco todo lo de esa ventana.":
        "1. Press “Open Firefox”. A separate, empty Firefox window opens: it can't see your tabs, accounts or "
        "extensions.\n2. Sign in to YouTube Music with the secondary account and wait until you see your library.\n"
        "3. Close that Firefox window. The app picks up the session, checks that it works, saves it encrypted for "
        "your Windows user and deletes everything from that window from the disk.",
    "¿Por qué Firefox y no Chrome? Las versiones recientes de Chrome ligan la sesión al dispositivo (Device Bound "
    "Session Credentials: cookies que caducan en minutos y solo ese Chrome puede renovar) y cifran sus cookies con "
    "una clave que solo Chrome puede usar (cifrado «app-bound» de DPAPI v20). Una sesión sacada de Chrome deja de "
    "valer en pocas horas fuera de él.":
        "Why Firefox and not Chrome? Recent versions of Chrome bind the session to the device (Device Bound Session "
        "Credentials: cookies that expire within minutes and only that Chrome can renew) and encrypt their cookies "
        "with a key only Chrome can use (DPAPI v20 “app-bound” encryption). A session taken out of Chrome stops "
        "working within a few hours outside it.",
    "No se encontró Firefox en este equipo. Instálalo desde mozilla.org (no hace falta que sea tu navegador "
    "habitual) o usa la importación manual de cabeceras.":
        "Firefox wasn't found on this computer. Install it from mozilla.org (it doesn't have to be your usual "
        "browser) or use the manual header import.",
    "Iniciar sesión con Firefox": "Sign in with Firefox",
    "Inicia sesión sin copiar cabeceras": "Sign in without copying headers",
    "Abrir Firefox": "Open Firefox",
    "Importar cabeceras a mano…": "Import headers by hand…",
    "Para usuarios avanzados o equipos sin Firefox.": "For advanced users or computers without Firefox.",
    "Cerrar": "Close",
    "Abriendo Firefox…": "Opening Firefox…",
    "Cancelar": "Cancel",
    "Inicia sesión en la ventana de Firefox y ciérrala cuando veas tu biblioteca. Cerrar este cuadro cancela el "
    "inicio de sesión.":
        "Sign in in the Firefox window and close it when you see your library. Closing this box cancels the sign-in.",
    "Comprobando la sesión…": "Checking the session…",
    "Volver a iniciar sesión": "Sign in again",

    # -- ui/home.py -----------------------------------------------------------
    "Inicio": "Home",
    "Tus listas": "Your playlists",
    "Escuchado hace poco": "Recently played",
    "Otras recomendaciones": "More recommendations",
    "Busca otras listas de la comunidad a partir de tu música": "Finds other community playlists based on your music",
    "Las que más escuchas van primero. El resto sigue en la barra lateral.":
        "The ones you play most come first. The rest are in the sidebar.",

    # -- ui/main_window.py ----------------------------------------------------
    "Recientes": "Recent",
    "Lo más escuchado del mes": "Most played this month",
    "Lo más escuchado": "Most played",
    "Busca sin iniciar sesión": "Search without signing in",
    "{hours} h {minutes} min": "{hours} h {minutes} min",
    "{hours} h": "{hours} h",
    "{minutes} min": "{minutes} min",
    "Biblioteca": "Library",
    "Sincronizar ahora": "Sync now",
    "Trae de tu cuenta las listas que han cambiado.": "Brings the playlists that changed from your account.",
    "Nueva lista…": "New playlist…",
    "Reproducción": "Playback",
    "Temporizador de apagado": "Sleep timer",
    "Vista": "View",
    "Ahora suena": "Now playing",
    "Panel con la canción que suena y su artista.": "Panel with the song that's playing and its artist.",
    "Barra lateral contraída": "Collapsed sidebar",
    "Mini reproductor": "Mini player",
    "Opciones…": "Options…",
    "Idioma, tema, cuenta, integraciones y mantenimiento.": "Language, theme, account, integrations and maintenance.",
    "Salir": "Quit",
    "Radio al terminar la cola": "Radio when the queue ends",
    "Seguir donde lo dejaste": "Pick up where you left off",
    "Igualar el volumen": "Even out the volume",
    "Guardar el historial de escucha": "Keep a listening history",
    "Al cerrar, seguir sonando en la bandeja": "When closing, keep playing in the tray",
    "Al minimizar, ir a la bandeja": "When minimising, go to the tray",
    "Iniciar con Windows (en la bandeja)": "Start with Windows (in the tray)",
    "Mostrar en Discord lo que escucho": "Show what I'm listening to on Discord",
    "Enviar mis escuchas a Last.fm": "Send my plays to Last.fm",
    "Permitir cambios en mi cuenta de YouTube Music": "Allow changes to my YouTube Music account",
    "Registrar mis escuchas en el historial de YouTube": "Record my plays in my YouTube history",
    "Necesita los cambios en tu cuenta permitidos.": "Needs changes to your account to be allowed.",
    "Avisar de versiones nuevas de la app": "Tell me about new versions of the app",
    "Como Windows": "Same as Windows",
    "Claro": "Light",
    "Oscuro": "Dark",
    "1 hora": "1 hour",
    "{minutes} minutos": "{minutes} minutes",
    "Otro tiempo…": "Another time…",
    "Al terminar esta canción": "When this song ends",
    "Cerrar la aplicación al terminar": "Close the app when it ends",
    "Cancelar el temporizador": "Cancel the timer",
    "Música de tu equipo": "Music on this computer",
    "Elegir la carpeta de música…": "Choose the music folder…",
    "Volver a leer la carpeta": "Read the folder again",
    "Dejar de usar la carpeta": "Stop using the folder",
    "Contraer la barra lateral (Ctrl+B)": "Collapse the sidebar (Ctrl+B)",
    "Desplegar la barra lateral (Ctrl+B)": "Expand the sidebar (Ctrl+B)",
    "Vistas y listas": "Views and playlists",
    "Pistas": "Tracks",
    "Ver la canción en grande, su artista y música parecida (Ctrl+I)":
        "See the song in large, its artist and similar music (Ctrl+I)",
    "Nada en reproducción": "Nothing playing",
    "Me gusta": "Like",
    "Anterior (Ctrl+Izquierda)": "Previous (Ctrl+Left)",
    "Siguiente (Ctrl+Derecha)": "Next (Ctrl+Right)",
    "Repetir: desactivado": "Repeat: off",
    "Posición en la pista": "Position in the track",
    "Mostrar u ocultar la letra (Ctrl+L)": "Show or hide the lyrics (Ctrl+L)",
    "Ver la cola de reproducción": "See the play queue",
    "Silenciar": "Mute",
    "Volumen": "Volume",
    "Tienes la última versión de la app.": "You have the latest version of the app.",
    "Descargando binarios… El progreso aparece al pie de la barra lateral.":
        "Downloading binaries… Progress is shown at the bottom of the sidebar.",
    "Faltan binarios en la carpeta bin: {names}. Sin ellos no se puede reproducir.":
        "Binaries missing from the bin folder: {names}. Nothing can play without them.",
    "Descargar ahora": "Download now",
    "Aún no hay credenciales de la cuenta secundaria: la biblioteca no se puede sincronizar. La búsqueda funciona "
    "sin cuenta.":
        "There are no credentials for the secondary account yet: the library can't sync. Search works without an "
        "account.",
    "Iniciar sesión": "Sign in",
    "La sesión de la cuenta secundaria caducó. La sincronización está detenida hasta que renueves las credenciales.":
        "The secondary account's session expired. Sync is stopped until you renew the credentials.",
    " Esta vez inicia sesión con Firefox: las sesiones de Chrome duran pocas horas.":
        " This time sign in with Firefox: Chrome sessions only last a few hours.",
    "Iniciar sesión otra vez": "Sign in again",
    "Opciones": "Options",
    "Esta lista aún no tiene pistas guardadas": "This playlist has no saved tracks yet",
    "Sincronízala para verlas.": "Sync it to see them.",
    "Buscar": "Search",
    "Cola de reproducción": "Play queue",
    "Historial": "History",
    "En tu equipo": "On this computer",
    "Nueva lista": "New playlist",
    "Crea una lista que se guarda solo en esta app (Ctrl+N)": "Creates a playlist that's saved only in this app (Ctrl+N)",
    "Sincronizar esta lista": "Sync this playlist",
    "Aceptar": "OK",
    "Nombre de la lista. Se guarda solo en esta app, no en tu cuenta.":
        "Playlist name. It's saved only in this app, not in your account.",
    "Crear": "Create",
    "Lista «{name}» creada con {plural}.": "Playlist “{name}” created with {plural}.",
    "Cambiar el nombre…": "Rename…",
    "Eliminar la lista…": "Delete the playlist…",
    "Copiar en tu cuenta de YouTube Music…": "Copy to your YouTube Music account…",
    "Cambiar el nombre": "Rename",
    "Nombre de la lista:": "Playlist name:",
    "Guardar": "Save",
    "Eliminar la lista": "Delete the playlist",
    "«{title}» se eliminará de esta app. Sus canciones no se borran de ningún otro sitio. No se puede deshacer.":
        "“{title}” will be deleted from this app. Its songs aren't deleted anywhere else. This can't be undone.",
    "Eliminar": "Delete",
    "Lista «{title}» eliminada.": "Playlist “{title}” deleted.",
    "«{title}» añadida a «{title2}».": "“{title}” added to “{title2}”.",
    "«{title}» ya estaba en «{title2}».": "“{title}” was already in “{title2}”.",
    "«{title}» quitada de la lista.": "“{title}” removed from the playlist.",
    "Elegir otra carpeta…": "Choose another folder…",
    "Lista local": "Local playlist",
    "Sincronizada {format_ago}": "Synced {format_ago}",
    "Error: {value}": "Error: {value}",
    "Lo que suena y lo que viene después. Un doble clic salta a esa pista.":
        "What's playing and what comes next. Double-click to jump to that track.",
    "La cola está vacía": "The queue is empty",
    "Reproduce una lista o una búsqueda.": "Play a playlist or a search.",
    "Esta lista está vacía": "This playlist is empty",
    "Añade canciones con el botón derecho sobre cualquier pista: «Añadir a una lista».":
        "Add songs by right-clicking any track: “Add to a playlist”.",
    "Lista local vacía": "Empty local playlist",
    "Sin sincronizar": "Not synced",
    "Aquí aparecerán tus listas cuando conectes la cuenta. Mientras tanto puedes buscar y reproducir cualquier "
    "canción sin iniciar sesión.":
        "Your playlists will show up here once you connect the account. Meanwhile you can search for and play any "
        "song without signing in.",
    "Tu biblioteca aún no se ha sincronizado. Pulsa F5 para traer tus listas.":
        "Your library hasn't synced yet. Press F5 to bring in your playlists.",
    "Última vez": "Last time",
    "{n} vez": "{n} time",
    "{n} veces": "{n} times",
    "{n} escucha en 30 días": "{n} play in 30 days",
    "{n} escuchas en 30 días": "{n} plays in 30 days",
    "{n} canción distinta": "{n} different song",
    "{n} canciones distintas": "{n} different songs",
    "Escuchas": "Plays",
    "Lo que has escuchado en esta app. Se guarda solo en este equipo.":
        "What you've listened to in this app. It's saved only on this computer.",
    "Borrar historial": "Clear history",
    "Nada escuchado en los últimos 30 días": "Nothing played in the last 30 days",
    "Lo anterior sigue en las otras dos pestañas.": "Older plays are still in the other two tabs.",
    "Aún no hay nada en el historial": "There's nothing in the history yet",
    "Aquí aparecerá cada canción que escuches al menos medio minuto.":
        "Every song you listen to for at least half a minute will appear here.",
    "El historial está desactivado": "History is turned off",
    "Actívalo en Opciones (Ctrl+,): «Guardar el historial de escucha».":
        "Turn it on in Options (Ctrl+,): “Keep a listening history”.",
    "Se borrarán {plural} de este equipo. Tus listas no cambian. No se puede deshacer.":
        "{plural} will be deleted from this computer. Your playlists don't change. This can't be undone.",
    "Borrar": "Clear",
    "Historial borrado.": "History cleared.",
    "Leyendo la carpeta…": "Reading the folder…",
    "Las canciones aparecen al terminar.": "The songs appear when it finishes.",
    "No hay canciones en esta carpeta": "There are no songs in this folder",
    "Formatos que se leen: MP3, FLAC, M4A, AAC, OGG, Opus y WAV.": "Supported formats: MP3, FLAC, M4A, AAC, OGG, Opus and WAV.",
    "Carpeta de música": "Music folder",
    "Las canciones de la carpeta dejan de aparecer en la app. Los archivos no se tocan.":
        "The folder's songs stop appearing in the app. The files aren't touched.",
    "Dejar de usarla": "Stop using it",
    "No se encuentra la carpeta de música: {folder}": "The music folder can't be found: {folder}",
    "No se pudo leer la carpeta de música.": "Couldn't read the music folder.",
    "Carpeta leída: {plural}.": "Folder read: {plural}.",
    "Buscando listas de la comunidad parecidas a tu música…": "Looking for community playlists similar to your music…",
    "Buscando otras listas…": "Looking for other playlists…",
    "No se pudieron renovar las recomendaciones.": "Couldn't refresh the recommendations.",
    "No se pudieron cargar las recomendaciones. Comprueba la conexión.":
        "Couldn't load the recommendations. Check the connection.",
    "Por ahora no hay recomendaciones que enseñar.": "There are no recommendations to show for now.",
    "Esa lista aún no tiene pistas guardadas: sincronízala primero.":
        "That playlist has no saved tracks yet: sync it first.",
    "Faltan binarios: abre Opciones (Ctrl+,) › «Mantenimiento» › «Binarios que faltan».":
        "Binaries are missing: open Options (Ctrl+,) › “Maintenance” › “Missing binaries”.",
    "«{title}» no tiene canciones que se puedan reproducir.": "“{title}” has no songs that can be played.",
    "Cargando «{title}»…": "Loading “{title}”…",
    "No se pudo abrir la lista. Comprueba la conexión.": "Couldn't open the playlist. Check the connection.",
    "No se pudo abrir la lista": "Couldn't open the playlist",
    "Comprueba la conexión y vuelve a abrirla desde el inicio.": "Check the connection and open it again from Home.",
    "Cargando la lista…": "Loading the playlist…",
    "Guardar como lista local": "Save as a local playlist",
    "Esta lista no tiene canciones que se puedan reproducir": "This playlist has no songs that can be played",
    "Buscando a {name}…": "Looking for {name}…",
    "No se pudo buscar al artista. Comprueba la conexión.": "Couldn't search for the artist. Check the connection.",
    "YouTube Music no tiene una página de artista para «{name}».": "YouTube Music has no artist page for “{name}”.",
    "Radio": "Radio",
    "Radio de {name}: sus canciones y otras parecidas": "{name} radio: their songs and similar ones",
    "Canciones de {name}": "Songs by {name}",
    "De {name}": "By {name}",
    "Cargando {value} de {name}…": "Loading {value} for {name}…",
    "la mezcla": "the mix",
    "la radio": "the radio",
    "No se pudo cargar la radio. Comprueba la conexión.": "Couldn't load the radio. Check the connection.",
    "Restaurar el volumen": "Restore the volume",
    "toda la cola": "whole queue",
    "una pista": "one track",
    "Repetir: {label}": "Repeat: {label}",
    "{ahead} por delante": "{ahead} up next",
    "{n} pista": "{n} track",
    "{n} pistas": "{n} tracks",
    "Reproducir a continuación": "Play next",
    "Añadir a la cola": "Add to queue",
    "Añadir a una lista": "Add to a playlist",
    "Quitar de esta lista": "Remove from this playlist",
    "Iniciar radio a partir de esta pista": "Start radio from this track",
    "Ir a {lead}": "Go to {lead}",
    "Quitar el me gusta": "Remove like",
    "Quitar de tu biblioteca de YouTube Music…": "Remove from your YouTube Music library…",
    "Guardar en tu biblioteca de YouTube Music": "Save to your YouTube Music library",
    "Quitar de esta lista en YouTube Music…": "Remove from this playlist on YouTube Music…",
    "Cambios en tu cuenta": "Changes to your account",
    "Con esta opción, «Me gusta», guardar en la biblioteca y crear o retocar listas modifican tu cuenta de YouTube "
    "Music en los servidores de Google, igual que en la web.\n\nLos cambios se envían de uno en uno y con pausas, pero "
    "cada uno es una petición más con tu cuenta. ¿Permitirlos?":
        "With this option, “Like”, saving to the library and creating or editing playlists change your YouTube Music "
        "account on Google's servers, just like on the web.\n\nChanges are sent one at a time with pauses, but each "
        "one is one more request with your account. Allow them?",
    "Permitir": "Allow",
    "Quitar de la biblioteca": "Remove from library",
    "¿Quitar «{title}» de tu biblioteca de YouTube Music?": "Remove “{title}” from your YouTube Music library?",
    "Quitar": "Remove",
    "la lista": "the playlist",
    "Quitar de la lista": "Remove from playlist",
    "¿Quitar «{title}» de «{name}» en tu cuenta de YouTube Music?": "Remove “{title}” from “{name}” in your YouTube Music account?",
    "La lista no tiene canciones de YouTube Music que copiar.": "The playlist has no YouTube Music songs to copy.",
    "Copiar en YouTube Music": "Copy to YouTube Music",
    "Nombre de la lista en tu cuenta (privada):": "Playlist name in your account (private):",
    "«{title}» añadida a la cola.": "“{title}” added to the queue.",
    "Cargando radio…": "Loading radio…",
    "Reproduce una canción para ver su letra.": "Play a song to see its lyrics.",
    "Sin letra disponible para esta canción.": "No lyrics available for this song.",
    "Buscando letra…": "Looking for lyrics…",
    "No se pudo buscar la letra (¿sin conexión?).": "Couldn't look for the lyrics (no connection?).",
    "de {source}": "from {source}",
    "de {source}, sin sincronizar": "from {source}, not synced",
    "El tiempo de espera del bloqueo terminó: ya puedes reintentar.": "The block's waiting time is over: you can retry now.",
    "Anti-bot: reintento en {format_time}": "Anti-bot: retrying in {format_time}",
    "Sincronizando la biblioteca…": "Syncing the library…",
    "Actualizando yt-dlp…": "Updating yt-dlp…",
    "Resolviendo el audio…": "Resolving the audio…",
    "Sesión caducada": "Session expired",
    "Error en la última sincronización": "Error in the last sync",
    "Sin credenciales": "No credentials",
    "Configura primero las credenciales de la cuenta secundaria.": "Set up the secondary account's credentials first.",
    "Error de sincronización: {error}": "Sync error: {error}",
    "Falta yt-dlp: descarga primero los binarios.": "yt-dlp is missing: download the binaries first.",
    "Buscando actualización de yt-dlp…": "Looking for a yt-dlp update…",
    "La actualización falló: {error}": "The update failed: {error}",
    "No falta ningún binario.": "No binaries are missing.",
    "Descargar binarios": "Download binaries",
    "Se descargarán desde sus repositorios oficiales en GitHub: {names}.\n\nDestino: {bin_dir}\n\n¿Continuar?":
        "They'll be downloaded from their official GitHub repositories: {names}.\n\nDestination: {bin_dir}\n\nContinue?",
    "Binarios instalados.": "Binaries installed.",
    "No se pudo obtener el diagnóstico: {error}": "Couldn't get the diagnostics: {error}",
    "híbrido": "hybrid",
    "siempre con cookies": "always with cookies",
    "siempre anónimo": "always anonymous",
    "desactivado": "off",
    "{FAST_PLAYER_CLIENT}, en pausa {format_time} más (fallaba)": "{FAST_PLAYER_CLIENT}, paused for {format_time} more (it was failing)",
    "{FAST_PLAYER_CLIENT} (solo en modo anónimo)": "{FAST_PLAYER_CLIENT} (anonymous mode only)",
    "Carpeta de datos: {app_home}": "Data folder: {app_home}",
    "no instalado": "not installed",
    "conectado": "connected",
    "no conectado": "not connected",
    "Modo de resolución: {mode}": "Resolution mode: {mode}",
    "Cliente rápido de YouTube: {fast}": "Fast YouTube client: {fast}",
    "presente": "present",
    "ausente": "missing",
    " (en uso permitido)": " (use allowed)",
    " (yt-dlp no las usa)": " (yt-dlp doesn't use them)",
    "Modo pegajoso con cookies: ": "Sticky cookie mode: ",
    "activo {format_time} más": "active for {format_time} more",
    "inactivo": "inactive",
    "Resoluciones en la última hora: {resolves_since} (tope para prefetch: {MAX_RESOLVES_PER_HOUR})":
        "Resolutions in the last hour: {resolves_since} (prefetch cap: {MAX_RESOLVES_PER_HOUR})",
    "Resoluciones en 24 h: {resolves_since}": "Resolutions in 24 h: {resolves_since}",
    "Última comprobación de yt-dlp: {format_ago}": "Last yt-dlp check: {format_ago}",
    "Estado de sincronización: {value}": "Sync state: {value}",
    "Diagnóstico": "Diagnostics",
    "Busca música nueva": "Find new music",
    "Empieza a escribir: las sugerencias y los resultados aparecen solos. No hace falta iniciar sesión.":
        "Start typing: suggestions and results appear on their own. No need to sign in.",
    "Sin resultados para «{query}»": "No results for “{query}”",
    "Prueba con otro título o artista, o con una de las sugerencias de arriba.":
        "Try another title or artist, or one of the suggestions above.",
    "listas de la comunidad": "community playlists",
    "artistas": "artists",
    "Buscando {what}…": "Searching {what}…",
    "No se pudieron buscar {what}": "Couldn't search {what}",
    "Comprueba la conexión y pulsa Intro para reintentar.": "Check the connection and press Enter to retry.",
    "Sin {what} para «{query}»": "No {what} for “{query}”",
    "Prueba con otro nombre o con menos palabras.": "Try another name or fewer words.",
    "Buscando «{query}»…": "Searching “{query}”…",
    "La búsqueda falló. Pulsa Intro para reintentar.": "The search failed. Press Enter to retry.",
    "artista": "artist",
    "{count} para «{query}»": "{count} for “{query}”",
    "Error de búsqueda: {error}": "Search error: {error}",
    "{plural} para «{query}»": "{plural} for “{query}”",
    "{n} lista de la comunidad": "{n} community playlist",
    "{n} listas de la comunidad": "{n} community playlists",
    "{n} artista": "{n} artist",
    "{n} artistas": "{n} artists",
    "{n} resultado": "{n} result",
    "{n} resultados": "{n} results",
    "La música sigue sonando": "The music keeps playing",
    "YT Music Nativo queda en la bandeja. Para cerrarlo del todo, usa «Salir definitivamente» en el menú de este "
    "icono.":
        "YT Music Nativo stays in the tray. To close it completely, use “Quit for good” in this icon's menu.",
    "No se pudo cambiar el inicio con Windows.": "Couldn't change starting with Windows.",
    "Mostrar en Discord": "Show on Discord",
    "Discord necesita el id de una aplicación registrada en\ndiscord.com/developers/applications (su nombre es lo "
    "que\naparece tras «Escuchando»). Pega aquí su «Application ID»:":
        "Discord needs the id of an application registered at\ndiscord.com/developers/applications (its name is "
        "what\nappears after “Listening to”). Paste its “Application ID” here:",
    "Ese id no es válido: son solo cifras (unas 18 o 19).": "That id isn't valid: it's digits only (about 18 or 19).",
    "la cuenta": "the account",
    "Desconectar Last.fm ({who})": "Disconnect Last.fm ({who})",
    "Conectar con Last.fm…": "Connect to Last.fm…",
    "Conecta antes una cuenta de Last.fm.": "Connect a Last.fm account first.",
    "Desconectar Last.fm": "Disconnect Last.fm",
    "Se olvidará la sesión de Last.fm de este equipo y las escuchas que esperaban en la cola. Tu perfil de Last.fm "
    "no cambia.":
        "The Last.fm session on this computer and the plays waiting in the queue will be forgotten. Your Last.fm "
        "profile doesn't change.",
    "Desconectar": "Disconnect",
    "Last.fm desconectado.": "Last.fm disconnected.",
    "Abriendo Last.fm en el navegador…": "Opening Last.fm in the browser…",
    "Conectar con Last.fm": "Connect to Last.fm",
    "Last.fm pide una clave de aplicación propia. Créala gratis en\nlast.fm/api/account/create y pega aquí su «API "
    "key»:":
        "Last.fm needs your own application key. Create one for free at\nlast.fm/api/account/create and paste its "
        "“API key” here:",
    "Y ahora su «Shared secret» (se guarda cifrado):": "And now its “Shared secret” (it's saved encrypted):",
    "No se pudo guardar la clave de Last.fm de forma segura.": "Couldn't save the Last.fm key securely.",
    "No se pudo conectar con Last.fm: {error}": "Couldn't connect to Last.fm: {error}",
    "Se abrió Last.fm en el navegador.": "Last.fm opened in the browser.",
    "Inicia sesión allí, pulsa «Sí, permitir acceso» y vuelve aquí.": "Sign in there, press “Yes, allow access” and come back here.",
    "Ya lo permití": "I've allowed it",
    "Last.fm no confirmó el permiso. Vuelve a intentarlo.": "Last.fm didn't confirm the permission. Try again.",
    "Last.fm conectado como {value}: tus escuchas se enviarán allí.": "Last.fm connected as {value}: your plays will be sent there.",
    "tu cuenta": "your account",
    "Versión {version} disponible: ver novedades": "Version {version} available: see what's new",
    "Hay una versión nueva de la app ({version}). Su enlace está al pie de la barra lateral.":
        "There's a new version of the app ({version}). Its link is at the bottom of the sidebar.",
    "Parar la música dentro de (minutos):": "Stop the music in (minutes):",
    "Al acabar": "When it ends",
    "La música se parará al terminar esta canción. Pulsa para cancelar.":
        "The music will stop when this song ends. Click to cancel.",
    "La música se parará a las {ends}. Pulsa para posponer o cancelar.":
        "The music will stop at {ends}. Click to postpone or cancel.",
    "Posponer 10 minutos": "Postpone 10 minutes",
    "Temporizador de apagado: la música se detuvo.": "Sleep timer: the music stopped.",
    "Exportar copia de seguridad": "Export backup",
    "Copia de seguridad (*.json)": "Backup (*.json)",
    "No se pudo guardar la copia: {error}": "Couldn't save the backup: {error}",
    "Copia guardada: {summary}.": "Backup saved: {summary}.",
    "Importar copia de seguridad": "Import backup",
    "Copia de seguridad (*.json);;Todos los archivos (*)": "Backup (*.json);;All files (*)",
    "No se pudo leer la copia.": "Couldn't read the backup.",
    "La copia tiene {summary}.": "The backup has {summary}.",
    "Fusionar añade lo que falta sin borrar nada ni repetir canciones. Sobrescribir deja tus listas locales, el "
    "historial y las preferencias como estaban en la copia (las listas de tu cuenta no se tocan).":
        "Merge adds what's missing without deleting anything or repeating songs. Overwrite leaves your local "
        "playlists, history and preferences as they were in the backup (your account's playlists aren't touched).",
    "Fusionar": "Merge",
    "Sobrescribir": "Overwrite",
    "No se pudo importar la copia: no se cambió nada.": "Couldn't import the backup: nothing was changed.",
    "Copia importada: {summary}.": "Backup imported: {summary}.",

    # -- ui/mini_player.py, now.py, search.py, taskbar.py, tray.py -----------
    "Volver a la ventana principal": "Back to the main window",
    "Anterior": "Previous",
    "Siguiente": "Next",
    "Pausar": "Pause",
    "Ocultar el panel (Ctrl+I)": "Hide the panel (Ctrl+I)",
    "Sobre esta canción": "About this song",
    "Más información": "More information",
    "Menos información": "Less information",
    "Ver artista": "See artist",
    "Abrir la página del artista: canciones, álbumes, listas y música parecida":
        "Open the artist's page: songs, albums, playlists and similar music",
    "Reproduce una canción para verla aquí en grande, con su artista y música parecida.":
        "Play a song to see it here in large, with its artist and similar music.",
    "Buscando información y música parecida…": "Looking for information and similar music…",
    "No se pudo cargar la información de esta canción (¿sin conexión?).":
        "Couldn't load this song's information (no connection?).",
    "No hay más información sobre esta canción.": "There's no more information about this song.",
    "Canciones": "Songs",
    "Listas de la comunidad": "Community playlists",
    "Artistas": "Artists",
    "{name}. Abrir la página del artista": "{name}. Open the artist's page",
    "Abrir la página del artista": "Open the artist's page",
    "Artista con {note}": "Artist with {note}",
    "Mostrar la ventana": "Show the window",
    "Salir definitivamente": "Quit for good",

    # -- ui/widgets.py --------------------------------------------------------
    "hace un momento": "a moment ago",
    "nunca": "never",
    "hace {int} min": "{int} min ago",
    "hace {int} h": "{int} h ago",
    "hace {int} d": "{int} d ago",
    "Título": "Title",
    "Duración": "Duration",
    "No disponible desde el {since}. Se reintentará sola pasados {days} días; un doble clic la reintenta ahora.":
        "Unavailable since {since}. It will be retried on its own after {days} days; double-click to retry now.",
    "Usa Firefox para este paso. Chrome en Windows liga la sesión al dispositivo y, copiada a esta app, deja de "
    "valer en pocas horas.":
        "Use Firefox for this step. Chrome on Windows binds the session to the device and, copied to this app, it "
        "stops working within a few hours.",
    "1. Abre una ventana privada nueva de Firefox, inicia sesión en YouTube con la cuenta secundaria y entra en "
    "https://music.youtube.com.\n2. Pulsa F12, abre la pestaña Red y escribe «browse» en el filtro.\n3. La lista "
    "empieza vacía: solo registra lo que ocurre con el panel abierto. Haz clic en Biblioteca o Explorar en la página "
    "(o recarga con F5) para que aparezca una petición.\n4. Busca una fila con método POST y dominio "
    "music.youtube.com. Las GET no sirven; si solo ves GET, sigue navegando.\n5. Clic derecho sobre esa fila → Copiar "
    "valor → Copiar cabeceras de la petición, y pégalas abajo.":
        "1. Open a new Firefox private window, sign in to YouTube with the secondary account and go to "
        "https://music.youtube.com.\n2. Press F12, open the Network tab and type “browse” in the filter.\n3. The list "
        "starts empty: it only records what happens while the panel is open. Click Library or Explore on the page "
        "(or reload with F5) so a request appears.\n4. Look for a row with method POST and domain music.youtube.com. "
        "GET rows don't work; if you only see GET, keep browsing.\n5. Right-click that row → Copy Value → Copy "
        "Request Headers, and paste them below.",
    "1. En esa misma ventana privada ve a https://www.youtube.com/robots.txt (debe ser la única pestaña privada "
    "abierta).\n2. Exporta las cookies de youtube.com en formato Netscape con una extensión de exportación de "
    "cookies.\n3. Cierra la ventana privada sin cerrar sesión y no vuelvas a abrir esa sesión en el navegador: "
    "YouTube rota las cookies de las pestañas abiertas.":
        "1. In that same private window go to https://www.youtube.com/robots.txt (it must be the only private tab "
        "open).\n2. Export the youtube.com cookies in Netscape format with a cookie export extension.\n3. Close the "
        "private window without signing out and don't open that session in the browser again: YouTube rotates the "
        "cookies of open tabs.",
    "Aunque no exportes cookies, al terminar cierra la ventana privada sin cerrar sesión.":
        "Even if you don't export cookies, close the private window without signing out when you're done.",
    "No configurado": "Not set up",
    "%d/%m/%Y %H:%M": "%Y-%m-%d %H:%M",
    "No se puede leer ({stamp}): guárdalo de nuevo": "Can't be read ({stamp}): save it again",
    "cifrado para tu usuario de Windows": "encrypted for your Windows user",
    "aún sin cifrar": "not encrypted yet",
    "Configurado ({stamp}, {sealed})": "Set up ({stamp}, {sealed})",
    "las cabeceras no incluyen la línea «cookie»: copia la sección Request Headers completa de una petición «browse» "
    "con la sesión iniciada":
        "the headers don't include the “cookie” line: copy the whole Request Headers section of a “browse” request "
        "while signed in",
    "Cuenta y reproducción": "Account and playback",
    "Credenciales": "Credentials",
    "Usa siempre la cuenta secundaria. Los archivos se guardan en {cred_dir} (fuera de la carpeta del proyecto).":
        "Always use the secondary account. The files are saved in {cred_dir} (outside the project folder).",
    "Iniciar sesión con Firefox (recomendado)…": "Sign in with Firefox (recommended)…",
    "Sin copiar cabeceras: inicias sesión en una ventana de Firefox aparte y la app recoge la sesión al cerrarla.":
        "Without copying headers: you sign in in a separate Firefox window and the app picks up the session when you "
        "close it.",
    "Biblioteca (ytmusicapi)": "Library (ytmusicapi)",
    "Pega aquí las cabeceras de la petición…": "Paste the request headers here…",
    "Guardar cabeceras": "Save headers",
    "Reproducción (cookies.txt para yt-dlp)": "Playback (cookies.txt for yt-dlp)",
    "Seleccionar cookies.txt…": "Choose cookies.txt…",
    "Cabeceras": "Headers",
    "Pega primero las cabeceras de la petición.": "Paste the request headers first.",
    "Cabeceras guardadas.": "Headers saved.",
    "Cabeceras guardadas.\n\n": "Headers saved.\n\n",
    "No se pudieron procesar las cabeceras:\n{exc}": "Couldn't process the headers:\n{exc}",
    "Seleccionar cookies.txt": "Choose cookies.txt",
    "Cookies (*.txt);;Todos (*.*)": "Cookies (*.txt);;All (*.*)",
    "No se pudo leer el archivo:\n{exc}": "Couldn't read the file:\n{exc}",
    "El archivo no parece estar en formato Netscape (cookies.txt).": "The file doesn't look like Netscape format (cookies.txt).",
    "No se pudieron guardar las cookies:\n{exc}": "Couldn't save the cookies:\n{exc}",
    "\n\nSe guardó una copia cifrada: ya puedes borrar el archivo original ({name}).":
        "\n\nAn encrypted copy was saved: you can now delete the original file ({name}).",
    "Cookies": "Cookies",
    "Cookies guardadas.": "Cookies saved.",
    "Cookies guardadas.\n\n": "Cookies saved.\n\n",
    "Modo de resolución de yt-dlp": "yt-dlp resolution mode",
    "Híbrido (recomendado): anónimo, y cookies solo ante un bloqueo anti-bot o una restricción de edad.":
        "Hybrid (recommended): anonymous, with cookies only on an anti-bot block or an age restriction.",
    "Siempre con cookies de la cuenta secundaria.": "Always with the secondary account's cookies.",
    "Siempre anónimo: nunca pasa cookies a yt-dlp.": "Always anonymous: never passes cookies to yt-dlp.",
    "Riesgo de la cuenta": "Account risk",
    "yt-dlp advierte que usar una cuenta puede llevar a un baneo temporal o permanente de esa cuenta. La cuenta "
    "secundaria limita el impacto, pero no lo elimina, y este uso va contra los términos de servicio de YouTube. "
    "Mientras no marques la casilla, yt-dlp nunca recibirá las cookies (la app funcionará solo en modo anónimo).":
        "yt-dlp warns that using an account can lead to a temporary or permanent ban of that account. The secondary "
        "account limits the impact but doesn't remove it, and this use goes against YouTube's terms of service. "
        "Until you tick the box, yt-dlp will never receive the cookies (the app will only work in anonymous mode).",
    "Acepto el riesgo de usar las cookies de la cuenta secundaria con yt-dlp":
        "I accept the risk of using the secondary account's cookies with yt-dlp",

    # -- ui/settings.py -------------------------------------------------------
    "Temas de las opciones": "Option topics",
    "Los cambios se guardan al momento.": "Changes are saved right away.",
    "General": "General",
    "Idioma, aspecto y qué hace la ventana al cerrarla.": "Language, appearance and what the window does when closed.",
    "Idioma y aspecto": "Language and appearance",
    "Idioma": "Language",
    "Como Windows ({language})": "Same as Windows ({language})",
    "Reiniciar ahora": "Restart now",
    "Falta reiniciar": "Restart needed",
    "Tema": "Theme",
    "Colores claros u oscuros. «Como Windows» cambia con el sistema.":
        "Light or dark colours. “Same as Windows” changes with the system.",
    "Ventana": "Window",
    "La X oculta la ventana y la música sigue; para salir, «Salir» en el menú o en la bandeja.":
        "The X hides the window and the music keeps going; to leave, use “Quit” in the menu or the tray.",
    "La ventana desaparece de la barra de tareas y queda junto al reloj.":
        "The window leaves the taskbar and stays next to the clock.",
    "Al encender el equipo": "When the computer starts",
    "Iniciar con Windows": "Start with Windows",
    "La app arranca en la bandeja al iniciar sesión, sin abrir la ventana.":
        "The app starts in the tray when you sign in to Windows, without opening the window.",
    "No disponible en este equipo.": "Not available on this computer.",
    "Cómo suena la música y qué se recuerda de ella.": "How the music sounds and what's remembered about it.",
    "Al reproducir": "While playing",
    "Cuando se acaba la cola, sigue sonando música parecida.": "When the queue runs out, similar music keeps playing.",
    "Nivela el volumen para que ninguna canción suene mucho más fuerte que otra.":
        "Levels the volume so no song sounds much louder than another.",
    "Al abrir la app vuelven la cola, la canción y el segundo en que te quedaste.":
        "When the app opens, the queue, the song and the second where you stopped come back.",
    "Anota en este equipo lo que escuchas. No se envía a ningún sitio.":
        "Notes what you listen to on this computer. It isn't sent anywhere.",
    "Cuando el temporizador para la música, la app también se cierra.":
        "When the timer stops the music, the app closes too.",
    "Cuenta": "Account",
    "Usa siempre una cuenta secundaria de YouTube Music.": "Always use a secondary YouTube Music account.",
    "Inicio de sesión": "Sign-in",
    "Inicias sesión en un Firefox aparte y la app toma de ahí las credenciales. Es lo recomendado.":
        "You sign in in a separate Firefox and the app takes the credentials from there. This is recommended.",
    "Iniciar sesión…": "Sign in…",
    "Credenciales y modo de reproducción": "Credentials and playback mode",
    "Cabeceras pegadas a mano, cookies.txt y cómo se obtiene el audio.":
        "Headers pasted by hand, cookies.txt and how the audio is obtained.",
    "Abrir…": "Open…",
    "Cambios en tu cuenta de YouTube Music": "Changes to your YouTube Music account",
    "«Me gusta», guardar en la biblioteca y editar listas cambian tu cuenta en YouTube Music, igual que en la web.":
        "“Like”, saving to the library and editing playlists change your account on YouTube Music, just like on the "
        "web.",
    "Integraciones": "Integrations",
    "Otros servicios que pueden saber lo que escuchas. Todo apagado de entrada.":
        "Other services that can know what you listen to. Everything is off to begin with.",
    "Discord": "Discord",
    "Last.fm": "Last.fm",
    "Tu estado de Discord enseña la canción que suena.": "Your Discord status shows the song that's playing.",
    "Cuenta de Last.fm": "Last.fm account",
    "Conectada como {user}.": "Connected as {user}.",
    "Registra en tu perfil de Last.fm cada canción que escuchas.": "Records every song you listen to on your Last.fm profile.",
    "Sin conectar. Hace falta una clave de aplicación de Last.fm.": "Not connected. A Last.fm application key is needed.",
    "Conectar…": "Connect…",
    "Mantenimiento": "Maintenance",
    "Actualizaciones, arreglos y tus datos.": "Updates, fixes and your data.",
    "Actualizaciones": "Updates",
    "Mira una vez al día si hay versión nueva. Nada se descarga solo.":
        "Checks once a day for a new version. Nothing downloads on its own.",
    "Versión nueva de la app": "New app version",
    "Comprueba ahora si hay una versión publicada.": "Checks now whether a version has been released.",
    "yt-dlp": "yt-dlp",
    "Es lo que obtiene el audio de YouTube; se actualiza a menudo.": "It's what gets the audio from YouTube; it updates often.",
    "Buscar actualización": "Check for update",
    "Binarios que faltan": "Missing binaries",
    "mpv, yt-dlp y deno, si aún no están en la carpeta de la app.": "mpv, yt-dlp and deno, if they're not in the app folder yet.",
    "Descargar": "Download",
    "Si algo falla": "If something fails",
    "Estado de los binarios, las credenciales y la conexión.": "State of the binaries, the credentials and the connection.",
    "Ver…": "View…",
    "Sincronizar todo de nuevo": "Sync everything again",
    "Vuelve a descargar todas las listas, también las que no han cambiado.":
        "Downloads all playlists again, including the ones that haven't changed.",
    "Sincronizar": "Sync",
    "Tus datos": "Your data",
    "Guarda en un archivo tus listas, el historial y las preferencias.":
        "Saves your playlists, history and preferences to a file.",
    "Exportar…": "Export…",
    "Recupera lo guardado en una copia anterior.": "Restores what was saved in an earlier backup.",
    "Importar…": "Import…",
    "Carpeta de datos": "Data folder",
    "La base, los registros y las credenciales de la app.": "The app's database, logs and credentials.",
    "Abrir": "Open",
    "La app se verá en {language} la próxima vez que se abra.": "The app will be in {language} the next time it opens.",
    "Se aplica al volver a abrir la app.": "Applies when the app is opened again.",
}
