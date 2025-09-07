#!/usr/bin/env python3
"""
WFR DAQ System - Startup Data Loader
Loads CSV data from startup-data directory into InfluxDB during system initialization
"""

import os
import sys
import asyncio
import time
from pathlib import Path
from typing import Optional

# Add the startup-data directory to the path to import helper
SCRIPT_DIR = Path(__file__).parent
STARTUP_DATA_DIR = SCRIPT_DIR.parent / "startup-data"
sys.path.append(str(STARTUP_DATA_DIR))

try:
    from helper import CANInfluxStreamer
except ImportError as e:
    print(f"❌ Failed to import helper module: {e}")
    print("Make sure the startup-data/helper.py file exists and has proper dependencies")
    sys.exit(1)

def find_csv_files(directory: Path) -> list[Path]:
    """Find all CSV files in the startup-data directory"""
    csv_files = list(directory.glob("*.csv"))
    return csv_files

def find_dbc_file() -> Optional[Path]:
    """Find the DBC file in car-to-influx directory"""
    installer_dir = SCRIPT_DIR.parent
    dbc_locations = [
        installer_dir / "car-to-influx" / "WFR25-f772b40.dbc",
        installer_dir / "car-to-influx" / "WFR25.dbc",
        installer_dir / "car-to-influx" / "WFR25-d3bcc24.dbc",
    ]
    
    for dbc_path in dbc_locations:
        if dbc_path.exists():
            return dbc_path
    
    return None

def progress_callback(processed: int, total: int):
    """Progress callback for data upload"""
    if total > 0:
        percentage = (processed / total) * 100
        print(f"\r📊 Progress: {processed:,}/{total:,} rows ({percentage:.1f}%)", end="", flush=True)

async def load_startup_data():
    """Load all CSV data from startup-data directory into InfluxDB"""
    print("🚀 WFR DAQ System - Startup Data Loader")
    print("=" * 50)
    
    # Check if we have the required token
    if not os.getenv("INFLUXDB_TOKEN") and not os.getenv("TOKEN"):
        print("❌ No InfluxDB token found in environment")
        print("Make sure INFLUXDB_TOKEN is set in .env file")
        return False
    
    # Set TOKEN for helper.py compatibility
    influx_token = os.getenv("INFLUXDB_TOKEN")
    if influx_token and not os.getenv("TOKEN"):
        os.environ["TOKEN"] = influx_token
    
    # Find DBC file
    dbc_file = find_dbc_file()
    if not dbc_file:
        print("❌ No DBC file found in car-to-influx directory")
        print("Expected files: WFR25-f772b40.dbc, WFR25.dbc, or WFR25-d3bcc24.dbc")
        return False
    
    # Copy DBC file to startup-data directory for helper.py
    import shutil
    startup_dbc = STARTUP_DATA_DIR / dbc_file.name
    if not startup_dbc.exists():
        shutil.copy2(dbc_file, startup_dbc)
        print(f"📁 Copied DBC file: {dbc_file.name}")
    
    # Find CSV files
    csv_files = find_csv_files(STARTUP_DATA_DIR)
    if not csv_files:
        print("⚠️  No CSV files found in startup-data directory")
        return True  # Not an error, just no data to load
    
    print(f"📂 Found {len(csv_files)} CSV file(s) to process:")
    for csv_file in csv_files:
        print(f"   • {csv_file.name}")
    print()
    
    # Initialize streamer
    try:
        # Change to startup-data directory so helper.py can find DBC file
        original_cwd = os.getcwd()
        os.chdir(STARTUP_DATA_DIR)
        
        streamer = CANInfluxStreamer(bucket="ourCar", batch_size=5000, max_concurrent_uploads=5)
        
        total_files = len(csv_files)
        for i, csv_file in enumerate(csv_files, 1):
            print(f"📊 Processing file {i}/{total_files}: {csv_file.name}")
            
            try:
                with open(csv_file, 'rb') as f:
                    await streamer.stream_to_influx(
                        file=f,
                        is_csv=True,
                        csv_filename=csv_file.name,
                        on_progress=progress_callback
                    )
                print(f"\n✅ Successfully loaded {csv_file.name}")
                
            except Exception as e:
                print(f"\n❌ Failed to process {csv_file.name}: {e}")
                continue
            
            print()  # Add spacing between files
        
        print("🎉 Startup data loading completed!")
        return True
        
    except Exception as e:
        print(f"❌ Error initializing data streamer: {e}")
        return False
    finally:
        try:
            streamer.close()
        except:
            pass
        # Restore original working directory
        os.chdir(original_cwd)

def main():
    """Main entry point"""
    start_time = time.time()
    
    # Set debug mode for local InfluxDB
    os.environ["DEBUG"] = "0"  # Use local InfluxDB in Docker
    
    try:
        success = asyncio.run(load_startup_data())
        elapsed = time.time() - start_time
        
        if success:
            print(f"\n🏁 Data loading completed in {elapsed:.2f} seconds")
            sys.exit(0)
        else:
            print(f"\n💥 Data loading failed after {elapsed:.2f} seconds")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n⏹️  Data loading interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
