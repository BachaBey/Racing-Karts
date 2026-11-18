"""Client networking: one NetClient per local player (split-screen = 2 NetClients).

* TCP socket + reader thread  -> parsed JSON messages pushed into `inbox` (queue)
* UDP socket + reader thread  -> world snapshots kept in a small time-stamped buffer
* main (pygame) thread         -> drains inbox, sends states, reads snapshots
"""
import collections
import json
import queue
import select
import socket
import threading
import time

from common import config as C
from common import protocol as P


class NetClient:
    def __init__(self, host, tcp_port=C.TCP_PORT, udp_port=C.UDP_PORT):
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.inbox = queue.Queue()
        self.tcp = None
        self.udp = None
        self.alive = False
        self.send_lock = threading.Lock()
        self.token = None
        self.pid = None
        self.user = None
        self.udp_ok = False
        self.state_seq = 0
        self.snapshots = collections.deque(maxlen=40)   # (local_recv_time, snapshot dict)
        self.snap_lock = threading.Lock()
        self.last_snap_seq = -1
        self.last_tcp = time.time()
        # statistics shown in the F3 network overlay
        self.rtt = None
        self.udp_recv = 0
        self.udp_lost = 0
        self.udp_sent = 0
        self.udp_bytes = 0
        self.out_of_order = 0
        self._last_ping = 0.0
        self._last_hello = 0.0
        # what the server observed (shows NAT translation: private ip:port -> public ip:port)
        self.local_tcp = None
        self.local_udp_port = None
        self.seen_tcp = None
        self.seen_udp = None
        # snapshot arrival jitter (EWMA) -> adaptive interpolation delay
        self.jitter = 0.0
        self._last_snap_arrival = None

    # ------------------------------------------------------------- connect
    def connect(self, timeout=5.0):
        self.server_ip = socket.gethostbyname(self.host)
        self.tcp = socket.create_connection((self.server_ip, self.tcp_port), timeout=timeout)
        self.tcp.settimeout(None)
        self.tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("", 0))
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            self.udp.ioctl(socket.SIO_UDP_CONNRESET, False)
        self.local_tcp = "%s:%d" % self.tcp.getsockname()[:2]
        self.local_udp_port = self.udp.getsockname()[1]
        self.alive = True
        self.last_tcp = time.time()
        threading.Thread(target=self._tcp_reader, daemon=True).start()
        threading.Thread(target=self._udp_reader, daemon=True).start()

    def close(self):
        self.alive = False
        for s in (self.tcp, self.udp):
            if s is None:
                continue
            try:
                if s is self.tcp:
                    s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass

    # ------------------------------------------------------------- TCP
    def send(self, typ, **fields):
        if not self.alive:
            return False
        try:
            with self.send_lock:
                self.tcp.sendall(P.encode_tcp(typ, **fields))
            return True
        except OSError:
            self._lost("send failed")
            return False

    def _lost(self, reason):
        if self.alive:
            self.alive = False
            self.inbox.put({"type": "_DISCONNECTED", "reason": reason})

    def _tcp_reader(self):
        framer = P.LineFramer()
        try:
            while self.alive:
                data = self.tcp.recv(8192)
                if not data:
                    self._lost("Server closed the connection")
                    return
                self.last_tcp = time.time()
                for msg in framer.feed(data):
                    t = msg["type"]
                    if t == P.S_PONG:
                        try:
                            self.rtt = time.time() - float(msg.get("t"))
                        except (TypeError, ValueError):
                            pass
                        continue
                    if t == P.S_LOGIN_OK:
                        self.token = msg["token"]
                        self.pid = msg["pid"]
                        self.user = msg["user"]
                        self.seen_tcp = msg.get("seen_tcp")
                        # NOTE: we keep the UDP port we were configured with instead of the one the
                        # server announces: behind a cloud port mapping / tunnel the public port differs.
                    self.inbox.put(msg)
        except (OSError, ValueError):
            self._lost("Connection to server lost")

    def poll(self):
        """Drain received TCP messages (main thread)."""
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def heartbeat(self):
        """Call every frame: TCP ping (keep-alive + RTT), UDP hello until confirmed, server-loss check."""
        now = time.time()
        if not self.alive:
            return
        if now - self._last_ping >= C.HEARTBEAT_INTERVAL:
            self._last_ping = now
            self.send(P.C_PING, t=now)
        # UDP HELLO: every 0.5 s until confirmed, then every UDP_KEEPALIVE seconds as a NAT keepalive
        # (home routers forget idle UDP mappings after ~30-120 s, e.g. while players sit in the lobby).
        hello_every = C.UDP_KEEPALIVE if self.udp_ok else 0.5
        if self.token is not None and now - self._last_hello > hello_every:
            self._last_hello = now
            self._udp_send(P.pack_hello(self.token))
        if now - self.last_tcp > C.SERVER_LOST_SECONDS:
            self._lost(f"No answer from server for {C.SERVER_LOST_SECONDS:.0f}s")

    # ------------------------------------------------------------- UDP
    def _udp_send(self, data):
        try:
            self.udp.sendto(data, (self.server_ip, self.udp_port))
            self.udp_sent += 1
        except OSError:
            pass

    def send_state(self, x, y, angle, speed, flags):
        if self.token is None:
            return
        self.state_seq += 1
        self._udp_send(P.pack_state(self.token, self.state_seq, x, y, angle, speed, flags))

    def _udp_reader(self):
        while self.alive:
            try:
                data, addr = self.udp.recvfrom(4096)
            except OSError:
                if not self.alive:
                    return
                continue
            typ, d = P.unpack_udp(data)
            if typ == P.U_WELCOME:
                self.udp_ok = True
                if d.get("ip"):
                    self.seen_udp = f"{d['ip']}:{d['port']}"
            elif typ == P.U_SNAPSHOT:
                self.udp_ok = True
                self.udp_recv += 1
                self.udp_bytes += len(data)
                now = time.time()
                if self._last_snap_arrival is not None:
                    dev = abs((now - self._last_snap_arrival) - 1.0 / C.SNAPSHOT_HZ)
                    self.jitter += (min(dev, 0.5) - self.jitter) * 0.05
                self._last_snap_arrival = now
                with self.snap_lock:
                    seq = d["seq"]
                    if seq <= self.last_snap_seq:
                        if self.last_snap_seq - seq < 1000:   # reordered/duplicated -> drop
                            self.out_of_order += 1
                            continue
                    elif self.last_snap_seq >= 0 and seq > self.last_snap_seq + 1:
                        self.udp_lost += seq - self.last_snap_seq - 1   # gap = lost datagrams
                    self.last_snap_seq = seq
                    self.snapshots.append((time.time(), d))

    def reset_snapshots(self):
        with self.snap_lock:
            self.snapshots.clear()
            self.last_snap_seq = -1
            self._last_snap_arrival = None

    def get_snapshots(self):
        with self.snap_lock:
            return list(self.snapshots)

    @property
    def interp_delay(self):
        """Render remote karts far enough in the past to always have 2 snapshots to blend,
        even on a jittery internet path (LAN: ~100 ms, bad Wi-Fi/4G: up to 300 ms)."""
        return min(C.INTERP_DELAY_MAX, max(C.INTERP_DELAY, 1.5 / C.SNAPSHOT_HZ + 3.0 * self.jitter))

    @property
    def loss_pct(self):
        total = self.udp_recv + self.udp_lost
        return 100.0 * self.udp_lost / total if total else 0.0


def local_ipv4s():
    """Every IPv4 address of this PC (Wi-Fi, Ethernet, hotspot, virtual adapters...)."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:   # the address of the default route (no packet is actually sent)
        t = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        t.connect(("8.8.8.8", 80))
        ips.add(t.getsockname()[0])
        t.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


def discover_servers(timeout=1.5):
    """LAN discovery, robust version.

    1. A broadcast to 255.255.255.255 leaves through ONE network card only (the one with the
       lowest route metric, often a VirtualBox/VPN adapter instead of the Wi-Fi). So we send
       it from a socket BOUND to each local address -> it leaves through every card.
    2. Many Wi-Fi networks and Windows Firewall drop broadcasts or the replies to them, so we
       also send a direct (unicast) probe to every address of each local /24 subnet. A unicast
       reply matches what we sent, so the firewall lets it in.
    """
    found = {}
    socks = []
    probe = P.DISCOVERY_REQUEST
    port = C.DISCOVERY_PORT

    def open_sock(bind_ip):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            s.ioctl(socket.SIO_UDP_CONNRESET, False)
        s.bind((bind_ip, 0))
        s.setblocking(False)
        socks.append(s)
        return s

    def send(s, target):
        try:
            s.sendto(probe, (target, port))
        except OSError:
            pass

    try:
        main = open_sock("")
        send(main, "127.0.0.1")              # a server on this same PC
        send(main, "255.255.255.255")
        for ip in local_ipv4s():
            try:
                s = open_sock(ip)            # bound -> the broadcast leaves through THIS card
            except OSError:
                continue
            prefix = ip.rsplit(".", 1)[0]
            send(s, "255.255.255.255")
            send(s, prefix + ".255")         # directed broadcast of the /24
            for host in range(1, 255):       # unicast sweep of the /24 (254 tiny datagrams)
                target = f"{prefix}.{host}"
                if target != ip:
                    send(s, target)
        end = time.time() + timeout
        while time.time() < end:
            ready, _, _ = select.select(socks, [], [], 0.1)
            for s in ready:
                try:
                    data, addr = s.recvfrom(1024)
                except OSError:
                    continue
                try:
                    info = json.loads(data.decode())
                except ValueError:
                    continue
                if isinstance(info, dict) and info.get("tk") == 1:
                    info["ip"] = addr[0]
                    found[addr[0]] = info
    finally:
        for s in socks:
            s.close()
    # same server seen on several addresses (e.g. Wi-Fi + VirtualBox): list the LAN one first
    return sorted(found.values(), key=lambda i: (i["ip"].startswith(("127.", "192.168.56.")), i["ip"]))
