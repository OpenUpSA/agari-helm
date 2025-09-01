from flask import Flask, request, jsonify, g
from flask_restx import Api, Resource, fields, Namespace
import logging
import requests
import os
import jwt
from functools import wraps
import time

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


def get_service_token():
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
                return jsonify({'error': 'No token provided'}), 401
            
            token = auth_header.split(' ')[1]
            
            # Validate the JWT token and get RPT permissions
            payload = validate_jwt_token(token)
            if not payload:
                return jsonify({'error': 'Invalid token'}), 401
            
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
                return jsonify({
                    'error': f'Missing required RPT permissions: {missing_scopes}',
                    'user_permissions': user_permissions,
                    'rpt_permissions': user_info.get('rpt_permissions', [])
                }), 403
            
            # Store user info in g
            g.user = user_info
            
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


@app.route('/debug')
def debug_routes():
    """Debug endpoint to show all registered routes"""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            'endpoint': rule.endpoint,
            'methods': list(rule.methods),
            'rule': rule.rule
        })
    return {"routes": routes}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
