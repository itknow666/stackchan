"""Run explicitly, or install the separate current-user startup helper."""
import argparse
import asyncio
import logging
from pathlib import Path

from dania_pc.client import Client
from dania_pc.config import load_config


def main():
    parser = argparse.ArgumentParser(description="Dania allowlisted Windows PC assistant")
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "local-config.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    # Dependency libraries must not log authentication headers or received frames.
    logging.getLogger("websockets").setLevel(logging.CRITICAL)
    try:
        config = load_config(args.config)
    except Exception:
        parser.exit(2, "Configuration is invalid or unavailable; check the local configuration file.\n")
    try:
        asyncio.run(Client(config).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
