# Astronomy Shop (OpenTelemetry Demo) - SRE Architecture Reference

## Service Topology

E-commerce application with 14 business microservices, Kafka event streaming, Valkey (Redis) cache, and a full observability stack. All services communicate via HTTP on port 8080 (internal). Deployed via Helm.

### Business Services

| Service | Port | Language | Purpose | Dependencies |
|---------|------|----------|---------|--------------|
| **frontend** | 8080 | Next.js | Web UI for browsing/purchasing astronomy products | ad, cart, checkout, currency, product-catalog, recommendation, shipping |
| **frontend-proxy** | 8080 | Envoy | Reverse proxy for frontend + observability UIs | frontend, grafana, jaeger, load-generator, flagd |
| **product-catalog** | 8080 | Go | Product inventory (12 products from ConfigMap) | None (reads products.json) |
| **cart** | 8080 | .NET | Shopping cart management | valkey-cart (6379) |
| **checkout** | 8080 | Go | Order orchestration | cart, currency, email, payment, product-catalog, shipping, kafka |
| **currency** | 8080 | C++ | Currency conversion | None |
| **payment** | 8080 | Node.js | Payment processing | None |
| **shipping** | 8080 | Rust | Shipping cost calculation | quote |
| **quote** | 8080 | PHP | Shipping quote calculation | None |
| **email** | 8080 | Ruby | Order confirmation emails | None |
| **ad** | 8080 | Java | Advertisement/product recommendations | None |
| **recommendation** | 8080 | Python | Personalized product recommendations | product-catalog |
| **image-provider** | 8081 | - | Image asset serving | None |
| **load-generator** | 8089 | Python/Locust | Synthetic user traffic generation | frontend-proxy |

### Async Processing Services

| Service | Purpose | Dependencies |
|---------|---------|--------------|
| **accounting** | Processes order accounting events | Kafka (consumer) |
| **fraud-detection** | Detects fraudulent transactions | Kafka (consumer) |

### Infrastructure

| Service | Port | Purpose |
|---------|------|---------|
| **kafka** | 9092 | Message broker for async event processing |
| **valkey-cart** | 6379 | Valkey 7.2 (Redis fork) for cart session storage |
| **flagd** | 8013 | OpenFeature flag management (UI on port 4000) |

## Inter-Service Call Graph

```
frontend-proxy (Envoy:8080) ─ entry point
└─→ frontend (Next.js:8080)
    ├─→ ad (8080)
    ├─→ cart (8080) → valkey-cart (6379)
    ├─→ checkout (8080)
    │   ├─→ cart (8080)
    │   ├─→ currency (8080)
    │   ├─→ email (8080)
    │   ├─→ payment (8080)
    │   ├─→ product-catalog (8080)
    │   ├─→ shipping (8080) → quote (8080)
    │   └─→ kafka (9092) ─ publishes order events
    │       ├─→ accounting (consumer)
    │       └─→ fraud-detection (consumer)
    ├─→ currency (8080)
    ├─→ product-catalog (8080)
    ├─→ recommendation (8080) → product-catalog (8080)
    └─→ shipping (8080) → quote (8080)

All services → flagd (8013) for feature flags
All services → otel-collector (4317/4318) for telemetry
```

## Database & Storage

| Store | Service | Type | Details |
|-------|---------|------|---------|
| valkey-cart | cart | In-memory cache | Valkey 7.2 Alpine, 20Mi memory limit |
| kafka | checkout → accounting, fraud-detection | Event stream | Async order/payment events |
| products.json | product-catalog | ConfigMap | 12 astronomy products ($21.95-$349.95) |
| flagd config | flagd | ConfigMap | Feature flag definitions (demo.flagd.json) |

No persistent database — product catalog is static, cart is ephemeral, events are streamed.

## Configuration

### Key Environment Variables (all services)
| Variable | Value | Purpose |
|----------|-------|---------|
| OTEL_SERVICE_NAME | (per service) | OpenTelemetry service identification |
| OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE | cumulative | Metrics aggregation mode |
| OTEL_RESOURCE_ATTRIBUTES | service.name, namespace, version | Resource attributes |
| FLAGD_HOST | flagd | Feature flag service |
| FLAGD_PORT | 8013 | Feature flag port |

### Per-Service Key Config
| Service | Key Env Vars |
|---------|-------------|
| cart | CART_SERVICE_PORT=8080, VALKEY_ADDR=valkey-cart:6379 |
| checkout | CHECKOUT_SERVICE_PORT=8080, CART_SERVICE_ADDR=cart:8080, CURRENCY_SERVICE_ADDR=currency:8080, EMAIL_SERVICE_ADDR=http://email:8080, PAYMENT_SERVICE_ADDR=payment:8080, PRODUCT_CATALOG_SERVICE_ADDR=product-catalog:8080, SHIPPING_SERVICE_ADDR=shipping:8080, KAFKA_SERVICE_ADDR=kafka:9092 |
| frontend | FRONTEND_ADDR=:8080, AD_SERVICE_ADDR=ad:8080, CART_SERVICE_ADDR=cart:8080, CHECKOUT_SERVICE_ADDR=checkout:8080, CURRENCY_SERVICE_ADDR=currency:8080, PRODUCT_CATALOG_SERVICE_ADDR=product-catalog:8080, RECOMMENDATION_SERVICE_ADDR=recommendation:8080, SHIPPING_SERVICE_ADDR=shipping:8080 |
| recommendation | RECOMMENDATION_SERVICE_PORT=8080, PRODUCT_CATALOG_SERVICE_ADDR=product-catalog:8080 |
| shipping | SHIPPING_SERVICE_PORT=8080, QUOTE_SERVICE_ADDR=http://quote:8080 |
| load-generator | LOCUST_USERS=10, LOCUST_SPAWN_RATE=1, LOCUST_HOST=http://frontend-proxy:8080 |

## Kubernetes Deployment (Helm)

### Chart Info
- **Chart**: opentelemetry-demo v0.37.2 (appVersion 2.0.2)
- **Requires**: Kubernetes 1.24+, Helm 3.14+
- **Location**: `charts/opentelemetry-demo/`

### Dependencies (sub-charts)
- opentelemetry-collector 0.117.1
- jaeger 3.4.0
- prometheus 27.4.0
- grafana 8.10.1
- opensearch 2.31.0

### Resource Limits
| Service | Memory Limit | Notes |
|---------|-------------|-------|
| load-generator | 1500Mi | Highest — runs Locust simulation |
| opensearch | 1100Mi | JVM heap 300m |
| recommendation | 500Mi | ML/cache for feature flag |
| kafka | 600Mi | Message broker |
| jaeger | 400Mi | Trace storage |
| prometheus | 300Mi | Metrics TSDB |
| frontend | 250Mi | Next.js SSR |
| grafana | 150Mi | Dashboards |
| Most others | 20-120Mi | Lightweight services |

### Init Containers (dependency ordering)
- **cart** waits for valkey-cart:6379
- **checkout** waits for kafka:9092
- **accounting** waits for kafka:9092
- **fraud-detection** waits for kafka:9092

### Images
- Business services: `ghcr.io/open-telemetry/demo:<service-name>` (tag = appVersion)
- valkey-cart: `valkey/valkey:7.2-alpine`
- flagd: `ghcr.io/open-feature/flagd:v0.11.1`
- Init containers: `busybox:latest`

### Security
- Most services run as non-root (runAsNonRoot: true)
- frontend: UID 1001, payment: UID 1000, quote: UID 33

## Observability Stack

### OpenTelemetry Collector
- **Receivers**: OTLP (gRPC 4317, HTTP 4318), httpcheck on frontend-proxy, Redis metrics from valkey-cart
- **Exporters**: Jaeger (traces), Prometheus (metrics via OTLP), OpenSearch (logs), debug (console)
- **Processors**: memory_limiter, resource enrichment, transform (normalize Next.js spans), batch
- **Connectors**: spanmetrics (generates RED metrics from traces)

### Pipelines
```
Traces:  [otlp] → [memory_limiter, resource, transform, batch] → [otlp/jaeger, debug, spanmetrics]
Metrics: [otlp, httpcheck, redis, spanmetrics] → [memory_limiter, resource, batch] → [prometheus, debug]
Logs:    [otlp] → [memory_limiter, resource, batch] → [opensearch, debug]
```

### Access Points (via frontend-proxy)
| UI | Path | Port |
|----|------|------|
| Shop | `/` | 8080 |
| Grafana | `/grafana` | 8080 |
| Jaeger | `/jaeger/ui` | 8080 |
| Locust | `/locust_web` | 8080 |
| FlagD | `/flagd-ui` | 8080 |

### Jaeger
- Mode: all-in-one, in-memory storage (max 5000 traces)
- UI base path: /jaeger/ui
- Metrics backend: Prometheus

### Prometheus
- Scrape interval: 5s, timeout: 3s
- OTLP receiver enabled, exemplar storage enabled
- TSDB out-of-order window: 30m (for Kafka delayed events)

### Grafana
- Admin: admin/admin, anonymous auth enabled
- Datasources: Prometheus (default), Jaeger (linked via exemplars), OpenSearch (logs)
- Dashboards pre-configured via ConfigMap

## Load Testing

- **Tool**: Locust (Python)
- **Default**: 10 users, 1 user/sec spawn rate, headless autostart
- **Target**: http://frontend-proxy:8080
- **Browser traffic simulation**: Enabled
- **Web UI**: Port 8089 (proxied at /locust_web)

## Product Catalog

12 astronomy products stored in ConfigMap (products.json):
- Telescopes, binoculars, accessories, travel gear
- Price range: $21.95 - $349.95 USD
- Categories: telescopes, binoculars, accessories, assembly, travel

## Source Code Layout

| Component | Path |
|-----------|------|
| Helm chart | `charts/opentelemetry-demo/` |
| Values | `charts/opentelemetry-demo/values.yaml` |
| Templates | `charts/opentelemetry-demo/templates/` |
| Products | `charts/opentelemetry-demo/products/products.json` |
| Examples | `charts/opentelemetry-demo/examples/` |

Note: This is a Helm-only deployment. Application source code is built into container images at `ghcr.io/open-telemetry/demo`. No application source code is included in this repository.
