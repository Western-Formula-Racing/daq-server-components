import os
import docker
from google import genai

try:
    from dotenv import load_dotenv
    # Load .env from parent directory (installer/.env) or current directory
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        load_dotenv()
except ImportError:
    pass

# --- CONFIGURATION ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError(
        "GOOGLE_API_KEY environment variable is not set. "
        "Please set it in your .env file (installer/.env) or export it in your environment."
    )

# Docker network name (if connecting to local docker-compose InfluxDB)
# Leave as None to use default bridge network (for remote InfluxDB)
DOCKER_NETWORK = "installer_datalink"  # Change to match your docker-compose network name, or None

# --- DOCKER SETUP ---
# Before running this script:
# 1. Build the influx-sandbox Docker image:
#      docker build -f Dockerfile.sandbox -t influx-sandbox .
# 2. If using local InfluxDB, ensure it's running:
#      cd installer && docker compose up -d influxdb3

# Setup Paths
CURRENT_DIR = os.path.abspath(os.getcwd())
OUTPUT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "output"))
TEMP_SCRIPT_NAME = "temp_generated_script.py"

# Initialize Clients
client = genai.Client(api_key=GOOGLE_API_KEY)
docker_client = docker.from_env()

# --- THE SYSTEM PROMPT ---
# We verify the connection details are hardcoded for the AI
SYSTEM_INSTRUCTIONS = """
You are a Python Data Analyst specializing in InfluxDB 3 queries and data visualization.
Your task is to write a COMPLETE, executable Python script that queries InfluxDB and creates a graph.

1. **Required Imports** (use exactly these):
   from influxdb_client_3 import InfluxDBClient3
   import pyarrow as pa
   import matplotlib.pyplot as plt
   import pandas as pd
   from datetime import datetime, timedelta

2. **Connection Setup** (use exactly this):
   client = InfluxDBClient3(
       host="http://148.113.191.22:9000",
       token="apiv3_dev-influxdb-admin-token",
       database="WFR25"
   )

3. **Database Schema**:
   - Schema name: "iox"
   - Table name: "WFR25" (or WFR26, WFR27, etc. for different years)
   - Full table reference: "iox"."WFR25"
   - Key columns: time, "sensorReading", "signalName"
   - Always use double quotes for column names: "signalName", "sensorReading"

4. **Date Range Handling**:
   - Define a default time range to avoid exceeding limits:
     DEFAULT_START_DATE = datetime(2025, 10, 3)
     DEFAULT_END_DATE = datetime(2025, 10, 5)
   - Adjust dates based on user's prompt if they specify a time range
   - Always filter by time in queries: WHERE time >= '{start.isoformat()}Z' AND time < '{end.isoformat()}Z'
   - Use ISO format with 'Z' suffix for UTC: '{date.isoformat()}Z'

5. **Query Patterns**:
   - Basic query: client.query(query=sql_query_string)
   - Convert to pandas: df = result.to_pandas()
   - Always include time filtering in WHERE clauses
   - Use LIMIT clauses when appropriate to avoid memory issues
   - Example query structure:
     query = f'''
         SELECT time, "sensorReading", "signalName"
         FROM "iox"."WFR25"
         WHERE time >= '{start_date.isoformat()}Z'
           AND time < '{end_date.isoformat()}Z'
           AND "signalName" = 'SIGNAL_NAME_HERE'
         ORDER BY time
     '''
     table = client.query(query)
     df = table.to_pandas()

6. **Plotting Requirements**:
   - DO NOT use plt.show() (This is headless environment)
   - ALWAYS save the graph to: /app/output/graph.png
   - Use: plt.savefig('/app/output/graph.png', dpi=150, bbox_inches='tight')
   - Set appropriate figure size: plt.figure(figsize=(10, 6))
   - Add titles, labels, and grid for clarity
   - Handle empty dataframes gracefully (check if df.empty and print a message)

7. **Error Handling**:
   - Wrap queries in try-except blocks
   - Print helpful error messages
   - Check if dataframe is empty before plotting

8. **Output Format**:
   - Return ONLY the raw Python code
   - Do NOT wrap in markdown code blocks (no ```python or ```)
   - Write complete, runnable script from imports to plot saving
   - Include all necessary logic to fulfill the user's prompt

9. **Common Signal Names** (for reference):
   - Examples: 'INV_DC_Bus_Voltage', 'INV_AC_Current', etc.
   - If user doesn't specify, you may need to query available signals first

10. **Memory Safety**:
    - Use appropriate LIMIT clauses
    - Consider time windows for large datasets
    - Process data in chunks if needed for very large queries
"""

def get_ai_code(user_prompt):
    print("🧠 AI is generating code...")
    # Combine system instructions with user prompt for Gemini
    full_prompt = f"{SYSTEM_INSTRUCTIONS}\n\nUser Request: {user_prompt}\n\nGenerate the complete Python code:"
    
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=full_prompt
    )
    # Clean up response just in case
    code = response.text
    code = code.replace("```python", "").replace("```", "").strip()
    return code

def run_in_docker(code_content):
    print("🐳 Spinning up Docker container...")
    
    # 1. Save code to a temp file on your host
    host_script_path = os.path.abspath(os.path.join(CURRENT_DIR, TEMP_SCRIPT_NAME))
    with open(host_script_path, "w", encoding="utf-8") as f:
        f.write(code_content)

    try:
        # 2. Run the container with the script
        # We mount the temp script into the container
        # We mount the output folder to get the image back
        # Docker Python SDK handles Windows path conversion automatically
        # Prepare container run arguments
        run_kwargs = {
            "image": "influx-sandbox",
            "command": ["python", "/app/generated_script.py"],
            "volumes": {
                host_script_path: {'bind': '/app/generated_script.py', 'mode': 'ro'},
                OUTPUT_DIR: {'bind': '/app/output', 'mode': 'rw'}
            },
            "remove": True,  # Auto-delete container after run
            "stdout": True,
            "stderr": True
        }
        
        # Add network if specified (for connecting to local docker-compose services)
        if DOCKER_NETWORK:
            run_kwargs["network"] = DOCKER_NETWORK
        
        logs = docker_client.containers.run(**run_kwargs)
        
        # Decode bytes to string if needed
        if isinstance(logs, bytes):
            logs = logs.decode('utf-8')
        
        # Print container logs for debugging
        if logs:
            print("Container output:")
            print(logs)
        
        return True
    except docker.errors.ImageNotFound:
        print(f"Docker image 'influx-sandbox' not found. Please build it first.")
        return False
    except docker.errors.ContainerError as e:
        print(f"Container execution failed with exit code {e.exit_status}:")
        print(e.stderr if hasattr(e, 'stderr') else str(e))
        return False
    except Exception as e:
        print(f"Docker execution failed: {e}")
        return False
    finally:
        # Cleanup the temp script so it doesn't stay in your repo
        if os.path.exists(host_script_path):
            os.remove(host_script_path)

def main():
    # Create output folder if it doesn't exist
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    print("--- InfluxDB AI Agent Started ---")
    while True:
        user_input = input("\nWhat do you want to graph? (or 'q' to quit): ")
        if user_input.lower() == 'q':
            break

        # 1. Get Code
        code = get_ai_code(user_input)
        
        # Optional: Show generated code (uncomment to debug)
        # print("\nGenerated code:")
        # print("=" * 60)
        # print(code)
        # print("=" * 60)
        
        # 2. Execute in Sandbox
        success = run_in_docker(code)
        
        if success:
            print(f"Graph saved to: {os.path.join(OUTPUT_DIR, 'graph.png')}")
        else:
            print("Logic executed, but check output folder for results.")

if __name__ == "__main__":
    main()