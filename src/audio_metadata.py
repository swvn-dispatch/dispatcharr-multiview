"""FFmpeg output metadata for generated multiview audio-track labels."""


def generated_audio_metadata_args(audio_channels, enabled: bool = True) -> list:
    """Return FFmpeg metadata arguments for generated audio-track labels."""
    if not enabled:
        return []

    args = []
    for index, channel in enumerate(audio_channels):
        args += [
            f"-metadata:s:a:{index}", f"title={channel.name}",
            f"-metadata:s:a:{index}", f"language={channel.lang}",
        ]
    return args
