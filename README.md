# TerraWatch V2 — Change Detection for Karnataka

Deterministic satellite change detection platform for Karnataka, powered by Sentinel-2 L2A observations via AWS Earth Search STAC, automated quality screening, and temporal change tracking.

## Prerequisites

- **Python**: 3.11 or higher
- **Node.js**: v20 or higher (with npm)
- **Internet Access**: Required for live Earth Search STAC observation acquisition and Sentinel-2 asset streaming

## Backend Setup

1. Create and activate a Python virtual environment:
   ```bash
   python -m venv .venv
   ```
   - On Windows (PowerShell):
     ```powershell
     .\.venv\Scripts\Activate.ps1
     ```
   - On Linux/macOS:
     ```bash
     source .venv/bin/activate
     ```

2. Install backend dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```

3. Run the FastAPI backend service:
   ```bash
   python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
   ```
   The backend API will be available at `http://localhost:8000`.

## Frontend Setup

1. Navigate to the frontend directory:
   ```bash
   cd frontend
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Run the development server:
   ```bash
   npm run dev
   ```
   The frontend application will be accessible at `http://localhost:5173`.

## Verification & Testing

### Backend Tests
Run the deterministic unit and integration test suite:
```bash
pytest backend/tests -k "not test_phase9_e2e"
```

### Frontend Tests, Linting & Build
From the `frontend` directory:
- Run test suite:
  ```bash
  npm test
  ```
- Run linter:
  ```bash
  npm run lint
  ```
- Run typecheck and production build:
  ```bash
  npm run build
  ```

## Runtime & Data Behavior

- **Clean Baseline**: Fresh installations start with zero application data. No production database or pre-generated data is tracked in the repository.
- **Automatic Schema Initialization**: The SQLite database (`backend/terrawatch.db`) is automatically created and initialized with all required tables on backend startup.
- **Runtime Imagery Storage**: The `backend/imagery/` directory serves as local runtime raster storage and is intentionally empty (containing only `.gitkeep`) in the repository.
- **Dynamic STAC Discovery**: Satellite observations are queried and acquired dynamically from the configured Element 84 Earth Search STAC endpoint (`https://earth-search.aws.element84.com/v1`) using user-defined Karnataka Areas of Interest (AOIs).
