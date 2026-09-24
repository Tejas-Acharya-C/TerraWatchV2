from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

KARNATAKA_BOUNDARY_SOURCE = "GADM 4.1 India ADM1"
KARNATAKA_BOUNDARY = shape(
    json.loads(
        (Path(__file__).parent / "data" / "karnataka.geojson").read_text(
            encoding="utf-8"
        )
    )
)


def is_within_karnataka(geometry: BaseGeometry) -> bool:
    return KARNATAKA_BOUNDARY.covers(geometry)