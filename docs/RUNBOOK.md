# Observe Error Runbook

This document maps common errors from the dashboard to root causes and recommended fixes. Use it when analyzing `error_to_fix.txt` or `error_report.txt`.

---

## [DeploymentControllerRMQ] error while deployment.onCreate on DeploymentControllerRMQ

**Service:** Launch Management Background Jobs Service  
**Region:** Typically aws-na, aws-eu (seen across regions)  
**Dataset:** 41249174

### What it means

The `DeploymentControllerRMQ` is a RabbitMQ message handler in the management-background-jobs service. It processes `deployment.onCreate` events. This error indicates the handler failed while creating a new deployment.

### Root causes (in order of likelihood)

1. **RabbitMQ connectivity issues**
   - RMQ broker disconnected or unavailable
   - Often co-occurs with: `[AmqpConnection] Disconnected from RabbitMQ broker (default)`, `[ClientProxy] Disconnected from RMQ. Trying to reconnect.`, `[Server] Disconnected from RMQ. Trying to reconnect.`

2. **Downstream failures**
   - Kubernetes API failures (e.g. `[KubernetesService] API call failure`, `Failed to cancel Kubernetes job`)
   - DeploymentService errors (e.g. `Error while cancelling Kubernetes job for deploymentUid`)

3. **Transient message delivery**
   - Message published to RMQ but consumer disconnected before processing
   - Network blip or pod restart during message handling

### Recommended fixes

| Action | Where | Details |
|--------|-------|---------|
| **1. Check RabbitMQ health** | Infrastructure / K8s | Verify RMQ broker is up, queues exist, no connection limits hit. Check `[AmqpConnection]` and `[Server]` logs in the same time window. |
| **2. Correlate with RMQ disconnects** | Observe Log Explorer | Use the link in the error report. Filter for `Disconnected from RMQ` or `AmqpConnection` within ±15 min of the error. If clustered, RMQ issues often explain onCreate failures. |
| **3. Verify K8s connectivity** | Management BG pods | Ensure pods can reach the K8s API. Check for `[KubernetesService]` or `[TriggerK8sJobServices]` errors. |
| **4. Add retry / dead-letter** | Management BG service (source code) | In `DeploymentControllerRMQ`'s `onCreate` handler: wrap in try/catch, log the underlying error (not just "error while deployment.onCreate"), and consider retry or DLQ for transient failures. |
| **5. Improve error logging** | Management BG service | The current message is generic. Log the caught exception (e.g. `err.message`, `err.stack`) so future occurrences have actionable context. |

### Source code location (outside this repo)

The fix for the **underlying error** lives in the **management-background-jobs service** (contentfly / Launch). This Observe-automation repo only extracts and reports errors; it does not contain that service’s code.

Look for:
- `DeploymentControllerRMQ` class or controller
- Handler for `deployment.onCreate` event
- RabbitMQ consumer setup and error handling

### Related errors (from historical reports)

- `[AmqpConnection] Disconnected from RabbitMQ broker (default)`
- `[ClientProxy] Disconnected from RMQ. Trying to reconnect.`
- `[Server] Disconnected from RMQ. Trying to reconnect.`
- `[DeploymentService] Error while cancelling Kubernetes job for deploymentUid ...`
- `[KubernetesService] API call failure error message: Failed to cancel Kubernetes job ...`
- `RMQ unavailable` (GCP NA/EU)

---

## Quick reference: Log Explorer link

For the error in `error_to_fix.txt`, use the **Link** column to open Observe Log Explorer. Adjust the time range to see surrounding logs (e.g. RMQ disconnects, K8s errors) for correlation.
