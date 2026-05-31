import unittest
import sqlite3
import tempfile
import os
import sys

# Ensure workspace is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from db import PhotosRegistry

class TestPhotosRegistry(unittest.TestCase):
    def setUp(self):
        # Create a temporary file for the database
        self.db_fd, self.db_path = tempfile.mkstemp()
        self.registry = PhotosRegistry(self.db_path)

    def tearDown(self):
        # Close the connection and clean up the file
        self.registry.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_schema_initialization(self):
        # Verify tables exist
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check pipeline_runs
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_runs'")
        self.assertIsNotNone(cursor.fetchone())
        
        # Check files
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
        self.assertIsNotNone(cursor.fetchone())
        
        conn.close()

    def test_create_run(self):
        run_id = self.registry.create_run()
        self.assertEqual(run_id, 1)
        
        # Verify run in DB
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM pipeline_runs WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        self.assertEqual(row[0], 'running')
        conn.close()

    def test_complete_run(self):
        run_id = self.registry.create_run()
        self.registry.complete_run(run_id, 'completed')
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT status, end_time FROM pipeline_runs WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        self.assertEqual(row[0], 'completed')
        self.assertIsNotNone(row[1])
        conn.close()

    def test_add_file(self):
        run_id = self.registry.create_run()
        file_id = self.registry.add_file(
            run_id=run_id,
            source_type='amazon_photos',
            source_root='/Volumes/T9_2T/Amazon Photos',
            relative_path='2021/IMG_0001.JPG',
            filename='IMG_0001.JPG',
            file_size=102400,
            sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            exif_date='2021-05-30 12:00:00',
            latitude=37.7749,
            longitude=-122.4194
        )
        self.assertEqual(file_id, 1)
        
        # Retrieve the file
        file_record = self.registry.get_file(file_id)
        self.assertEqual(file_record['filename'], 'IMG_0001.JPG')
        self.assertEqual(file_record['file_size'], 102400)
        self.assertEqual(file_record['sha256'], 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
        self.assertEqual(file_record['exif_date'], '2021-05-30 12:00:00')
        self.assertEqual(file_record['latitude'], 37.7749)
        self.assertEqual(file_record['longitude'], -122.4194)

if __name__ == '__main__':
    unittest.main()
