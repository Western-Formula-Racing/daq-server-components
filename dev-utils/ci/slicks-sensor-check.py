#!/usr/bin/env python3
"""
CI check: use slicks to discover sensors for June 2025.

Verifies the sandbox's InfluxDB connection and that the slicks
package can reach the database and return a non-empty sensor list.

Requires env vars: INFLUX_URL, INFLUX_TOKEN, INFLUX_DB
(set via GitHub secrets).
"""

import sys
from datetime import datetime

import slicks


def main() -> None:
    sensors = slicks.discover_sensors(
        start_time=datetime(2025, 6, 1),
        end_time=datetime(2025, 7, 1),
    )

    if not sensors:
        print("FAIL: discover_sensors returned an empty list for June 2025.")
        sys.exit(1)

    print(f"OK: Found {len(sensors)} sensors for June 2025:")
    for name in sensors:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
