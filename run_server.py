"""Start the Turbo Kart Arena server.

    python run_server.py                 # listen on all interfaces, default ports
    python run_server.py --tcp 5555 --udp 5556 --name "Lab PC"
"""
import argparse
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import config as C  # noqa: E402
from server.server import GameServer  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=f"{C.GAME_NAME} server")
    ap.add_argument("--host", default="0.0.0.0", help="interface to bind (default: all)")
    ap.add_argument("--tcp", type=int, default=C.TCP_PORT, help="TCP port (control channel)")
    ap.add_argument("--udp", type=int, default=C.UDP_PORT, help="UDP port (real-time channel)")
    ap.add_argument("--name", default=None, help="server name shown in LAN discovery")
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"),
                    help="folder for accounts and ghost recordings")
    args = ap.parse_args()
    srv = GameServer(args.host, args.tcp, args.udp, args.data, args.name)
    srv.start()

    def on_term(_sig, _frame):   # systemd / docker stop -> notify clients, then exit cleanly
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.log("stop requested, shutting down")
        srv.shutdown()


if __name__ == "__main__":
    main()
