import sqlite3
import re


class Deduplicator:
    def __init__(self, registry):
        self.registry = registry
        self.conn = registry.conn

    def _get_source_priority(self, source_type):
        """Returns priority score (lower is higher priority)."""
        priorities = {
            'macos_photos': 1,
            'amazon_photos': 2,
            'google_takeout': 3
        }
        return priorities.get(source_type.lower(), 4)

    def _get_metadata_score(self, row):
        """Count the number of rich metadata fields that are non-null."""
        score = 0
        fields = ['exif_date', 'latitude', 'longitude', 'camera_make', 'camera_model']
        for f in fields:
            if row.get(f) is not None and row.get(f) != '':
                score += 1
        return score

    def _select_best_candidate(self, rows):
        """
        Sorts the duplicate candidates and returns the best one.
        Priority:
        1. Larger file size (prefer higher resolution/quality)
        2. More metadata fields populated
        3. Source priority (macOS Photos > Amazon Photos > Google Takeout)
        """
        def sort_key(row):
            size = row.get('file_size') or 0
            meta_score = self._get_metadata_score(row)
            source_prio = self._get_source_priority(row.get('source_type'))
            # Sorting: Descending size, Descending meta_score, Ascending source_prio
            return (-size, -meta_score, source_prio)

        sorted_rows = sorted(rows, key=sort_key)
        return sorted_rows[0]

    def process_duplicates(self):
        """Perform exact SHA-256 grouping and near-duplicate resolution."""
        cursor = self.conn.cursor()

        # Step 1: Reset duplicate state for a fresh run
        cursor.execute("UPDATE files SET best_candidate_id = NULL, is_duplicate = 0")
        self.conn.commit()

        # Step 2: Exact duplicates (matching SHA-256)
        cursor.execute("""
            SELECT sha256 FROM files 
            WHERE sha256 IS NOT NULL AND sha256 != '' 
            GROUP BY sha256 
            HAVING count(*) > 1
        """)
        exact_hashes = [row[0] for row in cursor.fetchall()]

        for sha in exact_hashes:
            cursor.execute("SELECT * FROM files WHERE sha256 = ?", (sha,))
            dup_rows = [dict(r) for r in cursor.fetchall()]
            
            best_row = self._select_best_candidate(dup_rows)
            best_id = best_row['file_id']
            
            for row in dup_rows:
                fid = row['file_id']
                if fid == best_id:
                    cursor.execute(
                        "UPDATE files SET best_candidate_id = ?, is_duplicate = 0 WHERE file_id = ?",
                        (best_id, fid)
                    )
                else:
                    cursor.execute(
                        "UPDATE files SET best_candidate_id = ?, is_duplicate = 1 WHERE file_id = ?",
                        (best_id, fid)
                    )
        self.conn.commit()

        # Step 3: Near duplicates (matching filename and timestamp date)
        # Only group files that are not already marked as duplicates of an exact match
        cursor.execute("""
            SELECT file_id, filename, COALESCE(exif_date, takeout_json_date) as photo_date,
                   file_size, source_type, exif_date, takeout_json_date,
                   latitude, longitude, camera_make, camera_model
            FROM files 
            WHERE is_duplicate = 0
        """)
        candidates = [dict(r) for r in cursor.fetchall()]

        # Group in python
        amazon_date_pattern = re.compile(r'\s*\(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}\.\d{3}\)')
        groups = {}
        for r in candidates:
            filename = r.get('filename')
            photo_date = r.get('photo_date')
            
            if filename and photo_date:
                # Strip Amazon Photos appended date patterns for deduplication matching
                norm_filename = amazon_date_pattern.sub('', filename)
                # Normalise timestamp by removing any timezone offsets if they exist (standard format check)
                norm_date = photo_date.split('+')[0].strip()
                group_key = (norm_filename.lower(), norm_date)
                groups.setdefault(group_key, []).append(r)

        # Process each near duplicate group
        for key, group_rows in groups.items():
            if len(group_rows) > 1:
                best_row = self._select_best_candidate(group_rows)
                best_id = best_row['file_id']
                
                for row in group_rows:
                    fid = row['file_id']
                    if fid == best_id:
                        cursor.execute(
                            "UPDATE files SET best_candidate_id = ?, is_duplicate = 0 WHERE file_id = ?",
                            (best_id, fid)
                        )
                    else:
                        cursor.execute(
                            "UPDATE files SET best_candidate_id = ?, is_duplicate = 1 WHERE file_id = ?",
                            (best_id, fid)
                        )
        self.conn.commit()

        # Step 4: For all remaining unique files, point best_candidate_id to themselves
        cursor.execute("UPDATE files SET best_candidate_id = file_id WHERE best_candidate_id IS NULL")
        self.conn.commit()
