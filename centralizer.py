import os
import shutil
import zipfile
import hashlib
from metadata import write_exif_metadata, parse_date_string

class Centralizer:
    def __init__(self, registry, dest_dir, takeout_zips_dir=None):
        self.registry = registry
        self.conn = registry.conn
        self.dest_dir = dest_dir
        self.takeout_zips_dir = takeout_zips_dir or dest_dir

    def _get_target_path(self, record):
        """
        Determines the target path: YYYY/YYYY-MM/YYYYMMDD_HHMMSS_[sha256_prefix].ext
        """
        date_str = record.get('exif_date') or record.get('takeout_json_date')
        dt = parse_date_string(date_str)

        orig_ext = os.path.splitext(record['filename'])[1].lower()
        sha = record.get('sha256') or ''
        sha_prefix = sha[:8] if len(sha) >= 8 else 'unknown'

        if dt:
            year_dir = dt.strftime('%Y')
            month_dir = dt.strftime('%Y-%m')
            filename = f"{dt.strftime('%Y%m%d_%H%M%S')}_{sha_prefix}{orig_ext}"
        else:
            year_dir = 'unknown'
            month_dir = 'unknown'
            filename = f"unknown_{sha_prefix}_{record['filename']}"

        return os.path.join(self.dest_dir, year_dir, month_dir, filename)

    def _verify_sha256(self, file_path, expected_sha):
        """Verifies if the file matches the expected SHA-256."""
        if not expected_sha:
            return True
        sha256 = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    sha256.update(chunk)
            return sha256.hexdigest() == expected_sha
        except OSError:
            return False

    def execute(self, dry_run=True):
        """Iterates through all non-duplicate assets and copies them to the destination."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM files WHERE is_duplicate = 0")
        records = [dict(r) for r in cursor.fetchall()]

        stats = {
            'scanned_count': len(records),
            'copied_count': 0,
            'skipped_count': 0,
            'errors': 0
        }

        for record in records:
            source_type = record['source_type']
            
            # 1. Resolve source file handle or directory
            src_abs_path = None
            zip_handle = None
            
            if source_type in ('macos_photos', 'amazon_photos'):
                src_abs_path = os.path.join(record['source_root'], record['relative_path'])
            elif source_type == 'google_takeout':
                if os.path.isabs(record['source_root']):
                    zip_path = record['source_root']
                else:
                    zip_path = os.path.join(self.takeout_zips_dir, record['source_root'])
                
                if os.path.exists(zip_path):
                    src_abs_path = zip_path
                    zip_handle = record['relative_path']
            
            if not src_abs_path or (not zip_handle and not os.path.exists(src_abs_path)):
                stats['skipped_count'] += 1
                continue

            # 2. Determine target path
            target_path = self._get_target_path(record)
            
            if dry_run:
                stats['copied_count'] += 1
                continue

            # 3. Create target directory
            target_dir = os.path.dirname(target_path)
            os.makedirs(target_dir, exist_ok=True)

            # Avoid re-copying if the finalized file already exists
            if os.path.exists(target_path) and os.path.getsize(target_path) == record['file_size']:
                stats['skipped_count'] += 1
                continue

            temp_path = target_path + '.tmp'
            copy_success = False

            try:
                # 4. Perform atomic copy/extraction
                if zip_handle:
                    # Extract from zip container
                    with zipfile.ZipFile(src_abs_path, 'r') as zf:
                        with zf.open(zip_handle) as sf, open(temp_path, 'wb') as df:
                            shutil.copyfileobj(sf, df)
                    copy_success = True
                else:
                    # Direct file copy
                    shutil.copy2(src_abs_path, temp_path)
                    copy_success = True
                
                # 5. Verify size and hash safety
                if copy_success and os.path.exists(temp_path):
                    size_ok = os.path.getsize(temp_path) == record['file_size']
                    hash_ok = self._verify_sha256(temp_path, record.get('sha256'))
                    
                    if size_ok and hash_ok:
                        # Atomic rename
                        os.rename(temp_path, target_path)
                        
                        # 6. Apply metadata & timestamps (utime/touch)
                        date_str = record.get('exif_date') or record.get('takeout_json_date')
                        if date_str:
                            write_exif_metadata(
                                target_path, 
                                date_str, 
                                record.get('latitude'), 
                                record.get('longitude')
                            )
                        
                        stats['copied_count'] += 1
                    else:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                        stats['errors'] += 1
            except Exception as e:
                print(f"Failed to copy file {record['filename']}: {e}")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                stats['errors'] += 1

        return stats
