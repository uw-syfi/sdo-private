## Known Benign
(none yet)

## Required Checks
- Compare every Service's targetPort against its pods' containerPort.

## Anomaly Hints
- Pods not in Running/Completed state.
- Services with 0 ready endpoints.
