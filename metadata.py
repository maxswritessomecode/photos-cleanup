import os
import datetime
import subprocess
import shutil
import ctypes
import platform

# Define attributes structure for setattrlist on macOS
if platform.system() == "Darwin":
    class attrlist(ctypes.Structure):
        _fields_ = [
            ("bitmapcount", ctypes.c_uint16),
            ("reserved", ctypes.c_uint16),
            ("commonattr", ctypes.c_uint32),
            ("volattr", ctypes.c_uint32),
            ("dirattr", ctypes.c_uint32),
            ("fileattr", ctypes.c_uint32),
            ("forkattr", ctypes.c_uint32),
        ]

    class timespec(ctypes.Structure):
        _fields_ = [
            ("tv_sec", ctypes.c_long),
            ("tv_nsec", ctypes.c_long),
        ]

    ATTR_CMN_CRTIME = 0x00000200

    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except Exception:
        libc = None
else:
    libc = None

def parse_date_string(date_string):
    """Parse date string in standard format 'YYYY-MM-DD HH:MM:SS'."""
    if not date_string:
        return None
    try:
        # Support splitting on timezone offsets
        clean_str = date_string.split('+')[0].strip()
        return datetime.datetime.strptime(clean_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None

def set_file_timestamps(file_path, date_string):
    """
    Sets the filesystem access and modification times (utime) of a file,
    and ensures standard Finder Date Created matches on macOS using native setattrlist.
    """
    dt = parse_date_string(date_string)
    if not dt:
        return False
        
    try:
        # Convert UTC datetime to epoch timestamp
        dt_utc = dt.replace(tzinfo=datetime.timezone.utc)
        epoch_time = dt_utc.timestamp()
        
        # Set access and modification times
        os.utime(file_path, (epoch_time, epoch_time))
        
        # On macOS, try to set the creation time (birthtime) natively in-process
        if platform.system() == "Darwin" and libc is not None:
            alist = attrlist()
            alist.bitmapcount = 5  # ATTR_BIT_MAP_COUNT
            alist.commonattr = ATTR_CMN_CRTIME
            
            time_val = timespec(int(epoch_time), 0)
            
            result = libc.setattrlist(
                file_path.encode('utf-8'),
                ctypes.byref(alist),
                ctypes.byref(time_val),
                ctypes.sizeof(time_val),
                0
            )
            if result == 0:
                return True
        
        # Fallback to touch command if setattrlist fails or is not available
        touch_timestamp = dt.strftime('%Y%m%d%H%M.%S')
        env = os.environ.copy()
        env['TZ'] = 'UTC'
        subprocess.run(['touch', '-t', touch_timestamp, file_path], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        print(f"Error setting timestamps on {file_path}: {e}")
        return False

def write_exif_metadata(file_path, date_string, latitude=None, longitude=None):
    """
    Embed EXIF tags directly in the media file using exiftool if available.
    Falls back gracefully to filesystem utime if exiftool is not installed.
    """
    # Always set the filesystem modification and creation dates as a reliable base fallback
    set_file_timestamps(file_path, date_string)
    
    # Check if exiftool is available
    if not shutil.which('exiftool'):
        return False

    dt = parse_date_string(date_string)
    if not dt:
        return False

    exif_date_str = dt.strftime('%Y:%m:%d %H:%M:%S')
    
    cmd = [
        'exiftool',
        '-overwrite_original',
        f'-AllDates={exif_date_str}'
    ]
    
    if latitude is not None and longitude is not None:
        # Exiftool handles sign directly for latitude/longitude
        cmd.append(f'-GPSLatitude={abs(latitude)}')
        cmd.append(f'-GPSLatitudeRef={"N" if latitude >= 0 else "S"}')
        cmd.append(f'-GPSLongitude={abs(longitude)}')
        cmd.append(f'-GPSLongitudeRef={"E" if longitude >= 0 else "W"}')
        
    cmd.append(file_path)
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0
    except Exception as e:
        print(f"Error calling exiftool: {e}")
        return False
