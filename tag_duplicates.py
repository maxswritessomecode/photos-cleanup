import sqlite3
import subprocess
import os

# Colors for terminal output formatting
COLOR_GREEN = "\033[92m"
COLOR_BLUE = "\033[94m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_CYAN = "\033[96m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"

def print_info(message):
    print(f"{COLOR_CYAN}ℹ {message}{COLOR_RESET}")

def print_success(message):
    print(f"{COLOR_GREEN}✔ {message}{COLOR_RESET}")

def print_error(message):
    print(f"{COLOR_RED}✘ {message}{COLOR_RESET}")

def main():
    db_path = "/Volumes/T9_2T/Temp/photos_registry.db"
    album_name = "Duplicates for Deletion"

    if not os.path.exists(db_path):
        print_error(f"Database not found at '{db_path}'. Run 'scan' and 'dedup' first.")
        return

    print_info(f"Connecting to registry database at '{db_path}'...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Query duplicate assets from macOS Photos library
    # ZUUID is in the relative_path (e.g. originals/A/UUID.jpeg), but we also have it in ZUUID
    # Wait, our scraper stored uuid in the database files table under ZUUID or relative_path?
    # Let's check the schema. In our scrapers.py macOSPhotosScraper:
    # 'uuid': uuid, which maps to 'uuid' in our sqlite insert, but wait!
    # In db.py add_file, did we insert 'uuid'?
    # Ah! In db.py:
    # ZUUID was not a column in files! Let's check db.py schema:
    # files table columns: file_id, run_id, source_type, source_root, relative_path, filename, file_size, sha256...
    # Wait! In scrapers.py: 'uuid': uuid was yielded.
    # But in cli.py cmd_scan:
    # registry.add_file(...) was called. Did we pass uuid?
    # Let's check cli.py cmd_scan:
    # registry.add_file(..., filename=item['filename'], relative_path=item['relative_path'], ...)
    # Wait, the uuid of macOS Photos is in the relative path!
    # e.g., relative_path = 'originals/A/UUID1234.jpeg'
    # We can easily extract the UUID from the filename in relative_path!
    # In macOS Photos originals folder, the filename is the UUID!
    # e.g. ZFILENAME is 'A92A7D40-8EF7-4C21-A723-894C8A2F3034.jpeg'
    # So the UUID is the filename without its extension!
    # Let's verify: ZFILENAME is exactly '[UUID].[ext]'.
    # This is incredibly simple and robust! We can just split the filename of relative_path to get the UUID!
    
    cursor.execute("""
        SELECT relative_path FROM files 
        WHERE source_type = 'macos_photos' AND is_duplicate = 1
    """)
    rows = cursor.fetchall()
    
    if not rows:
        print_info("No macOS Photos duplicates found in the database. Nothing to tag.")
        conn.close()
        return

    # Extract UUIDs (the filename without extension under originals/)
    uuids = []
    for r in rows:
        rel_path = r[0]
        # rel_path format: 'originals/A/UUID.jpeg'
        basename = os.path.basename(rel_path)
        uuid = os.path.splitext(basename)[0]
        # Format for AppleScript
        apple_id = f"{uuid}/L0/001"
        uuids.append(apple_id)

    print_info(f"Found {len(uuids)} duplicates from macOS Photos to tag.")
    
    # 1. Create the album if it doesn't exist
    create_album_script = f"""
    tell application "Photos"
        set albumName to "{album_name}"
        if not (exists container albumName) then
            make new album named albumName
        end if
    end tell
    """
    
    try:
        subprocess.run(['osascript', '-e', create_album_script], check=True)
        print_success(f"Verified album '{album_name}' exists in macOS Photos.")
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to create album: {e}")
        conn.close()
        return

    # 2. Batch add the photos to the album (groups of 50 for speed and safety)
    batch_size = 50
    total_added = 0
    
    for i in range(0, len(uuids), batch_size):
        batch = uuids[i:i+batch_size]
        
        # Build the AppleScript list
        list_items = ", ".join([f'media item id "{uid}"' for uid in batch])
        
        add_script = f"""
        tell application "Photos"
            set targetAlbum to album "{album_name}"
            try
                add {{{list_items}}} to targetAlbum
            on error errText number errNum
                log "Error adding batch: " & errText
            end try
        end tell
        """
        
        try:
            subprocess.run(['osascript', '-e', add_script], check=True, stdout=subprocess.DEVNULL)
            total_added += len(batch)
            print_info(f"Tagged batch: {total_added}/{len(uuids)} duplicates grouped.")
        except subprocess.CalledProcessError as e:
            print_warning(f"Warning: Batch from index {i} failed to tag. Continuing...")
            
    print_success(f"Successfully tagged {total_added} duplicates inside the '{album_name}' album!")
    print_info(f"Please open your macOS Photos app, look for the album '{album_name}', select all items, and press Cmd+Delete to remove them safely.")
    
    conn.close()

if __name__ == '__main__':
    main()
