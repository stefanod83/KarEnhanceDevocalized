"""
Multiband STFT spectral gain processor.

Applies per-frequency-band gain to the instrumental track based on
the vocal intensity analysis. Each band is independently boosted
in proportion to how much vocal energy was detected in that band.

Processing is done entirely in the STFT domain:
- Compute STFT of instrumental (per channel)
- Map STFT bins to the same bands used in analysis
- Apply per-bin gain modulated by the band's vocal intensity
- ISTFT back to time domain (perfect reconstruction via overlap-add)

No segmentation, no crossfades, no limiter — just clean spectral gain.
"""

import asyncio
import os
import subprocess

import numpy as np
import librosa
import soundfile as sf
from scipy.interpolate import interp1d

from .analyzer import compute_band_edges, map_bins_to_bands
from .models import BandDefinition
from .utils import get_output_codec_args


# ---------------------------------------------------------------------------
# Intensity interpolation
# ---------------------------------------------------------------------------

def _interpolate_intensity_to_stft_frames(
    intensity_matrix: np.ndarray,
    analysis_frame_times: np.ndarray,
    target_frame_times: np.ndarray,
    mode: str = "vocal",
) -> np.ndarray:
    """
    Interpolate analysis intensity matrix to match the instrumental's
    STFT frame rate (which may differ due to different sample rates).

    For mode="vocal": values are 0-1 intensity, fill_value=0.0
    For mode="mix": values are gain ratios (1.0 to MAX_GAIN), fill_value=1.0
    """
    n_bands = intensity_matrix.shape[0]
    n_target = len(target_frame_times)
    fill = 1.0 if mode == "mix" else 0.0
    result = np.zeros((n_bands, n_target), dtype=np.float64)

    for b in range(n_bands):
        interp_fn = interp1d(
            analysis_frame_times,
            intensity_matrix[b, :],
            kind='linear',
            bounds_error=False,
            fill_value=fill,
        )
        result[b, :] = interp_fn(target_frame_times)

    if mode == "mix":
        return np.clip(result, 1.0, None)  # gain ratios >= 1.0
    return np.clip(result, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Gain matrix computation
# ---------------------------------------------------------------------------

def _compute_gain_matrix(
    intensity_matrix: np.ndarray,
    eq_level: int,
    band_defs: list[BandDefinition],
    mode: str = "vocal",
) -> np.ndarray:
    """
    Convert per-band analysis matrix to per-band gain matrix.

    Mode "vocal":
        intensity_matrix has values 0-1, eq_level controls boost amount.
        gain = 1.0 + eq_factor * freq_scale * intensity

    Mode "mix":
        intensity_matrix has gain_ratio values (1.0 to MAX_GAIN).
        eq_level maps to compensation percentage (0=0%, 10=100%).
        gain = 1.0 + pct * (gain_ratio - 1.0)

    Returns:
        (n_bands, n_frames) gain values >= 1.0
    """
    if eq_level == 0:
        return np.ones_like(intensity_matrix, dtype=np.float32)

    if mode == "mix":
        # eq_level 0-10 maps to 0-100% compensation
        pct = np.float32(eq_level / 10.0)
        gain = 1.0 + pct * (intensity_matrix - 1.0)
        return gain.astype(np.float32)

    # --- Vocal mode (unchanged) ---
    eq_factor = eq_level * 0.25  # 0 to 2.5 linear gain offset

    gain = np.ones_like(intensity_matrix, dtype=np.float32)
    for b, bdef in enumerate(band_defs):
        center = bdef.center_hz
        # Frequency-dependent scaling: more boost in vocal range
        if 200 <= center <= 4000:
            freq_scale = 1.2
        elif 100 <= center <= 6000:
            freq_scale = 1.0
        else:
            freq_scale = 0.7

        gain[b, :] = 1.0 + eq_factor * freq_scale * intensity_matrix[b, :]

    return gain


# ---------------------------------------------------------------------------
# Stereo widening (kept from previous version)
# ---------------------------------------------------------------------------

def apply_stereo_widen(audio: np.ndarray, amount: float = 1.3) -> np.ndarray:
    """Widen stereo image using mid/side processing."""
    if audio.ndim != 2 or audio.shape[1] != 2:
        return audio

    mid = (audio[:, 0] + audio[:, 1]) / 2
    side = (audio[:, 0] - audio[:, 1]) / 2
    side *= amount

    result = np.empty_like(audio)
    result[:, 0] = mid + side
    result[:, 1] = mid - side
    return result


# ---------------------------------------------------------------------------
# Normalization (simplified: no tanh limiter)
# ---------------------------------------------------------------------------

def apply_normalization(audio: np.ndarray, mode: str, sample_rate: int) -> np.ndarray:
    """Apply peak or loudness normalization. No limiter distortion."""
    if mode == "none":
        return audio

    if mode == "peak":
        peak = np.max(np.abs(audio))
        if peak > 0:
            return audio * (0.95 / peak)
        return audio

    if mode == "loudness":
        rms = np.sqrt(np.mean(audio ** 2))
        if rms > 0:
            target_rms = 10 ** (-16 / 20)
            gain = target_rms / rms
            result = audio * gain
            # Safety clip guard (just scale down, no distortion)
            peak = np.max(np.abs(result))
            if peak > 0.95:
                result *= 0.95 / peak
            return result

    return audio


# ---------------------------------------------------------------------------
# Main processing: STFT spectral gain
# ---------------------------------------------------------------------------

# STFT parameters — use 2048 (same as analysis) to save memory
PROC_N_FFT = 2048
PROC_HOP = 512


def process_audio(
    instrumental_path: str,
    output_path: str,
    intensity_matrix: np.ndarray,
    analysis_frame_times: np.ndarray,
    band_defs: list[BandDefinition],
    eq_level: int,
    mode: str = "vocal",
    stereo_widen: bool = False,
    normalization: str = "none",
    progress_callback=None,
) -> None:
    """
    Process the instrumental track with multiband STFT spectral gain.

    Each frequency band is independently boosted based on how much
    vocal energy was detected in that band. Processing is done in the
    STFT domain for clean, artifact-free results.

    Memory-optimized: uses float32, processes in-place, frees intermediates.
    """
    # Load audio as float32 to save memory
    audio, sr = sf.read(instrumental_path, dtype='float32')
    total_samples = audio.shape[0]
    is_stereo = audio.ndim == 2

    if progress_callback:
        progress_callback(5)

    n_bands = len(band_defs)

    # Compute band mapping for THIS sample rate (may differ from analysis SR)
    band_edges = compute_band_edges(n_bands, sr)
    bin_groups = map_bins_to_bands(PROC_N_FFT, sr, band_edges)

    # Compute STFT frame times for the instrumental
    n_stft_frames = 1 + (total_samples - PROC_N_FFT) // PROC_HOP
    if n_stft_frames <= 0:
        n_stft_frames = 1
    stft_frame_times = librosa.frames_to_time(
        np.arange(n_stft_frames), sr=sr, hop_length=PROC_HOP
    )

    if progress_callback:
        progress_callback(10)

    # Interpolate analysis intensity to instrumental STFT frame rate (float32)
    interp_intensity = _interpolate_intensity_to_stft_frames(
        intensity_matrix, analysis_frame_times, stft_frame_times, mode=mode
    ).astype(np.float32)

    # Free analysis matrix early
    del intensity_matrix

    # Compute per-band gain matrix
    gain_matrix = _compute_gain_matrix(interp_intensity, eq_level, band_defs, mode=mode)
    del interp_intensity  # free after gain computation

    if progress_callback:
        progress_callback(15)

    # Expand gain_matrix from bands to individual STFT bins (float32)
    n_bins = PROC_N_FFT // 2 + 1
    bin_gain = np.ones((n_bins, len(stft_frame_times)), dtype=np.float32)
    for b, bins in enumerate(bin_groups):
        if len(bins) > 0:
            bin_gain[bins, :] = gain_matrix[b, :]
    del gain_matrix  # free

    if progress_callback:
        progress_callback(20)

    # Process each channel through STFT → gain → ISTFT
    # Memory-efficient: multiply gain directly into STFT, avoid extra copies
    def process_channel(signal: np.ndarray) -> np.ndarray:
        S = librosa.stft(signal, n_fft=PROC_N_FFT, hop_length=PROC_HOP)
        n_actual_frames = S.shape[1]
        n_precomp_frames = bin_gain.shape[1]

        if n_actual_frames <= n_precomp_frames:
            bg = bin_gain[:, :n_actual_frames]
        else:
            pad_width = n_actual_frames - n_precomp_frames
            bg = np.pad(bin_gain, ((0, 0), (0, pad_width)),
                        mode='constant', constant_values=1.0)

        # Apply gain directly to the complex STFT (magnitude × gain, phase preserved)
        # |S| * gain * e^(j*phase) = S * gain  (since S = |S| * e^(j*phase))
        S *= bg
        result = librosa.istft(S, hop_length=PROC_HOP, length=len(signal))
        del S  # free STFT immediately
        return result

    if is_stereo:
        left = process_channel(audio[:, 0].copy())
        audio[:, 0] = left
        del left
        if progress_callback:
            progress_callback(45)
        right = process_channel(audio[:, 1].copy())
        audio[:, 1] = right
        del right
        if progress_callback:
            progress_callback(70)
    else:
        audio = process_channel(audio)
        if progress_callback:
            progress_callback(70)

    # Optional stereo widening (modulated by max intensity across bands)
    if stereo_widen and is_stereo:
        # Recompute a lightweight widen curve from bin_gain
        # Max gain across all bins per frame → where processing is active
        max_gain_per_frame = np.max(bin_gain, axis=0)
        # Normalize: gain=1 means no activity, above 1 means activity
        widen_intensity = np.clip((max_gain_per_frame - 1.0) /
                                  (max_gain_per_frame.max() - 1.0 + 1e-8), 0, 1)
        # Interpolate to sample level
        widen_sample = np.interp(
            np.arange(total_samples) / sr,
            stft_frame_times[:len(widen_intensity)],
            widen_intensity
        ).astype(np.float32)
        del widen_intensity, max_gain_per_frame

        wet = apply_stereo_widen(audio, 1.3)
        c = widen_sample[:, np.newaxis]
        audio = audio * (1.0 - c) + wet * c
        del wet, c, widen_sample

    del bin_gain  # free the last large array

    if progress_callback:
        progress_callback(80)

    # Per-sample clip guard: only reduce samples that exceed the ceiling.
    # This preserves untouched regions at their original level.
    ceiling = np.float32(0.98)
    over_mask = np.abs(audio) > ceiling
    if np.any(over_mask):
        audio[over_mask] = np.sign(audio[over_mask]) * (
            ceiling + (1.0 - ceiling) * np.tanh(
                (np.abs(audio[over_mask]) - ceiling) / (1.0 - ceiling)
            )
        )
    del over_mask

    # Normalization (applies only if user selected peak or loudness)
    audio = apply_normalization(audio, normalization, sr)

    if progress_callback:
        progress_callback(90)

    # Write output as WAV first (lossless intermediate)
    wav_output = output_path
    if not output_path.lower().endswith('.wav'):
        wav_output = output_path + '.tmp.wav'

    sf.write(wav_output, audio, sr, subtype='FLOAT')

    if progress_callback:
        progress_callback(95)

    # Convert to target format using ffmpeg if needed
    if wav_output != output_path:
        ext = os.path.splitext(output_path)[1].lower()
        codec_args = get_output_codec_args(ext, instrumental_path)
        cmd = [
            'ffmpeg', '-y', '-i', wav_output,
            *codec_args,
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        os.remove(wav_output)

    if progress_callback:
        progress_callback(100)


async def process_audio_async(
    instrumental_path: str,
    output_path: str,
    intensity_matrix: np.ndarray,
    analysis_frame_times: np.ndarray,
    band_defs: list[BandDefinition],
    eq_level: int,
    mode: str = "vocal",
    stereo_widen: bool = False,
    normalization: str = "none",
    progress_callback=None,
) -> None:
    """Async wrapper for process_audio with WebSocket progress support."""
    last_pct = [0]

    def sync_progress(pct: int):
        last_pct[0] = pct

    loop = asyncio.get_event_loop()

    task = loop.run_in_executor(
        None,
        lambda: process_audio(
            instrumental_path, output_path,
            intensity_matrix, analysis_frame_times, band_defs,
            eq_level, mode, stereo_widen, normalization,
            progress_callback=sync_progress,
        )
    )

    # Poll progress while processing runs
    while not task.done():
        if progress_callback and last_pct[0] > 0:
            await progress_callback(last_pct[0])
        await asyncio.sleep(0.3)

    # Await to propagate exceptions
    await task

    if progress_callback:
        await progress_callback(100)
