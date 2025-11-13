import requests, base64, json

TERRARIUM_URL = "http://localhost:8090"  # Endpoint for Terrarium service

sandbox_code = r"""
import matplotlib.pyplot as plt
import numpy as np

n = 100
voltage = np.random.uniform(300, 400, n)
current = np.random.uniform(50, 150, n)
power = voltage * current

plt.figure(figsize=(6,4))
sc = plt.scatter(voltage, current, c=power, cmap='viridis', s=40)
plt.colorbar(sc, label='Power (W)')
plt.title('Random Voltage–Current–Power Scatter')
plt.xlabel('Voltage (V)')
plt.ylabel('Current (A)')
plt.tight_layout()
plt.savefig("output.png")
print("Saved scatterplot as output.png")
"""

payload = {"code": sandbox_code}

print("Submitting code to Terrarium...")
resp = requests.post(TERRARIUM_URL, json=payload)
resp.raise_for_status()

result = resp.json()
print("Response received!\n")
print(json.dumps(result, indent=2))

# Extract file (json dict)
"""

{
  "success": true,
  "output_files": [
    {
      "filename": "output.png",
      "b64_data": "iVBORw0KGgoAAAAN... (truncated for brevity) ... "

"""

output_files = result.get("output_files", [])
if output_files:
    file_info = output_files[0]  # Get the first file dict
    filename = file_info["filename"]
    b64_data = file_info["b64_data"]
    
    # Remove all whitespace from base64 string
    b64_data = ''.join(b64_data.split())
    
    # Decode and save
    decoded_data = base64.b64decode(b64_data)
    
    with open(filename, "wb") as f:
        f.write(decoded_data)
    print(f"Image saved locally as: {filename}")
else:
    print("No output files returned.")

print("\nSTDOUT:\n", result.get("std_out", ""))
print("STDERR:\n", result.get("std_err", ""))