"""
Multiband vocal analysis using STFT.

Splits the vocal signal into N logarithmic frequency bands and computes
per-band intensity envelopes. The result is a 2D matrix (bands x frames)
indicating where and how much compensation is needed in the instrumental.
"""

import numpy as np
import librosa
from scipy.ndimage import median_filter

from .models import BandDefinition


# Analysis constants
ANALYSIS_SR = 22050
N_FFT = 2048        # ~93ms window at 22050 Hz
HOP_LENGTH = 512    # ~23ms hop → good temporal resolution


def compute_band_edges(n_bands: int, sr: int) -> np.ndarray:
    """
    Compute n_bands+1 logarithmically-spaced frequency edges
    from 60 Hz to min(16000, sr/2) Hz.

    Returns array of shape (n_bands+1,) in Hz.
    """
    f_min = 60.0
    f_max = min(16000.0, sr / 2.0)
    return np.geomspace(f_min, f_max, n_bands + 1)


def map_bins_to_bands(
    n_fft: int, sr: int, band_edges: np.ndarray
) -> list[np.ndarray]:
    """
    For each band, return the array of STFT bin indices that fall within it.
    Returns list of length n_bands, each element is an array of bin indices.
    """
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    n_bands = len(band_edges) - 1
    bin_groups = []
    for b in range(n_bands):
        mask = (freqs >= band_edges[b]) & (freqs < band_edges[b + 1])
        bin_groups.append(np.where(mask)[0])
    return bin_groups


def build_band_definitions(band_edges: np.ndarray) -> list[BandDefinition]:
    """Create BandDefinition objects from band edges."""
    n_bands = len(band_edges) - 1
    defs = []
    for b in range(n_bands):
        defs.append(BandDefinition(
            index=b,
            low_hz=round(float(band_edges[b]), 1),
            high_hz=round(float(band_edges[b + 1]), 1),
            center_hz=round(float(np.sqrt(band_edges[b] * band_edges[b + 1])), 1),
        ))
    return defs


def analyze_vocal_multiband(
    vocal_path: str,
    sensitivity: int = 5,
    n_bands: int = 12,
    progress_callback=None,
) -> tuple[np.ndarray, np.ndarray, list[BandDefinition]]:
    """
    Analyze vocal track into per-band intensity curves using STFT.

    Args:
        vocal_path: Path to the vocal audio file
        sensitivity: 1-10, higher = more sensitive to quiet vocals
        n_bands: Number of frequency bands (6-24)
        progress_callback: Optional callback(percent: int)

    Returns:
        intensity_matrix: np.ndarray of shape (n_bands, n_frames), values 0-1
        frame_times: np.ndarray of frame timestamps in seconds
        band_defs: list of BandDefinition describing each band
    """
    if progress_callback:
        progress_callback(5)

    # Load vocal track (mono, resampled to analysis rate)
    vocal, sr = librosa.load(vocal_path, sr=ANALYSIS_SR, mono=True)

    if progress_callback:
        progress_callback(15)

    # Compute STFT
    S = librosa.stft(vocal, n_fft=N_FFT, hop_length=HOP_LENGTH)
    mag = np.abs(S)  # shape: (n_fft//2+1, n_frames)

    frame_times = librosa.frames_to_time(
        np.arange(mag.shape[1]), sr=sr, hop_length=HOP_LENGTH
    )

    if progress_callback:
        progress_callback(25)

    # Band setup
    band_edges = compute_band_edges(n_bands, sr)
    bin_groups = map_bins_to_bands(N_FFT, sr, band_edges)
    band_defs = build_band_definitions(band_edges)

    # Per-band RMS energy
    n_frames = mag.shape[1]
    intensity_matrix = np.zeros((n_bands, n_frames), dtype=np.float64)

    for b, bins in enumerate(bin_groups):
        if len(bins) == 0:
            continue

        # RMS across bins in this band, per frame
        band_mag = mag[bins, :]  # shape: (n_bins_in_band, n_frames)
        band_rms = np.sqrt(np.mean(band_mag ** 2, axis=0))  # shape: (n_frames,)

        # Normalize this band independently to 0-1
        band_max = band_rms.max()
        if band_max > 0:
            intensity_matrix[b, :] = band_rms / band_max

        if progress_callback:
            progress_callback(int(25 + 50 * (b + 1) / n_bands))

    # Apply sensitivity threshold per band
    # sensitivity 1 → 0.70 (only very loud vocals)
    # sensitivity 5 → 0.42 (balanced, ≈ old max)
    # sensitivity 10 → 0.07 (detects everything)
    threshold = 0.70 - (sensitivity - 1) * 0.07
    intensity_matrix[intensity_matrix < threshold] = 0.0

    if progress_callback:
        progress_callback(80)

    # Light temporal smoothing per band (median filter removes single-frame spikes)
    for b in range(n_bands):
        intensity_matrix[b, :] = median_filter(intensity_matrix[b, :], size=5)

    # Clip to valid range
    intensity_matrix = np.clip(intensity_matrix, 0.0, 1.0)

    if progress_callback:
        progress_callback(90)

    return intensity_matrix, frame_times, band_defs


def analyze_mix_reference(
    mix_path: str,
    instrumental_path: str,
    n_bands: int = 24,
    progress_callback=None,
) -> tuple[np.ndarray, np.ndarray, list[BandDefinition]]:
    """
    Analyze mix vs instrumental to compute per-band gain ratios.

    For each frequency band and time frame, computes the exact gain
    needed to bring the instrumental's energy to match the original mix.

    No sensitivity parameter — the gain ratio is deterministic.

    Args:
        mix_path: Path to the original mix (vocals + instruments)
        instrumental_path: Path to the devocalized instrumental
        n_bands: Number of frequency bands (6-32)
        progress_callback: Optional callback(percent: int)

    Returns:
        gain_ratio_matrix: np.ndarray (n_bands, n_frames), values 1.0 to MAX_GAIN
        frame_times: np.ndarray of frame timestamps in seconds
        band_defs: list of BandDefinition describing each band
    """
    MAX_GAIN = 10.0  # cap at +20dB

    if progress_callback:
        progress_callback(5)

    # Load both tracks (mono, analysis sample rate)
    mix, sr = librosa.load(mix_path, sr=ANALYSIS_SR, mono=True)

    if progress_callback:
        progress_callback(10)

    inst, _ = librosa.load(instrumental_path, sr=ANALYSIS_SR, mono=True)

    if progress_callback:
        progress_callback(15)

    # Align lengths (pad shorter with zeros)
    if len(mix) > len(inst):
        inst = np.pad(inst, (0, len(mix) - len(inst)))
    elif len(inst) > len(mix):
        mix = np.pad(mix, (0, len(inst) - len(mix)))

    # STFT of both
    S_mix = librosa.stft(mix, n_fft=N_FFT, hop_length=HOP_LENGTH)
    mag_mix = np.abs(S_mix)
    del S_mix

    if progress_callback:
        progress_callback(25)

    S_inst = librosa.stft(inst, n_fft=N_FFT, hop_length=HOP_LENGTH)
    mag_inst = np.abs(S_inst)
    del S_inst

    if progress_callback:
        progress_callback(35)

    frame_times = librosa.frames_to_time(
        np.arange(mag_mix.shape[1]), sr=sr, hop_length=HOP_LENGTH
    )

    # Band setup
    band_edges = compute_band_edges(n_bands, sr)
    bin_groups = map_bins_to_bands(N_FFT, sr, band_edges)
    band_defs = build_band_definitions(band_edges)

    # Per-band gain ratio: RMS_mix / RMS_instrumental
    n_frames = mag_mix.shape[1]
    gain_ratio_matrix = np.ones((n_bands, n_frames), dtype=np.float32)

    eps = 1e-10  # avoid division by zero

    for b, bins in enumerate(bin_groups):
        if len(bins) == 0:
            continue

        # RMS across bins in this band, per frame
        mix_rms = np.sqrt(np.mean(mag_mix[bins, :] ** 2, axis=0))
        inst_rms = np.sqrt(np.mean(mag_inst[bins, :] ** 2, axis=0))

        # Gain ratio: how much to boost instrumental to match mix
        ratio = mix_rms / (inst_rms + eps)

        # Only boost (never attenuate), cap at MAX_GAIN
        ratio = np.clip(ratio, 1.0, MAX_GAIN)

        gain_ratio_matrix[b, :] = ratio

        if progress_callback:
            progress_callback(int(35 + 45 * (b + 1) / n_bands))

    del mag_mix, mag_inst

    if progress_callback:
        progress_callback(85)

    # Light temporal smoothing (median filter removes single-frame spikes)
    for b in range(n_bands):
        gain_ratio_matrix[b, :] = median_filter(gain_ratio_matrix[b, :], size=3)

    if progress_callback:
        progress_callback(90)

    return gain_ratio_matrix, frame_times, band_defs


def downsample_heatmap(
    intensity_matrix: np.ndarray,
    frame_times: np.ndarray,
    target_columns: int = 800,
    mode: str = "vocal",
) -> tuple[list[list[float]], list[float]]:
    """
    Downsample the intensity matrix for frontend visualization.

    For mode="mix", the matrix contains gain ratios (1.0 to MAX_GAIN).
    These are normalized to 0-1 for display: (ratio - 1) / (MAX_GAIN - 1).

    Returns:
        heatmap: list of lists [n_bands][target_columns], values 0-1
        times: list of timestamps for each column
    """
    MAX_GAIN = 10.0
    n_bands, n_frames = intensity_matrix.shape

    # For mix mode, normalize gain_ratio (1.0-MAX_GAIN) to 0-1 for visualization
    if mode == "mix":
        vis_matrix = np.clip((intensity_matrix - 1.0) / (MAX_GAIN - 1.0), 0.0, 1.0)
    else:
        vis_matrix = intensity_matrix

    if n_frames <= target_columns:
        return (
            vis_matrix.round(3).tolist(),
            frame_times.round(4).tolist(),
        )

    # Downsample by taking max in each window (preserves peaks)
    step = n_frames / target_columns
    heatmap = np.zeros((n_bands, target_columns), dtype=np.float64)
    times = np.zeros(target_columns)

    for i in range(target_columns):
        start_idx = int(i * step)
        end_idx = min(int((i + 1) * step), n_frames)
        if end_idx <= start_idx:
            end_idx = start_idx + 1
        heatmap[:, i] = vis_matrix[:, start_idx:end_idx].max(axis=1)
        times[i] = frame_times[start_idx]

    return heatmap.round(3).tolist(), times.round(4).tolist()
