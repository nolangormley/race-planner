#!/bin/bash
# Installs the service and the CI deploy hook on the server. Run as root after bootstrap.sh, and again after you change a file in this folder.
set -euo pipefail

here=$(dirname "$(readlink -f "$0")")

visudo -cf "$here/sudoers"
install -o root -g root -m 440 "$here/sudoers" /etc/sudoers.d/race-planner-deploy
install -o root -g root -m 755 "$here/receive.sh" /usr/local/bin/race-planner-receive
install -o root -g root -m 644 "$here/race-planner.service" /etc/systemd/system/race-planner.service
systemctl daemon-reload
systemctl enable race-planner

install -o root -g root -m 755 -d /opt/race-planner/.ssh
printf 'command="/usr/local/bin/race-planner-receive",restrict %s\n' "$(cat "$here/ci_deploy_key.pub")" \
  > /opt/race-planner/.ssh/authorized_keys
chown root:root /opt/race-planner/.ssh/authorized_keys
chmod 644 /opt/race-planner/.ssh/authorized_keys

echo "deploy hook installed"
