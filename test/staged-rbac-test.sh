#!/bin/bash

# Staged RBAC Testing Script
# Based on rest-min.http manual testing patterns

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test configuration
KEYCLOAK_URL="http://keycloak.local"
FOLIO_URL="http://folio.local"
REALM="agari"
CLIENT_ID="dms"
CLIENT_SECRET="VDyLEjGR3xDQvoQlrHq5AB6OwbW0Refc"

# Known test data
EXISTING_PROJECT_ID="9cf9946e-a231-4247-b9d5-cc200a543a49"
SARS_PATHOGEN_ID="81d27b39-19f0-4a2c-a547-bb91361645ab"

# Test users from rest-min.http
declare -A TEST_USERS=(
    ["system_admin"]="system.admin@agari.tech:admin123"
    ["owner"]="owner@nicd.ac.za:pass123"
    ["org_admin"]="org-admin@nicd.ac.za:pass123"
    ["org_contributor"]="org-contributor@nicd.ac.za:pass123"
    ["org_viewer"]="org-viewer@nicd.ac.za:pass123"
    ["project_admin"]="admin@nicd.ac.za:pass123"
    ["contributor"]="contributor@lab.bio:pass123"
    ["viewer"]="viewer@research.org:pass123"
    ["external"]="external@another-org.com:pass123"
)

# Function to get auth token
get_auth_token() {
    local username=$1
    local password=$2
    
    local response=$(curl -s -X POST "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "username=${username}&password=${password}&grant_type=password&client_id=${CLIENT_ID}&client_secret=${CLIENT_SECRET}")
    
    echo "$response" | jq -r '.access_token // empty'
}

# Function to test API endpoint
test_endpoint() {
    local method=$1
    local url=$2
    local token=$3
    local data=$4
    local expected_status=$5
    
    local curl_cmd="curl -s -w 'STATUS:%{http_code}' -X $method '$url'"
    
    if [ ! -z "$token" ]; then
        curl_cmd="$curl_cmd -H 'Authorization: Bearer $token'"
    fi
    
    if [ ! -z "$data" ]; then
        curl_cmd="$curl_cmd -H 'Content-Type: application/json' -d '$data'"
    fi
    
    local response=$(eval $curl_cmd)
    local status=$(echo "$response" | grep -o 'STATUS:[0-9]*' | cut -d: -f2)
    local body=$(echo "$response" | sed 's/STATUS:[0-9]*$//')
    
    if [ "$status" = "$expected_status" ]; then
        echo -e "${GREEN}✅${NC}"
        return 0
    else
        echo -e "${RED}❌ (got $status, expected $expected_status)${NC}"
        if [ ${#body} -lt 200 ]; then
            echo "    Response: $body"
        fi
        return 1
    fi
}

# Stage 1: Authentication Testing
echo -e "${BLUE}=== STAGE 1: AUTHENTICATION TESTING ===${NC}"
echo

declare -A USER_TOKENS

for user_role in "${!TEST_USERS[@]}"; do
    IFS=':' read -r username password <<< "${TEST_USERS[$user_role]}"
    
    echo -n "Testing login for $user_role ($username): "
    
    token=$(get_auth_token "$username" "$password")
    
    if [ ! -z "$token" ] && [ "$token" != "null" ]; then
        echo -e "${GREEN}✅ SUCCESS${NC}"
        USER_TOKENS["$user_role"]="$token"
    else
        echo -e "${RED}❌ FAILED${NC}"
    fi
done

echo
echo -e "${YELLOW}Authentication Results:${NC}"
for user_role in "${!USER_TOKENS[@]}"; do
    echo "  $user_role: Token acquired"
done

# Stage 2: Reading Permissions Testing
echo
echo -e "${BLUE}=== STAGE 2: READING PERMISSIONS TESTING ===${NC}"
echo

# Test health endpoint (should work for everyone)
echo "Testing health endpoint (should be accessible to all):"
for user_role in "${!USER_TOKENS[@]}"; do
    echo -n "  $user_role: "
    test_endpoint "GET" "${FOLIO_URL}/health" "${USER_TOKENS[$user_role]}" "" "200"
done

echo
echo "Testing pathogens endpoint (should be accessible to all authenticated users):"
for user_role in "${!USER_TOKENS[@]}"; do
    echo -n "  $user_role: "
    test_endpoint "GET" "${FOLIO_URL}/pathogens" "${USER_TOKENS[$user_role]}" "" "200"
done

echo
echo "Testing projects list (visibility depends on user role):"
for user_role in "${!USER_TOKENS[@]}"; do
    echo -n "  $user_role: "
    # Most users should be able to see projects list, content may vary
    test_endpoint "GET" "${FOLIO_URL}/projects" "${USER_TOKENS[$user_role]}" "" "200"
done

echo
echo "Testing specific project access (access depends on membership):"
for user_role in "${!USER_TOKENS[@]}"; do
    echo -n "  $user_role: "
    # This will vary - some users may get 200, others 403/404
    response=$(curl -s -w 'STATUS:%{http_code}' \
        -H "Authorization: Bearer ${USER_TOKENS[$user_role]}" \
        "${FOLIO_URL}/projects/${EXISTING_PROJECT_ID}")
    status=$(echo "$response" | grep -o 'STATUS:[0-9]*' | cut -d: -f2)
    
    case $status in
        200) echo -e "${GREEN}✅ CAN ACCESS${NC}" ;;
        403) echo -e "${YELLOW}⚠️  FORBIDDEN${NC}" ;;
        404) echo -e "${YELLOW}⚠️  NOT FOUND${NC}" ;;
        *) echo -e "${RED}❌ ERROR ($status)${NC}" ;;
    esac
done

# Stage 3: Project Management Testing
echo
echo -e "${BLUE}=== STAGE 3: PROJECT MANAGEMENT TESTING ===${NC}"
echo

echo "Testing project members list (requires Owner/Admin):"
for user_role in "${!USER_TOKENS[@]}"; do
    echo -n "  $user_role: "
    response=$(curl -s -w 'STATUS:%{http_code}' \
        -H "Authorization: Bearer ${USER_TOKENS[$user_role]}" \
        "${FOLIO_URL}/projects/${EXISTING_PROJECT_ID}/members")
    status=$(echo "$response" | grep -o 'STATUS:[0-9]*' | cut -d: -f2)
    
    case $status in
        200) echo -e "${GREEN}✅ CAN VIEW MEMBERS${NC}" ;;
        403) echo -e "${YELLOW}⚠️  FORBIDDEN${NC}" ;;
        404) echo -e "${YELLOW}⚠️  NOT FOUND${NC}" ;;
        *) echo -e "${RED}❌ ERROR ($status)${NC}" ;;
    esac
done

echo
echo "Testing project creation (requires Owner/Org Admin):"
for user_role in "${!USER_TOKENS[@]}"; do
    echo -n "  $user_role: "
    
    # Create unique project slug for each test
    project_data="{
        \"slug\": \"test-project-${user_role}-$(date +%s)\",
        \"name\": \"Test Project for $user_role\",
        \"description\": \"Test project creation by $user_role\",
        \"pathogen_id\": \"${SARS_PATHOGEN_ID}\",
        \"privacy\": \"private\",
        \"organisation_id\": \"default-org\"
    }"
    
    response=$(curl -s -w 'STATUS:%{http_code}' -X POST \
        -H "Authorization: Bearer ${USER_TOKENS[$user_role]}" \
        -H "Content-Type: application/json" \
        -d "$project_data" \
        "${FOLIO_URL}/projects")
    status=$(echo "$response" | grep -o 'STATUS:[0-9]*' | cut -d: -f2)
    
    case $status in
        200|201) echo -e "${GREEN}✅ CAN CREATE${NC}" ;;
        403) echo -e "${YELLOW}⚠️  FORBIDDEN${NC}" ;;
        400) echo -e "${RED}❌ BAD REQUEST${NC}" ;;
        *) echo -e "${RED}❌ ERROR ($status)${NC}" ;;
    esac
done

# Stage 4: Member Management Testing  
echo
echo -e "${BLUE}=== STAGE 4: MEMBER MANAGEMENT TESTING ===${NC}"
echo

echo "Testing adding users to project (requires Owner/Admin):"
for user_role in "${!USER_TOKENS[@]}"; do
    echo -n "  $user_role trying to add contributor: "
    
    member_data="{
        \"username\": \"contributor@lab.bio\",
        \"permission\": \"contributor\"
    }"
    
    response=$(curl -s -w 'STATUS:%{http_code}' -X POST \
        -H "Authorization: Bearer ${USER_TOKENS[$user_role]}" \
        -H "Content-Type: application/json" \
        -d "$member_data" \
        "${FOLIO_URL}/projects/${EXISTING_PROJECT_ID}/members")
    status=$(echo "$response" | grep -o 'STATUS:[0-9]*' | cut -d: -f2)
    
    case $status in
        200|201) echo -e "${GREEN}✅ CAN ADD MEMBERS${NC}" ;;
        403) echo -e "${YELLOW}⚠️  FORBIDDEN${NC}" ;;
        400) echo -e "${RED}❌ BAD REQUEST${NC}" ;;
        409) echo -e "${YELLOW}⚠️  ALREADY EXISTS${NC}" ;;
        *) echo -e "${RED}❌ ERROR ($status)${NC}" ;;
    esac
done

# Stage 5: Study Management Testing
echo
echo -e "${BLUE}=== STAGE 5: STUDY MANAGEMENT TESTING ===${NC}"
echo

echo "Testing studies list (user sees studies from accessible projects):"
for user_role in "${!USER_TOKENS[@]}"; do
    echo -n "  $user_role: "
    test_endpoint "GET" "${FOLIO_URL}/studies" "${USER_TOKENS[$user_role]}" "" "200"
done

echo
echo "Testing study creation (requires Owner/Admin/Contributor of project):"
for user_role in "${!USER_TOKENS[@]}"; do
    echo -n "  $user_role: "
    
    study_data="{
        \"study_id\": \"test-study-${user_role}-$(date +%s)\",
        \"name\": \"Test Study by $user_role\",
        \"description\": \"Test study creation by $user_role\",
        \"project_id\": \"${EXISTING_PROJECT_ID}\"
    }"
    
    response=$(curl -s -w 'STATUS:%{http_code}' -X POST \
        -H "Authorization: Bearer ${USER_TOKENS[$user_role]}" \
        -H "Content-Type: application/json" \
        -d "$study_data" \
        "${FOLIO_URL}/studies")
    status=$(echo "$response" | grep -o 'STATUS:[0-9]*' | cut -d: -f2)
    
    case $status in
        200|201) echo -e "${GREEN}✅ CAN CREATE STUDIES${NC}" ;;
        403) echo -e "${YELLOW}⚠️  FORBIDDEN${NC}" ;;
        400) echo -e "${RED}❌ BAD REQUEST${NC}" ;;
        *) echo -e "${RED}❌ ERROR ($status)${NC}" ;;
    esac
done

echo
echo -e "${BLUE}=== RBAC TESTING COMPLETE ===${NC}"
echo -e "${YELLOW}Summary: Check the results above to understand the permission matrix${NC}"
