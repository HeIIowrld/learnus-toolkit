#!/bin/bash
# LearnUs Contents Downloader - Web Version Setup Script
# For Proxmox LXC deployment

set -e  # Exit on error

echo "LearnUs Contents Downloader - Web Version Setup"
echo "================================================"

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
   echo "Do not run as root. Script will use sudo when needed."
   exit 1
fi

# Detect Linux distribution
if [ -f /etc/debian_version ]; then
    DISTRO="debian"
elif [ -f /etc/redhat-release ]; then
    DISTRO="redhat"
else
    echo "Unsupported Linux distribution"
    exit 1
fi

echo "Installing system dependencies..."

if [ "$DISTRO" = "debian" ]; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv ffmpeg git curl
elif [ "$DISTRO" = "redhat" ]; then
    sudo yum install -y python3 python3-pip ffmpeg git curl
    # Create venv module if needed
    python3 -m pip install --user virtualenv
fi

echo "System dependencies installed"

# Create virtual environment
echo "Creating Python virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install Python dependencies
echo "Installing Python packages..."
pip install -r requirements.txt

echo "Python dependencies installed"

# Create downloads directory
echo "Creating downloads directory..."
mkdir -p downloads

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file template..."
    cat > .env << 'EOF'
# LearnUs Credentials (Optional - browser cookies can be used instead)
# LEARNUS_USERNAME=your_yonsei_id
# LEARNUS_PASSWORD=your_password

# LLM API Settings (Optional)
# OPENAI_API_KEY=sk-...
# GOOGLE_API_KEY=...
# LLM_PROVIDER=openai
# OPENAI_MODEL=gpt-4
# GOOGLE_MODEL=gemini-pro
# OLLAMA_URL=http://localhost:11434
# OLLAMA_MODEL=llama2
EOF
    echo ".env file created"
else
    echo ".env file already exists"
fi

# Set permissions
echo "Setting permissions..."
chmod +x app.py
chmod -R 755 .

echo ""
echo "Setup completed successfully!"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your credentials (optional)"
echo "2. Run: source venv/bin/activate && python app.py"
echo "3. Or use the run.sh script: ./run.sh"
echo ""
echo "The application will be available at http://localhost:5000"

