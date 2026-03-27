# Social Network - SRE Architecture Reference

## Service Topology

12 Thrift-based C++ microservices + Nginx gateway + media frontend. Infrastructure: MongoDB (storage), Redis (timeline caching), Memcached (object caching), RabbitMQ (async fan-out), Jaeger (tracing).

### Core Services (all on port 9090, Thrift binary protocol)

| Service | Purpose | Storage Dependencies |
|---------|---------|---------------------|
| **ComposePostService** | Orchestrates post creation; calls 7 other services | None (orchestrator) |
| **PostStorageService** | Persists and retrieves posts | post-storage-mongodb, post-storage-memcached |
| **UserTimelineService** | User's own post feed | user-timeline-mongodb, user-timeline-redis |
| **HomeTimelineService** | Feed of posts from followed users | home-timeline-redis |
| **UserService** | Registration, login, profile management | user-mongodb, user-memcached |
| **SocialGraphService** | Follower/followee relationships | social-graph-mongodb, social-graph-redis |
| **TextService** | Parses post text, extracts @mentions and URLs | None (stateless) |
| **UserMentionService** | Resolves @usernames to user IDs | user-mongodb, user-memcached |
| **UrlShortenService** | Shortens and expands URLs | url-shorten-mongodb, url-shorten-memcached |
| **MediaService** | Media attachment metadata | None (stateless) |
| **UniqueIdService** | 64-bit distributed ID generation (machine ID + timestamp + counter) | None (stateless) |
| **WriteHomeTimelineService** | Async home timeline fan-out worker | RabbitMQ (consumer), home-timeline-redis |

### Frontend/Gateway

| Component | Port | Purpose |
|-----------|------|---------|
| **nginx-thrift** | 8080 | HTTP API gateway → Thrift RPC translation (OpenResty + Lua) |
| **media-frontend** | 8081 | Media upload/download (OpenResty + Lua, 100MB max body) |

## Inter-Service Call Graph

```
nginx-thrift (HTTP:8080)
│
├─→ ComposePostService (9090)
│   ├─→ UniqueIdService (9090) ─ generates post_id
│   ├─→ UserService (9090) ─ verify creator
│   │   ├─→ user-memcached (11211)
│   │   └─→ user-mongodb (27017)
│   ├─→ TextService (9090)
│   │   ├─→ UserMentionService (9090) → user-memcached / user-mongodb
│   │   └─→ UrlShortenService (9090) → url-shorten-memcached / url-shorten-mongodb
│   ├─→ MediaService (9090)
│   ├─→ PostStorageService (9090) → post-storage-memcached / post-storage-mongodb
│   ├─→ UserTimelineService (9090) → user-timeline-redis / user-timeline-mongodb
│   └─→ [async via RabbitMQ] → WriteHomeTimelineService
│       ├─→ SocialGraphService (9090) ─ get followers
│       └─→ home-timeline-redis ─ push to each follower's feed
│
├─→ HomeTimelineService (9090)
│   ├─→ home-timeline-redis (6379) ─ ZRANGE sorted set
│   └─→ PostStorageService (9090) ─ fetch post objects
│
├─→ UserTimelineService (9090)
│   ├─→ user-timeline-redis (6379)
│   └─→ PostStorageService (9090)
│
├─→ UserService (9090)
│
└─→ SocialGraphService (9090)
    ├─→ social-graph-mongodb (27017)
    ├─→ social-graph-redis (6379)
    └─→ UserService (9090)
```

## HTTP API Endpoints (nginx-thrift, port 8080)

| Endpoint | Method | Backend Call |
|----------|--------|-------------|
| `/api/post/compose` | POST | ComposePostService.ComposePost |
| `/api/home-timeline/read` | GET | HomeTimelineService.ReadHomeTimeline |
| `/api/user-timeline/read` | GET | UserTimelineService.ReadUserTimeline |
| `/api/user/register` | POST | UserService.RegisterUser |
| `/api/user/login` | POST | UserService.Login |
| `/api/user/follow` | POST | SocialGraphService.Follow |
| `/api/user/unfollow` | POST | SocialGraphService.Unfollow |
| `/api/user/get_follower` | GET | SocialGraphService.GetFollowers |
| `/api/user/get_followee` | GET | SocialGraphService.GetFollowees |

Load test variants use `/wrk2-api/` prefix.

## Database & Storage

### MongoDB Instances (all port 27017)

| Instance | Database | Purpose |
|----------|----------|---------|
| user-mongodb | user | User profiles and credentials |
| social-graph-mongodb | social-graph | Follower/followee relationships |
| post-storage-mongodb | post-storage | Post content and metadata |
| user-timeline-mongodb | user-timeline | User timeline post ID index |
| url-shorten-mongodb | url-shorten | URL shortening mappings |

Connection pool: 512 connections, 10s timeout, 10s keepalive.

### Redis Instances (all port 6379)

| Instance | Data Structure | Purpose |
|----------|---------------|---------|
| home-timeline-redis | Sorted set (by timestamp) | Home feed post IDs per user |
| user-timeline-redis | Sorted set (by timestamp) | User's own post IDs |
| social-graph-redis | Sets | Follower/followee lists |
| compose-post-redis | (configured but unused) | Reserved |

Supports standalone, cluster, or replica modes.

### Memcached Instances (all port 11211, binary protocol)

| Instance | Purpose |
|----------|---------|
| user-memcached | User profile/credential cache |
| post-storage-memcached | Post object cache |
| url-shorten-memcached | URL mapping cache |
| media-memcached | Reserved for media metadata |

### Message Queue

| Instance | Port | Purpose |
|----------|------|---------|
| write-home-timeline-rabbitmq | 5672 | Async home timeline fan-out |

## Configuration

### Service Config (`config/service-config.json`)

All services registered at port 9090 with hostnames matching service names. Key parameters:
- `secret`: "secret" (JWT/auth secret)
- `timeout_ms`: 10000 per service
- `keepalive_ms`: 10000
- `connections`: 512 per pool
- `netif`: "eth0" (for UniqueIdService and UserService machine ID generation)
- SSL/TLS: disabled by default, configurable via `caPath`, `serverCertPath`, `serverKeyPath`

### Nginx Config (`nginx-web-server/conf/nginx.conf`)

- Workers: auto (scales to CPU count)
- Worker connections: 1024
- Keepalive timeout: 120s
- Keepalive requests: 100000
- Lua shared dict for config and JWT support
- Jaeger tracing via opentracing_bridge_tracer
- Resolver: kube-dns.kube-system.svc.cluster.local (in K8s)

## Thrift RPC Interfaces

**ComposePostService**: `ComposePost(req_id, username, user_id, text, media_ids[], media_types[], post_type, carrier)`

**PostStorageService**: `StorePost(post)`, `ReadPost(post_id) → Post`, `ReadPosts(post_ids[]) → Post[]`

**HomeTimelineService**: `ReadHomeTimeline(user_id, start, stop) → Post[]`, `WriteHomeTimeline(post_id, user_id, timestamp, user_mentions_id[])`

**UserTimelineService**: `WriteUserTimeline(post_id, user_id, timestamp)`, `ReadUserTimeline(user_id, start, stop) → Post[]`

**UserService**: `RegisterUser(...)`, `Login(username, password) → token`, `ComposeCreatorWithUserId(user_id, username) → Creator`, `GetUserId(username) → i64`

**SocialGraphService**: `GetFollowers(user_id) → i64[]`, `GetFollowees(user_id) → i64[]`, `Follow(user_id, followee_id)`, `Unfollow(...)`, `InsertUser(user_id)`

**TextService**: `ComposeText(text) → {text, user_mentions[], urls[]}`

**UserMentionService**: `ComposeUserMentions(usernames[]) → UserMention[]`

**UrlShortenService**: `ComposeUrls(urls[]) → Url[]`, `GetExtendedUrls(shortened_urls[]) → urls[]`

**MediaService**: `ComposeMedia(media_types[], media_ids[]) → Media[]`

**UniqueIdService**: `ComposeUniqueId(req_id, post_type) → i64 post_id`

Protocol: TBinaryProtocol over TFramedTransport. Connection pooling with mutex guards.

## Kubernetes Deployment (Helm)

**Chart**: `helm-chart/socialnetwork/`

Key values:
- Replicas: 1 per service (default)
- Image: `yinfangchen/social-otel:test`
- Service type: ClusterIP (all services)
- Data stores via Bitnami charts (MongoDB, Redis, Memcached, RabbitMQ)
- Jaeger sampling: probabilistic 0.01 (1%)
- Redis cluster: disabled by default (standalone mode)

## Docker Build

- Base: `yg397/thrift-microservice-deps:xenial`
- Build deps: CMake 3.x, GCC 7, OpenTelemetry C++ 1.13.0, gRPC 1.60.0, Protobuf 25.1
- Image: `yinfangchen/social-otel:test`
- Nginx images: `yg397/openresty-thrift:xenial`, `yg397/media-frontend:xenial`
- Data stores: `mongo:4.4.6`, `redis:latest`, `memcached:latest`

## Workload Pattern

Load testing via wrk2 (`wrk2/scripts/social-network/mixed-workload.lua`):

| Request Type | Weight | Endpoint |
|-------------|--------|----------|
| Read home timeline | 60% | GET /wrk2-api/home-timeline/read |
| Read user timeline | 30% | GET /wrk2-api/user-timeline/read |
| Compose post | 10% | POST /wrk2-api/post/compose |

User pool: 962 users (username_0 to username_961). Posts include random text (256 chars), 0-5 @mentions, 0-5 URLs, 0-4 media attachments.

## Key Request Flows

### Compose Post (7-8 RPC calls)
1. UniqueIdService → generate post_id
2. UserService → verify creator
3. TextService → parse text (calls UserMentionService + UrlShortenService)
4. MediaService → validate media
5. PostStorageService → store post (MongoDB + Memcached)
6. UserTimelineService → add to user's timeline (Redis + MongoDB)
7. [Async] WriteHomeTimelineService → fan-out to all followers' home timelines via RabbitMQ

### Read Home/User Timeline (2 RPC calls)
1. Redis ZRANGE → get post IDs (sorted by timestamp)
2. PostStorageService.ReadPosts → fetch post objects (Memcached → MongoDB fallback)

## Source Code Layout

| Component | Path |
|-----------|------|
| Service implementations | `src/<ServiceName>/<ServiceName>Service.cpp` |
| Thrift definitions | `gen-cpp/`, `gen-lua/`, `gen-py/` |
| Configuration | `config/service-config.json` |
| Nginx gateway config | `nginx-web-server/conf/nginx.conf` |
| Nginx Lua scripts | `nginx-web-server/lua-scripts/` |
| Media frontend config | `media-frontend/conf/nginx.conf` |
| Helm chart | `helm-chart/socialnetwork/` |
| Load test scripts | `wrk2/scripts/social-network/` |
| Docker Compose | `docker-compose.yml` (+ tls, swarm, sharding variants) |
| Dockerfile | `Dockerfile` |
