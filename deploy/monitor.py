"""Read private metrics and emit bounded, credential-free JSON alerts."""
import json
import os
import sys
import time
import urllib.request


def check():
    try:
        base = os.environ.get("MONITOR_API", "http://127.0.0.1:8765")
        with urllib.request.urlopen(base + "/healthz", timeout=5) as response:
            healthy = json.load(response)["status"] == "ok"
        with urllib.request.urlopen(base + "/internal/metrics", timeout=5) as response:
            metrics = json.load(response)
        alerts = []
        if not healthy:
            alerts.append("api_unhealthy")
        if metrics["database_bytes"] > int(os.environ.get("MAX_DATABASE_BYTES", str(5 * 1024**3))):
            alerts.append("database_size")
        if metrics["disk_free_bytes"] < int(os.environ.get("MIN_DISK_FREE_BYTES", str(1024**3))):
            alerts.append("disk_space")
        if metrics["queued"] >= int(os.environ.get("MAX_QUEUE_ALERT", "5")):
            alerts.append("queue_capacity")
        if metrics["http_requests_5m"] >= 20 and metrics["http_5xx_5m"] / metrics["http_requests_5m"] >= .05:
            alerts.append("http_error_rate")
        print(json.dumps({"event": "monitor", "time": time.time(), "alerts": alerts, **metrics}), flush=True)
        return bool(alerts)
    except Exception:
        print(json.dumps({"event": "monitor", "time": time.time(), "alerts": ["monitor_unavailable"]}), flush=True)
        return True


if __name__ == "__main__":
    if "--loop" in sys.argv:
        while True:
            check()
            time.sleep(60)
    else:
        sys.exit(int(check()))
