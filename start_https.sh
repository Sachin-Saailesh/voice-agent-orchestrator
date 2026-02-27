#!/bin/bash
# Starts the voice agent server over HTTPS (required for microphone access in Brave/Chrome).
# Self-signed cert lives in ./certs/  — generated once by setup.
set -e
cd "$(dirname "$0")"

CERT="certs/cert.pem"
KEY="certs/key.pem"

# Regenerate cert if missing
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  echo "Generating self-signed TLS certificate..."
  mkdir -p certs
  openssl req -x509 -newkey rsa:2048 \
    -keyout "$KEY" -out "$CERT" \
    -days 3650 -nodes \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
  echo "Certificate generated."
fi

echo ""
echo "================================================================"
echo "  Voice Agent HTTPS Server"
echo "  Open: https://localhost:8000"
echo ""
echo "  First visit: Brave/Chrome will show 'Your connection is not"
echo "  private'. Click Advanced → Proceed to localhost (unsafe)."
echo "  This is normal for a self-signed cert on localhost."
echo "================================================================"
echo ""

# Activate the project venv so all installed packages (aiortc, etc) are found
if [ -f "../.venv/bin/activate" ]; then
  source "../.venv/bin/activate"
elif [ -f "../../.venv/bin/activate" ]; then
  source "../../.venv/bin/activate"
fi

cd src
uvicorn streaming.server:app \
  --host 0.0.0.0 \
  --port 8000 \
  --ssl-keyfile "../$KEY" \
  --ssl-certfile "../$CERT" \
  --reload
