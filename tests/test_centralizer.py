import unittest
import tempfile
import os
import sys
import zipfile
import shutil
import datetime
import hashlib

# Ensure workspace is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from db import PhotosRegistry
from centralizer import Centralizer

class TestCentralizer(unittest.TestCase):
    def setUp(self):
        # Create temp folder representing the external drive '/Volumes/T9_2T'
        self.temp_drive = tempfile.mkdtemp()
        
        # Paths representing sources
        self.amazon_dir = os.path.join(self.temp_drive, 'Amazon Photos')
        os.makedirs(self.amazon_dir)
        
        self.takeout_dir = os.path.join(self.temp_drive, 'Google Takeout')
        os.makedirs(self.takeout_dir)
        
        self.dest_dir = os.path.join(self.temp_drive, 'Centralized Photos')
        
        # Create DB
        self.db_path = os.path.join(self.temp_drive, 'photos_registry.db')
        self.registry = PhotosRegistry(self.db_path)
        self.run_id = self.registry.create_run()

        # Write mock Amazon source file
        self.amazon_file_rel = '2019/IMG_100.jpg'
        self.amazon_file_abs = os.path.join(self.amazon_dir, self.amazon_file_rel)
        os.makedirs(os.path.dirname(self.amazon_file_abs), exist_ok=True)
        
        self.dummy_data_amazon = b'\xFF\xD8\xFF\xE0\x00\x10JFIF\x00' + b'amazon photo data' * 10
        with open(self.amazon_file_abs, 'wb') as f:
            f.write(self.dummy_data_amazon)
            
        # Dynamically compute hash
        self.amazon_sha = hashlib.sha256(self.dummy_data_amazon).hexdigest()
            
        # Register Amazon File in DB (canonical best candidate)
        self.fid_amazon = self.registry.add_file(
            run_id=self.run_id,
            source_type='amazon_photos',
            source_root=self.amazon_dir,
            relative_path=self.amazon_file_rel,
            filename='IMG_100.jpg',
            file_size=len(self.dummy_data_amazon),
            sha256=self.amazon_sha,
            exif_date='2019-10-15 15:30:00'
        )
        # Update pointer
        cursor = self.registry.conn.cursor()
        cursor.execute("UPDATE files SET best_candidate_id = ?, is_duplicate = 0 WHERE file_id = ?", (self.fid_amazon, self.fid_amazon))
        self.registry.conn.commit()

        # Write mock Google Takeout zip file
        self.zip_filename = 'takeout-001.zip'
        self.zip_path = os.path.join(self.takeout_dir, self.zip_filename)
        self.takeout_file_rel = 'Takeout/Photos from 2021/IMG_200.jpg'
        self.dummy_data_takeout = b'\xFF\xD8\xFF\xE0\x00\x10JFIF\x00' + b'takeout photo data' * 10
        
        with zipfile.ZipFile(self.zip_path, 'w') as zf:
            zf.writestr(self.takeout_file_rel, self.dummy_data_takeout)
            
        # Dynamically compute hash
        self.takeout_sha = hashlib.sha256(self.dummy_data_takeout).hexdigest()
            
        # Register Google Takeout File in DB (canonical best candidate)
        self.fid_takeout = self.registry.add_file(
            run_id=self.run_id,
            source_type='google_takeout',
            source_root=self.zip_filename,
            relative_path=self.takeout_file_rel,
            filename='IMG_200.jpg',
            file_size=len(self.dummy_data_takeout),
            sha256=self.takeout_sha,
            takeout_json_date='2021-05-30 12:00:00'
        )
        cursor.execute("UPDATE files SET best_candidate_id = ?, is_duplicate = 0 WHERE file_id = ?", (self.fid_takeout, self.fid_takeout))
        self.registry.conn.commit()

        # Register duplicate file (should NOT be copied)
        self.fid_dup = self.registry.add_file(
            run_id=self.run_id,
            source_type='google_takeout',
            source_root=self.zip_filename,
            relative_path='Takeout/Photos from 2021/IMG_100_copy.jpg',
            filename='IMG_100_copy.jpg',
            file_size=len(self.dummy_data_amazon),
            sha256=self.amazon_sha, # Duplicate hash of Amazon file
            takeout_json_date='2019-10-15 15:30:00'
        )
        cursor.execute("UPDATE files SET best_candidate_id = ?, is_duplicate = 1 WHERE file_id = ?", (self.fid_amazon, self.fid_dup))
        self.registry.conn.commit()

        # Initialize Centralizer
        self.centralizer = Centralizer(self.registry, self.dest_dir, takeout_zips_dir=self.takeout_dir)

    def tearDown(self):
        self.registry.close()
        shutil.rmtree(self.temp_drive)

    def test_centralize_copied_files(self):
        # Run centralization copy operation
        stats = self.centralizer.execute(dry_run=False)
        self.assertEqual(stats['copied_count'], 2)
        
        # Check Amazon file copy: Target YYYY/YYYY-MM/YYYYMMDD_HHMMSS_[hash].ext
        amazon_prefix = self.amazon_sha[:8]
        expected_amazon_path = os.path.join(self.dest_dir, '2019', '2019-10', f'20191015_153000_{amazon_prefix}.jpg')
        self.assertTrue(os.path.exists(expected_amazon_path))
        
        with open(expected_amazon_path, 'rb') as f:
            self.assertEqual(f.read(), self.dummy_data_amazon)

        # Check Takeout file extracted from zip copy
        takeout_prefix = self.takeout_sha[:8]
        expected_takeout_path = os.path.join(self.dest_dir, '2021', '2021-05', f'20210530_120000_{takeout_prefix}.jpg')
        self.assertTrue(os.path.exists(expected_takeout_path))
        
        with open(expected_takeout_path, 'rb') as f:
            self.assertEqual(f.read(), self.dummy_data_takeout)
            
        # Verify filesystem modification time is applied
        mtime = os.path.getmtime(expected_takeout_path)
        dt = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc)
        self.assertEqual(dt.strftime('%Y-%m-%d %H:%M:%S'), '2021-05-30 12:00:00')

if __name__ == '__main__':
    unittest.main()
