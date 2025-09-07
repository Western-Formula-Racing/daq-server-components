#!/bin/bash
set -e

# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y docker.io docker-compose jq curl python3 python3-pip

# Enable and start Docker
sudo systemctl enable docker
sudo systemctl start docker

# Add current user to docker group (so no sudo needed)
sudo usermod -aG docker ubuntu

# Reboot required for group membership to take effect
echo "✅ Bootstrap complete. Please reboot instance or log out/in before running your DAQ startup script."