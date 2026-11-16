"""Menu scenes: Connect/Login, Lobby, Room."""
import threading

import pygame

from client import ui
from client.net import NetClient, discover_servers
from client.ui import (ACCENT, ACCENT2, BAD, GOOD, MUTED, PLAYER_COLORS, TEXT, Button, Cycle, ListView,
                       Scene, TextInput, Toggle)
from common import config as C
from common import protocol as P
from common.track import TRACK_ORDER, TRACKS, Track

TRACK_OPTS = [(k, TRACKS[k]["title"]) for k in TRACK_ORDER]
LAP_OPTS = [(n, str(n)) for n in range(1, 6)]
GHOST_OPTS = [(n, "none" if n == 0 else str(n)) for n in range(0, C.MAX_GHOSTS + 1)]


def title(surf, s, sub=None):
    ui.text(surf, s, (surf.get_width() // 2, 52), 54, ACCENT, bold=True, center=True, shadow=True)
    if sub:
        ui.text(surf, sub, (surf.get_width() // 2, 98), 20, MUTED, center=True)


def status_bar(surf, app):
    """Bottom-left connection info (ping + UDP status per local player)."""
    x = 18
    for lp in app.locals:
        n = lp.net
        rtt = f"{n.rtt * 1000:.0f} ms" if n.rtt is not None else "..."
        udp = "UDP ok" if n.udp_ok else "UDP waiting"
        col = GOOD if n.udp_ok else ACCENT
        r = ui.text(surf, f"P{lp.slot + 1} {lp.user}  ping {rtt}  {udp}", (x, surf.get_height() - 30), 16, col)
        x = r.right + 30
    if app.locals:
        n = app.locals[0].net
        if n.seen_udp:
            ui.text(surf, f"server {n.server_ip}  |  you are {n.local_tcp.split(':')[0]}, server sees {n.seen_udp}",
                    (surf.get_width() - 18, surf.get_height() - 30), 16, MUTED, right=True)


_preview_cache = {}


def draw_track_preview(surf, rect, key):
    ck = (key, rect.size)
    if ck not in _preview_cache:
        t = Track(key)
        img = pygame.Surface(rect.size, pygame.SRCALPHA)
        sc = min(rect.w, rect.h) / 2048 * 0.95
        ox = (rect.w - 2048 * sc) / 2
        oy = (rect.h - 2048 * sc) / 2
        pts = [(ox + x * sc, oy + y * sc) for x, y in t.pts]
        for col, extra in (((20, 20, 30), 3), ((200, 200, 215), 0)):
            r = t.half * sc + extra
            for p in pts:
                pygame.draw.circle(img, col, (int(p[0]), int(p[1])), int(r))
        fx, fy, _ = t.point_at(0)
        pygame.draw.circle(img, ACCENT, (int(ox + fx * sc), int(oy + fy * sc)), 7)
        ui.text(img, TRACKS[key]["title"], (rect.w // 2, rect.h - 14), 18, MUTED, center=True)
        _preview_cache[ck] = img
    surf.blit(_preview_cache[ck], rect.topleft)


# =========================================================================== CONNECT
class ConnectScene(Scene):
    def __init__(self, app, error=None):
        super().__init__(app)
        st = app.settings
        W = 1280
        self.host = self.add(TextInput((80, 210, 340, 46), "IP / DNS name [:tcp[:udp]]", value=st.get("host", "127.0.0.1"), max_len=60))
        self.add(Button((80, 268, 160, 44), "Scan LAN", self.scan, ACCENT2, 20))
        self.servers = self.add(ListView((80, 326, 340, 210), [("Server", 190), ("Players", 80)],
                                         on_select=self.pick_server, row_h=32))
        self.u1 = self.add(TextInput((480, 210, 330, 46), "username", value=st.get("user1", "")))
        self.p1 = self.add(TextInput((480, 268, 330, 46), "password (empty = guest)", password=True))
        self.p2_on = self.add(Toggle((880, 160, 320, 40), "Add local Player 2", st.get("p2", False)))
        self.u2 = self.add(TextInput((880, 210, 330, 46), "P2 username", value=st.get("user2", "")))
        self.p2 = self.add(TextInput((880, 268, 330, 46), "P2 password (empty = guest)", password=True))
        self.login_btn = self.add(Button((W // 2 - 270, 600, 250, 60), "LOGIN", lambda: self.go(False)))
        self.reg_btn = self.add(Button((W // 2 + 20, 600, 250, 60), "REGISTER", lambda: self.go(True), ACCENT2))
        self.add(Button((W - 150, 30, 120, 44), "Quit", app.quit, (120, 60, 80), 20))
        for w in (self.u1, self.p1, self.u2, self.p2, self.host):
            w.on_enter = lambda: self.go(False)
        self.u1.focused = not self.u1.value
        self.p1.focused = bool(self.u1.value)
        self.busy = False
        self.pending = []
        self.result = None
        if error:
            self.flash(error, BAD, 8)

    def scan(self):
        self.flash("Scanning LAN for servers...", ACCENT2, 2)

        def work():
            self.result = ("scan", discover_servers())
        threading.Thread(target=work, daemon=True).start()

    def pick_server(self, key):
        self.host.value = key

    def go(self, register):
        if self.busy:
            return
        host = self.host.value.strip()
        players = [(self.u1.value.strip(), self.p1.value)]
        if self.p2_on.value:
            players.append((self.u2.value.strip(), self.p2.value))
        for u, pw in players:
            if not u:
                self.flash("Enter a username for every player")
                return
            if register and not pw:
                self.flash("Registration needs a password (leave it empty only to play as guest)")
                return
        if len(players) == 2 and players[0][0].lower() == players[1][0].lower():
            self.flash("Player 1 and Player 2 need different names")
            return
        if not host:
            self.flash("Enter the server address")
            return
        self.app.settings.update(host=host, user1=players[0][0], user2=self.u2.value.strip(), p2=self.p2_on.value)
        self.app.save_settings()
        self.busy = True
        self.flash("Connecting to " + host + " ...", ACCENT2, 10)

        def work():
            nets = []
            try:
                h, tcp, udp = P.parse_address(host, C.TCP_PORT, C.UDP_PORT)
                for _ in players:
                    n = NetClient(h, tcp, udp)
                    n.connect()
                    nets.append(n)
                self.result = ("connected", nets, players, register)
            except ValueError:
                self.result = ("fail", "Address format: host  or  host:tcpport  or  host:tcpport:udpport")
            except OSError as e:
                for n in nets:
                    n.close()
                self.result = ("fail", f"Cannot reach server {host}: {e.strerror or e}")
        threading.Thread(target=work, daemon=True).start()

    def update(self, dt):
        if self.result is None:
            return
        res, self.result = self.result, None
        if res[0] == "scan":
            rows = [(s["ip"], [f"{s['name']} ({s['ip']})", str(s["online"])], None) for s in res[1]]
            self.servers.set_rows(rows)
            self.flash(f"Found {len(rows)} server(s)" if rows else "No server answered on this LAN",
                       GOOD if rows else ACCENT, 3)
            if len(rows) == 1:
                self.host.value = rows[0][0]
        elif res[0] == "fail":
            self.busy = False
            self.flash(res[1], BAD, 6)
        elif res[0] == "connected":
            _, nets, players, register = res
            self.app.locals = []
            for slot, (net, (u, pw)) in enumerate(zip(nets, players)):
                lp = self.app.add_local(slot, net)
                lp.pending_pw = pw
                lp.pending_user = u
                if register and pw:
                    net.send(P.C_REGISTER, user=u, pw=pw)
                else:
                    self._login(lp)

    def _login(self, lp):
        if lp.pending_pw:
            lp.net.send(P.C_LOGIN, user=lp.pending_user, pw=lp.pending_pw)
        else:
            lp.net.send(P.C_LOGIN, user=lp.pending_user, guest=True)

    def on_message(self, lp, msg):
        t = msg["type"]
        if t == P.S_REGISTER_OK:
            self.flash(f"Account {msg['user']} created", GOOD)
            self._login(lp)
        elif t == P.S_LOGIN_OK:
            lp.user = msg["user"]
            lp.pid = msg["pid"]
            lp.stats = msg.get("stats", {})
            if all(x.pid is not None for x in self.app.locals):
                self.busy = False
                self.app.go(LobbyScene(self.app))
        elif t == P.S_ERROR:
            self.flash(f"P{lp.slot + 1}: {msg['msg']}", BAD, 6)
            self.app.disconnect_all()
            self.busy = False

    def draw(self, surf):
        ui.background(surf)
        title(surf, C.GAME_NAME.upper(), "Online multiplayer kart racing  -  TCP + UDP sockets")
        ui.panel(surf, pygame.Rect(60, 150, 380, 410), "1. Server")
        ui.panel(surf, pygame.Rect(460, 150, 370, 410), "2. Player 1")
        ui.panel(surf, pygame.Rect(860, 150, 370, 410), None)
        ui.text(surf, "Split-screen: 2 players on this PC", (880, 330), 16, MUTED)
        ui.text(surf, "P1: WASD + Space    P2: Arrows + Enter", (880, 352), 16, MUTED)
        ui.text(surf, "Leave the password empty to play", (480, 330), 16, MUTED)
        ui.text(surf, "as a guest (stats are not saved).", (480, 352), 16, MUTED)
        ui.text(surf, "Register once, then log in with", (480, 390), 16, MUTED)
        ui.text(surf, "the same name on any PC.", (480, 412), 16, MUTED)
        self.u2.enabled = self.p2.enabled = self.p2_on.value
        self.draw_widgets(surf)


# =========================================================================== LOBBY
class LobbyScene(Scene):
    def __init__(self, app):
        super().__init__(app)
        self.rooms = self.add(ListView((40, 150, 760, 420),
                                       [("Room", 260), ("Track", 170), ("Players", 100), ("Laps", 70), ("Status", 140)],
                                       on_activate=lambda k: self.join(), row_h=36))
        self.add(Button((40, 590, 200, 54), "JOIN", self.join))
        self.add(Button((260, 590, 200, 54), "Refresh", self.refresh, ACCENT2))
        self.add(Button((600, 590, 200, 54), "Logout", self.app.logout, (120, 60, 80)))
        primary = app.locals[0].user
        self.name = self.add(TextInput((850, 190, 390, 44), "room name", value=f"{primary}'s room"))
        self.track = self.add(Cycle((850, 244, 390, 44), "Track", TRACK_OPTS))
        self.laps = self.add(Cycle((850, 298, 390, 44), "Laps", LAP_OPTS, index=2))
        self.ghosts = self.add(Cycle((850, 352, 390, 44), "Ghost opponents", GHOST_OPTS, index=0))
        self.add(Button((850, 408, 390, 50), "CREATE ROOM", self.create))
        self.leader = self.add(ListView((850, 498, 390, 160), [("#", 36), ("Player", 180), ("Pts", 70), ("Wins", 60)],
                                        row_h=26))
        self.refresh()
        self.apply_rooms()

    def refresh(self):
        self.app.primary.send(P.C_LIST_ROOMS)
        self.app.primary.send(P.C_LEADERBOARD)

    def apply_rooms(self):
        rows = []
        for r in self.app.rooms:
            col = GOOD if r["status"] == "WAITING" else MUTED
            rows.append((r["id"], [r["name"], TRACKS.get(r["track"], {}).get("title", r["track"]),
                                   f"{r['players']}/{r['max']}", str(r["laps"]), r["status"]], col))
        self.rooms.set_rows(rows)
        lb = [(i, [str(i + 1), row["user"], str(row["points"]), str(row["wins"])], None)
              for i, row in enumerate(self.app.leaderboard[:6])]
        self.leader.set_rows(lb)

    def join(self):
        rid = self.rooms.selected
        if rid is None:
            self.flash("Select a room first (or create one)")
            return
        free = next((r["max"] - r["players"] for r in self.app.rooms if r["id"] == rid), 0)
        if free < len(self.app.locals):
            self.flash("Not enough free seats for all local players")
            return
        self.app.primary.send(P.C_JOIN_ROOM, room_id=rid)

    def create(self):
        self.app.primary.send(P.C_CREATE_ROOM, name=self.name.value.strip(), track=self.track.value,
                              laps=self.laps.value, ghosts=self.ghosts.value)

    def on_message(self, lp, msg):
        t = msg["type"]
        if t in (P.S_ROOM_LIST, P.S_LEADERBOARD):
            self.apply_rooms()
        elif t == P.S_ROOM_STATE and lp.slot == 0:
            self.app.go(RoomScene(self.app))
        elif t == P.S_ERROR:
            self.flash(msg["msg"])

    def draw(self, surf):
        ui.background(surf)
        title(surf, "LOBBY", "Players on this PC: " + ",  ".join(f"P{lp.slot + 1} {lp.user}" for lp in self.app.locals)
              + f"      online: {self.app.online}")
        ui.text(surf, "Rooms (double-click to join)", (44, 120), 20, ACCENT, bold=True)
        ui.panel(surf, pygame.Rect(830, 140, 430, 530), "Create a room")
        ui.text(surf, "Leaderboard", (852, 466), 18, ACCENT, bold=True)
        self.draw_widgets(surf)
        status_bar(surf, self.app)


# =========================================================================== ROOM
class RoomScene(Scene):
    def __init__(self, app):
        super().__init__(app)
        self.track = self.add(Cycle((60, 200, 420, 46), "Track", TRACK_OPTS, on_change=self.changed))
        self.laps = self.add(Cycle((60, 256, 420, 46), "Laps", LAP_OPTS, on_change=self.changed))
        self.ghosts = self.add(Cycle((60, 312, 420, 46), "Ghost opponents", GHOST_OPTS, on_change=self.changed))
        self.ready_btn = self.add(Button((560, 590, 220, 60), "READY", self.toggle_ready, GOOD))
        self.start_btn = self.add(Button((800, 590, 220, 60), "START RACE", self.start))
        self.add(Button((1040, 590, 180, 60), "Leave", self.leave, (120, 60, 80)))
        self.sync()

    @property
    def room(self):
        return self.app.room

    def sync(self):
        r = self.room
        if not r:
            return
        is_host = r["host_pid"] == self.app.locals[0].pid
        for w in (self.track, self.laps, self.ghosts):
            w.enabled = is_host
        self.track.set_value(r["track"])
        self.laps.set_value(r["laps"])
        self.ghosts.set_value(r["ghosts"])
        self.start_btn.visible = is_host
        mine = [m for m in r["members"] if m["pid"] in self.app.local_pids]
        all_ready = mine and all(m["ready"] for m in mine)
        self.ready_btn.label = "NOT READY" if all_ready else "READY"
        self.ready_btn.color = ACCENT if all_ready else GOOD
        waiting = r["status"] == "WAITING"
        self.ready_btn.enabled = waiting
        self.start_btn.enabled = waiting and all(m["ready"] for m in r["members"])
        if not waiting:
            self.ready_btn.label = "wait..."

    def changed(self, _v):
        self.app.primary.send(P.C_ROOM_SETTINGS, track=self.track.value, laps=self.laps.value, ghosts=self.ghosts.value)

    def toggle_ready(self):
        r = self.room
        mine = [m for m in r["members"] if m["pid"] in self.app.local_pids]
        target = not all(m["ready"] for m in mine)
        for lp in self.app.locals:
            lp.net.send(P.C_SET_READY, ready=target)

    def start(self):
        self.app.primary.send(P.C_START_GAME)

    def leave(self):
        for lp in self.app.locals:
            lp.net.send(P.C_LEAVE_ROOM)

    def on_message(self, lp, msg):
        t = msg["type"]
        if t == P.S_ROOM_STATE:
            self.sync()
        elif t == P.S_LEFT_ROOM and lp.slot == 0:
            self.app.room = None
            self.app.go(LobbyScene(self.app))
        elif t == P.S_RACE_START and lp.slot == 0:
            from client.race import RaceScene
            self.app.go(RaceScene(self.app, msg))
        elif t == P.S_ERROR:
            self.flash(msg["msg"])
        elif t == P.S_EVENT:
            self.flash(msg["text"], ACCENT2)

    def draw(self, surf):
        ui.background(surf)
        r = self.room
        if not r:
            return
        title(surf, r["name"], f"Room #{r['id']}  -  host: {r['host']}  -  {r['status']}")
        ui.panel(surf, pygame.Rect(40, 140, 460, 520), "Race settings" + ("" if self.track.enabled else " (host only)"))
        draw_track_preview(surf, pygame.Rect(90, 370, 360, 280), self.track.value)
        ui.panel(surf, pygame.Rect(530, 140, 710, 430), f"Players ({len(r['members'])}/{r['max']})")
        y = 190
        for m in r["members"]:
            row = pygame.Rect(550, y, 670, 52)
            pygame.draw.rect(surf, (22, 24, 50), row, border_radius=8)
            pygame.draw.rect(surf, PLAYER_COLORS[m["color"] % len(PLAYER_COLORS)], (row.x + 10, row.y + 10, 32, 32),
                             border_radius=6)
            tag = ""
            if m["pid"] == r["host_pid"]:
                tag += "  [HOST]"
            if m["pid"] in self.app.local_pids:
                tag += "  (this PC)"
            ui.text(surf, m["name"] + tag, (row.x + 56, row.y + 12), 22, TEXT, bold=m["pid"] in self.app.local_pids)
            ui.text(surf, m["addr"], (row.x + 420, row.y + 16), 16, MUTED)
            ui.text(surf, "READY" if m["ready"] else "not ready", (row.right - 16, row.y + 13), 22,
                    GOOD if m["ready"] else BAD, bold=True, right=True)
            y += 60
        n_ghosts = min(r["ghosts"], C.MAX_PLAYERS_PER_ROOM - len(r["members"]))
        if n_ghosts:
            ui.text(surf, f"+ {n_ghosts} ghost opponent(s) replaying saved runs", (556, y + 8), 18, ACCENT2)
        if len(self.app.locals) == 2:
            ui.text(surf, "Split-screen: P1 = WASD + Space,  P2 = Arrows + Enter", (556, 540), 16, MUTED)
        else:
            ui.text(surf, "Controls: Arrows/WASD to drive, Space to use item, F3 network stats", (556, 540), 16, MUTED)
        self.draw_widgets(surf)
        status_bar(surf, self.app)
