import unittest
import sqlite3
import tempfile
import os
import sys
import zipfile
import json
import shutil

# Ensure workspace is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scrapers import macOSPhotosScraper, AmazonPhotosScraper, GoogleTakeoutScraper

class TestMacOSPhotosScraper(unittest.TestCase):
    def setUp(self):
        # Create a mock Photos library package directory structure
        self.temp_dir = tempfile.mkdtemp()
        self.db_dir = os.path.join(self.temp_dir, 'database')
        os.makedirs(self.db_dir)
        self.db_path = os.path.join(self.db_dir, 'Photos.sqlite')
        
        # Initialize mock database schema
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()
        
        cursor.execute("""
        CREATE TABLE ZASSET (
            Z_PK INTEGER PRIMARY KEY,
            ZUUID VARCHAR,
            ZDIRECTORY VARCHAR,
            ZFILENAME VARCHAR,
            ZDATECREATED TIMESTAMP,
            ZLATITUDE FLOAT,
            ZLONGITUDE FLOAT,
            ZTRASHEDSTATE INTEGER,
            ZADDITIONALATTRIBUTES INTEGER,
            ZEXTENDEDATTRIBUTES INTEGER
        )
        """)
        
        cursor.execute("""
        CREATE TABLE ZADDITIONALASSETATTRIBUTES (
            Z_PK INTEGER PRIMARY KEY,
            ZORIGINALFILENAME VARCHAR
        )
        """)
        
        cursor.execute("""
        CREATE TABLE ZEXTENDEDATTRIBUTES (
            Z_PK INTEGER PRIMARY KEY,
            ZCAMERAMAKE VARCHAR,
            ZCAMERAMODEL VARCHAR
        )
        """)
        
        # Insert sample data (taken on 2011-04-05 17:24:02 UTC = 323717042 Mac time)
        cursor.execute("""
        INSERT INTO ZASSET 
        (Z_PK, ZUUID, ZDIRECTORY, ZFILENAME, ZDATECREATED, ZLATITUDE, ZLONGITUDE, ZTRASHEDSTATE, ZADDITIONALATTRIBUTES, ZEXTENDEDATTRIBUTES)
        VALUES (1, 'UUID1234', 'A', 'UUID1234.jpeg', 323717042, 28.3691, -81.5463, 0, 1, 1)
        """)
        
        cursor.execute("""
        INSERT INTO ZADDITIONALASSETATTRIBUTES (Z_PK, ZORIGINALFILENAME) VALUES (1, 'IMG_0186.JPG')
        """)
        
        cursor.execute("""
        INSERT INTO ZEXTENDEDATTRIBUTES (Z_PK, ZCAMERAMAKE, ZCAMERAMODEL) VALUES (1, 'Apple', 'iPhone 4')
        """)
        
        self.conn.commit()
        self.conn.close()

        # Create the originals folder hierarchy
        self.originals_dir = os.path.join(self.temp_dir, 'originals')
        self.sub_dir = os.path.join(self.originals_dir, 'A')
        os.makedirs(self.sub_dir)
        self.file_path = os.path.join(self.sub_dir, 'UUID1234.jpeg')
        
        # Create dummy JPEG file
        with open(self.file_path, 'wb') as f:
            f.write(b'\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xDB\x00C\x00\x08\x06\x06' + b'a' * 100)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_scrape_metadata(self):
        scraper = macOSPhotosScraper(self.temp_dir)
        assets = list(scraper.scan_metadata())
        self.assertEqual(len(assets), 1)
        
        asset = assets[0]
        self.assertEqual(asset['uuid'], 'UUID1234')
        self.assertEqual(asset['filename'], 'IMG_0186.JPG')
        self.assertEqual(asset['file_size'], os.path.getsize(self.file_path))
        self.assertEqual(asset['exif_date'], '2011-04-05 17:24:02')
        self.assertEqual(asset['latitude'], 28.3691)
        self.assertEqual(asset['longitude'], -81.5463)
        self.assertEqual(asset['camera_make'], 'Apple')
        self.assertEqual(asset['camera_model'], 'iPhone 4')
        self.assertTrue(asset['relative_path'].endswith('originals/A/UUID1234.jpeg'))


class TestAmazonPhotosScraper(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sub_dir = os.path.join(self.temp_dir, 'Amazon Photos Downloads', 'Photos', 'Pictures')
        os.makedirs(self.sub_dir)
        self.file_path = os.path.join(self.sub_dir, 'DCP_0099.JPG')
        
        # Write mock photo
        with open(self.file_path, 'wb') as f:
            f.write(b'\xFF\xD8\xFF\xE0\x00\x10JFIF\x00' + b'dummy jpeg body' * 10)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_scan_files(self):
        scraper = AmazonPhotosScraper(self.temp_dir)
        files = list(scraper.scan_files())
        self.assertEqual(len(files), 1)
        
        f = files[0]
        self.assertEqual(f['filename'], 'DCP_0099.JPG')
        self.assertTrue(f['relative_path'].endswith('DCP_0099.JPG'))
        self.assertEqual(f['file_size'], len(b'\xFF\xD8\xFF\xE0\x00\x10JFIF\x00' + b'dummy jpeg body' * 10))


class TestGoogleTakeoutScraper(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.zip_path = os.path.join(self.temp_dir, 'takeout-20260526T010422Z-3-001.zip')
        
        # Mock media and sidecar json metadata
        self.media_name = 'Takeout/Google Photos/Photos from 2021/IMG_5678.JPG'
        self.json_name = 'Takeout/Google Photos/Photos from 2021/IMG_5678.JPG.json'
        
        self.metadata = {
            "title": "IMG_5678.JPG",
            "photoTakenTime": {
                "timestamp": "1622376000", # 2021-05-30 12:00:00 UTC
                "formatted": "May 30, 2021, 12:00:00 PM UTC"
            },
            "geoData": {
                "latitude": 37.7749,
                "longitude": -122.4194,
                "altitude": 0.0
            }
        }
        
        with zipfile.ZipFile(self.zip_path, 'w') as zf:
            zf.writestr(self.media_name, b'\xFF\xD8\xFF\xE0\x00\x10JFIF\x00' + b'google photo body' * 10)
            zf.writestr(self.json_name, json.dumps(self.metadata))

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_scan_zip(self):
        scraper = GoogleTakeoutScraper(self.temp_dir)
        files = list(scraper.scan_zip_files())
        self.assertEqual(len(files), 1)
        
        f = files[0]
        self.assertEqual(f['filename'], 'IMG_5678.JPG')
        self.assertEqual(f['source_root'], self.zip_path)
        self.assertEqual(f['relative_path'], self.media_name)
        self.assertEqual(f['takeout_json_date'], '2021-05-30 12:00:00')
        self.assertEqual(f['latitude'], 37.7749)
        self.assertEqual(f['longitude'], -122.4194)
        
if __name__ == '__main__':
    unittest.main()
