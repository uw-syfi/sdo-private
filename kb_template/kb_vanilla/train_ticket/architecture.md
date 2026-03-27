# Train Ticket - SRE Architecture Reference

## Overview

Large-scale train ticket booking system with 45 Java/Spring Boot microservices, Spring Cloud Gateway, Nacos service discovery, MySQL cluster, and RabbitMQ. Deployed on Kubernetes via Helm charts.

## Service Topology

### Gateway & Frontend

| Service | Port | NodePort | Purpose |
|---------|------|----------|---------|
| **ts-gateway-service** | 18888 | 30467 | Spring Cloud Gateway (reactive/WebFlux) — routes all API traffic |
| **ts-ui-dashboard** | 8080 | 32677 | Web UI frontend |

### Core Services (45 microservices)

**User & Auth:**
| Service | Port | Database | Purpose |
|---------|------|----------|---------|
| ts-auth-service | 12340 | MySQL | Authentication |
| ts-user-service | 12342 | MySQL | User management |
| ts-verification-code-service | 15678 | - | Verification codes |
| ts-security-service | 11188 | MySQL | Security |

**Travel & Routing:**
| Service | Port | Database | Purpose |
|---------|------|----------|---------|
| ts-travel-service | 12346 | MySQL | Travel/trip management |
| ts-travel2-service | 16346 | MySQL | Alternative travel service |
| ts-travel-plan-service | 14322 | - | Travel planning |
| ts-route-service | 11178 | MySQL | Route information |
| ts-route-plan-service | 14578 | - | Route planning |
| ts-station-service | 12345 | MySQL | Station information |
| ts-train-service | 14567 | MySQL | Train information |
| ts-basic-service | 15680 | - | Basic data service |

**Order & Booking:**
| Service | Port | Database | RabbitMQ | Purpose |
|---------|------|----------|----------|---------|
| ts-order-service | 12031 | MySQL | Yes | Main order processing |
| ts-order-other-service | 12032 | MySQL | Yes | Secondary order service |
| ts-wait-order-service | 17525 | MySQL | - | Waitlist orders |
| ts-preserve-service | 14568 | - | Yes | Ticket preservation/holds |
| ts-preserve-other-service | 14569 | - | Yes | Secondary preservation |
| ts-seat-service | 18898 | - | - | Seat management (no DB) |
| ts-cancel-service | 18885 | - | - | Order cancellation |
| ts-rebook-service | 18886 | - | Yes | Rebooking |

**Payment & Financial:**
| Service | Port | Database | Purpose |
|---------|------|----------|---------|
| ts-payment-service | 19001 | MySQL | Payment processing |
| ts-inside-payment-service | 18673 | MySQL | Internal payments |
| ts-price-service | 16579 | MySQL | Pricing |
| ts-assurance-service | 18888 | MySQL | Insurance/assurance |
| ts-voucher-service | 16101 | MySQL | Voucher management (Python) |

**Food Services:**
| Service | Port | Database | RabbitMQ | Purpose |
|---------|------|----------|----------|---------|
| ts-food-service | 18856 | MySQL | Yes | Food ordering |
| ts-food-delivery-service | 18957 | MySQL | - | Food delivery |
| ts-train-food-service | 19999 | MySQL | - | Train food |
| ts-station-food-service | 18855 | MySQL | - | Station food |

**Logistics & Other:**
| Service | Port | Database | RabbitMQ | Purpose |
|---------|------|----------|----------|---------|
| ts-delivery-service | 18808 | MySQL | Yes | Delivery management |
| ts-consign-service | 16111 | MySQL | - | Consignment shipping |
| ts-consign-price-service | 16110 | MySQL | - | Consignment pricing |
| ts-contacts-service | 12347 | MySQL | - | Contact management |
| ts-config-service | 15679 | MySQL | - | Configuration |
| ts-notification-service | 17853 | MySQL | Yes | Email notifications |
| ts-news-service | 12862 | - | - | News (no DB) |
| ts-execute-service | 12386 | - | - | Task execution (no DB) |
| ts-avatar-service | 17001 | - | - | Avatar/image |
| ts-ticket-office-service | 16108 | MySQL | - | Ticket office |

**Admin Services:**
| Service | Port | Purpose |
|---------|------|---------|
| ts-admin-basic-info-service | 18767 | Admin basic info |
| ts-admin-order-service | 16112 | Order admin |
| ts-admin-route-service | 16113 | Route admin |
| ts-admin-travel-service | 16114 | Travel admin |
| ts-admin-user-service | 16115 | User admin |

## Inter-Service Communication

```
Client → ts-ui-dashboard (8080)
       → ts-gateway-service (18888)
            ↓ (REST/HTTP, path-based routing)
         Nacos Service Discovery (8848)
            ↓ (lb:// load-balanced)
         45 microservices
            ↓
         MySQL Cluster (3306) ← 28 services share "ts" database
         RabbitMQ (5672) ← 8 services for async events
```

- **Synchronous**: REST/HTTP via Spring Cloud Gateway with Nacos load balancing
- **Asynchronous**: RabbitMQ for order, delivery, notification, food, preserve, rebook events
- **Service Discovery**: Nacos cluster (3-node StatefulSet)
- **Gateway Routing Pattern**: `/api/v1/{service}/**` → `lb://{service-name}`

## Database & Storage

### MySQL (RadonDB Cluster)

- **Topology**: 3-node cluster (tsdb-mysql-leader + 2 replicas) with Xenon HA coordination
- **Version**: Percona 5.7.34
- **Port**: 3306
- **Database**: `ts` (single shared database for all 28 services)
- **Credentials**: user `ts` / password `Ts_123456`
- **ORM**: Hibernate with `ddl-auto=update` (auto-schema migration)
- **Storage**: 1Gi PVC per node, ReadWriteOnce
- **Resource limits**: 256Mi request, 1Gi limit per MySQL pod

### Nacos MySQL (separate cluster)

- **Purpose**: Stores Nacos service registry and config data
- **Topology**: nacos-mysql-leader (separate from app data)
- **Credentials**: nacos / `Abcd1234#`
- **Init**: `initmysql` pod runs DB setup before Nacos starts

### RabbitMQ

- **Image**: codewisdom/rabbitmq:3
- **Port**: 5672 (AMQP)
- **Replicas**: 1
- **Services using it**: ts-order-service, ts-order-other-service, ts-preserve-service, ts-preserve-other-service, ts-rebook-service, ts-delivery-service, ts-food-service, ts-notification-service

### Nacos (Service Discovery & Config)

- **Port**: 8848
- **Version**: 2.0.1
- **Topology**: 3-node StatefulSet (nacos-0, nacos-1, nacos-2)
- **Headless service**: nacos-headless.default.svc.cluster.local
- **Mode**: Cluster
- **Resource limits**: 500m CPU, 1Gi memory per pod

## Configuration

### Environment Variables (common pattern)

```yaml
NACOS_ADDRS: nacos-0.nacos-headless.default.svc.cluster.local:8848,nacos-1...,nacos-2...
{SERVICE}_MYSQL_HOST: tsdb-mysql-leader
{SERVICE}_MYSQL_PORT: 3306
{SERVICE}_MYSQL_DATABASE: ts
{SERVICE}_MYSQL_USER: ts
{SERVICE}_MYSQL_PASSWORD: Ts_123456
rabbitmq_host: rabbitmq-service
rabbitmq_port: 5672
```

Config injected via Kubernetes Secrets (`secretRef`) and ConfigMaps (`configMapRef`).

### Per-Service Config

- Location: `{service}/src/main/resources/application.yml`
- MySQL connection via Spring Data JPA / JDBC
- Hibernate dialect: MySQL5Dialect
- DDL auto-migration enabled

## Kubernetes Deployment

### Resource Limits (per service pod)

| Component | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|-----------|-----------|---------------|-------------|
| Microservices | 100m | 500m | 300Mi | 2000Mi |
| UI Dashboard | 50m | 500m | 100Mi | 500Mi |
| MySQL (per node) | - | - | 256Mi | 1Gi |
| Nacos (per node) | - | - | - | 1Gi |
| RabbitMQ | 100m | - | 200Mi | - |

### Health Probes

- **Readiness**: TCP socket on service port, initial delay 60s, period 10s, timeout 5s
- **Liveness**: Not configured (gap)

### Container Images

- Most services: `codewisdom/{service}:{version}` (1.0.x)
- Some services: `saad1038/{service}` (ts-cancel, ts-contacts, ts-voucher)
- Image pull policy: IfNotPresent

### Exposed Services

- ts-gateway-service: NodePort 30467
- ts-ui-dashboard: NodePort 32677
- All others: ClusterIP (internal only)

## Gateway API Routes

50+ routes following pattern `/api/v1/{service}/**` → `lb://{service-name}`:

| Route Pattern | Target Service |
|--------------|----------------|
| `/api/v1/auth/**` | ts-auth-service |
| `/api/v1/orderservice/**` | ts-order-service |
| `/api/v1/travelservice/**` | ts-travel-service |
| `/api/v1/routeservice/**` | ts-route-service |
| `/api/v1/seatservice/**` | ts-seat-service |
| `/api/v1/stationservice/**` | ts-station-service |
| `/api/v1/trainservice/**` | ts-train-service |
| `/api/v1/userservice/**` | ts-user-service |
| `/api/v1/foodservice/**` | ts-food-service |
| `/api/v1/paymentservice/**` | ts-payment-service |
| ... | (45+ more) |

## Key Operational Notes

- **Single shared database**: All 28 MySQL-backed services share the `ts` database — potential bottleneck
- **DDL auto-migration**: Hibernate auto-updates schema on startup — risky for production
- **RabbitMQ single-node**: No HA for message broker
- **No liveness probes**: Only readiness probes configured
- **No distributed tracing**: Zipkin support available but not enabled
- **Sentinel**: Available for circuit breaking in gateway but not explicitly configured
- **Notification service**: Uses external SMTP (smtp.163.com:465)

## Source Code Layout

| Component | Path |
|-----------|------|
| Service source | `ts-{service-name}/src/main/` |
| Service config | `ts-{service-name}/src/main/resources/application.yml` |
| K8s manifests | `deployment/kubernetes-manifests/quickstart-k8s/` |
| Docker Compose | `docker-compose.yml` |
| MySQL chart | `deployment/kubernetes-manifests/quickstart-k8s/charts/mysql/` |
| Nacos chart | `deployment/kubernetes-manifests/quickstart-k8s/charts/nacos/` |
| RabbitMQ chart | `deployment/kubernetes-manifests/quickstart-k8s/charts/rabbitmq/` |
| Secrets | `deployment/kubernetes-manifests/quickstart-k8s/yamls/secret.yaml` |
| Gateway routes | `ts-gateway-service/src/main/resources/application.yml` |
| Build config | `pom.xml` (parent), `ts-{service}/pom.xml` (per service) |
