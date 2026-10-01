"""Run Klack's web, API, and maintenance processes on one Render service."""

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    children: list[subprocess.Popen] = []
    stopping = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        # Render's free service has no pre-deploy command. Upgrade before accepting traffic.
        migration = subprocess.Popen(["alembic", "upgrade", "head"])
        children.append(migration)
        if migration.wait() != 0 or stopping:
            return 1
        commands = [
            ["uvicorn", "klack.main:create_app", "--factory", "--host", "127.0.0.1",
             "--port", "8000", "--no-access-log", "--ws-max-size", "16384"],
            ["klack-identity-worker", "run"],
            ["klack-files-worker"],
        ]
        children.clear()
        for command in commands:
            children.append(subprocess.Popen(command))
        web_env = {**os.environ, "HOSTNAME": "0.0.0.0"}
        children.append(subprocess.Popen(
            ["node", "server.js"], cwd="/app/frontend", env=web_env,
        ))
        while not stopping:
            for child in children:
                code = child.poll()
                if code is not None:
                    print(f"Service process exited ({code}); restarting the container.", flush=True)
                    return 1
            time.sleep(0.5)
        return 0
    finally:
        stop(signal.SIGTERM, None)
        deadline = time.monotonic() + 15
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    sys.exit(main())
