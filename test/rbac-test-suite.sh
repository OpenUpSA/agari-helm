#!/bin/bash
# AGARI RBAC Test Suite
# Tests complete genomics workflow with different user roles
# Run from: /home/dimee/Work/OpenUp/SANBI/agari-helm/test/

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
KEYCLOAK_URL="http://keycloak.local"
FOLIO_URL="http://folio.local"
SONG_URL="http://song.local"
SCORE_URL="http://score.local"
ARRANGER_URL="http://arranger.local"
CLIENT_ID="dms"
CLIENT_SECRET="VDyLEjGR3xDQvoQlrHq5AB6OwbW0Refc"
REALM="agari"

# Test users from your realm export
declare -A USERS=(
    ["owner"]="owner@nicd.ac.za:pass123"
    ["org-admin"]="org-admin@nicd.ac.za:pass123"
    ["org-contributor"]="org-contributor@nicd.ac.za:pass123"
    ["org-viewer"]="org-viewer@nicd.ac.za:pass123"
    ["project-admin"]="admin@nicd.ac.za:pass123"
    ["project-contributor"]="contributor@lab.bio:pass123"
    ["project-viewer"]="viewer@research.org:pass123"
    ["external"]="external@another-org.com:pass123"
)

# Test data sets
declare -A TEST_DATA=(
    ["covid-ba5"]="covid_ba5_sample.fasta:covid_ba5_analysis.json:covid-surveillance-001"
    ["covid-xbb"]="covid_xbb_sample.fasta:covid_xbb_analysis.json:covid-surveillance-002"
    ["malaria-3d7"]="malaria_3d7_sample.fasta:malaria_3d7_analysis.json:malaria-surveillance-001"
    ["malaria-vivax"]="malaria_vivax_sample.fasta:malaria_vivax_analysis.json:malaria-surveillance-002"
)

# Expected permissions matrix (PASS/FAIL/SKIP)
declare -A PERMISSIONS=(
    # Folio Operations
    ["owner:create_pathogen"]="PASS"
    ["owner:create_project"]="PASS"
    ["owner:create_study"]="PASS"
    ["owner:view_projects"]="PASS"
    
    ["org-admin:create_pathogen"]="PASS"
    ["org-admin:create_project"]="PASS"
    ["org-admin:create_study"]="PASS"
    ["org-admin:view_projects"]="PASS"
    
    ["org-contributor:create_pathogen"]="FAIL"
    ["org-contributor:create_project"]="FAIL"
    ["org-contributor:create_study"]="PASS"
    ["org-contributor:view_projects"]="PASS"
    
    ["org-viewer:create_pathogen"]="FAIL"
    ["org-viewer:create_project"]="FAIL"
    ["org-viewer:create_study"]="FAIL"
    ["org-viewer:view_projects"]="PASS"
    
    ["project-admin:create_pathogen"]="FAIL"
    ["project-admin:create_project"]="FAIL"
    ["project-admin:create_study"]="PASS"
    ["project-admin:view_projects"]="PASS"
    
    ["project-contributor:create_pathogen"]="FAIL"
    ["project-contributor:create_project"]="FAIL"
    ["project-contributor:create_study"]="PASS"
    ["project-contributor:view_projects"]="PASS"
    
    ["project-viewer:create_pathogen"]="FAIL"
    ["project-viewer:create_project"]="FAIL"
    ["project-viewer:create_study"]="FAIL"
    ["project-viewer:view_projects"]="PASS"
    
    ["external:create_pathogen"]="FAIL"
    ["external:create_project"]="FAIL"
    ["external:create_study"]="FAIL"
    ["external:view_projects"]="PASS"
    
    # SONG Operations
    ["owner:submit_analysis"]="PASS"
    ["org-admin:submit_analysis"]="PASS"
    ["org-contributor:submit_analysis"]="PASS"
    ["org-viewer:submit_analysis"]="FAIL"
    ["project-admin:submit_analysis"]="PASS"
    ["project-contributor:submit_analysis"]="PASS"
    ["project-viewer:submit_analysis"]="FAIL"
    ["external:submit_analysis"]="FAIL"
    
    # SCORE Operations
    ["owner:upload_file"]="PASS"
    ["org-admin:upload_file"]="PASS"
    ["org-contributor:upload_file"]="PASS"
    ["org-viewer:upload_file"]="FAIL"
    ["project-admin:upload_file"]="PASS"
    ["project-contributor:upload_file"]="PASS"
    ["project-viewer:upload_file"]="FAIL"
    ["external:upload_file"]="FAIL"
    
    # Arranger Operations (all authenticated users can view published data)
    ["owner:view_data"]="PASS"
    ["org-admin:view_data"]="PASS"
    ["org-contributor:view_data"]="PASS"
    ["org-viewer:view_data"]="PASS"
    ["project-admin:view_data"]="PASS"
    ["project-contributor:view_data"]="PASS"
    ["project-viewer:view_data"]="PASS"
    ["external:view_data"]="PASS"
)

# Global test counters
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0
SKIPPED_TESTS=0

# Global variables for created resources
declare -A CREATED_PATHOGENS=()
declare -A CREATED_PROJECTS=()
declare -A CREATED_STUDIES=()

function log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

function log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

function log_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

function log_error() {
    echo -e "${RED}❌ $1${NC}"
}

function log_test_result() {
    local test_name="$1"
    local expected="$2"
    local actual="$3"
    local user="$4"
    
    ((TOTAL_TESTS++))
    
    if [[ "$expected" == "SKIP" ]]; then
        echo -e "${YELLOW}⏭️  $test_name: SKIPPED${NC}"
        ((SKIPPED_TESTS++))
        return
    fi
    
    if [[ "$expected" == "PASS" && "$actual" =~ ^(200|201|202)$ ]]; then
        echo -e "${GREEN}✅ $test_name: PASS (expected)${NC}"
        ((PASSED_TESTS++))
    elif [[ "$expected" == "FAIL" && "$actual" =~ ^(401|403|404)$ ]]; then
        echo -e "${GREEN}✅ $test_name: FAIL (expected)${NC}"
        ((PASSED_TESTS++))
    else
        echo -e "${RED}❌ $test_name: Unexpected result $actual (expected $expected)${NC}"
        ((FAILED_TESTS++))
    fi
}

function get_token() {
    local username="$1"
    local password="$2"
    
    local response=$(curl -s -X POST "$KEYCLOAK_URL/realms/$REALM/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "username=$username&password=$password&grant_type=password&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET")
    
    echo "$response" | jq -r '.access_token // "null"'
}

function test_folio_pathogen_creation() {
    local token="$1"
    local user="$2"
    
    local pathogen_name="Test-Pathogen-$user-$(date +%s)"
    
    local response_code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$FOLIO_URL/pathogens" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"$pathogen_name\",
            \"scientific_name\": \"Test pathogen for $user\",
            \"description\": \"RBAC test pathogen created by $user\"
        }")
    
    if [[ "$response_code" =~ ^(200|201)$ ]]; then
        CREATED_PATHOGENS["$user"]="$pathogen_name"
    fi
    
    echo "$response_code"
}

function test_folio_project_creation() {
    local token="$1"
    local user="$2"
    
    local project_slug="test-project-$user-$(date +%s)"
    
    local response_code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$FOLIO_URL/projects" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{
            \"slug\": \"$project_slug\",
            \"name\": \"Test Project for $user\",
            \"description\": \"RBAC test project created by $user\",
            \"pathogen_id\": \"bc860c60-d5a7-40f6-896a-2db048bcd79a\",
            \"privacy\": \"private\",
            \"organisation_id\": \"default-org\"
        }")
    
    if [[ "$response_code" =~ ^(200|201)$ ]]; then
        CREATED_PROJECTS["$user"]="$project_slug"
    fi
    
    echo "$response_code"
}

function test_folio_study_creation() {
    local token="$1"
    local user="$2"
    
    local study_id="test-study-$user-$(date +%s)"
    
    local response_code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$FOLIO_URL/studies" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{
            \"study_id\": \"$study_id\",
            \"name\": \"Test Study for $user\",
            \"description\": \"RBAC test study created by $user\",
            \"project_id\": \"8bc2c6bc-94c9-4739-827e-0a5d1584afa6\"
        }")
    
    if [[ "$response_code" =~ ^(200|201)$ ]]; then
        CREATED_STUDIES["$user"]="$study_id"
    fi
    
    echo "$response_code"
}

function test_folio_project_view() {
    local token="$1"
    
    local response_code=$(curl -s -w "%{http_code}" -o /dev/null -X GET "$FOLIO_URL/projects" \
        -H "Authorization: Bearer $token")
    
    echo "$response_code"
}

function test_song_analysis_submission() {
    local token="$1"
    local user="$2"
    local test_data_key="$3"
    
    IFS=':' read -r fasta_file analysis_file study_id <<< "${TEST_DATA[$test_data_key]}"
    
    # First create the study in SONG
    local create_study_response=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$SONG_URL/studies/$study_id/" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{
            \"studyId\": \"$study_id\",
            \"name\": \"RBAC Test Study for $user\",
            \"description\": \"Test study for $test_data_key by $user\",
            \"organization\": \"AGARI\",
            \"info\": {\"testUser\": \"$user\", \"testData\": \"$test_data_key\"}
        }")
    
    # Submit analysis
    local response_code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$SONG_URL/submit/$study_id/" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d @"$analysis_file")
    
    echo "$response_code"
}

function test_score_file_upload() {
    local token="$1"
    local user="$2"
    local test_data_key="$3"
    
    IFS=':' read -r fasta_file analysis_file study_id <<< "${TEST_DATA[$test_data_key]}"
    
    # Generate a test object ID
    local object_id="test-object-$user-$(date +%s)"
    local file_size=$(wc -c < "$fasta_file")
    local file_md5=$(md5sum "$fasta_file" | cut -d' ' -f1)
    
    # Initialize upload
    local response_code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$SCORE_URL/upload/$object_id/uploads" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "fileSize=$file_size&md5=$file_md5&overwrite=true")
    
    echo "$response_code"
}

function test_arranger_data_access() {
    local token="$1"
    
    local response_code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$ARRANGER_URL/graphql" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d '{"query": "query {file {hits {total}}}"}')
    
    echo "$response_code"
}

function run_user_tests() {
    local user_key="$1"
    IFS=':' read -r username password <<< "${USERS[$user_key]}"
    
    echo ""
    log_info "Testing user: $user_key ($username)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Get authentication token
    local token=$(get_token "$username" "$password")
    
    if [[ "$token" == "null" || -z "$token" ]]; then
        log_error "Authentication failed for $username"
        return 1
    fi
    
    log_success "Authentication successful"
    
    # Test Folio operations
    log_info "Testing Folio operations..."
    
    # Test pathogen creation
    local expected="${PERMISSIONS[$user_key:create_pathogen]}"
    if [[ -n "$expected" ]]; then
        local result=$(test_folio_pathogen_creation "$token" "$user_key")
        log_test_result "Pathogen Creation" "$expected" "$result" "$user_key"
    fi
    
    # Test project creation
    expected="${PERMISSIONS[$user_key:create_project]}"
    if [[ -n "$expected" ]]; then
        result=$(test_folio_project_creation "$token" "$user_key")
        log_test_result "Project Creation" "$expected" "$result" "$user_key"
    fi
    
    # Test study creation
    expected="${PERMISSIONS[$user_key:create_study]}"
    if [[ -n "$expected" ]]; then
        result=$(test_folio_study_creation "$token" "$user_key")
        log_test_result "Study Creation" "$expected" "$result" "$user_key"
    fi
    
    # Test project viewing
    expected="${PERMISSIONS[$user_key:view_projects]}"
    if [[ -n "$expected" ]]; then
        result=$(test_folio_project_view "$token")
        log_test_result "Project Viewing" "$expected" "$result" "$user_key"
    fi
    
    # Test SONG operations
    log_info "Testing SONG operations..."
    expected="${PERMISSIONS[$user_key:submit_analysis]}"
    if [[ -n "$expected" ]]; then
        result=$(test_song_analysis_submission "$token" "$user_key" "covid-ba5")
        log_test_result "Analysis Submission" "$expected" "$result" "$user_key"
    fi
    
    # Test SCORE operations
    log_info "Testing SCORE operations..."
    expected="${PERMISSIONS[$user_key:upload_file]}"
    if [[ -n "$expected" ]]; then
        result=$(test_score_file_upload "$token" "$user_key" "covid-ba5")
        log_test_result "File Upload" "$expected" "$result" "$user_key"
    fi
    
    # Test Arranger operations
    log_info "Testing Arranger operations..."
    expected="${PERMISSIONS[$user_key:view_data]}"
    if [[ -n "$expected" ]]; then
        result=$(test_arranger_data_access "$token")
        log_test_result "Data Access" "$expected" "$result" "$user_key"
    fi
}

function setup_test_environment() {
    log_info "Setting up test environment..."
    
    # Verify all services are accessible
    local services=("$KEYCLOAK_URL" "$FOLIO_URL" "$SONG_URL" "$SCORE_URL" "$ARRANGER_URL")
    local service_names=("Keycloak" "Folio" "SONG" "SCORE" "Arranger")
    
    for i in "${!services[@]}"; do
        local service="${services[$i]}"
        local name="${service_names[$i]}"
        
        if curl -s -f "$service/health" > /dev/null 2>&1 || curl -s -f "$service" > /dev/null 2>&1; then
            log_success "$name is accessible"
        else
            log_warning "$name might not be accessible at $service"
        fi
    done
    
    # Verify test data files exist
    for test_key in "${!TEST_DATA[@]}"; do
        IFS=':' read -r fasta_file analysis_file study_id <<< "${TEST_DATA[$test_key]}"
        
        if [[ -f "$fasta_file" && -f "$analysis_file" ]]; then
            log_success "Test data for $test_key is ready"
        else
            log_error "Missing test data files for $test_key"
            return 1
        fi
    done
}

function print_test_summary() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_info "AGARI RBAC Test Suite Results"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "📊 Test Statistics:"
    echo "   Total tests: $TOTAL_TESTS"
    echo "   ✅ Passed: $PASSED_TESTS"
    echo "   ❌ Failed: $FAILED_TESTS"
    echo "   ⏭️  Skipped: $SKIPPED_TESTS"
    
    if [[ $TOTAL_TESTS -gt 0 ]]; then
        local success_rate=$(( PASSED_TESTS * 100 / TOTAL_TESTS ))
        echo "   📈 Success rate: $success_rate%"
    fi
    
    echo ""
    
    if [[ $FAILED_TESTS -eq 0 ]]; then
        log_success "🎉 All tests passed! RBAC is working correctly."
        echo ""
        echo "🔒 Your Role-Based Access Control implementation is solid:"
        echo "   • Authentication is working for all user types"
        echo "   • Permissions are properly enforced"
        echo "   • Users can only access resources they should"
        echo "   • The complete genomics workflow respects RBAC rules"
        echo ""
        return 0
    else
        log_error "💥 Some tests failed. Check the RBAC implementation."
        echo ""
        echo "🔍 Debug recommendations:"
        echo "   • Check Keycloak group assignments"
        echo "   • Verify UMA policies in DMS client"
        echo "   • Review Folio authorization logic"
        echo "   • Check SONG/SCORE token validation"
        echo ""
        return 1
    fi
}

function main() {
    echo "🎯 AGARI RBAC Test Suite"
    echo "========================"
    echo "Testing complete genomics workflow with role-based access control"
    echo ""
    
    # Setup
    if ! setup_test_environment; then
        log_error "Test environment setup failed"
        exit 1
    fi
    
    # Run tests for each user
    for user_key in "${!USERS[@]}"; do
        run_user_tests "$user_key"
    done
    
    # Print summary and exit
    print_test_summary
    exit $?
}

# Check if script is being run from correct directory
if [[ ! -f "covid_ba5_sample.fasta" ]]; then
    log_error "Please run this script from the test directory containing the test data files"
    exit 1
fi

# Run main function
main "$@"
