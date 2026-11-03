#!/usr/bin/env bash
# Builds a clean Ubuntu 24.04 image with systemd from archive.ubuntu.com.
set -euo pipefail
IMAGE="${1:-ccvps-noble}"
docker image inspect "$IMAGE" >/dev/null 2>&1 && exit 0
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
debootstrap --variant=minbase --include=systemd,systemd-sysv,dbus,apt noble "$work/rootfs" \
  http://archive.ubuntu.com/ubuntu >/dev/null
cat >"$work/rootfs/etc/apt/sources.list" <<'SRC'
deb http://archive.ubuntu.com/ubuntu noble main universe
deb http://archive.ubuntu.com/ubuntu noble-updates main universe
deb http://archive.ubuntu.com/ubuntu noble-security main universe
SRC
tar -C "$work/rootfs" -c . | docker import - "$IMAGE" >/dev/null
