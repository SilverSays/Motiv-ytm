# YT Music Nativo

A desktop client for YouTube Music on Windows, built with Qt instead of a bundled
browser. It starts fast, uses little memory and has its own clean, cool-toned
interface, with each playlist's color pulled from its cover art. The app's interface is in Spanish.

> **Work in progress.** The app is already usable day to day, but I'm still
> adding features and polishing details. There may be big changes from one
> version to the next.

> **Legal notice.** This project is not official and has no relationship with
> YouTube, YouTube Music or Google. It uses their services in a way they don't
> intend, which may go against their [terms of
> service](https://www.youtube.com/t/terms). Using it is each person's own
> responsibility, including whatever may happen to their account: using a
> secondary account is recommended. The app streams music, just like the website;
> it's not meant for downloading or redistributing content, and must not be used
> for that. All content belongs to its creators.

## What it does

- **Your library**: syncs your YouTube Music playlists and stores them in a local
  database, so they open instantly and without connecting to the account.
- **Local playlists**: create playlists that only exist in the app, or save any
  playlist or album you find as your own.
- **Search without signing in**: songs, community playlists and artists, with
  suggestions as you type and recent searches.
- **Home**: your most-played playlists and recommendations of playlists similar to
  your music.
- **Listening history**: every song you play for at least 30 seconds is saved
  locally on your device. It shows recent plays, most-played this month and
  all-time most-played tracks, and powers recommendations even without an account.
  You can disable it or clear it at any time.
- **Artist page**: popular songs, albums, singles, playlists, similar artists and
  their bio.
- **"Now playing"** («Ahora suena»): large cover art, artist info and music similar
  to what's playing.
- **Synced lyrics** from [LRCLIB](https://lrclib.net) that follow the song line by line.
  When LRCLIB doesn't have them, the app asks YouTube Music (without your account)
  and shows which source each lyric comes from.
- **Music on your computer**: choose a music folder and its MP3, FLAC, M4A, AAC, OGG,
  Opus and WAV files appear under **En tu equipo**, with their tags and embedded
  cover art. They play straight from disk: no `yt-dlp`, no network, and they're never
  sent to Last.fm or to YouTube.
- **Light and dark themes**: the "Plata" look also comes in a night version. By
  default it follows Windows (light or dark apps) and switches on the fly when you
  change it, without stopping the music.
- **Playback**: queue, shuffle, repeat, radio from a song or an artist, and
  preloading of the next tracks so there's no waiting. When you open the app,
  the queue and the song come back at the second you left them, stopped (this can
  be turned off in the **⋯** menu).
- **Loudness equalization (volume normalization)**: optional audio normalization
  using `mpv`'s `loudnorm` (EBU R128) or `dynaudnorm` filters, switchable on the fly
  without interrupting playback.
- **Sleep timer**: schedule playback to stop after 15, 30, 45, 60 minutes or when the
  current song ends. Gently fades out volume over the last 12 seconds and restores your
  listening volume once stopped. Can optionally exit the app.
- **System tray & background playback**: closing the window minimizes to the system tray
  by default so music never stops. Includes a rich context menu with playback controls,
  mini player toggle, and an optional "Start with Windows" setting. Single-instance
  support restores the existing window when launched again.
- **Taskbar thumbnail toolbar**: hover over the taskbar button to access playback
  controls (Previous, Play/Pause, Next) directly within Windows' thumbnail preview,
  adapting cleanly to light and dark themes.
- **Compact mini player**: a floating, frameless always-on-top window (332×104 px)
  with smooth marquee title scrolling, cover art, seek bar, and screen clamping so it
  never gets lost off-screen (`Ctrl+M`).
- **Backup and restore**: export your local playlists, listening history, and settings
  to an atomic JSON backup file. Import back with options to merge or replace. Private
  by design: never exports credentials, session tokens, or cookies.
- **Resilience**: if YouTube changes something and playback breaks, the app updates
  `yt-dlp` by itself, checks that the new version works and rolls back if it doesn't.
- **Windows media controls**: the media keys on your keyboard or headphones work
  with the app in the background, and the volume flyout and lock screen show the
  song with its cover art, its buttons and the progress bar.
- **Discord Rich Presence (optional, off by default)**: shows "Listening to" with
  the song, artist, cover art and progress in your Discord profile. It talks to the
  Discord desktop app through its local pipe, with no extra libraries; if Discord
  isn't running or closes, the app keeps playing and retries quietly in the
  background.
- **Last.fm scrobbling (optional, off by default)**: announces what's playing and
  scrobbles each song once half of it (or 4 minutes) has played. Without a
  connection, scrobbles wait in a local queue and are sent in batches when the
  service is back. The Last.fm session key is encrypted with Windows DPAPI (tied
  to your Windows user) and never stored in plain text.
- **Changes to your YouTube Music account (optional, off by default)**: like songs,
  save them to or remove them from your library, copy a local playlist to your
  account, and optionally add what you listen to to your YouTube history. The app
  warns you before turning this on; while it's off, nothing is ever written to the
  account. Changes show at once and are undone with a notice if YouTube refuses them.
- **New version notice**: at most once a day the app asks GitHub for the latest
  release and, if there's a newer one, shows a small link at the bottom of the
  sidebar to its page. It never downloads or installs anything by itself, and it
  can be turned off.
- **Interface**: purposeful animations (sliding panels, cover art that travels from
  the card to the header, lists that appear staggered) that respect Windows'
  reduce-animations setting; full keyboard navigation.

### Keyboard shortcuts

| Shortcut | Action |
| :--- | :--- |
| Space | Play or pause |
| Ctrl+→ / Ctrl+← | Next / previous song |
| Ctrl+F | Search |
| Ctrl+L | Show lyrics |
| Ctrl+I | Show "Now playing" |
| Ctrl+M | Toggle mini player |
| Ctrl+B | Collapse or expand the sidebar |
| Ctrl+N | New local playlist |
| F5 | Sync library with account |
| Alt+← | Back to the previous view |
| Ctrl+Q | Exit application |

## Installation

### With the installer (no Python needed)

Download `YTMusicNativo-<version>-instalador.exe` from the
[Releases](https://github.com/SilverSays/Proyecto-YT-Music-Nativo/releases) page
and run it. It installs for your user only (no administrator rights), adds a
Start menu shortcut (and a desktop one if you want) and registers a regular
uninstaller in Windows Settings. The first time the app opens, it offers to
download `yt-dlp`, `deno` and `mpv` (see step 4 below).

When uninstalling, it asks whether to also delete your data in
`%LOCALAPPDATA%\YTMusicNativo`; answer "No" if you plan to reinstall.

### From the source code

Requirements: **Windows 10 or 11** and **Python 3.10 or later** (from
[python.org](https://www.python.org/downloads/), with the `py` launcher).

1. Download or clone this repository.
2. Run `instalar.bat`. It creates its own Python environment in
   `%LOCALAPPDATA%\YTMusicNativo\venv` and installs the dependencies
   (`PySide6-Essentials`, `ytmusicapi` and the `winrt` packages for the media
   controls). If you already had the app installed, run it again to add the
   missing ones.
3. Run `iniciar.bat` to open the app. If the environment doesn't exist yet,
   it installs it first.
4. The first time, a notice at the top offers to **Descargar ahora** (Download now)
   the programs the app uses for playback. They're downloaded from their official
   sources on GitHub and are not part of this repository:
   - [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (*nightly* channel): gets
     the audio URL.
   - [`deno`](https://github.com/denoland/deno): needed by `yt-dlp`.
   - [`mpv`](https://mpv.io) ([shinchiro](https://github.com/shinchiro/mpv-winbuild-cmake)
     build): plays the audio.

   They can also be downloaded from a terminal with
   `python -m ytm_native.playback.bootstrap`.

Without an account you can already search, browse artists and listen to any song.

## Account setup (optional)

You only need this to see **your library**. **Use a secondary account**, not your
main one.

### Sign in with Firefox (recommended)

From the **⋯** menu (at the bottom of the sidebar) → **Iniciar sesión con Firefox**
(Sign in with Firefox), or the **Iniciar sesión** button of the notice at the top:

1. Click **Abrir Firefox**. A separate, empty Firefox window opens (its own
   temporary profile: it doesn't see your usual tabs, accounts or extensions).
2. Sign in to YouTube Music with the secondary account and wait until you see
   your library.
3. Close that Firefox window. The app reads the session cookies, checks that the
   session works, stores it encrypted and deletes that temporary Firefox profile
   from disk.

Firefox has to be installed, but it doesn't need to be your default browser.
Chrome isn't supported for this on purpose: recent versions bind the session to
the device (*Device Bound Session Credentials*) and encrypt their cookies with a
key only Chrome can use (*app-bound* encryption), so a session taken out of Chrome
stops working within a few hours.

### Manual setup (advanced)

From **⋯** → **Cuenta y reproducción** (Account and playback):

1. Open a **Firefox private window** and sign in to
   [music.youtube.com](https://music.youtube.com) with that account. Use Firefox:
   Chrome ties the session to the device, and copied credentials expire within a few
   hours.
2. Open the developer tools (F12) → **Network** tab, filter by `browse` and reload
   the page.
3. Right-click a `browse` request → **Copy** → **Copy Request Headers**.
4. In the app, on the **Credenciales** (Credentials) tab, paste the headers and click
   **Guardar cabeceras** (Save headers).
5. Close the private window **without signing out** and don't open it again:
   signing out invalidates the copied credentials.

**Cookies for playback (advanced, optional).** By default the audio is fetched
without an account. If YouTube blocks those requests, the app can use the
account's cookies, but only after you explicitly accept the risk (with cookies,
playback is tied to the account). Signing in with Firefox already stores them; you
can also load a `cookies.txt` (exported as explained in the [yt-dlp
guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)).

**Your credentials are encrypted.** `browser.json`, `cookies.txt` and the Last.fm
session are stored encrypted with Windows DPAPI: only your Windows user, on this
computer, can read them. Files left in plain text by older versions are encrypted
automatically the next time the app starts. `yt-dlp` gets a readable copy of the
cookies only while it runs, under a random name, and that copy is deleted as soon
as it finishes (leftovers from a crash are removed at startup). If the files are
copied to another computer or Windows user, the app just asks you to sign in again.

### Discord and Last.fm (optional)

Both are off by default and live in the **⋯** menu → **Discord y Last.fm**.

- **Discord**: Discord needs the ID of an application registered at
  [discord.com/developers/applications](https://discord.com/developers/applications)
  (its name is what appears after "Listening to"). The first time you turn the
  option on, the app asks for that *Application ID*. Only the song title,
  artist, album, cover art and times are shared.
- **Last.fm**: choose **Conectar con Last.fm…**. If the app doesn't come with
  its own Last.fm API key, it asks for one (create it for free at
  [last.fm/api/account/create](https://www.last.fm/api/account/create)); then it
  opens Last.fm in your browser so you can allow access. The key, the secret and
  the session are stored encrypted with Windows DPAPI in
  `%LOCALAPPDATA%\YTMusicNativo\credenciales\lastfm.bin`.

### Account changes, music folder and theme

All three live in the **⋯** menu:

- **Cambios en tu cuenta de YouTube Music**: turn on **Permitir cambios en mi
  cuenta** to get the heart in the player bar and the like/library options in a
  song's right-click menu. Removing something asks for confirmation, and changes are
  sent one at a time with short pauses. **Registrar mis escuchas en el historial de
  YouTube** is a separate option on top of it.
- **Música de tu equipo**: choose the folder; only new or changed files are read
  again when the app opens.
- **Tema**: *Como Windows*, *Claro* or *Oscuro*.

### Where everything is stored

None of this is stored in the project folder or leaves your computer:

| What | Where |
| :--- | :--- |
| Python environment | `%LOCALAPPDATA%\YTMusicNativo\venv` |
| `yt-dlp`, `deno`, `mpv` | `%LOCALAPPDATA%\YTMusicNativo\bin` |
| Credentials (`browser.json`, `cookies.txt`, `lastfm.bin`, all encrypted) | `%LOCALAPPDATA%\YTMusicNativo\credenciales` |
| Library, cover art cache and logs | `%LOCALAPPDATA%\YTMusicNativo` |

To uninstall a copy run from the source code, delete the project folder and
`%LOCALAPPDATA%\YTMusicNativo`. The installed version is removed from Windows
Settings → Apps.

## Status and plans

The app is under active development. I keep working on new features and on
polishing the interface and stability. If something breaks, open an *issue* saying
what you were doing and, if you can, attach the log from
`%LOCALAPPDATA%\YTMusicNativo\logs` (check it first: it may include song and
playlist names).

## Contributing or forking

Contributions are welcome. Before a big change, open an *issue* to discuss it.

### How it's organized

```
ytm_native/
├── __main__.py, app.py   Startup: creates the database, the player and the window
├── core/                 The base everything else uses
├── playback/             Everything that makes a song play
├── services/             YouTube Music, LRCLIB data, and local backups
├── ui/                   The Qt interface, system tray, mini player, and taskbar
└── tools/                Development tools (not part of the app)
tests/                    Automated tests (no network, with a fake mpv and yt-dlp)
instalar.bat, iniciar.bat Install and launch on Windows from the source code
ytm_native.spec           PyInstaller build of the standalone program
installer.iss             Inno Setup script for the Windows installer
construir_instalador.bat  Builds both in one step
```

**`core/`: the base**

| File | What it does |
| :--- | :--- |
| `paths.py` | Data, binary and credential paths in `%LOCALAPPDATA%` (the `YTMN_HOME`, `YTMN_BIN_DIR` and `YTMN_CRED_DIR` variables override them). |
| `config.py` | Constants: timeouts, request limits, sync cadence. |
| `crypto.py` | Encryption at rest with Windows DPAPI (`CryptProtectData` / `CryptUnprotectData`). |
| `credentials.py` | Encrypted reading and writing of the account credentials, migration of plain-text files and the short-lived cookie copy for `yt-dlp`. |
| `db.py` | SQLite database: tracks, playlists, local playlists, settings and caches. |
| `errors.py` | Classifies `yt-dlp` errors (blocked, network, expired session, unavailable…) and their messages. |
| `instance.py` | Single-instance lock via local IPC socket and window foreground activation. |
| `proc_guard.py` | Windows Job Objects for process tree management and synchronous I/O cancellation. |

**`playback/`: playback**

| File | What it does |
| :--- | :--- |
| `extractor.py` | The audio *fetcher*: runs `yt-dlp` to get each song's URL, keeps a URL cache and updates `yt-dlp` with rollback if the new version fails. |
| `mpv_ipc.py` | Launches `mpv` as a separate process, controls it through IPC, and applies audio normalization filters. |
| `history.py` | Decides when a song counts as listened and records it in listening history. |
| `player.py` | The player: queue, shuffle, repeat, radio, preloading and error recovery. |
| `sleep_timer.py` | Sleep timer with smooth volume fade-out and track-completion stopping. |
| `session.py` | Saves the queue and the current second, and restores them on startup. |
| `bootstrap.py` | Downloads and installs `yt-dlp`, `deno` and `mpv`. |
| `media_controls.py` | Publishes what's playing to Windows (title, artist, cover art) and handles its buttons. |

**`services/`: online data and backups**

| File | What it does |
| :--- | :--- |
| `sync.py` | Syncs the account's library with `ytmusicapi`, manages the session and searches the local library. |
| `auth_assistant.py` | Assisted sign-in: launches Firefox with a throwaway profile, reads its cookies and stores the session encrypted. |
| `discover.py` | Search, suggestions, home recommendations, other people's playlists, artist pages and the "Now playing" info. |
| `lyrics.py` | Lyrics from LRCLIB and, as a fallback, YouTube Music; which line is the current one. |
| `account_actions.py` | Optional changes to the account (likes, library, playlists, history) behind an off-by-default switch, with optimistic UI and rate limiting. |
| `local_media.py` | Music on disk: tag reader for ID3, Vorbis, FLAC, MP4 and WAV (standard library only) and the folder scanner. |
| `backup.py` | Atomic export and import of local playlists, listening history, and preferences. |
| `discord_rpc.py` | Optional Discord Rich Presence over Discord's local IPC pipe, in its own thread. |
| `lastfm.py` | Optional Last.fm scrobbling: API client, retry queue in SQLite and encrypted session. |
| `updater.py` | Once-a-day check of GitHub Releases for a newer version of the app (notice only). |

**`ui/`: interface**

| File | What it does |
| :--- | :--- |
| `main_window.py` | The window: ties together the views, the player and the panels, and decides what's shown. |
| `theme.py` | Colors (light and dark schemes), typography and stylesheet. It's the only place with color values. |
| `motion.py` | Curves, durations and `Tween`, the animated value every animation uses. |
| `components.py` | Reusable pieces: buttons, playlist header, track table, notices, animated cover art. |
| `home.py` | The home screen and playlist cards. |
| `search.py` | Search parts: tabs and results grid. |
| `artist.py` | The artist page. |
| `now.py` | The "Now playing" panel. |
| `mini_player.py` | Compact floating mini player with smooth marquee text and screen clamping. |
| `tray.py` | System tray icon with multi-resolution vector rendering and Windows autostart. |
| `taskbar.py` | Native Windows COM (`ITaskbarList3`) thumbnail toolbar controls. |
| `widgets.py` | The track table model and the account dialog. |
| `dialogs.py` | The guided "Sign in with Firefox" dialog. |
| `art.py` | Cover art download and cache, and the color pulled from each one. |
| `chrome.py`, `focus.py` | Window-wide details: title bar, menus and a keyboard-only focus ring. |

**`tools/fase0.py`** is the feasibility test the project started with: it
measures how many requests YouTube blocks with and without an account.

### Tests

```
%LOCALAPPDATA%\YTMusicNativo\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
%LOCALAPPDATA%\YTMusicNativo\venv\Scripts\python.exe -m pytest tests
```

They don't touch the network or your data: they use a temporary folder.

### Building the installer

```
construir_instalador.bat
```

It creates a separate build environment in `%LOCALAPPDATA%\YTMusicNativo-construccion`,
packages the app with [PyInstaller](https://pyinstaller.org) (`ytm_native.spec`)
and, if [Inno Setup 6.3+](https://jrsoftware.org/isdl.php) is installed, compiles
`installer.iss` into `dist\YTMusicNativo-<version>-instalador.exe`. Without Inno
Setup it stops at the portable folder with `YTMusicNativo.exe`.

`yt-dlp`, `deno` and `mpv` are not bundled on purpose: `yt-dlp` updates itself
and needs a writable folder, and downloading them on first launch keeps the
installer small.

To ship your own Discord application ID or Last.fm API key with a build, set
`DISCORD_CLIENT_ID`, `LASTFM_API_KEY` and `LASTFM_API_SECRET` in
`ytm_native/core/config.py` before building.

### Code guidelines

- Colors, radii and typography only come from `ui/theme.py`.
- Every animation goes through `ui/motion.py`, lasts at most 300 ms and respects
  Windows' reduced animations. Nothing animates on keyboard actions that repeat
  a lot.
- Interface text is in Spanish, in normal sentence case (no all caps).

## License

[GPL-3.0](LICENSE). You can use, study, modify and redistribute the code,
as long as the versions you distribute stay free under the same license.

It uses [PySide6](https://doc.qt.io/qtforpython/) (LGPL),
[ytmusicapi](https://github.com/sigma67/ytmusicapi) (MIT),
[PyWinRT](https://github.com/pywinrt/pywinrt) (MIT),
[yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense),
[mpv](https://mpv.io) (GPL/LGPL) and [deno](https://deno.com) (MIT), which are
installed separately and keep their own licenses.
