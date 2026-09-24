from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for deterministic TerraWatch processing.

    ``minimum_usable_pixel_fraction`` is a provisional observation-level
    engineering policy. It rejects AOI crops where more than half of the
    pixels are unavailable after source-validity, SCL, cloud, and shadow
    filtering. It is not a claim that 50% usable coverage makes a scene
    scientifically sufficient for change detection; downstream analysis
    must still evaluate its own spatial and temporal requirements.
    """

    app_name: str = "TerraWatch V2"
    app_env: str = os.getenv("APP_ENV", "development")
    api_v1_prefix: str = os.getenv("API_V1_PREFIX", "/api/v1")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    database_path: Path = Path(
        os.getenv(
            "DATABASE_PATH",
            str(Path(__file__).resolve().parents[1] / "terrawatch.db"),
        )
    )
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    stac_api_url: str = os.getenv(
        "STAC_API_URL", "https://earth-search.aws.element84.com/v1"
    )
    stac_collection: str = os.getenv("STAC_COLLECTION", "sentinel-2-l2a")
    stac_result_limit: int = int(os.getenv("STAC_RESULT_LIMIT", "25"))
    acquisition_concurrency: int = int(os.getenv("ACQUISITION_CONCURRENCY", "3"))
    minimum_usable_pixel_fraction: float = float(
        os.getenv("MINIMUM_USABLE_PIXEL_FRACTION", "0.5")
    )
    minimum_detection_region_pixels: int = int(
        os.getenv("MINIMUM_DETECTION_REGION_PIXELS", "4")
    )
    imagery_data_dir: Path = Path(
        os.getenv(
            "IMAGERY_DATA_DIR",
            str(Path(__file__).resolve().parents[1] / "imagery"),
        )
    )


settings = Settings()
