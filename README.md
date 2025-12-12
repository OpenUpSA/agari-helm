# AGARI Genomics Data Management Stack

Complete Agari genomics Stack deployment on Kubernetes with authentication, file management, and data indexing.

![Architecture Diagram](Agari-Genomics-Platform.png)

## Prerequisites

- Kubernetes cluster (k3d recommended for dev)
- kubectl configured  
- Helm 3.x installed
- nginx-ingress controller

## Quick Deploy

### 1. Setup Cluster

In dev you might want to use **k3d** for quick setup:

```bash
k3d cluster create agari --agents 2 --port "80:80@loadbalancer"

# Install nginx ingress
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.1/deploy/static/provider/cloud/deploy.yaml

# Wait for readiness
kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=300s
```

### 2. Create Namespace

```bash
kubectl create namespace agari
```

### 3. Minio Object Storage


```bash
helm install minio ./helm/minio -n agari

# Minio might require prot-forwarding:
kubectl port-forward -n agari service/minio 9000:9000
```

### 4. Keycloak

```bash
# Database
helm install keycloak-db ./helm/keycloak-db -n agari

# Keycloak
helm install keycloak ./helm/keycloak -n agari
```

Set up the **client** in Keycloak and copy the **secret** to **folio** `values.yaml`

use `utils/update-secrets.sh` script to update the secrets in all services


### 5. Elasticsearch

```bash
# Elasticsearch
helm install elasticsearch ./helm/elasticsearch -n agari

# Create agari-index with proper mapping
curl -X PUT "http://elasticsearch.local/agari-index" \
    -H "Content-Type: application/json" \
    -d @helm/elasticsearch/configs/agari-index-mapping.json
```


### 6. Folio

**Find Folio repo at [https://github.com/OpenUpSA/agari-folio](https://github.com/OpenUpSA/agari-folio)**

```bash
# Database
helm install folio-db ./helm/folio-db -n agari

# Folio
helm install folio ./helm/folio -n agari

```

### 7. Folio Worker

**Find Folio repo at [https://github.com/OpenUpSA/agari-folio](https://github.com/OpenUpSA/agari-folio)**

```bash
# Folio Worker
helm install folio-worker ./helm/folio-worker -n agari

```


## Ingress Configuration

For local development, you can use `/etc/hosts` to map the services:

```bash
echo "127.0.0.1 keycloak.local
127.0.0.1 elasticsearch.local
127.0.0.1 minio-console.local
127.0.0.1 folio.local" | sudo tee -a /etc/hosts
```

## Service Access

Services are available at these URLs:

- **Keycloak**: http://keycloak.local
- **Elasticsearch**: http://elasticsearch.local
- **MinIO Console**: http://minio-console.local
- **Folio**: http://folio.local/docs

## Authentication and Authorization

### Default Credentials
- **Keycloak Admin**: admin / admin123

### Basic End to End Requirements

- **Realm**: `agari`

  - **Group**:
    - `admin`
  - **User**:
    - `admin` / `admin123` (member of `admin` group) 
  - **Client**:
    - `dms` - Data Management System (for Folio). Policy enforcement: `permissive` and Decision strategy: `affirmative`
      - **Scopes**:
        - `READ`
        - `WRITE`
        - `ADMIN`
      - **Resources**:
        - `folio` - Folio API - with `READ` and `WRITE` scopes
      - **Policies**:
        - `admin-policy` - group policy - with `admin` group
        - `client-policy` - client policy - with `dms` client
      - **Permissions**:
        - `admin-permission` - resources `folio` with `admin-policy`
        - `client-permission` - resources `folio` with `client-policy`
      - **Service account roles**:
        - `realm-admin` - to allow service account (folio) to manage users and roles programmatically

### JWT Token Example
```bash
# Get JWT token from Keycloak
curl -d "client_id=song-api" \
     -d "client_secret=song-secret" \
     -d "username=admin@example.com" \
     -d "password=admin123" \
     -d "grant_type=password" \
     "http://keycloak.local/realms/agari/protocol/openid-connect/token"
```

## Troubleshooting

### Check service status
```bash
kubectl get pods -n agari
kubectl get ingress -n agari
```

### View logs
```bash
kubectl logs <pod-name> -n agari
```

### Pause and unpause all pods in a namespace

Great for freeing up some system resources when idle

```bash
kubectl scale --replicas=0 deployment --all -n agari
kubectl scale --replicas=1 deployment --all -n agari
```

## Configuration

Key configuration files:
- `helm/*/values.yaml` - Service configurations
- `helm/elasticsearch/configs/agari-index-mapping.json` - Elasticsearch schema