# Dispatcharr Multiview

> Dispatcharr plugin that combines multiple channel streams into a single tiled MPEG-TS output. Define named layouts, pick your channels, and each layout appears as a standard M3U channel you can open in any player.

## Support

This project is maintained in my spare time. If it's saved you some headaches, a tip is always appreciated.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/sethwv)

---

> **Before any stream will play:** open the plugin settings page and run the **Install PyAV** action. This downloads the media engine the compositor depends on. It is a one-time step per host and takes about 30 seconds. Streams return a 503 until it completes. After that first run, the plugin remembers your consent and automatically reinstalls PyAV in the background if it's ever found missing or outdated (e.g. after a plugin update resets the vendored copy) -- no need to click it again.

---

## Dashboard

A mobile-friendly PWA is served at `http://<host>:9292/dash/`. Log in with your Dispatcharr credentials to edit all plugin settings and manage active streams without opening the Dispatcharr admin UI.

**Disabled by default.** Enable it via the **Web Dashboard** setting on the plugin settings page, then reload the plugin (or restart Dispatcharr) for the change to take effect.

- **Settings** auto-save as you change them.
- **Active Multiviews** button (top bar) shows running streams; per-layout reload and per-channel reconnect are available.
- **Refresh** regenerates the M3U and EPG and triggers a Dispatcharr sync.
- Click the logo (top-left) for links to this repo and Ko-fi support.

Add `9292:9292` to your `docker-compose.yml` ports to expose the dashboard.

The dashboard SPA (`src/dash/ui/`) is React + Mantine, and shares its theme, header bar, login screen, confirm modal, and settings-panel components with the `source-switch` plugin's dashboard via a separate package, `@swvn-dispatch/dispatch-ui-kit`. See [that package's README](https://github.com/swvn-dispatch/plugins/blob/main/ui-kit/README.md) for the component list and local-testing instructions.

```bash
cd src/dash/ui
npm install   # needs a personal GitHub Packages PAT — see ui-kit README
npm run dev
```

---

## How it works

Each `/stream/{n}` request spawns a dedicated Python worker process with no gevent overhead, giving the compositor real OS threads and full CPU parallelism:

- Each source channel is decoded in its own thread using PyAV (bundled). Frames are scaled and composited onto a numpy YUV420p canvas at the configured frame rate.
- The canvas is piped to a libx264 subprocess for CBR encoding to MPEG-TS, which the plugin's gevent server streams to the player.
- Source channels are fetched through Dispatcharr's internal live proxy so they appear in stream stats and respect stream profiles.
- A per-channel PTS rate limiter ensures playback stays at realtime regardless of how fast the host machine or source proxy delivers packets. Audio is aligned to the video PTS clock at startup and the audio buffer is flushed on reconnect to keep lip-sync stable.

## Configuration

**Global settings** apply to all layouts:

| Setting | Description |
|---|---|
| Output Resolution | 480p / 720p / 1080p |
| Output Frame Rate | 24 / 25 / 30 / 50 / 60 fps |
| Output Bitrate (kbps) | CBR target. 8000 is a good baseline for 1080p; 12000-16000 for noticeably sharper tiles. |
| Encoder Preset | ultrafast (lowest CPU) through slow (highest quality). Ultrafast recommended for live use. |
| Auto-Refresh Interval | Hours between automatic M3U and EPG regeneration. 0 = manual only. |

**Per-layout settings:**

| Setting | Description |
|---|---|
| Name | Shown in the M3U playlist |
| Layout Style | Auto Grid, Featured, or Top Featured (see below) |
| Channel Selection | Classic (dropdowns) or Regex (dynamic match) |
| Channel Count | Number of tiles. Max 4 recommended. |
| Audio Source | Single channel or All Channels (one AC3 track per tile) |
| EPG fields | Title, subtitle, categories for the guide entry |

**Layout styles:**

- **Auto Grid:** tiles arranged in a square-ish grid sized from channel count.
- **Featured:** channel 1 fills the left portion (at least 60% of width); remaining channels stack on the right.
- **Top Featured:** channel 1 fills a full-width strip across the top (at least 60% of height); remaining channels sit in a row at the bottom.

**Channel selection modes:**

- **Classic:** select each channel from a dropdown.
- **Regex:** enter a pattern (e.g. `TSN\s*\d` or `^CA \|`) matched case-insensitively against channel names. Channels are sorted by channel number. The channel count setting becomes the maximum number of matches to tile.

**Audio source:**

- **Single channel:** one AC3 audio track from the selected tile.
- **All channels:** one AC3 track per tile. Players that support multi-track audio (VLC, Infuse, mpv) can switch between them via the Audio Track menu. Duplicate track labels (e.g. three TSN channels) are auto-numbered: `ts1`, `ts2`, `ts3`.

After saving settings, click **Regenerate M3U** to write `multiview.m3u`, create or update the M3U account, and rebuild EPG data. The Auto-Refresh Interval controls how often this happens automatically.

## Hardware encoding (coming soon)

NVIDIA NVENC, Intel QSV, and AMD/Intel VAAPI encode support are in progress. Only software encoding (libx264) is available today. The encoder preset setting already applies; hardware paths will add a video encoder dropdown when ready.

## Notes

- 4 channels per layout is the recommended maximum; higher counts may not initialise reliably on all hardware.
- Tiles show a channel logo on a black background while the source is connecting or reconnecting. The logo is pulled from Dispatcharr's channel record.
- EPG categories are comma-separated tags (e.g. `Sports, News`) written to the XMLTV `<category>` field. EPG apps use these for colour coding.
