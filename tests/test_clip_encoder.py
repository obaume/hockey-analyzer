"""`FfmpegClipEncoder` (ticket 24) against real ffmpeg on tiny footage
generated per test session from ffmpeg's own test sources -- the encoder's
whole job is what ffmpeg writes, so a mocked subprocess would prove
nothing. Outputs are checked by probing them back."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest

from hockey_analyzer.clip_encoder import FfmpegClipEncoder, probe_footage
from hockey_analyzer.domain.clip_export import ClipEncodingError, ClipSegment

SECOND = 1000
FOOTAGE_MS = 6 * SECOND


def _make_footage(path: Path, *, video: list[str], audio: list[str] | None) -> Path:
    """6s of 160x120 test pattern at 25fps (a keyframe every second), with
    a sine tone unless `audio` is None."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    inputs = ["-f", "lavfi", "-i", "testsrc=duration=6:size=160x120:rate=25"]
    if audio is not None:
        inputs += ["-f", "lavfi", "-i", "sine=frequency=440:duration=6"]
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", *inputs, *video, "-g", "25"]
        + (audio if audio is not None else [])
        + ["-shortest", str(path)],
        check=True,
    )
    return path


@pytest.fixture(scope="session")
def footage(tmp_path_factory):
    """Name -> a source file: `phone_ready` is already H.264/AAC `.mp4`;
    the others each need re-encoding for a different reason."""
    root = tmp_path_factory.mktemp("footage")
    h264 = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    return {
        "phone_ready": _make_footage(
            root / "phone_ready.mp4", video=h264, audio=["-c:a", "aac"]
        ),
        "mpeg4_video": _make_footage(
            root / "mpeg4_video.avi", video=["-c:v", "mpeg4"], audio=["-c:a", "aac"]
        ),
        "mp3_audio": _make_footage(
            root / "mp3_audio.mkv", video=h264, audio=["-c:a", "libmp3lame"]
        ),
        "silent": _make_footage(root / "silent.mp4", video=h264, audio=None),
        # Full-range 4:2:0, as many phones and cameras record it.
        "full_range": _make_footage(
            root / "full_range.mp4",
            video=["-c:v", "libx264", "-pix_fmt", "yuvj420p"],
            audio=["-c:a", "aac"],
        ),
    }


def test_probe_reports_duration_and_codecs(footage):
    info = probe_footage(footage["phone_ready"])

    assert info.duration_ms == pytest.approx(FOOTAGE_MS, abs=100)
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"


def test_probe_reports_no_audio_codec_for_silent_footage(footage):
    info = probe_footage(footage["silent"])

    assert info.video_codec == "h264"
    assert info.audio_codec is None


def _encode(source, segments, output):
    FfmpegClipEncoder().encode(
        str(source), [ClipSegment(i, s, e) for i, (s, e) in enumerate(segments)], output
    )
    return probe_footage(output)


def test_footage_in_another_codec_is_re_encoded_to_h264_aac_mp4(footage, tmp_path):
    output = tmp_path / "clip.mp4"

    info = _encode(footage["mpeg4_video"], [(1500, 4000)], output)

    assert (info.video_codec, info.audio_codec) == ("h264", "aac")
    assert info.pixel_format == "yuv420p"
    assert info.duration_ms == pytest.approx(2500, abs=100)


def _video_packet_hashes(path: Path) -> list[str]:
    """MD5 of each compressed video packet, as stored in the file."""
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-i", str(path)]
        + ["-map", "0:v:0", "-c", "copy", "-f", "framemd5", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line for line in result.stdout.splitlines() if not line.startswith("#")]
    return [line.rsplit(",", 1)[1].strip() for line in lines]


@pytest.mark.parametrize("name", ["phone_ready", "silent", "full_range"])
def test_h264_aac_footage_passes_through_untouched(footage, tmp_path, name):
    output = tmp_path / "clip.mp4"

    info = _encode(footage[name], [(2000, 4000)], output)

    passed_through = _video_packet_hashes(output)
    assert passed_through
    assert set(passed_through) <= set(_video_packet_hashes(footage[name]))
    assert info.video_codec == "h264"
    # Copied streams can only be cut on packet/keyframe boundaries, so the
    # clip may run up to one keyframe interval (1s here) long.
    assert 1900 <= info.duration_ms <= 3000


def test_h264_footage_with_other_audio_is_re_encoded(footage, tmp_path):
    output = tmp_path / "clip.mp4"

    info = _encode(footage["mp3_audio"], [(2000, 4000)], output)

    assert (info.video_codec, info.audio_codec) == ("h264", "aac")
    assert not set(_video_packet_hashes(output)) & set(
        _video_packet_hashes(footage["mp3_audio"])
    )


@pytest.mark.parametrize(
    ("name", "max_overrun_ms"),
    [
        # Copied: each segment may run up to a keyframe interval (1s) long.
        pytest.param("phone_ready", 3 * SECOND, id="stream-copied"),
        pytest.param("mpeg4_video", 150, id="re-encoded"),
    ],
)
def test_a_reel_hard_cuts_every_segment_into_one_file(
    footage, tmp_path, name, max_overrun_ms
):
    output = tmp_path / "reel.mp4"

    info = _encode(footage[name], [(0, 1000), (2000, 3000), (4000, 5000)], output)

    assert (info.video_codec, info.audio_codec) == ("h264", "aac")
    assert 3000 - 150 <= info.duration_ms <= 3000 + max_overrun_ms


def test_a_relative_source_path_is_read_from_the_working_directory(
    footage, tmp_path, monkeypatch
):
    monkeypatch.chdir(footage["phone_ready"].parent)

    info = _encode("phone_ready.mp4", [(1000, 2000)], tmp_path / "clip.mp4")

    assert info.video_codec == "h264"


def test_the_output_folder_is_created(footage, tmp_path):
    output = tmp_path / "new" / "folder" / "clip.mp4"

    _encode(footage["phone_ready"], [(1000, 2000)], output)

    assert output.exists()


def test_unreadable_footage_raises_an_encoding_error(tmp_path):
    missing = tmp_path / "gone.mp4"

    with pytest.raises(ClipEncodingError):
        _encode(missing, [(1000, 2000)], tmp_path / "clip.mp4")


def test_a_clip_entirely_past_the_footage_raises_an_encoding_error(footage, tmp_path):
    with pytest.raises(ClipEncodingError):
        _encode(footage["phone_ready"], [(FOOTAGE_MS, FOOTAGE_MS)], tmp_path / "c.mp4")


def _decode_errors(path: Path) -> str:
    """Everything ffmpeg complains about while decoding the whole file --
    e.g. timestamps running backwards at a reel's cut, which phone players
    can stutter on."""
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path)]
        + ["-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    return result.stderr.strip()


@pytest.mark.parametrize("name", ["phone_ready", "mpeg4_video"])
def test_a_reel_cut_between_keyframes_decodes_cleanly(footage, tmp_path, name):
    output = tmp_path / "reel.mp4"

    _encode(footage[name], [(500, 1500), (2500, 3500), (4500, 5500)], output)

    assert _decode_errors(output) == ""
