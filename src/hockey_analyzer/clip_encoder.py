"""The ffmpeg-backed half of clip export (ticket 24): `FfmpegClipEncoder` is
the real `ClipEncoder` that `clip_export.run_export` hands each planned
output to, and `probe_footage` reads the footage facts a plan needs.

Every output is H.264/AAC `.mp4` (see CONTEXT.md's Clip entry). Footage
already in that format is stream-copied rather than re-encoded -- fast and
lossless, at the cost of each cut snapping back to the keyframe before it,
so a clip may start up to one keyframe interval early. Anything else is
re-encoded with frame-accurate cuts. The ffmpeg binary comes from the
`imageio-ffmpeg` wheel, so nothing needs installing separately -- see
docs/adr/0010-bundled-ffmpeg-for-clip-encoding.md.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg

from hockey_analyzer.domain.clip_export import ClipEncodingError, ClipSegment

_DURATION = re.compile(r"Duration: (\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_STREAM = re.compile(r"Stream #\d+:\d+.*?: (Video|Audio): (\w+)")
# The first comma-separated field after a video stream's codec details.
_PIXEL_FORMAT = re.compile(r"Video: \w+[^,]*, (\w+)")

# Settings every phone plays: 8-bit 4:2:0 H.264 (even dimensions are a
# 4:2:0 requirement) and AAC audio, with the index up front so playback
# can start before the whole file has downloaded.
_H264_AAC = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "160k",
]  # fmt: skip
_MP4 = ["-movflags", "+faststart"]

# framemd5 output: the stream's time base, then one line per packet
# (stream, dts, pts, duration, size, hash).
_TIME_BASE = re.compile(r"^#tb 0: (\d+)/(\d+)", re.MULTILINE)
_PACKET = re.compile(r"^0,\s*-?\d+,\s*(-?\d+),", re.MULTILINE)


@dataclass(frozen=True)
class FootageInfo:
    duration_ms: int
    video_codec: str | None
    audio_codec: str | None
    pixel_format: str | None


def probe_footage(path: str | Path) -> FootageInfo:
    """Duration and first video/audio codec of a footage file. Raises
    `ClipEncodingError` if ffmpeg can't read it."""
    # With no output file ffmpeg exits non-zero after printing the input's
    # description to stderr -- that description is all this needs.
    result = _run_ffmpeg(["-hide_banner", "-i", str(path)], check=False)
    duration = _DURATION.search(result.stderr)
    if duration is None:
        raise ClipEncodingError(f"couldn't read footage {path}:\n{result.stderr}")
    hours, minutes, seconds = duration.groups()
    codecs: dict[str, str] = {}
    pixel_format = None
    for line in result.stderr.splitlines():
        stream = _STREAM.search(line)
        # A cover image embedded in the file shows up as a video stream.
        if stream is not None and "attached pic" not in line:
            if stream.group(1) == "Video" and "Video" not in codecs:
                match = _PIXEL_FORMAT.search(line)
                pixel_format = match.group(1) if match else None
            codecs.setdefault(stream.group(1), stream.group(2))
    return FootageInfo(
        duration_ms=round(
            (int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000
        ),
        video_codec=codecs.get("Video"),
        audio_codec=codecs.get("Audio"),
        pixel_format=pixel_format,
    )


class FfmpegClipEncoder:
    """`ClipEncoder` over the bundled ffmpeg binary."""

    def encode(
        self, source_path: str, segments: Sequence[ClipSegment], output_path: Path
    ) -> None:
        # A segment clamped to nothing (an event logged past the footage's
        # end) has no frames to contribute.
        segments = [s for s in segments if s.end_ms > s.start_ms]
        if not segments:
            raise ClipEncodingError(
                f"{output_path.name}: every clip lies outside the footage"
            )
        info = probe_footage(source_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not _is_phone_ready(info):
            _run_ffmpeg(_re_encode_args(source_path, segments, info, output_path))
            return
        with tempfile.TemporaryDirectory() as scratch:
            _copy_segments(source_path, segments, Path(scratch), output_path)


def _copy_segments(
    source_path: str,
    segments: Sequence[ClipSegment],
    scratch: Path,
    output_path: Path,
) -> None:
    """Stream-copies each segment to its own part, then joins the parts.
    Each part starts exactly on a keyframe, so it carries no pre-roll
    packets from before its start. Joining whole parts, rather than cutting
    every segment in one concat pass, offsets each part by its real
    duration: that includes the frames B-frame reordering pushes past a cut.
    Either shortcut leaves timestamps running backwards at the joins."""
    parts = []
    for index, segment in enumerate(segments):
        start_ms = _keyframe_at_or_before(source_path, segment.start_ms)
        part = scratch / f"part{index}.mp4"
        _concat_copy(
            [_ConcatEntry(source_path, start_ms, segment.end_ms)], scratch, part
        )
        parts.append(_ConcatEntry(str(part)))
    _concat_copy(parts, scratch, output_path)


@dataclass(frozen=True)
class _ConcatEntry:
    """One file in a concat playlist; `None` points mean the file's own
    start/end."""

    path: str
    inpoint_ms: int | None = None
    outpoint_ms: int | None = None


def _concat_copy(
    entries: Sequence[_ConcatEntry],
    scratch: Path,
    output_path: Path,
) -> None:
    """Stream-copies `entries`, in order, into one `.mp4` via ffmpeg's
    concat demuxer."""
    playlist = scratch / "playlist.ffconcat"
    playlist.write_text(_playlist(entries), encoding="utf-8")
    concat = ["-f", "concat", "-safe", "0", "-auto_convert", "0"]
    streams = ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy"]
    _run_ffmpeg(
        ["-y", "-hide_banner", "-loglevel", "error", *concat]
        + ["-i", str(playlist), *streams, *_MP4, str(output_path)]
    )


def _keyframe_at_or_before(source_path: str, ms: int) -> int:
    """Where stream copy can really start a cut at `ms`: ffmpeg's input
    seek lands on that keyframe, and `-copyts` keeps its timestamp."""
    result = _run_ffmpeg(
        ["-hide_banner", "-loglevel", "error", "-copyts", "-ss", _seconds(ms)]
        + ["-i", source_path, "-map", "0:v:0", "-c", "copy", "-frames:v", "1"]
        + ["-f", "framemd5", "-"]
    )
    time_base = _TIME_BASE.search(result.stdout)
    packet = _PACKET.search(result.stdout)
    if time_base is None or packet is None:
        return ms
    numerator, denominator = map(int, time_base.groups())
    pts = int(packet.group(1))
    return min(ms, max(0, pts * numerator * 1000 // denominator))


def _is_phone_ready(info: FootageInfo) -> bool:
    """Already the H.264/AAC (or silent H.264) an export produces, so its
    streams can be copied as-is."""
    return (
        info.video_codec == "h264"
        and info.pixel_format in ("yuv420p", "yuvj420p")
        and info.audio_codec in ("aac", None)
    )


def _playlist(entries: Sequence[_ConcatEntry]) -> str:
    """An ffconcat playlist of `entries`."""
    lines = ["ffconcat version 1.0"]
    for entry in entries:
        # Inside single quotes everything is literal except the quote
        # itself, which is closed, escaped, and reopened.
        quoted = Path(entry.path).resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{quoted}'")
        if entry.inpoint_ms is not None:
            lines.append(f"inpoint {_seconds(entry.inpoint_ms)}")
        if entry.outpoint_ms is not None:
            lines.append(f"outpoint {_seconds(entry.outpoint_ms)}")
    return "\n".join(lines) + "\n"


def _re_encode_args(
    source_path: str,
    segments: Sequence[ClipSegment],
    info: FootageInfo,
    output_path: Path,
) -> list[str]:
    """Each segment is its own input, seeked by ffmpeg before decoding
    (fast, and frame-accurate when re-encoding), then concatenated."""
    has_audio = info.audio_codec is not None
    inputs: list[str] = []
    labels = ""
    for index, segment in enumerate(segments):
        inputs += [
            "-ss", _seconds(segment.start_ms),
            "-t", _seconds(segment.end_ms - segment.start_ms),
            "-i", source_path,
        ]  # fmt: skip
        labels += f"[{index}:v:0]" + (f"[{index}:a:0]" if has_audio else "")
    graph = (
        f"{labels}concat=n={len(segments)}:v=1:a={int(has_audio)}[cat_v]"
        + ("[a]" if has_audio else "")
        + ";[cat_v]scale=trunc(iw/2)*2:trunc(ih/2)*2[v]"
    )
    maps = ["-map", "[v]"] + (["-map", "[a]"] if has_audio else [])
    return [
        "-y", "-hide_banner", "-loglevel", "error", *inputs,
        "-filter_complex", graph, *maps, *_H264_AAC, *_MP4, str(output_path),
    ]  # fmt: skip


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.3f}"


def _run_ffmpeg(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise ClipEncodingError(result.stderr.strip())
    return result
