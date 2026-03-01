# Target Applications

This directory contains the online applications used to evaluate SDS. Microservices architectures are the first class studied — they represent realistic, multi-service deployment complexity that exercises the operator's full capabilities (codebase analysis, script generation, self-healing, health monitoring).

Each application is a self-contained benchmark with its own `docker-compose.yml` and service configuration. The operator generates deployment and health check scripts tailored to each application's structure.

## Applications

* [deathstarbench/hotelReservation](./deathstarbench/hotelReservation)
* [deathstarbench/mediaMicroservices](./deathstarbench/mediaMicroservices)
* [deathstarbench/socialNetwork](./deathstarbench/socialNetwork)
* [fleetcast](./fleetcast)
* [onlineboutique](./onlineboutique)
* [pitstop](./pitstop)
* [sockshop](./sockshop)
* [sockshop-helidon](./sockshop-helidon)
* [teastore](./teastore)
* [teastore-saga](./teastore-saga)
* [train-ticket](./train-ticket)
