#!/bin/sh
set -eu
. /etc/os-release
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl
curl -fsSL "https://packages.microsoft.com/config/${ID}/${VERSION_ID}/packages-microsoft-prod.deb" -o /tmp/ms-repo.deb
dpkg -i /tmp/ms-repo.deb
apt-get update
ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18
rm -f /tmp/ms-repo.deb
rm -rf /var/lib/apt/lists/*
