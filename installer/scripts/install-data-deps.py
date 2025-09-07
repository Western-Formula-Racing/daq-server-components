#!/usr/bin/env python3
"""
WFR DAQ System - Install startup data dependencies
"""

import subprocess
import sys
import os

def install_package(package):
    """Install a package using pip"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        return True
    except subprocess.CalledProcessError:
        return False

def main():
    """Install required packages for startup data loading"""
    print("📦 Installing startup data loader dependencies...")
    
    packages = [
        "influxdb-client",
        "cantools", 
        "python-dotenv",
        "asyncio"
    ]
    
    failed_packages = []
    
    for package in packages:
        print(f"Installing {package}...", end=" ")
        if install_package(package):
            print("✅")
        else:
            print("❌")
            failed_packages.append(package)
    
    if failed_packages:
        print(f"\n❌ Failed to install: {', '.join(failed_packages)}")
        print("Please install them manually:")
        for pkg in failed_packages:
            print(f"  pip install {pkg}")
        return False
    else:
        print("\n✅ All dependencies installed successfully!")
        return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
