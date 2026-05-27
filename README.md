# Dispatcharr Multiview

> Dispatcharr plugin that combines multiple channel streams into a single tiled MPEG-TS output. Define named layouts, pick your channels, and each layout appears as a standard M3U channel you can open in any player.

## How it works

Each `/stream/{n}` request spawns an FFmpeg process that pulls per-channel feeds from an internal HTTP endpoint, tiles them with `xstack`, and pipes MPEG-TS to the client. A placeholder image runs in parallel during startup so the stream opens immediately. Channels are opened through Dispatcharr's ProxyServer so they appear in stats and respect stream profiles.

## Configuration

**Global:** output resolution, max bitrate, video encoder (libx264 / h264_nvenc / h264_qsv / h264_vaapi), encoder quality and preset.

**Per layout:** name, style (auto grid or featured), channel selection mode, channel count, audio source.

- **Auto grid:** square-ish grid sized from channel count, last row centred.
- **Featured:** channel 1 fills the left portion; side streams stack on the right and are anchored to the right edge. The featured stream grows to claim horizontal space as side streams get shorter with higher counts (featured always takes at least 60% of the width).
- **Classic selection:** pick channels from dropdowns.
- **Regex selection:** enter a pattern (e.g. `TSN\s*\d`) and channels matching it are resolved automatically at stream time, sorted by channel number.
- **Audio source:** single channel, or "All channels" for one AC3 track per tile (switch tracks in VLC/Infuse/mpv via the Audio Track menu). Duplicate track labels (e.g. three TSN channels) are automatically numbered: `ts1`, `ts2`, `ts3`.

After saving, click **Regenerate M3U** to write `multiview.m3u` and create/update the M3U account in Dispatcharr.

## Notes

- 4 channels per layout is the recommended max; higher counts may not initialise on all hardware.
- The placeholder shown during startup includes a centred "Starting up..." banner and channel logos where available.
- Hardware encoders require appropriate drivers and an FFmpeg build with that encoder compiled in.
