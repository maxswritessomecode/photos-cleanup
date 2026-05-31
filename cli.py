import argparse
import sys
import os
import hashlib
from db import PhotosRegistry
from scrapers import macOSPhotosScraper, AmazonPhotosScraper, GoogleTakeoutScraper, get_file_sha256
from dedup import Deduplicator
from centralizer import Centralizer

# ANSI Colors for premium visual formatting
COLOR_GREEN = "\033[92m"
COLOR_BLUE = "\033[94m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_CYAN = "\033[96m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"

def print_header(title):
    print(f"\n{COLOR_BOLD}{COLOR_BLUE}=== {title} ==={COLOR_RESET}\n")

def print_success(message):
    print(f"{COLOR_GREEN}✔ {message}{COLOR_RESET}")

def print_info(message):
    print(f"{COLOR_CYAN}ℹ {message}{COLOR_RESET}")

def print_warning(message):
    print(f"{COLOR_YELLOW}⚠ {message}{COLOR_RESET}")

def print_error(message):
    print(f"{COLOR_RED}✘ {message}{COLOR_RESET}")

def format_bytes(bytes_num):
    """Format bytes as a human-readable string."""
    if bytes_num is None:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.2f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.2f} PB"

def cmd_scan(args):
    print_header("Step 1: Inventory & Scanning Sources")
    
    registry = PhotosRegistry(args.db)
    run_id = registry.create_run()
    print_info(f"Initialized Pipeline Run ID: {run_id}")
    
    # 1. macOS Photos
    if args.macos_dir:
        print_info(f"Scanning macOS Photos Library at '{args.macos_dir}'...")
        scraper = macOSPhotosScraper(args.macos_dir)
        count = 0
        for item in scraper.scan_metadata():
            # Compute SHA-256 for macOS original file on scan
            abs_path = os.path.join(args.macos_dir, item['relative_path'])
            sha256 = get_file_sha256(abs_path) if os.path.exists(abs_path) else None
            
            registry.add_file(
                run_id=run_id,
                source_type='macos_photos',
                source_root=args.macos_dir,
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
            count += 1
        print_success(f"Indexed {count} assets from macOS Photos Library.")

    # 2. Amazon Photos
    if args.amazon_dir:
        print_info(f"Scanning Amazon Photos at '{args.amazon_dir}'...")
        scraper = AmazonPhotosScraper(args.amazon_dir)
        count = 0
        for item in scraper.scan_files():
            # Compute SHA-256 on the fly for file indexing
            abs_path = os.path.join(args.amazon_dir, item['relative_path'])
            sha256 = get_file_sha256(abs_path) if os.path.exists(abs_path) else None
            
            # Walk EXIF details if pillow or basic library works, fallback to fs
            registry.add_file(
                run_id=run_id,
                source_type='amazon_photos',
                source_root=args.amazon_dir,
                relative_path=item['relative_path'],
                filename=item['filename'],
                file_size=item['file_size'],
                sha256=sha256,
                exif_date=item['exif_date']
            )
            count += 1
        print_success(f"Indexed {count} assets from Amazon Photos.")

    # 3. Google Takeout Zips
    if args.takeout_dir:
        print_info(f"Scanning Google Takeout Zip container files at '{args.takeout_dir}'...")
        scraper = GoogleTakeoutScraper(args.takeout_dir)
        count = 0
        for item in scraper.scan_zip_files():
            # Hashing and JSON mapping already done on the fly in GoogleTakeoutScraper!
            registry.add_file(
                run_id=run_id,
                source_type='google_takeout',
                source_root=item['source_root'],
                relative_path=item['relative_path'],
                filename=item['filename'],
                file_size=item['file_size'],
                sha256=None, # Computed later or dynamically handled
                takeout_json_date=item['takeout_json_date'],
                latitude=item['latitude'],
                longitude=item['longitude']
            )
            count += 1
        print_success(f"Indexed {count} zipped assets from Google Takeout.")

    registry.complete_run(run_id, 'completed')
    print_success(f"Scan complete. Data registered securely in '{args.db}'.")
    registry.close()

def cmd_dedup(args):
    print_header("Step 2: De-duplication Analysis & Planning")
    registry = PhotosRegistry(args.db)
    deduplicator = Deduplicator(registry)
    
    print_info("Analyzing database for exact and near-duplicates...")
    deduplicator.process_duplicates()
    
    # Compile statistics
    cursor = registry.conn.cursor()
    
    cursor.execute("SELECT count(*) FROM files")
    total_files = cursor.fetchone()[0]
    
    cursor.execute("SELECT count(*) FROM files WHERE is_duplicate = 1")
    dup_files = cursor.fetchone()[0]
    
    cursor.execute("SELECT sum(file_size) FROM files WHERE is_duplicate = 1")
    dup_bytes = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT count(*) FROM files WHERE is_duplicate = 0")
    unique_files = cursor.fetchone()[0]
    
    print_success("Deduplication matching logic complete.")
    print_info(f"Total Assets Found: {total_files}")
    print_info(f"Unique Canonical Assets: {unique_files}")
    print_info(f"Redundant Duplicate Assets: {dup_files}")
    print_info(f"Potential Storage Recoverable: {COLOR_GREEN}{format_bytes(dup_bytes)}{COLOR_CYAN}")
    
    registry.close()

def cmd_report(args):
    print_header("Database Status & Analysis Report")
    if not os.path.exists(args.db):
        print_error(f"Database not found at '{args.db}'. Run the 'scan' command first.")
        return
        
    registry = PhotosRegistry(args.db)
    cursor = registry.conn.cursor()
    
    cursor.execute("SELECT source_type, is_duplicate, count(*), sum(file_size) FROM files GROUP BY source_type, is_duplicate")
    rows = cursor.fetchall()
    
    print_info("Breakdown by Source System:")
    print(f"\n{COLOR_BOLD}{'Source Type':<20} | {'Status':<12} | {'Count':<8} | {'Storage size':<12}{COLOR_RESET}")
    print("-" * 60)
    for r in rows:
        status_str = "Duplicate" if r[1] == 1 else "Canonical"
        print(f"{r[0]:<20} | {status_str:<12} | {r[2]:<8} | {format_bytes(r[3]):<12}")
    print()
    registry.close()

def cmd_execute(args):
    print_header("Step 3: Copying & Centralization")
    if not os.path.exists(args.db):
        print_error(f"Database not found at '{args.db}'. Run 'scan' and 'dedup' first.")
        return

    registry = PhotosRegistry(args.db)
    
    if args.no_dry_run:
        print_warning("Executing direct centralization COPY operation on disk...")
    else:
        print_info("Executing DRY-RUN simulation. No files will be copied.")

    centralizer = Centralizer(
        registry, 
        args.dest, 
        takeout_zips_dir=args.takeout_zips_dir
    )
    
    stats = centralizer.execute(dry_run=not args.no_dry_run)
    
    print_success("Centralization task completed successfully.")
    print_info(f"Total Canonical Checked: {stats['scanned_count']}")
    print_info(f"Total Files Copied/Extracted: {stats['copied_count']}")
    print_info(f"Total Files Skipped (Existing): {stats['skipped_count']}")
    
    if stats['errors'] > 0:
        print_error(f"File Copy Errors Encountered: {stats['errors']}")
    else:
        print_success("Zero copy errors encountered.")
        
    registry.close()

def main():
    parser = argparse.ArgumentParser(
        description="Photos Deduplication and Centralization Pipeline",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Pipeline commands')
    
    # Scan command
    scan_parser = subparsers.add_parser('scan', help='Indexes photos and hashes files')
    scan_parser.add_argument('--macos-dir', type=str, help='Absolute path to Merged Library.photoslibrary')
    scan_parser.add_argument('--amazon-dir', type=str, help='Absolute path to Amazon Photos directory')
    scan_parser.add_argument('--takeout-dir', type=str, help='Absolute path to Google Takeout Zip container directory')
    scan_parser.add_argument('--db', type=str, default='/Volumes/T9_2T/Temp/photos_registry.db', help='Path to tracking SQLite database')
    
    # Dedup command
    dedup_parser = subparsers.add_parser('dedup', help='Resolve duplicates and select best copies')
    dedup_parser.add_argument('--db', type=str, default='/Volumes/T9_2T/Temp/photos_registry.db', help='Path to tracking SQLite database')
    
    # Report command
    report_parser = subparsers.add_parser('report', help='Display index and deduplication analysis reports')
    report_parser.add_argument('--db', type=str, default='/Volumes/T9_2T/Temp/photos_registry.db', help='Path to tracking SQLite database')
    
    # Execute command
    execute_parser = subparsers.add_parser('execute', help='Centralize unique photos and videos')
    execute_parser.add_argument('--dest', type=str, required=True, help='Absolute path to target centralization directory')
    execute_parser.add_argument('--takeout-zips-dir', type=str, help='Directory containing Google Takeout zip files (defaults to takeout-dir)')
    execute_parser.add_argument('--db', type=str, default='/Volumes/T9_2T/Temp/photos_registry.db', help='Path to tracking SQLite database')
    execute_parser.add_argument('--no-dry-run', action='store_true', help='Set this flag to perform actual copy/extraction')
    
    args = parser.parse_args()
    
    if args.command == 'scan':
        cmd_scan(args)
    elif args.command == 'dedup':
        cmd_dedup(args)
    elif args.command == 'report':
        cmd_report(args)
    elif args.command == 'execute':
        cmd_execute(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
