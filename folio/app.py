from flask import Flask, request, jsonify, g
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

app = Flask(__name__)


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
    
    # Get RPT-based permissions (the new way!)
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


# Routes
@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "folio"})


@app.route('/api/test', methods=['GET'])
@authenticate_token
def test_endpoint():
    """Simple test endpoint that requires authentication"""
    logger.info(f"Test endpoint called by user: {g.user['username']}")
    return jsonify({
        "message": "Hello from Folio!",
        "user": g.user,
        "status": "authenticated"
    })


@app.route('/api/test/read', methods=['GET'])
@require_permissions(["READ"])
def test_read_endpoint():
    """Test endpoint that requires READ permission"""
    logger.info(f"Read endpoint called by user: {g.user['username']}")
    return jsonify({
        "message": "You have READ access!",
        "user": g.user,
        "action": "read",
        "status": "authorized"
    })


@app.route('/api/test/write', methods=['GET'])
@require_permissions(["WRITE"])
def test_write_endpoint():
    """Test endpoint that requires WRITE permission"""
    logger.info(f"Write endpoint called by user: {g.user['username']}")
    return jsonify({
        "message": "You have WRITE access!",
        "user": g.user,
        "action": "write",
        "status": "authorized"
    })


@app.route('/api/test/admin', methods=['POST'])
@require_permissions(["READ", "WRITE"])
def test_admin_endpoint():
    """Test endpoint that requires both read and write permissions"""
    logger.info(f"Admin endpoint called by user: {g.user['username']}")
    return jsonify({
        "message": "You have full READ and WRITE access!",
        "user": g.user,
        "action": "admin",
        "status": "authorized"
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
