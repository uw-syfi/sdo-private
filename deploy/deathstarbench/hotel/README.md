# Hotel Reservation Deployment

This directory contains the deployment scripts for the DeathStarBench Hotel Reservation microservices application.

## Quick Start

```bash
# Navigate to this directory
cd /mnt/sda/shli/sds/deploy/deathstarbench/hotel

# Start the application
./deploy.sh start --build

# Check status
./deploy.sh status

# View logs
./deploy.sh logs

# Stop the application
./deploy.sh stop
```

## Features

The `deploy.sh` script provides a comprehensive deployment management interface:

- ✅ **Automated deployment** - One command to start all services
- ✅ **Prerequisites checking** - Validates Docker installation and configuration
- ✅ **Health monitoring** - Checks service status and connectivity
- ✅ **Log management** - Easy access to service logs
- ✅ **Environment configuration** - Support for TLS, logging, tracing, etc.
- ✅ **Cleanup utilities** - Safe cleanup with confirmation prompts
- ✅ **Color-coded output** - Easy-to-read status messages

## Available Commands

### Deployment Commands

| Command | Description |
|---------|-------------|
| `./deploy.sh start` | Start all services |
| `./deploy.sh start --build` | Build images and start services |
| `./deploy.sh stop` | Stop all services |
| `./deploy.sh restart` | Restart all services |

### Monitoring Commands

| Command | Description |
|---------|-------------|
| `./deploy.sh status` | Check service health and status |
| `./deploy.sh logs` | View logs from all services |
| `./deploy.sh logs frontend` | View logs from specific service |
| `./deploy.sh test` | Run application tests |

### Maintenance Commands

| Command | Description |
|---------|-------------|
| `./deploy.sh build` | Build Docker images |
| `./deploy.sh cleanup` | Remove containers |
| `./deploy.sh cleanup --volumes` | Remove containers and data volumes |
| `./deploy.sh cleanup --all` | Remove containers, volumes, and images |

### Information Commands

| Command | Description |
|---------|-------------|
| `./deploy.sh list` | List all available services |
| `./deploy.sh config` | Show current configuration |
| `./deploy.sh help` | Display help message |

## Configuration

The script supports several environment variables for customizing the deployment:

### TLS Configuration

```bash
# Disable TLS (default)
./deploy.sh start

# Enable TLS
TLS=1 ./deploy.sh start

# Use specific cipher suite
TLS=TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256 ./deploy.sh start
```

### Logging Configuration

```bash
# Set log level (ERROR, WARNING, INFO, TRACE, DEBUG)
LOG_LEVEL=DEBUG ./deploy.sh start
```

### Performance Tuning

```bash
# Set garbage collection target percentage
GC=50 ./deploy.sh start

# Set Jaeger sampling ratio (0.0 to 1.0)
JAEGER_SAMPLE_RATIO=0.1 ./deploy.sh start

# Set memcached timeout in seconds
MEMC_TIMEOUT=5 ./deploy.sh start
```

### Combine Multiple Options

```bash
TLS=1 GC=50 LOG_LEVEL=DEBUG JAEGER_SAMPLE_RATIO=0.1 ./deploy.sh start --build
```

## Architecture

The application consists of:

### Microservices (10)
- **frontend** - User-facing API (port 5000)
- **profile** - Hotel profile information
- **search** - Hotel search functionality
- **geo** - Geographic/location services
- **rate** - Hotel pricing
- **review** - Hotel reviews
- **attractions** - Nearby attractions
- **recommendation** - Hotel recommendations
- **user** - User authentication
- **reservation** - Booking management

### Infrastructure Components
- **Consul** - Service discovery (port 8500)
- **Jaeger** - Distributed tracing (port 16686)
- **MongoDB** - 8 instances for persistent storage
- **Memcached** - 4 instances for caching

## Access Points

Once deployed, you can access:

- **Frontend API**: http://localhost:5000
- **Consul UI**: http://localhost:8500
- **Jaeger UI**: http://localhost:16686

## Example API Calls

### Get Hotel Reviews

```bash
curl "http://localhost:5000/review?hotelId=2&username=Cornell_0&password=0000000000"
```

### Search Hotels

```bash
curl "http://localhost:5000/hotels?inDate=2024-12-20&outDate=2024-12-25&lat=37.7&lon=-122.4"
```

### Get Recommendations

```bash
curl "http://localhost:5000/recommendations?require=dis&lat=37.7&lon=-122.4"
```

### User Login

```bash
curl -X POST "http://localhost:5000/user?username=Cornell_0&password=0000000000"
```

## Troubleshooting

### Port Already in Use

If port 5000 is already in use:

```bash
# Find what's using the port
sudo lsof -i :5000

# Kill the process or modify docker-compose.yml
```

### Services Not Starting

Check the logs for errors:

```bash
# View all logs
./deploy.sh logs

# View specific service
./deploy.sh logs frontend
```

### Clean Start

If experiencing issues, perform a complete cleanup and restart:

```bash
# Stop and remove everything
./deploy.sh cleanup --all

# Start fresh
./deploy.sh start --build
```

### Check Prerequisites

Ensure Docker is properly installed and running:

```bash
docker --version
docker compose version
docker info
```

## Load Testing

For load testing, use the wrk2 tool located in the project:

```bash
# Navigate to wrk2 directory
cd /mnt/sda/shli/sds/apps/deathstarbench/wrk2

# Run mixed workload
./wrk -D exp -t 2 -c 10 -d 30s -L \
  -s ./scripts/hotel-reservation/mixed-workload_type_1.lua \
  http://localhost:5000 -R 100
```

## File Structure

```
deploy/deathstarbench/hotel/
├── deploy.sh          # Main deployment script
└── README.md          # This file
```

## Support

For questions or issues:
- Check the main application README: `/mnt/sda/shli/sds/apps/deathstarbench/hotelReservation/README.md`
- Review Docker Compose logs: `./deploy.sh logs`
- Check service status: `./deploy.sh status`

## Notes

- The script automatically navigates to the correct application directory
- All Docker Compose commands are executed from the application directory
- Container data is persisted in Docker volumes by default
- Use `cleanup --volumes` to remove data (irreversible)

