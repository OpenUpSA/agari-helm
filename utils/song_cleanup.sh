#!/bin/bash
# Enhanced Song Database Cleanup Script
# Provides robust operations for managing Song database studies

NAMESPACE="agari-dev"
POD_NAME=$(kubectl get pod -n $NAMESPACE -l app.kubernetes.io/name=song-db -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [ -z "$POD_NAME" ]; then
    # Fallback to deployment name
    POD_NAME=$(kubectl get pod -n $NAMESPACE | grep song-db | head -1 | awk '{print $1}')
fi

if [ -z "$POD_NAME" ]; then
    echo "❌ Error: Could not find song-db pod in namespace $NAMESPACE"
    exit 1
fi

echo "🔗 Using pod: $POD_NAME"

# Function to execute SQL and handle errors
exec_sql() {
    local sql="$1"
    local description="$2"
    
    echo "📝 $description"
    if kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d songDb -c "$sql" 2>&1; then
        echo "✅ $description completed successfully"
        return 0
    else
        echo "❌ $description failed"
        return 1
    fi
}

# Function to get study info before deletion
get_study_info() {
    local study_id="$1"
    echo "📊 Getting study information for: $study_id"
    kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d songDb -c "
        SELECT 
            s.id as study_id, 
            s.name, 
            s.description, 
            s.organization,
            COUNT(DISTINCT a.id) as analysis_count,
            COUNT(DISTINCT f.id) as file_count,
            COUNT(DISTINCT sa.id) as sample_count
        FROM study s
        LEFT JOIN analysis a ON s.id = a.study_id
        LEFT JOIN file f ON a.id = f.analysis_id
        LEFT JOIN sample sa ON a.id = sa.analysis_id
        WHERE s.id = '$study_id'
        GROUP BY s.id, s.name, s.description, s.organization;
    "
}

# Function to safely delete a study with proper foreign key handling
safe_delete_study() {
    local study_id="$1"
    local confirm="${2:-true}"
    
    # Get study info first
    get_study_info "$study_id"
    
    if [ "$confirm" = "true" ]; then
        echo ""
        read -p "⚠️  Are you sure you want to delete study '$study_id' and ALL its data? (yes/no): " response
        if [ "$response" != "yes" ]; then
            echo "🚫 Delete cancelled."
            return 1
        fi
    fi
    
    echo "🗑️  Starting safe deletion of study: $study_id"
    
    # Execute deletion in correct order to handle foreign keys
    exec_sql "
        BEGIN;
        
        -- Delete analysis state changes first (foreign key constraint)
        DELETE FROM analysis_state_change 
        WHERE analysis_id IN (SELECT id FROM analysis WHERE study_id = '$study_id');
        
        -- Delete uploads
        DELETE FROM upload 
        WHERE analysis_id IN (SELECT id FROM analysis WHERE study_id = '$study_id');
        
        -- Delete files
        DELETE FROM file 
        WHERE analysis_id IN (SELECT id FROM analysis WHERE study_id = '$study_id');
        
        -- Delete samples
        DELETE FROM sample 
        WHERE analysis_id IN (SELECT id FROM analysis WHERE study_id = '$study_id');
        
        -- Delete samplesets 
        DELETE FROM sampleset 
        WHERE analysis_id IN (SELECT id FROM analysis WHERE study_id = '$study_id');
        
        -- Delete analyses
        DELETE FROM analysis WHERE study_id = '$study_id';
        
        -- Delete study info records
        DELETE FROM info WHERE id = '$study_id' AND id_type = 'Study';
        
        -- Delete study
        DELETE FROM study WHERE id = '$study_id';
        
        COMMIT;
    " "Deleting study $study_id with all related data"
}

case "$1" in
    "list")
        echo "📋 Listing all studies:"
        exec_sql "
            SELECT 
                s.id as study_id, 
                s.name, 
                s.description, 
                s.organization,
                COUNT(DISTINCT a.id) as analyses,
                COUNT(DISTINCT f.id) as files
            FROM study s
            LEFT JOIN analysis a ON s.id = a.study_id
            LEFT JOIN file f ON a.id = f.analysis_id
            GROUP BY s.id, s.name, s.description, s.organization
            ORDER BY s.name;
        " "Listing studies with counts"
        ;;
    "delete")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 delete <study_id> [--force]"
            exit 1
        fi
        STUDY_ID="$2"
        FORCE_FLAG="$3"
        
        if [ "$FORCE_FLAG" = "--force" ]; then
            safe_delete_study "$STUDY_ID" false
        else
            safe_delete_study "$STUDY_ID" true
        fi
        ;;
    "delete-all")
        echo "⚠️  WARNING: This will delete ALL studies and data!"
        read -p "Type 'DELETE ALL' to confirm: " confirm
        if [ "$confirm" = "DELETE ALL" ]; then
            echo "🗑️  Deleting all data..."
            exec_sql "
                BEGIN;
                -- Delete in reverse dependency order
                DELETE FROM analysis_state_change;
                DELETE FROM upload;
                DELETE FROM file;
                DELETE FROM sample;
                DELETE FROM sampleset;
                DELETE FROM analysis;
                DELETE FROM info WHERE id_type = 'Study';
                DELETE FROM study;
                COMMIT;
            " "Deleting all studies and data"
        else
            echo "🚫 Operation cancelled."
        fi
        ;;
    "count")
        echo "📊 Counting database records:"
        exec_sql "
            SELECT 
                (SELECT COUNT(*) FROM study) AS studies,
                (SELECT COUNT(*) FROM analysis) AS analyses,
                (SELECT COUNT(*) FROM file) AS files,
                (SELECT COUNT(*) FROM sample) AS samples,
                (SELECT COUNT(*) FROM analysis_state_change) AS state_changes,
                (SELECT COUNT(*) FROM upload) AS uploads;
        " "Getting record counts"
        ;;
    "info")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 info <study_id>"
            exit 1
        fi
        get_study_info "$2"
        ;;
    "tables")
        echo "🗂️  Database table information:"
        exec_sql "
            SELECT 
                schemaname,
                tablename,
                tableowner
            FROM pg_tables 
            WHERE schemaname = 'public'
            ORDER BY tablename;
        " "Listing database tables"
        ;;
    "vacuum")
        echo "🧹 Running database vacuum and analyze..."
        exec_sql "VACUUM ANALYZE;" "Vacuum and analyze database"
        ;;
    *)
        echo "🔧 Enhanced Song Database Cleanup Tool"
        echo ""
        echo "Usage: $0 {command} [options]"
        echo ""
        echo "Commands:"
        echo "  list                       List all studies with counts"
        echo "  delete <study_id>          Delete a specific study (with confirmation)"
        echo "  delete <study_id> --force  Delete a study without confirmation"
        echo "  delete-all                 Delete ALL studies (with confirmation)"
        echo "  count                      Count all records in database"
        echo "  info <study_id>            Get detailed study information"
        echo "  tables                     List all database tables"
        echo "  vacuum                     Run database vacuum and analyze"
        echo ""
        echo "Examples:"
        echo "  $0 list                    # List all studies"
        echo "  $0 delete study1           # Delete study1 with confirmation"
        echo "  $0 delete study1 --force   # Delete study1 without confirmation"
        echo "  $0 delete-all              # Delete everything"
        echo "  $0 count                   # Count all records"
        echo "  $0 info study1             # Get study1 details"
        exit 1
        ;;
esac
