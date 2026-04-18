"""
SmartQueue Model Promotion and Rollback Triggers

This script monitors the deployed model and:
1. Promotes Staging -> Production if canary metrics pass
2. Rolls back to previous version if production metrics fail

Metrics monitored:
- Error rate (4xx/5xx responses)
- p95 latency
- Invalid prediction rate (scores outside [0,1])
- Health endpoint availability

Usage:
    # Run canary evaluation for a new model
    python promotion_triggers.py canary --duration 1800

    # Run continuous production monitoring
    python promotion_triggers.py monitor --interval 60

    # Manual rollback
    python promotion_triggers.py rollback
"""

import os
import sys
import time
import json
import shutil
import subprocess
import argparse
import requests
from datetime import datetime, timezone
from dataclasses import dataclass

# ─── Configuration ───────────────────────────────────────────────────────────
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
SERVING_URL = os.environ.get("SERVING_URL", "http://localhost:8000")

# Deployment mode: "k8s" uses kubectl, "docker" uses docker compose
DEPLOY_MODE = os.environ.get("DEPLOY_MODE", "docker")
K8S_NAMESPACE = os.environ.get("K8S_NAMESPACE", "smartqueue-prod")
K8S_DEPLOYMENT = os.environ.get("K8S_DEPLOYMENT", "smartqueue-serving")
COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "docker-compose-lightgbm.yaml")
COMPOSE_SERVICE = os.environ.get("COMPOSE_SERVICE", "fastapi_lgbm")

# Promotion thresholds (canary must pass ALL)
CANARY_DURATION_SECONDS = 1800  # 30 minutes
CANARY_ERROR_RATE_MAX = 0.01   # 1%
CANARY_P95_LATENCY_MAX_MS = 800
CANARY_INVALID_SCORE_RATE_MAX = 0.001  # 0.1%

# Rollback thresholds (trigger if ANY exceeded)
ROLLBACK_ERROR_RATE_THRESHOLD = 0.02  # 2%
ROLLBACK_ERROR_RATE_DURATION_MIN = 5
ROLLBACK_P95_LATENCY_THRESHOLD_MS = 1200
ROLLBACK_P95_LATENCY_DURATION_MIN = 10
ROLLBACK_HEALTH_FAILURES_MAX = 3
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Metrics:
    error_rate: float
    p95_latency_ms: float
    invalid_score_rate: float
    request_rate: float
    avg_score: float
    rerank_rate: float
    feedback_skip_rate: float
    feedback_completion_rate: float
    timestamp: str


def query_prometheus(query: str) -> float:
    """Execute a PromQL query and return the result."""
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        if data["status"] == "success" and data["data"]["result"]:
            return float(data["data"]["result"][0]["value"][1])
        return 0.0
    except Exception as e:
        print(f"[prometheus] Query failed: {e}")
        return -1.0


def check_health() -> bool:
    """Check if serving health endpoint is responsive."""
    try:
        r = requests.get(f"{SERVING_URL}/health", timeout=5)
        return r.status_code == 200
    except:
        return False


def get_current_metrics() -> Metrics:
    """Fetch current metrics from Prometheus."""
    
    # Error rate: (4xx + 5xx) / total over last 5m
    error_rate = query_prometheus(
        'sum(rate(http_requests_total{handler="/queue",status=~"4..|5.."}[5m])) / '
        'sum(rate(http_requests_total{handler="/queue"}[5m]))'
    )
    
    # p95 latency in ms
    p95_latency = query_prometheus(
        '1000 * histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{handler="/queue"}[5m]))'
    )
    
    # Invalid score rate
    invalid_rate = query_prometheus(
        'rate(prediction_invalid_total[5m]) / rate(prediction_score_count[5m])'
    )
    
    # Request rate (requests per second)
    request_rate = query_prometheus(
        'sum(rate(http_requests_total{handler="/queue"}[5m]))'
    )
    
    # Average prediction score
    avg_score = query_prometheus(
        'sum(rate(prediction_score_sum[5m])) / sum(rate(prediction_score_count[5m]))'
    )

    rerank_rate = query_prometheus('sum(rate(smartqueue_rerank_total[5m]))')
    feedback_skip_rate = query_prometheus('sum(rate(smartqueue_feedback_skips_total[5m]))')
    feedback_completion_rate = query_prometheus('sum(rate(smartqueue_feedback_completions_total[5m]))')

    return Metrics(
        error_rate=max(0, error_rate) if error_rate >= 0 else 0,
        p95_latency_ms=max(0, p95_latency) if p95_latency >= 0 else 0,
        invalid_score_rate=max(0, invalid_rate) if invalid_rate >= 0 else 0,
        request_rate=max(0, request_rate) if request_rate >= 0 else 0,
        avg_score=avg_score if avg_score >= 0 else 0,
        rerank_rate=max(0, rerank_rate) if rerank_rate >= 0 else 0,
        feedback_skip_rate=max(0, feedback_skip_rate) if feedback_skip_rate >= 0 else 0,
        feedback_completion_rate=max(0, feedback_completion_rate) if feedback_completion_rate >= 0 else 0,
        timestamp=datetime.now(timezone.utc).isoformat()
    )


def print_metrics(m: Metrics, prefix: str = ""):
    """Pretty print metrics."""
    print(f"{prefix}Timestamp:        {m.timestamp}")
    print(f"{prefix}Request rate:     {m.request_rate:.2f} req/s")
    print(f"{prefix}Error rate:       {m.error_rate * 100:.3f}%")
    print(f"{prefix}p95 latency:      {m.p95_latency_ms:.1f} ms")
    print(f"{prefix}Invalid scores:   {m.invalid_score_rate * 100:.4f}%")
    print(f"{prefix}Avg pred score:   {m.avg_score:.3f}")
    print(f"{prefix}Rerank rate:      {m.rerank_rate:.2f} req/s")
    print(f"{prefix}Feedback skips:   {m.feedback_skip_rate:.2f}/s")
    print(f"{prefix}Feedback compl:   {m.feedback_completion_rate:.2f}/s")


def run_canary(duration_seconds: int) -> bool:
    """
    Run canary evaluation for specified duration.
    Returns True if model should be promoted, False otherwise.
    """
    print(f"\n{'='*60}")
    print(f"CANARY EVALUATION - {duration_seconds}s window")
    print(f"{'='*60}")
    print(f"\nPromotion criteria:")
    print(f"  - Error rate <= {CANARY_ERROR_RATE_MAX * 100}%")
    print(f"  - p95 latency <= {CANARY_P95_LATENCY_MAX_MS} ms")
    print(f"  - Invalid score rate <= {CANARY_INVALID_SCORE_RATE_MAX * 100}%")
    print()
    
    start_time = time.time()
    check_interval = 30  # Check every 30 seconds
    all_metrics = []
    health_failures = 0
    
    while time.time() - start_time < duration_seconds:
        elapsed = int(time.time() - start_time)
        remaining = duration_seconds - elapsed
        print(f"\n[{elapsed}s / {duration_seconds}s] Checking metrics...")
        
        # Health check
        if not check_health():
            health_failures += 1
            print(f"  ⚠ Health check failed ({health_failures} total)")
            if health_failures >= ROLLBACK_HEALTH_FAILURES_MAX:
                print(f"\n❌ CANARY FAILED: {health_failures} consecutive health failures")
                return False
        else:
            health_failures = 0
        
        # Get metrics
        m = get_current_metrics()
        all_metrics.append(m)
        print_metrics(m, prefix="  ")
        
        # Early failure checks
        if m.error_rate > CANARY_ERROR_RATE_MAX * 2:
            print(f"\n❌ CANARY FAILED: Error rate {m.error_rate*100:.2f}% exceeds 2x threshold")
            return False
        
        if m.p95_latency_ms > CANARY_P95_LATENCY_MAX_MS * 1.5:
            print(f"\n❌ CANARY FAILED: p95 latency {m.p95_latency_ms:.0f}ms exceeds 1.5x threshold")
            return False
        
        time.sleep(check_interval)
    
    # Final evaluation
    print(f"\n{'='*60}")
    print("CANARY COMPLETE - Final Evaluation")
    print(f"{'='*60}")
    
    if not all_metrics:
        print("❌ No metrics collected")
        return False
    
    # Calculate averages over the canary window
    avg_error_rate = sum(m.error_rate for m in all_metrics) / len(all_metrics)
    avg_p95 = sum(m.p95_latency_ms for m in all_metrics) / len(all_metrics)
    avg_invalid = sum(m.invalid_score_rate for m in all_metrics) / len(all_metrics)
    
    print(f"\nAverage metrics over {len(all_metrics)} samples:")
    print(f"  Error rate:       {avg_error_rate * 100:.3f}% (threshold: {CANARY_ERROR_RATE_MAX * 100}%)")
    print(f"  p95 latency:      {avg_p95:.1f} ms (threshold: {CANARY_P95_LATENCY_MAX_MS} ms)")
    print(f"  Invalid scores:   {avg_invalid * 100:.4f}% (threshold: {CANARY_INVALID_SCORE_RATE_MAX * 100}%)")
    
    # Check all criteria
    passed = True
    reasons = []
    
    if avg_error_rate > CANARY_ERROR_RATE_MAX:
        passed = False
        reasons.append(f"error rate {avg_error_rate*100:.2f}% > {CANARY_ERROR_RATE_MAX*100}%")
    
    if avg_p95 > CANARY_P95_LATENCY_MAX_MS:
        passed = False
        reasons.append(f"p95 latency {avg_p95:.0f}ms > {CANARY_P95_LATENCY_MAX_MS}ms")
    
    if avg_invalid > CANARY_INVALID_SCORE_RATE_MAX:
        passed = False
        reasons.append(f"invalid rate {avg_invalid*100:.3f}% > {CANARY_INVALID_SCORE_RATE_MAX*100}%")
    
    if passed:
        print(f"\n✅ CANARY PASSED - Model ready for production promotion")
        return True
    else:
        print(f"\n❌ CANARY FAILED:")
        for r in reasons:
            print(f"   - {r}")
        return False


def run_continuous_monitor(check_interval: int):
    """
    Continuously monitor production and trigger rollback if needed.
    """
    print(f"\n{'='*60}")
    print("PRODUCTION MONITORING")
    print(f"{'='*60}")
    print(f"\nRollback triggers:")
    print(f"  - Error rate > {ROLLBACK_ERROR_RATE_THRESHOLD * 100}% for {ROLLBACK_ERROR_RATE_DURATION_MIN}+ min")
    print(f"  - p95 latency > {ROLLBACK_P95_LATENCY_THRESHOLD_MS} ms for {ROLLBACK_P95_LATENCY_DURATION_MIN}+ min")
    print(f"  - Health endpoint fails {ROLLBACK_HEALTH_FAILURES_MAX} times consecutively")
    print(f"\nChecking every {check_interval}s...")
    
    health_failures = 0
    error_rate_violation_start = None
    latency_violation_start = None
    
    while True:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Checking...")
        
        # Health check
        if not check_health():
            health_failures += 1
            print(f"  ⚠ Health check failed ({health_failures}/{ROLLBACK_HEALTH_FAILURES_MAX})")
            if health_failures >= ROLLBACK_HEALTH_FAILURES_MAX:
                trigger_rollback("Health endpoint failed 3 times consecutively")
                health_failures = 0
        else:
            health_failures = 0
        
        # Get metrics
        m = get_current_metrics()
        print_metrics(m, prefix="  ")
        
        # Check error rate
        if m.error_rate > ROLLBACK_ERROR_RATE_THRESHOLD:
            if error_rate_violation_start is None:
                error_rate_violation_start = time.time()
                print(f"  ⚠ Error rate violation started")
            elif time.time() - error_rate_violation_start > ROLLBACK_ERROR_RATE_DURATION_MIN * 60:
                trigger_rollback(f"Error rate {m.error_rate*100:.2f}% exceeded threshold for {ROLLBACK_ERROR_RATE_DURATION_MIN}+ min")
                error_rate_violation_start = None
        else:
            error_rate_violation_start = None
        
        # Check latency
        if m.p95_latency_ms > ROLLBACK_P95_LATENCY_THRESHOLD_MS:
            if latency_violation_start is None:
                latency_violation_start = time.time()
                print(f"  ⚠ Latency violation started")
            elif time.time() - latency_violation_start > ROLLBACK_P95_LATENCY_DURATION_MIN * 60:
                trigger_rollback(f"p95 latency {m.p95_latency_ms:.0f}ms exceeded threshold for {ROLLBACK_P95_LATENCY_DURATION_MIN}+ min")
                latency_violation_start = None
        else:
            latency_violation_start = None
        
        time.sleep(check_interval)


def _execute_k8s_rollback() -> bool:
    """Roll back the K8s deployment to the previous revision."""
    kubectl = shutil.which("kubectl")
    if not kubectl:
        print("  [error] kubectl not found on PATH")
        return False

    print(f"  Running: kubectl rollout undo deployment/{K8S_DEPLOYMENT} -n {K8S_NAMESPACE}")
    result = subprocess.run(
        [kubectl, "rollout", "undo", f"deployment/{K8S_DEPLOYMENT}", "-n", K8S_NAMESPACE],
        capture_output=True, text=True, timeout=60,
    )
    print(f"  stdout: {result.stdout.strip()}")
    if result.returncode != 0:
        print(f"  stderr: {result.stderr.strip()}")
        return False

    print(f"  Waiting for rollout to complete...")
    wait = subprocess.run(
        [kubectl, "rollout", "status", f"deployment/{K8S_DEPLOYMENT}", "-n", K8S_NAMESPACE, "--timeout=120s"],
        capture_output=True, text=True, timeout=150,
    )
    print(f"  stdout: {wait.stdout.strip()}")
    return wait.returncode == 0


def _execute_docker_rollback() -> bool:
    """Restart the Docker Compose serving container (picks up previous model volume)."""
    docker = shutil.which("docker")
    if not docker:
        print("  [error] docker not found on PATH")
        return False

    compose_path = os.path.join(os.path.dirname(__file__), "..", "docker", COMPOSE_FILE)
    if not os.path.exists(compose_path):
        compose_path = COMPOSE_FILE

    print(f"  Restarting {COMPOSE_SERVICE} via docker compose...")
    result = subprocess.run(
        [docker, "compose", "-f", compose_path, "restart", COMPOSE_SERVICE],
        capture_output=True, text=True, timeout=120,
    )
    print(f"  stdout: {result.stdout.strip()}")
    if result.returncode != 0:
        print(f"  stderr: {result.stderr.strip()}")
        return False
    return True


def trigger_rollback(reason: str):
    """
    Execute an automated rollback.
    In K8s mode: kubectl rollout undo (matches the prod-health-rollback CronWorkflow).
    In Docker mode: docker compose restart the serving service.
    """
    print(f"\n{'!'*60}")
    print("ROLLBACK TRIGGERED")
    print(f"{'!'*60}")
    print(f"\nReason: {reason}")
    print(f"Time:   {datetime.now(timezone.utc).isoformat()}")
    print(f"Mode:   {DEPLOY_MODE}")
    print()

    log_entry = {
        "event": "rollback",
        "reason": reason,
        "deploy_mode": DEPLOY_MODE,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if DEPLOY_MODE == "k8s":
        success = _execute_k8s_rollback()
    else:
        success = _execute_docker_rollback()

    log_entry["success"] = success
    log_path = os.environ.get("ROLLBACK_LOG", "/tmp/rollback_log.jsonl")
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")
    print(f"\nLogged to: {log_path}")

    if success:
        print("\nRollback executed successfully. Verifying health...")
        time.sleep(10)
        if check_health():
            print("Health check PASSED after rollback")
        else:
            print("Health check FAILED after rollback — may need manual inspection")
    else:
        print("\nRollback command failed — manual intervention required")


def main():
    parser = argparse.ArgumentParser(description="SmartQueue Model Promotion/Rollback Triggers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Canary command
    canary_parser = subparsers.add_parser("canary", help="Run canary evaluation for new model")
    canary_parser.add_argument("--duration", type=int, default=CANARY_DURATION_SECONDS,
                               help=f"Canary duration in seconds (default: {CANARY_DURATION_SECONDS})")
    
    # Monitor command
    monitor_parser = subparsers.add_parser("monitor", help="Run continuous production monitoring")
    monitor_parser.add_argument("--interval", type=int, default=60,
                                help="Check interval in seconds (default: 60)")
    
    # Rollback command
    subparsers.add_parser("rollback", help="Manually trigger rollback")
    
    # Status command
    subparsers.add_parser("status", help="Show current metrics")
    
    args = parser.parse_args()
    
    if args.command == "canary":
        success = run_canary(args.duration)
        sys.exit(0 if success else 1)
    
    elif args.command == "monitor":
        run_continuous_monitor(args.interval)
    
    elif args.command == "rollback":
        trigger_rollback("Manual rollback requested")
    
    elif args.command == "status":
        print("\nCurrent Metrics:")
        print("-" * 40)
        if check_health():
            print("Health: ✅ OK")
        else:
            print("Health: ❌ FAILED")
        m = get_current_metrics()
        print_metrics(m)


if __name__ == "__main__":
    main()
