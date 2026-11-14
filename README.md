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
- **Playback**: queue, shuffle, repeat, radio from a song or an artist, and
  preloading of the next tracks so there's no waiting.
- **Resilience**: if YouTube changes something and playback breaks, the app updates
  `yt-dlp` by itself, checks that the new version works and rolls back if it doesn't.
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
| Ctrl+B | Collapse or expand the sidebar |
| Ctrl+N | New local playlist |
| Alt+← | Back to the previous view |

## Installation

Requirements: **Windows 10 or 11** and **Python 3.10 or later** (from
[python.org](https://www.python.org/downloads/), with the `py` launcher).

1. Download or clone this repository.
2. Run `instalar.bat`. It creates its own Python environment in
   `%LOCALAPPDATA%\YTMusicNativo\venv` and installs the dependencies
   (`PySide6-Essentials` and `ytmusicapi`).
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

You only need this to see **your library**. It's done from the **⋯** menu (at the
bottom of the sidebar) → **Cuenta y reproducción** (Account and playback).

**Use a secondary account**, not your main one.

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
without an account. If YouTube blocks those requests, you can load a
`cookies.txt` (exported as explained in the [yt-dlp
guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies))
and explicitly accept the risk: with cookies, playback is tied to the account.

### Where everything is stored

None of this is stored in the project folder or leaves your computer:

| What | Where |
| :--- | :--- |
| Python environment | `%LOCALAPPDATA%\YTMusicNativo\venv` |
| `yt-dlp`, `deno`, `mpv` | `%LOCALAPPDATA%\YTMusicNativo\bin` |
| Credentials (`browser.json`, `cookies.txt`) | `%LOCALAPPDATA%\YTMusicNativo\credenciales` |
| Library, cover art cache and logs | `%LOCALAPPDATA%\YTMusicNativo` |

To uninstall, delete the project folder and
`%LOCALAPPDATA%\YTMusicNativo`.

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
├── services/             YouTube Music and LRCLIB data
├── ui/                   The Qt interface
└── tools/                Development tools (not part of the app)
tests/                    Automated tests (no network, with a fake mpv and yt-dlp)
instalar.bat, iniciar.bat Install and launch on Windows
```

**`core/`: the base**

| File | What it does |
| :--- | :--- |
| `paths.py` | Data, binary and credential paths in `%LOCALAPPDATA%` (the `YTMN_HOME`, `YTMN_BIN_DIR` and `YTMN_CRED_DIR` variables override them). |
| `config.py` | Constants: timeouts, request limits, sync cadence. |
| `db.py` | SQLite database: tracks, playlists, local playlists, settings and caches. |
| `errors.py` | Classifies `yt-dlp` errors (blocked, network, expired session, unavailable…) and their messages. |

**`playback/`: playback**

| File | What it does |
| :--- | :--- |
| `extractor.py` | The audio *fetcher*: runs `yt-dlp` to get each song's URL, keeps a URL cache and updates `yt-dlp` with rollback if the new version fails. |
| `mpv_ipc.py` | Launches `mpv` as a separate process and controls it through its IPC pipe. |
| `history.py` | Decides when a song counts as listened and records it in listening history. |
| `player.py` | The player: queue, shuffle, repeat, radio, preloading and error recovery. |
| `bootstrap.py` | Downloads and installs `yt-dlp`, `deno` and `mpv`. |

**`services/`: online data**

| File | What it does |
| :--- | :--- |
| `sync.py` | Syncs the account's library with `ytmusicapi`, manages the session and searches the local library. |
| `discover.py` | Search, suggestions, home recommendations, other people's playlists, artist pages and the "Now playing" info. |
| `lyrics.py` | LRCLIB lyrics and which line is the current one. |

**`ui/`: interface**

| File | What it does |
| :--- | :--- |
| `main_window.py` | The window: ties together the views, the player and the panels, and decides what's shown. |
| `theme.py` | Colors, typography and stylesheet. It's the only place with color values. |
| `motion.py` | Curves, durations and `Tween`, the animated value every animation uses. |
| `components.py` | Reusable pieces: buttons, playlist header, track table, notices, animated cover art. |
| `home.py` | The home screen and playlist cards. |
| `search.py` | Search parts: tabs and results grid. |
| `artist.py` | The artist page. |
| `now.py` | The "Now playing" panel. |
| `widgets.py` | The track table model and the account dialog. |
| `art.py` | Cover art download and cache, and the color pulled from each one. |
| `chrome.py`, `focus.py` | Window-wide details: title bar, menus and a keyboard-only focus ring. |

**`tools/fase0.py`** is the feasibility test the project started with: it
measures how many requests YouTube blocks with and without an account.

### Tests

```
%LOCALAPPDATA%\YTMusicNativo\venv\Scripts\python.exe -m pip install pytest
%LOCALAPPDATA%\YTMusicNativo\venv\Scripts\python.exe -m pytest tests
```

They don't touch the network or your data: they use a temporary folder.

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
[yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense),
[mpv](https://mpv.io) (GPL/LGPL) and [deno](https://deno.com) (MIT), which are
installed separately and keep their own licenses.
