# Hotel Reservation - SRE Architecture Reference

## Service Topology

7 backend microservices + 1 frontend, communicating via gRPC. Infrastructure: Consul (service discovery), Jaeger (tracing), MongoDB (storage), Memcached (caching).

| Service | Port | Protocol | Dependencies | Function |
|---------|------|----------|--------------|----------|
| **frontend** | 5000 | HTTP | search, profile, recommendation, user, reservation | HTTP entry point routing to backend gRPC services |
| **profile** | 8081 | gRPC | mongodb-profile, memcached-profile | Hotel metadata (name, phone, description, address, images) |
| **search** | 8082 | gRPC | geo, rate | Finds nearby hotels, returns sorted by rate |
| **geo** | 8083 | gRPC | mongodb-geo | Geolocation - KNN index finds 5 nearest hotels within 10km |
| **rate** | 8084 | gRPC | mongodb-rate, memcached-rate | Hotel pricing/rate plans |
| **recommendation** | 8085 | gRPC | mongodb-recommendation | Filters hotels by distance, rate, or price |
| **user** | 8086 | gRPC | mongodb-user | User authentication (SHA256 password hashing) |
| **reservation** | 8087 | gRPC | mongodb-reservation, memcached-reserve | Checks availability, makes reservations with capacity tracking |

## Inter-Service Call Graph

```
Frontend (HTTP:5000)
├─→ Search (gRPC:8082)
│   ├─→ Geo (gRPC:8083) → MongoDB-Geo (27017)
│   └─→ Rate (gRPC:8084) → MongoDB-Rate (27017) + Memcached-Rate (11211)
├─→ Profile (gRPC:8081) → MongoDB-Profile (27017) + Memcached-Profile (11211)
├─→ Recommendation (gRPC:8085) → MongoDB-Recommendation (27017)
├─→ User (gRPC:8086) → MongoDB-User (27017)
└─→ Reservation (gRPC:8087) → MongoDB-Reservation (27017) + Memcached-Reserve (11211)
```

## Frontend HTTP API

| Endpoint | Method | Parameters | Backend Calls |
|----------|--------|-----------|---------------|
| `/hotels` | GET | inDate, outDate, lat, lon, locale | Search.Nearby → Geo.Nearby + Rate.GetRates; Reservation.CheckAvailability; Profile.GetProfiles |
| `/recommendations` | GET | lat, lon, require (dis\|rate\|price), locale | Recommendation.GetRecommendations; Profile.GetProfiles |
| `/user` | POST | username, password | User.CheckUser |
| `/reservation` | POST | inDate, outDate, hotelId, customerName, username, password, number | User.CheckUser; Reservation.MakeReservation |

## Database & Storage

### MongoDB (all on port 27017)

| Instance | Database | Collection(s) | Key Fields |
|----------|----------|---------------|------------|
| mongodb-geo | geo-db | geo | hotelId, lat, lon |
| mongodb-profile | profile-db | hotels | id, name, phoneNumber, description, address, images |
| mongodb-rate | rate-db | inventory | hotelId, inDate, outDate, roomType.totalRate |
| mongodb-recommendation | recommendation-db | recommendation | hotelId, lat, lon, rate, price |
| mongodb-user | user-db | user | username (key), password (SHA256) |
| mongodb-reservation | reservation-db | reservation, number | reservation: {hotelId, customerName, inDate, outDate, number}; number: {hotelId, numberOfRoom} |

### Memcached (all on port 11211)

| Instance | Cached By | Cache Key Pattern | Purpose |
|----------|-----------|-------------------|---------|
| memcached-rate | rate | hotelId | RatePlan objects |
| memcached-profile | profile | hotelId | Hotel metadata objects |
| memcached-reserve | reservation | hotelId_date_date, hotelId_cap | Reservation counts and hotel capacity |

## Configuration

### config.json (mounted at application root)

```json
{
  "consulAddress": "consul:8500",
  "jaegerAddress": "jaeger:6831",
  "FrontendPort": "5000",
  "GeoPort": "8083",
  "GeoMongoAddress": "mongodb-geo:27017",
  "ProfilePort": "8081",
  "ProfileMongoAddress": "mongodb-profile:27017",
  "ProfileMemcAddress": "memcached-profile:11211",
  "RatePort": "8084",
  "RateMongoAddress": "mongodb-rate:27017",
  "RateMemcAddress": "memcached-rate:11211",
  "RecommendPort": "8085",
  "RecommendMongoAddress": "mongodb-recommendation:27017",
  "ReservePort": "8087",
  "ReserveMongoAddress": "mongodb-reservation:27017",
  "ReserveMemcAddress": "memcached-reserve:11211",
  "SearchPort": "8082",
  "UserPort": "8086",
  "UserMongoAddress": "mongodb-user:27017"
}
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| TLS | 0 | Enable TLS for gRPC/HTTP (0=off, 1=on) |
| GC | 100 | Go GC target percentage |
| JAEGER_SAMPLE_RATIO | 0.01 (compose), 1 (k8s) | Jaeger trace sampling ratio |
| MEMC_TIMEOUT | 2 | Memcached operation timeout (seconds) |
| LOG_LEVEL | INFO | Log verbosity (ERROR, WARNING, INFO, TRACE, DEBUG) |

## Service Startup Sequence

Each service (in `cmd/<service>/main.go`):
1. Loads `config.json` from working directory
2. Extracts service-specific addresses and ports
3. Connects to MongoDB via `mgo.Dial()`
4. Connects to Memcached (if applicable)
5. Initializes Jaeger tracer
6. Registers with Consul (service name: `srv-<service>`)
7. Starts gRPC server on configured port

Command flags: `-jaegeraddr`, `-consuladdr` (override config.json values)

## Kubernetes Deployment

- **Image**: `yinfangchen/hotelreservation:latest` (single image, all binaries compiled in; entrypoint selects service)
- **Replicas**: 1 per service
- **Resources**: requests 100m CPU, limits 1000m CPU; no memory limits set
- **Frontend Service**: LoadBalancer type on port 5000
- **All other services**: ClusterIP
- **MongoDB storage**: 1Gi PVC per instance (hostPath or configured StorageClass)
- **Tracing**: 100% sampling in K8s (`JAEGER_SAMPLE_RATIO=1`)

## Helm Chart

Location: `helm-chart/hotelreservation/` with 20 subcharts (one per service + infrastructure).

Key global values:
- `global.services.environments.tls`: TLS toggle
- `global.services.environments.logLevel`: log level
- `global.memcached.environments.cacheSize`: 128 MB default
- `global.mongodb.persistentVolume.size`: 1Gi default

## Workload Pattern

Load testing via wrk2 (`wrk2/scripts/hotel-reservation/mixed-workload_type_1.lua`):

| Request Type | Weight | Endpoint | Details |
|-------------|--------|----------|---------|
| Hotel Search | 60% | GET /hotels | Date range Apr 9-24 2015, SF Bay Area coords |
| Recommendations | 39% | GET /recommendations | Random criteria (dis/rate/price) |
| User Login | 0.5% | POST /user | 500 pre-defined users (Cornell_0..Cornell_500) |
| Reservation | 0.5% | POST /reservation | 80 hotel IDs, 1 room per reservation |

## Source Code Layout

| Component | Path |
|-----------|------|
| Service entry points | `cmd/<service>/main.go` |
| Service implementations | `services/<service>/server.go` |
| Proto definitions | `services/<service>/proto/` |
| Configuration | `config.json` |
| K8s manifests | `kubernetes/` |
| Helm chart | `helm-chart/hotelreservation/` |
| Load test scripts | `wrk2/scripts/hotel-reservation/` |
| Dockerfile | `Dockerfile` (Go 1.17.3 base, compiles all binaries) |
