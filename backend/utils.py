import os
import re
import shutil
import subprocess
import json

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "..", "uploads"))

_SESSION_ID_RE = re.compile(r"^[a-f0-9]{12}$")

CODEC_MAP = {
    ".mp3": None,
    ".flac": "flac",
    ".wav": "pcm_s16le",
    ".opus": "libopus",
    ".ogg": "libvorbis",
    ".m4a": "aac",
    ".aac": "aac",
}

SUPPORTED_EXTENSIONS = set(CODEC_MAP.keys())


def get_upload_dir() -> str:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    return UPLOAD_DIR


def validate_session_id(session_id: str) -> str:
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")
    return session_id


def get_session_dir(session_id: str) -> str:
    validate_session_id(session_id)
    d = os.path.join(get_upload_dir(), session_id)
    os.makedirs(d, exist_ok=True)
    return d


def check_dependencies():
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found in PATH")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found in PATH")


def get_audio_duration(filepath: str) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {filepath}: {result.stderr}")
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"Invalid duration from ffprobe: {result.stdout}")


def get_audio_bitrate(filepath: str) -> int:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=bit_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath,
        ],
        capture_output=True, text=True,
    )
    try:
        val = int(result.stdout.strip())
        return val if val > 0 else 192000
    except (ValueError, TypeError):
        return 192000


def get_output_codec_args(ext: str, input_file: str) -> list[str]:
    if ext not in CODEC_MAP:
        return []
    codec = CODEC_MAP[ext]
    if codec is None:
        bitrate = get_audio_bitrate(input_file)
        return ["-b:a", str(bitrate)]
    return ["-c:a", codec]


def get_waveform_peaks(filepath: str, num_peaks: int = 1000) -> list[float]:
    """Extract waveform peaks for visualization using ffmpeg."""
    duration = get_audio_duration(filepath)
    samples_per_peak = max(1, int(duration * 44100 / num_peaks))

    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-f", "lavfi",
            "-i", f"amovie={filepath},asetnsamples={samples_per_peak},astats=metadata=1:reset=1",
            "-show_entries", "frame_tags=lavfi.astats.Overall.Peak_level",
            "-of", "json",
        ],
        capture_output=True, text=True,
    )

    try:
        data = json.loads(result.stdout)
        peaks = []
        for frame in data.get("frames", []):
            tags = frame.get("tags", {})
            level_str = tags.get("lavfi.astats.Overall.Peak_level", "-inf")
            if level_str == "-inf":
                peaks.append(0.0)
            else:
                db = float(level_str)
                peaks.append(max(0.0, min(1.0, 10 ** (db / 20))))
        return peaks
    except (json.JSONDecodeError, ValueError):
        return [0.0] * num_peaks
