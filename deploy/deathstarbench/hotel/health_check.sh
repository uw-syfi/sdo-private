#!/bin/bash

################################################################################
# Hotel Reservation Health Check Script
# 
# This script performs comprehensive health checks on the Hotel Reservation
# application to ensure all services are running correctly.
#
# Usage: ./health_check.sh [options]
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

# Service endpoints
FRONTEND_URL="http://localhost:5000"
CONSUL_URL="http://localhost:8500"
JAEGER_URL="http://localhost:16686"

# Test parameters
TIMEOUT=5
VERBOSE=false

# Health status tracking
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
}

print_subheader() {
    echo -e "\n${MAGENTA}▶ $1${NC}"
}

print_success() {
    echo -e "  ${GREEN}✓${NC} $1"
    ((PASSED_CHECKS++))
}

print_error() {
    echo -e "  ${RED}✗${NC} $1"
    ((FAILED_CHECKS++))
}

print_warning() {
    echo -e "  ${YELLOW}⚠${NC} $1"
    ((WARNING_CHECKS++))
}

print_info() {
    if [ "$VERBOSE" = true ]; then
        echo -e "  ${CYAN}ℹ${NC} $1"
    fi
}

increment_check() {
    ((TOTAL_CHECKS++))
}

################################################################################
# Container Health Checks
################################################################################

check_docker() {
    print_subheader "Docker Environment"
    
    increment_check
    if command -v docker &> /dev/null; then
        DOCKER_VERSION=$(docker --version | cut -d' ' -f3 | cut -d',' -f1)
        print_success "Docker is installed (version: ${DOCKER_VERSION})"
    else
        print_error "Docker is not installed"
        return 1
    fi
    
    increment_check
    if docker info &> /dev/null; then
        print_success "Docker daemon is running"
    else
        print_error "Docker daemon is not running"
        return 1
    fi
}

check_containers() {
    print_subheader "Container Status"
    
    cd "${APP_DIR}"
    
    increment_check
    if [ ! -f "${COMPOSE_FILE}" ]; then
        print_error "Docker Compose file not found: ${COMPOSE_FILE}"
        return 1
    fi
    print_success "Docker Compose file found"
    
    # Get container counts
    TOTAL_CONTAINERS=$(docker compose ps -a -q 2>/dev/null | wc -l)
    RUNNING_CONTAINERS=$(docker compose ps -q --status running 2>/dev/null | wc -l)
    
    increment_check
    if [ "${TOTAL_CONTAINERS}" -eq 0 ]; then
        print_error "No containers found. Application may not be deployed."
        return 1
    else
        print_success "Found ${TOTAL_CONTAINERS} containers"
    fi
    
    increment_check
    if [ "${RUNNING_CONTAINERS}" -eq "${TOTAL_CONTAINERS}" ]; then
        print_success "All ${RUNNING_CONTAINERS} containers are running"
    elif [ "${RUNNING_CONTAINERS}" -gt 0 ]; then
        print_warning "${RUNNING_CONTAINERS}/${TOTAL_CONTAINERS} containers running"
    else
        print_error "No containers are running"
        return 1
    fi
    
    # Check individual service containers
    print_info "Checking individual services..."
    
    SERVICES=(
        "frontend"
        "profile"
        "search"
        "geo"
        "rate"
        "review"
        "attractions"
        "recommendation"
        "user"
        "reservation"
        "consul"
        "jaeger"
    )
    
    for service in "${SERVICES[@]}"; do
        increment_check
        if docker compose ps "${service}" 2>/dev/null | grep -q "Up\|running"; then
            print_info "Service '${service}' is running"
            ((PASSED_CHECKS++))
        else
            print_warning "Service '${service}' is not running"
        fi
    done
}

################################################################################
# Network Connectivity Checks
################################################################################

check_ports() {
    print_subheader "Port Availability"
    
    PORTS=(
        "5000:Frontend"
        "8500:Consul"
        "16686:Jaeger"
    )
    
    for port_info in "${PORTS[@]}"; do
        increment_check
        PORT=$(echo "${port_info}" | cut -d':' -f1)
        NAME=$(echo "${port_info}" | cut -d':' -f2)
        
        if nc -z localhost "${PORT}" 2>/dev/null || timeout 1 bash -c "echo > /dev/tcp/localhost/${PORT}" 2>/dev/null; then
            print_success "${NAME} port ${PORT} is accessible"
        else
            print_error "${NAME} port ${PORT} is not accessible"
        fi
    done
}

################################################################################
# Service Endpoint Checks
################################################################################

check_frontend() {
    print_subheader "Frontend Service"
    
    increment_check
    if curl -s -f --max-time "${TIMEOUT}" "${FRONTEND_URL}" > /dev/null 2>&1; then
        print_success "Frontend is responding (${FRONTEND_URL})"
    else
        print_error "Frontend is not responding (${FRONTEND_URL})"
        return 1
    fi
    
    # Test review endpoint
    increment_check
    REVIEW_URL="${FRONTEND_URL}/review?hotelId=2&username=Cornell_0&password=0000000000"
    RESPONSE=$(curl -s --max-time "${TIMEOUT}" "${REVIEW_URL}" 2>/dev/null)
    
    if [ -n "${RESPONSE}" ]; then
        print_success "Review endpoint is functional"
        if [ "$VERBOSE" = true ]; then
            echo -e "    Response: ${RESPONSE:0:100}..."
        fi
    else
        print_warning "Review endpoint returned empty response"
    fi
    
    # Test recommendations endpoint
    increment_check
    RECOMMEND_URL="${FRONTEND_URL}/recommendations?require=dis&lat=37.7&lon=-122.4"
    RESPONSE=$(curl -s --max-time "${TIMEOUT}" "${RECOMMEND_URL}" 2>/dev/null)
    
    if [ -n "${RESPONSE}" ]; then
        print_success "Recommendations endpoint is functional"
    else
        print_warning "Recommendations endpoint returned empty response"
    fi
}

check_consul() {
    print_subheader "Consul Service Discovery"
    
    increment_check
    if curl -s -f --max-time "${TIMEOUT}" "${CONSUL_URL}/v1/status/leader" > /dev/null 2>&1; then
        print_success "Consul is responding (${CONSUL_URL})"
    else
        print_error "Consul is not responding (${CONSUL_URL})"
        return 1
    fi
    
    # Check registered services
    increment_check
    SERVICES_JSON=$(curl -s --max-time "${TIMEOUT}" "${CONSUL_URL}/v1/catalog/services" 2>/dev/null)
    
    if [ -n "${SERVICES_JSON}" ]; then
        SERVICE_COUNT=$(echo "${SERVICES_JSON}" | grep -o '"[^"]*":' | wc -l)
        print_success "Consul has ${SERVICE_COUNT} registered services"
        
        if [ "$VERBOSE" = true ]; then
            echo "${SERVICES_JSON}" | python3 -m json.tool 2>/dev/null | while read -r line; do
                print_info "${line}"
            done
        fi
    else
        print_warning "Could not retrieve service list from Consul"
    fi
    
    # Check specific service registrations
    EXPECTED_SERVICES=(
        "frontend"
        "profile"
        "search"
        "geo"
        "rate"
        "recommendation"
        "user"
        "reservation"
    )
    
    for service in "${EXPECTED_SERVICES[@]}"; do
        increment_check
        if echo "${SERVICES_JSON}" | grep -q "\"${service}\""; then
            print_info "Service '${service}' is registered in Consul"
            ((PASSED_CHECKS++))
        else
            print_warning "Service '${service}' not found in Consul"
        fi
    done
}

check_jaeger() {
    print_subheader "Jaeger Tracing"
    
    increment_check
    if curl -s -f --max-time "${TIMEOUT}" "${JAEGER_URL}" > /dev/null 2>&1; then
        print_success "Jaeger UI is accessible (${JAEGER_URL})"
    else
        print_error "Jaeger UI is not accessible (${JAEGER_URL})"
        return 1
    fi
    
    # Check Jaeger API
    increment_check
    JAEGER_API="${JAEGER_URL}/api/services"
    SERVICES=$(curl -s --max-time "${TIMEOUT}" "${JAEGER_API}" 2>/dev/null)
    
    if [ -n "${SERVICES}" ]; then
        SERVICE_COUNT=$(echo "${SERVICES}" | grep -o '"' | wc -l)
        print_success "Jaeger has trace data (${SERVICE_COUNT} services traced)"
    else
        print_warning "Jaeger may not have trace data yet"
    fi
}

################################################################################
# Database and Cache Checks
################################################################################

check_databases() {
    print_subheader "Database Services"
    
    cd "${APP_DIR}"
    
    MONGO_SERVICES=(
        "mongodb-geo"
        "mongodb-profile"
        "mongodb-rate"
        "mongodb-review"
        "mongodb-attractions"
        "mongodb-recommendation"
        "mongodb-reservation"
        "mongodb-user"
    )
    
    for service in "${MONGO_SERVICES[@]}"; do
        increment_check
        if docker compose ps "${service}" 2>/dev/null | grep -q "Up\|running"; then
            print_info "${service} is running"
            ((PASSED_CHECKS++))
        else
            print_warning "${service} is not running"
        fi
    done
}

check_cache() {
    print_subheader "Cache Services"
    
    cd "${APP_DIR}"
    
    MEMCACHED_SERVICES=(
        "memcached-rate"
        "memcached-profile"
        "memcached-reserve"
        "memcached-review"
    )
    
    for service in "${MEMCACHED_SERVICES[@]}"; do
        increment_check
        if docker compose ps "${service}" 2>/dev/null | grep -q "Up\|running"; then
            print_info "${service} is running"
            ((PASSED_CHECKS++))
        else
            print_warning "${service} is not running"
        fi
    done
}

################################################################################
# Performance Checks
################################################################################

check_performance() {
    print_subheader "Performance Metrics"
    
    # Check response time
    increment_check
    START_TIME=$(date +%s%N)
    curl -s --max-time "${TIMEOUT}" "${FRONTEND_URL}/review?hotelId=2&username=Cornell_0&password=0000000000" > /dev/null 2>&1
    END_TIME=$(date +%s%N)
    RESPONSE_TIME=$(( (END_TIME - START_TIME) / 1000000 ))
    
    if [ "${RESPONSE_TIME}" -lt 1000 ]; then
        print_success "Response time: ${RESPONSE_TIME}ms (excellent)"
    elif [ "${RESPONSE_TIME}" -lt 3000 ]; then
        print_success "Response time: ${RESPONSE_TIME}ms (good)"
    elif [ "${RESPONSE_TIME}" -lt 5000 ]; then
        print_warning "Response time: ${RESPONSE_TIME}ms (acceptable)"
    else
        print_warning "Response time: ${RESPONSE_TIME}ms (slow)"
    fi
    
    # Check container resource usage
    increment_check
    cd "${APP_DIR}"
    CPU_USAGE=$(docker stats --no-stream --format "{{.CPUPerc}}" $(docker compose ps -q) 2>/dev/null | sed 's/%//' | awk '{sum+=$1} END {print sum}')
    
    if [ -n "${CPU_USAGE}" ]; then
        CPU_INT=$(echo "${CPU_USAGE}" | cut -d'.' -f1)
        if [ "${CPU_INT}" -lt 50 ]; then
            print_success "Total CPU usage: ${CPU_USAGE}% (low)"
        elif [ "${CPU_INT}" -lt 80 ]; then
            print_success "Total CPU usage: ${CPU_USAGE}% (moderate)"
        else
            print_warning "Total CPU usage: ${CPU_USAGE}% (high)"
        fi
    else
        print_warning "Could not determine CPU usage"
    fi
}

################################################################################
# Summary Report
################################################################################

print_summary() {
    echo ""
    print_header "Health Check Summary"
    
    echo -e "\n${CYAN}Results:${NC}"
    echo -e "  Total checks:   ${TOTAL_CHECKS}"
    echo -e "  ${GREEN}Passed:${NC}         ${PASSED_CHECKS}"
    echo -e "  ${RED}Failed:${NC}         ${FAILED_CHECKS}"
    echo -e "  ${YELLOW}Warnings:${NC}       ${WARNING_CHECKS}"
    
    echo -e "\n${CYAN}Health Score:${NC}"
    if [ "${TOTAL_CHECKS}" -gt 0 ]; then
        SCORE=$(( (PASSED_CHECKS * 100) / TOTAL_CHECKS ))
        
        if [ "${SCORE}" -ge 90 ]; then
            echo -e "  ${GREEN}${SCORE}%${NC} - Excellent"
        elif [ "${SCORE}" -ge 75 ]; then
            echo -e "  ${GREEN}${SCORE}%${NC} - Good"
        elif [ "${SCORE}" -ge 50 ]; then
            echo -e "  ${YELLOW}${SCORE}%${NC} - Fair"
        else
            echo -e "  ${RED}${SCORE}%${NC} - Poor"
        fi
    fi
    
    echo -e "\n${CYAN}Quick Access:${NC}"
    echo "  Frontend API:  ${FRONTEND_URL}"
    echo "  Consul UI:     ${CONSUL_URL}"
    echo "  Jaeger UI:     ${JAEGER_URL}"
    
    if [ "${FAILED_CHECKS}" -gt 0 ]; then
        echo -e "\n${RED}Recommendation:${NC} Some critical checks failed. Review the output above."
        echo "  Run: ./deploy.sh logs"
        echo "  Or:  docker compose ps"
        return 1
    elif [ "${WARNING_CHECKS}" -gt 0 ]; then
        echo -e "\n${YELLOW}Recommendation:${NC} Some services need attention. Review warnings above."
        return 0
    else
        echo -e "\n${GREEN}All systems operational!${NC}"
        return 0
    fi
}

################################################################################
# Main Health Check Routine
################################################################################

run_health_check() {
    print_header "Hotel Reservation Health Check"
    echo ""
    
    # Run all checks
    check_docker || true
    check_containers || true
    check_ports || true
    check_frontend || true
    check_consul || true
    check_jaeger || true
    check_databases || true
    check_cache || true
    check_performance || true
    
    # Print summary
    print_summary
}

################################################################################
# Parse Command Line Arguments
################################################################################

show_help() {
    cat << EOF
${CYAN}Hotel Reservation Health Check Script${NC}

${YELLOW}USAGE:${NC}
    ./health_check.sh [options]

${YELLOW}OPTIONS:${NC}
    -v, --verbose       Enable verbose output
    -t, --timeout SEC   Set timeout for HTTP requests (default: ${TIMEOUT}s)
    -h, --help          Show this help message

${YELLOW}EXAMPLES:${NC}
    # Run basic health check
    ./health_check.sh

    # Run with verbose output
    ./health_check.sh --verbose

    # Use custom timeout
    ./health_check.sh --timeout 10

${YELLOW}EXIT CODES:${NC}
    0    All checks passed or only warnings
    1    One or more critical checks failed

EOF
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case $1 in
            -v|--verbose)
                VERBOSE=true
                shift
                ;;
            -t|--timeout)
                TIMEOUT="$2"
                shift 2
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                echo -e "${RED}Unknown option: $1${NC}"
                show_help
                exit 1
                ;;
        esac
    done
}

################################################################################
# Main Script Execution
################################################################################

main() {
    parse_args "$@"
    run_health_check
}

main "$@"

