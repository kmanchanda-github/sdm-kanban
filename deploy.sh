#!/bin/bash
# Full deploy script — works without docker-compose
# Run from: /path/to/sdm-kanban/

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Copying SSL certs..."
bash "$DIR/ssl/copy_cert_from_devengagement.sh"

echo "==> Stopping existing containers..."
docker stop sdm-kanban-nginx sdm-kanban-app 2>/dev/null || true
docker rm sdm-kanban-nginx sdm-kanban-app 2>/dev/null || true

echo "==> Creating network and volume..."
docker network create sdm-kanban-net 2>/dev/null || true
docker volume create sdm-kanban-data 2>/dev/null || true

echo "==> Building Flask app image..."
docker build -t sdm-kanban "$DIR"

echo "==> Starting Flask app (internal only, not exposed)..."
docker run -d \
  --name sdm-kanban-app \
  --network sdm-kanban-net \
  --restart unless-stopped \
  -e PORT=9999 \
  -e DATA_DIR=/app/data \
  -v sdm-kanban-data:/app/data \
  sdm-kanban

echo "==> Starting Nginx (HTTPS on 9999)..."
docker run -d \
  --name sdm-kanban-nginx \
  --network sdm-kanban-net \
  --restart unless-stopped \
  -p 9999:9999 \
  -v "$DIR/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$DIR/ssl/server.crt:/etc/ssl/certs/server.crt:ro" \
  -v "$DIR/ssl/server.key:/etc/ssl/private/server.key:ro" \
  nginx:alpine

echo "==> Done! Access at: https://s51s01v004-vrouter.cisco.com:9999"
docker ps | grep sdm-kanban
