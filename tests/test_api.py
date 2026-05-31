import unittest
import os
import sys
import tempfile
import sqlite3
import json

# Ensure workspace is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from api import app, set_registry_db
from db import PhotosRegistry

class TestAPI(unittest.TestCase):
    def setUp(self):
        # Create a temporary database for the API server
        self.db_fd, self.db_path = tempfile.mkstemp()
        self.registry = PhotosRegistry(self.db_path)
        set_registry_db(self.db_path)
        
        self.client = TestClient(app)
        
        # Populate mock database records
        self.run_id = self.registry.create_run()
        self.fid1 = self.registry.add_file(
            run_id=self.run_id,
            source_type='macos_photos',
            source_root='/Volumes/T9_2T/Merged Library.photoslibrary',
            relative_path='originals/A/IMG_100.JPG',
            filename='IMG_100.JPG',
            file_size=100000,
            sha256='sha_100',
            exif_date='2021-05-30 12:00:00'
        )
        
        # Make it canonical
        cursor = self.registry.conn.cursor()
        cursor.execute("UPDATE files SET best_candidate_id = ?, is_duplicate = 0 WHERE file_id = ?", (self.fid1, self.fid1))
        self.registry.conn.commit()

        # Set up a temporary mock folder for scan testing
        self.temp_scan_dir = tempfile.mkdtemp()

    def tearDown(self):
        self.registry.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)
        import shutil
        shutil.rmtree(self.temp_scan_dir)

    def test_get_report(self):
        # Call GET /report
        response = self.client.get("/report")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['total_assets'], 1)
        self.assertEqual(data['unique_canonical'], 1)
        self.assertEqual(data['duplicates'], 0)

    def test_post_scan(self):
        # Call POST /scan with the mock temp folder
        payload = {
            "db_path": self.db_path,
            "macos_dir": self.temp_scan_dir, # Safe mock path
            "amazon_dir": None,
            "takeout_dir": None
        }
        response = self.client.post("/scan", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn("run_id", data)

    def test_post_dedup(self):
        # Call POST /dedup
        response = self.client.post("/dedup")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['unique_canonical'], 1)

    def test_post_execute(self):
        # Call POST /execute
        payload = {
            "dest": self.temp_scan_dir, # Safe mock path
            "takeout_zips_dir": None,
            "no_dry_run": False
        }
        response = self.client.post("/execute", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['stats']['scanned_count'], 1)

    def test_get_run_status(self):
        # Call GET /run/{run_id}
        response = self.client.get(f"/run/{self.run_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['run_id'], self.run_id)
        self.assertEqual(data['run_status'], 'running')

    def test_post_cancel_scan(self):
        # Call POST /scan/cancel/{run_id}
        response = self.client.post(f"/scan/cancel/{self.run_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        
        # Verify status is updated to cancelled
        status_resp = self.client.get(f"/run/{self.run_id}")
        self.assertEqual(status_resp.json()['run_status'], 'cancelled')

if __name__ == '__main__':
    unittest.main()
