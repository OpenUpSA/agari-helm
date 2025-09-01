from flask import Flask, request, jsonify, g
from flask_restx import Api, Resource, fields, Namespace
import logging
import requests
import os
import jwt
from functools import wraps
import time
import psycopg2
from psycopg2.extras import RealDictCursor, Json
import uuid
from datetime import datetime
import traceback
import json


def serialize_datetime(obj):
    """Convert datetime objects to ISO format strings for JSON serialization"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def serialize_record(record):
    """Convert a database record to a JSON-serializable dictionary"""
    if not record:
        return None
    
    result = {}
    for key, value in record.items():
        result[key] = serialize_datetime(value)
    return result

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Keycloak configuration from environment variables
KEYCLOAK_HOST = os.getenv("KEYCLOAK_HOST", "http://keycloak:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "agari")
KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", f"{KEYCLOAK_HOST}/realms/{KEYCLOAK_REALM}")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "dms")
KEYCLOAK_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "")
KEYCLOAK_PERMISSION_URI = f"{KEYCLOAK_HOST}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"

# Keycloak Admin API endpoints - should use UMA Resource Server instead
KEYCLOAK_ADMIN_TOKEN_URI = f"{KEYCLOAK_HOST}/realms/master/protocol/openid-connect/token"
KEYCLOAK_ADMIN_BASE_URI = f"{KEYCLOAK_HOST}/admin/realms/{KEYCLOAK_REALM}"
KEYCLOAK_ADMIN_CLIENT_ID = "admin-cli"

# UMA Resource Server endpoints (proper way for DMS client)
KEYCLOAK_UMA_RESOURCE_URI = f"{KEYCLOAK_HOST}/realms/{KEYCLOAK_REALM}/authz/protection/resource_set"

# Database configuration
DB_HOST = os.getenv("DB_HOST", "folio-db")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "folio")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "admin")

app = Flask(__name__)

# Initialize Flask-RESTX for Swagger documentation
api = Api(
    app,
    version='1.0',
    title='Folio API',
    description='JWT Authentication and Group Management API for AGARI Genomics Data Management',
    doc='/docs/',  # Swagger UI will be available at /docs/
    authorizations={
        'Bearer': {
            'type': 'apiKey',
            'in': 'header',
            'name': 'Authorization',
            'description': 'JWT Bearer token. Format: Bearer <token>'
        }
    },
    security='Bearer'
)

# Create API namespaces
health_ns = api.namespace('health', description='Health check operations')
auth_ns = api.namespace('auth', description='Authentication test operations') 
projects_ns = api.namespace('projects', description='Project management operations')
pathogens_ns = api.namespace('pathogens', description='Pathogen management operations')
studies_ns = api.namespace('studies', description='Study management operations')

# Define data models for Swagger documentation
user_model = api.model('User', {
    'username': fields.String(description='Username'),
    'email': fields.String(description='Email address'),
    'sub': fields.String(description='User ID'),
    'permissions': fields.List(fields.String, description='User permissions'),
    'folio_permissions': fields.List(fields.String, description='Folio-specific permissions')
})

group_model = api.model('Group', {
    'id': fields.String(description='Group ID'),
    'name': fields.String(description='Group name'),
    'path': fields.String(description='Group path'),
    'attributes': fields.Raw(description='Group attributes')
})

member_model = api.model('Member', {
    'id': fields.String(description='User ID'),
    'username': fields.String(description='Username'),
    'email': fields.String(description='Email address'),
    'firstName': fields.String(description='First name'),
    'lastName': fields.String(description='Last name'),
    'enabled': fields.Boolean(description='Account enabled status')
})

resource_model = api.model('Resource', {
    '_id': fields.String(description='Resource ID'),
    'name': fields.String(description='Resource name'),
    'displayName': fields.String(description='Resource display name'),
    'type': fields.String(description='Resource type'),
    'scopes': fields.List(fields.String, description='Available scopes')
})

error_model = api.model('Error', {
    'error': fields.String(description='Error message'),
    'user_permissions': fields.List(fields.String, description='Current user permissions'),
    'rpt_permissions': fields.List(fields.Raw, description='Raw RPT permissions')
})

pathogen_model = api.model('Pathogen', {
    'id': fields.String(description='Pathogen UUID', readonly=True),
    'name': fields.String(required=True, description='Pathogen name'),
    'scientific_name': fields.String(description='Scientific name'),
    'description': fields.String(description='Pathogen description'),
    'created_at': fields.DateTime(description='Creation timestamp', readonly=True),
    'updated_at': fields.DateTime(description='Last update timestamp', readonly=True)
})

pathogen_input_model = api.model('PathogenInput', {
    'name': fields.String(required=True, description='Pathogen name'),
    'scientific_name': fields.String(description='Scientific name'),
    'description': fields.String(description='Pathogen description')
})

project_model = api.model('Project', {
    'id': fields.String(description='Project UUID', readonly=True),
    'slug': fields.String(required=True, description='Project slug/identifier'),
    'name': fields.String(required=True, description='Project name'),
    'description': fields.String(description='Project description'),
    'organization_id': fields.String(description='Organization ID from Keycloak', readonly=True),
    'user_id': fields.String(description='User ID from Keycloak (creator)', readonly=True),
    'status': fields.String(description='Project status'),
    'pathogen_id': fields.String(description='Associated pathogen UUID'),
    'pathogen_name': fields.String(description='Pathogen name', readonly=True),
    'created_at': fields.DateTime(description='Creation timestamp', readonly=True),
    'updated_at': fields.DateTime(description='Last update timestamp', readonly=True),
    'deleted_at': fields.DateTime(description='Deletion timestamp', readonly=True)
})

project_input_model = api.model('ProjectInput', {
    'slug': fields.String(required=True, description='Project slug/identifier'),
    'name': fields.String(required=True, description='Project name'),
    'description': fields.String(description='Project description'),
    'pathogen_id': fields.String(required=True, description='Associated pathogen UUID')
})

study_model = api.model('Study', {
    'id': fields.String(description='Study UUID', readonly=True),
    'study_id': fields.String(required=True, description='Study identifier'),
    'name': fields.String(required=True, description='Study name'),
    'description': fields.String(description='Study description'),
    'project_id': fields.String(required=True, description='Associated project UUID'),
    'project_slug': fields.String(description='Project slug', readonly=True),
    'start_date': fields.Date(description='Study start date'),
    'end_date': fields.Date(description='Study end date'),
    'status': fields.String(description='Study status'),
    'song_created': fields.Boolean(description='Whether study was created in SONG', readonly=True),
    'created_at': fields.DateTime(description='Creation timestamp', readonly=True),
    'updated_at': fields.DateTime(description='Last update timestamp', readonly=True),
    'deleted_at': fields.DateTime(description='Deletion timestamp', readonly=True)
})

study_input_model = api.model('StudyInput', {
    'study_id': fields.String(required=True, description='Study identifier'),
    'name': fields.String(required=True, description='Study name'), 
    'description': fields.String(description='Study description'),
    'project_id': fields.String(required=True, description='Associated project UUID'),
    'start_date': fields.Date(description='Study start date'),
    'end_date': fields.Date(description='Study end date')
})


def get_service_token():
    """Get a service token from Keycloak for admin operations"""
    try:
        logger.info("Getting service token from Keycloak")
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'grant_type': 'client_credentials',
            'client_id': KEYCLOAK_CLIENT_ID,
            'client_secret': KEYCLOAK_CLIENT_SECRET
        }
        
        response = requests.post(KEYCLOAK_PERMISSION_URI, headers=headers, data=data, timeout=10)
        response.raise_for_status()
        
        token_data = response.json()
        access_token = token_data.get('access_token')
        
        if access_token:
            logger.info("Successfully obtained service token")
            return access_token
        else:
            logger.error("No access token in response")
            return None
            
    except Exception as e:
        logger.error(f"Failed to get service token: {e}")
        return None


def get_dms_client_token():
    """Get client credentials token for DMS client (service-to-service auth)"""
    try:
        logger.info("=== Getting DMS client credentials token for resource management ===")
        data = {
            'grant_type': 'client_credentials',
            'client_id': KEYCLOAK_CLIENT_ID,  # Use DMS client
            'client_secret': KEYCLOAK_CLIENT_SECRET,  # Use DMS client secret
        }
        
        response = requests.post(KEYCLOAK_PERMISSION_URI, data=data, timeout=10)
        response.raise_for_status()
        
        token_data = response.json()
        logger.info(f"Got DMS client credentials token for resource management")
        return token_data.get('access_token')
        
    except Exception as e:
        logger.error(f"Failed to get DMS client credentials token: {e}")
        return None


def get_dms_client_id():
    """Get the internal client ID for the DMS client
    
    For now, we'll try to fetch it, but if that fails due to permissions,
    we'll need to find an alternative approach.
    """
    try:
        service_token = get_service_token()
        if not service_token:
            return None
            
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        # Try to get all clients and find DMS - this may fail due to permissions
        response = requests.get(f"{KEYCLOAK_ADMIN_BASE_URI}/clients", headers=headers, timeout=10)
        
        if response.status_code == 403:
            logger.warning("Service account doesn't have admin permissions to list clients")
            logger.info("Need to grant realm-admin role to service-account-dms or use alternative approach")
            return None
            
        response.raise_for_status()
        
        clients = response.json()
        for client in clients:
            if client.get('clientId') == KEYCLOAK_CLIENT_ID:
                return client.get('id')
        
        logger.error(f"DMS client '{KEYCLOAK_CLIENT_ID}' not found")
        return None
        
    except Exception as e:
        logger.error(f"Failed to get DMS client ID: {e}")
        return None


def create_project_resource(project_slug):
    """Create a Keycloak resource for a project using UMA Resource Registration API"""
    try:
        logger.info(f"=== CREATING UMA RESOURCE FOR PROJECT: {project_slug} ===")
        
        service_token = get_service_token()
        if not service_token:
            logger.error("Failed to get service token")
            return False
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        # Create the resource using UMA Resource Registration API
        resource_data = {
            'name': project_slug,
            'displayName': f"Project: {project_slug}",
            'type': 'urn:folio:resources:project',
            'scopes': ['READ', 'WRITE'],  # Use existing scopes
            'attributes': {
                'project_slug': [project_slug],
                'created_by': ['folio-service']
            }
        }
        
        # Use UMA Resource Registration endpoint instead of Admin API
        response = requests.post(KEYCLOAK_UMA_RESOURCE_URI, headers=headers, json=resource_data, timeout=10)
        
        if response.status_code == 201:
            resource = response.json()
            logger.info(f"Successfully created UMA resource '{project_slug}' with ID: {resource.get('_id')}")
            logger.info(f"Resource scopes: {resource.get('scopes', [])}")
            return resource
        elif response.status_code == 409:
            logger.warning(f"UMA Resource '{project_slug}' already exists")
            return None
        else:
            logger.error(f"Failed to create UMA resource: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to create project resource: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False


def get_project_resource(project_slug):
    """Get an existing project resource from Keycloak using UMA Resource Registration API"""
    try:
        service_token = get_service_token()
        if not service_token:
            return None
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        # Get all resources and filter by name (UMA API doesn't support name filtering directly)
        response = requests.get(KEYCLOAK_UMA_RESOURCE_URI, headers=headers, timeout=10)
        response.raise_for_status()
        
        resource_ids = response.json()
        
        # Search through resources to find the one with matching name
        for resource_id in resource_ids:
            resource_response = requests.get(f"{KEYCLOAK_UMA_RESOURCE_URI}/{resource_id}", 
                                           headers=headers, timeout=10)
            if resource_response.status_code == 200:
                resource = resource_response.json()
                if resource.get('name') == project_slug:
                    logger.info(f"Found existing UMA resource '{project_slug}': {resource.get('_id')}")
                    return resource
        
        logger.info(f"UMA resource '{project_slug}' not found")
        return None
        
    except Exception as e:
        logger.error(f"Failed to get project resource: {e}")
        return None


def create_project_group(project_slug):
    """Create a Keycloak group for a project"""
    try:
        logger.info(f"=== CREATING GROUP FOR PROJECT: {project_slug} ===")
        
        service_token = get_service_token()
        if not service_token:
            logger.error("Failed to get service token")
            return False
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        # Create the group data
        group_data = {
            'name': f"project-{project_slug}",
            'path': f"/project-{project_slug}",
            'attributes': {
                'project_slug': [project_slug],
                'created_by': ['folio-service'],
                'group_type': ['project'],
                'description': [f"Project group for {project_slug}"]
            }
        }
        
        # Create the group using Keycloak Admin API
        response = requests.post(f"{KEYCLOAK_ADMIN_BASE_URI}/groups", 
                               headers=headers, json=group_data, timeout=10)
        
        if response.status_code == 201:
            # Get the created group ID from Location header
            location = response.headers.get('Location')
            group_id = location.split('/')[-1] if location else None
            
            if group_id:
                # Get the full group details
                group_response = requests.get(f"{KEYCLOAK_ADMIN_BASE_URI}/groups/{group_id}", 
                                            headers=headers, timeout=10)
                if group_response.status_code == 200:
                    group = group_response.json()
                    logger.info(f"Successfully created group '{group['name']}' with ID: {group['id']}")
                    return group
            
            logger.info(f"Successfully created group for project '{project_slug}'")
            return {"name": group_data["name"], "id": group_id}
            
        elif response.status_code == 409:
            logger.warning(f"Group for project '{project_slug}' already exists")
            return None
        else:
            logger.error(f"Failed to create group: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to create project group: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False


def get_project_group(project_slug):
    """Get an existing project group from Keycloak"""
    try:
        service_token = get_service_token()
        if not service_token:
            return None
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        # Get all groups and search for the project group
        response = requests.get(f"{KEYCLOAK_ADMIN_BASE_URI}/groups", headers=headers, timeout=10)
        response.raise_for_status()
        
        groups = response.json()
        group_name = f"project-{project_slug}"
        
        # Search for the group by name
        for group in groups:
            if group.get('name') == group_name:
                logger.info(f"Found existing group '{group_name}': {group.get('id')}")
                return group
        
        logger.info(f"Group '{group_name}' not found")
        return None
        
    except Exception as e:
        logger.error(f"Failed to get project group: {e}")
        return None


def get_user_by_username(username):
    """Get user details by username from Keycloak"""
    try:
        service_token = get_service_token()
        if not service_token:
            return None
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        # Search for user by username
        response = requests.get(f"{KEYCLOAK_ADMIN_BASE_URI}/users", 
                              headers=headers, 
                              params={'username': username, 'exact': 'true'}, 
                              timeout=10)
        response.raise_for_status()
        
        users = response.json()
        if users:
            user = users[0]  # Get first (and should be only) exact match
            logger.info(f"Found user '{username}' with ID: {user.get('id')}")
            return user
        else:
            logger.warning(f"User '{username}' not found")
            return None
        
    except Exception as e:
        logger.error(f"Failed to get user by username: {e}")
        return None


def add_user_to_project_group(project_slug, username):
    """Add a user to a project group"""
    try:
        logger.info(f"=== ADDING USER '{username}' TO PROJECT GROUP '{project_slug}' ===")
        
        # Get the project group
        group = get_project_group(project_slug)
        if not group:
            logger.error(f"Project group for '{project_slug}' not found")
            return False
        
        # Get the user
        user = get_user_by_username(username)
        if not user:
            logger.error(f"User '{username}' not found")
            return False
        
        service_token = get_service_token()
        if not service_token:
            return False
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        group_id = group['id']
        user_id = user['id']
        
        # Add user to group
        response = requests.put(f"{KEYCLOAK_ADMIN_BASE_URI}/users/{user_id}/groups/{group_id}", 
                              headers=headers, timeout=10)
        
        if response.status_code == 204:
            logger.info(f"Successfully added user '{username}' to group '{group['name']}'")
            return True
        else:
            logger.error(f"Failed to add user to group: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to add user to project group: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False


def remove_user_from_project_group(project_slug, username):
    """Remove a user from a project group"""
    try:
        logger.info(f"=== REMOVING USER '{username}' FROM PROJECT GROUP '{project_slug}' ===")
        
        # Get the project group
        group = get_project_group(project_slug)
        if not group:
            logger.error(f"Project group for '{project_slug}' not found")
            return False
        
        # Get the user
        user = get_user_by_username(username)
        if not user:
            logger.error(f"User '{username}' not found")
            return False
        
        service_token = get_service_token()
        if not service_token:
            return False
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        group_id = group['id']
        user_id = user['id']
        
        # Remove user from group
        response = requests.delete(f"{KEYCLOAK_ADMIN_BASE_URI}/users/{user_id}/groups/{group_id}", 
                                 headers=headers, timeout=10)
        
        if response.status_code == 204:
            logger.info(f"Successfully removed user '{username}' from group '{group['name']}'")
            return True
        else:
            logger.error(f"Failed to remove user from group: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to remove user from project group: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False


def get_project_group_members(project_slug):
    """Get all members of a project group"""
    try:
        # Get the project group
        group = get_project_group(project_slug)
        if not group:
            logger.error(f"Project group for '{project_slug}' not found")
            return None
        
        service_token = get_service_token()
        if not service_token:
            return None
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        group_id = group['id']
        
        # Get group members
        response = requests.get(f"{KEYCLOAK_ADMIN_BASE_URI}/groups/{group_id}/members", 
                              headers=headers, timeout=10)
        response.raise_for_status()
        
        members = response.json()
        logger.info(f"Found {len(members)} members in group '{group['name']}'")
        
        # Return simplified member info
        member_list = []
        for member in members:
            member_list.append({
                'id': member.get('id'),
                'username': member.get('username'),
                'email': member.get('email'),
                'firstName': member.get('firstName'),
                'lastName': member.get('lastName'),
                'enabled': member.get('enabled')
            })
        
        return member_list
        
    except Exception as e:
        logger.error(f"Failed to get project group members: {e}")
        return None


def create_project_group_with_permission(project_slug, permission):
    """Create a Keycloak group for a project with specific permission (read or write)"""
    try:
        group_name = f"project-{project_slug}-{permission}"
        logger.info(f"=== CREATING {permission.upper()} GROUP FOR PROJECT: {project_slug} ===")
        
        service_token = get_service_token()
        if not service_token:
            logger.error("Failed to get service token")
            return False
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        # Create the group data
        group_data = {
            'name': group_name,
            'path': f"/{group_name}",
            'attributes': {
                'project_slug': [project_slug],
                'permission': [permission],
                'created_by': ['folio-service'],
                'group_type': ['project'],
                'description': [f"Project {permission} group for {project_slug}"]
            }
        }
        
        # Create the group using Keycloak Admin API
        response = requests.post(f"{KEYCLOAK_ADMIN_BASE_URI}/groups", 
                               headers=headers, json=group_data, timeout=10)
        
        if response.status_code == 201:
            # Get the created group ID from Location header
            location = response.headers.get('Location')
            group_id = location.split('/')[-1] if location else None
            logger.info(f"Successfully created {permission} group '{group_name}' with ID: {group_id}")
            return True
        elif response.status_code == 409:
            logger.warning(f"{permission.capitalize()} group for project '{project_slug}' already exists")
            return True
        else:
            logger.error(f"Failed to create {permission} group: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to create {permission} group for project {project_slug}: {e}")
        return False


def add_user_to_project_group_with_permission(project_slug, username, permission):
    """Add a user to a project group with specific permission (read or write)"""
    try:
        group_name = f"project-{project_slug}-{permission}"
        logger.info(f"=== ADDING USER '{username}' TO {permission.upper()} GROUP '{group_name}' ===")
        
        # Get the specific permission group
        group = get_project_group_by_name(group_name)
        if not group:
            logger.error(f"Project {permission} group '{group_name}' not found")
            return False
        
        # Get the user
        user = get_user_by_username(username)
        if not user:
            logger.error(f"User '{username}' not found")
            return False
        
        service_token = get_service_token()
        if not service_token:
            return False
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        group_id = group['id']
        user_id = user['id']
        
        # Add user to group
        response = requests.put(f"{KEYCLOAK_ADMIN_BASE_URI}/users/{user_id}/groups/{group_id}", 
                              headers=headers, timeout=10)
        
        if response.status_code == 204:
            logger.info(f"Successfully added user '{username}' to {permission} group '{group_name}'")
            return True
        else:
            logger.error(f"Failed to add user to {permission} group: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to add user '{username}' to {permission} group: {e}")
        return False


def get_project_group_by_name(group_name):
    """Get a project group by its exact name"""
    try:
        service_token = get_service_token()
        if not service_token:
            return None
        
        headers = {
            'Authorization': f'Bearer {service_token}',
            'Content-Type': 'application/json'
        }
        
        # Search for the group by name
        response = requests.get(f"{KEYCLOAK_ADMIN_BASE_URI}/groups?search={group_name}", 
                              headers=headers, timeout=10)
        response.raise_for_status()
        
        groups = response.json()
        
        # Find exact match
        for group in groups:
            if group.get('name') == group_name:
                logger.info(f"Found group '{group_name}' with ID: {group['id']}")
                return group
        
        logger.warning(f"Group '{group_name}' not found")
        return None
        
    except Exception as e:
        logger.error(f"Failed to get group by name '{group_name}': {e}")
        return None


def get_rpt_permissions(access_token):
    """Exchange JWT access token for RPT permissions (Following SONG's pattern)"""
    try:
        logger.info("=== FETCHING RPT PERMISSIONS ===")
        
        # Prepare UMA token exchange request (like SONG does)
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f'Bearer {access_token}'
        }
        
        data = {
            'grant_type': 'urn:ietf:params:oauth:grant-type:uma-ticket',
            'audience': KEYCLOAK_CLIENT_ID,
            'response_mode': 'permissions'
        }
        
        logger.info(f"Exchanging JWT for RPT permissions at: {KEYCLOAK_PERMISSION_URI}")
        response = requests.post(KEYCLOAK_PERMISSION_URI, headers=headers, data=data, timeout=10)
        
        if response.status_code in [200, 207]:  # OK or Multi-Status
            permissions = response.json()
            logger.info(f"Successfully fetched {len(permissions)} RPT permissions")
            
            # Log permissions for debugging
            for perm in permissions:
                logger.info(f"Permission: {perm.get('rsname')} -> {perm.get('scopes', [])}")
            
            return permissions
        else:
            logger.warning(f"RPT request failed with status {response.status_code}: {response.text}")
            return []
            
    except Exception as e:
        logger.error(f"Failed to fetch RPT permissions: {e}")
        return []


def extract_scopes_from_rpt(permissions):
    """Extract scopes from RPT permissions (Following SONG's extractGrantedScopesFromRpt pattern)"""
    granted_scopes = set()
    
    for permission in permissions:
        rsname = permission.get('rsname', '')
        scopes = permission.get('scopes', [])
        
        for scope in scopes:
            # Create fully qualified scope like "folio.READ", "folio.malaria-study.WRITE"
            full_scope = f"{rsname}.{scope}"
            granted_scopes.add(full_scope)
    
    logger.info(f"Extracted scopes from RPT: {granted_scopes}")
    return granted_scopes


def validate_jwt_token(token):
    """Validate JWT token by exchanging it for RPT permissions (Following SONG's pattern)"""
    try:
        logger.info("=== Starting JWT validation via RPT exchange ===")
        
        # Skip local JWT validation - let Keycloak validate it!
        # Just extract basic info without validation for logging
        try:
            unverified_payload = jwt.decode(token, options={"verify_signature": False})
            username = unverified_payload.get('preferred_username', 'unknown')
            logger.info(f"Token for user: {username}")
        except:
            logger.info("Could not decode token for logging (that's ok)")
        
        # The real validation: try to get RPT permissions from Keycloak
        # If this works, the token is valid!
        rpt_permissions = get_rpt_permissions(token)
        
        if not rpt_permissions and rpt_permissions != []:  # Allow empty list but not None/False
            logger.error("Failed to get RPT permissions - token invalid or no permissions")
            return None
        
        logger.info("JWT validated successfully via RPT exchange!")
        
        # Create a minimal payload with RPT data
        payload = {
            'rpt_permissions': rpt_permissions,
            'granted_scopes': extract_scopes_from_rpt(rpt_permissions)
        }
        
        # Add basic user info from unverified token if available
        try:
            unverified_payload = jwt.decode(token, options={"verify_signature": False})
            payload.update({
                'preferred_username': unverified_payload.get('preferred_username'),
                'email': unverified_payload.get('email'),
                'name': unverified_payload.get('name'),
                'sub': unverified_payload.get('sub'),
                'iss': unverified_payload.get('iss'),
                'azp': unverified_payload.get('azp'),
                'aud': unverified_payload.get('aud')
            })
        except:
            logger.warning("Could not extract user info from token")
        
        return payload
        
    except Exception as e:
        logger.error(f"Unexpected error during token validation: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None


def extract_user_info(payload):
    """Extract user information and RPT permissions from JWT payload"""
    
    # Extract user information
    user_info = {
        "username": payload.get("preferred_username", "unknown"),
        "email": payload.get("email"),
        "name": payload.get("name"),
        "sub": payload.get("sub"),
        "iss": payload.get("iss"),
        "client_id": payload.get("azp", payload.get("aud")),
    }
    
    # Get RPT-based permissions
    granted_scopes = payload.get('granted_scopes', set())
    rpt_permissions = payload.get('rpt_permissions', [])
    
    # Convert to list for JSON serialization
    user_info["permissions"] = list(granted_scopes)
    user_info["rpt_permissions"] = rpt_permissions
    
    # Extract folio-specific permissions
    folio_permissions = [scope for scope in granted_scopes if scope.startswith('folio.')]
    user_info["folio_permissions"] = folio_permissions
    
    logger.info(f"User {user_info['username']} has RPT permissions: {folio_permissions}")
    
    return user_info


def authenticate_token(f):
    """Decorator to require valid JWT token"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        logger.info("=== AUTH: JWT Token received ===")
        
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'No token provided'}), 401
        
        token = auth_header.split(' ')[1]
        
        # Validate the JWT token
        payload = validate_jwt_token(token)
        if not payload:
            return jsonify({'error': 'Invalid token'}), 401
        
        # Extract user info and store in g
        g.user = extract_user_info(payload)
        g.token = token  # Store the token for SONG API calls
        
        return f(*args, **kwargs)
    
    return decorated_function


def require_permissions(required_scopes):
    """Decorator to require specific RPT permissions"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            logger.info(f"=== AUTH: Checking RPT permissions: {required_scopes} ===")
            
            # Get token from Authorization header
            auth_header = request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                return {'error': 'No token provided'}, 401
            
            token = auth_header.split(' ')[1]
            
            # Validate the JWT token and get RPT permissions
            payload = validate_jwt_token(token)
            if not payload:
                return {'error': 'Invalid token'}, 401
            
            # Extract user info with RPT permissions
            user_info = extract_user_info(payload)
            
            # Check required permissions - format them as folio.SCOPE
            user_permissions = user_info.get("permissions", [])
            missing_scopes = []
            
            for scope in required_scopes:
                # Convert scope to folio.SCOPE format if needed
                formatted_scope = scope if scope.startswith('folio.') else f'folio.{scope}'
                if formatted_scope not in user_permissions:
                    missing_scopes.append(formatted_scope)
            
            if missing_scopes:
                logger.warning(f"User {user_info['username']} missing required RPT scopes: {missing_scopes}")
                logger.info(f"User has permissions: {user_permissions}")
                logger.info(f"Raw RPT permissions: {user_info.get('rpt_permissions', [])}")
                return {
                    'error': f'Missing required RPT permissions: {missing_scopes}',
                    'user_permissions': user_permissions,
                    'rpt_permissions': user_info.get('rpt_permissions', [])
                }, 403
            
            # Store user info in g
            g.user = user_info
            g.token = token  # Store the token for SONG API calls
            
            return f(*args, **kwargs)
        
        return decorated_function
    return decorator


# Routes converted to Flask-RESTX Resources

@health_ns.route('')
class Health(Resource):
    @health_ns.doc('health_check')
    @health_ns.marshal_with(api.model('HealthResponse', {
        'status': fields.String(description='Service status'),
        'service': fields.String(description='Service name')
    }))
    def get(self):
        """Health check endpoint"""
        return {"status": "healthy", "service": "folio"}


@auth_ns.route('/test')
class AuthTest(Resource):
    @auth_ns.doc('test_authentication', security='Bearer')
    @auth_ns.marshal_with(api.model('AuthTestResponse', {
        'message': fields.String(description='Response message'),
        'user': fields.Nested(user_model, description='User information'),
        'status': fields.String(description='Authentication status')
    }))
    @auth_ns.response(401, 'Invalid or missing token')
    @authenticate_token
    def get(self):
        """Test endpoint that requires JWT authentication"""
        logger.info(f"Test endpoint called by user: {g.user['username']}")
        return {
            "message": "Hello from Folio!",
            "user": g.user,
            "status": "authenticated"
        }


@auth_ns.route('/test/read')
class AuthTestRead(Resource):
    @auth_ns.doc('test_read_permission', security='Bearer')
    @auth_ns.marshal_with(api.model('AuthReadResponse', {
        'message': fields.String(description='Response message'),
        'user': fields.Nested(user_model, description='User information'),
        'action': fields.String(description='Action performed'),
        'status': fields.String(description='Authorization status')
    }))
    @auth_ns.response(401, 'Invalid or missing token')
    @auth_ns.response(403, 'Insufficient permissions', error_model)
    @require_permissions(["READ"])
    def get(self):
        """Test endpoint that requires READ permission"""
        logger.info(f"Read endpoint called by user: {g.user['username']}")
        return {
            "message": "You have READ access!",
            "user": g.user,
            "action": "read",
            "status": "authorized"
        }


@auth_ns.route('/test/write')
class AuthTestWrite(Resource):
    @auth_ns.doc('test_write_permission', security='Bearer')
    @auth_ns.marshal_with(api.model('AuthWriteResponse', {
        'message': fields.String(description='Response message'),
        'user': fields.Nested(user_model, description='User information'),
        'action': fields.String(description='Action performed'),
        'status': fields.String(description='Authorization status')
    }))
    @auth_ns.response(401, 'Invalid or missing token')
    @auth_ns.response(403, 'Insufficient permissions', error_model)
    @require_permissions(["WRITE"])
    def get(self):
        """Test endpoint that requires WRITE permission"""
        logger.info(f"Write endpoint called by user: {g.user['username']}")
        return {
            "message": "You have WRITE access!",
            "user": g.user,
            "action": "write",
            "status": "authorized"
        }


@auth_ns.route('/test/admin')
class AuthTestAdmin(Resource):
    @auth_ns.doc('test_admin_permission', security='Bearer')
    @auth_ns.marshal_with(api.model('AuthAdminResponse', {
        'message': fields.String(description='Response message'),
        'user': fields.Nested(user_model, description='User information'),
        'action': fields.String(description='Action performed'),
        'status': fields.String(description='Authorization status')
    }))
    @auth_ns.response(401, 'Invalid or missing token')
    @auth_ns.response(403, 'Insufficient permissions', error_model)
    @require_permissions(["READ", "WRITE"])
    def post(self):
        """Test endpoint that requires both READ and write permissions"""
        logger.info(f"Admin endpoint called by user: {g.user['username']}")
        return {
            "message": "You have full READ and WRITE access!",
            "user": g.user,
            "action": "admin",
            "status": "authorized"
        }


@projects_ns.route('/<string:project_slug>/resource')
@projects_ns.param('project_slug', 'The project identifier')
class ProjectResource(Resource):
    @projects_ns.doc('create_project_resource', security='Bearer')
    @projects_ns.marshal_with(api.model('ProjectResourceResponse', {
        'message': fields.String(description='Response message'),
        'resource': fields.Nested(resource_model, description='Created resource'),
        'status': fields.String(description='Operation status')
    }))
    @projects_ns.response(400, 'Invalid project slug')
    @projects_ns.response(401, 'Invalid or missing token')
    @projects_ns.response(403, 'Insufficient permissions', error_model)
    @projects_ns.response(409, 'Resource already exists')
    @projects_ns.response(500, 'Failed to create resource')
    @require_permissions(["WRITE"])
    def post(self, project_slug):
        """Create a Keycloak resource for a project"""
        logger.info(f"Creating resource for project: {project_slug} by user: {g.user['username']}")
        
        # Validate project slug (basic validation)
        if not project_slug or len(project_slug) < 2:
            return {"error": "Invalid project slug"}, 400
        
        # Check if resource already exists
        existing_resource = get_project_resource(project_slug)
        if existing_resource:
            return {
                "message": f"Resource for project '{project_slug}' already exists",
                "resource": existing_resource,
                "status": "exists"
            }, 200
        
        # Create the resource
        result = create_project_resource(project_slug)
        
        if result is False:
            return {"error": "Failed to create project resource"}, 500
        elif result is None:
            return {"error": "Resource already exists"}, 409
        else:
            return {
                "message": f"Successfully created resource for project '{project_slug}'",
                "resource": result,
                "status": "created"
            }, 201

    @projects_ns.doc('get_project_resource', security='Bearer')
    @projects_ns.marshal_with(api.model('GetProjectResourceResponse', {
        'message': fields.String(description='Response message'),
        'resource': fields.Nested(resource_model, description='Found resource'),
        'status': fields.String(description='Operation status')
    }))
    @projects_ns.response(401, 'Invalid or missing token')
    @projects_ns.response(403, 'Insufficient permissions', error_model)
    @projects_ns.response(404, 'Resource not found')
    @require_permissions(["READ"])
    def get(self, project_slug):
        """Get a project resource from Keycloak"""
        logger.info(f"Getting resource for project: {project_slug} by user: {g.user['username']}")
        
        resource = get_project_resource(project_slug)
        
        if resource:
            return {
                "message": f"Found resource for project '{project_slug}'",
                "resource": resource,
                "status": "found"
            }
        else:
            return {
                "message": f"Resource for project '{project_slug}' not found",
                "status": "not_found"
            }, 404


@projects_ns.route('/<string:project_slug>/group')
@projects_ns.param('project_slug', 'The project identifier')
class ProjectGroup(Resource):
    @projects_ns.doc('create_project_group', security='Bearer')
    @projects_ns.marshal_with(api.model('ProjectGroupResponse', {
        'message': fields.String(description='Response message'),
        'group': fields.Nested(group_model, description='Created group'),
        'status': fields.String(description='Operation status')
    }))
    @projects_ns.response(400, 'Invalid project slug')
    @projects_ns.response(401, 'Invalid or missing token')
    @projects_ns.response(403, 'Insufficient permissions', error_model)
    @projects_ns.response(409, 'Group already exists')
    @projects_ns.response(500, 'Failed to create group')
    @require_permissions(["WRITE"])
    def post(self, project_slug):
        """Create a Keycloak group for a project"""
        logger.info(f"Creating group for project: {project_slug} by user: {g.user['username']}")
        
        # Validate project slug (basic validation)
        if not project_slug or len(project_slug) < 2:
            return {"error": "Invalid project slug"}, 400
        
        # Check if group already exists
        existing_group = get_project_group(project_slug)
        if existing_group:
            return {
                "message": f"Group for project '{project_slug}' already exists",
                "group": existing_group,
                "status": "exists"
            }, 200
        
        # Create the group
        result = create_project_group(project_slug)
        
        if result is False:
            return {"error": "Failed to create project group"}, 500
        elif result is None:
            return {"error": "Group already exists"}, 409
        else:
            return {
                "message": f"Successfully created group for project '{project_slug}'",
                "group": result,
                "status": "created"
            }, 201

    @projects_ns.doc('get_project_group', security='Bearer')
    @projects_ns.marshal_with(api.model('GetProjectGroupResponse', {
        'message': fields.String(description='Response message'),
        'group': fields.Nested(group_model, description='Found group'),
        'status': fields.String(description='Operation status')
    }))
    @projects_ns.response(401, 'Invalid or missing token')
    @projects_ns.response(403, 'Insufficient permissions', error_model)
    @projects_ns.response(404, 'Group not found')
    @require_permissions(["READ"])
    def get(self, project_slug):
        """Get a project group from Keycloak"""
        logger.info(f"Getting group for project: {project_slug} by user: {g.user['username']}")
        
        group = get_project_group(project_slug)
        
        if group:
            return {
                "message": f"Found group for project '{project_slug}'",
                "group": group,
                "status": "found"
            }
        else:
            return {
                "message": f"Group for project '{project_slug}' not found",
                "status": "not_found"
            }, 404


@projects_ns.route('/<string:project_slug>/group/members')
@projects_ns.param('project_slug', 'The project identifier')
class ProjectGroupMembers(Resource):
    @projects_ns.doc('get_project_group_members', security='Bearer')
    @projects_ns.marshal_with(api.model('GroupMembersResponse', {
        'message': fields.String(description='Response message'),
        'project': fields.String(description='Project slug'),
        'members': fields.List(fields.Nested(member_model), description='Group members'),
        'count': fields.Integer(description='Number of members'),
        'status': fields.String(description='Operation status')
    }))
    @projects_ns.response(401, 'Invalid or missing token')
    @projects_ns.response(403, 'Insufficient permissions', error_model)
    @projects_ns.response(404, 'Group not found')
    @require_permissions(["READ"])
    def get(self, project_slug):
        """Get all members of a project group"""
        logger.info(f"Getting group members for project: {project_slug} by user: {g.user['username']}")
        
        members = get_project_group_members(project_slug)
        
        if members is not None:
            return {
                "message": f"Found {len(members)} members in project group '{project_slug}'",
                "project": project_slug,
                "members": members,
                "count": len(members),
                "status": "found"
            }
        else:
            return {
                "message": f"Group for project '{project_slug}' not found or error occurred",
                "status": "error"
            }, 404


@projects_ns.route('/<string:project_slug>/group/members/<string:username>')
@projects_ns.param('project_slug', 'The project identifier')
@projects_ns.param('username', 'The username to add/remove from the group')
class ProjectGroupMember(Resource):
    @projects_ns.doc('add_user_to_project_group', security='Bearer')
    @projects_ns.marshal_with(api.model('GroupMemberResponse', {
        'message': fields.String(description='Response message'),
        'project': fields.String(description='Project slug'),
        'username': fields.String(description='Username'),
        'status': fields.String(description='Operation status')
    }))
    @projects_ns.response(400, 'Invalid project slug or username')
    @projects_ns.response(401, 'Invalid or missing token')
    @projects_ns.response(403, 'Insufficient permissions', error_model)
    @projects_ns.response(500, 'Failed to add user to group')
    @require_permissions(["WRITE"])
    def post(self, project_slug, username):
        """Add a user to a project group"""
        logger.info(f"Adding user '{username}' to project group '{project_slug}' by user: {g.user['username']}")
        
        # Validate inputs
        if not project_slug or len(project_slug) < 2:
            return {"error": "Invalid project slug"}, 400
        
        if not username or len(username) < 1:
            return {"error": "Invalid username"}, 400
        
        # Add user to group
        result = add_user_to_project_group(project_slug, username)
        
        if result:
            return {
                "message": f"Successfully added user '{username}' to project group '{project_slug}'",
                "project": project_slug,
                "username": username,
                "status": "added"
            }, 200
        else:
            return {
                "error": f"Failed to add user '{username}' to project group '{project_slug}'",
                "project": project_slug,
                "username": username,
                "status": "failed"
            }, 500

    @projects_ns.doc('remove_user_from_project_group', security='Bearer')
    @projects_ns.marshal_with(api.model('GroupMemberRemoveResponse', {
        'message': fields.String(description='Response message'),
        'project': fields.String(description='Project slug'),
        'username': fields.String(description='Username'),
        'status': fields.String(description='Operation status')
    }))
    @projects_ns.response(400, 'Invalid project slug or username')
    @projects_ns.response(401, 'Invalid or missing token')
    @projects_ns.response(403, 'Insufficient permissions', error_model)
    @projects_ns.response(500, 'Failed to remove user from group')
    @require_permissions(["WRITE"])
    def delete(self, project_slug, username):
        """Remove a user from a project group"""
        logger.info(f"Removing user '{username}' from project group '{project_slug}' by user: {g.user['username']}")
        
        # Validate inputs
        if not project_slug or len(project_slug) < 2:
            return {"error": "Invalid project slug"}, 400
        
        if not username or len(username) < 1:
            return {"error": "Invalid username"}, 400
        
        # Remove user from group
        result = remove_user_from_project_group(project_slug, username)
        
        if result:
            return {
                "message": f"Successfully removed user '{username}' from project group '{project_slug}'",
                "project": project_slug,
                "username": username,
                "status": "removed"
            }, 200
        else:
            return {
                "error": f"Failed to remove user '{username}' from project group '{project_slug}'",
                "project": project_slug,
                "username": username,
                "status": "failed"
            }, 500


@projects_ns.route('/<string:project_slug>/users')
@projects_ns.param('project_slug', 'The project slug/identifier')
class ProjectUsers(Resource):
    @projects_ns.doc('get_project_users', security='Bearer')
    @projects_ns.marshal_list_with(member_model)
    @projects_ns.response(401, 'Invalid or missing token')
    @projects_ns.response(403, 'Insufficient permissions', error_model)
    @projects_ns.response(404, 'Project not found')
    @require_permissions(["READ"])
    def get(self, project_slug):
        """Get all users/members in a project (based on project group membership)"""
        logger.info(f"Getting users for project {project_slug} by user: {g.user['username']}")
        
        # This is an alias for the existing group members endpoint for convenience
        members = get_project_group_members(project_slug)
        
        if members is not None:
            return members
        else:
            return {"error": f"Project '{project_slug}' not found or no group exists"}, 404


@projects_ns.route('/<string:project_slug>/studies')
@projects_ns.param('project_slug', 'The project slug/identifier')
class ProjectStudies(Resource):
    @projects_ns.doc('get_project_studies', security='Bearer')
    @projects_ns.marshal_list_with(study_model)
    @projects_ns.response(401, 'Invalid or missing token')
    @projects_ns.response(403, 'Insufficient permissions', error_model)
    @projects_ns.response(404, 'Project not found')
    @require_permissions(["READ"])
    def get(self, project_slug):
        """Get all studies for a specific project"""
        logger.info(f"Getting studies for project {project_slug} by user: {g.user['username']}")
        
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # First verify project exists
                cur.execute("SELECT id FROM projects WHERE slug = %s", (project_slug,))
                project = cur.fetchone()
                if not project:
                    return {"error": f"Project {project_slug} not found"}, 404
                
                # Get studies for this project
                query = """
                    SELECT s.*, p.slug as project_slug, p.name as project_name
                    FROM studies s
                    JOIN projects p ON s.project_id = p.id
                    WHERE p.slug = %s
                    ORDER BY s.name
                """
                cur.execute(query, (project_slug,))
                studies = cur.fetchall()
                
                # Add song_created flag to each study (simplified - no metadata)
                studies_with_flags = []
                for study in studies:
                    study_dict = serialize_record(study)
                    # Default song_created to False since we don't persist it anymore
                    study_dict['song_created'] = False
                    studies_with_flags.append(study_dict)
                
                return studies_with_flags
                
        except Exception as e:
            logger.error(f"Failed to get studies for project: {e}")
            return {"error": "Failed to retrieve studies"}, 500
        finally:
            conn.close()


# Add a summary endpoint that gives an overview of a project
@projects_ns.route('/<string:project_slug>/summary')
@projects_ns.param('project_slug', 'The project slug/identifier')
class ProjectSummary(Resource):
    @projects_ns.doc('get_project_summary', security='Bearer')
    @projects_ns.marshal_with(api.model('ProjectSummary', {
        'project': fields.Nested(project_model, description='Project details'),
        'studies_count': fields.Integer(description='Number of studies'),
        'users_count': fields.Integer(description='Number of users'),
        'studies': fields.List(fields.Nested(study_model), description='Project studies'),
        'users': fields.List(fields.Nested(member_model), description='Project users'),
        'status': fields.String(description='Operation status')
    }))
    @projects_ns.response(401, 'Invalid or missing token')
    @projects_ns.response(403, 'Insufficient permissions', error_model)
    @projects_ns.response(404, 'Project not found')
    @require_permissions(["READ"])
    def get(self, project_slug):
        """Get a complete summary of a project including details, studies, and users"""
        logger.info(f"Getting project summary for {project_slug} by user: {g.user['username']}")
        
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get project details with pathogen info
                query = """
                    SELECT p.*, pat.name as pathogen_name
                    FROM projects p
                    LEFT JOIN pathogens pat ON p.pathogen_id = pat.id
                    WHERE p.slug = %s
                """
                cur.execute(query, (project_slug,))
                project = cur.fetchone()
                
                if not project:
                    return {"error": f"Project {project_slug} not found"}, 404
                
                # Get studies for this project
                studies_query = """
                    SELECT s.*, p.slug as project_slug, p.name as project_name
                    FROM studies s
                    JOIN projects p ON s.project_id = p.id
                    WHERE p.slug = %s
                    ORDER BY s.name
                """
                cur.execute(studies_query, (project_slug,))
                studies = cur.fetchall()
                
                # Add song_created flag to studies
                studies_with_flags = []
                for study in studies:
                    study_dict = serialize_record(study)
                    study_dict['song_created'] = False  # Simplified - no metadata persistence
                    studies_with_flags.append(study_dict)
                
                # Get users/members from project group
                users = get_project_group_members(project_slug)
                if users is None:
                    users = []
                
                return {
                    "project": dict(project),
                    "studies_count": len(studies_with_flags),
                    "users_count": len(users),
                    "studies": studies_with_flags,
                    "users": users,
                    "status": "success"
                }
                
        except Exception as e:
            logger.error(f"Failed to get project summary: {e}")
            return {"error": "Failed to retrieve project summary"}, 500
        finally:
            conn.close()


# Pathogen CRUD endpoints
@pathogens_ns.route('')
class PathogenList(Resource):
    """Operations for multiple pathogens"""
    
    @pathogens_ns.doc('list_pathogens')
    @pathogens_ns.response(200, 'Success', [pathogen_model])
    @pathogens_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["READ"])
    def get(self):
        """Get all pathogens"""
        logger.info(f"Getting all pathogens for user: {g.user['username']}")
        
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM pathogens ORDER BY name")
                pathogens = cur.fetchall()
                return [serialize_record(pathogen) for pathogen in pathogens]
        except Exception as e:
            logger.error(f"Failed to get pathogens: {e}")
            return {"error": "Failed to retrieve pathogens"}, 500
        finally:
            conn.close()
    
    @pathogens_ns.doc('create_pathogen')
    @pathogens_ns.expect(pathogen_input_model, validate=True)
    @pathogens_ns.response(201, 'Pathogen created', pathogen_model)
    @pathogens_ns.response(400, 'Invalid input', error_model)
    @pathogens_ns.response(409, 'Pathogen already exists', error_model)
    @pathogens_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["WRITE"])
    def post(self):
        """Create a new pathogen"""
        logger.info(f"Creating new pathogen by user: {g.user['username']}")
        
        data = request.json
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check if pathogen already exists
                cur.execute("SELECT id FROM pathogens WHERE name = %s", (data['name'],))
                if cur.fetchone():
                    return {"error": "Pathogen already exists"}, 409
                
                # Create pathogen
                insert_query = """
                    INSERT INTO pathogens (name, scientific_name, description)
                    VALUES (%s, %s, %s)
                    RETURNING *
                """
                cur.execute(insert_query, (
                    data['name'],
                    data.get('scientific_name'),
                    data.get('description')
                ))
                pathogen = cur.fetchone()
                conn.commit()
                
                logger.info(f"Created pathogen: {pathogen['name']}")
                return serialize_record(pathogen), 201
                
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to create pathogen: {e}")
            return {"error": "Failed to create pathogen"}, 500
        finally:
            conn.close()


@pathogens_ns.route('/<string:pathogen_id>')
class PathogenDetail(Resource):
    """Operations for a single pathogen"""
    
    @pathogens_ns.doc('get_pathogen')
    @pathogens_ns.response(200, 'Success', pathogen_model)
    @pathogens_ns.response(404, 'Pathogen not found', error_model)
    @pathogens_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["READ"])
    def get(self, pathogen_id):
        """Get a specific pathogen by ID"""
        logger.info(f"Getting pathogen {pathogen_id} for user: {g.user['username']}")
        
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM pathogens WHERE id = %s", (pathogen_id,))
                pathogen = cur.fetchone()
                
                if not pathogen:
                    return {"error": "Pathogen not found"}, 404
                
                return serialize_record(pathogen)
        except Exception as e:
            logger.error(f"Failed to get pathogen: {e}")
            return {"error": "Failed to retrieve pathogen"}, 500
        finally:
            conn.close()
    
    @pathogens_ns.doc('update_pathogen')
    @pathogens_ns.expect(pathogen_input_model, validate=True)
    @pathogens_ns.response(200, 'Pathogen updated', pathogen_model)
    @pathogens_ns.response(404, 'Pathogen not found', error_model)
    @pathogens_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["WRITE"])
    def put(self, pathogen_id):
        """Update a pathogen"""
        logger.info(f"Updating pathogen {pathogen_id} by user: {g.user['username']}")
        
        data = request.json
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check if pathogen exists
                cur.execute("SELECT id FROM pathogens WHERE id = %s", (pathogen_id,))
                if not cur.fetchone():
                    return {"error": "Pathogen not found"}, 404
                
                # Update pathogen
                update_query = """
                    UPDATE pathogens 
                    SET name = %s, scientific_name = %s, description = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING *
                """
                cur.execute(update_query, (
                    data['name'],
                    data.get('scientific_name'),
                    data.get('description'),
                    pathogen_id
                ))
                pathogen = cur.fetchone()
                conn.commit()
                
                logger.info(f"Updated pathogen: {pathogen['name']}")
                return serialize_record(pathogen)
                
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to update pathogen: {e}")
            return {"error": "Failed to update pathogen"}, 500
        finally:
            conn.close()
    
    @pathogens_ns.doc('delete_pathogen')
    @pathogens_ns.response(204, 'Pathogen deleted')
    @pathogens_ns.response(404, 'Pathogen not found', error_model)
    @pathogens_ns.response(409, 'Cannot delete pathogen with associated projects', error_model)
    @pathogens_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["DELETE"])
    def delete(self, pathogen_id):
        """Delete a pathogen"""
        logger.info(f"Deleting pathogen {pathogen_id} by user: {g.user['username']}")
        
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check if pathogen exists
                cur.execute("SELECT id FROM pathogens WHERE id = %s", (pathogen_id,))
                if not cur.fetchone():
                    return {"error": "Pathogen not found"}, 404
                
                # Check if pathogen has associated projects
                cur.execute("SELECT COUNT(*) as count FROM projects WHERE pathogen_id = %s", (pathogen_id,))
                result = cur.fetchone()
                if result['count'] > 0:
                    return {"error": "Cannot delete pathogen with associated projects"}, 409
                
                # Delete pathogen
                cur.execute("DELETE FROM pathogens WHERE id = %s", (pathogen_id,))
                conn.commit()
                
                logger.info(f"Deleted pathogen: {pathogen_id}")
                return '', 204
                
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete pathogen: {e}")
            return {"error": "Failed to delete pathogen"}, 500
        finally:
            conn.close()


# Project CRUD endpoints
@projects_ns.route('')
class ProjectList(Resource):
    """Operations for multiple projects"""
    
    @projects_ns.doc('list_projects')
    @projects_ns.response(200, 'Success', [project_model])
    @projects_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["READ"])
    def get(self):
        """Get all projects"""
        logger.info(f"Getting all projects for user: {g.user['username']}")
        
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = """
                    SELECT p.*, pat.name as pathogen_name
                    FROM projects p
                    LEFT JOIN pathogens pat ON p.pathogen_id = pat.id
                    WHERE p.deleted_at IS NULL
                    ORDER BY p.name
                """
                cur.execute(query)
                projects = cur.fetchall()
                return [serialize_record(project) for project in projects]
        except Exception as e:
            logger.error(f"Failed to get projects: {e}")
            return {"error": "Failed to retrieve projects"}, 500
        finally:
            conn.close()
    
    @projects_ns.doc('create_project')
    @projects_ns.expect(project_input_model, validate=True)
    @projects_ns.response(201, 'Project created', project_model)
    @projects_ns.response(400, 'Invalid input', error_model)
    @projects_ns.response(409, 'Project already exists', error_model)
    @projects_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["WRITE"])
    def post(self):
        """Create a new project"""
        logger.info(f"Creating new project by user: {g.user['username']}")
        
        data = request.json
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check if project slug already exists
                cur.execute("SELECT id FROM projects WHERE slug = %s", (data['slug'],))
                if cur.fetchone():
                    return {"error": "Project slug already exists"}, 409
                
                # Verify pathogen exists if provided
                if data.get('pathogen_id'):
                    cur.execute("SELECT id FROM pathogens WHERE id = %s", (data['pathogen_id'],))
                    if not cur.fetchone():
                        return {"error": "Pathogen not found"}, 400
                
                # Create project
                insert_query = """
                    INSERT INTO projects (name, slug, description, organization_id, user_id, pathogen_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *
                """
                # Extract user and organization info from JWT
                user_id = g.user.get('sub')  # Keycloak user ID
                organization_id = g.user.get('organization_id', 'default-org')  # TODO: Get from Keycloak claims
                
                cur.execute(insert_query, (
                    data['name'],
                    data['slug'],
                    data.get('description'),
                    organization_id,
                    user_id,
                    data.get('pathogen_id')
                ))
                project = cur.fetchone()
                conn.commit()
                
                project_slug = project['slug']
                username = g.user.get('username', 'unknown')
                
                logger.info(f"Created project: {project['name']} ({project_slug})")
                
                # Automatically create Keycloak resources and groups for the new project
                try:
                    logger.info(f"Setting up Keycloak resources and groups for project: {project_slug}")
                    
                    # 1. Create the project resource in Keycloak
                    resource_created = create_project_resource(project_slug)
                    if resource_created:
                        logger.info(f"Successfully created Keycloak resource for project: {project_slug}")
                    else:
                        logger.warning(f"Failed to create Keycloak resource for project: {project_slug}")
                    
                    # 2. Create read and write groups
                    read_group_created = create_project_group_with_permission(project_slug, 'read')
                    write_group_created = create_project_group_with_permission(project_slug, 'write')
                    
                    if read_group_created:
                        logger.info(f"Successfully created read group for project: {project_slug}")
                    if write_group_created:
                        logger.info(f"Successfully created write group for project: {project_slug}")
                    
                    # 3. Add the project creator to both read and write groups
                    if read_group_created:
                        add_user_to_project_group_with_permission(project_slug, username, 'read')
                        logger.info(f"Added user {username} to read group for project: {project_slug}")
                    
                    if write_group_created:
                        add_user_to_project_group_with_permission(project_slug, username, 'write')
                        logger.info(f"Added user {username} to write group for project: {project_slug}")
                
                except Exception as keycloak_error:
                    logger.error(f"Failed to set up Keycloak resources for project {project_slug}: {keycloak_error}")
                    # Don't fail the project creation if Keycloak setup fails
                
                return serialize_record(project), 201
                
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to create project: {e}")
            return {"error": "Failed to create project"}, 500
        finally:
            conn.close()


# Study CRUD endpoints
@studies_ns.route('')
class StudyList(Resource):
    """Operations for multiple studies"""
    
    @studies_ns.doc('list_studies')
    @studies_ns.response(200, 'Success', [study_model])
    @studies_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["READ"])
    def get(self):
        """Get all studies"""
        logger.info(f"Getting all studies for user: {g.user['username']}")
        
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = """
                    SELECT s.*, p.slug as project_slug, p.name as project_name
                    FROM studies s
                    JOIN projects p ON s.project_id = p.id
                    ORDER BY s.name
                """
                cur.execute(query)
                studies = cur.fetchall()
                
                # Add song_created flag to studies
                studies_with_flags = []
                for study in studies:
                    study_dict = dict(study)
                    study_dict['song_created'] = False  # Will be updated in response
                    studies_with_flags.append(study_dict)
                
                return studies_with_flags
        except Exception as e:
            logger.error(f"Failed to get studies: {e}")
            return {"error": "Failed to retrieve studies"}, 500
        finally:
            conn.close()
    
    @studies_ns.doc('create_study')
    @studies_ns.expect(study_input_model, validate=True)
    @studies_ns.response(201, 'Study created', study_model)
    @studies_ns.response(400, 'Invalid input', error_model)
    @studies_ns.response(409, 'Study already exists', error_model)
    @studies_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["WRITE"])
    def post(self):
        """Create a new study and automatically create it in SONG"""
        logger.info(f"Creating new study by user: {g.user['username']}")
        
        data = request.json
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check if study_id already exists
                cur.execute("SELECT id FROM studies WHERE study_id = %s", (data['study_id'],))
                if cur.fetchone():
                    return {"error": "Study ID already exists"}, 409
                
                # Verify project exists
                cur.execute("SELECT id FROM projects WHERE id = %s", (data['project_id'],))
                if not cur.fetchone():
                    return {"error": "Project not found"}, 400
                
                # Create study in database first
                insert_query = """
                    INSERT INTO studies (study_id, name, description, project_id, start_date, end_date)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *
                """
                cur.execute(insert_query, (
                    data['study_id'],
                    data['name'],
                    data.get('description'),
                    data['project_id'],
                    data.get('start_date'),
                    data.get('end_date')
                ))
                study = cur.fetchone()
                
                # Try to create study in SONG
                song_result = create_song_study(dict(study), g.token)
                
                # Note: We simplified the schema and removed metadata field
                # Track SONG creation status in the response only
                conn.commit()
                
                logger.info(f"Created study: {study['name']} (SONG: {'success' if song_result else 'failed'})")
                
                study_dict = serialize_record(study)
                study_dict['song_created'] = song_result is not None
                return study_dict, 201
                
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to create study: {e}")
            return {"error": "Failed to create study"}, 500
        finally:
            conn.close()


@studies_ns.route('/<string:study_id>')
class StudyDetail(Resource):
    """Operations for a single study"""
    
    @studies_ns.doc('get_study')
    @studies_ns.response(200, 'Success', study_model)
    @studies_ns.response(404, 'Study not found', error_model)
    @studies_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["READ"])
    def get(self, study_id):
        """Get a specific study by study_id"""
        logger.info(f"Getting study {study_id} for user: {g.user['username']}")
        
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = """
                    SELECT s.*, p.slug as project_slug, p.name as project_name
                    FROM studies s
                    JOIN projects p ON s.project_id = p.id
                    WHERE s.study_id = %s
                """
                cur.execute(query, (study_id,))
                study = cur.fetchone()
                
                if not study:
                    return {"error": "Study not found"}, 404

                study_dict = dict(study)
                study_dict['song_created'] = False  # Will be updated in response
                return study_dict
        except Exception as e:
            logger.error(f"Failed to get study: {e}")
            return {"error": "Failed to retrieve study"}, 500
        finally:
            conn.close()
    
    @studies_ns.doc('update_study')
    @studies_ns.expect(study_input_model, validate=True)
    @studies_ns.response(200, 'Study updated', study_model)
    @studies_ns.response(404, 'Study not found', error_model)
    @studies_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["WRITE"])
    def put(self, study_id):
        """Update a study"""
        logger.info(f"Updating study {study_id} by user: {g.user['username']}")
        
        data = request.json
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check if study exists
                cur.execute("SELECT * FROM studies WHERE study_id = %s", (study_id,))
                existing_study = cur.fetchone()
                if not existing_study:
                    return {"error": "Study not found"}, 404
                
                # Verify project exists if being updated
                if data.get('project_id') and data['project_id'] != existing_study['project_id']:
                    cur.execute("SELECT id FROM projects WHERE id = %s", (data['project_id'],))
                    if not cur.fetchone():
                        return {"error": "Project not found"}, 400
                
                # Update study (simplified schema)
                update_query = """
                    UPDATE studies 
                    SET name = %s, description = %s, project_id = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE study_id = %s
                    RETURNING *
                """
                cur.execute(update_query, (
                    data['name'],
                    data.get('description'),
                    data.get('project_id', existing_study['project_id']),
                    study_id
                ))
                study = cur.fetchone()
                conn.commit()
                
                logger.info(f"Updated study: {study['name']}")
                
                study_dict = dict(study)
                study_dict['song_created'] = False  # Will be updated in response
                return study_dict
                
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to update study: {e}")
            return {"error": "Failed to update study"}, 500
        finally:
            conn.close()
    
    @studies_ns.doc('delete_study')
    @studies_ns.response(204, 'Study deleted')
    @studies_ns.response(404, 'Study not found', error_model)
    @studies_ns.response(500, 'Internal server error', error_model)
    @require_permissions(["DELETE"])
    def delete(self, study_id):
        """Delete a study"""
        logger.info(f"Deleting study {study_id} by user: {g.user['username']}")
        
        conn = get_db_connection()
        if not conn:
            return {"error": "Database connection failed"}, 500
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check if study exists
                cur.execute("SELECT id FROM studies WHERE study_id = %s", (study_id,))
                if not cur.fetchone():
                    return {"error": "Study not found"}, 404
                
                # Delete study
                cur.execute("DELETE FROM studies WHERE study_id = %s", (study_id,))
                conn.commit()
                
                logger.info(f"Deleted study: {study_id}")
                return '', 204
                
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete study: {e}")
            return {"error": "Failed to delete study"}, 500
        finally:
            conn.close()


def get_db_connection():
    """Get a database connection"""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return None


def create_song_study(study_data, token):
    """Create a study in SONG using the SONG API"""
    try:
        logger.info(f"Creating SONG study: {study_data.get('study_id')}")
        
        # Prepare SONG study payload (simplified)
        song_payload = {
            "studyId": study_data.get('study_id'),
            "name": study_data.get('name'),
            "description": study_data.get('description'),
            "organization": study_data.get('organization', 'SANBI'),
            "info": {}  # Empty info object for simplified schema
        }
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        # Create study in SONG
        song_url = f"http://song:8080/studies/{study_data.get('study_id')}/"
        response = requests.post(song_url, headers=headers, json=song_payload, timeout=30)
        
        if response.status_code in [200, 201]:
            logger.info(f"Successfully created SONG study: {study_data.get('study_id')}")
            return response.json()
        else:
            logger.error(f"Failed to create SONG study: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error creating SONG study: {e}")
        return None
