#!/usr/bin/env python3
"""
WFR DAQ Test Data Generator
Populates InfluxDB with realistic racing telemetry data for dashboard testing
"""

import os
import sys
import time
import random
import math
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# InfluxDB Configuration
INFLUXDB_URL = "http://localhost:8086"
INFLUXDB_ORG = "WFR"
INFLUXDB_BUCKET = "ourCar"

def get_influxdb_token():
    """Get InfluxDB token from environment or .env file"""
    token = os.getenv('INFLUXDB_TOKEN')
    if not token:
        try:
            with open('.env', 'r') as f:
                for line in f:
                    if line.startswith('INFLUXDB_TOKEN='):
                        token = line.split('=', 1)[1].strip()
                        break
        except FileNotFoundError:
            pass
    
    if not token:
        print("❌ INFLUXDB_TOKEN not found in environment or .env file")
        sys.exit(1)
    
    return token

def generate_racing_session_data(duration_minutes=10):
    """Generate realistic racing telemetry data"""
    
    print(f"🏁 Generating {duration_minutes} minutes of racing telemetry...")
    
    token = get_influxdb_token()
    client = InfluxDBClient(url=INFLUXDB_URL, token=token, org=INFLUXDB_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    
    start_time = datetime.utcnow() - timedelta(minutes=duration_minutes)
    points = []
    
    # Simulation parameters
    lap_time = 90  # seconds per lap
    total_points = duration_minutes * 60 * 2  # 2 points per second
    
    for i in range(total_points):
        timestamp = start_time + timedelta(seconds=i * 0.5)
        lap_progress = (i * 0.5) % lap_time / lap_time  # 0-1 progress through lap
        
        # Simulate racing conditions based on track position
        if lap_progress < 0.3:  # Straight section
            base_speed = 180 + random.uniform(-10, 15)
            base_rpm = 7000 + random.uniform(-200, 500)
            lateral_g = random.uniform(-0.5, 0.5)
            longitudinal_g = random.uniform(-0.8, 1.2)
        elif lap_progress < 0.7:  # Turn section
            base_speed = 80 + random.uniform(-15, 25)
            base_rpm = 5500 + random.uniform(-300, 800)
            lateral_g = random.uniform(-2.5, 2.5)
            longitudinal_g = random.uniform(-1.5, 0.5)
        else:  # Acceleration zone
            base_speed = 120 + random.uniform(-20, 40)
            base_rpm = 6500 + random.uniform(-400, 1000)
            lateral_g = random.uniform(-1.0, 1.0)
            longitudinal_g = random.uniform(-0.5, 2.0)
        
        # Engine telemetry
        points.append(
            Point("engine")
            .field("rpm", max(1000, base_rpm))
            .field("coolant_temp", 85 + random.uniform(-5, 15))
            .field("oil_pressure", 35 + random.uniform(-5, 10))
            .field("throttle_position", max(0, min(100, 60 + random.uniform(-30, 40))))
            .time(timestamp)
        )
        
        # Vehicle dynamics
        points.append(
            Point("vehicle")
            .field("speed", max(0, base_speed))
            .field("steering_angle", random.uniform(-45, 45))
            .field("brake_pressure", random.uniform(0, 80) if base_speed < 100 else random.uniform(0, 20))
            .time(timestamp)
        )
        
        # G-forces
        points.append(
            Point("accelerometer")
            .field("lateral_g", lateral_g)
            .field("longitudinal_g", longitudinal_g)
            .field("vertical_g", 1.0 + random.uniform(-0.3, 0.5))
            .time(timestamp)
        )
        
        # Tire data
        base_pressure = 30
        points.append(
            Point("tires")
            .field("pressure_fl", base_pressure + random.uniform(-2, 3))
            .field("pressure_fr", base_pressure + random.uniform(-2, 3))
            .field("pressure_rl", base_pressure + random.uniform(-2, 3))
            .field("pressure_rr", base_pressure + random.uniform(-2, 3))
            .field("temp_fl", 80 + random.uniform(-10, 25))
            .field("temp_fr", 80 + random.uniform(-10, 25))
            .field("temp_rl", 80 + random.uniform(-10, 25))
            .field("temp_rr", 80 + random.uniform(-10, 25))
            .time(timestamp)
        )
        
        # Fuel system
        fuel_consumption_rate = 0.1  # % per minute
        fuel_level = max(0, 100 - (i * 0.5 / 60) * fuel_consumption_rate)
        points.append(
            Point("fuel")
            .field("level", fuel_level)
            .field("pressure", 45 + random.uniform(-5, 10))
            .field("flow_rate", random.uniform(8, 15))
            .time(timestamp)
        )
        
        # Batch write every 100 points
        if len(points) >= 100:
            write_api.write(bucket=INFLUXDB_BUCKET, record=points)
            points = []
            print(f"📊 Written batch {i//100 + 1} ({i/total_points*100:.1f}% complete)")
    
    # Write remaining points
    if points:
        write_api.write(bucket=INFLUXDB_BUCKET, record=points)
    
    client.close()
    print(f"✅ Generated {total_points} data points over {duration_minutes} minutes")
    print(f"🎯 Data range: {start_time.strftime('%H:%M:%S')} to {datetime.utcnow().strftime('%H:%M:%S')}")

def main():
    duration = 5  # Default 5 minutes
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
        except ValueError:
            print("Usage: python3 generate_test_data.py [duration_minutes]")
            sys.exit(1)
    
    print("🏎️  WFR Racing Telemetry Generator")
    print("=" * 40)
    
    generate_racing_session_data(duration)
    
    print("\n🚀 Ready to test dashboards!")
    print("📈 Open Grafana: http://localhost:8087")
    print("🔐 Login: admin / admin")
    print("📊 Check: WFR Racing Telemetry Dashboard")

if __name__ == "__main__":
    main()
