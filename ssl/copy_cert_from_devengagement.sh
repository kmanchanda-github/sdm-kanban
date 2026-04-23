#!/bin/bash
# Copy the shared self-signed cert from devengagement-webapp
# Run this once on the server before starting the nginx container

SRC=/Users/kamancha/Projects/webapps/devengagement-webapp/ssl
DST=/Users/kamancha/Projects/webapps/sdm-kanban/ssl

cp "$SRC/server.crt" "$DST/server.crt"
cp "$SRC/server.key" "$DST/server.key"
chmod 644 "$DST/server.crt"
chmod 600 "$DST/server.key"
echo "Certs copied to $DST"
