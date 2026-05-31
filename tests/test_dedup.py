import unittest
import tempfile
import os
import sys

# Ensure workspace is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from db import PhotosRegistry
from dedup import Deduplicator

class TestDeduplicator(unittest.TestCase):
    def setUp(self):
        # Initialize database in temporary file
        self.db_fd, self.db_path = tempfile.mkstemp()
        self.registry = PhotosRegistry(self.db_path)
        self.deduplicator = Deduplicator(self.registry)
        self.run_id = self.registry.create_run()

    def tearDown(self):
        self.registry.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_exact_duplicates(self):
        # Insert exact duplicates (identical sha256)
        # File 1: Google Takeout (compressed/lower priority)
        f1 = self.registry.add_file(
            run_id=self.run_id,
            source_type='google_takeout',
            source_root='takeout-001.zip',
            relative_path='Takeout/IMG_0001.JPG',
            filename='IMG_0001.JPG',
            file_size=50000,
            sha256='hash_exact_123',
            exif_date='2021-05-30 12:00:00'
        )
        
        # File 2: macOS Photos (highest priority, larger metadata)
        f2 = self.registry.add_file(
            run_id=self.run_id,
            source_type='macos_photos',
            source_root='/Volumes/T9_2T/Merged Library.photoslibrary',
            relative_path='originals/A/UUID_IMG_0001.jpeg',
            filename='IMG_0001.JPG',
            file_size=100000, # Larger size
            sha256='hash_exact_123',
            exif_date='2021-05-30 12:00:00',
            latitude=37.7749,
            longitude=-122.4194,
            camera_make='Apple',
            camera_model='iPhone 12'
        )

        # Run deduplication
        self.deduplicator.process_duplicates()

        # Check results
        # File 2 should be canonical best copy since it has macOS Photos source, larger size and more complete metadata
        f1_record = self.registry.get_file(f1)
        f2_record = self.registry.get_file(f2)

        self.assertEqual(f1_record['is_duplicate'], 1)
        self.assertEqual(f1_record['best_candidate_id'], f2)
        
        self.assertEqual(f2_record['is_duplicate'], 0)
        self.assertEqual(f2_record['best_candidate_id'], f2)

    def test_near_duplicates(self):
        # Insert near duplicates (same date + name, differing hash/size)
        # File 1: Low quality copy from Google Photos Takeout
        f1 = self.registry.add_file(
            run_id=self.run_id,
            source_type='google_takeout',
            source_root='takeout-001.zip',
            relative_path='Takeout/IMG_9999.JPG',
            filename='IMG_9999.JPG',
            file_size=80000,
            sha256='hash_low_quality',
            exif_date='2019-10-15 15:30:00'
        )
        
        # File 2: High quality original from Amazon Photos
        f2 = self.registry.add_file(
            run_id=self.run_id,
            source_type='amazon_photos',
            source_root='/Volumes/T9_2T/Amazon Photos',
            relative_path='2019/IMG_9999.JPG',
            filename='IMG_9999.JPG',
            file_size=250000, # Much larger size
            sha256='hash_high_quality',
            exif_date='2019-10-15 15:30:00',
            latitude=40.7128,
            longitude=-74.0060,
            camera_make='Canon',
            camera_model='EOS R'
        )

        self.deduplicator.process_duplicates()

        f1_record = self.registry.get_file(f1)
        f2_record = self.registry.get_file(f2)

        self.assertEqual(f1_record['is_duplicate'], 1)
        self.assertEqual(f1_record['best_candidate_id'], f2)

        self.assertEqual(f2_record['is_duplicate'], 0)
        self.assertEqual(f2_record['best_candidate_id'], f2)

    def test_amazon_near_duplicates_normalization(self):
        # File 1: Original from macOS Photos
        f1 = self.registry.add_file(
            run_id=self.run_id,
            source_type='macos_photos',
            source_root='/Volumes/T9_2T/Photos Library.photoslibrary',
            relative_path='originals/A/UUID_IMG_2687.jpeg',
            filename='IMG_2687.JPG',
            file_size=120000,
            sha256='hash_mac',
            exif_date='2018-07-15 05:22:28'
        )
        
        # File 2: Amazon Photos copy with parenthesized timestamp appended
        f2 = self.registry.add_file(
            run_id=self.run_id,
            source_type='amazon_photos',
            source_root='/Volumes/T9_2T/Amazon Photos',
            relative_path='IMG_2687 (2018-07-15T05_22_28.426).JPG',
            filename='IMG_2687 (2018-07-15T05_22_28.426).JPG',
            file_size=100000,
            sha256='hash_amazon',
            exif_date='2018-07-15 05:22:28'
        )

        self.deduplicator.process_duplicates()

        f1_record = self.registry.get_file(f1)
        f2_record = self.registry.get_file(f2)

        # macOS Photos has higher priority, so f1 should be canonical, and f2 is the duplicate
        self.assertEqual(f1_record['is_duplicate'], 0)
        self.assertEqual(f2_record['is_duplicate'], 1)
        self.assertEqual(f2_record['best_candidate_id'], f1)

if __name__ == '__main__':
    unittest.main()
