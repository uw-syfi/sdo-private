#!/bin/bash

################################################################################
# Hotel Reservation Application Deployment Script
# 
# This script manages the deployment of the DeathStarBench Hotel Reservation
# microservices application using Docker Compose.
#
# Usage: ./deploy.sh [command] [options]
################################################################################

set -e  # Exit on error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="/mnt/sda/shli/sds"
APP_DIR="${PROJECT_ROOT}/apps/deathstarbench/hotelReservation"
COMPOSE_FILE="${APP_DIR}/docker-compose.yml"

# Default environment variables
export TLS="${TLS:-0}"
export GC="${GC:-100}"
export JAEGER_SAMPLE_RATIO="${JAEGER_SAMPLE_RATIO:-0.01}"
export MEMC_TIMEOUT="${MEMC_TIMEOUT:-2}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${MAGENTA}ℹ${NC} $1"
}

################################################################################
# Check Prerequisites
################################################################################

check_prerequisites() {
    print_header "Checking Prerequisites"
    
    local all_good=true
    
    # Check if Docker is installed
    if command -v docker &> /dev/null; then
        DOCKER_VERSION=$(docker --version | cut -d' ' -f3 | cut -d',' -f1)
        print_success "Docker installed (version: ${DOCKER_VERSION})"
    else
        print_error "Docker is not installed"
        all_good=false
    fi
    
    # Check if Docker Compose is installed
    if docker compose version &> /dev/null; then
        COMPOSE_VERSION=$(docker compose version | grep -oP 'version \K[^ ]+' | head -1)
        print_success "Docker Compose installed (version: ${COMPOSE_VERSION})"
    else
        print_error "Docker Compose is not installed"
        all_good=false
    fi
    
    # Check if Docker daemon is running
    if docker info &> /dev/null; then
        print_success "Docker daemon is running"
    else
        print_error "Docker daemon is not running"
        all_good=false
    fi
    
    # Check if application directory exists
    if [ -d "${APP_DIR}" ]; then
        print_success "Application directory found: ${APP_DIR}"
    else
        print_error "Application directory not found: ${APP_DIR}"
        all_good=false
    fi
    
    # Check if docker-compose.yml exists
    if [ -f "${COMPOSE_FILE}" ]; then
        print_success "Docker Compose file found"
    else
        print_error "Docker Compose file not found: ${COMPOSE_FILE}"
        all_good=false
    fi
    
    echo ""
    
    if [ "$all_good" = false ]; then
        print_error "Prerequisites check failed. Please fix the issues above."
        exit 1
    fi
    
    print_success "All prerequisites met!"
    echo ""
}

################################################################################
# Display Current Configuration
################################################################################

show_config() {
    print_header "Current Configuration"
    echo -e "${CYAN}Environment Variables:${NC}"
    echo "  TLS:                  ${TLS}"
    echo "  GC:                   ${GC}"
    echo "  JAEGER_SAMPLE_RATIO:  ${JAEGER_SAMPLE_RATIO}"
    echo "  MEMC_TIMEOUT:         ${MEMC_TIMEOUT}"
    echo "  LOG_LEVEL:            ${LOG_LEVEL}"
    echo ""
    echo -e "${CYAN}Application Directory:${NC} ${APP_DIR}"
    echo -e "${CYAN}Compose File:${NC}          ${COMPOSE_FILE}"
    echo ""
}

################################################################################
# Build Images
################################################################################

build_images() {
    print_header "Building Docker Images"
    cd "${APP_DIR}"
    
    print_info "Building images (this may take several minutes)..."
    if docker compose build; then
        print_success "Images built successfully"
    else
        print_error "Failed to build images"
        exit 1
    fi
    echo ""
}

################################################################################
# Start Services
################################################################################

start_services() {
    print_header "Starting Hotel Reservation Services"
    cd "${APP_DIR}"
    
    local BUILD_FLAG=""
    if [ "${1}" = "--build" ]; then
        BUILD_FLAG="--build"
        print_info "Build flag detected. Will rebuild images..."
    fi
    
    print_info "Starting services in detached mode..."
    if docker compose up -d ${BUILD_FLAG}; then
        print_success "Services started successfully"
    else
        print_error "Failed to start services"
        exit 1
    fi
    
    echo ""
    print_info "Waiting for services to initialize (10 seconds)..."
    sleep 10
    
    check_health
}

################################################################################
# Stop Services
################################################################################

stop_services() {
    print_header "Stopping Hotel Reservation Services"
    cd "${APP_DIR}"
    
    print_info "Stopping all services..."
    if docker compose stop; then
        print_success "Services stopped successfully"
    else
        print_error "Failed to stop services"
        exit 1
    fi
    echo ""
}

################################################################################
# Restart Services
################################################################################

restart_services() {
    print_header "Restarting Hotel Reservation Services"
    stop_services
    start_services
}

################################################################################
# Check Service Health
################################################################################

check_health() {
    print_header "Service Health Check"
    cd "${APP_DIR}"
    
    # Get container status
    echo -e "${CYAN}Container Status:${NC}"
    docker compose ps
    echo ""
    
    # Count running containers
    TOTAL_CONTAINERS=$(docker compose ps -q | wc -l)
    RUNNING_CONTAINERS=$(docker compose ps -q --status running | wc -l)
    
    echo -e "${CYAN}Summary:${NC}"
    echo "  Total containers:   ${TOTAL_CONTAINERS}"
    echo "  Running containers: ${RUNNING_CONTAINERS}"
    echo ""
    
    if [ "${RUNNING_CONTAINERS}" -eq "${TOTAL_CONTAINERS}" ] && [ "${TOTAL_CONTAINERS}" -gt 0 ]; then
        print_success "All containers are running!"
    elif [ "${RUNNING_CONTAINERS}" -gt 0 ]; then
        print_warning "Some containers are not running"
    else
        print_error "No containers are running"
    fi
    
    # Test frontend endpoint
    echo ""
    print_info "Testing frontend endpoint (http://localhost:5000)..."
    sleep 2
    
    if curl -s -f http://localhost:5000 > /dev/null 2>&1; then
        print_success "Frontend is responding"
    else
        print_warning "Frontend may not be ready yet (this is normal if just started)"
    fi
    
    echo ""
    print_info "Access points:"
    echo "  Frontend API:  http://localhost:5000"
    echo "  Consul UI:     http://localhost:8500"
    echo "  Jaeger UI:     http://localhost:16686"
    echo ""
}

################################################################################
# View Logs
################################################################################

view_logs() {
    cd "${APP_DIR}"
    
    if [ -z "$1" ]; then
        print_header "Viewing All Service Logs"
        print_info "Press Ctrl+C to exit"
        echo ""
        docker compose logs -f
    else
        print_header "Viewing Logs for: $1"
        print_info "Press Ctrl+C to exit"
        echo ""
        docker compose logs -f "$1"
    fi
}

################################################################################
# Cleanup
################################################################################

cleanup() {
    print_header "Cleaning Up"
    cd "${APP_DIR}"
    
    local REMOVE_VOLUMES=false
    local REMOVE_IMAGES=false
    
    while [ "$#" -gt 0 ]; do
        case $1 in
            --volumes|-v)
                REMOVE_VOLUMES=true
                shift
                ;;
            --images|-i)
                REMOVE_IMAGES=true
                shift
                ;;
            --all|-a)
                REMOVE_VOLUMES=true
                REMOVE_IMAGES=true
                shift
                ;;
            *)
                shift
                ;;
        esac
    done
    
    print_warning "This will stop and remove all containers"
    if [ "$REMOVE_VOLUMES" = true ]; then
        print_warning "All data volumes will be deleted (IRREVERSIBLE)"
    fi
    if [ "$REMOVE_IMAGES" = true ]; then
        print_warning "All images will be removed"
    fi
    
    echo ""
    read -p "Are you sure? (yes/no): " -r
    if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        print_info "Cleanup cancelled"
        exit 0
    fi
    
    print_info "Removing containers..."
    local FLAGS=""
    [ "$REMOVE_VOLUMES" = true ] && FLAGS="${FLAGS} -v"
    [ "$REMOVE_IMAGES" = true ] && FLAGS="${FLAGS} --rmi all"
    
    if docker compose down ${FLAGS}; then
        print_success "Cleanup completed"
    else
        print_error "Cleanup failed"
        exit 1
    fi
    echo ""
}

################################################################################
# Run Tests
################################################################################

run_tests() {
    print_header "Running Application Tests"
    cd "${APP_DIR}"
    
    print_info "Testing review endpoint..."
    if [ -f "test.py" ]; then
        python3 test.py
    else
        print_warning "test.py not found, running manual test..."
        curl "http://localhost:5000/review?hotelId=2&username=Cornell_0&password=0000000000"
    fi
    echo ""
}

################################################################################
# Show Service List
################################################################################

list_services() {
    print_header "Available Services"
    cd "${APP_DIR}"
    
    echo -e "${CYAN}Microservices:${NC}"
    echo "  1.  frontend       - User-facing API (port 5000)"
    echo "  2.  profile        - Hotel profile information"
    echo "  3.  search         - Hotel search functionality"
    echo "  4.  geo            - Geographic/location services"
    echo "  5.  rate           - Hotel pricing"
    echo "  6.  review         - Hotel reviews"
    echo "  7.  attractions    - Nearby attractions"
    echo "  8.  recommendation - Hotel recommendations"
    echo "  9.  user           - User authentication"
    echo "  10. reservation    - Booking management"
    echo ""
    echo -e "${CYAN}Infrastructure:${NC}"
    echo "  • consul           - Service discovery (port 8500)"
    echo "  • jaeger           - Distributed tracing (port 16686)"
    echo "  • 8x MongoDB       - Persistent storage"
    echo "  • 4x Memcached     - Caching layer"
    echo ""
}

################################################################################
# Display Help
################################################################################

show_help() {
    cat << EOF
${CYAN}Hotel Reservation Deployment Script${NC}

${YELLOW}USAGE:${NC}
    ./deploy.sh [command] [options]

${YELLOW}COMMANDS:${NC}
    start               Start all services
    start --build       Build images and start services
    stop                Stop all services
    restart             Restart all services
    status              Check service health and status
    logs [service]      View logs (all or specific service)
    build               Build Docker images
    test                Run application tests
    list                List all available services
    cleanup             Remove containers (prompts for confirmation)
    cleanup --volumes   Remove containers and volumes
    cleanup --images    Remove containers and images
    cleanup --all       Remove containers, volumes, and images
    config              Show current configuration
    help                Show this help message

${YELLOW}ENVIRONMENT VARIABLES:${NC}
    TLS                 Enable TLS (0=disabled, 1=enabled, or cipher suite name)
                        Default: ${TLS}
    
    GC                  Garbage collection target percentage
                        Default: ${GC}
    
    JAEGER_SAMPLE_RATIO Jaeger sampling ratio (0.0-1.0)
                        Default: ${JAEGER_SAMPLE_RATIO}
    
    MEMC_TIMEOUT        Memcached timeout in seconds
                        Default: ${MEMC_TIMEOUT}
    
    LOG_LEVEL           Logging level (ERROR|WARNING|INFO|TRACE|DEBUG)
                        Default: ${LOG_LEVEL}

${YELLOW}EXAMPLES:${NC}
    # Start with default settings
    ./deploy.sh start

    # Start with custom configuration
    TLS=1 LOG_LEVEL=DEBUG ./deploy.sh start --build

    # View logs for frontend service
    ./deploy.sh logs frontend

    # Check service status
    ./deploy.sh status

    # Complete cleanup
    ./deploy.sh cleanup --all

${YELLOW}ACCESS POINTS:${NC}
    Frontend API:  http://localhost:5000
    Consul UI:     http://localhost:8500
    Jaeger UI:     http://localhost:16686

${YELLOW}EXAMPLE API CALLS:${NC}
    # Get hotel reviews
    curl "http://localhost:5000/review?hotelId=2&username=Cornell_0&password=0000000000"
    
    # Search hotels
    curl "http://localhost:5000/hotels?inDate=2024-12-20&outDate=2024-12-25&lat=37.7&lon=-122.4"

EOF
}

################################################################################
# Main Script Logic
################################################################################

main() {
    # Print banner
    echo ""
    print_header "Hotel Reservation Deployment Manager"
    echo ""
    
    # Handle commands
    case "${1:-help}" in
        start)
            check_prerequisites
            show_config
            start_services "${2}"
            ;;
        stop)
            stop_services
            ;;
        restart)
            check_prerequisites
            restart_services
            ;;
        status|health)
            check_health
            ;;
        logs)
            view_logs "${2}"
            ;;
        build)
            check_prerequisites
            build_images
            ;;
        test)
            run_tests
            ;;
        list)
            list_services
            ;;
        cleanup|clean|down)
            shift
            cleanup "$@"
            ;;
        config)
            show_config
            ;;
        help|--help|-h|"")
            show_help
            ;;
        *)
            print_error "Unknown command: $1"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# Run main function
main "$@"

