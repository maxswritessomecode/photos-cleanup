# ⚙️ AetherPhotos Backend Pipeline Engine

This repository contains the core pipeline engine for **AetherPhotos**—a premium, high-performance command-line utility and local FastAPI sidecar API server for de-duplicating and centralizing scattered photos and videos from macOS Photos, Amazon Photos, and Google Takeout archives.

This engine runs as the high-speed processing core behind the [AetherPhotos Desktop App](https://github.com/maxswritessomecode/aetherphotos-desktop).

---

## 🚀 Core Engine Features

1.  **High-Speed SQLite Indexing (`db.py`):**
    Indexes content hashes (SHA-256), file paths, camera metadata, GPS coordinates, and sizes into a highly-indexed local SQLite database to prevent memory overflows on huge libraries (100k+ assets).
    
2.  **Flexible Source Scrapers (`scrapers.py`):**
    *   **macOS Photos Scraper:** Extracts internal master records directly from the Apple `Photos.sqlite` database file inside macOS package structures.
    *   **Amazon Photos Scraper:** Crawls Amazon backup structures recursively.
    *   **Google Takeout ZIP Streamer:** Performs zero-extraction streaming zip parsing, matching JSON sidecar metadata to media files across split-zip boundaries and handling Google's 51-character long-filename truncations automatically.

3.  **Best-Candidate Deduplication Resolution (`dedup.py`):**
    Groups identical file hashes and selects the single "best copy" based on metadata completeness (EXIF, camera specs, GPS), size, and source priority. Normalizes Amazon Photos duplicates to match original assets seamlessly.

4.  **Preservation & Optimization (`centralizer.py` & `metadata.py`):**
    *   Saves files in a clean `YYYY/YYYY-MM/` structure.
    *   Updates macOS creation timestamps (`birthtime`) natively in-process using `ctypes` (`setattrlist`) to bypass expensive subprocess spawning, resulting in near-instant copies.
    *   Provides non-blocking execution thread handling and a progressive callback mechanism.

---

## 🖥️ CLI Usage

If you prefer to run the engine via the CLI:

### 1. Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Scan
Independently index your sources:
```bash
python cli.py scan \
  --macos "/Volumes/T9_2T/Library.photoslibrary" \
  --amazon "/Volumes/T9_2T/Amazon Photos" \
  --takeout "/Volumes/T9_2T/Google Takeout"
```

### 3. Run Deduplication
Process duplicate matching in-memory and select optimal candidates:
```bash
python cli.py dedup
```

### 4. Print Report
Generate a summary report of duplicates and storage space reclaimable:
```bash
python cli.py report
```

### 5. Execute Centralization
Execute the safe centralization copy process:
```bash
# Dry-run simulation first
python cli.py execute --dest "/Volumes/T9_2T/Centralized Backup" --dry-run

# Live centralization copy
python cli.py execute --dest "/Volumes/T9_2T/Centralized Backup"
```

---

## ⚡ FastAPI Sidecar API

The engine includes a FastAPI wrapper (`api.py`) that acts as a local sidecar service communicating over HTTP with the Tauri desktop shell:

```bash
python api.py
```
*   Exposes endpoints: `/scan`, `/dedup`, `/report`, `/execute`, `/execute/status`.
*   Includes WAL database concurrency configurations and automatic event-loop GIL-yielding to keep polling responsive.

---

## 🧪 Running Tests

AetherPhotos has a comprehensive test suite covering all scrapers, database operations, duplicate matching heuristics, and sidecar status APIs:

```bash
python -m unittest discover tests
```

---

## 🛡️ License
Proprietary / Personal Local Archive Utility.
