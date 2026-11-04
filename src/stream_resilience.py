"""Small, dependency-free recovery rules for multiview child streams."""

PTS_DISCONTINUITY_SECS = 2.0


def is_pts_discontinuity(gap):
    """Whether a source PTS jump invalidates the current clock anchor."""
    return abs(gap) >= PTS_DISCONTINUITY_SECS


def is_pts_progress(last_pts, pts):
    """Treat forward progress and a significant source reset as progress."""
    return (last_pts is None
            or pts > last_pts
            or pts <= last_pts - PTS_DISCONTINUITY_SECS)


def held_frame(latest, fallback, fresh_until, now):
    """Keep the last complete frame visible until its hold interval expires."""
    return latest if now < fresh_until else fallback


def presentation_target(anchor_pts, anchor_wall, next_pts, wall_time):
    """Return a video-only PTS anchor and the frame PTS due at wall_time."""
    if anchor_pts is None or anchor_wall is None:
        return next_pts, wall_time, next_pts
    return anchor_pts, anchor_wall, anchor_pts + wall_time - anchor_wall


def anchored_pts(anchor_pts, anchor_wall, wall_time):
    """Return source PTS at wall_time for an established presentation anchor."""
    if anchor_pts is None or anchor_wall is None:
        return None
    return anchor_pts + wall_time - anchor_wall


def decode_packet(packet, on_error):
    """Return decoded frames, treating one bad transition packet as recoverable."""
    try:
        return packet.decode()
    except Exception as error:  # noqa: BLE001
        on_error(error)
        return ()


def resample_frame(resampler, frame, make_resampler, on_error):
    """Retry a frame with fresh resampler state after a format transition."""
    try:
        return resampler.resample(frame), resampler
    except Exception as error:  # noqa: BLE001
        on_error("reset", error)
        try:
            resampler.close()
        except Exception:
            pass
        replacement = make_resampler()
        try:
            return replacement.resample(frame), replacement
        except Exception as retry_error:  # noqa: BLE001
            on_error("drop", retry_error)
            return (), replacement
