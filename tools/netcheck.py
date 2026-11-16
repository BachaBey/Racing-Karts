"""Network diagnostics against a Turbo Kart Arena server (no login needed).

    python tools/netcheck.py 20.51.33.10            # cloud VM public IP
    python tools/netcheck.py kart.example.com:5555:5556 --count 200

Checks, in order:
  1. DNS resolution
  2. TCP reachability + handshake time, then RTT of PING/PONG over the TCP channel
  3. UDP echo: N datagrams -> loss %, min/avg/max RTT, jitter, reordering
and prints a verdict (e.g. "UDP blocked -> open UDP 5556 in the cloud firewall").
"""
import argparse
import ipaddress
import os
import socket
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import config as C  # noqa: E402
from common import protocol as P  # noqa: E402


def ms(x):
    return f"{x * 1000:.1f} ms"


def main():
    ap = argparse.ArgumentParser(description="Turbo Kart Arena network check")
    ap.add_argument("server", help="host  or  host:tcp  or  host:tcp:udp")
    ap.add_argument("--count", type=int, default=100, help="number of UDP echo packets")
    ap.add_argument("--interval", type=float, default=0.03, help="seconds between UDP packets")
    args = ap.parse_args()
    host, tcp_port, udp_port = P.parse_address(args.server, C.TCP_PORT, C.UDP_PORT)
    print(f"Target: {host}  TCP {tcp_port}  UDP {udp_port}\n")

    # 1. DNS ---------------------------------------------------------------
    t0 = time.perf_counter()
    try:
        ip = socket.gethostbyname(host)
    except OSError as e:
        print(f"[DNS ] FAILED: cannot resolve {host}: {e}")
        return 1
    print(f"[DNS ] {host} -> {ip}  ({ms(time.perf_counter() - t0)})")
    ok_tcp = ok_udp = False

    # 2. TCP ---------------------------------------------------------------
    try:
        t0 = time.perf_counter()
        s = socket.create_connection((ip, tcp_port), timeout=5)
        hs = time.perf_counter() - t0
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        local_ip, local_port = s.getsockname()[:2]
        try:
            a = ipaddress.ip_address(local_ip)
            private = a.is_private and not a.is_loopback
        except ValueError:
            private = False
        print(f"[TCP ] connected in {ms(hs)} (3-way handshake ~ 1 RTT)")
        print(f"       local endpoint {local_ip}:{local_port}"
              + ("  <- private address: you are behind NAT, the server sees your router's public IP" if private else ""))
        framer = P.LineFramer()
        rtts = []
        s.settimeout(3)
        for _ in range(5):
            t = time.time()
            s.sendall(P.encode_tcp(P.C_PING, t=t))
            got = None
            while got is None:
                data = s.recv(4096)
                if not data:
                    raise OSError("server closed the connection")
                for m in framer.feed(data):
                    if m["type"] == P.S_PONG:
                        got = time.time() - float(m["t"])
            rtts.append(got)
        print(f"[TCP ] PING/PONG RTT  min {ms(min(rtts))}  avg {ms(statistics.mean(rtts))}  max {ms(max(rtts))}")
        s.close()
        ok_tcp = True
    except OSError as e:
        print(f"[TCP ] FAILED: {e}")
        print("       -> is the server running? Is TCP port", tcp_port, "open in the cloud firewall / security group?")

    # 3. UDP ---------------------------------------------------------------
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        u.ioctl(socket.SIO_UDP_CONNRESET, False)
    u.bind(("", 0))
    u.settimeout(0.2)
    replies = {}
    order = []
    done = threading.Event()

    def reader():
        while not done.is_set():
            try:
                data, _ = u.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                continue
            now = time.perf_counter()
            typ, d = P.unpack_udp(data)
            if typ == P.U_PONG and d["seq"] not in replies:
                replies[d["seq"]] = now - d["t"]
                order.append(d["seq"])

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    print(f"[UDP ] sending {args.count} echo datagrams ({len(P.pack_ping(0, 0.0))} bytes each) ...")
    for seq in range(args.count):
        u.sendto(P.pack_ping(seq, time.perf_counter()), (ip, udp_port))
        time.sleep(args.interval)
    time.sleep(1.0)
    done.set()
    th.join()
    u.close()
    n = len(replies)
    if n == 0:
        print("[UDP ] FAILED: no echo received (100% loss)")
        print(f"       -> UDP port {udp_port} is blocked: add an inbound UDP rule in the cloud firewall"
              " / security group and the VM's own firewall (ufw / iptables).")
    else:
        ok_udp = True
        vals = [replies[s] for s in sorted(replies)]
        jitter = statistics.mean(abs(a - b) for a, b in zip(vals, vals[1:])) if len(vals) > 1 else 0.0
        reordered = sum(1 for a, b in zip(order, order[1:]) if b < a)
        loss = 100.0 * (args.count - n) / args.count
        print(f"[UDP ] received {n}/{args.count}  loss {loss:.1f}%  reordered {reordered}")
        print(f"[UDP ] RTT  min {ms(min(vals))}  avg {ms(statistics.mean(vals))}  max {ms(max(vals))}"
              f"  jitter {ms(jitter)}")

    # verdict --------------------------------------------------------------
    print()
    if ok_tcp and ok_udp:
        print("VERDICT: OK - both channels work, the game will run over this path.")
    elif ok_tcp:
        print("VERDICT: TCP works but UDP does not - you could log in but karts would not move.")
    else:
        print("VERDICT: server unreachable.")
    return 0 if ok_tcp and ok_udp else 2


if __name__ == "__main__":
    sys.exit(main())
