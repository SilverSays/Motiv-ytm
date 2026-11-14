# YT Music Nativo

Un cliente de escritorio para YouTube Music en Windows, hecho con Qt en lugar
de un navegador empaquetado. Arranca rápido, ocupa poca memoria y tiene una
interfaz propia, clara y fría, con el color de cada lista sacado de su carátula.

> **Proyecto en desarrollo.** La app ya se puede usar a diario, pero sigo
> añadiendo funciones y puliendo detalles. Puede haber cambios grandes de una
> versión a otra.

> **Aviso legal.** Este proyecto no es oficial ni tiene relación alguna con
> YouTube, YouTube Music ni Google. Usa sus servicios de una forma que no está
> prevista por ellos y que puede ir contra sus [términos de
> servicio](https://www.youtube.com/t/terms). Usarlo es responsabilidad de cada
> persona, también lo que pueda pasarle a su cuenta: se recomienda usar una
> cuenta secundaria. La app reproduce música en streaming, igual que la web;
> no está pensada para descargar ni redistribuir contenido, y no debe usarse
> para eso. Todo el contenido pertenece a sus autores.

## Qué hace

- **Tu biblioteca**: sincroniza tus listas de YouTube Music y las guarda en una
  base de datos local, así se abren al instante y sin conexión a la cuenta.
- **Listas locales**: crea listas que solo existen en la app, o guarda como
  propia cualquier lista o álbum que encuentres.
- **Búsqueda sin iniciar sesión**: canciones, listas de la comunidad y
  artistas, con sugerencias mientras escribes y búsquedas recientes.
- **Inicio**: tus listas más escuchadas, lo que has escuchado hace poco y
  recomendaciones de listas parecidas a tu música.
- **Historial de escucha**: cada canción que oyes al menos medio minuto queda
  anotada, solo en tu equipo. Enseña lo último, lo más escuchado del mes y lo
  más escuchado de siempre, y sirve para recomendarte música aunque no tengas
  cuenta. Se puede desactivar o borrar cuando quieras.
- **Página de artista**: canciones populares, álbumes, sencillos, listas,
  artistas parecidos y su biografía.
- **«Ahora suena»**: la carátula en grande, información del artista y música
  parecida a la que suena.
- **Letras sincronizadas** de [LRCLIB](https://lrclib.net), que siguen la
  canción línea a línea.
- **Reproducción**: cola, aleatorio, repetir, radio a partir de una canción o
  de un artista, y precarga de las siguientes pistas para que no haya esperas.
- **Robustez**: si YouTube cambia algo y deja de sonar, la app actualiza sola
  `yt-dlp`, comprueba que la versión nueva funciona y vuelve a la anterior si no.
- **Interfaz**: animaciones con propósito (paneles que se deslizan, carátulas
  que viajan de la tarjeta a la cabecera, listas que aparecen escalonadas) que
  respetan la opción de Windows de reducir las animaciones; navegación completa
  con el teclado.

### Atajos de teclado

| Atajo | Acción |
| :--- | :--- |
| Espacio | Reproducir o pausar |
| Ctrl+→ / Ctrl+← | Canción siguiente / anterior |
| Ctrl+F | Buscar |
| Ctrl+L | Mostrar la letra |
| Ctrl+I | Mostrar «Ahora suena» |
| Ctrl+B | Contraer o desplegar la barra lateral |
| Ctrl+N | Nueva lista local |
| Alt+← | Volver a la vista anterior |

## Instalación

Requisitos: **Windows 10 u 11** y **Python 3.10 o superior** (desde
[python.org](https://www.python.org/downloads/), con el lanzador `py`).

1. Descarga o clona este repositorio.
2. Ejecuta `instalar.bat`. Crea un entorno de Python propio en
   `%LOCALAPPDATA%\YTMusicNativo\venv` e instala las dependencias
   (`PySide6-Essentials` y `ytmusicapi`).
3. Ejecuta `iniciar.bat` para abrir la app. Si el entorno no existe todavía,
   lo instala primero.
4. La primera vez, un aviso arriba ofrece **Descargar ahora** los programas
   que la app usa para reproducir. Se descargan de sus fuentes oficiales en
   GitHub y no forman parte de este repositorio:
   - [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (canal *nightly*): obtiene
     la dirección del audio.
   - [`deno`](https://github.com/denoland/deno): lo necesita `yt-dlp`.
   - [`mpv`](https://mpv.io) (build de
     [shinchiro](https://github.com/shinchiro/mpv-winbuild-cmake)): reproduce
     el audio.

   También se pueden descargar desde una consola con
   `python -m ytm_native.playback.bootstrap`.

Sin cuenta ya se puede buscar, ver artistas y escuchar cualquier canción.

## Configuración de la cuenta (opcional)

Solo hace falta para ver **tu biblioteca**. Se hace desde el menú **⋯** (al pie
de la barra lateral) → **Cuenta y reproducción**.

**Usa una cuenta secundaria**, no la principal.

1. Abre una **ventana privada de Firefox** e inicia sesión en
   [music.youtube.com](https://music.youtube.com) con esa cuenta. Usa Firefox:
   Chrome liga la sesión al equipo y las credenciales copiadas caducan en pocas
   horas.
2. Abre las herramientas de desarrollo (F12) → pestaña **Red**, filtra por
   `browse` y recarga la página.
3. Haz clic derecho en una petición `browse` → **Copiar** → **Copiar
   encabezados de la solicitud**.
4. En la app, pestaña **Credenciales**, pega las cabeceras y pulsa **Guardar
   cabeceras**.
5. Cierra la ventana privada **sin cerrar sesión** y no vuelvas a abrirla:
   cerrar sesión invalida las credenciales copiadas.

**Cookies para la reproducción (avanzado, opcional).** Por defecto el audio se
obtiene sin cuenta. Si YouTube bloquea esas peticiones, puedes cargar un
`cookies.txt` (exportado como explica la [guía de
yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies))
y aceptar expresamente el riesgo: con cookies, la reproducción queda ligada a
la cuenta.

### Dónde se guarda cada cosa

Nada de esto se guarda en la carpeta del proyecto ni sale de tu equipo:

| Qué | Dónde |
| :--- | :--- |
| Entorno de Python | `%LOCALAPPDATA%\YTMusicNativo\venv` |
| `yt-dlp`, `deno`, `mpv` | `%LOCALAPPDATA%\YTMusicNativo\bin` |
| Credenciales (`browser.json`, `cookies.txt`) | `%LOCALAPPDATA%\YTMusicNativo\credenciales` |
| Biblioteca, caché de carátulas y registros | `%LOCALAPPDATA%\YTMusicNativo` |

Para desinstalar, borra la carpeta del proyecto y
`%LOCALAPPDATA%\YTMusicNativo`.

## Estado y planes

La app está en desarrollo activo. Sigo trabajando en funciones nuevas y en
pulir la interfaz y la estabilidad. Si algo falla, abre un *issue* contando qué
hacías y, si puedes, adjunta el registro de `%LOCALAPPDATA%\YTMusicNativo\logs`
(revísalo antes: puede incluir nombres de canciones y listas).

## Para colaborar o hacer un fork

Las aportaciones son bienvenidas. Antes de un cambio grande, abre un *issue*
para comentarlo.

### Cómo está organizado

```
ytm_native/
├── __main__.py, app.py   Arranque: crea la base de datos, el reproductor y la ventana
├── core/                 La base que usa todo lo demás
├── playback/             Todo lo que hace sonar una canción
├── services/             Los datos de YouTube Music y de LRCLIB
├── ui/                   La interfaz Qt
└── tools/                Herramientas de desarrollo (no forman parte de la app)
tests/                    Pruebas automáticas (sin red, con un mpv y un yt-dlp simulados)
instalar.bat, iniciar.bat Instalación y arranque en Windows
```

**`core/`: la base**

| Archivo | Qué hace |
| :--- | :--- |
| `paths.py` | Rutas de datos, binarios y credenciales en `%LOCALAPPDATA%` (las variables `YTMN_HOME`, `YTMN_BIN_DIR` y `YTMN_CRED_DIR` las cambian). |
| `config.py` | Constantes: tiempos, límites de peticiones, cadencia de sincronización. |
| `db.py` | Base de datos SQLite: pistas, listas, listas locales, ajustes y cachés. |
| `errors.py` | Clasifica los errores de `yt-dlp` (bloqueo, red, sesión caducada, no disponible…) y sus mensajes. |

**`playback/`: reproducción**

| Archivo | Qué hace |
| :--- | :--- |
| `extractor.py` | El *fetcher* del audio: lanza `yt-dlp` para obtener la dirección de cada canción, guarda una caché de direcciones y actualiza `yt-dlp` con vuelta atrás si la versión nueva falla. |
| `mpv_ipc.py` | Lanza `mpv` como proceso aparte y lo controla por su tubería IPC. |
| `history.py` | Decide cuándo una canción cuenta como escuchada y la anota en el historial. |
| `player.py` | El reproductor: cola, aleatorio, repetir, radio, precarga y recuperación de errores. |
| `bootstrap.py` | Descarga e instala `yt-dlp`, `deno` y `mpv`. |

**`services/`: datos en línea**

| Archivo | Qué hace |
| :--- | :--- |
| `sync.py` | Sincroniza la biblioteca de la cuenta con `ytmusicapi`, gestiona la sesión y busca en la biblioteca local. |
| `discover.py` | Búsqueda, sugerencias, recomendaciones del inicio, listas ajenas, páginas de artista y la información de «Ahora suena». |
| `lyrics.py` | Letras de LRCLIB y cuál es la línea actual. |

**`ui/`: interfaz**

| Archivo | Qué hace |
| :--- | :--- |
| `main_window.py` | La ventana: une las vistas, el reproductor y los paneles, y decide qué se enseña. |
| `theme.py` | Colores, tipografía y hoja de estilos. Es el único sitio con valores de color. |
| `motion.py` | Curvas, duraciones y `Tween`, el valor animado que usan todas las animaciones. |
| `components.py` | Piezas reutilizables: botones, cabecera de la lista, tabla de pistas, avisos, carátulas animadas. |
| `home.py` | La pantalla de inicio y las tarjetas de listas. |
| `search.py` | Partes de la búsqueda: pestañas y rejilla de resultados. |
| `artist.py` | La página de artista. |
| `now.py` | El panel «Ahora suena». |
| `widgets.py` | El modelo de la tabla de pistas y el diálogo de la cuenta. |
| `art.py` | Descarga y caché de carátulas, y el color que se saca de cada una. |
| `chrome.py`, `focus.py` | Detalles de toda la ventana: barra de título, menús y anillo de foco solo con teclado. |

**`tools/fase0.py`** es la prueba de viabilidad con la que empezó el proyecto:
mide cuántas peticiones bloquea YouTube con y sin cuenta.

### Pruebas

```
%LOCALAPPDATA%\YTMusicNativo\venv\Scripts\python.exe -m pip install pytest
%LOCALAPPDATA%\YTMusicNativo\venv\Scripts\python.exe -m pytest tests
```

No tocan la red ni tus datos: usan una carpeta temporal.

### Normas del código

- Colores, radios y tipografía, solo desde `ui/theme.py`.
- Toda animación pasa por `ui/motion.py`, dura como mucho 300 ms y respeta
  las animaciones reducidas de Windows. Nada se anima en acciones de teclado
  que se repiten mucho.
- Los textos de la interfaz están en español, en frases normales (sin
  mayúsculas sostenidas).

## Licencia

[GPL-3.0](LICENSE). Puedes usar, estudiar, modificar y redistribuir el código,
siempre que las versiones que distribuyas sigan siendo libres bajo la misma
licencia.

Usa a su vez [PySide6](https://doc.qt.io/qtforpython/) (LGPL),
[ytmusicapi](https://github.com/sigma67/ytmusicapi) (MIT),
[yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense),
[mpv](https://mpv.io) (GPL/LGPL) y [deno](https://deno.com) (MIT), que se
instalan aparte y conservan sus propias licencias.
