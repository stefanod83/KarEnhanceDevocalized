import asyncio
import os
import uuid
import json
from pathlib import Path

import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .analyzer import analyze_vocal_multiband, analyze_mix_reference, downsample_heatmap, ANALYSIS_SR, HOP_LENGTH
from .processor import process_audio_async
from .models import AnalysisResponse, ProcessRequest, ProcessResponse, BandDefinition
from .utils import get_session_dir, get_audio_duration, get_waveform_peaks, SUPPORTED_EXTENSIONS, check_dependencies

app = FastAPI(title="Enhance Devocalized", version="2.0.0")


@app.on_event("startup")
async def startup_event():
    check_dependencies()

# Serve frontend static files
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# Active WebSocket connections per session
_ws_connections: dict[str, list[WebSocket]] = {}


@app.get("/")
async def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.post("/api/analyze")
async def analyze(
    vocal: UploadFile = File(...),
    instrumental: UploadFile = File(...),
    sensitivity: int = Form(default=9),
    band_count: int = Form(default=24),
    mode: str = Form(default="mix"),
):
    """
    Upload reference + instrumental tracks and perform multiband analysis.
    mode="vocal": reference is isolated vocal track (estimates where to boost)
    mode="mix": reference is original mix (computes exact gain ratios)
    Returns Server-Sent Events (SSE) for progress, then the final JSON result.
    """
    vocal_ext = Path(vocal.filename).suffix.lower()
    inst_ext = Path(instrumental.filename).suffix.lower()
    if vocal_ext not in SUPPORTED_EXTENSIONS or inst_ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported format. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")

    # Read file contents before entering the generator
    vocal_bytes = await vocal.read()
    inst_bytes = await instrumental.read()

    async def event_stream():
        def sse(event_type: str, data: dict) -> str:
            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        # Step 1: Save files
        yield sse("progress", {"step": "Salvataggio file...", "percent": 5})
        session_id = uuid.uuid4().hex[:12]
        session_dir = get_session_dir(session_id)
        # Always save as "vocal" prefix for compatibility (it's the reference track)
        vocal_path = os.path.join(session_dir, f"vocal{vocal_ext}")
        inst_path = os.path.join(session_dir, f"instrumental{inst_ext}")
        with open(vocal_path, "wb") as f:
            f.write(vocal_bytes)
        with open(inst_path, "wb") as f:
            f.write(inst_bytes)
        # Save mode for reanalyze/process
        with open(os.path.join(session_dir, "mode.txt"), "w") as f:
            f.write(mode)
        yield sse("progress", {"step": "File salvati", "percent": 10})
        await asyncio.sleep(0)

        # Step 2: Analysis (depends on mode)
        if mode == "mix":
            yield sse("progress", {"step": "Analisi confronto mix vs strumentale (STFT)...", "percent": 15})
            await asyncio.sleep(0)
            intensity_matrix, frame_times, band_defs = await asyncio.to_thread(
                analyze_mix_reference,
                vocal_path,
                inst_path,
                band_count,
            )
            yield sse("progress", {"step": "Analisi completata", "percent": 65})
        else:
            yield sse("progress", {"step": "Analisi multiband vocale (STFT)...", "percent": 15})
            await asyncio.sleep(0)
            intensity_matrix, frame_times, band_defs = await asyncio.to_thread(
                analyze_vocal_multiband,
                vocal_path,
                sensitivity,
                band_count,
            )
            yield sse("progress", {"step": "Analisi vocale completata", "percent": 65})
        await asyncio.sleep(0)

        # Save analysis results for later processing
        np.save(os.path.join(session_dir, "intensity_matrix.npy"), intensity_matrix)
        np.save(os.path.join(session_dir, "frame_times.npy"), frame_times)
        _save_band_defs(os.path.join(session_dir, "band_defs.json"), band_defs)

        # Step 3: Waveform peaks
        yield sse("progress", {"step": "Calcolo waveform riferimento...", "percent": 70})
        await asyncio.sleep(0)
        vocal_peaks = await asyncio.to_thread(get_waveform_peaks, vocal_path, 800)

        yield sse("progress", {"step": "Calcolo waveform strumentale...", "percent": 80})
        await asyncio.sleep(0)
        inst_peaks = await asyncio.to_thread(get_waveform_peaks, inst_path, 800)

        yield sse("progress", {"step": "Preparazione risultato...", "percent": 90})
        await asyncio.sleep(0)

        # Downsample heatmap for frontend
        heatmap, heatmap_times = downsample_heatmap(intensity_matrix, frame_times, mode=mode)
        duration = get_audio_duration(inst_path)
        hop_seconds = float(HOP_LENGTH) / ANALYSIS_SR

        result = AnalysisResponse(
            session_id=session_id,
            duration=duration,
            sample_rate=ANALYSIS_SR,
            n_bands=band_count,
            n_frames=intensity_matrix.shape[1],
            hop_seconds=hop_seconds,
            bands=band_defs,
            intensity_heatmap=heatmap,
            heatmap_times=heatmap_times,
            vocal_peaks=vocal_peaks,
            instrumental_peaks=inst_peaks,
            mode=mode,
        )
        yield sse("progress", {"step": "Completato!", "percent": 100})
        yield sse("result", result.model_dump())

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/reanalyze", response_model=AnalysisResponse)
async def reanalyze(
    session_id: str = Form(...),
    sensitivity: int = Form(default=9),
    band_count: int = Form(default=24),
    mode: str = Form(default="mix"),
):
    """Re-analyze with different parameters without re-uploading."""
    session_dir = get_session_dir(session_id)

    vocal_path = _find_file(session_dir, "vocal")
    inst_path = _find_file(session_dir, "instrumental")
    if not vocal_path or not inst_path:
        raise HTTPException(404, "Session not found or files missing")

    # Update saved mode
    with open(os.path.join(session_dir, "mode.txt"), "w") as f:
        f.write(mode)

    if mode == "mix":
        intensity_matrix, frame_times, band_defs = analyze_mix_reference(
            vocal_path,
            inst_path,
            n_bands=band_count,
        )
    else:
        intensity_matrix, frame_times, band_defs = analyze_vocal_multiband(
            vocal_path,
            sensitivity=sensitivity,
            n_bands=band_count,
        )

    # Save updated analysis
    np.save(os.path.join(session_dir, "intensity_matrix.npy"), intensity_matrix)
    np.save(os.path.join(session_dir, "frame_times.npy"), frame_times)
    _save_band_defs(os.path.join(session_dir, "band_defs.json"), band_defs)

    heatmap, heatmap_times = downsample_heatmap(intensity_matrix, frame_times, mode=mode)
    vocal_peaks = get_waveform_peaks(vocal_path, num_peaks=800)
    inst_peaks = get_waveform_peaks(inst_path, num_peaks=800)
    duration = get_audio_duration(inst_path)
    hop_seconds = float(HOP_LENGTH) / ANALYSIS_SR

    return AnalysisResponse(
        session_id=session_id,
        duration=duration,
        sample_rate=ANALYSIS_SR,
        n_bands=band_count,
        n_frames=intensity_matrix.shape[1],
        hop_seconds=hop_seconds,
        bands=band_defs,
        intensity_heatmap=heatmap,
        heatmap_times=heatmap_times,
        vocal_peaks=vocal_peaks,
        instrumental_peaks=inst_peaks,
        mode=mode,
    )


@app.post("/api/process", response_model=ProcessResponse)
async def process(req: ProcessRequest):
    """Process the instrumental track with multiband STFT spectral gain."""
    session_dir = get_session_dir(req.session_id)

    inst_path = _find_file(session_dir, "instrumental")
    if not inst_path:
        raise HTTPException(404, "Session not found or files missing")

    inst_ext = Path(inst_path).suffix.lower()

    # Read session mode
    mode_path = os.path.join(session_dir, "mode.txt")
    if os.path.exists(mode_path):
        with open(mode_path, "r") as f:
            mode = f.read().strip()
    else:
        mode = req.mode

    # Load pre-computed analysis results
    matrix_path = os.path.join(session_dir, "intensity_matrix.npy")
    times_path = os.path.join(session_dir, "frame_times.npy")
    bands_path = os.path.join(session_dir, "band_defs.json")

    if not os.path.exists(matrix_path):
        # Need to analyze first (e.g. band_count changed)
        vocal_path = _find_file(session_dir, "vocal")
        if not vocal_path:
            raise HTTPException(404, "Reference file not found. Please re-analyze.")
        if mode == "mix":
            intensity_matrix, frame_times, band_defs = analyze_mix_reference(
                vocal_path, inst_path, n_bands=req.band_count,
            )
        else:
            intensity_matrix, frame_times, band_defs = analyze_vocal_multiband(
                vocal_path, sensitivity=req.sensitivity, n_bands=req.band_count,
            )
        np.save(matrix_path, intensity_matrix)
        np.save(times_path, frame_times)
        _save_band_defs(bands_path, band_defs)
    else:
        intensity_matrix = np.load(matrix_path)
        frame_times = np.load(times_path)
        band_defs = _load_band_defs(bands_path)

    # Output file
    output_name = f"enhanced_{req.session_id}{inst_ext}"
    output_path = os.path.join(session_dir, output_name)

    # Progress callback via WebSocket
    async def on_progress(pct: int):
        await _broadcast_progress(req.session_id, pct)

    try:
        await process_audio_async(
            instrumental_path=inst_path,
            output_path=output_path,
            intensity_matrix=intensity_matrix,
            analysis_frame_times=frame_times,
            band_defs=band_defs,
            eq_level=req.eq_level,
            mode=mode,
            stereo_widen=req.stereo_widen,
            normalization=req.normalization,
            progress_callback=on_progress,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Processing failed: {str(e)}")

    duration = get_audio_duration(output_path)

    return ProcessResponse(
        session_id=req.session_id,
        output_filename=output_name,
        duration=duration,
    )


@app.get("/api/download/{session_id}/{filename}")
async def download(session_id: str, filename: str):
    """Download a processed file."""
    safe_filename = Path(filename).name
    if not safe_filename.startswith("enhanced_"):
        raise HTTPException(403, "Only enhanced files can be downloaded")
    session_dir = get_session_dir(session_id)
    filepath = os.path.join(session_dir, safe_filename)

    if not os.path.isfile(filepath):
        raise HTTPException(404, "File not found")

    return FileResponse(filepath, filename=safe_filename)


@app.get("/api/audio/{session_id}/{which}")
async def serve_audio(session_id: str, which: str):
    """Serve uploaded audio for browser playback."""
    if which not in ("vocal", "instrumental"):
        raise HTTPException(400, "Invalid track type")

    session_dir = get_session_dir(session_id)
    filepath = _find_file(session_dir, which)
    if not filepath:
        raise HTTPException(404, "File not found")

    return FileResponse(filepath)


@app.websocket("/ws/progress/{session_id}")
async def ws_progress(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for processing progress updates."""
    await websocket.accept()
    if session_id not in _ws_connections:
        _ws_connections[session_id] = []
    _ws_connections[session_id].append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if session_id in _ws_connections:
            try:
                _ws_connections[session_id].remove(websocket)
            except ValueError:
                pass
            if not _ws_connections.get(session_id):
                _ws_connections.pop(session_id, None)


async def _broadcast_progress(session_id: str, percent: int):
    """Send progress update to all WebSocket connections for a session."""
    conns = _ws_connections.get(session_id, [])
    for ws in list(conns):
        try:
            await ws.send_json({"type": "progress", "percent": percent})
        except Exception:
            pass


def _find_file(session_dir: str, prefix: str) -> str | None:
    """Find a file in session_dir starting with the given prefix."""
    if not os.path.isdir(session_dir):
        return None
    for f in os.listdir(session_dir):
        if f.startswith(prefix):
            return os.path.join(session_dir, f)
    return None


def _save_band_defs(path: str, band_defs: list[BandDefinition]) -> None:
    """Save band definitions to JSON."""
    with open(path, "w") as f:
        json.dump([bd.model_dump() for bd in band_defs], f)


def _load_band_defs(path: str) -> list[BandDefinition]:
    """Load band definitions from JSON."""
    with open(path, "r") as f:
        data = json.load(f)
    return [BandDefinition(**d) for d in data]
