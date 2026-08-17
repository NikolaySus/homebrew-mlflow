#!/bin/sh
set -eu

retired_dir=/root/retired-services/local-llm-proxy
install -d -m 0700 "$retired_dir"

systemctl stop local-llm-proxy.service 2>/dev/null || true
systemctl disable local-llm-proxy.service 2>/dev/null || true
if [ -f /etc/systemd/system/local-llm-proxy.service ]; then
  mv /etc/systemd/system/local-llm-proxy.service "$retired_dir/"
fi
if [ -f /etc/local-llm-proxy.env ]; then
  mv /etc/local-llm-proxy.env "$retired_dir/"
fi
if [ -d /opt/local-llm ]; then
  mv /opt/local-llm "$retired_dir/"
fi
systemctl daemon-reload

if ! swapon --show=NAME --noheadings | grep -q '^/swapfile$'; then
  if [ ! -f /swapfile ]; then
    fallocate -l 4G /swapfile
    chmod 0600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
fi
if ! grep -q '^/swapfile ' /etc/fstab; then
  printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
fi
printf '%s\n' 'vm.swappiness=10' > /etc/sysctl.d/60-homebrew-mlflow.conf
sysctl --system >/dev/null

install -d -m 0755 /etc/ssh/sshd_config.d
rm -f /etc/ssh/sshd_config.d/60-homebrew-mlflow.conf
printf '%s\n' \
  'PasswordAuthentication no' \
  'KbdInteractiveAuthentication no' \
  'PermitRootLogin prohibit-password' \
  > /etc/ssh/sshd_config.d/00-homebrew-mlflow.conf
sshd -t
systemctl reload ssh

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 2222/tcp
for address in 92.53.116.12 92.53.116.111 92.53.116.119; do
  ufw allow from "$address" to any port 10050 proto tcp
done
ufw --force enable

install -d -m 0755 /opt/homebrew-mlflow
install -d -m 0700 /opt/homebrew-mlflow-secrets

echo "VPS preparation complete"
