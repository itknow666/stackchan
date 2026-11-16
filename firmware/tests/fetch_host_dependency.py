"""Fetch only the pinned header-only parser needed by firmware host tests."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    lock = Path(__file__).resolve().parents[1] / "repos.json"
    dependency = next(item for item in json.loads(lock.read_text(encoding="utf-8"))
                      if item["path"] == "components/ArduinoJson")
    destination = args.destination.resolve()
    if destination.exists():
        raise SystemExit("Destination must not exist; refusing to modify an existing checkout")
    destination.mkdir(parents=True)
    subprocess.run(["git", "init", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "fetch", "--depth=1",
                    dependency["url"], dependency["branch"]], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"], check=True)
    actual = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
    if actual != dependency["branch"]:
        raise SystemExit("Dependency commit does not match repos.json")


if __name__ == "__main__":
    main()
