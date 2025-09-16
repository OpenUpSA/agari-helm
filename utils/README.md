# AGARI Utilities

This directory contains utility scripts for managing the AGARI platform.

## Database Cleanup Utility

**Usage:**
```bash
# List all studies
./utils/song_cleanup.sh list

# Delete a specific study
./utils/song_cleanup.sh delete study1

# Count all records
./utils/song_cleanup.sh count

# Delete all records
./utils/song_cleanup.sh delete-all
```

## OAuth2 Secret Update Utility

Updates OAuth2 client secrets across Song, Score, and Maestro services.

**Usage:**
```bash
# Update secrets to a new value
./utils/update-secrets.sh "your-new-secret-here"

# Example with a complex secret
./utils/update-secrets.sh "abc123-xyz789-secret"
```

**What it does:**
- Updates `secret` and `clientSecret` fields in Song, Score, and Maestro values.yaml files
- Creates backup files with timestamps before making changes
- Provides instructions for applying changes to your Kubernetes cluster

**Files modified:**
- `helm/song/values.yaml`
- `helm/score/values.yaml` 
- `helm/maestro/values.yaml`

**After running the script:**
```bash
# Apply changes to your cluster
helm upgrade song ./helm/song -n agari
helm upgrade score ./helm/score -n agari
helm upgrade maestro ./helm/maestro -n agari
```

**Delete backup files if everything works fine:**
```bash
find helm/ -name "*.backup.*" -type f -delete
```
