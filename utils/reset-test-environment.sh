#!/bin/bash

# AGARI Test Environment Reset Script
# This script resets the test environment with configurable options:
# 1. Cleaning SONG database (--song or --all)
# 2. Wiping Folio database (--folio or --all)
# 3. Deleting and reimporting Keycloak agari realm (--keycloak or --all)

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default options
RESET_SONG=false
RESET_FOLIO=false
RESET_KEYCLOAK=false

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "Options:"
    echo "  --song          Reset SONG database only"
    echo "  --folio         Reset Folio database only"
    echo "  --keycloak      Reset Keycloak agari realm only"
    echo "  --all           Reset everything (SONG + Folio + Keycloak)"
    echo "  --sf            Reset SONG and Folio (common usage)"
    echo "  --help, -h      Show this help message"
    echo
    echo "Examples:"
    echo "  $0 --all                    # Reset everything"
    echo "  $0 --sf                     # Reset SONG and Folio (most common)"
    echo "  $0 --song --folio           # Same as --sf"
    echo "  $0 --folio                  # Reset only Folio"
    echo "  $0 --keycloak               # Reset only Keycloak"
    exit 1
}

# Parse command line arguments
if [ $# -eq 0 ]; then
    show_usage
fi

while [[ $# -gt 0 ]]; do
    case $1 in
        --song)
            RESET_SONG=true
            shift
            ;;
        --folio)
            RESET_FOLIO=true
            shift
            ;;
        --keycloak)
            RESET_KEYCLOAK=true
            shift
            ;;
        --all)
            RESET_SONG=true
            RESET_FOLIO=true
            RESET_KEYCLOAK=true
            shift
            ;;
        --sf)
            RESET_SONG=true
            RESET_FOLIO=true
            shift
            ;;
        --help|-h)
            show_usage
            ;;
        *)
            echo -e "${RED}❌ Unknown option: $1${NC}"
            show_usage
            ;;
    esac
done

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo -e "${BLUE}🔄 AGARI Test Environment Reset${NC}"
echo -e "${YELLOW}Reset options:${NC}"
echo "  • SONG: $([ "$RESET_SONG" = true ] && echo "✅ Yes" || echo "❌ No")"
echo "  • Folio: $([ "$RESET_FOLIO" = true ] && echo "✅ Yes" || echo "❌ No")"
echo "  • Keycloak: $([ "$RESET_KEYCLOAK" = true ] && echo "✅ Yes" || echo "❌ No")"
echo

# Step 1: Clean SONG Database (if requested)
if [ "$RESET_SONG" = true ]; then
    echo -e "${BLUE}=== Step 1: Cleaning SONG Database ===${NC}"
    cd "$SCRIPT_DIR"
    echo "DELETE ALL" | ./song_cleanup.sh delete-all
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ SONG database cleaned successfully${NC}"
    else
        echo -e "${RED}❌ SONG cleanup failed${NC}"
        exit 1
    fi
    echo
else
    echo -e "${YELLOW}⏭️  Skipping SONG database cleanup${NC}"
fi

# Step 2: Wipe Folio Database (if requested)
if [ "$RESET_FOLIO" = true ]; then
    echo -e "${BLUE}=== Step 2: Wiping Folio Database ===${NC}"
    cd "$SCRIPT_DIR"
    echo "WIPE ALL DATA" | ./folio_cleanup.sh wipe
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Folio database wiped successfully${NC}"
    else
        echo -e "${RED}❌ Folio wipe failed${NC}"
        exit 1
    fi
    echo
else
    echo -e "${YELLOW}⏭️  Skipping Folio database wipe${NC}"
fi

# Step 3: Reset Keycloak Agari Realm (if requested)
if [ "$RESET_KEYCLOAK" = true ]; then
    echo -e "${BLUE}=== Step 3: Resetting Keycloak Agari Realm ===${NC}"

    # Find Keycloak pod
    KEYCLOAK_POD=$(kubectl get pods -n agari | grep keycloak | grep -v db | awk '{print $1}' | head -1)
    if [ -z "$KEYCLOAK_POD" ]; then
        echo -e "${RED}❌ Could not find Keycloak pod${NC}"
        exit 1
    fi
    echo "Found Keycloak pod: $KEYCLOAK_POD"

    # Configure admin CLI
    echo "Configuring Keycloak admin CLI..."
    kubectl exec -n agari "$KEYCLOAK_POD" -- /opt/keycloak/bin/kcadm.sh config credentials \
        --server http://localhost:8080 --realm master --user admin --password admin123
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to configure Keycloak admin CLI${NC}"
        exit 1
    fi

    # Delete existing agari realm (ignore errors if it doesn't exist)
    echo "Deleting existing agari realm..."
    kubectl exec -n agari "$KEYCLOAK_POD" -- /opt/keycloak/bin/kcadm.sh delete realms/agari 2>/dev/null || true

    # Import fresh agari realm
    echo "Importing fresh agari realm..."
    cd "$PROJECT_ROOT"
    cat helm/keycloak/configs/agari-realm-simple.json | kubectl exec -n agari "$KEYCLOAK_POD" -i -- /opt/keycloak/bin/kcadm.sh create realms -f -
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Keycloak agari realm reset successfully${NC}"
    else
        echo -e "${RED}❌ Keycloak realm import failed${NC}"
        exit 1
    fi
    echo
else
    echo -e "${YELLOW}⏭️  Skipping Keycloak realm reset${NC}"
fi

# Step 4: Verification
echo -e "${BLUE}=== Verification ===${NC}"

# Verify SONG is clean (if it was reset)
if [ "$RESET_SONG" = true ]; then
    echo -n "Verifying SONG is clean: "
    SONG_COUNT=$(cd "$SCRIPT_DIR" && ./song_cleanup.sh list 2>/dev/null | grep -c "rows)" || echo "0")
    if [ "$SONG_COUNT" -eq 1 ]; then
        echo -e "${GREEN}✅ Empty${NC}"
    else
        echo -e "${YELLOW}⚠️  May have data${NC}"
    fi
fi

# Verify Folio is clean (if it was reset)
if [ "$RESET_FOLIO" = true ]; then
    echo -n "Verifying Folio is clean: "
    FOLIO_COUNT=$(cd "$SCRIPT_DIR" && ./folio_cleanup.sh list 2>/dev/null | grep -c "rows)" || echo "0")
    if [ "$FOLIO_COUNT" -eq 1 ]; then
        echo -e "${GREEN}✅ Empty${NC}"
    else
        echo -e "${YELLOW}⚠️  May have data${NC}"
    fi
fi

# Verify Keycloak realm exists (if it was reset)
if [ "$RESET_KEYCLOAK" = true ]; then
    echo -n "Verifying Keycloak agari realm: "
    kubectl exec -n agari "$KEYCLOAK_POD" -- /opt/keycloak/bin/kcadm.sh get realms/agari --fields enabled 2>/dev/null | grep -q "true"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Active${NC}"
    else
        echo -e "${RED}❌ Not found or inactive${NC}"
    fi
fi

echo
echo -e "${GREEN}🎉 Reset Complete!${NC}"
echo -e "${YELLOW}Summary:${NC}"
[ "$RESET_SONG" = true ] && echo "  • SONG database: Cleaned" || echo "  • SONG database: Skipped"
[ "$RESET_FOLIO" = true ] && echo "  • Folio database: Wiped" || echo "  • Folio database: Skipped"
[ "$RESET_KEYCLOAK" = true ] && echo "  • Keycloak agari realm: Reset" || echo "  • Keycloak agari realm: Skipped"
echo
echo -e "${BLUE}Ready for testing!${NC}"
