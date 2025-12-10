#!/bin/bash
# LearnUs Contents Downloader - Web Version Run Script

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found!"
    echo "Please run setup.sh first: ./setup.sh"
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found. Creating template..."
    cat > .env << 'EOF'
# LearnUs Credentials (Optional - can use browser cookies instead)
# LEARNUS_USERNAME=your_yonsei_id
# LEARNUS_PASSWORD=your_password

# LLM API Settings (Optional - for future features)
# OPENAI_API_KEY=sk-...
# GOOGLE_API_KEY=...
EOF
    echo "✅ .env file created. Please edit it with your settings if needed."
fi

# Run the application
echo "Starting LearnUs Contents Downloader - Web Version..."
echo "Server will be available at http://0.0.0.0:5000"
echo "Press Ctrl+C to stop"
echo ""

python app.py --host 0.0.0.0 --port 5000

