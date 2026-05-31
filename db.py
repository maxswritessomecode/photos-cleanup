import sqlite3
import os

class PhotosRegistry:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None
        self.connect()
        self.initialize_schema()

    def connect(self):
        # Ensure parent directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            
        self.conn = sqlite3.connect(self.db_path)
        # Enable returning dictionary-like rows for ease of use
        self.conn.row_factory = sqlite3.Row
        
        # Concurrency enhancements for multi-threaded access (WAL mode)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA busy_timeout=30000;")

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def initialize_schema(self):
        cursor = self.conn.cursor()
        
        # Create pipeline_runs table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_time TIMESTAMP,
            status TEXT NOT NULL
        )
        """)
        
        # Create files table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            source_type TEXT NOT NULL,
            source_root TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT,
            mime_type TEXT,
            exif_date TIMESTAMP,
            takeout_json_date TIMESTAMP,
            latitude REAL,
            longitude REAL,
            camera_make TEXT,
            camera_model TEXT,
            best_candidate_id INTEGER,
            is_duplicate INTEGER DEFAULT 0,
            FOREIGN KEY(run_id) REFERENCES pipeline_runs(run_id)
        )
        """)
        
        # Indexes for fast querying
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_date_name ON files(exif_date, filename)")
        
        self.conn.commit()

    def create_run(self):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO pipeline_runs (status) VALUES (?)",
            ('running',)
        )
        self.conn.commit()
        return cursor.lastrowid

    def complete_run(self, run_id, status='completed'):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE pipeline_runs SET status = ?, end_time = CURRENT_TIMESTAMP WHERE run_id = ?",
            (status, run_id)
        )
        self.conn.commit()

    def add_file(self, run_id, source_type, source_root, relative_path, filename, file_size,
                 sha256=None, exif_date=None, takeout_json_date=None,
                 latitude=None, longitude=None, camera_make=None, camera_model=None):
        cursor = self.conn.cursor()
        cursor.execute("""
        INSERT INTO files (
            run_id, source_type, source_root, relative_path, filename, file_size,
            sha256, exif_date, takeout_json_date, latitude, longitude, camera_make, camera_model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id, source_type, source_root, relative_path, filename, file_size,
            sha256, exif_date, takeout_json_date, latitude, longitude, camera_make, camera_model
        ))
        return cursor.lastrowid

    def get_file(self, file_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM files WHERE file_id = ?", (file_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
