# Blueprint Hotel Reservation - SRE Architecture Reference

## Overview

Pre-packaged variant of Hotel Reservation with no source code — only Kubernetes manifests and pre-built container images (777lefty/*). Same logical architecture as hotelReservation but with different images, port schemes, and a custom workload generator.

## Service Topology

8 microservices communicating via gRPC (port 12345 on container), plus 6 MongoDB instances, 3 Memcached caches, and Jaeger tracing.

| Service | Container Port | Protocol | Dependencies |
|---------|---------------|----------|--------------|
| **frontend-service** | 2000 | HTTP | profile, recomd, reserv, search, user services |
| **search-service** | 12345 | gRPC | geo-service, rate-service |
| **geo-service** | 12345 | gRPC | geo-db (MongoDB) |
| **rate-service** | 12345 | gRPC | rate-db (MongoDB), rate-cache (Memcached) |
| **profile-service** | 12345 | gRPC | profile-db (MongoDB), profile-cache (Memcached) |
| **recomd-service** | 12345 | gRPC | recomd-db (MongoDB) |
| **user-service** | 12345 | gRPC | user-db (MongoDB) |
| **reserv-service** | 12345 | gRPC | reserv-db (MongoDB), reserv-cache (Memcached) |

## Inter-Service Call Graph

```
Frontend (HTTP:2000)
├─→ Search (gRPC:12345)
│   ├─→ Geo (gRPC:12345) → geo-db (MongoDB:27017)
│   └─→ Rate (gRPC:12345) → rate-db (MongoDB:27017) + rate-cache (Memcached:11211)
├─→ Profile (gRPC:12345) → profile-db (MongoDB:27017) + profile-cache (Memcached:11211)
├─→ Recomd (gRPC:12345) → recomd-db (MongoDB:27017)
├─→ User (gRPC:12345) → user-db (MongoDB:27017)
└─→ Reserv (gRPC:12345) → reserv-db (MongoDB:27017) + reserv-cache (Memcached:11211)

All services → Jaeger (14268)
```

## Database & Storage

| Instance | Type | Image | Port | Serves |
|----------|------|-------|------|--------|
| geo-db | MongoDB | mongo:latest | 27017 | geo-service |
| profile-db | MongoDB | mongo:latest | 27017 | profile-service |
| rate-db | MongoDB | mongo:latest | 27017 | rate-service |
| recomd-db | MongoDB | mongo:latest | 27017 | recomd-service |
| user-db | MongoDB | mongo:latest | 27017 | user-service |
| reserv-db | MongoDB | mongo:latest | 27017 | reserv-service |
| profile-cache | Memcached | memcached:latest | 11211 | profile-service |
| rate-cache | Memcached | memcached:latest | 11211 | rate-service |
| reserv-cache | Memcached | memcached:latest | 11211 | reserv-service |

## Configuration

### Global gRPC Config (rpc-configmap.yaml)
```
GRPC_CLIENT_RETRIES_ON_ERROR: 1
GRPC_CLIENT_TIMEOUT: 1s
```

### Per-Service Config (via ConfigMaps)

Each service has a ConfigMap with:
- `*_GRPC_BIND_ADDR`: `0.0.0.0:12345` (listen address)
- `JAEGER_DIAL_ADDR`: `jaeger:14268` (tracing endpoint)
- `*_DB_DIAL_ADDR`: `<db-name>:27017` (MongoDB connection)
- `*_CACHE_DIAL_ADDR`: `<cache-name>:11211` (Memcached connection, where applicable)
- Downstream service addresses (e.g., frontend → `search-service:12345`)

### Frontend Config
```
FRONTEND_SERVICE_HTTP_BIND_ADDR: 0.0.0.0:2000
PROFILE_SERVICE_GRPC_DIAL_ADDR: profile-service:12345
RECOMD_SERVICE_GRPC_DIAL_ADDR: recomd-service:12345
RESERV_SERVICE_GRPC_DIAL_ADDR: reserv-service:12345
SEARCH_SERVICE_GRPC_DIAL_ADDR: search-service:12345
USER_SERVICE_GRPC_DIAL_ADDR: user-service:12345
```

## Kubernetes Service Port Mapping

Each K8s Service exposes both a custom port and the container port:

| Service | K8s Port | Container Port | Type |
|---------|----------|---------------|------|
| frontend-service | 12345, 2000 | 2000 | ClusterIP |
| search-service | 12361, 12345 | 12345 | ClusterIP |
| geo-service | 12347, 12345 | 12345 | ClusterIP |
| rate-service | 12355, 12345 | 12345 | ClusterIP |
| profile-service | 12352, 12345 | 12345 | ClusterIP |
| recomd-service | 12357, 12345 | 12345 | ClusterIP |
| user-service | 12363, 12345 | 12345 | ClusterIP |
| reserv-service | 12360, 12345 | 12345 | ClusterIP |
| jaeger | 12348/12349, 14268/16686 | 14268/16686 | NodePort |

Database and cache services follow similar dual-port pattern.

## Container Images

All pre-built, hosted on Docker Hub:
- `777lefty/docker-frontend-service-container:latest`
- `777lefty/docker-search-service-container:latest`
- `777lefty/docker-geo-service-container:latest`
- `777lefty/docker-rate-service-container:latest`
- `777lefty/docker-profile-service-container:latest`
- `777lefty/docker-recomd-service-container:latest`
- `777lefty/docker-user-service-container:latest`
- `777lefty/docker-reserv-service-container:latest`
- `777lefty/wlgen-proc:latest` (workload generator)
- `mongo:latest` (all databases)
- `memcached:latest` (all caches)
- `jaegertracing/all-in-one:latest`

Image pull policy: IfNotPresent. No source code included.

## Observability

- **Jaeger**: all-in-one, ports 14268 (collector) and 16686 (UI)
- All services send traces to `jaeger:14268`
- Jaeger UI exposed via NodePort

## Workload Generator

Custom tool `wlgen_proc` deployed as a Kubernetes Job:

| Parameter | Value | Description |
|-----------|-------|-------------|
| FRONTEND_SERVICE_HTTP_DIAL_ADDR | frontend-service.blueprint-hotel-reservation:12345 | Target endpoint |
| DURATION | 120s | Test duration |
| TPUT | 3000 | Throughput target (req/s) |
| MULTIPLIER | 6 | Load multiplier |
| STABLETIME | 60 | Seconds at stable load |
| TRIGGERTIME | 30 | Seconds at triggered load |
| REVERTTIME | 30 | Seconds reverting to stable |
| OUTFILE | stats.csv | Statistics output |

## Differences from hotelReservation

| Aspect | hotelReservation | BlueprintHotelReservation |
|--------|-----------------|--------------------------|
| Source code | Full Go source in cmd/, services/ | None — pre-built images only |
| Images | yinfangchen/hotelreservation | 777lefty/docker-*-container |
| Deployment | Helm chart + raw K8s manifests | Raw K8s manifests only |
| Service ports | Unique per service (8081-8087) | All gRPC on 12345 |
| Frontend port | 5000 | 2000 |
| Config | config.json file | Environment variables via ConfigMaps |
| Service discovery | Consul | Kubernetes DNS |
| Load testing | wrk2 with Lua scripts | wlgen_proc custom tool |
| Docker Compose | Yes | No |

## Deployment

Namespace: `blueprint-hotel-reservation`

```bash
kubectl create namespace blueprint-hotel-reservation
kubectl apply -f kubernetes/ -R -n blueprint-hotel-reservation
# To run load test:
kubectl apply -f wlgen/ -n blueprint-hotel-reservation
```

## File Layout

```
BlueprintHotelReservation/
├── kubernetes/
│   ├── rpc-configmap.yaml          # Global gRPC config
│   ├── frontend/                   # 3 files (deployment, service, configmap)
│   ├── search/                     # 3 files
│   ├── geo/                        # 5 files (+ geo-db)
│   ├── rate/                       # 7 files (+ rate-db, rate-cache)
│   ├── profile/                    # 7 files (+ profile-db, profile-cache)
│   ├── recommend/                  # 5 files (+ recomd-db)
│   ├── user/                       # 5 files (+ user-db)
│   ├── reservation/                # 7 files (+ reserv-db, reserv-cache)
│   └── jaeger/                     # 2 files
└── wlgen/
    ├── wlgen_proc-configmap.yaml   # Load test config
    └── wlgen_proc-job.yaml         # Load test job
```
