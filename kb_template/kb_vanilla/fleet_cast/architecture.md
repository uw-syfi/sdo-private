# FleetCast (Satellite Operations Simulator) - SRE Architecture Reference

## Service Topology

2-tier web application (React frontend + FastAPI backend) with TiDB distributed database. Deployed via Helm on Kubernetes with TiDB Operator.

| Service | Port | Technology | Replicas | Purpose |
|---------|------|-----------|----------|---------|
| **Frontend** | 80 | React 19 + Nginx Alpine | 2 | SPA dashboard with satellite stats and ground station telemetry (5s polling) |
| **Backend** | 5000 | Python 3.10 FastAPI/Uvicorn | 2 | REST API, satellite simulation (10s scheduled), telemetry generation |
| **TiDB** | 4000 | Distributed SQL (MySQL-compatible) v6.5.0 | 1 | Primary data store (satellite_sim database) |
| **TiDB PD** | 2379 | Placement Driver | 1 | Cluster metadata and scheduling |
| **TiKV** | 20160 | Key-Value store | 1 | Distributed storage engine |

## Inter-Service Communication

```
Ingress (orbital.local:8080, Nginx)
├─ / → Frontend (ClusterIP:80)
│      └─ Axios polling every 5s
│         ├─ GET /api/dashboard
│         └─ GET /api/station/{id}
│
└─ /api → Backend (ClusterIP:5000)
          ├─→ TiDB (4000, MySQL protocol over TLS)
          ├─→ Jaeger (14268, OTLP traces)
          └─→ Prometheus scrape (/metrics)
```

## API Endpoints

| Endpoint | Method | Purpose | Response |
|----------|--------|---------|----------|
| `/api/health` | GET | Liveness/readiness probe | `{"status": "ok"}` |
| `/api/dashboard` | GET | Mission control summary | totalSatellites, activeContacts, lowBattery, errorState, totalTelemetry |
| `/api/station/{station_id}` | GET | Ground station telemetry | List of satellites in contact with battery, temperature, status |
| `/api/simulate` | GET | Manual simulation trigger | Runs simulation cycle and logs to TiDB |
| `/metrics` | GET | Prometheus metrics | HTTP request counts, latencies, sizes |

## Database Schema (TiDB: `satellite_sim`)

### `telemetry` table
| Column | Type | Description |
|--------|------|-------------|
| satellite_id | FK | SAT-1 to SAT-100 |
| ground_station_id | FK | GS-1 to GS-7 |
| timestamp | DATETIME | When telemetry was recorded |
| battery_level | FLOAT | 20-100% |
| temperature | FLOAT | -40 to 85°C |
| position_lat | FLOAT | Satellite latitude |
| position_lon | FLOAT | Satellite longitude |
| status | ENUM | OK, LOW_POWER, ERROR, MAINTENANCE |

### `contact_windows` table
| Column | Type | Description |
|--------|------|-------------|
| satellite_id | FK | Satellite identifier |
| ground_station_id | FK | Ground station identifier |
| start_time | DATETIME | Contact window start |
| end_time | DATETIME | Contact window end |
| distance | FLOAT | Distance in km |
| datavolume | INT | Data volume in MB |
| priority | INT | 1, 2, or 3 |
| assigned | BOOLEAN | Whether contact is assigned |

### Simulation Logic
- 100 satellites (SAT-1 to SAT-100), orbit period 90-180 min, priority 1-3
- 7 ground stations (GS-1 to GS-7), capacity 1-10 concurrent contacts
- Contact window generated when satellite within 5000km of station (haversine distance)
- Simulation runs every 10 seconds via scheduler
- Generates synthetic telemetry for assigned contacts
- Cleans expired contact windows

## Configuration

### Environment Variables (Backend)
| Variable | Value | Purpose |
|----------|-------|---------|
| TIDB_HOST | basic-tidb.tidb-cluster.svc.cluster.local | TiDB connection host |
| TIDB_USER | root | Database user |
| TIDB_PASSWORD | (from values.secret.yaml) | Database password |
| TIDB_DATABASE | satellite_sim | Database name |
| TIDB_CA_PEM | /app/tidb-ca.pem | TLS CA certificate path |
| OTEL_SERVICE_NAME | fleetcast | OpenTelemetry service name |
| OTEL_TRACES_EXPORTER | jaeger | Trace exporter type |
| OTEL_EXPORTER_JAEGER_ENDPOINT | http://jaeger-agent.observe.svc.cluster.local:14268/api/traces | Jaeger endpoint |

### CORS Origins
- `http://localhost:3000`, `http://localhost:3001`
- `http://orbital.local:8080`
- `127.0.0.1:3000`

## Kubernetes Deployment

### Resources
| Component | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|-----------|-----------|---------------|-------------|
| Frontend | 50m | 250m | 64Mi | 128Mi |
| Backend | 100m | 500m | 128Mi | 256Mi |
| TiDB/PD/TiKV | - | - | - | - |

### Health Probes
- **Frontend**: Liveness `GET /` (30s interval), Readiness `GET /` (10s interval)
- **Backend**: Liveness `GET /api/health` (30s interval), Readiness `GET /api/health` (10s interval)

### Helm Chart (`satellite-app/`)
- Ingress: Nginx class, host `orbital.local`, routes `/` → frontend, `/api` → backend
- HPA: Disabled by default (min 1, max 5, target 80% CPU)
- TiDB CA cert mounted via ConfigMap `tidb-ca-configmap`
- Storage: `local-path` StorageClass, 1Gi per TiDB component

### TiDB Operator (v1.6.3)
- Manages TiDB cluster lifecycle via `TidbCluster` CRD
- Auto-failover: 5m period for PD, TiKV, TiDB
- Namespace: `tidb-cluster`

## Docker Build

### Backend
```
Base: python:3.10-slim
Entry: uvicorn server:app --host 0.0.0.0 --port 5000
```

### Frontend
```
Build stage: node:18 (npm install + npm run build)
Runtime: nginx:alpine (serves static files)
```

### Images
- Frontend: `lilygniedz/orbital-frontend:latest`
- Backend: `lilygniedz/satellite-backend:latest`

## Observability

### Tracing (OpenTelemetry)
- Instrumented spans: scheduled_simulation, simulate_and_log, fetch_dashboard_data, query_low_battery, query_error_states, query_active_contacts, get_station_data, health_check
- Span attributes: db.system, db.operation, station.id, station.satellite_count, telemetry counts

### Metrics (Prometheus)
- Backend annotated with `prometheus.io/scrape: "true"` on port 5000 at `/metrics`
- Prometheus scrapes: self, kubelet/cAdvisor, node-exporter, annotated pods
- Prometheus server: NodePort 32000, 8Gi persistent volume

## Workload Pattern

Load testing via wrk (`wrk/wrk.lua`):

| Request Type | Weight | Endpoint |
|-------------|--------|----------|
| Dashboard | 50% | GET /api/dashboard |
| Station | 70% | GET /api/station/GS-{001-010} |
| Simulate | 10% | GET /api/simulate |

## Source Code Layout

| Component | Path |
|-----------|------|
| Backend app | `backend/server.py` |
| Simulation logic | `backend/satellite_config.py` |
| Backend deps | `backend/requirements.txt` |
| Frontend app | `frontend/src/App.js`, `Dashboard.jsx`, `Station.jsx` |
| Frontend nginx | `frontend/nginx.conf` |
| Helm chart | `satellite-app/` |
| TiDB operator | `tidb-operator/` |
| TiDB cluster def | `tidb-operator/tidb-cluster.yaml` |
| Prometheus config | `prometheus/prometheus.yaml` |
| Load test | `wrk/wrk.lua` |
| Docker Compose | `docker-compose.yml` |
