#!/bin/bash
# Enhanced Folio Database Cleanup Script v2.0
# Updated for new RBAC-enabled database schema with UUIDs, organisation_id, privacy settings
# Provides robust operations for managing Folio database (pathogens, projects, studies)

NAMESPACE="agari"
POD_NAME=$(kubectl get pod -n $NAMESPACE -l app.kubernetes.io/name=folio-db -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [ -z "$POD_NAME" ]; then
    # Fallback to deployment name
    POD_NAME=$(kubectl get pod -n $NAMESPACE | grep folio-db | head -1 | awk '{print $1}')
fi

if [ -z "$POD_NAME" ]; then
    echo "❌ Error: Could not find folio-db pod in namespace $NAMESPACE"
    exit 1
fi

echo "🔗 Using pod: $POD_NAME"

# Function to execute SQL and handle errors
exec_sql() {
    local sql="$1"
    local description="$2"
    
    echo "📝 $description"
    if kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d folio -c "$sql" 2>&1; then
        echo "✅ $description completed successfully"
        return 0
    else
        echo "❌ $description failed"
        return 1
    fi
}

# Function to get pathogen info before deletion
get_pathogen_info() {
    local pathogen_id="$1"
    echo "📊 Getting pathogen information for: $pathogen_id"
    kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d folio -c "
        SELECT 
            p.id, 
            p.name, 
            p.scientific_name,
            p.description,
            p.created_at,
            COUNT(DISTINCT pr.id) as project_count,
            COUNT(DISTINCT s.id) as study_count
        FROM pathogens p
        LEFT JOIN projects pr ON p.id = pr.pathogen_id AND pr.deleted_at IS NULL
        LEFT JOIN studies s ON pr.id = s.project_id AND s.deleted_at IS NULL
        WHERE p.id = '$pathogen_id' AND p.deleted_at IS NULL
        GROUP BY p.id, p.name, p.scientific_name, p.description, p.created_at;
    "
}

# Function to get project info before deletion (with new RBAC fields)
get_project_info() {
    local project_id="$1"
    echo "📊 Getting project information for: $project_id"
    kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d folio -c "
        SELECT 
            pr.id, 
            pr.slug,
            pr.name, 
            pr.description,
            pr.organisation_id,
            pr.user_id,
            pr.privacy,
            pr.created_at,
            p.name as pathogen_name,
            COUNT(DISTINCT s.id) as study_count
        FROM projects pr
        LEFT JOIN pathogens p ON pr.pathogen_id = p.id AND p.deleted_at IS NULL
        LEFT JOIN studies s ON pr.id = s.project_id AND s.deleted_at IS NULL
        WHERE pr.id = '$project_id' AND pr.deleted_at IS NULL
        GROUP BY pr.id, pr.slug, pr.name, pr.description, pr.organisation_id, 
                 pr.user_id, pr.privacy, pr.created_at, p.name;
    "
}

# Function to get study info before deletion
get_study_info() {
    local study_id="$1"
    echo "📊 Getting study information for: $study_id"
    kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d folio -c "
        SELECT 
            s.id, 
            s.study_id,
            s.name, 
            s.description,
            s.start_date,
            s.end_date,
            s.created_at,
            pr.slug as project_code,
            pr.name as project_name,
            pr.organisation_id,
            pr.privacy as project_privacy,
            p.name as pathogen_name
        FROM studies s
        LEFT JOIN projects pr ON s.project_id = pr.id AND pr.deleted_at IS NULL
        LEFT JOIN pathogens p ON pr.pathogen_id = p.id AND p.deleted_at IS NULL
        WHERE s.id = '$study_id' AND s.deleted_at IS NULL;
    "
}

# Function to safely delete a pathogen (soft delete - checks for dependencies)
safe_delete_pathogen() {
    local pathogen_id="$1"
    local confirm="${2:-true}"
    
    # Get pathogen info first
    get_pathogen_info "$pathogen_id"
    
    if [ "$confirm" = "true" ]; then
        echo ""
        read -p "⚠️  Are you sure you want to delete pathogen '$pathogen_id' and ALL its projects/studies? (yes/no): " response
        if [ "$response" != "yes" ]; then
            echo "🚫 Delete cancelled."
            return 1
        fi
    fi
    
    echo "🗑️  Starting safe deletion of pathogen: $pathogen_id"
    
    # Execute deletion in correct order to handle foreign keys
    exec_sql "
        BEGIN;
        
        -- Soft delete studies first
        UPDATE studies 
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE project_id IN (SELECT id FROM projects WHERE pathogen_id = '$pathogen_id' AND deleted_at IS NULL)
        AND deleted_at IS NULL;
        
        -- Soft delete projects
        UPDATE projects 
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE pathogen_id = '$pathogen_id' AND deleted_at IS NULL;
        
        -- Soft delete pathogen
        UPDATE pathogens 
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE id = '$pathogen_id' AND deleted_at IS NULL;
        
        COMMIT;
    " "Soft deleting pathogen $pathogen_id with all related data"
}

# Function to safely delete a project (soft delete - checks for dependencies)
safe_delete_project() {
    local project_id="$1"
    local confirm="${2:-true}"
    
    # Get project info first
    get_project_info "$project_id"
    
    if [ "$confirm" = "true" ]; then
        echo ""
        read -p "⚠️  Are you sure you want to delete project '$project_id' and ALL its studies? (yes/no): " response
        if [ "$response" != "yes" ]; then
            echo "🚫 Delete cancelled."
            return 1
        fi
    fi
    
    echo "🗑️  Starting safe deletion of project: $project_id"
    
    # Execute deletion in correct order to handle foreign keys
    exec_sql "
        BEGIN;
        
        -- Soft delete studies first
        UPDATE studies 
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE project_id = '$project_id' AND deleted_at IS NULL;
        
        -- Soft delete project
        UPDATE projects 
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE id = '$project_id' AND deleted_at IS NULL;
        
        COMMIT;
    " "Soft deleting project $project_id with all related studies"
}

# Function to safely delete a study (soft delete)
safe_delete_study() {
    local study_id="$1"
    local confirm="${2:-true}"
    
    # Get study info first
    get_study_info "$study_id"
    
    if [ "$confirm" = "true" ]; then
        echo ""
        read -p "⚠️  Are you sure you want to delete study '$study_id'? (yes/no): " response
        if [ "$response" != "yes" ]; then
            echo "🚫 Delete cancelled."
            return 1
        fi
    fi
    
    echo "🗑️  Starting safe deletion of study: $study_id"
    
    exec_sql "
        UPDATE studies 
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE id = '$study_id' AND deleted_at IS NULL;
    " "Soft deleting study $study_id"
}

# Function to delete by organisation (new feature for RBAC)
delete_by_organisation() {
    local org_id="$1"
    local confirm="${2:-true}"
    
    echo "📊 Getting organisation data for: $org_id"
    kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d folio -c "
        SELECT 
            organisation_id,
            COUNT(DISTINCT id) as project_count,
            COUNT(DISTINCT CASE WHEN privacy = 'public' THEN id END) as public_projects,
            COUNT(DISTINCT CASE WHEN privacy = 'private' THEN id END) as private_projects
        FROM projects 
        WHERE organisation_id = '$org_id' AND deleted_at IS NULL
        GROUP BY organisation_id;
    "
    
    if [ "$confirm" = "true" ]; then
        echo ""
        read -p "⚠️  Are you sure you want to delete ALL projects and studies for organisation '$org_id'? (yes/no): " response
        if [ "$response" != "yes" ]; then
            echo "🚫 Delete cancelled."
            return 1
        fi
    fi
    
    echo "🗑️  Starting deletion of all data for organisation: $org_id"
    
    exec_sql "
        BEGIN;
        
        -- Soft delete studies for this organisation
        UPDATE studies 
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE project_id IN (SELECT id FROM projects WHERE organisation_id = '$org_id' AND deleted_at IS NULL)
        AND deleted_at IS NULL;
        
        -- Soft delete projects for this organisation
        UPDATE projects 
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE organisation_id = '$org_id' AND deleted_at IS NULL;
        
        COMMIT;
    " "Soft deleting all data for organisation $org_id"
}

# Function to delete by user (new feature for RBAC)
delete_by_user() {
    local user_id="$1"
    local confirm="${2:-true}"
    
    echo "📊 Getting user data for: $user_id"
    kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d folio -c "
        SELECT 
            user_id,
            organisation_id,
            COUNT(DISTINCT id) as project_count,
            COUNT(DISTINCT CASE WHEN privacy = 'public' THEN id END) as public_projects,
            COUNT(DISTINCT CASE WHEN privacy = 'private' THEN id END) as private_projects
        FROM projects 
        WHERE user_id = '$user_id' AND deleted_at IS NULL
        GROUP BY user_id, organisation_id;
    "
    
    if [ "$confirm" = "true" ]; then
        echo ""
        read -p "⚠️  Are you sure you want to delete ALL projects and studies created by user '$user_id'? (yes/no): " response
        if [ "$response" != "yes" ]; then
            echo "🚫 Delete cancelled."
            return 1
        fi
    fi
    
    echo "🗑️  Starting deletion of all data for user: $user_id"
    
    exec_sql "
        BEGIN;
        
        -- Soft delete studies for this user's projects
        UPDATE studies 
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE project_id IN (SELECT id FROM projects WHERE user_id = '$user_id' AND deleted_at IS NULL)
        AND deleted_at IS NULL;
        
        -- Soft delete projects for this user
        UPDATE projects 
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE user_id = '$user_id' AND deleted_at IS NULL;
        
        COMMIT;
    " "Soft deleting all data for user $user_id"
}

# Function to hard delete all soft-deleted records
purge_deleted_records() {
    local confirm="${1:-true}"
    
    if [ "$confirm" = "true" ]; then
        echo "⚠️  WARNING: This will permanently delete ALL soft-deleted records!"
        read -p "Type 'PURGE ALL' to confirm: " response
        if [ "$response" != "PURGE ALL" ]; then
            echo "🚫 Purge cancelled."
            return 1
        fi
    fi
    
    echo "🗑️  Purging all soft-deleted records..."
    
    exec_sql "
        BEGIN;
        
        -- Hard delete soft-deleted studies
        DELETE FROM studies WHERE deleted_at IS NOT NULL;
        
        -- Hard delete soft-deleted projects 
        DELETE FROM projects WHERE deleted_at IS NOT NULL;
        
        -- Hard delete soft-deleted pathogens
        DELETE FROM pathogens WHERE deleted_at IS NOT NULL;
        
        COMMIT;
    " "Purging all soft-deleted records"
}

# Function to completely wipe all data (hard reset)
wipe_all_data() {
    local confirm="${1:-true}"
    
    if [ "$confirm" = "true" ]; then
        echo "⚠️  WARNING: This will PERMANENTLY DELETE ALL DATA in the Folio database!"
        echo "📊 Current data count:"
        exec_sql "
            SELECT 'PATHOGENS' as table_name, COUNT(*) as count FROM pathogens
            UNION ALL SELECT 'PROJECTS', COUNT(*) FROM projects
            UNION ALL SELECT 'STUDIES', COUNT(*) FROM studies;
        " "Getting current data count"
        
        echo ""
        echo "🔥 This action will:"
        echo "   - DELETE all studies (including active ones)"
        echo "   - DELETE all projects (including active ones)" 
        echo "   - DELETE all pathogens (including active ones)"
        echo "   - This is a HARD DELETE - data cannot be recovered!"
        echo ""
        read -p "Type 'WIPE ALL DATA' to confirm complete database wipe: " response
        
        if [ "$response" != "WIPE ALL DATA" ]; then
            echo "🚫 Database wipe cancelled."
            return 1
        fi
    fi
    
    echo "🔥 Wiping ALL data from Folio database..."
    
    exec_sql "
        BEGIN;
        
        -- Hard delete ALL studies (active and deleted)
        DELETE FROM studies;
        
        -- Hard delete ALL projects (active and deleted)
        DELETE FROM projects;
        
        -- Hard delete ALL pathogens (active and deleted)
        DELETE FROM pathogens;
        
        COMMIT;
    " "Wiping all data from database"
    
    echo "✅ Database completely wiped!"
    echo "📊 Verifying database is empty:"
    exec_sql "
        SELECT 'PATHOGENS' as table_name, COUNT(*) as count FROM pathogens
        UNION ALL SELECT 'PROJECTS', COUNT(*) FROM projects
        UNION ALL SELECT 'STUDIES', COUNT(*) FROM studies;
    " "Verifying empty database"
}

case "$1" in
    "list")
        echo "📋 Listing all active records:"
        exec_sql "
            -- Active Pathogens
            SELECT 'PATHOGENS' as type, '' as id, '' as name, '' as details;
            SELECT 
                'pathogen' as type,
                p.id::text, 
                p.name, 
                CONCAT(p.scientific_name, ' | Projects: ', COUNT(DISTINCT pr.id), ' | Studies: ', COUNT(DISTINCT s.id)) as details
            FROM pathogens p
            LEFT JOIN projects pr ON p.id = pr.pathogen_id AND pr.deleted_at IS NULL
            LEFT JOIN studies s ON pr.id = s.project_id AND s.deleted_at IS NULL
            WHERE p.deleted_at IS NULL
            GROUP BY p.id, p.name, p.scientific_name
            ORDER BY p.name;
            
            SELECT '' as type, '' as id, '' as name, '' as details;
            SELECT 'PROJECTS' as type, '' as id, '' as name, '' as details;
            SELECT 
                'project' as type,
                pr.id::text, 
                CONCAT(pr.slug, ' - ', pr.name) as name,
                CONCAT('Org: ', pr.organisation_id, ' | Privacy: ', pr.privacy, ' | Pathogen: ', COALESCE(p.name, 'None'), ' | Studies: ', COUNT(DISTINCT s.id)) as details
            FROM projects pr
            LEFT JOIN pathogens p ON pr.pathogen_id = p.id AND p.deleted_at IS NULL
            LEFT JOIN studies s ON pr.id = s.project_id AND s.deleted_at IS NULL
            WHERE pr.deleted_at IS NULL
            GROUP BY pr.id, pr.slug, pr.name, pr.organisation_id, pr.privacy, p.name
            ORDER BY pr.organisation_id, pr.slug;
            
            SELECT '' as type, '' as id, '' as name, '' as details;
            SELECT 'STUDIES' as type, '' as id, '' as name, '' as details;
            SELECT 
                'study' as type,
                s.id::text, 
                s.name,
                CONCAT('Study ID: ', s.study_id, ' | Project: ', COALESCE(pr.slug, 'None'), ' | Org: ', COALESCE(pr.organisation_id, 'None')) as details
            FROM studies s
            LEFT JOIN projects pr ON s.project_id = pr.id AND pr.deleted_at IS NULL
            WHERE s.deleted_at IS NULL
            ORDER BY s.name;
        " "Listing active records"
        ;;
    "list-by-org")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 list-by-org <organisation_id>"
            exit 1
        fi
        ORG_ID="$2"
        echo "📋 Listing all records for organisation: $ORG_ID"
        exec_sql "
            SELECT 
                'project' as type,
                pr.id::text, 
                CONCAT(pr.slug, ' - ', pr.name) as name,
                CONCAT('User: ', pr.user_id, ' | Privacy: ', pr.privacy, ' | Studies: ', COUNT(DISTINCT s.id)) as details
            FROM projects pr
            LEFT JOIN studies s ON pr.id = s.project_id AND s.deleted_at IS NULL
            WHERE pr.organisation_id = '$ORG_ID' AND pr.deleted_at IS NULL
            GROUP BY pr.id, pr.slug, pr.name, pr.user_id, pr.privacy
            ORDER BY pr.slug;
        " "Listing records for organisation $ORG_ID"
        ;;
    "list-by-user")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 list-by-user <user_id>"
            exit 1
        fi
        USER_ID="$2"
        echo "📋 Listing all records for user: $USER_ID"
        exec_sql "
            SELECT 
                'project' as type,
                pr.id::text, 
                CONCAT(pr.slug, ' - ', pr.name) as name,
                CONCAT('Org: ', pr.organisation_id, ' | Privacy: ', pr.privacy, ' | Studies: ', COUNT(DISTINCT s.id)) as details
            FROM projects pr
            LEFT JOIN studies s ON pr.id = s.project_id AND s.deleted_at IS NULL
            WHERE pr.user_id = '$USER_ID' AND pr.deleted_at IS NULL
            GROUP BY pr.id, pr.slug, pr.name, pr.organisation_id, pr.privacy
            ORDER BY pr.slug;
        " "Listing records for user $USER_ID"
        ;;
    "list-privacy")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 list-privacy <public|private>"
            exit 1
        fi
        PRIVACY="$2"
        echo "📋 Listing all $PRIVACY projects:"
        exec_sql "
            SELECT 
                pr.id::text, 
                pr.slug,
                pr.name,
                pr.organisation_id,
                pr.user_id,
                COUNT(DISTINCT s.id) as studies
            FROM projects pr
            LEFT JOIN studies s ON pr.id = s.project_id AND s.deleted_at IS NULL
            WHERE pr.privacy = '$PRIVACY' AND pr.deleted_at IS NULL
            GROUP BY pr.id, pr.slug, pr.name, pr.organisation_id, pr.user_id
            ORDER BY pr.organisation_id, pr.slug;
        " "Listing $PRIVACY projects"
        ;;
    "list-deleted")
        echo "📋 Listing all soft-deleted records:"
        exec_sql "
            -- Soft-deleted records
            SELECT 'DELETED PATHOGENS' as type, '' as id, '' as name, '' as deleted_at;
            SELECT 
                'pathogen' as type,
                id::text, 
                name,
                deleted_at::text
            FROM pathogens 
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC;
            
            SELECT '' as type, '' as id, '' as name, '' as deleted_at;
            SELECT 'DELETED PROJECTS' as type, '' as id, '' as name, '' as deleted_at;
            SELECT 
                'project' as type,
                id::text, 
                CONCAT(slug, ' - ', name, ' (', organisation_id, ')') as name,
                deleted_at::text
            FROM projects 
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC;
            
            SELECT '' as type, '' as id, '' as name, '' as deleted_at;
            SELECT 'DELETED STUDIES' as type, '' as id, '' as name, '' as deleted_at;
            SELECT 
                'study' as type,
                id::text, 
                CONCAT(study_id, ' - ', name) as name,
                deleted_at::text
            FROM studies 
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC;
        " "Listing soft-deleted records"
        ;;
    "delete-pathogen")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 delete-pathogen <pathogen_id> [--force]"
            exit 1
        fi
        PATHOGEN_ID="$2"
        FORCE_FLAG="$3"
        
        if [ "$FORCE_FLAG" = "--force" ]; then
            safe_delete_pathogen "$PATHOGEN_ID" false
        else
            safe_delete_pathogen "$PATHOGEN_ID" true
        fi
        ;;
    "delete-project")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 delete-project <project_id> [--force]"
            exit 1
        fi
        PROJECT_ID="$2"
        FORCE_FLAG="$3"
        
        if [ "$FORCE_FLAG" = "--force" ]; then
            safe_delete_project "$PROJECT_ID" false
        else
            safe_delete_project "$PROJECT_ID" true
        fi
        ;;
    "delete-study")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 delete-study <study_id> [--force]"
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
    "delete-by-org")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 delete-by-org <organisation_id> [--force]"
            exit 1
        fi
        ORG_ID="$2"
        FORCE_FLAG="$3"
        
        if [ "$FORCE_FLAG" = "--force" ]; then
            delete_by_organisation "$ORG_ID" false
        else
            delete_by_organisation "$ORG_ID" true
        fi
        ;;
    "delete-by-user")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 delete-by-user <user_id> [--force]"
            exit 1
        fi
        USER_ID="$2"
        FORCE_FLAG="$3"
        
        if [ "$FORCE_FLAG" = "--force" ]; then
            delete_by_user "$USER_ID" false
        else
            delete_by_user "$USER_ID" true
        fi
        ;;
    "delete-all")
        echo "⚠️  WARNING: This will soft-delete ALL pathogens, projects, and studies!"
        read -p "Type 'DELETE ALL' to confirm: " confirm
        if [ "$confirm" = "DELETE ALL" ]; then
            echo "🗑️  Soft-deleting all data..."
            exec_sql "
                BEGIN;
                -- Soft delete in dependency order
                UPDATE studies SET deleted_at = NOW(), updated_at = NOW() WHERE deleted_at IS NULL;
                UPDATE projects SET deleted_at = NOW(), updated_at = NOW() WHERE deleted_at IS NULL;
                UPDATE pathogens SET deleted_at = NOW(), updated_at = NOW() WHERE deleted_at IS NULL;
                COMMIT;
            " "Soft-deleting all records"
        else
            echo "🚫 Operation cancelled."
        fi
        ;;
    "purge")
        purge_deleted_records true
        ;;
    "purge-force")
        purge_deleted_records false
        ;;
    "wipe")
        wipe_all_data true
        ;;
    "wipe-force")
        wipe_all_data false
        ;;
    "restore-pathogen")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 restore-pathogen <pathogen_id>"
            exit 1
        fi
        PATHOGEN_ID="$2"
        echo "♻️  Restoring pathogen: $PATHOGEN_ID"
        exec_sql "
            UPDATE pathogens 
            SET deleted_at = NULL, updated_at = NOW()
            WHERE id = '$PATHOGEN_ID' AND deleted_at IS NOT NULL;
        " "Restoring pathogen $PATHOGEN_ID"
        ;;
    "restore-project")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 restore-project <project_id>"
            exit 1
        fi
        PROJECT_ID="$2"
        echo "♻️  Restoring project: $PROJECT_ID"
        exec_sql "
            UPDATE projects 
            SET deleted_at = NULL, updated_at = NOW()
            WHERE id = '$PROJECT_ID' AND deleted_at IS NOT NULL;
        " "Restoring project $PROJECT_ID"
        ;;
    "restore-study")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 restore-study <study_id>"
            exit 1
        fi
        STUDY_ID="$2"
        echo "♻️  Restoring study: $STUDY_ID"
        exec_sql "
            UPDATE studies 
            SET deleted_at = NULL, updated_at = NOW()
            WHERE id = '$STUDY_ID' AND deleted_at IS NOT NULL;
        " "Restoring study $STUDY_ID"
        ;;
    "count")
        echo "📊 Counting database records:"
        exec_sql "
            SELECT 
                'Active Records' as category,
                (SELECT COUNT(*) FROM pathogens WHERE deleted_at IS NULL) AS pathogens,
                (SELECT COUNT(*) FROM projects WHERE deleted_at IS NULL) AS projects,
                (SELECT COUNT(*) FROM studies WHERE deleted_at IS NULL) AS studies;
            
            SELECT 
                'Deleted Records' as category,
                (SELECT COUNT(*) FROM pathogens WHERE deleted_at IS NOT NULL) AS pathogens,
                (SELECT COUNT(*) FROM projects WHERE deleted_at IS NOT NULL) AS projects,
                (SELECT COUNT(*) FROM studies WHERE deleted_at IS NOT NULL) AS studies;
            
            SELECT 
                'Total Records' as category,
                (SELECT COUNT(*) FROM pathogens) AS pathogens,
                (SELECT COUNT(*) FROM projects) AS projects,
                (SELECT COUNT(*) FROM studies) AS studies;
        " "Getting record counts"
        ;;
    "count-by-org")
        echo "📊 Counting records by organisation:"
        exec_sql "
            SELECT 
                organisation_id,
                COUNT(*) as total_projects,
                COUNT(CASE WHEN privacy = 'public' THEN 1 END) as public_projects,
                COUNT(CASE WHEN privacy = 'private' THEN 1 END) as private_projects,
                COUNT(CASE WHEN deleted_at IS NULL THEN 1 END) as active_projects,
                COUNT(CASE WHEN deleted_at IS NOT NULL THEN 1 END) as deleted_projects
            FROM projects 
            GROUP BY organisation_id
            ORDER BY organisation_id;
        " "Getting record counts by organisation"
        ;;
    "info-pathogen")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 info-pathogen <pathogen_id>"
            exit 1
        fi
        get_pathogen_info "$2"
        ;;
    "info-project")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 info-project <project_id>"
            exit 1
        fi
        get_project_info "$2"
        ;;
    "info-study")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 info-study <study_id>"
            exit 1
        fi
        get_study_info "$2"
        ;;
    "schema")
        echo "🗂️  Database schema information:"
        exec_sql "
            SELECT 
                table_name,
                column_name,
                data_type,
                is_nullable,
                column_default
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name IN ('pathogens', 'projects', 'studies')
            ORDER BY table_name, ordinal_position;
        " "Listing database schema"
        ;;
    "vacuum")
        echo "🧹 Running database vacuum and analyze..."
        exec_sql "VACUUM ANALYZE;" "Vacuum and analyze database"
        ;;
    *)
        echo "🔧 Enhanced Folio Database Cleanup Tool v2.0 (RBAC Edition)"
        echo ""
        echo "Usage: $0 {command} [options]"
        echo ""
        echo "📋 Listing Commands:"
        echo "  list                           List all active records"
        echo "  list-by-org <org_id>           List records for specific organisation"
        echo "  list-by-user <user_id>         List records for specific user"
        echo "  list-privacy <public|private>  List projects by privacy setting"
        echo "  list-deleted                   List all soft-deleted records"
        echo "  count                          Count all records (active/deleted/total)"
        echo "  count-by-org                   Count records by organisation"
        echo ""
        echo "🗑️  Deletion Commands (soft delete):"
        echo "  delete-pathogen <id>           Delete a pathogen (with confirmation)"
        echo "  delete-pathogen <id> --force   Delete a pathogen without confirmation"
        echo "  delete-project <id>            Delete a project (with confirmation)"
        echo "  delete-project <id> --force    Delete a project without confirmation"
        echo "  delete-study <id>              Delete a study (with confirmation)"
        echo "  delete-study <id> --force      Delete a study without confirmation"
        echo "  delete-by-org <org_id>         Delete all data for an organisation"
        echo "  delete-by-user <user_id>       Delete all data created by a user"
        echo "  delete-all                     Soft-delete ALL records (with confirmation)"
        echo ""
        echo "♻️  Restoration Commands:"
        echo "  restore-pathogen <id>          Restore a soft-deleted pathogen"
        echo "  restore-project <id>           Restore a soft-deleted project"
        echo "  restore-study <id>             Restore a soft-deleted study"
        echo ""
        echo "💥 Hard Deletion Commands:"
        echo "  purge                          Hard delete all soft-deleted records (with confirmation)"
        echo "  purge-force                    Hard delete all soft-deleted records without confirmation"
        echo "  wipe                           COMPLETE DATABASE WIPE - delete ALL data (with confirmation)"
        echo "  wipe-force                     COMPLETE DATABASE WIPE - delete ALL data without confirmation"
        echo ""
        echo "ℹ️  Information Commands:"
        echo "  info-pathogen <id>             Get detailed pathogen information"
        echo "  info-project <id>              Get detailed project information"
        echo "  info-study <id>                Get detailed study information"
        echo "  schema                         Show database schema"
        echo "  vacuum                         Run database vacuum and analyze"
        echo ""
        echo "🔐 RBAC Examples:"
        echo "  $0 list-by-org org1           # List all projects for org1"
        echo "  $0 list-by-user user123       # List all projects created by user123"
        echo "  $0 list-privacy private       # List all private projects"
        echo "  $0 delete-by-org org1         # Delete all data for org1"
        echo "  $0 count-by-org               # Count projects by organisation"
        echo ""
        echo "Examples:"
        echo "  $0 list                        # List all active records"
        echo "  $0 delete-project abc-123      # Delete project with UUID confirmation"
        echo "  $0 delete-study xyz-789 --force # Delete study without confirmation"
        echo "  $0 restore-pathogen path-001   # Restore a deleted pathogen"
        echo "  $0 purge                       # Permanently delete all soft-deleted records"
        echo "  $0 wipe                        # COMPLETE DATABASE RESET - delete everything!"
        echo "  $0 count                       # Count all records"
        exit 1
        ;;
esac
