# Dispatcharr Multiview

> Dispatcharr plugin that combines multiple channel streams into a single tiled MPEG-TS output. Define named layouts, pick your channels, and each layout appears as a standard M3U channel you can open in any player.

## Support

This project is maintained in my spare time. If it's saved you some headaches, a tip is always appreciated.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/sethwv)

---

> **Before any stream will play:** open the plugin settings page and run the **Install PyAV** action. This queues a download of the media engine. Streams return a 503 until installation completes. A successful install records consent for automatic repair if PyAV is later missing or outdated.

## Requirements and upgrades

This version requires Dispatcharr 0.34.0 or a development build containing manifest v1, plugin sections, asynchronous actions, durable plugin storage, and leader-elected services. These APIs were unreleased after Dispatcharr 0.33.0 when this change was written. The administrator must approve the listed plugin capabilities; listener approval may require a Dispatcharr restart. Manifest v1 discloses capabilities but does not isolate plugins from the host process.

Generated playlists, EPG files, PyAV, and style backgrounds are stored in Dispatcharr's durable plugin data directory. Existing files are copied when available. A managed upgrade can remove the old code directory before Multiview runs; if a custom background is already missing, its style reference is cleared and a warning asks you to upload it again. Dashboard backups contain style settings and filenames, not background image bytes.

---

## Dashboard

A mobile-friendly PWA is served at `http://<host>:9292/dash/`. Log in with your Dispatcharr credentials to edit all plugin settings and manage active streams without opening the Dispatcharr admin UI.

**Disabled by default.** Enable it via the **Web Dashboard** setting on the plugin settings page, then reload the plugin (or restart Dispatcharr) for the change to take effect.

- **Settings** auto-save as you change them.
- **Active Multiviews** button (top bar) shows running streams; per-layout reload and per-channel reconnect are available.
- The plugin settings pane includes **Reload All Active Multiviews** for reloading every running compositor without opening the dashboard.
- **Refresh** queues M3U and EPG regeneration. Dispatcharr's plugin task history reports the generation action; subsequent M3U and EPG refreshes run separately.
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

- Each source channel is decoded in its own thread using the separately installed PyAV dependency. Frames are scaled and selected against a source-PTS clock at the configured output frame rate.
- Software profiles composite tile frames onto a numpy YUV420p canvas. Hardware profiles automatically use matching FFmpeg CUDA, QSV, or VAAPI composition filters when available, then encode CBR MPEG-TS for the plugin's gevent server to stream.
- Source channels are fetched through Dispatcharr's internal live proxy so they appear in stream stats and respect stream profiles.
- A per-channel PTS rate limiter ensures playback stays at realtime regardless of how fast the host machine or source proxy delivers packets. Audio is aligned to the video PTS clock at startup and the audio buffer is flushed on reconnect to keep lip-sync stable.

## Configuration

The native settings page groups fields into Global Settings, Video Settings, and one section per layout. Global and video settings apply to all layouts:

| Setting | Description |
|---|---|
| Output Resolution | 480p / 720p / 1080p / 4K (3840×2160) |
| Output Frame Rate | 24 / 25 / 30 / 50 / 60 fps |
| Output Bitrate (kbps) | CBR target. 8000 is a good baseline for 1080p; 12000-16000 for noticeably sharper tiles. |
| Encoder Preset | ultrafast (lowest CPU) through slow (highest quality). Ultrafast recommended for live use. |
| Auto-Refresh Interval | Hours between automatic M3U and EPG regeneration. 0 = manual only. |
| Live Multiview Reload | Manual by default. Poll active multiviews every 15s, 30s, 60s, 2m, or 5m and reload only those whose regex matches or stream-affecting settings changed. Reloads briefly interrupt players while Dispatcharr reconnects. |

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
- **Audio Track Labels:** enabled by default. Generated title and language labels identify multiview audio tracks in compatible players. Disabling them omits generated labels; source metadata cannot be preserved because audio is decoded to raw PCM before it is re-encoded for output.

After saving settings, click **Regenerate M3U** to queue the playlist and EPG update. The service waits for the M3U task to succeed and checks layout mappings before dispatching EPG parsing. If the result is ambiguous, the refresh is blocked rather than risking an out-of-order EPG update. The dashboard displays a warning and can check recovery for a known completed task. For an unknown submission outcome, inspect Dispatcharr's task history and active workers before clearing the `refresh-state.json` file in the plugin data directory and retrying. The Auto-Refresh Interval controls automatic scheduling.

## Hardware acceleration

Selecting NVIDIA NVENC, Intel QSV, or Intel/AMD VAAPI automatically attempts GPU tile composition as well as GPU encoding. There is no extra setting. The worker validates the selected device and FFmpeg's required hardware filters at startup. If either is unavailable, it logs the reason and keeps the stream running with the CPU compositor.

PyAV source decode, source reconnect handling, PTS scheduling, and audio processing remain on the CPU. A custom layout background image also uses the CPU compositor for now, so its letterbox padding remains correct. Check the worker log for `compositor=cuda`, `compositor=qsv`, `compositor=vaapi`, or a CPU fallback reason.

4K output has four times the pixel workload of 1080p. In a four-tile grid, each tile receives a 1920×1080 region. Use capable hardware, hardware encoding/composition where available, and enough output bitrate for the source material.

## Notes

- 4 channels per layout is the recommended maximum; higher counts may not initialise reliably on all hardware.
- Tiles show a channel logo on a black background while the source is connecting or reconnecting. The logo is pulled from Dispatcharr's channel record.
- EPG categories are comma-separated tags (e.g. `Sports, News`) written to the XMLTV `<category>` field. EPG apps use these for colour coding.
