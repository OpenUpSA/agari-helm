#!/bin/bash
# Quick script to list and delete studies using kubectl exec

NAMESPACE="agari-dev"
POD_NAME=$(kubectl get pod -n $NAMESPACE -l app.kubernetes.io/name=song-db -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [ -z "$POD_NAME" ]; then
    # Fallback to deployment name
    POD_NAME=$(kubectl get pod -n $NAMESPACE | grep song-db | head -1 | awk '{print $1}')
fi

if [ -z "$POD_NAME" ]; then
    echo "Error: Could not find song-db pod in namespace $NAMESPACE"
    exit 1
fi

echo "Using pod: $POD_NAME"

case "$1" in
    "list")
        echo "Listing all studies:"
        kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d songDb -c "
            SELECT id as study_id, name, description, organization 
            FROM study 
            ORDER BY name;
        "
        ;;
    "delete")
        if [ -z "$2" ]; then
            echo "Usage: $0 delete <study_id>"
            exit 1
        fi
        STUDY_ID="$2"
        echo "Deleting study: $STUDY_ID"
        kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d songDb -c "
            BEGIN;
            -- Delete files first
            DELETE FROM file WHERE analysis_id IN (SELECT id FROM analysis WHERE study_id = '$STUDY_ID');
            -- Delete samplesets 
            DELETE FROM sampleset WHERE analysis_id IN (SELECT id FROM analysis WHERE study_id = '$STUDY_ID');
            -- Delete analyses
            DELETE FROM analysis WHERE study_id = '$STUDY_ID';
            -- Delete study
            DELETE FROM study WHERE id = '$STUDY_ID';
            COMMIT;
        "
        echo "Study $STUDY_ID deleted successfully."
        ;;
    "count")
        echo "Counting records:"
        kubectl exec -n $NAMESPACE $POD_NAME -- psql -U admin -d songDb -c "
            SELECT 
                (SELECT COUNT(*) FROM study) AS studies,
                (SELECT COUNT(*) FROM analysis) AS analyses,
                (SELECT COUNT(*) FROM file) AS files,
                (SELECT COUNT(*) FROM sample) AS samples;
        "
        ;;
    *)
        echo "Usage: $0 {list|delete <study_id>|count}"
        echo "Examples:"
        echo "  $0 list                    # List all studies"
        echo "  $0 delete study1          # Delete study1"
        echo "  $0 count                  # Count all records"
        exit 1
        ;;
esac
