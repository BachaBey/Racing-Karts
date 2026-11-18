"""Turbo Kart Arena game server.

Threads
-------
* accept thread      : accepts TCP connections, spawns one reader thread per client
* reader thread (xN) : blocking recv() on that client's TCP socket -> framed JSON -> dispatch
* writer thread (xN) : drains that client's outgoing queue with sendall() so a slow client
                       can never block the game loop or other players
* udp thread         : recvfrom() on the UDP socket: kart states + UDP HELLO
* tick thread        : 30 Hz simulation (items, projectiles, laps) + 20 Hz UDP snapshots
                       + heartbeat timeout detection
* discovery thread   : answers LAN broadcast "who is a server?" probes

One RLock (self.lock) protects all shared game state.
"""
import itertools
import os
import queue
import secrets
import socket
import threading
import time

from common import config as C
from common import protocol as P
from .accounts import Accounts
from .ghosts import GhostStore
from .room import Room


class Session:
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.pid = None
        self.user = None
        self.guest = False
        self.token = None
        self.room = None
        self.udp_addr = None
        self.connected_at = time.time()
        self.last_seen = time.time()
        self.outq = queue.Queue()
        self.alive = True

    @property
    def logged_in(self):
        return self.user is not None


class GameServer:
    def __init__(self, host="0.0.0.0", tcp_port=C.TCP_PORT, udp_port=C.UDP_PORT, data_dir="data", name=None):
        self.host, self.tcp_port, self.udp_port = host, tcp_port, udp_port
        self.name = name or socket.gethostname()
        self.lock = threading.RLock()
        self.sessions = set()
        self.by_pid = {}
        self.by_token = {}
        self.rooms = {}
        self.room_ids = itertools.count(1)
        self.accounts = Accounts(os.path.join(data_dir, "users.json"))
        self.ghosts = GhostStore(os.path.join(data_dir, "ghosts"))
        self.running = True
        self.stats = {"udp_in": 0, "udp_out": 0, "tcp_in": 0, "tcp_out": 0}

    # ------------------------------------------------------------------ util
    def log(self, text):
        print(time.strftime("[%H:%M:%S] ") + text, flush=True)

    def send(self, session, typ, **fields):
        if session.alive:
            session.outq.put(P.encode_tcp(typ, **fields))

    def _alloc_pid(self):
        for pid in range(1, 200):
            if pid not in self.by_pid:
                return pid
        return None

    # ------------------------------------------------------------------ startup
    def start(self):
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp.bind((self.host, self.tcp_port))
        self.tcp.listen(16)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind((self.host, self.udp_port))
        if hasattr(socket, "SIO_UDP_CONNRESET"):   # Windows quirk: don't error on ICMP unreachable
            self.udp.ioctl(socket.SIO_UDP_CONNRESET, False)
        threading.Thread(target=self._accept_loop, daemon=True, name="accept").start()
        threading.Thread(target=self._udp_loop, daemon=True, name="udp").start()
        threading.Thread(target=self._tick_loop, daemon=True, name="tick").start()
        threading.Thread(target=self._discovery_loop, daemon=True, name="discovery").start()
        self.log(f"{C.GAME_NAME} server '{self.name}' listening  TCP {self.tcp_port}  UDP {self.udp_port}  "
                 f"discovery UDP {C.DISCOVERY_PORT}")
        for ip in local_ips():
            self.log(f"  local address: {ip}")
        self.log("  (on a cloud VM, clients must use the VM's PUBLIC IP / DNS name instead)")

    def shutdown(self):
        self.running = False
        with self.lock:
            for s in list(self.sessions):
                self.send(s, P.S_SHUTDOWN, msg="Server is shutting down")
        time.sleep(0.3)
        for sock in (self.tcp, self.udp):
            try:
                sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ TCP
    def _accept_loop(self):
        while self.running:
            try:
                sock, addr = self.tcp.accept()
            except OSError:
                break
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # small messages: no Nagle delay
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            with self.lock:
                same_ip = sum(1 for x in self.sessions if x.addr[0] == addr[0])
                refuse = None
                if len(self.sessions) >= C.MAX_SESSIONS:
                    refuse = "Server is full"
                elif same_ip >= C.MAX_CONNECTIONS_PER_IP:
                    refuse = "Too many connections from your address"
                if refuse is None:
                    s = Session(sock, addr)
                    self.sessions.add(s)
            if refuse:
                self.log(f"Refused TCP connection from {addr[0]}: {refuse}")
                try:
                    sock.sendall(P.encode_tcp(P.S_ERROR, msg=refuse, ctx="CONNECT"))
                    sock.close()
                except OSError:
                    pass
                continue
            self.log(f"TCP connection from {addr[0]}:{addr[1]}")
            threading.Thread(target=self._reader, args=(s,), daemon=True).start()
            threading.Thread(target=self._writer, args=(s,), daemon=True).start()

    def _writer(self, s):
        while s.alive:
            try:
                data = s.outq.get(timeout=0.5)
            except queue.Empty:
                continue
            if data is None:
                break
            try:
                s.sock.sendall(data)
                self.stats["tcp_out"] += 1
            except OSError:
                self.disconnect(s, "send failed")
                break

    def _reader(self, s):
        framer = P.LineFramer()
        reason = "connection closed"
        try:
            while s.alive:
                data = s.sock.recv(4096)
                if not data:           # orderly FIN from peer (client closed the game)
                    reason = "closed connection"
                    break
                s.last_seen = time.time()
                for msg in framer.feed(data):
                    self.stats["tcp_in"] += 1
                    with self.lock:
                        try:
                            self.dispatch(s, msg)
                        except Exception as e:  # never let a bad message kill the server
                            self.log(f"error handling {msg.get('type')} from {s.user}: {e!r}")
                            self.send(s, P.S_ERROR, msg="Server error handling request", ctx=msg.get("type"))
        except (ConnectionResetError, ConnectionAbortedError):
            reason = "connection reset (crash / network loss)"
        except (OSError, ValueError) as e:
            reason = f"socket error {e.__class__.__name__}"
        self.disconnect(s, reason)

    def disconnect(self, s, reason):
        with self.lock:
            if not s.alive:
                return
            s.alive = False
            self.sessions.discard(s)
            if s.room is not None:
                room = s.room
                room.remove(s, reason="left" if reason == "logout" else "disconnected")
                if not room.members:
                    self.rooms.pop(room.id, None)
                self.lobby_changed()
            if s.pid is not None:
                self.by_pid.pop(s.pid, None)
            if s.token is not None:
                self.by_token.pop(s.token, None)
        s.outq.put(None)
        try:
            s.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            s.sock.close()
        except OSError:
            pass
        self.log(f"Player {s.user or s.addr[0]} disconnected: {reason}")

    # ------------------------------------------------------------------ dispatch
    def dispatch(self, s, msg):
        t = msg["type"]
        if t == P.C_PING:
            self.send(s, P.S_PONG, t=msg.get("t"))
            return
        if t == P.C_REGISTER:
            user, pw = str(msg.get("user", "")).strip(), str(msg.get("pw", ""))
            ok, text = self.accounts.register(user, pw)
            if ok:
                self.log(f"Registered new player {user}")
                self.send(s, P.S_REGISTER_OK, user=user)
            else:
                self.send(s, P.S_ERROR, msg=text, ctx=t)
            return
        if t == P.C_LOGIN:
            self._login(s, msg)
            return
        if not s.logged_in:
            self.send(s, P.S_ERROR, msg="Please log in first", ctx=t)
            return
        if t == P.C_LOGOUT:
            threading.Thread(target=self.disconnect, args=(s, "logout"), daemon=True).start()
        elif t == P.C_LIST_ROOMS:
            self.send(s, P.S_ROOM_LIST, rooms=[r.summary() for r in self.rooms.values()], online=len(self.by_pid))
        elif t == P.C_LEADERBOARD:
            self.send(s, P.S_LEADERBOARD, rows=self.accounts.leaderboard())
        elif t == P.C_CREATE_ROOM:
            if s.room:
                self.send(s, P.S_ERROR, msg="Leave your current room first", ctx=t)
                return
            rid = next(self.room_ids)
            room = Room(self, rid, str(msg.get("name", ""))[:24] or f"{s.user}'s room", msg.get("track"),
                        msg.get("laps"), msg.get("ghosts"))
            self.rooms[rid] = room
            room.add(s)
            self.log(f"{s.user} created room {rid} '{room.name}'")
            self.lobby_changed()
        elif t == P.C_JOIN_ROOM:
            room = self.rooms.get(msg.get("room_id"))
            if s.room:
                self.send(s, P.S_ERROR, msg="Already in a room", ctx=t)
            elif room is None:
                self.send(s, P.S_ERROR, msg="Room no longer exists", ctx=t)
            else:
                err = room.add(s)
                if err:
                    self.send(s, P.S_ERROR, msg=err, ctx=t)
                else:
                    self.log(f"{s.user} joined room {room.id}")
                    self.lobby_changed()
        elif t == P.C_LEAVE_ROOM:
            room = s.room
            if room:
                room.remove(s, reason="left")
                if not room.members:
                    self.rooms.pop(room.id, None)
                self.send(s, P.S_LEFT_ROOM)
                self.log(f"{s.user} left room {room.id}")
                self.lobby_changed()
        elif t == P.C_SET_READY:
            if s.room:
                s.room.set_ready(s, msg.get("ready", True))
        elif t == P.C_ROOM_SETTINGS:
            if s.room:
                err = s.room.settings(s, msg.get("track"), msg.get("laps"), msg.get("ghosts"))
                if err:
                    self.send(s, P.S_ERROR, msg=err, ctx=t)
                else:
                    self.lobby_changed()
        elif t == P.C_START_GAME:
            if s.room:
                err = s.room.start(s)
                if err:
                    self.send(s, P.S_ERROR, msg=err, ctx=t)
        elif t == P.C_USE_ITEM:
            if s.room:
                s.room.use_item(s)
        else:
            self.send(s, P.S_ERROR, msg=f"Unknown message {t}", ctx=t)

    def _login(self, s, msg):
        if s.logged_in:
            self.send(s, P.S_ERROR, msg="Already logged in", ctx=P.C_LOGIN)
            return
        user, pw = str(msg.get("user", "")).strip(), str(msg.get("pw", ""))
        guest = bool(msg.get("guest"))
        if guest:
            base = "".join(ch for ch in user if ch.isalnum() or ch == "_")[:12] or "Guest"
            user = base
            n = 1
            taken = {x.user.lower() for x in self.by_pid.values()} | {u.lower() for u in self.accounts.users}
            while user.lower() in taken:
                n += 1
                user = f"{base}{n}"
        else:
            ok, text = self.accounts.check(user, pw)
            if not ok:
                self.send(s, P.S_ERROR, msg=text, ctx=P.C_LOGIN)
                return
            if any(x.user == user for x in self.by_pid.values()):
                self.send(s, P.S_ERROR, msg=f"{user} is already logged in", ctx=P.C_LOGIN)
                return
        pid = self._alloc_pid()
        if pid is None:
            self.send(s, P.S_ERROR, msg="Server full", ctx=P.C_LOGIN)
            return
        s.user, s.guest, s.pid = user, guest, pid
        s.token = secrets.randbits(32)
        while s.token in self.by_token:
            s.token = secrets.randbits(32)
        self.by_pid[pid] = s
        self.by_token[s.token] = s
        stats = self.accounts.stats(user) if not guest else {"races": 0, "wins": 0, "points": 0}
        self.send(s, P.S_LOGIN_OK, pid=pid, token=s.token, user=user, guest=guest, stats=stats,
                  udp_port=self.udp_port, server=self.name, seen_tcp=f"{s.addr[0]}:{s.addr[1]}")
        self.send(s, P.S_ROOM_LIST, rooms=[r.summary() for r in self.rooms.values()], online=len(self.by_pid))
        self.log(f"{user} logged in{' (guest)' if guest else ''} from {s.addr[0]}  pid={pid}")

    def lobby_changed(self):
        rooms = [r.summary() for r in self.rooms.values()]
        for s in list(self.by_pid.values()):
            if s.room is None:
                self.send(s, P.S_ROOM_LIST, rooms=rooms, online=len(self.by_pid))

    # ------------------------------------------------------------------ UDP
    def _udp_loop(self):
        while self.running:
            try:
                data, addr = self.udp.recvfrom(2048)
            except ConnectionResetError:
                continue   # Windows: ICMP port unreachable from a closed client, ignore
            except OSError:
                if not self.running:
                    break
                continue
            typ, d = P.unpack_udp(data)
            if typ is None:
                continue
            self.stats["udp_in"] += 1
            if typ == P.U_PING:
                # stateless echo for tools/netcheck.py: same size as the request, so it cannot amplify traffic
                self._udp_send(P.pack_ping(d["seq"], d["t"], pong=True), addr)
                continue
            with self.lock:
                s = self.by_token.get(d.get("token"))
                if s is None or not s.alive:
                    continue
                s.last_seen = time.time()
                if s.udp_addr != addr:
                    # Learn / update the client's PUBLIC UDP endpoint. Behind NAT this is the router's
                    # address and port, not the PC's private IP. If the NAT re-maps the port, we follow it.
                    if s.udp_addr is None:
                        self.log(f"{s.user}: UDP endpoint {addr[0]}:{addr[1]}  (TCP from {s.addr[0]}:{s.addr[1]})")
                    else:
                        self.log(f"{s.user}: UDP endpoint changed {s.udp_addr[0]}:{s.udp_addr[1]} -> "
                                 f"{addr[0]}:{addr[1]} (NAT rebinding)")
                    s.udp_addr = addr
                if typ == P.U_HELLO:
                    self._udp_send(P.pack_welcome(addr[0], addr[1]), addr)
                elif typ == P.U_STATE and s.room is not None:
                    s.room.on_state(s, d)

    def _udp_send(self, data, addr):
        try:
            self.udp.sendto(data, addr)
            self.stats["udp_out"] += 1
        except OSError:
            pass

    # ------------------------------------------------------------------ tick
    def _tick_loop(self):
        dt = 1.0 / C.SERVER_TICK_HZ
        snap_every = 1.0 / C.SNAPSHOT_HZ
        last = time.time()
        next_snap = last
        next_report = last + 30
        while self.running:
            now = time.time()
            elapsed = now - last
            last = now
            timed_out = []
            with self.lock:
                for room in list(self.rooms.values()):
                    room.tick(elapsed)
                if now >= next_snap:
                    next_snap = now + snap_every
                    for room in list(self.rooms.values()):
                        pkt = room.snapshot()
                        if pkt:
                            for s in room.members.values():
                                if s.udp_addr:
                                    self._udp_send(pkt, s.udp_addr)
                for s in list(self.sessions):
                    if now - s.last_seen > C.TIMEOUT_SECONDS:
                        timed_out.append((s, f"heartbeat timeout ({C.TIMEOUT_SECONDS:.0f}s silent)"))
                    elif not s.logged_in and now - s.connected_at > C.LOGIN_DEADLINE:
                        timed_out.append((s, "did not log in"))
            for s, why in timed_out:
                self.disconnect(s, why)
            if now >= next_report:
                next_report = now + 30
                with self.lock:
                    self.log(f"status: {len(self.by_pid)} players online, {len(self.rooms)} rooms, "
                             f"udp in/out {self.stats['udp_in']}/{self.stats['udp_out']}")
            time.sleep(max(0.0, dt - (time.time() - now)))

    # ------------------------------------------------------------------ discovery
    def _discovery_loop(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", C.DISCOVERY_PORT))
        except OSError as e:
            self.log(f"LAN discovery disabled: {e}")
            return
        seen_probe = {}
        while self.running:
            try:
                data, addr = sock.recvfrom(256)
            except OSError:
                continue
            if data == P.DISCOVERY_REQUEST:
                now = time.time()
                if now - seen_probe.get(addr[0], 0) > 30:     # log each source at most every 30 s
                    seen_probe[addr[0]] = now
                    self.log(f"LAN discovery probe from {addr[0]} -> answering")
                with self.lock:
                    online = len(self.by_pid)
                try:
                    sock.sendto(P.discovery_reply(self.name, self.tcp_port, self.udp_port, online), addr)
                except OSError:
                    pass


def local_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # no packet is sent; just picks the default route's interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips) or ["127.0.0.1"]
