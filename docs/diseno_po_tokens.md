# Contingencia de PO tokens: diseño (Tarea 8.7)

> **Estado:** documento de diseño. No hay código de producción asociado. Se
> implementa solo cuando se cumplan las condiciones de activación de la
> sección 2 de forma reproducible.

## 1. Contexto

YouTube exige a algunos clientes un *Proof of Origin token* (PO token) para
entregar los flujos de audio y vídeo (GVS) o la respuesta del reproductor.
Sin él, yt-dlp recibe errores HTTP 403 al descargar, se queda sin formatos o
el servidor le pide la verificación anti-bot.

Hoy la app no lo necesita:

- `ExtractorManager.base_args()` resuelve con los clientes que elige yt-dlp
  (nivel `default`) o con el cliente rápido `visionos`
  (`config.FAST_PLAYER_CLIENT`, nivel `fast`).
- yt-dlp ya resuelve el desafío de firma ("n challenge") con el runtime Deno
  incluido en `bin/`: se le pasa `--js-runtimes deno:<ruta>`.

Según la guía de yt-dlp (*PO Token Guide*, revisada en julio de 2026), estos
clientes piden token: `web` (GVS y subtítulos; solo formatos SABR),
`web_safari` (GVS, salvo HLS), `mweb` (GVS), `web_music` (GVS), `tv_simply`
(GVS), `web_creator` (GVS y cuenta), y `android` e `ios` (GVS o Player).
Estos no lo piden: `tv`, `web_embedded` y `android_vr`. YouTube cambia esta
tabla con frecuencia: por eso el plan no se activa por calendario, sino por
señales medidas.

La mayoría de los tokens (GVS y Player de `web`) van ligados al id del vídeo:
hace falta uno por pista. Su vida puede ser de solo unas 12 horas. Copiar un
token a mano ya no sirve.

## 2. Señales de activación

Las señales salen de la taxonomía de `ytm_native/core/errors.py`, que solo
mira las líneas `ERROR:` de yt-dlp y respeta el orden bloqueo, red, runtime,
sesión, disponibilidad, extracción.

### 2.1 Qué cuenta como señal

| Señal | `ErrorKind` | Patrones de `errors.py` |
| :--- | :--- | :--- |
| Bloqueo anti-bot | `BOT_BLOCK` | `confirm you're not a bot`, `http error 429`, `too many requests`, `this content isn't available, try again later`, `unusual traffic` |
| Fallo de extracción | `EXTRACTION` | `n challenge`, `challenge solving failed`, `requested format is not available`, `no video formats found`, `sabr` |
| 403 al cargar en mpv una URL recién resuelta | (lo ve `PlaybackController`) | `end-file` con error tras agotar `LOAD_RETRY_DELAYS_MS` |

### 2.2 Qué se descarta antes de contar

- `NETWORK`: sin conexión no hay nada que concluir.
- `UNAVAILABLE` y `NEEDS_LOGIN`: son de la pista, no del cliente.
- `RUNTIME_MISSING`: falta Deno o es antiguo, y eso se arregla con
  «Descargar binarios que faltan».
- Una versión de yt-dlp recién instalada que falla: ya la cubre la reversión
  automática de la Fase 2 (`bad_versions`).

### 2.3 Umbral

La contingencia se activa solo si se cumplen **las tres** condiciones a la vez:

1. **Persistencia:** en `resolve_log`, durante 48 horas seguidas, más del 30 %
   de las resoluciones terminan en `BOT_BLOCK` o `EXTRACTION`, con un mínimo
   de 20 intentos (por debajo de eso, el ruido de un día malo pesa demasiado).
2. **Simultaneidad en los clientes primarios:** fallan los niveles `fast` y
   `default` de `ExtractorManager`, que juntos cubren los clientes que yt-dlp
   elige (`mweb`, `ios`, `android`, `web`) y `visionos`. Si un solo cliente
   falla y otro responde, basta con cambiar el cliente en `config.py`, sin
   token.
3. **Con y sin sesión:** el fallo se repite tanto sin cookies como con la
   copia efímera de la cuenta. Si solo falla una de las dos, el problema es la
   sesión (cookies caducadas o ligadas al dispositivo), no el origen.

Con la versión de yt-dlp al día y Deno presente, que se cumplan las tres
indica que YouTube exige PO token a todos los clientes útiles.

## 3. El proveedor `bgutil-ytdlp-pot-provider`

Es un plugin de yt-dlp (lo mantiene un mantenedor de yt-dlp; versión 2.0.2 en
el README consultado) que genera los tokens con BotGuard en un runtime de
JavaScript. Necesita yt-dlp 2025.05.22 o posterior y **Node.js 22+ o Deno
2.0+**. Tiene dos modos.

### Modelo A: script (sin servidor)

- yt-dlp lanza un proceso del runtime por cada token y lo guarda en caché.
  La caché dura `TOKEN_TTL` horas (6 por defecto).
- Para encontrar el runtime, el plugin respeta `--js-runtimes RUNTIME:PATH`.
  Como la app ya pasa `--js-runtimes deno:<bin>\deno.exe`, **Deno basta**:
  no hace falta Node. Ojo: `--no-js-runtimes` no le impide usar el runtime.
- Argumento: `--extractor-args "youtubepot-bgutilscript:server_home=<carpeta>\server"`.
  Sin él, busca la carpeta en `%USERPROFILE%\bgutil-ytdlp-pot-provider`, que
  no es nuestra: siempre se pasará la ruta.
- Preparación con Deno, una sola vez en la carpeta `server`:
  `deno install --allow-scripts=npm:canvas --frozen`.
- El README no lo recomienda para mucha concurrencia. Aquí no la hay: la app
  resuelve una pista a la vez, más un prefetch como mucho.

### Modelo B: servidor local bajo demanda

- Servidor HTTP en el puerto 4416 por defecto, que solo escucha en
  127.0.0.1. Con Deno:
  `deno run --allow-env --allow-net --allow-ffi=. --allow-read=. ../src/main.ts --port <puerto>`.
- Argumento: `--extractor-args "youtubepot-bgutilhttp:base_url=http://127.0.0.1:<puerto>"`.
- Si los dos modos están disponibles, el plugin prefiere el servidor.

### Elección

**Modelo A con el Deno ya instalado.** Motivos:

- No queda un proceso residente ni un puerto abierto. El Modelo B obliga a
  vigilar otro hijo (encolarlo en `proc_guard`, relanzarlo, elegir un puerto
  libre) y a defender el puerto de otros programas locales.
- La caché del plugin cubre el uso de la app: decenas de pistas al día, una a
  la vez.
- No hace falta Node: no cambia lo que se instala, solo se añade la carpeta
  del proveedor junto a `bin/`.

El Modelo B queda de respaldo si el arranque de Deno por token resulta
demasiado lento (más de 2 s de media en el registro de tiempos de
`resolve_log`).

## 4. Inyección en yt-dlp

Todo se concentra en `ExtractorManager.base_args()`, que hoy ya construye la
línea de órdenes. Con la contingencia activa se añadiría:

```text
--plugin-dirs <bin>\yt-dlp-plugins
--extractor-args "youtubepot-bgutilscript:server_home=<bin>\bgutil\server"
```

- `--plugin-dirs` es necesario porque la app lanza yt-dlp con
  `--ignore-config` y desde su propia carpeta. Así el plugin se carga solo
  cuando la app lo pide, y nunca desde la carpeta personal del usuario.
- El plugin `bgutil-ytdlp-pot-provider.zip` se descarga de la página de
  versiones del proyecto, con la misma descarga atómica y verificada de
  `bootstrap.py` (Fase 2.3), en `bin\yt-dlp-plugins\`.
- Los argumentos de cliente (`youtube:player_client=...`) no cambian. Con el
  proveedor activo, yt-dlp pide el token por sí mismo a los clientes que lo
  necesitan; para audio basta `mweb`, que es lo que recomienda la guía.
- **No** se usa `youtube:po_token=CLIENTE.CONTEXTO+TOKEN` con un token fijo:
  los tokens van ligados al vídeo y caducan en horas, así que uno pegado a
  mano deja de valer enseguida.

### Caducidad y rotación

- La caché del plugin (`TOKEN_TTL`, en horas) se fija en 6 h, por debajo de la
  vida observada de unas 12 h. Se pasa como variable de entorno al proceso de
  yt-dlp en `_runner`.
- La caché de URL de `player.py` (`PREFETCH_MAX_AGE_S`) ya descarta las URL
  viejas, así que un token caducado solo puede afectar a una URL nueva.
- Si con el proveedor activo vuelve un 403 o un `BOT_BLOCK`, se reintenta una
  sola vez con un token nuevo. Habrá que comprobar con la versión del plugin
  de ese momento cómo se vacía su caché. Si falla otra vez, cuenta como fallo
  normal, con las mismas rachas y pausas de `PlaybackController`.
- Un fallo del proveedor (Deno ausente, plugin roto) nunca debe dejar la app
  peor que sin él: se registra, se desactiva la contingencia en esa sesión y
  se resuelve como hoy.

## 5. Interfaz y pruebas previstas

- La contingencia sería un ajuste interno (`po_token_provider`, apagado) que
  se enciende solo si se cumplen las señales de la sección 2. El diagnóstico
  enseñaría si está activa y qué versión del plugin hay.
- Pruebas previstas: `base_args()` con y sin la contingencia, usando un Deno y
  un plugin simulados; el cálculo de las señales sobre un `resolve_log`
  sembrado; y la desactivación de la sesión ante un plugin roto. Todo con
  dobles, sin red.

## 6. Referencias

- yt-dlp, *PO Token Guide*: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
- Brainicism, *bgutil-ytdlp-pot-provider*: https://github.com/Brainicism/bgutil-ytdlp-pot-provider
