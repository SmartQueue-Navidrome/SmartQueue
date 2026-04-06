import os
import signal
import time

import ray
from ray import serve

from app import RankingService


def main():
    host = os.environ.get("RAY_SERVE_HOST", "0.0.0.0")
    port = int(os.environ.get("RAY_SERVE_PORT", "8000"))
    replicas = int(os.environ.get("RAY_SERVE_REPLICAS", "2"))

    ray.init(address="auto", ignore_reinit_error=True)
    serve.start(
        detached=False,
        http_options={
            "host": host,
            "port": port,
        },
    )
    serve.run(RankingService.options(num_replicas=replicas).bind(), route_prefix="/")

    keep_running = True

    def _stop(_signum, _frame):
        nonlocal keep_running
        keep_running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while keep_running:
        time.sleep(1)


if __name__ == "__main__":
    main()
