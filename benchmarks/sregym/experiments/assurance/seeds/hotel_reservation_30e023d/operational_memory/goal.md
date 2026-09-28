---
schema_version: 1
owner: human
application: Hotel Reservation
---
# Health objective

The deployed Deployments named consul, frontend, geo, jaeger, memcached-profile, memcached-rate, memcached-reserve, mongodb-geo, mongodb-profile, mongodb-rate, mongodb-recommendation, mongodb-reservation, mongodb-user, profile, rate, recommendation, reservation, search, user remain available; the deployed Services named consul, frontend, geo, jaeger-out, memcached-profile, memcached-rate, memcached-reserve, mongodb-geo, mongodb-profile, mongodb-rate, mongodb-recommendation, mongodb-reservation, mongodb-user, profile, rate, recommendation, reservation, search, user expose ready endpoints; the ExternalName Services named jaeger, jaeger-agent, jaeger-collector, jaeger-query are DNS aliases with no endpoints and must not be required to have ready endpoints; required non-optional ConfigMap volume references remain present; and representative requests succeed.
