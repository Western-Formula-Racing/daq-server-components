from __future__ import annotations

import asyncio
import logging

from backend.config import get_settings
from backend.services import DataDownloaderService

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def run_worker():
    settings = get_settings()
    service = DataDownloaderService(settings)
    interval = max(30, settings.periodic_interval_seconds)
    logging.info("Starting periodic scanner loop (interval=%ss)", interval)
    while True:
        try:
            logging.info("Running scheduled scan...")
            service.run_full_scan(source="periodic")
            logging.info("Finished scheduled scan.")
            await asyncio.sleep(interval)
        except Exception:
            logging.exception("Scheduled scan failed. Retrying in 60s...")
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(run_worker())
