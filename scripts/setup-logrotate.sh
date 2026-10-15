#!/usr/bin/env bash
# One-time setup on the EC2 server: rotate pm2 logs so they never fill the disk.
# Keeps 7 compressed files of max 10 MB each per log, rotated daily at midnight Dubai time.
set -euo pipefail

pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 10M
pm2 set pm2-logrotate:retain 7
pm2 set pm2-logrotate:compress true
pm2 set pm2-logrotate:rotateInterval '0 0 * * *'
pm2 set pm2-logrotate:TZ Asia/Dubai
pm2 save
echo "✅ pm2-logrotate configured (10M x 7, daily, compressed)"
