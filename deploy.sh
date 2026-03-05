#!/bin/bash
set -ex
# This script deploys the Hotel Reservation application.
# MODIFIED: The original script ran docker-compose directly, but no
# docker-compose.yml exists in this directory, causing it to run a
# different application's compose file from a parent directory.
# This version uses the Makefile, which is expected to contain the
# correct build and deployment logic for this Go application.


# Navigate to the application directory
cd apps/deathstarbench/hotelReservation

# Build and run the application using the Makefile, which is assumed
# to contain the correct orchestration logic (e.g., calling docker-compose
# with the right configuration file). `make up` is a common convention for this.
make
make up
