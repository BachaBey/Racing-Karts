#!/usr/bin/env bash
# Installs the Turbo Kart Arena server on an Ubuntu/Debian cloud VM as a systemd service.
#
#   sudo bash deploy/setup_server.sh            # default ports TCP 5555 / UDP 5556
#   sudo bash deploy/setup_server.sh 5555 5556
#
# Re-running it updates the code and restarts the service (accounts/ghosts in data/ are kept).
set -euo pipefail

TCP_PORT="${1:-5555}"
UDP_PORT="${2:-5556}"
APP_DIR=/opt/kartarena
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "run with sudo"; exit 1; fi

echo "==> installing python3"
if ! command -v python3 >/dev/null; then
  apt-get update -y && apt-get install -y python3
fi
python3 --version

echo "==> creating service user and copying files to $APP_DIR"
id kart >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin kart
mkdir -p "$APP_DIR/data"
rm -rf "$APP_DIR/common" "$APP_DIR/server"
cp -r "$SRC_DIR/common" "$SRC_DIR/server" "$SRC_DIR/run_server.py" "$APP_DIR/"
chown -R kart:kart "$APP_DIR"

echo "==> installing systemd unit"
sed -e "s/@TCP@/$TCP_PORT/g" -e "s/@UDP@/$UDP_PORT/g" "$SRC_DIR/deploy/kartarena.service" \
    > /etc/systemd/system/kartarena.service

echo "==> opening ports inside the VM firewall"
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow "$TCP_PORT/tcp"
  ufw allow "$UDP_PORT/udp"
fi
# Oracle Cloud Ubuntu images ship iptables rules that REJECT everything except SSH.
if command -v iptables >/dev/null && iptables -S INPUT | grep -q -- "-j REJECT"; then
  iptables -C INPUT -p tcp --dport "$TCP_PORT" -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport "$TCP_PORT" -j ACCEPT
  iptables -C INPUT -p udp --dport "$UDP_PORT" -j ACCEPT 2>/dev/null || iptables -I INPUT -p udp --dport "$UDP_PORT" -j ACCEPT
  command -v netfilter-persistent >/dev/null && netfilter-persistent save || true
fi

echo "==> starting service"
systemctl daemon-reload
systemctl enable kartarena >/dev/null
systemctl restart kartarena
sleep 1
systemctl --no-pager --lines=8 status kartarena || true

cat <<EOF

Done. The server listens on TCP $TCP_PORT and UDP $UDP_PORT.
Remember: the CLOUD firewall (Azure NSG / AWS security group / Oracle security list)
must ALSO allow inbound TCP $TCP_PORT and UDP $UDP_PORT.

  live logs : sudo journalctl -u kartarena -f
  restart   : sudo systemctl restart kartarena
  stop      : sudo systemctl stop kartarena
EOF
