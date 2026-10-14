#!/bin/bash
# Deploy script for AWS EC2 (Ubuntu/Amazon Linux)
# Run this on your AWS server after cloning the repo

set -e

echo "=== AmirClaw Deployment Script ==="

# Update system
sudo apt-get update -y || sudo yum update -y

# Install Node.js 20 if not installed
if ! command -v node &> /dev/null; then
    echo "Installing Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

# Install Python 3 and pip if not installed
if ! command -v python3 &> /dev/null; then
    sudo apt-get install -y python3 python3-pip python3-venv
fi

# Install PM2 globally (process manager)
if ! command -v pm2 &> /dev/null; then
    sudo npm install -g pm2
fi

# Install dependencies
echo "Installing Node dependencies..."
npm install

# Setup Python venv for reports_engine
echo "Setting up Python environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
deactivate

# Build TypeScript
echo "Building project..."
npm run build

# Create workspace directories
mkdir -p workspace/downloads workspace/whatsapp_session workspace/audio

# Setup env file if not exists
if [ ! -f .env ]; then
    echo "⚠️  WARNING: .env file not found!"
    echo "Create .env with your API keys (see .env.example)"
    exit 1
fi

# Setup service-account.json if not exists
if [ ! -f service-account.json ]; then
    echo "⚠️  WARNING: service-account.json not found!"
    echo "Upload your Firebase service account file to the server"
    exit 1
fi

echo "=== Starting with PM2 ==="
pm2 delete amirclaw 2>/dev/null || true
pm2 start dist/index.js --name amirclaw --restart-delay=5000
pm2 save
pm2 startup

echo "=== Deployment Complete! ==="
echo "Check status: pm2 status"
echo "View logs: pm2 logs amirclaw"
