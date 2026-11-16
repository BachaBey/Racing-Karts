"""Start the Turbo Kart Arena client.

    python run_client.py                       # then type the server IP in the login screen
    python run_client.py --host 100.64.0.5     # pre-fill the server address
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from client.app import App  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Turbo Kart Arena client")
    ap.add_argument("--host", default=None, help="server address to pre-fill")
    ap.add_argument("--fullscreen", action="store_true")
    args = ap.parse_args()
    App(args.host, args.fullscreen).run()


if __name__ == "__main__":
    main()
