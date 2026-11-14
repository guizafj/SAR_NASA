# Deployment Guide — Rol 1 (`pozo`)

The app requires **zero NASA credentials at runtime** and serves pre-validated artefacts from
`data/processed/{case_id}/` (< 15 MiB total). Two deployment targets are pre-configured so the
public demo URL has an immediate fallback.

---

## Option 1 — Streamlit Community Cloud (Primary, fastest zero-ops deploy)

1. Push `release/mvp` or `main` to GitHub (`neoyuls/SAR_NASA`).
2. In <https://share.streamlit.io>, create a new app:
   - **Repository:** `neoyuls/SAR_NASA`
   - **Branch:** `main` (or `release/mvp`)
   - **Main file path:** `app.py`
   - **Python version:** `3.12` (pinned automatically via `.python-version`)
3. Streamlit Cloud installs `requirements.txt` (lightweight runtime stack; heavy SAR ingestion
   packages are isolated in `requirements-pipeline.txt` so GDAL never slows or breaks builds).
4. Verify health at `<app-url>/_stcore/health`.

---

## Option 2 — Container Fallback (Hugging Face Spaces / Render / Railway / Local)

If Streamlit Community Cloud sleeps or throttles during judging, deploy `deploy/Dockerfile`:

```bash
# Build from repository root (runs contract + artefact validation during docker build)
docker build -f deploy/Dockerfile -t sar-nasa:mvp .

# Run locally on http://localhost:8501
docker run --rm -p 8501:8501 sar-nasa:mvp
```

- **Hugging Face Spaces (Docker SDK):** set `PORT=7860` in Space variables or Dockerfile env;
  16 GB RAM free tier, no cold-sleep during active hours.
- **Render / Railway:** point service to `deploy/Dockerfile`; `PORT` is honoured automatically.

---

## Pre-deploy Checklist (Phase 4 gate)

```bash
./scripts/python.sh -m src.domain.contracts
./scripts/python.sh -m pytest tests -q
./scripts/python.sh -m src.data.validation --processed data/processed --require-cases
```
