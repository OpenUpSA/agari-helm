# AGARI Utilities

This directory contains utility scripts for managing the AGARI platform.

## Database Cleanup Utilities

### Option 1: Bash Script (Recommended - Simple & Reliable)

**Usage:**
```bash
# List all studies
./utils/song_cleanup.sh list

# Delete a specific study
./utils/song_cleanup.sh delete study1

# Count all records
./utils/song_cleanup.sh count
```

**Features:**
- ✅ Works reliably by connecting directly to pods
- ✅ No dependencies or port forwarding needed
- ✅ Simple and fast
- ❌ Less detailed output
- ❌ No confirmation prompts

### Option 2: Python Script (Advanced - More Features)

**Prerequisites:**
1. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Port-forward the SONG database:
   ```bash
   kubectl port-forward -n agari-dev svc/song-db 5433:5432
   ```

**Usage:**
```bash
# List all studies with detailed info
python cleanup_song_db.py --list-studies

# Delete a specific study (with confirmation)
python cleanup_song_db.py --delete-study study1

# Delete ALL studies (dangerous!)
python cleanup_song_db.py --delete-all-studies

# Skip confirmation prompts
python cleanup_song_db.py --delete-study study1 --force

# Run database vacuum
python cleanup_song_db.py --vacuum
```

**Features:**
- ✅ Detailed output and confirmations
- ✅ Safety prompts
- ✅ Database vacuum functionality
- ✅ Better error handling
- ❌ Requires port forwarding setup
- ❌ More dependencies

## Recommendation

Use the **bash script** for quick daily operations and the **Python script** when you need more detailed information or safety features.
