# AGARI Genomics Data Management Stack

A complete Kubernetes-based genomics data management platform with Song, Score, Keycloak, Arranger, and supporting services.

## Quick Deploy

This guide will deploy the complete stack on a Kubernetes cluster. All commands should be run from the repository root.

### Prerequisites

- Kubernetes cluster (k3d recommended for dev)
- kubectl configured
- Helm 3.x installed
- nginx-ingress controller

### 1. Setup k3d Cluster (Dev)

```bash
# Create k3d cluster with 3 nodes and port mapping
k3d cluster create agari-dev --agents 2 --port "80:80@loadbalancer"

# Install nginx ingress
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.1/deploy/static/provider/cloud/deploy.yaml

# Wait for ingress to be ready
kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=300s

```

### 2. Create Namespace

```bash
kubectl create namespace agari-dev
```

### 3. Deploy Infrastructure Services

#### PostgreSQL Databases
```bash
# Keycloak database  
helm install keycloak-db ./helm/databases -n agari-dev -f values-keycloak-db.yaml

# Song database
helm install song-db ./helm/databases -n agari-dev -f values-song-db.yaml
```

#### MinIO Object Storage
```bash
helm install minio ./helm/minio -n agari-dev -f values-minio.yaml

# List Buckets
kubectl exec -it minio-86c949747-dkfzg -n agari-dev -- mc ls localminio
```

#### Kafka
```bash
helm install kafka ./helm/kafka -n agari-dev -f values-kafka.yaml
```

#### Keycloak Authentication
```bash
# Create configuration ConfigMap
kubectl create configmap keycloak-config --from-file=configs/keycloakConfigs/ -n agari-dev

# Deploy Keycloak
helm install keycloak ./helm/keycloak -n agari-dev -f values-keycloak.yaml
```

### 4. Deploy Genomics Services

#### Song Metadata Service
```bash
helm install song ./helm/song -n agari-dev -f values-song.yaml
```

#### Score File Service
```bash
helm install score ./helm/score -n agari-dev -f values-score.yaml
```

#### Elasticsearch
```bash
helm install elasticsearch ./helm/elasticsearch -n agari-dev -f values-elasticsearch.yaml
```

#### Maestro Workflow Orchestration
```bash
helm install maestro ./helm/maestro -n agari-dev -f values-maestro.yaml
```

### 5. Deploy Data Portal

#### Create Arranger Configuration
```bash
# Create ConfigMap from Arranger configs
kubectl create configmap arranger-config --from-file=configs/arrangerConfigs/ -n agari-dev

# Deploy Arranger
helm install arranger ./helm/arranger -n agari-dev -f values-arranger.yaml
```

#### Load Sample Data into Elasticsearch
```bash
# Create index
kubectl exec -i elasticsearch-<pod-name> -n agari-dev -- curl -X PUT "localhost:9200/overture-quickstart-index" -H "Content-Type: application/json" -d '{
  "settings": {
    "analysis": {
      "analyzer": {
        "autocomplete_analyzed": {
          "filter": ["lowercase", "edge_ngram"],
          "tokenizer": "standard"
        },
        "autocomplete_prefix": {
          "filter": ["lowercase", "edge_ngram"],
          "tokenizer": "keyword"
        },
        "lowercase_keyword": {
          "filter": ["lowercase"],
          "tokenizer": "keyword"
        }
      },
      "filter": {
        "edge_ngram": {
          "max_gram": "20",
          "min_gram": "1",
          "side": "front",
          "type": "edge_ngram"
        }
      }
    },
    "index.max_result_window": 300000,
    "index.number_of_shards": 1
  }
}'

# Load sample documents
cd configs/elasticsearchConfigs/es-docs
for doc in *.json; do
  kubectl exec -i elasticsearch-<pod-name> -n agari-dev -- curl -X POST "localhost:9200/overture-quickstart-index/_doc/$(basename $doc .json)" -H "Content-Type: application/json" -d @- < "$doc"
done
```

## Service Access

After deployment, services are available at:

- **Arranger Data Portal**: http://arranger.local/graphql
- **Song API**: http://song.local
- **Score API**: http://score.local  
- **Maestro**: http://maestro.local
- **Keycloak**: http://keycloak.local
- **MinIO Console**: http://minio-console.local
- **Elasticsearch**: http://elasticsearch.local

## Authentication

### Keycloak Credentials
- **Admin**: admin / admin123
- **Realm**: myrealm
- **User**: admin@example.com (imported with API keys)

### API Key for Song/Score
```
API Key: F4C094A60BA88FB3F42BB9D20D75931286549D7F3C2E448F62D81CE20237B9BC
Scopes: score.READ, score.WRITE, song.READ, song.WRITE
```

## Monitoring

Check service status:
```bash
kubectl get pods -n agari-dev
kubectl get ingress -n agari-dev
```

View logs:
```bash
kubectl logs <pod-name> -n agari-dev
```

## Configuration Files

- `values-*.yaml`: Helm values for each service
- `configs/keycloakConfigs/`: Keycloak realm and user configurations
- `configs/arrangerConfigs/`: Arranger UI configuration files
- `configs/elasticsearchConfigs/`: Sample genomic data and index templates

## Production Notes

For production deployment:

1. **Update hostnames** in values files from `.local` to your domain
2. **Configure TLS certificates** for HTTPS
3. **Set production passwords** (not the development defaults)
4. **Scale resources** based on your workload requirements
5. **Configure persistent storage** for databases and MinIO
6. **Set up monitoring** and logging solutions

## Service Dependencies

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│   Keycloak  │◄───│  PostgreSQL  │    │    MinIO    │
└─────────────┘    └──────────────┘    └─────────────┘
       │                                       │
       ▼                                       ▼
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│    Song     │◄───│  PostgreSQL  │───►│    Score    │
└─────────────┘    └──────────────┘    └─────────────┘
       │                                       │
       ▼                                       ▼
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│   Kafka     │◄───│   Maestro    │───►│Elasticsearch│
└─────────────┘    └──────────────┘    └─────────────┘
                                              │
                                              ▼
                                    ┌─────────────┐
                                    │  Arranger   │
                                    └─────────────┘
```

## Sample GraphQL Queries

Test Arranger data portal:

```graphql
{
  file {
    hits {
      total
      edges {
        node {
          object_id
          data_type
          file_type
        }
      }
    }
  }
}
```