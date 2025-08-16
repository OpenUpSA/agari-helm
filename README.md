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
# Check for existing clusters
k3d cluster list

# If cluster exists but is stopped, start it
k3d cluster start agari-dev

# If no cluster exists, create a new one with 3 nodes and port mapping
k3d cluster create agari-dev --agents 2 --port "80:80@loadbalancer"

# Install nginx ingress
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.1/deploy/static/provider/cloud/deploy.yaml

# Wait for ingress to be ready
kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=300s

# Verify cluster is running
kubectl cluster-info

```

### 2. Create Namespace

```bash
kubectl create namespace agari-dev
```

### 3. Deploy Infrastructure Services

#### PostgreSQL Databases
```bash
# Keycloak database  
helm install keycloak-db ./helm/keycloak-db -n agari-dev

# Song database
helm install song-db ./helm/song-db -n agari-dev
```

#### MinIO Object Storage
```bash
helm install minio ./helm/minio -n agari-dev

# List Buckets (after MinIO is running)
MINIO_POD=$(kubectl get pods -n agari-dev | grep minio | awk '{print $1}')
kubectl exec -it $MINIO_POD -n agari-dev -- mc ls localminio
```

#### Kafka
```bash
# Add Bitnami repository if not already added
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# Install Kafka using Bitnami chart with our values file
helm install kafka bitnami/kafka -f helm/kafka/values-bitnami.yaml -n agari-dev 
```

#### Keycloak Authentication
```bash
# Create configuration ConfigMap (only the JSON file, not the JAR)
kubectl create configmap keycloak-config --from-file=agari-realm-fixed.json=configs/keycloakConfigs/agari-realm-fixed.json -n agari-dev

# Deploy Keycloak
helm install keycloak ./helm/keycloak -n agari-dev
```

### 4. Deploy Genomics Services

#### Song Metadata Service
```bash
helm install song ./helm/song -n agari-dev
```

#### Score File Service
```bash
helm install score ./helm/score -n agari-dev
```

#### Elasticsearch
```bash
helm install elasticsearch ./helm/elasticsearch -n agari-dev
```


#### Load Sample Data into Elasticsearch
```bash
# First, get the actual Elasticsearch pod name
ES_POD=$(kubectl get pods -n agari-dev | grep elasticsearch | awk '{print $1}')
echo "Using Elasticsearch pod: $ES_POD"

# Create index
kubectl exec -i $ES_POD -n agari-dev -- curl -X PUT "localhost:9200/overture-quickstart-index" -H "Content-Type: application/json" -d '{
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
  kubectl exec -i $ES_POD -n agari-dev -- curl -X POST "localhost:9200/overture-quickstart-index/_doc/$(basename $doc .json)" -H "Content-Type: application/json" -d @- < "$doc"
done

# Verify data loaded successfully
kubectl exec -i $ES_POD -n agari-dev -- curl -X GET "localhost:9200/overture-quickstart-index/_count"

# Return to repository root
cd ../../..
```

#### Maestro Workflow Orchestration
```bash
helm install maestro ./helm/maestro -n agari-dev
```

### 5. Deploy Data Portal

#### Create Arranger Configuration
```bash
# Create ConfigMap from Arranger configs
kubectl create configmap arranger-config --from-file=configs/arrangerConfigs/ -n agari-dev

# Deploy Arranger
helm install arranger ./helm/arranger -n agari-dev
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
- **Realm**: agari
- **User**: admin@example.com (imported with API keys)



Without this mapper, SONG will return 403 "insufficient scope" errors.

### API Key for Song/Score
```
API Key: F4C094A60BA88FB3F42BB9D20D75931286549D7F3C2E448F62D81CE20237B9BC
Scopes: score.READ, score.WRITE, song.READ, song.WRITE
```

## Troubleshooting

### Arranger GraphQL Shows No Data

If Arranger GraphQL returns empty results, check if Elasticsearch has data:

```bash
# Check document count in Elasticsearch
ES_POD=$(kubectl get pods -n agari-dev | grep elasticsearch | awk '{print $1}')
kubectl exec -i $ES_POD -n agari-dev -- curl -s "localhost:9200/overture-quickstart-index/_count"

# If count is 0, reload sample data following the steps in section "Load Sample Data into Elasticsearch"
```

### Check Service Status

Check service status:
```bash
kubectl get pods -n agari-dev
kubectl get ingress -n agari-dev
```

View logs:
```bash
kubectl logs <pod-name> -n agari-dev
```

## Ingress

```bash
# Restart ingress
kubectl rollout restart deployment/ingress-nginx-controller -n ingress-nginx
```

## Configuration Files

- `values-*.yaml`: Helm values for each service
- `values-kafka-bitnami.yaml`: Bitnami Kafka chart configuration
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