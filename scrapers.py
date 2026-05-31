import os
import sqlite3
import datetime
import hashlib
import zipfile
import json
import re

# Standard media extensions to process
MEDIA_EXTENSIONS = {
    # Photos
    '.jpg', '.jpeg', '.png', '.heic', '.heif', '.tiff', '.tif', '.gif', '.webp', '.bmp',
    # Videos
    '.mp4', '.mov', '.m4v', '.avi', '.mkv', '.3gp'
}

def get_file_sha256(file_path):
    """Calculate SHA-256 hash of a file incrementally."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            sha256.update(chunk)
    return sha256.hexdigest()

def mac_to_utc_timestamp(mac_time):
    """Convert Mac absolute time (seconds since 2001-01-01) to standard UTC string."""
    if mac_time is None:
        return None
    # 978307200 is the difference in seconds between Unix epoch (1970) and Mac epoch (2001)
    unix_time = mac_time + 978307200
    try:
        dt = datetime.datetime.fromtimestamp(unix_time, datetime.timezone.utc)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OverflowError):
        return None

def unix_to_utc_timestamp(unix_timestamp):
    """Convert Unix timestamp (seconds since 1970) to standard UTC string."""
    if unix_timestamp is None:
        return None
    try:
        dt = datetime.datetime.fromtimestamp(int(unix_timestamp), datetime.timezone.utc)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OverflowError):
        return None

class macOSPhotosScraper:
    def __init__(self, library_path):
        self.library_path = library_path
        self.db_path = os.path.join(library_path, 'database', 'Photos.sqlite')
        self.originals_dir = os.path.join(library_path, 'originals')

    def scan_metadata(self):
        """Query Photos.sqlite and yields metadata records matching files on disk."""
        if not os.path.exists(self.db_path):
            return

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Build join query to extract asset UUID, directory, filename, original name, date, camera
        query = """
        SELECT 
            a.ZUUID as uuid,
            a.ZDIRECTORY as directory,
            a.ZFILENAME as filename,
            add_attr.ZORIGINALFILENAME as original_filename,
            a.ZDATECREATED as date_created,
            a.ZLATITUDE as latitude,
            a.ZLONGITUDE as longitude,
            ext_attr.ZCAMERAMAKE as camera_make,
            ext_attr.ZCAMERAMODEL as camera_model
        FROM ZASSET a
        LEFT JOIN ZADDITIONALASSETATTRIBUTES add_attr ON a.ZADDITIONALATTRIBUTES = add_attr.Z_PK
        LEFT JOIN ZEXTENDEDATTRIBUTES ext_attr ON a.ZEXTENDEDATTRIBUTES = ext_attr.Z_PK
        WHERE a.ZTRASHEDSTATE = 0
        """

        try:
            cursor.execute(query)
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            # Handle fallback schemas if attributes have different names
            print(f"Database query error: {e}. Falling back to basic ZASSET query.")
            cursor.execute("SELECT ZUUID as uuid, ZDIRECTORY as directory, ZFILENAME as filename, ZDATECREATED as date_created, ZLATITUDE as latitude, ZLONGITUDE as longitude FROM ZASSET WHERE ZTRASHEDSTATE = 0")
            rows = cursor.fetchall()

        for row in rows:
            r_dict = dict(row)
            uuid = r_dict.get('uuid')
            directory = r_dict.get('directory') or (uuid[0] if uuid else None)
            filename = r_dict.get('filename')
            
            if not uuid or not filename:
                continue

            # In macOS Photos, original files are structured as originals/dir/filename
            # where dir is either ZDIRECTORY or first character of ZUUID
            rel_path = os.path.join('originals', directory, filename)
            abs_path = os.path.join(self.library_path, rel_path)

            if os.path.exists(abs_path):
                file_size = os.path.getsize(abs_path)
                exif_date = mac_to_utc_timestamp(r_dict.get('date_created'))
                
                # Fetch original filename, fallback to current filename
                orig_filename = r_dict.get('original_filename') or filename
                
                yield {
                    'uuid': uuid,
                    'filename': orig_filename,
                    'file_size': file_size,
                    'relative_path': rel_path,
                    'exif_date': exif_date,
                    'latitude': r_dict.get('latitude'),
                    'longitude': r_dict.get('longitude'),
                    'camera_make': r_dict.get('camera_make'),
                    'camera_model': r_dict.get('camera_model')
                }

        conn.close()


class AmazonPhotosScraper:
    def __init__(self, base_path):
        self.base_path = base_path

    def scan_files(self):
        """Walk the directory structure recursively and yield basic media file stats."""
        for root, dirs, files in os.walk(self.base_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in MEDIA_EXTENSIONS:
                    abs_path = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_path, self.base_path)
                    
                    try:
                        file_size = os.path.getsize(abs_path)
                        # Fallback creation date from filesystem mtime
                        mtime = os.path.getmtime(abs_path)
                        fs_date = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    except OSError:
                        continue

                    # Yield record. EXIF and hashing are run during subsequent pipeline processes
                    yield {
                        'filename': file,
                        'relative_path': rel_path,
                        'file_size': file_size,
                        'exif_date': fs_date, # Base fallback
                        'latitude': None,
                        'longitude': None,
                        'camera_make': None,
                        'camera_model': None
                    }


class GoogleTakeoutScraper:
    def __init__(self, takeout_path):
        self.takeout_path = takeout_path
        self.json_metadata = {}
        self._pre_index_jsons()

    def _pre_index_jsons(self):
        """Locates all zip archives and builds a global mapping of lowercase json paths to parsed dicts."""
        if not os.path.exists(self.takeout_path):
            return

        zip_files = []
        if os.path.isdir(self.takeout_path):
            for file in os.listdir(self.takeout_path):
                if file.lower().endswith('.zip'):
                    zip_files.append(os.path.join(self.takeout_path, file))
        elif self.takeout_path.lower().endswith('.zip'):
            zip_files.append(self.takeout_path)

        for zip_path in sorted(zip_files):
            try:
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    for name in zf.namelist():
                        if name.endswith('/'):
                            continue
                        if name.lower().endswith('.json'):
                            try:
                                with zf.open(name) as jf:
                                    data = json.loads(jf.read().decode('utf-8', errors='ignore'))
                                    if 'photoTakenTime' in data or 'geoData' in data or 'geoDataExif' in data:
                                        self.json_metadata[name.lower()] = data
                            except Exception:
                                pass
            except (zipfile.BadZipFile, OSError):
                continue

    def _get_json_candidates(self, name):
        """Generates all possible JSON metadata file path candidates for a media file."""
        dir_name = os.path.dirname(name)
        base_name = os.path.basename(name)
        
        suffixes = ['.json', '.supplemental-metadata.json']
        candidates = []
        
        for ext_orig in [base_name, os.path.splitext(base_name)[0]]:
            for suffix in suffixes:
                full_name = ext_orig + suffix
                if len(full_name) > 51:
                    truncated_name = full_name[:-5][:46] + '.json'
                    candidates.append(os.path.join(dir_name, truncated_name))
                candidates.append(os.path.join(dir_name, full_name))
                
        seen = set()
        result = []
        for c in candidates:
            c_low = c.lower()
            if c_low not in seen:
                seen.add(c_low)
                result.append(c_low)
        return result

    def scan_zip_files(self):
        """Locates all zip archives in the Takeout path and parses media/metadata on-the-fly."""
        if not os.path.exists(self.takeout_path):
            return

        zip_files = []
        if os.path.isdir(self.takeout_path):
            for file in os.listdir(self.takeout_path):
                if file.lower().endswith('.zip'):
                    zip_files.append(os.path.join(self.takeout_path, file))
        elif self.takeout_path.lower().endswith('.zip'):
            zip_files.append(self.takeout_path)

        for zip_path in sorted(zip_files):
            zip_filename = os.path.basename(zip_path)
            
            try:
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    namelist = zf.namelist()
                    
                    for name in namelist:
                        # Skip directory entries
                        if name.endswith('/'):
                            continue
                            
                        ext = os.path.splitext(name)[1].lower()
                        if ext in MEDIA_EXTENSIONS:
                            info = zf.getinfo(name)
                            file_size = info.file_size
                            filename = os.path.basename(name)
                            
                            # Lookup corresponding JSON metadata from candidates list
                            metadata = {}
                            candidates = self._get_json_candidates(name)
                            for c in candidates:
                                if c in self.json_metadata:
                                    metadata = self.json_metadata[c]
                                    break

                            # Parse Takeout JSON metadata fields
                            takeout_json_date = None
                            latitude = None
                            longitude = None
                            
                            if metadata:
                                # Extract photo taken time
                                photo_time_data = metadata.get('photoTakenTime')
                                if photo_time_data and 'timestamp' in photo_time_data:
                                    takeout_json_date = unix_to_utc_timestamp(photo_time_data['timestamp'])
                                
                                # Extract location data
                                geo = metadata.get('geoData') or metadata.get('geoDataExif')
                                if geo:
                                    latitude = geo.get('latitude')
                                    longitude = geo.get('longitude')
                                    if latitude == 0.0 and longitude == 0.0:
                                        latitude = None
                                        longitude = None

                            yield {
                                'filename': filename,
                                'source_root': zip_path,
                                'relative_path': name,
                                'file_size': file_size,
                                'takeout_json_date': takeout_json_date,
                                'latitude': latitude,
                                'longitude': longitude,
                                'camera_make': None,
                                'camera_model': None
                            }
            except (zipfile.BadZipFile, OSError) as e:
                print(f"Skipping bad zip file {zip_filename}: {e}")
                continue

