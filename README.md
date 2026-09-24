# TerraWatch V2 — Change Detection for Karnataka

TerraWatch V2 is a Karnataka-focused geospatial change-detection application that combines satellite observation discovery, quality screening, deterministic change detection, temporal change analysis, candidate prioritization, evidence inspection, analyst review, and export into one integrated workflow.

The system is strictly domain-neutral: it is a **Change Detection Platform for Karnataka**, not a specialized forestry, agriculture, or land-clearing tool. While spectral change signals can be used across multiple domains, TerraWatch V2 detects, tracks, and prioritizes surface reflectance changes without asserting unverified physical or causal classifications.

The platform is designed around six core architectural principles:
- **Deterministic Processing**: All spectral calculations, spatial filtering, temporal tracking, and candidate triage use explicit mathematical formulas with zero stochastic or uncalibrated machine-learning components.
- **Explicit Provenance**: Every candidate change is traceable back through temporal signals, adjacent detection intervals, radiometric raster grids, and original satellite item IDs.
- **Quality-Aware Analysis**: Scene Classification Layer (SCL) masks gate observations before analysis, ensuring that clouds, cloud shadows, and invalid pixels never manifest as false change signals.
- **Scientific Reproducibility**: Given the same Area of Interest (AOI), date window, and satellite data, identical results are guaranteed.
- **Strict Geographic Integrity**: The authoritative Karnataka state boundary is strictly enforced at every level of the pipeline.
- **Zero Fabricated Production Data**: The system acquires real Sentinel-2 Level-2A observations dynamically from public STAC APIs and operates without dummy, seeded, or synthetic production records.

---

## Table of Contents

1. [Geographic Scope](#geographic-scope)
2. [End-to-End System Workflow](#end-to-end-system-workflow)
3. [Scientific Method & Spectral Processing](#scientific-method--spectral-processing)
4. [Quality Screening System](#quality-screening-system)
5. [Observation Lifecycle & State Model](#observation-lifecycle--state-model)
6. [Temporal Change Analysis](#temporal-change-analysis)
7. [Deterministic Candidate Triage & Scoring](#deterministic-candidate-triage--scoring)
8. [Candidate Explainability](#candidate-explainability)
9. [Evidence Architecture & 24-Point Provenance](#evidence-architecture--24-point-provenance)
10. [Analyst Review Model](#analyst-review-model)
11. [Investigation Export System](#investigation-export-system)
12. [Performance Engineering & Concurrency](#performance-engineering--concurrency)
13. [System Architecture](#system-architecture)
14. [Backend Module Reference](#backend-module-reference)
15. [Frontend Workstation Architecture](#frontend-workstation-architecture)
16. [Database Schema & Integrity](#database-schema--integrity)
17. [Data & Artifact Persistence Policy](#data--artifact-persistence-policy)
18. [Failure Semantics & Exception Hierarchy](#failure-semantics--exception-hierarchy)
19. [Security, Isolation & Data Integrity](#security-isolation--data-integrity)
20. [Workstation UX Philosophy](#workstation-ux-philosophy)
21. [Implementation History (Phases 0 — 19G)](#implementation-history-phases-0--19g)
22. [Architectural Decisions: What TerraWatch V2 Deliberately Avoids](#architectural-decisions-what-terrawatch-v2-deliberately-avoids)
23. [System Limitations](#system-limitations)
24. [Scientific Reproducibility Guarantee](#scientific-reproducibility-guarantee)
25. [API Specification](#api-specification)
26. [Repository Structure](#repository-structure)
27. [Technology Stack & Dependency Versions](#technology-stack--dependency-versions)
28. [Installation & Operational Setup](#installation--operational-setup)
29. [Verification & Test Commands](#verification--test-commands)
30. [Clean-Start Baseline Behavior](#clean-start-baseline-behavior)

---

## Geographic Scope

TerraWatch V2 is strictly restricted to **Karnataka, India**.

```
Authoritative Karnataka Boundary
  Source: GADM 4.1 India ADM1 (bundled in backend/app/data/karnataka.geojson)
  Validation Engine: Shapely geometry predicate (covers)
  Coordinate Reference System: WGS 84 (EPSG:4326)
```

### Boundary Enforcement Rules
- **Containment Requirement**: Any user-defined Area of Interest (AOI) must be **completely contained** within the authoritative boundary of Karnataka (`karnataka_boundary.covers(aoi_geometry)`).
- **Hard Gate**: An AOI that crosses the state boundary, touches the exterior, or falls entirely outside Karnataka is rejected with an explicit `AOIOutsideKarnatakaError` (HTTP 400).
- **Downstream Re-validation**: Invariant: *No downstream stage silently processes an AOI that is outside Karnataka.* Every subsequent pipeline stage (Imagery Acquisition, Change Detection, Temporal Analysis, Candidate Triage, Evidence Generation, and Export) verifies AOI provenance and geographic containment.

---

## End-to-End System Workflow

TerraWatch V2 organizes the scientific investigation into eight sequential stages:

```
[01 AREA] ➔ [02 OBSERVATIONS] ➔ [03 CHANGES] ➔ [04 CHANGE HISTORY]
                                                        │
[08 EXPORT] ⬅ [07 REVIEW] ⬅ [06 EVIDENCE] ⬅ [05 CANDIDATES]
```

### Stage 01 — AREA
- **Purpose**: Define and validate the geographic Area of Interest within Karnataka.
- **User Action**: Draw a bounding box interactively on the MapLibre map or adjust coordinate inputs, then save the AOI.
- **Inputs**: Bounding rectangle coordinates in WGS 84 (`min_lon, min_lat, max_lon, max_lat`).
- **Outputs**: Persisted AOI record with authoritative Karnataka containment confirmation.
- **Prerequisite / Failure**: Rejects geometries outside Karnataka or invalid self-intersecting polygons.
- **Persistence**: Saved in SQLite table `aoi`. Changing the AOI automatically invalidates all downstream stages.

### Stage 02 — OBSERVATIONS
- **Purpose**: Discover, screen, and acquire real Sentinel-2 Level-2A satellite acquisitions for the saved AOI.
- **User Action**: Select a target observation date range and trigger acquisition discovery.
- **Inputs**: Active AOI ID, start date, end date.
- **Outputs**: Chronological catalog of acquisitions classified by observation quality.
- **Critical Distinction**:
  - *Scenes Found*: Raw metadata items returned by STAC catalog search (up to 25 items).
  - *Screened Usable*: Scenes whose Scene Classification Layer (SCL) over the AOI contains $\ge 50\%$ valid pixels.
  - *Screened Unusable*: Scenes found in the catalog but rejected due to cloud cover ($\ge 50\%$), cloud shadow, or invalid pixels over the AOI.
  - *Usable Observations*: High-quality radiometric rasters prepared and ready for change detection. A date range with scenes found but zero usable scenes explicitly indicates quality gating, never "no satellite scenes found".
- **Persistence**: Saved in `imagery_acquisitions` table and local GeoTIFF rasters in `backend/imagery/`.

### Stage 03 — CHANGES
- **Purpose**: Orchestrate automated adjacent-interval change detection across chronological observations.
- **User Action**: Trigger automatic analysis orchestration.
- **Inputs**: Chronologically ordered set of usable observations ($N \ge 2$).
- **Outputs**: $N-1$ adjacent intervals with deterministic Normalized Difference Vegetation Index (NDVI) difference maps and discrete change polygons.
- **Optimization**: Detection reuse. Existing persisted interval detection runs matching the exact pair and parameters are reused without redundant computation.
- **Persistence**: Saved in `detection_runs` and `raw_change_regions` tables.

### Stage 04 — CHANGE HISTORY
- **Purpose**: Track spatial change signals across multiple observation intervals over time.
- **User Action**: Review temporal tracking summary, interval coverage, and signal behavior.
- **Inputs**: Adjacent interval detection runs and spatial change polygons.
- **Outputs**: Multi-interval temporal signals classified into behavioral categories (`persistent`, `recurrent`, `transient`, `isolated`).
- **Algorithm**: Spatial matching across intervals using polygon Intersection-over-Union (IoU $\ge 0.25$) via R-tree spatial indexing (`STRtree`).
- **Persistence**: Saved in `temporal_analyses`, `temporal_observations`, `temporal_relationships`, and `temporal_signals` tables.

### Stage 05 — CANDIDATES
- **Purpose**: Rank and prioritize tracked change signals for operational analyst inspection.
- **User Action**: Filter candidates by score, priority, or severity; select a specific candidate to investigate.
- **Inputs**: Temporal signals and associated physical metrics.
- **Outputs**: Deterministically ranked candidate list with bounded triage scores, priority tiers, and positive/limiting factor explanations.
- **Critical Distinction**: Candidate score is an **operational evidence triage score**, NOT a probability, likelihood, or confidence percentage.
- **Persistence**: Saved in SQLite table `candidates`.

### Stage 06 — EVIDENCE
- **Purpose**: Inspect candidate-specific multi-spectral and temporal evidence.
- **User Action**: View candidate extent, Before/After true-color RGB imagery, derived NDVI difference mask, spatial alignment status, and 24-point provenance validation.
- **Inputs**: Selected candidate ID.
- **Outputs**: Verified, aligned radiometric evidence package.
- **Critical Distinction**: True-color RGB imagery is **source observation evidence**; NDVI difference regions are **derived analytical evidence**. RGB imagery does not perform change detection.
- **Persistence**: Read-only inspection of persisted rasters and database records; no recomputation allowed.

### Stage 07 — REVIEW
- **Purpose**: Record analyst assessment and operational decision on the selected candidate.
- **User Action**: Assign a review decision (`accepted`, `rejected`, or `investigate`) and optionally attach analyst notes.
- **Inputs**: Selected candidate ID, decision enum, analyst notes text.
- **Outputs**: Persisted review record.
- **Scientific Immutability**: Analyst review is stored in an independent review table (`candidate_reviews`). Recording or modifying an analyst review **never mutates** the underlying candidate metrics, detection runs, or temporal signals.
- **Persistence**: Saved in SQLite table `candidate_reviews` and mirrored in candidate review status.

### Stage 08 — EXPORT
- **Purpose**: Export a portable, self-contained dossier of the investigated candidate for reporting or offline archival.
- **User Action**: Download ReportLab PDF dossier or structured JSON package.
- **Inputs**: Investigated candidate with complete evidence and review state.
- **Outputs**:
  - `terrawatch_investigation_<candidate_id>.pdf`: Professional 2-page investigation report with candidate metrics, visual evidence, review history, and provenance chain.
  - `terrawatch_investigation_<candidate_id>.json`: Comprehensive, portable machine-readable snapshot.
- **Security & Privacy**: Server filesystem paths are strictly sanitized and stripped from exports. Exports are strictly read-only.

---

## Scientific Method & Spectral Processing

### Satellite Observation Source
- **Sensor**: Sentinel-2 MultiSpectral Instrument (MSI), Level-2A (Bottom of Atmosphere Surface Reflectance).
- **Catalog Service**: Element 84 AWS Earth Search STAC API (`https://earth-search.aws.element84.com/v1`).
- **STAC Collection**: `sentinel-2-l2a`.
- **Spectral Bands Acquired**:
  - **Band 04 (Red)**: Central wavelength ~665 nm, 10-meter spatial resolution.
  - **Band 08 (NIR - Near Infrared)**: Central wavelength ~842 nm, 10-meter spatial resolution.
  - **Scene Classification Layer (SCL)**: 20-meter resolution, upsampled to 10-meter grid via nearest-neighbor interpolation.
  - **Band 02 (Blue), Band 03 (Green)**: Used in conjunction with Band 04 for true-color RGB preview generation.

### Normalized Difference Vegetation Index (NDVI)
The Normalized Difference Vegetation Index is computed per-pixel using single-precision floating point (`float32`):

$$\text{NDVI} = \frac{\text{B08} - \text{B04}}{\text{B08} + \text{B04}}$$

Pixels where $\text{B08} + \text{B04} = 0$ or nodata are set to NaN / nodata. Valid NDVI values are strictly bounded in $[-1.0, 1.0]$.

### Change Detection Metric
The change signal between an earlier acquisition ($t_1$) and later acquisition ($t_2$) is the absolute difference:

$$\Delta\text{NDVI} = |\text{NDVI}_{t_2} - \text{NDVI}_{t_1}|$$

Absolute difference captures both significant vegetation decrease (e.g., clearing, harvesting, drying) and vegetation increase (e.g., green-up, crop growth) without directional bias.

### Authoritative Detection Threshold
- **Threshold Value**: `0.20`
- **Operational Meaning**: Pixels where $\Delta\text{NDVI} \ge 0.20$ qualify as raw candidate change pixels. This threshold filters subtle seasonal oscillations, minor sun-angle variations, and atmospheric haze while capturing meaningful surface alterations.

### Spatial Noise Floor (Minimum Region Size)
- **Minimum Size**: `4 connected pixels`
- **Spatial Area**: On Sentinel-2's 10-meter grid, 4 pixels corresponds to approximately $400\text{ m}^2$ ($0.04\text{ ha}$).
- **Filtering**: Contiguous change patches with fewer than 4 pixels are rejected as spatial noise and excluded from qualifying change regions.

### Spatial Connectivity
- **Neighborhood**: **8-connectivity** (Moore neighborhood: horizontal, vertical, and diagonal neighbors).
- **Implementation**: Connected components are labeled using `scipy.ndimage.label` with a full $3 \times 3$ footprint (`np.ones((3, 3), dtype=int)`), and vectorized using `rasterio.features.shapes(..., connectivity=8)`.

### Spatial Compatibility Requirements
Before change calculation, Before and After rasters are rigorously validated for spatial congruence:
- Identical Coordinate Reference System (CRS)
- Identical affine transform matrix (origin and pixel size)
- Identical raster dimensions (width and height)
- Identical data types and nodata designations
- Full geometric intersection with the active AOI
*Incompatible rasters fail immediately with an explicit error and are never resampled or interpolated on the fly.*

---

## Quality Screening System

Satellite optical observations are vulnerable to clouds, cloud shadows, and atmospheric noise. TerraWatch V2 integrates quality screening directly into the analytical pipeline using the Sentinel-2 Scene Classification Layer (SCL).

### SCL Class Mapping
| SCL Class Code | Semantic Description | Quality Category | Used in Detection? |
|---|---|---|---|
| `4` | Vegetation | **VALID** | Yes |
| `5` | Bare Soil / Non-vegetated | **VALID** | Yes |
| `6` | Water | **VALID** | Yes |
| `3` | Cloud Shadows | **SHADOW** | Excluded |
| `8` | Cloud Medium Probability | **CLOUD** | Excluded |
| `9` | Cloud High Probability | **CLOUD** | Excluded |
| `10` | Thin Cirrus Clouds | **CLOUD** | Excluded |
| `0` | No Data | **INVALID** | Excluded |
| `1` | Saturated / Defective | **INVALID** | Excluded |
| `2` | Dark Feature / Cast Shadow | **INVALID** | Excluded |
| `7` | Unclassified | **INVALID** | Excluded |
| `11` | Snow / Ice | **INVALID** | Excluded |

### Usable Pixel Fraction Formula
Over the clipped AOI geometry:

$$\text{usable\_fraction} = \frac{\text{count}(\text{SCL} \in \{4, 5, 6\})}{\text{total\_aoi\_pixels}}$$

### Quality Gate (50% Minimum Usable Coverage)
- **Authoritative Threshold**: `0.50` ($50\%$)
- **Gating Rule**: If an observation has an AOI usable fraction $< 50\%$, it is marked `valid_unusable` and excluded from downstream change detection and temporal interval formation.
- **Dual-Observation Pixel Quality Gate**: During change detection between $t_1$ and $t_2$, a pixel is evaluated **only if it is valid in both $t_1$ and $t_2$**:

$$\text{Valid}(\text{pixel}) = (\text{SCL}_{t_1} \in \{4, 5, 6\}) \land (\text{SCL}_{t_2} \in \{4, 5, 6\})$$

Pixels occluded by clouds or shadows in either observation are masked out, preventing transient atmospheric features from generating false change signals.

---

## Observation Lifecycle & State Model

Every satellite observation transitions through an explicit lifecycle:

```
[STAC Discovery]
       │
       ▼
 [discovered]
       │
       ▼  (SCL-first screening)
 ┌─────┴────────────────────────┐
 │                              │
 ▼ (< 50% usable)               ▼ (≥ 50% usable)
[valid_unusable]          [usable] (B04/B08 downloaded & prepared)
                                │
                                ▼
                         [Detection / Temporal]
```

- `discovered`: Observation metadata identified in STAC search; raster assets not yet downloaded.
- `valid_unusable`: SCL screening determined that valid pixels cover $< 50\%$ of the AOI. The observation is cataloged but skipped for downstream processing.
- `usable`: Observation passed SCL screening ($\ge 50\%$ valid pixels), and radiometric B04/B08 bands were clipped, aligned, and persisted.
- `failed`: Network or raster processing failure occurred during acquisition.
- `legacy_unassessed`: Historical records created prior to SCL quality screening (tracked for backward compatibility).

### SCL-First Band Screening Optimization
To optimize bandwidth and processing speed, the system fetches and analyzes the 20-meter SCL asset **before** downloading the larger 10-meter B04 (Red) and B08 (NIR) assets. If the SCL fails the 50% threshold, download of the spectral bands is bypassed entirely.

---

## Temporal Change Analysis

Single-interval change detection cannot differentiate between transient events (e.g., agricultural plowing, weather anomalies) and sustained environmental changes. The temporal analysis engine evaluates spatial change signals across multiple sequential observation intervals.

### Chronological Interval Construction
- Given $N$ chronologically sorted usable observations:

$$O = [O_1, O_2, O_3, \dots, O_N] \quad (t_1 < t_2 < \dots < t_N)$$

- Exactly $N - 1$ adjacent intervals are constructed:

$$I_k = (O_k, O_{k+1}) \quad \text{for } k \in [1, N-1]$$

### Spatial Signal Tracking & IoU Threshold
- Across consecutive intervals $I_k$ and $I_{k+1}$, spatial change regions $R_A \in I_k$ and $R_B \in I_{k+1}$ are matched using Intersection-over-Union:

$$\text{IoU}(R_A, R_B) = \frac{\text{Area}(R_A \cap R_B)}{\text{Area}(R_A \cup R_B)}$$

- **Authoritative Matching Threshold**: `0.25`
- A spatial R-tree (`shapely.strtree.STRtree`) accelerates cross-interval geometric matching.

### Temporal State Classifications
Based on interval tracking across time, each temporal change signal is classified:
- **`persistent`**: The change region is detected and spatially matched across all supporting observation intervals ($N > 1$ intervals, support ratio $= 1.0$). Indicates long-term surface transformation.
- **`recurrent`**: The change region disappears and reappears across non-adjacent intervals (recurrence count $> 0$). Indicates cyclical or periodic activity.
- **`transient`**: The change region is detected in exactly one interval out of multiple evaluated intervals ($N > 1$ intervals, support count $= 1$). Indicates temporary disturbance or ephemeral surface condition.
- **`isolated`**: Evaluated across only a single interval ($N = 1$ interval, no multi-temporal tracking available).

### Non-Causal Seasonal Context
The engine checks day-of-year circular distance ($\le 60\text{ days}$) between observations to note whether compared dates fall within similar seasons. This context is strictly descriptive and makes no causal assertions regarding agricultural or weather phenomena.

---

## Deterministic Candidate Triage & Scoring

Change regions from temporal analysis are converted into prioritized investigation candidates.

### Operational Evidence Score Formula
The candidate score is calculated using four bounded, normalized components:

$$\text{Score} = 0.35 \times C_{\text{pers}} + 0.25 \times C_{\text{cons}} + 0.20 \times C_{\text{mag}} + 0.20 \times C_{\text{qual}}$$

Where all components are clamped to $[0.0, 1.0]$:
1. **Temporal Persistence ($C_{\text{pers}}$)**:

$$C_{\text{pers}} = \mathrm{clamp}(\text{persistence\_ratio}, 0.0, 1.0)$$

   Measures the fraction of usable observation intervals supporting the change.
2. **Temporal Consistency ($C_{\text{cons}}$)**:

$$C_{\text{cons}} = \mathrm{clamp}(\text{temporal\_consistency}, 0.0, 1.0)$$

   Measures the mean geometric IoU overlap consistency across supporting intervals.
3. **Change Magnitude ($C_{\text{mag}}$)**:

$$
C_{\text{mag}} =
0.60 \times
\mathrm{clamp}
\left(
\frac{\Delta NDVI_{\text{mean}} - 0.20}{0.60},
0.0,
1.0
\right)
+
0.40 \times
\mathrm{clamp}
\left(
\frac{Area_{m^2} - 500}{9500},
0.0,
1.0
\right)
$$

   Combines spectral shift magnitude and physical surface area.
4. **Observation Quality Support ($C_{\text{qual}}$)**:

$$C_{\text{qual}} = \mathrm{clamp}(\text{mean\_usable\_pixel\_fraction}, 0.0, 1.0)$$

   Reflects the atmospheric clarity and valid data support of the underlying imagery.

### What the Score Is and Is Not
- **IS**: A deterministic, repeatable operational evidence and triage ranking score bounded between `0.0000` and `1.0000`.
- **IS NOT**: A probability, a likelihood of ground truth, a confidence interval, or a machine-learning confidence score.

### Physical Severity Classification
Severity is determined **exclusively by physical change evidence** (area and spectral delta), independent of temporal state:
- **`high`**: Large spatial clearing ($\text{Area} \ge 5000\text{ m}^2$) OR strong spectral change ($\Delta\text{NDVI} \ge 0.50$) OR strong overall magnitude ($C_{\text{mag}} \ge 0.45$).
- **`medium`**: Moderate spatial clearing ($\text{Area} \ge 2400\text{ m}^2$) OR moderate spectral change ($\Delta\text{NDVI} \ge 0.35$) OR moderate overall magnitude ($C_{\text{mag}} \ge 0.20$).
- **`low`**: Change signals below medium physical criteria.

### Operational Priority Classification
Priority reflects operational urgency, combining evidence score, physical severity, temporal state, and observation quality:
- **`urgent`**: High physical severity with strong evidence ($\text{Score} \ge 0.50$) OR medium severity persistent change with strong evidence ($\text{Score} \ge 0.55$), provided quality support is not degraded ($C_{\text{qual}} \ge 0.40$).
- **`high`**: High severity OR medium severity with moderate evidence ($\text{Score} \ge 0.40$) OR persistent change with moderate evidence ($\text{Score} \ge 0.45$) OR recurrent change with medium severity.
- **`normal`**: Evidence score $\ge 0.30$, medium severity, or any valid tracked temporal state.
- **`low`**: Minor change signals with low evidence support.

---

## Candidate Explainability

Every candidate exposes transparent explainability factors derived from its underlying deterministic metrics:

- **Positive Supporting Factors**:
  - *Strong temporal persistence* ($C_{\text{pers}} \ge 0.70$)
  - *High spatial tracking consistency across intervals* ($C_{\text{cons}} \ge 0.70$)
  - *Significant physical change magnitude* ($\Delta NDVI_{\text{mean}} \ge 0.40$ or large footprint)
  - *High observation quality support* ($C_{\text{qual}} \ge 0.70$)
- **Limiting Factors**:
  - *Low temporal persistence* ($C_{\text{pers}} < 0.40$)
  - *Low spatial tracking overlap across intervals* ($C_{\text{cons}} < 0.40$)
  - *Limited physical change magnitude* ($\Delta NDVI_{\text{mean}} < 0.20$)
  - *Degraded observation quality support* ($C_{\text{qual}} < 0.40$)

*The system explains the strength and limitations of the observational evidence; it does not infer what human activity or natural phenomenon occurred on the ground.*

---

## Evidence Architecture & 24-Point Provenance

The evidence engine prepares an exhaustive evidence dossier for each candidate and performs a verified **24-point provenance validation** before returning data to the workstation:

```
Candidate Provenance Chain
  Candidate ID (UUID / deterministic slug)
    ├── Temporal Analysis ID ➔ Temporal Signal ID
    ├── AOI ID ➔ Karnataka Containment Verified
    ├── Detection Run IDs ➔ Before/After Acquisition IDs
    ├── Primary GeoTIFF Rasters (B04, B08)
    ├── SCL Quality Masks & Version Tags
    └── True-Color RGB Display Artifacts (B04, B03, B02)
```

### 24-Point Provenance Validation Rules (Verified by Test Suite)
1. **Valid Candidate Evidence Resolution**: Resolves all candidate-linked records without missing relations.
2. **Complete Provenance Chain**: Verifies full chain linking candidate, analysis, signal, AOI, runs, and acquisitions.
3. **Before/After Distinctness**: Enforces that $t_1 \ne t_2$ via database check constraints and validation logic.
4. **Display Artifact Provenance**: Confirms availability of percentile-stretched true-color RGB rasters.
5. **Quality-Mask Provenance**: Verifies that SCL quality rasters exist and match the standardized masking method.
6. **Missing Display Handling**: Falls back gracefully to radiometric preview rendering if display raster is absent.
7. **Missing Quality Mask Handling**: Flags evidence as quality-limited while preserving raster grid alignment.
8. **Stale Display Version Protection**: Rejects or ignores display artifacts from deprecated visualization versions.
9. **Stale Quality Processing Version Protection**: Identifies legacy quality masks and flags quality limitations.
10. **Detection Mismatch Protection**: Fails if detection run references acquisitions outside the candidate signal.
11. **Invalid Candidate Handling**: Returns clean HTTP 404 for non-existent candidate identifiers.
12. **Karnataka Containment Verification**: Re-validates that candidate geometry lies strictly within Karnataka.
13. **AOI Containment Intersection**: Confirms that candidate geometry intersects the persisted parent AOI.
14. **Incompatible Grid Metadata**: Detects raster CRS, affine transform, or dimension mismatch and flags misalignment.
15. **Temporal Evidence Immutability**: Asserts that evidence inspection causes zero writes to temporal tables.
16. **Detection Metrics Immutability**: Asserts that evidence inspection causes zero writes to detection tables.
17. **Zero Detection Recomputation**: Guarantees that detection algorithms are not re-executed during evidence loading.
18. **Zero Temporal Recomputation**: Guarantees that temporal analysis is not re-executed during evidence loading.
19. **Zero Fallback Acquisition**: Fails explicitly if primary raster is missing from disk; never substitutes arbitrary scenes.
20. **Legacy State Non-Reinterpretation**: Preserves historical `legacy_unassessed` states without silent mutation.
21. **Deterministic Idempotency**: Successive requests return byte-for-byte identical evidence structures.
22. **Zero-Change Distinctness**: Distinguishes between unavailable evidence (404) and confirmed 0-pixel change (200).
23. **Quality-Limited Semantics**: Accurately formats and reports quality-limitation reasons to the analyst.
24. **Independent Artifact Availability**: Missing preview artifacts do not invalidate geometric spatial alignment.

---

## Analyst Review Model

Analyst review is cleanly separated from the underlying scientific detection results:

- **Review Decisions**:
  - `accepted`: Analyst confirms the candidate warrants operational attention.
  - `rejected`: Analyst determines the candidate does not warrant operational attention (e.g., expected agricultural harvest).
  - `investigate`: Analyst flags the candidate for field verification or higher-resolution review.
- **Scientific Immutability**:
  - Review decisions and optional analyst notes are written to the `candidate_reviews` table.
  - An analyst review **never alters** the candidate score, severity, temporal state, NDVI delta, or spatial geometry.
  - Scientific observations and detection runs remain permanent, tamper-proof records.

---

## Investigation Export System

The export module generates portable, self-contained documentation of an investigated candidate.

### Export Formats & Endpoints
- **PDF Report**: `GET /api/v1/candidates/{candidate_id}/export?format=pdf`
  - Generated using ReportLab (`SimpleDocTemplate`).
  - Contains candidate summary, evidence metrics, true-color Before/After image previews, NDVI difference masks, analyst review decision, notes, and complete provenance chain.
- **JSON Data Package**: `GET /api/v1/candidates/{candidate_id}/export?format=json`
  - Structured, machine-readable snapshot conforming to `InvestigationSnapshot` schema.
  - Contains exact GeoJSON candidate geometry, centroid, detection summary, temporal metrics, observation metadata, and quality flags.

### Path Sanitization & Read-Only Safety
- **No Path Leaks**: Absolute server filesystem paths (e.g., `E:\Python\...` or `/var/terrawatch/...`) are strictly scrubbed from all export payloads.
- **Read-Only**: Export requests perform read operations only. They do not update review states or alter database timestamps.

---

## Performance Engineering & Concurrency

### SCL-First Screening
By evaluating the 20-meter Scene Classification Layer before fetching 10-meter spectral bands, unviable cloud-covered observations are filtered early, reducing network bandwidth and disk I/O.

### Bounded Concurrency
- Satellite acquisition and band preparation utilize a bounded worker pool (`concurrent.futures.ThreadPoolExecutor(max_workers=3)`).
- Concurrency is deliberately capped at 3 workers to prevent rate-limiting from public STAC endpoints and avoid CPU thrashing during raster decompression.

### Streaming Progress Updates
- Long-running multi-observation acquisitions and analysis orchestration provide real-time status streaming via Newline Delimited JSON (NDJSON) endpoints:
  - `POST /api/v1/imagery/acquire/stream`
  - `POST /api/v1/temporal/analyses/orchestrate/stream`
- Emits structured progress events (`discovery`, `screening`, `preparing`, `completed`) to ensure the workstation UI remains responsive.

### Verified Benchmark (Phase 18 Validation)
During Phase 18 performance validation, processing 25 candidate Sentinel-2 scenes demonstrated significant acceleration:
- **Baseline Sequential Runtime**: ~12.50 minutes
- **Optimized Concurrent Runtime**: ~5.36 minutes
- **Measured Speedup**: **2.34× faster** (concurrency = 3)
*(Benchmark conducted on representative Karnataka AOI under standard network conditions; not a universal SLA).*

---

## System Architecture

```
User (Geospatial Analyst)
  │
  ▼
Frontend Workstation (React 19 + TypeScript + Vite + MapLibre GL)
  │
  │ HTTP REST / NDJSON Event Streams
  ▼
FastAPI Backend Application (Python 3.11+, Uvicorn)
  │
  ├── app.domain       ➔ Karnataka GADM 4.1 Boundary Enforcement
  ├── app.imagery      ➔ STAC Search, SCL Quality Screening, Raster Prep
  ├── app.detection    ➔ Radiometric Verification, NDVI Difference Engine
  ├── app.temporal     ➔ Multi-Interval Spatial Tracking (STRtree, IoU)
  ├── app.candidates   ➔ Deterministic Triage Scoring & Explainability
  ├── app.evidence     ➔ Aligned Evidence Package & 24-Point Provenance
  ├── app.export       ➔ ReportLab PDF & Portable JSON Generation
  └── app.db           ➔ SQLite Persistence & Foreign-Key Integrity
  │
  ├── SQLite Database (`backend/terrawatch.db`)
  └── Local Raster Storage (`backend/imagery/*.tif`)
```

### Architectural Guarantees
- **Backend Authority**: The backend is the sole source of truth for scientific calculation, observation ordering, and candidate triage. The frontend never recalculates scores or reorders candidates.
- **Stateless Frontend Sessions**: No backend session or workflow table is maintained. Refreshing the browser resets the workstation to an empty state, while all historical records remain securely persisted in SQLite.

---

## Backend Module Reference

All backend code resides in `backend/app/`:

- [`main.py`](backend/app/main.py): FastAPI entry point, CORS configuration, API routes, streaming endpoints, and unified exception handling.
- [`config.py`](backend/app/config.py): Pydantic settings loading paths, STAC endpoint URLs, thresholds, and runtime defaults.
- [`db.py`](backend/app/db.py): SQLite database connection management, table schemas, migrations, and foreign-key enforcement (`PRAGMA foreign_keys = ON`).
- [`schemas.py`](backend/app/schemas.py): Pydantic models for API requests, responses, GeoJSON validation, and data structures.
- [`domain.py`](backend/app/domain.py): Authoritative Karnataka state boundary loader (GADM 4.1 GeoJSON) and Shapely spatial containment logic.
- [`imagery.py`](backend/app/imagery.py): STAC client, SCL quality screening, B04/B08 raster extraction, true-color RGB generation, and acquisition lifecycle.
- [`detection.py`](backend/app/detection.py): Spatial compatibility verification, NDVI difference calculation, 8-connected polygon extraction, and noise filtering.
- [`temporal.py`](backend/app/temporal.py): Chronological observation sequencing, adjacent interval construction, R-tree spatial tracking, and analysis orchestration.
- [`candidates.py`](backend/app/candidates.py): Deterministic candidate triage scoring, severity/priority assignment, explainability factors, and pagination.
- [`evidence.py`](backend/app/evidence.py): Candidate evidence assembly, spatial grid alignment verification, and 24-point provenance validation.
- [`export.py`](backend/app/export.py): ReportLab PDF investigation dossier generation, structured JSON export, and path sanitization.
- [`exceptions.py`](backend/app/exceptions.py): Custom application exception hierarchy with mapped HTTP status codes.
- [`logging_conf.py`](backend/app/logging_conf.py): Structured application logging configuration.

---

## Frontend Workstation Architecture

The frontend is constructed with **React 19**, **TypeScript**, and **Vite**, styled with modern custom workstation CSS.

- [`App.tsx`](frontend/src/App.tsx): Primary workstation shell managing top-level workflow state, active AOI, observations, candidates, and review actions.
- [`workflow.ts`](frontend/src/workflow.ts): Stage definitions, navigation contracts, and sequential step transition rules.
- [`WorkflowProgressIndicator.tsx`](frontend/src/WorkflowProgressIndicator.tsx): The canonical **8-stage passive workflow rail**. Renders on a single horizontal row (`grid-template-columns: repeat(8, 1fr)`) without wrapping. Indicates overall workflow position and completed (`✓`) stages.
- [`WorkflowNavigation.tsx`](frontend/src/WorkflowNavigation.tsx): Stage navigation controls providing explicit Previous and Next actions.
- [`MapCanvas.tsx`](frontend/src/MapCanvas.tsx): MapLibre GL map component rendering Karnataka boundaries, AOI bounding boxes, satellite raster extents, and candidate polygons.
- [`App.css`](frontend/src/App.css): Workstation design system utilizing dark theme, CSS grid layouts, high-contrast indicators, and zero utility framework dependencies.

---

## Database Schema & Integrity

Persisted in SQLite at `backend/terrawatch.db`. SQLite foreign keys are explicitly activated on every connection (`PRAGMA foreign_keys = ON`). Deletions are guarded with `ON DELETE RESTRICT`.

### Schema Tables (10 Tables)
1. **`aoi`**: User-defined Areas of Interest with GeoJSON polygon strings and timestamps.
2. **`imagery_acquisitions`**: Discovered and prepared Sentinel-2 scenes, SCL quality metrics, prepared raster paths, and display artifacts (`FOREIGN KEY (aoi_id) REFERENCES aoi(id) ON DELETE RESTRICT`).
3. **`detection_runs`**: Change detection runs between Before/After pairs (`FOREIGN KEY (before_acquisition_id) REFERENCES imagery_acquisitions(id) ON DELETE RESTRICT`, `FOREIGN KEY (after_acquisition_id) REFERENCES imagery_acquisitions(id) ON DELETE RESTRICT`, `CHECK (before_acquisition_id <> after_acquisition_id)`).
4. **`raw_change_regions`**: Contiguous changed polygons extracted from detection runs (`FOREIGN KEY (run_id) REFERENCES detection_runs(id) ON DELETE RESTRICT`).
5. **`temporal_analyses`**: Multi-observation temporal analysis summaries and temporal spans.
6. **`temporal_observations`**: Chronological junction table linking analyses to acquisitions (`FOREIGN KEY (analysis_id) REFERENCES temporal_analyses(id)`, `FOREIGN KEY (acquisition_id) REFERENCES imagery_acquisitions(id)`).
7. **`temporal_relationships`**: Adjacent interval evaluation records and matched region pairs.
8. **`temporal_signals`**: Tracked change signals across intervals, persistence ratios, and temporal states.
9. **`candidates`**: Prioritized candidate records, triage scores, ranks, severity, and priority tiers (`FOREIGN KEY (analysis_id, signal_id) REFERENCES temporal_signals(analysis_id, signal_id) ON DELETE RESTRICT`).
10. **`candidate_reviews`**: Analyst review decisions (`accepted`, `rejected`, `investigate`) and notes (`FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE RESTRICT`).

---

## Data & Artifact Persistence Policy

- **Code vs. Runtime Separation**: The git repository contains application logic and tests only. Runtime data is never committed.
- **Excluded by `.gitignore`**:
  - `*.db`, `*.sqlite3` (SQLite databases)
  - `backend/imagery/*` (downloaded satellite rasters and generated masks, preserving `.gitkeep`)
  - `dist/`, `build/` (frontend bundles)
  - `.venv/`, `node_modules/` (virtual environments and packages)
  - `*.pyc`, `__pycache__/`, `.pytest_cache/` (test and execution caches)
- **Clean Baseline**: A freshly cloned repository contains zero database rows and zero downloaded satellite scenes.

---

## Failure Semantics & Exception Hierarchy

TerraWatch V2 uses explicit failure semantics. Incomplete, incompatible, or invalid data causes explicit errors rather than silent fallbacks.

| Exception Class | HTTP Code | Trigger Condition |
|---|---|---|
| `AOIOutsideKarnatakaError` | 400 | Defined AOI is not completely contained within Karnataka boundary. |
| `NoAOIError` | 404 | Downstream operation requested when no active AOI exists. |
| `InsufficientUsableObservationsError` | 400 | Fewer than 2 usable observations available for change detection. |
| `IncompatibleAcquisitionsError` | 400 | Before/After rasters have mismatched CRS, dimensions, or grid transforms. |
| `CandidateNotFoundError` | 404 | Requested candidate ID does not exist in the database. |
| `CandidateEvidenceError` | 502 | Provenance chain verification failed (e.g., missing related record or unreadable raster). |
| `EvidenceUnavailableError` | 404 | Physical raster file referenced in database is missing from disk storage. |
| `TemporalAnalysisUnavailableError` | 404 | Requested temporal analysis record does not exist. |

---

## Security, Isolation & Data Integrity

- **Geographic Containment**: Authoritative Karnataka boundary loaded from read-only bundled GeoJSON; cannot be bypassed.
- **Cross-AOI Isolation**: Analysis runs, detections, and candidates are strictly keyed to specific AOIs; results cannot bleed across geographic areas.
- **Foreign-Key Integrity**: Enforced at runtime with SQLite `ON DELETE RESTRICT`; prevents orphan detection or candidate records.
- **Filesystem Path Protection**: Internal server directories are sanitized from API responses, JSON exports, and PDF reports.
- **Immutable Scientific Record**: Analyst reviews are stored independently and cannot alter underlying spectral or temporal measurements.

---

## Workstation UX Philosophy

The user interface is designed as a **professional geospatial workstation**:
- **Map-Dominant**: MapLibre GL canvas occupies the central visual space.
- **Passive Workflow Rail**: The 8-stage header rail displays overall progress without serving as an arbitrary jumping mechanism.
- **Explicit In-Stage Controls**: Stage navigation uses clear **Previous** and **Next** buttons with descriptive labels.
- **Plain Technical Language**: Clear metrics (`Evidence Score`, `Affected Area`, `Priority`, `Severity`) avoid confusing machine-learning jargon.
- **Separation of Source and Derived Evidence**: Source satellite imagery is visually and conceptually distinguished from derived change polygons.
- **Dual Export Actions**: Dedicated, independent buttons for PDF and JSON exports.

---

## Implementation History (Phases 0 — 19G)

| Phase | Milestone Name | Key Implementation & Result | Status |
|---|---|---|---|
| **0** | Foundation / Environment | FastAPI backend, Vite/React frontend, directory layout, pytest & vitest environments. | Completed |
| **1** | Karnataka AOI Enforcement | GADM 4.1 GeoJSON boundary loader, Shapely `covers` containment validation. | Completed |
| **2** | AOI Persistence & Map UI | Interactive MapLibre bounding box drawing, AOI database persistence. | Completed |
| **3** | STAC Ingestion & Quality | AWS Earth Search client, Sentinel-2 L2A querying, SCL quality screening gate. | Completed |
| **4** | Deterministic Detection | 10m B04/B08 NDVI calculation, $\Delta\text{NDVI} \ge 0.20$ threshold, 8-connectivity filtering. | Completed |
| **5** | Temporal Analysis | Adjacent interval sequencing, R-tree spatial tracking, IoU $\ge 0.25$ signal matching. | Completed |
| **6** | Candidate Triage | Deterministic triage formula ($0.35 C_{\text{pers}} + 0.25 C_{\text{cons}} + 0.20 C_{\text{mag}} + 0.20 C_{\text{qual}}$). | Completed |
| **7** | Evidence Assembly | Candidate extent extraction, Before/After alignment verification, raster preview generation. | Completed |
| **8** | Analyst Review | Independent review table (`candidate_reviews`), `accepted`/`rejected`/`investigate` decisions. | Completed |
| **9** | E2E Integration | Full pipeline connectivity from AOI creation to candidate review. | Completed |
| **10** | Export Hardening | ReportLab PDF investigation dossier and structured JSON export with path sanitization. | Completed |
| **A** | Schema & FK Integrity | SQLite foreign keys enabled at runtime, `ON DELETE RESTRICT` constraints installed. | Completed |
| **B** | SCL Quality Masking | Class-level masking (valid: 4, 5, 6; cloud: 8, 9, 10; shadow: 3; invalid: 0, 1, 2, 7, 11). | Completed |
| **C** | Detection Diagnostics | Quality-aware pixel counting, cloud/shadow exclusion tracking per detection run. | Completed |
| **D** | Temporal Hardening | Quality propagation across intervals, circular seasonal distance calculation. | Completed |
| **E** | RGB Display Hardening | True-color B04/B03/B02 percentile stretching (`sentinel-2-rgb-percentile-v2`). | Completed |
| **F** | AOI Editing | Invalidation propagation: editing an AOI cleanly invalidates downstream stale stages. | Completed |
| **G** | Score Normalization | Fully bounded triage scoring in $[0, 1]$, positive and limiting factor explanations. | Completed |
| **H** | Evidence Provenance | Implementation and testing of the authoritative 24-point provenance validation suite. | Completed |
| **I** | Review Separation | Preservation of scientific immutability during analyst review operations. | Completed |
| **J** | Export Hardening | Elimination of absolute filesystem paths from PDF and JSON export artifacts. | Completed |
| **K** | Workstation UX Audit | Standardization of analyst-facing terminology, elimination of probabilistic jargon. | Completed |
| **L** | Validation Consolidation | Verification of full test suites and failure scenario handling. | Completed |
| **16** | Repository Cleanup | Removal of legacy test files, temporary artifacts, and stale documentation. | Completed |
| **17** | Streaming Progress | NDJSON streaming endpoints for observation acquisition and analysis orchestration. | Completed |
| **18** | Performance Concurrency | SCL-first screening optimization and 3-worker concurrency (2.34× measured speedup). | Completed |
| **19A** | Scientific Output Audit | Elimination of mock/synthetic fallback pathways in production code. | Completed |
| **19B** | Header Status Cleanup | Consolidation of global status headers to prevent visual clutter. | Completed |
| **19C** | Automatic Orchestration | Single-action analysis orchestration in Stage 03 CHANGES with detection reuse. | Completed |
| **19D** | Passive Workflow Rail | Workflow rail made passive; explicit in-stage Previous/Next navigation established. | Completed |
| **19E** | Information Hierarchy | Primary placement of scientific metrics on authoritative stage cards. | Completed |
| **19F** | Workstation Layout | Single-row 8-column layout (`repeat(8, 1fr)`) and export state stabilization. | Completed |
| **19G** | Final Full Validation | 220 frontend tests, 195 backend tests passed; 16 failure scenarios validated. | Completed |

---

## Architectural Decisions: What TerraWatch V2 Deliberately Avoids

1. **No Machine Learning Models in Core Detection**: Surface change detection uses deterministic spectral equations and morphology. Avoids black-box hallucinations, training bias, and GPU dependencies.
2. **No Fabricated Production Data**: The system does not use synthetic satellite rasters, mock change footprints, or pre-seeded candidate databases.
3. **No Session / Workflow Table**: The backend maintains scientific entities (`aoi`, `acquisitions`, `runs`, `analyses`, `candidates`), not transient user UI state. Browser refresh cleanly restarts the UI workflow.
4. **No External Task Queues (Celery/Redis)**: Long operations stream over NDJSON using Python's native `concurrent.futures`. Keeps deployment lightweight and dependency-free.
5. **No Arbitrary Geographic Boundaries**: The platform strictly enforces Karnataka state borders.
6. **No Silent Error Fallback**: If an observation lacks data or rasters are misaligned, the system halts with a typed error rather than inventing fallback pixels.

---

## System Limitations

- **Optical Atmospheric Sensitivity**: Heavy monsoonal cloud cover can reduce the number of usable observations during rainy seasons (June–September).
- **Domain Neutrality / Non-Causal**: $\Delta\text{NDVI}$ detects spectral changes but cannot determine whether a change was caused by agriculture, logging, construction, or fire.
- **Triage Score Semantics**: Candidate score is an operational sorting metric, not a statistical likelihood of ground truth.
- **Fixed Sensor Resolution**: Sentinel-2's 10-meter pixel size limits detection to spatial alterations $\ge 400\text{ m}^2$.
- **External API Dependency**: Observation discovery relies on the availability of the Element 84 AWS Earth Search STAC service.

---

## Scientific Reproducibility Guarantee

Every analysis in TerraWatch V2 is fully reproducible:
- Exact STAC item IDs and UTC acquisition timestamps are recorded.
- Spectral calculations are deterministic (`float32` NDVI difference).
- Quality masks and detector parameters (`threshold = 0.20`, `min_region_pixels = 4`) are persisted.
- Spatial tracking uses a fixed IoU threshold (`0.25`).
- Candidate scoring uses an explicit mathematical formula.
*Re-running the pipeline with the same inputs and satellite scenes produces identical candidates and scores.*

---

## API Specification

All endpoints are prefixed with `/api/v1`:

### Health & System
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service health status probe. |
| `GET` | `/ready` | Service readiness probe. |

### Area of Interest (AOI)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/aoi` | Retrieve the active persisted AOI. |
| `POST` | `/aoi` | Create or update AOI (validates Karnataka containment). |
| `DELETE` | `/aoi` | Clear active AOI and invalidate downstream workflow. |

### Imagery & Observations
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/imagery/acquisitions` | List acquired observations for the active AOI. |
| `POST` | `/imagery/acquire` | Discover and acquire Sentinel-2 observations. |
| `POST` | `/imagery/acquire/stream` | Stream acquisition progress via NDJSON. |
| `GET` | `/imagery/acquisitions/{id}/display` | Retrieve true-color RGB GeoTIFF display raster. |

### Analysis & Change Detection
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/temporal/analyses/orchestrate` | Orchestrate change detection and temporal analysis. |
| `POST` | `/temporal/analyses/orchestrate/stream` | Stream orchestration progress via NDJSON. |
| `GET` | `/temporal/analyses/{id}` | Retrieve temporal analysis summary and intervals. |
| `POST` | `/detection/runs` | Execute single-interval change detection between two scenes. |

### Candidates, Evidence, Review & Export
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/candidates` | List ranked candidates (supports sorting and filtering). |
| `GET` | `/candidates/{id}` | Retrieve candidate details, metrics, and explanations. |
| `GET` | `/candidates/{id}/evidence` | Retrieve aligned candidate evidence and 24-point provenance. |
| `GET` | `/candidates/{id}/evidence/acquisitions/{acq_id}/preview` | Render PNG visual evidence preview. |
| `GET` | `/candidates/{id}/review` | Retrieve candidate review decision and analyst notes. |
| `PATCH` | `/candidates/{id}/review` | Record or update analyst review decision and notes. |
| `GET` | `/candidates/{id}/export?format=pdf` | Export candidate investigation dossier as PDF. |
| `GET` | `/candidates/{id}/export?format=json` | Export candidate investigation data package as JSON. |

---

## Repository Structure

```
TerraWatch V2/
├── backend/
│   ├── app/
│   │   ├── data/
│   │   │   └── karnataka.geojson     # Authoritative GADM 4.1 boundary
│   │   ├── candidates.py              # Candidate triage and scoring
│   │   ├── config.py                  # Application settings
│   │   ├── db.py                      # Database schema and connections
│   │   ├── detection.py               # NDVI change detection engine
│   │   ├── domain.py                  # Karnataka boundary validation
│   │   ├── evidence.py                # Evidence assembly & 24-pt provenance
│   │   ├── exceptions.py              # Application exception hierarchy
│   │   ├── export.py                  # ReportLab PDF & JSON export
│   │   ├── imagery.py                 # STAC client & SCL quality screening
│   │   ├── logging_conf.py            # Structured logging
│   │   ├── main.py                    # FastAPI routes & middleware
│   │   ├── schemas.py                 # Pydantic schemas
│   │   └── temporal.py                # Multi-interval temporal analysis
│   ├── imagery/                       # Local runtime raster cache (.gitkeep)
│   ├── tests/                         # Pytest test suite (195 tests)
│   ├── pytest.ini                     # Pytest configuration
│   └── requirements.txt               # Backend Python dependencies
├── frontend/
│   ├── src/
│   │   ├── App.tsx                    # Workstation root component
│   │   ├── App.css                    # Custom workstation design system
│   │   ├── main.tsx                   # React entry point
│   │   ├── workflow.ts                # 8-stage workflow definition
│   │   ├── WorkflowProgressIndicator.tsx # 8-column non-wrapping rail
│   │   ├── WorkflowNavigation.tsx     # Explicit Previous/Next controls
│   │   ├── MapCanvas.tsx              # MapLibre GL mapping component
│   │   └── *.test.tsx                 # Vitest test suite (220 tests, 17 files)
│   ├── index.html                     # HTML root template
│   ├── package.json                   # Frontend dependencies and scripts
│   ├── tsconfig.json                  # TypeScript configuration
│   └── vite.config.ts                 # Vite build configuration
├── .gitignore                         # Strict runtime artifact exclusions
└── README.md                          # Project documentation
```

---

## Technology Stack & Dependency Versions

### Backend (Python 3.11+)
- **FastAPI** (`>=0.110.0`): Asynchronous REST API framework
- **Uvicorn** (`>=0.29.0`): ASGI server
- **Pydantic** (`>=2.7.0`): Data validation and settings management
- **Rasterio** (`>=1.3.0`): Geospatial raster I/O (GDAL wrapper)
- **Shapely** (`>=2.0.0`): Planar geometry processing and spatial indexing
- **PyProj** (`>=3.6.0`): Cartographic projections and coordinate transformations
- **SciPy** (`>=1.11.0`): Connected component labeling (`scipy.ndimage`)
- **pystac-client** (`>=0.8.0`): STAC catalog client
- **ReportLab** (`>=4.0.0`): PDF generation
- **HTTPX** (`>=0.27.0`): Asynchronous HTTP client
- **Pytest** (`>=8.0.0`): Backend test framework

### Frontend (Node.js 20+)
- **React** (`^19.2.8`): UI framework
- **TypeScript** (`~6.0.2`): Type safety
- **Vite** (`^8.3.0`): Build tool and dev server
- **MapLibre GL** (`^6.9.0`): Geospatial map rendering
- **Vitest** (`^5.0.0`): Frontend test runner
- **Oxlint** (`^1.81.0`): JavaScript/TypeScript linter
- **Testing Library** (`@testing-library/react ^16.3.3`): Component testing

---

## Installation & Operational Setup

### Prerequisites
- Python 3.11 or higher
- Node.js 20 or higher (with npm)
- Active Internet connection (for live Sentinel-2 STAC queries)

### 1. Backend Setup
From the repository root:
```bash
# Create Python virtual environment
python -m venv .venv

# Activate virtual environment
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt

# Start FastAPI backend
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```
Backend will be live at `http://127.0.0.1:8000` (API documentation at `/docs`).

### 2. Frontend Setup
In a separate terminal:
```bash
cd frontend

# Install npm packages
npm install

# Start development server
npm run dev
```
Workstation UI will be accessible at `http://localhost:5173`.

---

## Verification & Test Commands

### Run Backend Tests
```bash
# Run all deterministic unit and integration tests (195 passed)
pytest backend/tests -k "not test_phase9_e2e"
```

### Run Frontend Tests
From the `frontend/` directory:
```bash
# Run full Vitest suite (220 passed across 17 test files)
npm test
```

### Run Code Quality & Production Build
From the `frontend/` directory:
```bash
# Lint with oxlint (0 errors)
npm run lint

# TypeScript verification and production bundle build
npm run build
```

---

## Clean-Start Baseline Behavior

When TerraWatch V2 is started for the first time:
1. **Empty Database**: SQLite creates tables automatically; all tables contain zero records.
2. **Empty Raster Cache**: `backend/imagery/` contains only `.gitkeep`.
3. **Clean Workstation State**: Stage 01 AREA is active; no AOI, observations, or candidates exist.
4. **Live Observation Ingestion**: Defining an AOI and selecting a date range triggers live discovery from Element 84 AWS Earth Search STAC.
