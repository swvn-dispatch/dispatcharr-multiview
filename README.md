# Dispatcharr Multiview

> Dispatcharr plugin that combines multiple channel streams into a single tiled MPEG-TS output. Define named layouts, pick your channels, and each layout appears as a standard M3U channel you can open in any player.

## How it works

Each `/stream/{n}` request spawns an FFmpeg process that pulls per-channel feeds from an internal HTTP endpoint, tiles them with `xstack`, and pipes MPEG-TS to the client. A placeholder image runs in parallel during startup so the stream opens immediately. Channels are opened through Dispatcharr's ProxyServer so they appear in stats and respect stream profiles.

## Configuration

**Global:** output resolution, max bitrate, video encoder (libx264 / h264_nvenc / h264_qsv / h264_vaapi), encoder quality and preset.

**Per layout:** name, style (auto grid or featured), channel count, channel slots, audio source.

- **Auto grid:** square-ish grid sized from channel count, last row centred.
- **Featured:** channel 1 takes the left two-thirds, the rest stack on the right.
- **Audio source:** single channel, or "All channels" for one AC3 track per tile (switch tracks in VLC/Infuse/mpv via the Audio Track menu).

After saving, click **Regenerate M3U** to write `multiview.m3u` and create/update the M3U account in Dispatcharr.

## Notes

- 4 channels per layout is the recommended max; higher counts may not initialise on all hardware.
- Hardware encoders require appropriate drivers and an FFmpeg build with that encoder compiled in.
