from pydantic import BaseModel, Field


class BandDefinition(BaseModel):
    """Describes one frequency band for multiband analysis."""
    index: int
    low_hz: float
    high_hz: float
    center_hz: float


class AnalysisRequest(BaseModel):
    sensitivity: int = Field(default=9, ge=1, le=10)
    band_count: int = Field(default=24, ge=6, le=32)


class ProcessRequest(BaseModel):
    session_id: str
    mode: str = Field(default="mix", pattern=r"^(vocal|mix)$")
    eq_level: int = Field(default=7, ge=0, le=10)
    band_count: int = Field(default=24, ge=6, le=32)
    sensitivity: int = Field(default=9, ge=1, le=10)
    stereo_widen: bool = False
    normalization: str = Field(default="none", pattern=r"^(none|peak|loudness)$")


class AnalysisResponse(BaseModel):
    session_id: str
    duration: float
    sample_rate: int
    n_bands: int
    n_frames: int
    hop_seconds: float
    bands: list[BandDefinition]
    intensity_heatmap: list[list[float]]  # [n_bands][n_vis_frames], 0-1
    heatmap_times: list[float]
    vocal_peaks: list[float]
    instrumental_peaks: list[float]
    mode: str = "vocal"  # "vocal" or "mix"


class ProcessResponse(BaseModel):
    session_id: str
    output_filename: str
    duration: float
