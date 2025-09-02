#!/bin/bash

# Script to update OAuth2 client secrets in Song, Score, and Maestro values.yaml files
# Usage: ./update-secrets.sh <new-secret>

set -e

# Check if argument is provided
if [ $# -eq 0 ]; then
    echo "Usage: $0 <new-secret>"
    echo "Example: $0 my-new-secret-123"
    exit 1
fi

NEW_SECRET="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELM_DIR="$(dirname "$SCRIPT_DIR")/helm"

# Validate that we're in the right directory structure
if [ ! -d "$HELM_DIR" ]; then
    echo "Error: helm directory not found at $HELM_DIR"
    echo "Make sure you're running this script from the utils directory"
    exit 1
fi

echo "Updating OAuth2 client secrets to: $NEW_SECRET"
echo

# Function to update secrets in a values.yaml file
update_secrets_in_file() {
    local file="$1"
    local service_name="$2"
    
    if [ ! -f "$file" ]; then
        echo "Warning: $file not found, skipping..."
        return
    fi
    
    echo "Updating $service_name secrets in $file"
    
    # Create backup
    cp "$file" "$file.backup.$(date +%Y%m%d_%H%M%S)"
    
    # Update the secrets using sed
    # This handles both 'secret:' and 'clientSecret:' patterns
    # First, get the current secret values to update them regardless of what they are
    sed -i.tmp \
        -e "s/secret: \"[^\"]*\"/secret: \"$NEW_SECRET\"/g" \
        -e "s/clientSecret: \"[^\"]*\"/clientSecret: \"$NEW_SECRET\"/g" \
        "$file"
    
    # Remove the temporary file created by sed
    rm -f "$file.tmp"
    
    # Verify changes were made
    if grep -q "\"$NEW_SECRET\"" "$file"; then
        echo "✓ Successfully updated secrets in $service_name"
    else
        echo "⚠ Warning: No secrets were updated in $service_name (maybe they were already different?)"
    fi
    echo
}

# Update secrets in each service
update_secrets_in_file "$HELM_DIR/song/values.yaml" "Song"
update_secrets_in_file "$HELM_DIR/score/values.yaml" "Score"  
update_secrets_in_file "$HELM_DIR/maestro/values.yaml" "Maestro"
update_secrets_in_file "$HELM_DIR/folio/values.yaml" "Folio"

echo "Secret update completed!"
echo
echo "Modified files:"
echo "- $HELM_DIR/song/values.yaml"
echo "- $HELM_DIR/score/values.yaml"
echo "- $HELM_DIR/maestro/values.yaml"
echo "- $HELM_DIR/folio/values.yaml"
echo
echo "Backup files created with timestamp suffix (.backup.YYYYMMDD_HHMMSS)"
echo
echo "To apply changes to your cluster, run:"
echo "  helm upgrade song ./helm/song -n agari"
echo "  helm upgrade score ./helm/score -n agari"
echo "  helm upgrade maestro ./helm/maestro -n agari"
echo "  helm upgrade folio ./helm/folio -n agari"
