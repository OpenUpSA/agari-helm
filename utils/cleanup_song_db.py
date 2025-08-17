#!/usr/bin/env python3
"""
SONG Database Cleanup Utility

This script provides utilities to clean up SONG database tables.
Use with caution - this will permanently delete data!

Usage:
    python cleanup_song_db.py --help
    python cleanup_song_db.py --list-studies
    python cleanup_song_db.py --delete-study study1
    python cleanup_song_db.py --delete-all-studies
    python cleanup_song_db.py --vacuum
"""

import argparse
import sys
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import json

# Database connection settings
DB_CONFIG = {
    'host': 'localhost',
    'port': 5433,  # Port forwarded from song-db service
    'database': 'songDb',
    'user': 'admin',
    'password': 'song-db-pass-123'
}

def get_db_connection():
    """Get database connection"""
    try:
        conn = psycopg2.connect(
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            database=DB_CONFIG['database'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            sslmode='prefer'
        )
        return conn
    except psycopg2.Error as e:
        print(f"Error connecting to database: {e}")
        sys.exit(1)

def list_studies():
    """List all studies in the database"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT study_id, name, description, organization, created_at
                FROM study 
                ORDER BY created_at DESC
            """)
            studies = cur.fetchall()
            
            if not studies:
                print("No studies found in database.")
                return
                
            print(f"Found {len(studies)} studies:")
            print("-" * 80)
            for study in studies:
                print(f"Study ID: {study['study_id']}")
                print(f"Name: {study['name']}")
                print(f"Description: {study['description']}")
                print(f"Organization: {study['organization']}")
                print(f"Created: {study['created_at']}")
                print("-" * 80)
                
    except psycopg2.Error as e:
        print(f"Error listing studies: {e}")
    finally:
        conn.close()

def get_study_info(study_id):
    """Get detailed information about a study"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get study info
            cur.execute("SELECT * FROM study WHERE study_id = %s", (study_id,))
            study = cur.fetchone()
            
            if not study:
                print(f"Study '{study_id}' not found.")
                return None
                
            # Get analysis count
            cur.execute("SELECT COUNT(*) as count FROM analysis WHERE study_id = %s", (study_id,))
            analysis_count = cur.fetchone()['count']
            
            # Get file count
            cur.execute("""
                SELECT COUNT(*) as count 
                FROM file f 
                JOIN analysis a ON f.analysis_id = a.id 
                WHERE a.study_id = %s
            """, (study_id,))
            file_count = cur.fetchone()['count']
            
            return {
                'study': study,
                'analysis_count': analysis_count,
                'file_count': file_count
            }
            
    except psycopg2.Error as e:
        print(f"Error getting study info: {e}")
        return None
    finally:
        conn.close()

def delete_study(study_id, confirm=True):
    """Delete a study and all its related data"""
    
    # Get study info first
    study_info = get_study_info(study_id)
    if not study_info:
        return False
        
    print(f"\nStudy to delete: {study_id}")
    print(f"Name: {study_info['study']['name']}")
    print(f"Analyses: {study_info['analysis_count']}")
    print(f"Files: {study_info['file_count']}")
    
    if confirm:
        response = input(f"\nAre you sure you want to delete study '{study_id}' and ALL its data? (yes/no): ")
        if response.lower() != 'yes':
            print("Delete cancelled.")
            return False
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            print(f"Deleting study '{study_id}' and all related data...")
            
            # Delete in correct order due to foreign key constraints
            # 1. Delete files first
            cur.execute("""
                DELETE FROM file 
                WHERE analysis_id IN (
                    SELECT id FROM analysis WHERE study_id = %s
                )
            """, (study_id,))
            deleted_files = cur.rowcount
            print(f"Deleted {deleted_files} files")
            
            # 2. Delete samples
            cur.execute("""
                DELETE FROM sample 
                WHERE analysis_id IN (
                    SELECT id FROM analysis WHERE study_id = %s
                )
            """, (study_id,))
            deleted_samples = cur.rowcount
            print(f"Deleted {deleted_samples} samples")
            
            # 3. Delete analyses
            cur.execute("DELETE FROM analysis WHERE study_id = %s", (study_id,))
            deleted_analyses = cur.rowcount
            print(f"Deleted {deleted_analyses} analyses")
            
            # 4. Delete study
            cur.execute("DELETE FROM study WHERE study_id = %s", (study_id,))
            deleted_studies = cur.rowcount
            print(f"Deleted {deleted_studies} study")
            
            conn.commit()
            print(f"Successfully deleted study '{study_id}' and all related data.")
            return True
            
    except psycopg2.Error as e:
        conn.rollback()
        print(f"Error deleting study: {e}")
        return False
    finally:
        conn.close()

def delete_all_studies(confirm=True):
    """Delete ALL studies from the database"""
    
    if confirm:
        response = input("Are you sure you want to delete ALL studies and data? This cannot be undone! (yes/no): ")
        if response.lower() != 'yes':
            print("Delete cancelled.")
            return False
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            print("Deleting ALL studies and data...")
            
            # Delete in correct order
            cur.execute("DELETE FROM file")
            deleted_files = cur.rowcount
            print(f"Deleted {deleted_files} files")
            
            cur.execute("DELETE FROM sample")
            deleted_samples = cur.rowcount
            print(f"Deleted {deleted_samples} samples")
            
            cur.execute("DELETE FROM analysis")
            deleted_analyses = cur.rowcount
            print(f"Deleted {deleted_analyses} analyses")
            
            cur.execute("DELETE FROM study")
            deleted_studies = cur.rowcount
            print(f"Deleted {deleted_studies} studies")
            
            conn.commit()
            print("Successfully deleted all data.")
            return True
            
    except psycopg2.Error as e:
        conn.rollback()
        print(f"Error deleting all data: {e}")
        return False
    finally:
        conn.close()

def vacuum_database():
    """Run VACUUM on the database to reclaim space"""
    conn = get_db_connection()
    conn.autocommit = True  # VACUUM requires autocommit
    
    try:
        with conn.cursor() as cur:
            print("Running VACUUM on database...")
            cur.execute("VACUUM ANALYZE")
            print("VACUUM completed successfully.")
            
    except psycopg2.Error as e:
        print(f"Error running VACUUM: {e}")
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description='SONG Database Cleanup Utility')
    parser.add_argument('--list-studies', action='store_true', help='List all studies')
    parser.add_argument('--delete-study', metavar='STUDY_ID', help='Delete a specific study')
    parser.add_argument('--delete-all-studies', action='store_true', help='Delete ALL studies (dangerous!)')
    parser.add_argument('--vacuum', action='store_true', help='Run VACUUM on database')
    parser.add_argument('--force', action='store_true', help='Skip confirmation prompts')
    
    args = parser.parse_args()
    
    if not any([args.list_studies, args.delete_study, args.delete_all_studies, args.vacuum]):
        parser.print_help()
        return
    
    # Check if we can connect to the database
    print("Checking database connection...")
    try:
        conn = get_db_connection()
        conn.close()
        print("Database connection successful.")
    except:
        print("Failed to connect to database.")
        print("Make sure you have port-forwarded the song-db service:")
        print("kubectl port-forward -n agari-dev svc/song-db 5433:5432")
        sys.exit(1)
    
    if args.list_studies:
        list_studies()
    
    if args.delete_study:
        delete_study(args.delete_study, confirm=not args.force)
    
    if args.delete_all_studies:
        delete_all_studies(confirm=not args.force)
    
    if args.vacuum:
        vacuum_database()

if __name__ == '__main__':
    main()
