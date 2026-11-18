# Changelog

All notable changes to this project are documented in this file.

## [v0.7.0](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.7.0) - 2026-11-18

### Added

- Added 29.97 and 59.94 fps output options.
- Added a two-minute diagnostic action that samples active multiviews without interrupting playback and provides a downloadable JSON report.
- Added a Refresh Settings action to update the settings display without restarting active streams.

### Changed

- Requires Dispatcharr 0.34.0 or later and uses its managed plugin capabilities and service lifecycle.
- Reorganized native settings into sections and replaced classic channel selectors with an ordered channel table.
- Migrate existing playlists, guide data, and custom backgrounds to persistent plugin data; store PyAV installations there as well.
- Improved playback smoothness with a small per-tile presentation buffer.
- Improved M3U and EPG regeneration handling, including overlapping requests and delayed refresh tasks.

### Fixed

- Fixed compositor startup and active-stream reloads in multi-worker Dispatcharr installations.
- Kept failed tiles reconnectable from the dashboard.
- Prevented concurrent settings updates from overwriting one another.
- Improved warnings for stream profiles that may drop multiview audio tracks.
- Avoided unnecessary automatic PyAV reinstallations and corrected the result when PyAV is already current.

### Security

- Restricted custom background file paths to the plugin data directory and added integrity verification for downloaded PyAV packages.

## [v0.6.0](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.6.0) - 2026-11-06

### Added

- Added a 4K (3840x2160) output resolution preset.
- Added optional automatic reloads for active multiviews when matching channels or stream settings change. Reloads briefly interrupt playback.
- Added a plugin settings button to reload all active multiviews.
- Added a setting to enable or disable generated audio track labels.

### Fixed

- Improved tile recovery when sources stall, switch formats, reconnect, or experience timestamp jumps, while keeping unaffected tiles playing.
- Improved live playback recovery when a tile falls behind the available stream buffer.
- Improved audio synchronization with the video being displayed, including after source recovery.

## [v0.5.0](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.5.0) - 2026-10-15

### Added

- Added automatic GPU tile composition for NVENC, QSV, and VAAPI encoders when the installed FFmpeg and GPU support it. Streams fall back to CPU composition when unavailable.
- Added regex channel ordering by channel number, reverse channel number, or the order of top-level regex alternatives.

### Changed

- Layout reordering now immediately regenerates the M3U playlist so channel numbers follow the dashboard order.
- Regex channel patterns now use a multi-line editor that wraps long expressions.

### Fixed

- Improved audio and video synchronization, including recovery when audio timestamp gating would otherwise remain silent.
- Improved video cadence for sources with frame rates that differ from the output frame rate.
- Restored automatic PyAV installation on Python 3.13.

## [v0.4.3](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.4.3) - 2026-08-31

### Fixed

- Reduced microstutter in layouts that combine channels with different frame rates by timing each tile to its source video.
- Preserved broadcast MPEG-2 B-frames so tiles keep their intended source cadence.
- Removed stale EPG entries for deleted or legacy layouts when refreshing programme data.
- Corrected audio buffer timestamps to improve audio and video synchronization.

## [v0.4.2](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.4.2) - 2026-07-21

### Added

- Render Device setting for QSV and VAAPI encoders. Auto (the default) keeps the current behavior of using the first detected `/dev/dri/render*` node. On systems with more than one GPU, you can now pin a specific render node so encoding runs on the intended device. Available options are populated from the render nodes detected on the host and appear in both the native Dispatcharr settings page and the web dashboard.

## [v0.4.1](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.4.1) - 2026-07-17

## What's Changed
* Fix channel select regressions: typable input and stray Layout N field by @sethwv in https://github.com/swvn-dispatch/dispatcharr-multiview/pull/12

## [v0.4.0](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.4.0) - 2026-07-17

### Added

- Style Builder: a visual, drag-and-drop editor for creating custom multiview layouts, selectable alongside the built-in Auto Grid and Featured layouts. Supports static tiles, dynamic rows/grids with per-element caps, and live preview.
- Background images for custom layouts, shown through any gaps or letterboxing left by the layout.
- Drag-to-reorder layouts in the dashboard.
- Backup and restore: export all layouts and settings to a JSON file, and re-import them (including from an older backup format).
- Configurable dashboard mount path (default `/dash`), changeable without a restart.
- Plugin and Dispatcharr version shown in the dashboard header.
- "Remember me" username and silent session refresh, so the dashboard stays logged in without re-entering credentials.
- Searchable channel select in the dashboard's channel pickers.
- Dashboard logo links to the project repo and Ko-fi support page.

### Changed

- Layouts now use stable internal IDs instead of position numbers, so reordering or deleting a layout no longer changes another layout's stream URL or causes a viewer's stream to swap on reconnect. As part of this, the M3U `tvg-id`, stream URL, and EPG channel id format changed from `multiview_N` / `/stream/N` to `mv-<id>` / `/stream/<id>`. Existing multiview channels in Dispatcharr will likely be re-created (not renamed) on the next M3U/EPG refresh, so re-check channel numbering, groups, and any manual EPG mapping afterward.
- M3U refresh no longer silently skips when EPG refresh fails; it now always runs once EPG finishes.

### Fixed

- Layout count desync between the native settings page and the dashboard that could break "Add Layout" or delete existing multiviews.
- Audio desync on unstable streams now resyncs automatically.
- 1-2px gaps between touching tiles in Auto Row layouts on non-round coordinates.
- Gaps at the seam between stacked side tiles in the Featured layout.
- Style Builder guide-line placement for centered/aligned Auto Row blocks.
- Style Builder aspect-ratio lock now uses pixel-space ratio instead of raw fractions, so resizing no longer snaps to square and typing Width/Height while locked updates the other field correctly.

## [v0.3.1](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.3.1) - 2026-07-06

### Fixed

- Selecting the same channel in more than one slot of a layout with EPG forwarding enabled could crash both the plugin's settings page and the web dashboard with a "Duplicate options" error.
- The plugin's dashboard server could leak database connections over time, eventually causing the dashboard (and sometimes the plugin's settings page) to stop responding until Dispatcharr was restarted.
- Auto Grid layout: content in stacked rows no longer doubles up its letterboxing gap at the seam between rows. Mismatched-aspect-ratio video now collects its padding at the outer top and bottom edges instead of gapping where the rows meet.

## [v0.3.0](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.3.0) - 2026-07-01

### Added

- Web dashboard: a mobile-friendly PWA served at `http://<host>:9292/dash/` for editing plugin settings and managing active streams without opening the Dispatcharr admin UI. Settings auto-save as you change them; an "Active Multiviews" view shows running streams with per-layout reload and per-channel reconnect; a Refresh action regenerates the M3U/EPG and triggers a Dispatcharr sync. Disabled by default, enable it via the Web Dashboard setting on the plugin settings page (requires a plugin/Dispatcharr reload to take effect). May require adding `9292:9292` to your `docker-compose.yml` ports.
- Plugin settings page now warns when Dispatcharr's Proxy "Channel Initialization Timeout" is set below 8 seconds, which can cause multiview tiles to fail on startup before channels finish initializing.
- After the first manual "Install PyAV" run, the plugin remembers consent and automatically reinstalls PyAV in the background if it's ever found missing or outdated (e.g. after a plugin update resets the vendored copy), with no need to click it again.

### Changed

- Plugin settings reorganized into a Global Settings section (Web Dashboard, Auto-Refresh Interval, Number of Multiview Layouts) above the existing Video Settings section.

### Fixed

- Closed the seam gap between stacked side tiles in the Featured layout, without cropping tile content.
- Audio drift resync on unstable streams.
- Dashboard API is now served under `/dash/api/` so it works correctly behind a reverse proxy.

## [v0.2.3](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.2.3) - 2026-06-25

### Added

- Intel QSV (`h264_qsv`) and VAAPI (`h264_vaapi`) hardware encoder options alongside the existing NVENC option.
- EPG forward mode now emits all programme metadata from Dispatcharr's EPG data: categories, episode numbers (xmltv_ns, onscreen, and external IDs), ratings, poster icons, live/premiere/new flags, runtime, country, language, and more. Previously only title, subtitle, and description were forwarded.
- EPG forward mode appends the layout's channel list to each programme description.
- Dynamic Warnings section in plugin settings that surfaces common configuration issues: missing PyAV installation, audio copy stream profile that will silently drop multi-track audio, and software encoding (`libx264`) with 4+ streams.

### Fixed

- Audio desync after channel reconnects and live_proxy fallback resets.
- Audio track language codes for channels with mixed-case multi-word names (e.g. "Rogers TV" was producing an incorrect language tag).
- Reduced startup log noise: port race condition demoted to INFO level; warmup retry added for initial channel connections to reduce false error messages.

## [v0.2.2](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.2.2) - 2026-06-25

### Added

- Per-layout EPG source mode: each layout can now forward real EPG data from one of its source channels instead of showing a built-in placeholder entry. When "Forward from channel" is selected, a channel picker appears and the layout's EPG shows actual programme titles, subtitles, and descriptions. Falls back to placeholder if the selected channel has no EPG data.

### Fixed

- Fixed an intermittent plugin load failure that occurred during Dispatcharr reload cycles. Relative `importlib.import_module` calls would fail with `KeyError` or `ModuleNotFoundError` if the parent package was absent from `sys.modules` at reload time. Replaced with a file-path-based loader that bypasses the package registry lookup.

## [v0.2.1](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.2.1) - 2026-06-24

### Added
- NVIDIA NVENC hardware encoding (`h264_nvenc`) as an optional encoder, selectable in plugin settings. Requires an NVIDIA GPU with driver support. Startup exits with a clear error if NVENC is selected but unavailable.
- Encoder preset selection for NVENC (p1-p7, default p4 balanced).
- Channel logos now load from HTTP URLs in addition to local file paths.
- Keepalive client: multiview registers a background connection with Dispatcharr's live_proxy so channels stay warm between viewer connections, preventing cold-start delays on reconnect.
- RSS memory usage logged in the worker heartbeat alongside per-channel frame rates. (Log level will be changed in a later release)

### Changed
- Channel reconnect now uses exponential backoff (2s, 4s, 8s... up to 60s) instead of a fixed 2s delay. Channels give up after 12 consecutive failures (roughly 8 minutes total).
- Logo rendering preserves aspect ratio and correctly composites transparency over black. Previously logos were cropped to a square and transparent areas were not handled.
- Logo loading moved to a background thread so startup is not blocked waiting for logo decode.

### Fixed
- Server returns 503 when a channel's live_proxy is not yet ready, instead of 200 with an empty body. This lets the compositor worker retry cleanly via its backoff rather than failing with a cryptic "Invalid data found" error.
- Audio resampler, audio write pipe FDs, and encoder output pipe FD are now properly closed on worker shutdown, eliminating file descriptor leaks.

## [v0.2.0](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.2.0) - 2026-06-24

### Added
- PyAV compositor worker: decoding, compositing, and encoding now run in a dedicated subprocess using PyAV (Python FFmpeg bindings). Each tile decodes in its own thread; the compositor blits the latest frame per tile onto a shared canvas and encodes it continuously. A slow or disconnected channel never stalls the grid.
- "Install PyAV" actions in plugin settings to download and install the PyAV media dependency at runtime, for both amd64/x86_64 and arm64/aarch64 hosts. Internet access is required for the one-time install.
- PyAV installation status indicator in plugin settings showing whether the media engine is installed and ready for this host's architecture.
- Configurable output frame rate: 24, 25, 30, 50, or 60 fps (default 30).
- CBR (constant bitrate) encoding: output bitrate is now a true constant-rate target, not a ceiling. The encoder pads with filler NAL units so the data rate stays flat regardless of content complexity, preventing IPTV player buffer drain and fast-forward on low-motion content.
- PTS rate limiter: the compositor paces output to 1x realtime using PTS timestamps, preventing fast-forward playback on fast machines where Dispatcharr's live proxy delivers frames faster than realtime.

### Changed
- Minimum required Dispatcharr version raised from v0.22.0 to v0.27.0.
- M3U and EPG refresh after "Regenerate M3U" are now serialized (M3U first, then EPG) using a Celery task chain. Previously both tasks fired simultaneously, which caused a Dispatcharr DB collision.
- Output bitrate field label changed from "Max Output Bitrate" to "Output Bitrate" to reflect CBR behaviour.

### Fixed
- A/V sync: audio is now PTS-aligned to video at stream startup. The audio buffer is also flushed on channel reconnect, preventing audio lag from accumulating.
- M3U + EPG simultaneous refresh could cause "INSERT 0 N" DB errors in Dispatcharr when both tasks hit the shared celery connection at the same time.

### Removed
- Hardware encoder options (NVIDIA h264_nvenc, Intel QuickSync h264_qsv, AMD/Intel VA-API h264_vaapi) are temporarily removed while the compositor is rebuilt on PyAV. Only libx264 (software) is available in this release. Hardware encoders will return as an opt-in mode in a future update.
- CRF/CQ quality mode removed. Encoding is CBR-only in this release.

## [v0.1.0](https://github.com/swvn-dispatch/dispatcharr-multiview/releases/tag/v0.1.0) - 2026-05-27

- Tile Dispatcharr channel streams into a single MPEG-TS output using FFmpeg xstack; each layout appears as a standard M3U channel
- Three layout styles: Auto Grid (square-ish grid, last row centred), Featured (channel 1 large on the left, others stacked on the right), and Top Featured (channel 1 full-width on top, others in a row at the bottom)
- Classic channel selection via dropdowns and Regex selection - enter a pattern (e.g. `TSN\s*\d`) to match channels by name automatically at stream time, sorted by channel number
- Hardware encoder support: Software (libx264), NVIDIA (h264_nvenc), Intel QuickSync (h264_qsv), AMD/Intel VA-API (h264_vaapi)
- Multi-audio output: choose a single channel's audio or output one AC3 track per tile; players with multi-track support (VLC, Infuse, mpv) can switch between them. Duplicate track labels are auto-numbered (e.g. `ts1`, `ts2`, `ts3`)
- Startup placeholder: tiled black frames with channel logos and a "Starting up..." banner display immediately while FFmpeg initialises the real stream
- EPG support: generates a 14-day XMLTV feed per layout with configurable title, subtitle, and category tags; registered automatically as an EPGSource in Dispatcharr
- Configurable output resolution (1080p / 720p / 480p), max bitrate, encoder quality (CRF/CQ/global_quality), and encoder preset per global settings
- Auto-refresh interval setting to regenerate M3U and EPG on a schedule (default 24 h; 0 = manual only via Regenerate M3U button)
- Streams open through Dispatcharr's ProxyServer so connections appear in the stats view, respect stream profiles and fallback behaviour, and carry the user-agent `multiview-plugin`
- No FFmpeg processes run when nobody is watching; processes are spawned per request and killed on disconnect
