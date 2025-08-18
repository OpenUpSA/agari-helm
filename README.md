# AGARI Genomics Data Management Stack

Complete Overture stack deployment on Kubernetes with authentication, file management, and data indexing.

## Prerequisites

- Kubernetes cluster (k3d recommended for dev)
- kubectl configured  
- Helm 3.x installed
- nginx-ingress controller

## Quick Deploy

### 1. Setup k3d Cluster

```bash
k3d cluster create agari-dev --agents 2 --port "80:80@loadbalancer"

# Install nginx ingress
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.1/deploy/static/provider/cloud/deploy.yaml

# Wait for readiness
kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=300s
```

### 2. Create Namespace

```bash
kubectl create namespace agari-dev
```

### 3. Deploy Infrastructure

```bash
# Databases
helm install keycloak-db ./helm/keycloak-db -n agari-dev
helm install song-db ./helm/song-db -n agari-dev

# Object storage
helm install minio ./helm/minio -n agari-dev

# Message queue
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install kafka bitnami/kafka -f helm/kafka/values-bitnami.yaml -n agari-dev

# Authentication (Keycloak 22.0.5 for SONG compatibility)
helm install keycloak ./helm/keycloak -n agari-dev
```

### 4. Deploy Overture Stack

```bash
# Core services  
helm install song ./helm/song -n agari-dev
helm install score ./helm/score -n agari-dev

# Search and indexing
helm install elasticsearch ./helm/elasticsearch -n agari-dev
helm install maestro ./helm/maestro -n agari-dev

# GraphQL API
helm install arranger ./helm/arranger -n agari-dev
```

### 5. Create Elasticsearch Index

```bash
# Create agari-index with proper mapping
curl -X PUT "http://elasticsearch.local/agari-index" \
    -H "Content-Type: application/json" \
    -d @helm/elasticsearch/configs/agari-index-mapping.json

# Restart Arranger to detect new index
kubectl rollout restart deployment/arranger -n agari-dev
```

## Service Access

Services are available at these URLs:

- **SONG API**: http://song.local
- **Score API**: http://score.local  
- **Arranger GraphQL**: http://arranger.local/graphql
- **Keycloak**: http://keycloak.local
- **Elasticsearch**: http://elasticsearch.local
- **MinIO Console**: http://minio-console.local

## Authentication

### Default Credentials
- **Keycloak Admin**: admin / admin123
- **API Key**: F4C094A60BA88FB3F42BB9D20D75931286549D7F3C2E448F62D81CE20237B9BC

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

## Data Flow

1. **Submit metadata** → SONG validates and stores in PostgreSQL
2. **Upload files** → Score stores in MinIO object storage  
3. **Analysis events** → Kafka message queue
4. **Index data** → Maestro processes and indexes in Elasticsearch
5. **Query data** → Arranger provides GraphQL API

## Troubleshooting

### Check service status
```bash
kubectl get pods -n agari-dev
kubectl get ingress -n agari-dev
```

### View logs
```bash
kubectl logs <pod-name> -n agari-dev
```


## Configuration

Key configuration files:
- `helm/*/values.yaml` - Service configurations
- `helm/elasticsearch/configs/agari-index-mapping.json` - Elasticsearch schema
- `helm/keycloak/configs/agari-realm.json` - Keycloak realm setup