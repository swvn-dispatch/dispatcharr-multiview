# Dispatcharr Multiview

> Dispatcharr plugin that combines multiple channel streams into a single tiled MPEG-TS output. Define named layouts, pick your channels, and each layout appears as a standard M3U channel you can open in any player.

## Support

This project is maintained in my spare time. If it's saved you some headaches, a tip is always appreciated.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/sethwv)

## How it works

Each `/stream/{n}` request spawns an FFmpeg process that pulls per-channel feeds from an internal HTTP endpoint, tiles them with `xstack`, and pipes MPEG-TS to the client. A placeholder image runs in parallel during startup so the stream opens immediately. Channels are opened through Dispatcharr's ProxyServer so they appear in stats and respect stream profiles.

## Configuration

**Global:** output resolution, max bitrate, video encoder (libx264 / h264_nvenc / h264_qsv / h264_vaapi), encoder quality and preset, auto-refresh interval.

**Per layout:** name, style (auto grid, featured, or top featured), channel selection mode, channel count, audio source, EPG title/subtitle/categories.

- **Auto grid:** square-ish grid sized from channel count, last row centred.
- **Featured:** channel 1 fills the left portion; side streams stack on the right. Side tiles meet at their shared seams (no gap). The featured stream always takes at least 60% of the width.
- **Top Featured:** channel 1 fills the top full-width strip; remaining channels sit in a horizontal row at the bottom. Tiles are naturally 16:9 and centred when they don't span the full width (n=2 or n=3 at 1080p). The featured stream always takes at least 60% of the height.
- **Classic selection:** pick channels from dropdowns.
- **Regex selection:** enter a pattern (e.g. `TSN\s*\d`) and channels matching it are resolved automatically at stream time, sorted by channel number.
- **Audio source:** single channel, or "All channels" for one AC3 track per tile (switch tracks in VLC/Infuse/mpv via the Audio Track menu). Duplicate track labels (e.g. three TSN channels) are automatically numbered: `ts1`, `ts2`, `ts3`.
- **EPG categories:** comma-separated tags (e.g. `Sports, News`) written to the XMLTV `<category>` field. EPG apps use this for colour coding.

After saving, click **Regenerate M3U** to write `multiview.m3u`, create/update the M3U account, and regenerate EPG data in Dispatcharr. The **Auto-Refresh Interval** setting controls how often this happens automatically (default: every 24 hours; 0 = manual only).

## Notes

- 4 channels per layout is the recommended max; higher counts may not initialise on all hardware.
- The placeholder shown during startup includes a centred "Starting up..." banner and channel logos where available.
- Hardware encoders require appropriate drivers and an FFmpeg build with that encoder compiled in.
