"""Wire protocol shared by server and client.

TCP  (reliable, ordered byte STREAM)  -> newline-delimited JSON messages.
     TCP has no message boundaries, so we frame each message with '\n'
     and reassemble partial reads in a buffer (see LineFramer).

UDP  (unreliable DATAGRAMS)          -> fixed binary layouts packed with `struct`.
     Each datagram is self-contained, carries a sequence number so stale /
     reordered packets can be dropped, and stays far below the ~1200 byte MTU-safe size.
"""
import json
import socket
import struct

# --------------------------------------------------------------------------- TCP
# Client -> Server
C_REGISTER = "REGISTER"        # {user, pw}
C_LOGIN = "LOGIN"              # {user, pw}
C_LOGOUT = "LOGOUT"
C_LIST_ROOMS = "LIST_ROOMS"
C_CREATE_ROOM = "CREATE_ROOM"  # {name, track, laps, ghosts}
C_JOIN_ROOM = "JOIN_ROOM"      # {room_id}
C_LEAVE_ROOM = "LEAVE_ROOM"
C_SET_READY = "SET_READY"      # {ready}
C_ROOM_SETTINGS = "ROOM_SETTINGS"  # {track, laps, ghosts}  (host only)
C_START_GAME = "START_GAME"    # (host only)
C_USE_ITEM = "USE_ITEM"
C_LEADERBOARD = "LEADERBOARD"
C_PING = "PING"                # {t}

# Server -> Client
S_ERROR = "ERROR"              # {msg, ctx}
S_REGISTER_OK = "REGISTER_OK"
S_LOGIN_OK = "LOGIN_OK"        # {pid, token, user, stats, udp_port}
S_ROOM_LIST = "ROOM_LIST"      # {rooms:[...], online}
S_ROOM_STATE = "ROOM_STATE"    # {room:{...}}
S_LEFT_ROOM = "LEFT_ROOM"
S_RACE_START = "RACE_START"    # {track, laps, countdown, grid:[...]}
S_GO = "GO"
S_ITEM_GOT = "ITEM_GOT"        # {item, count}
S_EFFECT = "EFFECT"            # {kind}
S_EVENT = "EVENT"              # {text}
S_FINISHED = "FINISHED"        # {pid, name, rank, time}
S_PLAYER_LEFT = "PLAYER_LEFT"  # {pid, name, reason}
S_RACE_RESULTS = "RACE_RESULTS"  # {results:[...]}
S_LEADERBOARD = "LEADERBOARD"  # {rows:[...]}
S_PONG = "PONG"                # {t}
S_SHUTDOWN = "SHUTDOWN"


def encode_tcp(msg_type, **fields):
    fields["type"] = msg_type
    return (json.dumps(fields, separators=(",", ":")) + "\n").encode("utf-8")


class LineFramer:
    """Re-assembles newline-framed JSON messages from an arbitrary TCP byte stream."""

    MAX_BUFFER = 64 * 1024

    def __init__(self):
        self.buf = b""

    def feed(self, data):
        self.buf += data
        if len(self.buf) > self.MAX_BUFFER and b"\n" not in self.buf:
            raise ValueError("TCP frame too large")
        messages = []
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue  # ignore garbage line, keep connection alive
            if isinstance(msg, dict) and "type" in msg:
                messages.append(msg)
        return messages


# --------------------------------------------------------------------------- UDP
MAGIC = b"TK"
U_HELLO = 1      # client -> server : bind this UDP address to my session token (also NAT keepalive)
U_WELCOME = 2    # server -> client : UDP path confirmed + the public ip:port the server observed
U_STATE = 3      # client -> server : my kart state
U_SNAPSHOT = 4   # server -> client : world snapshot of the room
U_PING = 5       # anyone -> server : UDP echo request (network diagnostics, no login needed)
U_PONG = 6       # server -> sender : UDP echo reply (same seq/timestamp)

_HDR = struct.Struct("!2sBB")                 # magic, version, type
_HELLO = struct.Struct("!2sBBI")              # + token
_WELCOME = struct.Struct("!2sBB4sH")          # + observed IPv4, observed port
_PING = struct.Struct("!2sBBId")              # + seq, sender timestamp
_STATE = struct.Struct("!2sBBIIffffB")        # + token, seq, x, y, angle, speed, flags
_SNAP_HEAD = struct.Struct("!2sBBIfIBB")      # + seq, race_time, boxes_bitmask, n_karts, n_objs
_SNAP_KART = struct.Struct("!BffffhBB")       # pid, x, y, angle, speed, lap, rank, flags
_SNAP_OBJ = struct.Struct("!BHff")            # kind, obj_id, x, y

VERSION = 1

# kart flags (bitmask)
F_BOOST = 1
F_SPIN = 2
F_SHIELD = 4
F_GHOST = 8
F_FINISHED = 16
F_DISCONNECTED = 32

# world object kinds
OBJ_BANANA = 1
OBJ_SHELL = 2
OBJ_HOMING = 3


def pack_hello(token):
    return _HELLO.pack(MAGIC, VERSION, U_HELLO, token)


def pack_welcome(ip="0.0.0.0", port=0):
    try:
        raw = socket.inet_aton(ip)
    except OSError:
        raw = b"\0\0\0\0"
    return _WELCOME.pack(MAGIC, VERSION, U_WELCOME, raw, port)


def pack_ping(seq, t, pong=False):
    return _PING.pack(MAGIC, VERSION, U_PONG if pong else U_PING, seq & 0xFFFFFFFF, t)


def pack_state(token, seq, x, y, angle, speed, flags):
    return _STATE.pack(MAGIC, VERSION, U_STATE, token, seq & 0xFFFFFFFF, x, y, angle, speed, flags)


def pack_snapshot(seq, race_time, boxes, karts, objs):
    """karts: list of (pid, x, y, angle, speed, lap, rank, flags); objs: list of (kind, id, x, y)."""
    parts = [_SNAP_HEAD.pack(MAGIC, VERSION, U_SNAPSHOT, seq & 0xFFFFFFFF, race_time,
                             boxes & 0xFFFFFFFF, len(karts), len(objs))]
    for k in karts:
        parts.append(_SNAP_KART.pack(*k))
    for o in objs:
        parts.append(_SNAP_OBJ.pack(*o))
    return b"".join(parts)


def unpack_udp(data):
    """Returns (type, payload dict) or (None, None) for invalid datagrams."""
    if len(data) < _HDR.size:
        return None, None
    magic, ver, typ = _HDR.unpack_from(data)
    if magic != MAGIC or ver != VERSION:
        return None, None
    try:
        if typ == U_HELLO:
            _, _, _, token = _HELLO.unpack_from(data)
            return typ, {"token": token}
        if typ == U_WELCOME:
            if len(data) >= _WELCOME.size:
                _, _, _, raw, port = _WELCOME.unpack_from(data)
                return typ, {"ip": socket.inet_ntoa(raw), "port": port}
            return typ, {"ip": None, "port": None}
        if typ in (U_PING, U_PONG):
            _, _, _, seq, t = _PING.unpack_from(data)
            return typ, {"seq": seq, "t": t}
        if typ == U_STATE:
            _, _, _, token, seq, x, y, a, spd, flags = _STATE.unpack_from(data)
            return typ, {"token": token, "seq": seq, "x": x, "y": y, "a": a, "speed": spd, "flags": flags}
        if typ == U_SNAPSHOT:
            _, _, _, seq, rt, boxes, nk, no = _SNAP_HEAD.unpack_from(data)
            off = _SNAP_HEAD.size
            karts = []
            for _ in range(nk):
                pid, x, y, a, spd, lap, rank, flags = _SNAP_KART.unpack_from(data, off)
                off += _SNAP_KART.size
                karts.append({"pid": pid, "x": x, "y": y, "a": a, "speed": spd,
                              "lap": lap, "rank": rank, "flags": flags})
            objs = []
            for _ in range(no):
                kind, oid, x, y = _SNAP_OBJ.unpack_from(data, off)
                off += _SNAP_OBJ.size
                objs.append({"kind": kind, "id": oid, "x": x, "y": y})
            return typ, {"seq": seq, "time": rt, "boxes": boxes, "karts": karts, "objs": objs}
    except struct.error:
        pass
    return None, None


def parse_address(text, default_tcp, default_udp=None):
    """'host', 'host:tcp' or 'host:tcp:udp' -> (host, tcp_port, udp_port).
    Lets clients reach servers behind tunnels / cloud port mappings where the
    public ports differ from the defaults. UDP defaults to TCP port + 1."""
    parts = text.strip().split(":")
    host = parts[0].strip()
    tcp = int(parts[1]) if len(parts) > 1 and parts[1].strip() else default_tcp
    if len(parts) > 2 and parts[2].strip():
        udp = int(parts[2])
    elif default_udp is not None and tcp == default_tcp:
        udp = default_udp
    else:
        udp = tcp + 1
    return host, tcp, udp


# --------------------------------------------------------------------------- discovery
DISCOVERY_REQUEST = b"TK_DISCOVER?"


def discovery_reply(name, tcp_port, udp_port, online):
    return json.dumps({"tk": 1, "name": name, "tcp": tcp_port, "udp": udp_port, "online": online}).encode()
