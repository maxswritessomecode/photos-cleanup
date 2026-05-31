from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
import sqlite3
import platform

from db import PhotosRegistry
from scrapers import macOSPhotosScraper, AmazonPhotosScraper, GoogleTakeoutScraper, get_file_sha256
from dedup import Deduplicator
from centralizer import Centralizer
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AetherPhotos API", description="Local backend sidecar for photos deduplication")

# Enable CORS for Tauri custom schemes and localhost origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "AetherPhotos API"}

# Dynamic tracking database path inside standard hidden App Support folder
def get_default_db_path() -> str:
    if platform.system() == "Darwin":
        app_support = os.path.expanduser("~/Library/Application Support/AetherPhotos")
    elif platform.system() == "Windows":
        app_support = os.path.join(os.environ.get("APPDATA", ""), "AetherPhotos")
    else:
        app_support = os.path.expanduser("~/.config/AetherPhotos")
    os.makedirs(app_support, exist_ok=True)
    return os.path.join(app_support, "photos_registry.db")

REGISTRY_DB_PATH = get_default_db_path()

def set_registry_db(db_path: str):
    """Overrides the default database path (useful for testing)."""
    global REGISTRY_DB_PATH
    REGISTRY_DB_PATH = db_path

class ScanRequest(BaseModel):
    db_path: Optional[str] = None
    macos_dir: Optional[str] = None
    amazon_dir: Optional[str] = None
    takeout_dir: Optional[str] = None

class ExecuteRequest(BaseModel):
    dest: str
    takeout_zips_dir: Optional[str] = None
    no_dry_run: Optional[bool] = False

# Background worker for scanning
def run_scan_worker(run_id: int, db_path: str, macos_dir: Optional[str], amazon_dir: Optional[str], takeout_dir: Optional[str]):
    registry = PhotosRegistry(db_path)
    cursor = registry.conn.cursor()
    
    def is_cancelled() -> bool:
        cursor.execute("SELECT status FROM pipeline_runs WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        return row and row[0] == 'cancelled'
        
    try:
        # 1. macOS Photos Scraper
        if macos_dir and os.path.exists(macos_dir):
            cursor.execute("SELECT count(*) FROM files WHERE source_type = 'macos_photos' AND source_root = ?", (macos_dir,))
            has_macos = cursor.fetchone()[0] > 0
            
            if not has_macos:
                scraper = macOSPhotosScraper(macos_dir)
                inserted = 0
                for item in scraper.scan_metadata():
                    if is_cancelled():
                        return
                    abs_path = os.path.join(macos_dir, item['relative_path'])
                    sha256 = get_file_sha256(abs_path) if os.path.exists(abs_path) else None
                    registry.add_file(
                        run_id=run_id,
                        source_type='macos_photos',
                        source_root=macos_dir,
                        relative_path=item['relative_path'],
                        filename=item['filename'],
                        file_size=item['file_size'],
                        sha256=sha256,
                        exif_date=item['exif_date'],
                        latitude=item['latitude'],
                        longitude=item['longitude'],
                        camera_make=item['camera_make'],
                        camera_model=item['camera_model']
                    )
                    inserted += 1
                    if inserted % 500 == 0:
                        registry.conn.commit()
                registry.conn.commit()
            else:
                if is_cancelled():
                    return
                # Instantly reuse existing records by updating run_id
                cursor.execute("UPDATE files SET run_id = ? WHERE source_type = 'macos_photos' AND source_root = ?", (run_id, macos_dir))
                registry.conn.commit()
        
        # 2. Amazon Photos Scraper
        if amazon_dir and os.path.exists(amazon_dir):
            cursor.execute("SELECT count(*) FROM files WHERE source_type = 'amazon_photos' AND source_root = ?", (amazon_dir,))
            has_amazon = cursor.fetchone()[0] > 0
            
            if not has_amazon:
                scraper = AmazonPhotosScraper(amazon_dir)
                inserted = 0
                for item in scraper.scan_files():
                    if is_cancelled():
                        return
                    abs_path = os.path.join(amazon_dir, item['relative_path'])
                    sha256 = get_file_sha256(abs_path) if os.path.exists(abs_path) else None
                    registry.add_file(
                        run_id=run_id,
                        source_type='amazon_photos',
                        source_root=amazon_dir,
                        relative_path=item['relative_path'],
                        filename=item['filename'],
                        file_size=item['file_size'],
                        sha256=sha256,
                        exif_date=item['exif_date']
                    )
                    inserted += 1
                    if inserted % 500 == 0:
                        registry.conn.commit()
                registry.conn.commit()
            else:
                if is_cancelled():
                    return
                # Instantly reuse existing records
                cursor.execute("UPDATE files SET run_id = ? WHERE source_type = 'amazon_photos' AND source_root = ?", (run_id, amazon_dir))
                registry.conn.commit()
                
        # 3. Google Takeout Scraper
        if takeout_dir and os.path.exists(takeout_dir):
            # To check if Google Takeout is already scanned, we check if there are any records 
            # where the parent directory of source_root zip files matches takeout_dir
            cursor.execute("SELECT DISTINCT source_root FROM files WHERE source_type = 'google_takeout'")
            rows = cursor.fetchall()
            old_takeout = os.path.dirname(rows[0][0]) if rows else None
            has_takeout = (old_takeout == takeout_dir)
            
            if not has_takeout:
                scraper = GoogleTakeoutScraper(takeout_dir)
                inserted = 0
                for item in scraper.scan_zip_files():
                    if is_cancelled():
                        return
                    registry.add_file(
                        run_id=run_id,
                        source_type='google_takeout',
                        source_root=item['source_root'], # Absolute ZIP file path
                        relative_path=item['relative_path'],
                        filename=item['filename'],
                        file_size=item['file_size'],
                        sha256=None,
                        takeout_json_date=item['takeout_json_date'],
                        latitude=item['latitude'],
                        longitude=item['longitude']
                    )
                    inserted += 1
                    if inserted % 500 == 0:
                        registry.conn.commit()
                registry.conn.commit()
            else:
                if is_cancelled():
                    return
                # Instantly reuse existing records
                cursor.execute("UPDATE files SET run_id = ? WHERE source_type = 'google_takeout'", (run_id,))
                registry.conn.commit()
        
        if is_cancelled():
            return
            
        # Clean up any leftover orphaned records from previous runs
        cursor.execute("DELETE FROM files WHERE run_id != ?", (run_id,))
        registry.conn.commit()
        
        registry.complete_run(run_id, 'completed')
    except Exception as e:
        print(f"Scan background worker failed: {e}")
        try:
            registry.complete_run(run_id, 'failed')
        except Exception:
            pass
    finally:
        registry.close()

@app.post("/scan")
async def scan_sources(request: ScanRequest, background_tasks: BackgroundTasks):
    db_path = request.db_path or REGISTRY_DB_PATH
    
    registry = PhotosRegistry(db_path)
    cursor = registry.conn.cursor()
    
    # Intelligently clean only directories that changed or are empty
    try:
        # Check if database schema exists before selecting
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
        if cursor.fetchone():
            # 1. macOS Photos
            cursor.execute("SELECT DISTINCT source_root FROM files WHERE source_type = 'macos_photos'")
            row = cursor.fetchone()
            old_macos = row[0] if row else None
            if request.macos_dir != old_macos or not request.macos_dir:
                cursor.execute("DELETE FROM files WHERE source_type = 'macos_photos'")
                
            # 2. Amazon Photos
            cursor.execute("SELECT DISTINCT source_root FROM files WHERE source_type = 'amazon_photos'")
            row = cursor.fetchone()
            old_amazon = row[0] if row else None
            if request.amazon_dir != old_amazon or not request.amazon_dir:
                cursor.execute("DELETE FROM files WHERE source_type = 'amazon_photos'")
                
            # 3. Google Takeout
            cursor.execute("SELECT DISTINCT source_root FROM files WHERE source_type = 'google_takeout'")
            rows = cursor.fetchall()
            old_takeout = os.path.dirname(rows[0][0]) if rows else None
            if request.takeout_dir != old_takeout or not request.takeout_dir:
                cursor.execute("DELETE FROM files WHERE source_type = 'google_takeout'")
                
        cursor.execute("DELETE FROM pipeline_runs")
        registry.conn.commit()
    except sqlite3.OperationalError:
        pass # Will auto-initialize inside PhotosRegistry if not present
        
    run_id = registry.create_run()
    registry.close()
    
    # Delegate parsing to FastAPI BackgroundTasks thread pool
    background_tasks.add_task(
        run_scan_worker,
        run_id,
        db_path,
        request.macos_dir,
        request.amazon_dir,
        request.takeout_dir
    )
    
    return {
        "status": "success",
        "message": "Scan triggered successfully in background.",
        "run_id": run_id
    }

@app.post("/scan/cancel/{run_id}")
async def cancel_scan(run_id: int):
    if not os.path.exists(REGISTRY_DB_PATH):
        raise HTTPException(status_code=404, detail="Registry database not found.")
    registry = PhotosRegistry(REGISTRY_DB_PATH)
    cursor = registry.conn.cursor()
    cursor.execute("UPDATE pipeline_runs SET status = 'cancelled' WHERE run_id = ?", (run_id,))
    registry.conn.commit()
    registry.close()
    return {"status": "success", "message": f"Scan run {run_id} marked for cancellation."}

@app.get("/run/{run_id}")
async def get_run_status(run_id: int):
    if not os.path.exists(REGISTRY_DB_PATH):
        raise HTTPException(status_code=404, detail="Registry database not found.")
    registry = PhotosRegistry(REGISTRY_DB_PATH)
    cursor = registry.conn.cursor()
    cursor.execute("SELECT status FROM pipeline_runs WHERE run_id = ?", (run_id,))
    row = cursor.fetchone()
    registry.close()
    if row:
        return {"status": "success", "run_id": run_id, "run_status": row[0]}
    raise HTTPException(status_code=404, detail=f"Run ID {run_id} not found.")

@app.post("/dedup")
async def deduplicate_assets():
    if not os.path.exists(REGISTRY_DB_PATH):
        raise HTTPException(status_code=404, detail="Registry database not found. Scan first.")
        
    registry = PhotosRegistry(REGISTRY_DB_PATH)
    deduplicator = Deduplicator(registry)
    deduplicator.process_duplicates()
    
    cursor = registry.conn.cursor()
    cursor.execute("SELECT count(*) FROM files WHERE is_duplicate = 0")
    unique_canonical = cursor.fetchone()[0]
    cursor.execute("SELECT count(*) FROM files WHERE is_duplicate = 1")
    duplicates = cursor.fetchone()[0]
    
    registry.close()
    return {
        "status": "success",
        "unique_canonical": unique_canonical,
        "duplicates": duplicates
    }

@app.get("/report")
async def get_report():
    if not os.path.exists(REGISTRY_DB_PATH):
        return {
            "status": "success",
            "total_assets": 0,
            "unique_canonical": 0,
            "duplicates": 0,
            "reclaimable_bytes": 0,
            "breakdown": []
        }
        
    registry = PhotosRegistry(REGISTRY_DB_PATH)
    cursor = registry.conn.cursor()
    
    # Restrict report queries to the active/latest pipeline run ID to prevent run crosstalk
    cursor.execute("SELECT max(run_id) FROM pipeline_runs")
    row = cursor.fetchone()
    latest_run_id = row[0] if row else None
    
    if latest_run_id is None:
        registry.close()
        return {
            "status": "success",
            "total_assets": 0,
            "unique_canonical": 0,
            "duplicates": 0,
            "reclaimable_bytes": 0,
            "breakdown": []
        }
    
    cursor.execute("SELECT count(*) FROM files WHERE run_id = ?", (latest_run_id,))
    total_assets = cursor.fetchone()[0]
    
    cursor.execute("SELECT count(*) FROM files WHERE run_id = ? AND is_duplicate = 0", (latest_run_id,))
    unique_canonical = cursor.fetchone()[0]
    
    cursor.execute("SELECT count(*) FROM files WHERE run_id = ? AND is_duplicate = 1", (latest_run_id,))
    duplicates = cursor.fetchone()[0]
    
    cursor.execute("SELECT sum(file_size) FROM files WHERE run_id = ? AND is_duplicate = 1", (latest_run_id,))
    reclaimable_bytes = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT source_type, is_duplicate, count(*), sum(file_size) FROM files WHERE run_id = ? GROUP BY source_type, is_duplicate", (latest_run_id,))
    breakdown_rows = cursor.fetchall()
    
    breakdown = []
    for r in breakdown_rows:
        breakdown.append({
            "source_type": r[0],
            "status": "duplicate" if r[1] == 1 else "canonical",
            "count": r[2],
            "file_size": r[3]
        })
        
    registry.close()
    return {
        "status": "success",
        "total_assets": total_assets,
        "unique_canonical": unique_canonical,
        "duplicates": duplicates,
        "reclaimable_bytes": reclaimable_bytes,
        "breakdown": breakdown
    }

@app.post("/execute")
async def execute_centralization(request: ExecuteRequest):
    if not os.path.exists(REGISTRY_DB_PATH):
        raise HTTPException(status_code=404, detail="Registry database not found. Scan and dedup first.")
        
    registry = PhotosRegistry(REGISTRY_DB_PATH)
    centralizer = Centralizer(
        registry, 
        request.dest, 
        takeout_zips_dir=request.takeout_zips_dir
    )
    
    stats = centralizer.execute(dry_run=not request.no_dry_run)
    registry.close()
    
    return {
        "status": "success",
        "stats": stats
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
