#!/usr/bin/env python3
"""
InfluxDB Token Extractor for Grafana Auto-Configuration
Automatically extracts the all-access token from InfluxDB and configures Grafana
"""

import requests
import json
import time
import os
import sys
from datetime import datetime

class InfluxTokenExtractor:
    def __init__(self):
        self.influxdb_url = "http://localhost:8086"
        self.username = "admin"
        self.password = os.getenv("INFLUXDB_PASSWORD", "YOUR_INFLUXDB_PASSWORD")
        self.org = "WFR"
        self.session = requests.Session()
    
    def wait_for_influxdb(self, max_attempts=30):
        """Wait for InfluxDB to be ready"""
        print("⏳ Waiting for InfluxDB to be ready...")
        
        for attempt in range(1, max_attempts + 1):
            try:
                response = self.session.get(f"{self.influxdb_url}/health", timeout=5)
                if response.status_code == 200:
                    print("✅ InfluxDB is ready!")
                    return True
            except requests.exceptions.RequestException:
                pass
            
            print(f"   Attempt {attempt}/{max_attempts}: InfluxDB not ready yet, waiting 2 seconds...")
            time.sleep(2)
        
        print("❌ InfluxDB failed to become ready within the timeout period")
        return False
    
    def authenticate(self):
        """Authenticate with InfluxDB"""
        print("🔐 Authenticating with InfluxDB...")
        
        auth_data = {
            "username": self.username,
            "password": self.password
        }
        
        try:
            response = self.session.post(
                f"{self.influxdb_url}/api/v2/signin",
                json=auth_data,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 204:
                print("✅ Authentication successful!")
                return True
            else:
                print(f"❌ Authentication failed: {response.status_code}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"❌ Authentication error: {e}")
            return False
    
    def get_existing_token(self):
        """Get existing all-access token if available"""
        print("📋 Fetching existing tokens...")
        
        try:
            response = self.session.get(
                f"{self.influxdb_url}/api/v2/authorizations",
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code != 200:
                print(f"❌ Failed to fetch tokens: {response.status_code}")
                return None
            
            tokens_data = response.json()
            
            # Look for a token with comprehensive permissions
            for auth in tokens_data.get("authorizations", []):
                permissions = auth.get("permissions", [])
                if len(permissions) > 10:  # Likely an all-access token
                    print("✅ Found existing all-access token!")
                    return auth.get("token")
            
            print("⚠️  No existing all-access token found")
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching tokens: {e}")
            return None
    
    def create_all_access_token(self):
        """Create a new all-access token"""
        print("🔧 Creating new all-access token...")
        
        # Get organization ID first
        try:
            orgs_response = self.session.get(f"{self.influxdb_url}/api/v2/orgs")
            if orgs_response.status_code != 200:
                print(f"❌ Failed to get organizations: {orgs_response.status_code}")
                return None
            
            orgs_data = orgs_response.json()
            org_id = None
            
            for org in orgs_data.get("orgs", []):
                if org.get("name") == self.org:
                    org_id = org.get("id")
                    break
            
            if not org_id:
                print(f"❌ Organization '{self.org}' not found")
                return None
            
            # Create comprehensive permissions
            permissions = [
                {"action": "read", "resource": {"type": "buckets"}},
                {"action": "write", "resource": {"type": "buckets"}},
                {"action": "read", "resource": {"type": "dashboards"}},
                {"action": "read", "resource": {"type": "tasks"}},
                {"action": "read", "resource": {"type": "telegrafs"}},
                {"action": "read", "resource": {"type": "users"}},
                {"action": "read", "resource": {"type": "variables"}},
                {"action": "read", "resource": {"type": "scrapers"}},
                {"action": "read", "resource": {"type": "secrets"}},
                {"action": "read", "resource": {"type": "labels"}},
                {"action": "read", "resource": {"type": "views"}},
                {"action": "read", "resource": {"type": "documents"}},
                {"action": "read", "resource": {"type": "notificationRules"}},
                {"action": "read", "resource": {"type": "notificationEndpoints"}},
                {"action": "read", "resource": {"type": "checks"}},
                {"action": "read", "resource": {"type": "dbrp"}}
            ]
            
            token_data = {
                "description": f"Grafana All-Access Token - {datetime.now().isoformat()}",
                "orgID": org_id,
                "permissions": permissions
            }
            
            response = self.session.post(
                f"{self.influxdb_url}/api/v2/authorizations",
                json=token_data,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 201:
                token_response = response.json()
                token = token_response.get("token")
                if token:
                    print("✅ Created new all-access token!")
                    return token
            
            print(f"❌ Failed to create token: {response.status_code}")
            print(f"Response: {response.text}")
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Error creating token: {e}")
            return None
    
    def save_token_to_env(self, token):
        """Save token to .env file"""
        print("💾 Writing token to .env file...")
        
        env_content = f"""# InfluxDB Configuration
INFLUXDB_TOKEN={token}

# Generated on: {datetime.now().isoformat()}
# This token provides all-access permissions for Grafana integration
"""
        
        try:
            with open(".env", "w") as f:
                f.write(env_content)
            print("✅ Token saved to .env file!")
            return True
        except Exception as e:
            print(f"❌ Failed to save token: {e}")
            return False
    
    def extract_token(self):
        """Main method to extract and configure the token"""
        print("🔍 Starting InfluxDB Token Extraction...")
        
        # Wait for InfluxDB to be ready
        if not self.wait_for_influxdb():
            return False
        
        # Authenticate
        if not self.authenticate():
            return False
        
        # Try to get existing token
        token = self.get_existing_token()
        
        # If no existing token, create one
        if not token:
            token = self.create_all_access_token()
        
        if not token:
            print("❌ Failed to obtain access token")
            return False
        
        print(f"🔑 Token extracted: {token[:20]}...")
        
        # Save to .env file
        if not self.save_token_to_env(token):
            return False
        
        print("\n🚀 Success! You can now start the stack with:")
        print("   docker-compose up -d")
        print("\n📊 Grafana will automatically use this token to connect to InfluxDB!")
        
        return True

def main():
    extractor = InfluxTokenExtractor()
    
    if extractor.extract_token():
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
