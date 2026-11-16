"""Internet-conditions emulator: a TCP + UDP forwarding proxy that adds delay, jitter and loss.

Run the server normally, then:
    python tools/netem_proxy.py --server 127.0.0.1 --listen 7555 --delay 60 --jitter 20 --loss 5
and connect clients to  <this-pc>:7555  (UDP goes to 7556).

* TCP chunks are delayed but never dropped or reordered (TCP itself guarantees that,
  so loss on a real network shows up as extra delay = retransmission).
* UDP datagrams are delayed independently (so they can arrive out of order) and
  dropped with probability --loss, exactly the conditions the game must tolerate.
Applied in both directions, so the round-trip time is about 2 x delay.
"""
import argparse
import heapq
import itertools
import os
import random
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import config as C  # noqa: E402
from common import protocol as P  # noqa: E402


class Scheduler:
    """Delivers (callback) at a given time, from one thread."""

    def __init__(self):
        self.heap = []
        self.cv = threading.Condition()
        self.counter = itertools.count()
        threading.Thread(target=self._run, daemon=True).start()

    def at(self, when, fn):
        with self.cv:
            heapq.heappush(self.heap, (when, next(self.counter), fn))
            self.cv.notify()

    def _run(self):
        while True:
            with self.cv:
                while not self.heap or self.heap[0][0] > time.time():
                    self.cv.wait(None if not self.heap else max(0.0, self.heap[0][0] - time.time()))
                _, _, fn = heapq.heappop(self.heap)
            try:
                fn()
            except OSError:
                pass


class Netem:
    def __init__(self, delay, jitter, loss):
        self.delay, self.jitter, self.loss = delay / 1000.0, jitter / 1000.0, loss / 100.0
        self.sched = Scheduler()
        self.stats = {"udp_fwd": 0, "udp_drop": 0, "tcp_chunks": 0}

    def lat(self):
        return max(0.0, self.delay + random.uniform(-self.jitter, self.jitter))


def tcp_proxy(net, listen_port, up_host, up_port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", listen_port))
    srv.listen(16)

    def pump(src, dst, label):
        last = [0.0]   # keep TCP order: delivery times never go backwards

        def deliver(data):
            try:
                dst.sendall(data)
            except OSError:
                pass

        def close():
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        while True:
            try:
                data = src.recv(65536)
            except OSError:
                data = b""
            when = max(time.time() + net.lat(), last[0])
            last[0] = when
            if not data:
                net.sched.at(when, close)
                return
            net.stats["tcp_chunks"] += 1
            net.sched.at(when, lambda d=data: deliver(d))

    while True:
        c, addr = srv.accept()
        try:
            up = socket.create_connection((up_host, up_port), timeout=5)
            up.settimeout(None)
        except OSError as e:
            print(f"[tcp] upstream connect failed: {e}")
            c.close()
            continue
        for s in (c, up):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print(f"[tcp] {addr[0]}:{addr[1]} <-> {up_host}:{up_port}")
        threading.Thread(target=pump, args=(c, up, "c->s"), daemon=True).start()
        threading.Thread(target=pump, args=(up, c, "s->c"), daemon=True).start()


def udp_proxy(net, listen_port, up_host, up_port):
    front = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    front.bind(("0.0.0.0", listen_port))
    for s in (front,):
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            s.ioctl(socket.SIO_UDP_CONNRESET, False)
    backs = {}   # client addr -> upstream socket (one per client, like a NAT mapping)
    lock = threading.Lock()

    def forward(sock, data, addr):
        if random.random() < net.loss:
            net.stats["udp_drop"] += 1
            return
        net.stats["udp_fwd"] += 1
        net.sched.at(time.time() + net.lat(), lambda: sock.sendto(data, addr))

    def back_reader(client, back):
        while True:
            try:
                data, _ = back.recvfrom(4096)
            except OSError:
                continue
            forward(front, data, client)

    while True:
        try:
            data, addr = front.recvfrom(4096)
        except OSError:
            continue
        with lock:
            back = backs.get(addr)
            if back is None:
                back = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                if hasattr(socket, "SIO_UDP_CONNRESET"):
                    back.ioctl(socket.SIO_UDP_CONNRESET, False)
                back.bind(("", 0))
                backs[addr] = back
                print(f"[udp] new mapping {addr[0]}:{addr[1]} -> proxy :{back.getsockname()[1]}")
                threading.Thread(target=back_reader, args=(addr, back), daemon=True).start()
        forward(back, data, (up_host, up_port))


def main():
    ap = argparse.ArgumentParser(description="TCP+UDP proxy with delay/jitter/loss")
    ap.add_argument("--server", default="127.0.0.1", help="real server: host[:tcp[:udp]]")
    ap.add_argument("--listen", type=int, default=7555, help="proxy TCP port (UDP = this + 1)")
    ap.add_argument("--delay", type=float, default=60, help="one-way delay in ms")
    ap.add_argument("--jitter", type=float, default=15, help="+/- random jitter in ms")
    ap.add_argument("--loss", type=float, default=3, help="UDP loss in percent (each direction)")
    args = ap.parse_args()
    host, tcp, udp = P.parse_address(args.server, C.TCP_PORT, C.UDP_PORT)
    host = socket.gethostbyname(host)
    net = Netem(args.delay, args.jitter, args.loss)
    threading.Thread(target=tcp_proxy, args=(net, args.listen, host, tcp), daemon=True).start()
    threading.Thread(target=udp_proxy, args=(net, args.listen + 1, host, udp), daemon=True).start()
    print(f"netem proxy: clients -> :{args.listen}/tcp :{args.listen + 1}/udp  ->  {host} {tcp}/tcp {udp}/udp")
    print(f"             delay {args.delay:.0f}ms +/-{args.jitter:.0f}ms each way, UDP loss {args.loss:.1f}% each way")
    try:
        while True:
            time.sleep(10)
            print(f"[stats] udp forwarded {net.stats['udp_fwd']}  dropped {net.stats['udp_drop']}  "
                  f"tcp chunks {net.stats['tcp_chunks']}", flush=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
