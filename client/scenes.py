"""Menu scenes: Connect/Login, Lobby, Room."""
import threading

import pygame

from client import ui
from client.net import NetClient, discover_servers
from client.ui import (BLUE, DARK_GREEN, DARK_RED, GREEN, INK, INK_SOFT, ORANGE, OUTLINE, PLAYER_COLORS,
                       RED, WHITE, YELLOW, Button, Cycle, ListView, Scene, TextInput, Toggle)
from common import config as C
from common import protocol as P
from common.track import TRACK_ORDER, TRACKS, Track

TRACK_OPTS = [(k, TRACKS[k]["title"]) for k in TRACK_ORDER]
LAP_OPTS = [(n, str(n)) for n in range(1, 6)]
GHOST_OPTS = [(n, "none" if n == 0 else str(n)) for n in range(0, C.MAX_GHOSTS + 1)]

FLASH_OK = (120, 240, 140)
FLASH_INFO = (130, 210, 255)
FLASH_BAD = (255, 110, 110)
FLASH_WARN = (255, 200, 80)


def header(surf, big, sub=None, sub_color=BLUE):
    """Big bouncing title + a small ribbon under it."""
    if big == "LOGO":
        ui.logo(surf, (surf.get_width() // 2, 58), 60)
    else:
        ui.otext(surf, big, (surf.get_width() // 2, 54), 48, grad=ui.LOGO_GRAD, thick=5, center=True)
    if sub:
        ui.ribbon(surf, (surf.get_width() // 2, 112), sub, sub_color, 17)


def status_bar(surf, app):
    """Bottom strip: ping + UDP status per local player, and what the server sees."""
    W, H = surf.get_size()
    strip = pygame.Surface((W, 30), pygame.SRCALPHA)
    strip.fill((16, 18, 40, 170))
    surf.blit(strip, (0, H - 30))
    x = 14
    for lp in app.locals:
        n = lp.net
        rtt = f"{n.rtt * 1000:.0f} ms" if n.rtt is not None else "..."
        udp = "UDP ok" if n.udp_ok else "UDP waiting"
        col = FLASH_OK if n.udp_ok else FLASH_WARN
        r = ui.text(surf, f"P{lp.slot + 1} {lp.user} · ping {rtt} · {udp}", (x, H - 26), 16, col, bold=True)
        x = r.right + 26
    if app.locals:
        n = app.locals[0].net
        if n.seen_udp:
            ui.text(surf, f"server {n.server_ip}  ·  you {n.local_tcp.split(':')[0]}  ·  server sees {n.seen_udp}",
                    (W - 14, H - 26), 16, (200, 206, 240), right=True)


_preview_cache = {}


def draw_track_preview(surf, rect, key):
    """Track map on a grass card."""
    ck = (key, rect.size)
    if ck not in _preview_cache:
        t = Track(key)
        theme = TRACKS[key]["theme"]
        ground = {"grass": (92, 182, 74), "desert": (226, 190, 128), "neon": (34, 26, 70)}[theme]
        road = {"grass": (78, 80, 92), "desert": (120, 108, 100), "neon": (60, 60, 90)}[theme]
        edge = {"grass": (245, 245, 245), "desert": (255, 240, 220), "neon": (255, 60, 210)}[theme]
        img = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(img, ground, img.get_rect(), border_radius=16)
        area = pygame.Rect(10, 10, rect.w - 20, rect.h - 52)
        sc = min(area.w, area.h) / 2048 * 0.98
        ox = area.x + (area.w - 2048 * sc) / 2
        oy = area.y + (area.h - 2048 * sc) / 2
        pts = [(ox + x * sc, oy + y * sc) for x, y in t.pts]
        for col, extra in ((OUTLINE, 4), (edge, 2), (road, 0)):
            r = t.half * sc + extra
            for p in pts:
                pygame.draw.circle(img, col, (int(p[0]), int(p[1])), int(r))
        fx, fy, h = t.point_at(0)
        cx, cy = int(ox + fx * sc), int(oy + fy * sc)
        for i in range(4):
            pygame.draw.rect(img, WHITE if i % 2 == 0 else OUTLINE, (cx - 8 + (i % 2) * 8, cy - 8 + (i // 2) * 8, 8, 8))
        pygame.draw.rect(img, OUTLINE, img.get_rect(), 4, border_radius=16)
        name = ui.outlined(TRACKS[key]["title"].upper(), 20, thick=2, depth=2)
        img.blit(name, name.get_rect(center=(rect.w // 2, rect.h - 24)))
        _preview_cache[ck] = img
    surf.blit(_preview_cache[ck], rect.topleft)


def pill(surf, pos, s, color, size=15, right=False):
    img = ui.outlined(s, size, thick=2, depth=1)
    r = pygame.Rect(0, 0, img.get_width() + 18, img.get_height() + 2)
    if right:
        r.midright = pos
    else:
        r.midleft = pos
    pygame.draw.rect(surf, color, r, border_radius=r.h // 2)
    pygame.draw.rect(surf, OUTLINE, r, 2, border_radius=r.h // 2)
    surf.blit(img, img.get_rect(center=(r.centerx, r.centery)))
    return r


def crown(surf, x, y, s=1.0):
    pts = [(x, y + 14 * s), (x + 2 * s, y), (x + 8 * s, y + 7 * s), (x + 13 * s, y - 2 * s), (x + 18 * s, y + 7 * s),
           (x + 24 * s, y), (x + 26 * s, y + 14 * s)]
    pygame.draw.polygon(surf, YELLOW, pts)
    pygame.draw.polygon(surf, OUTLINE, pts, 2)


# =========================================================================== CONNECT
class ConnectScene(Scene):
    def __init__(self, app, error=None):
        super().__init__(app)
        st = app.settings
        self.host = self.add(TextInput((62, 222, 346, 48), "IP / DNS name [:tcp[:udp]]", value=st.get("host", "127.0.0.1"), max_len=60))
        self.add(Button((62, 286, 180, 48), "SCAN LAN", self.scan, BLUE, 19))
        self.servers = self.add(ListView((62, 350, 346, 200), [("Server", 250), ("On", 60)],
                                         on_select=self.pick_server, row_h=32))
        self.u1 = self.add(TextInput((472, 222, 336, 48), "username", value=st.get("user1", "")))
        self.p1 = self.add(TextInput((472, 286, 336, 48), "password (empty = guest)", password=True))
        self.p2_on = self.add(Toggle((872, 224, 330, 44), "Add local Player 2", st.get("p2", False)))
        self.u2 = self.add(TextInput((872, 286, 346, 48), "P2 username", value=st.get("user2", "")))
        self.p2 = self.add(TextInput((872, 350, 346, 48), "P2 password (empty = guest)", password=True))
        self.login_btn = self.add(Button((360, 594, 260, 66), "LOGIN", lambda: self.go(False), RED, 26))
        self.reg_btn = self.add(Button((660, 594, 260, 66), "REGISTER", lambda: self.go(True), BLUE, 26))
        self.add(Button((1150, 20, 110, 46), "QUIT", app.quit, DARK_RED, 18))
        for w in (self.u1, self.p1, self.u2, self.p2, self.host):
            w.on_enter = lambda: self.go(False)
        self.u1.focused = not self.u1.value
        self.p1.focused = bool(self.u1.value)
        self.busy = False
        self.result = None
        if error:
            self.flash(error, FLASH_BAD, 8)

    def scan(self):
        self.flash("Scanning every network card for servers...", FLASH_INFO, 2.5)

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
                self.flash("Enter a username for every player", FLASH_BAD)
                return
            if register and not pw:
                self.flash("Registration needs a password (leave it empty only to play as guest)", FLASH_BAD)
                return
        if len(players) == 2 and players[0][0].lower() == players[1][0].lower():
            self.flash("Player 1 and Player 2 need different names", FLASH_BAD)
            return
        if not host:
            self.flash("Enter the server address", FLASH_BAD)
            return
        self.app.settings.update(host=host, user1=players[0][0], user2=self.u2.value.strip(), p2=self.p2_on.value)
        self.app.save_settings()
        self.busy = True
        self.flash("Connecting to " + host + " ...", FLASH_INFO, 10)

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
            rows = [(s["ip"], [f"{s['name']}  {s['ip']}", str(s["online"])], None) for s in res[1]]
            self.servers.set_rows(rows)
            if rows:
                self.host.value = rows[0][0]     # best address first (the real LAN card)
                self.servers.selected = rows[0][0]
                self.flash(f"Found {len(rows)} server address(es)", FLASH_OK, 3)
            else:
                self.flash("No server answered: type its IP. If that fails too, this Wi-Fi blocks "
                           "devices from each other: use a phone hotspot", FLASH_WARN, 7)
        elif res[0] == "fail":
            self.busy = False
            self.flash(res[1], FLASH_BAD, 6)
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
            self.flash(f"Account {msg['user']} created", FLASH_OK)
            self._login(lp)
        elif t == P.S_LOGIN_OK:
            lp.user = msg["user"]
            lp.pid = msg["pid"]
            lp.stats = msg.get("stats", {})
            if all(x.pid is not None for x in self.app.locals):
                self.busy = False
                self.app.go(LobbyScene(self.app))
        elif t == P.S_ERROR:
            self.flash(f"P{lp.slot + 1}: {msg['msg']}", FLASH_BAD, 6)
            self.app.disconnect_all()
            self.busy = False

    def draw(self, surf):
        ui.background(surf)
        header(surf, "LOGO", "ONLINE MULTIPLAYER RACING", RED)
        ui.panel(surf, pygame.Rect(40, 152, 390, 420), "SERVER", BLUE)
        ui.panel(surf, pygame.Rect(450, 152, 380, 420), "PLAYER 1", RED)
        ui.panel(surf, pygame.Rect(850, 152, 390, 420), "PLAYER 2", GREEN)
        ui.text(surf, "Leave the password empty to", (472, 352), 17, INK_SOFT, bold=True)
        ui.text(surf, "play as a guest (no stats saved).", (472, 374), 17, INK_SOFT, bold=True)
        ui.text(surf, "Register once, then log in", (472, 410), 17, INK_SOFT, bold=True)
        ui.text(surf, "with the same name on any PC.", (472, 432), 17, INK_SOFT, bold=True)
        ui.text(surf, "Controls", (472, 474), 19, INK, bold=True)
        ui.text(surf, "Drive: WASD / arrows · Item: Space", (472, 500), 16, INK_SOFT, bold=True)
        ui.text(surf, "Network stats: F3", (472, 522), 16, INK_SOFT, bold=True)
        ui.text(surf, "Split-screen on this PC:", (872, 420), 17, INK_SOFT, bold=True)
        ui.text(surf, "P1  WASD + Space", (872, 448), 18, INK, bold=True)
        ui.text(surf, "P2  Arrows + Enter", (872, 474), 18, INK, bold=True)
        self.u2.enabled = self.p2.enabled = self.p2_on.value
        self.draw_widgets(surf)


# =========================================================================== LOBBY
class LobbyScene(Scene):
    def __init__(self, app):
        super().__init__(app)
        self.rooms = self.add(ListView((52, 200, 756, 316),
                                       [("Room", 250), ("Track", 170), ("Players", 100), ("Laps", 70), ("Status", 150)],
                                       on_activate=lambda k: self.join(), row_h=38))
        self.add(Button((52, 532, 200, 54), "JOIN", self.join, GREEN, 22))
        self.add(Button((270, 532, 190, 54), "REFRESH", self.refresh, BLUE, 20))
        self.add(Button((618, 532, 190, 54), "LOGOUT", self.app.logout, DARK_RED, 20))
        primary = app.locals[0].user
        self.name = self.add(TextInput((858, 198, 376, 46), "room name", value=f"{primary}'s room"))
        self.track = self.add(Cycle((858, 252, 376, 46), "Track", TRACK_OPTS))
        self.laps = self.add(Cycle((858, 306, 376, 46), "Laps", LAP_OPTS, index=2))
        self.ghosts = self.add(Cycle((858, 360, 376, 46), "Ghosts", GHOST_OPTS, index=0))
        self.add(Button((858, 416, 376, 52), "CREATE ROOM", self.create, ORANGE, 22))
        self.leader = self.add(ListView((858, 544, 376, 130), [("#", 36), ("Player", 186), ("Pts", 70), ("Wins", 60)],
                                        row_h=26))
        self.refresh()
        self.apply_rooms()

    def refresh(self):
        self.app.primary.send(P.C_LIST_ROOMS)
        self.app.primary.send(P.C_LEADERBOARD)

    def apply_rooms(self):
        rows = []
        for r in self.app.rooms:
            col = DARK_GREEN if r["status"] == "WAITING" else INK_SOFT
            rows.append((r["id"], [r["name"], TRACKS.get(r["track"], {}).get("title", r["track"]),
                                   f"{r['players']}/{r['max']}", str(r["laps"]),
                                   "OPEN" if r["status"] == "WAITING" else "RACING"], col))
        self.rooms.set_rows(rows)
        lb = [(i, [str(i + 1), row["user"], str(row["points"]), str(row["wins"])], None)
              for i, row in enumerate(self.app.leaderboard[:6])]
        self.leader.set_rows(lb)

    def join(self):
        rid = self.rooms.selected
        if rid is None:
            self.flash("Select a room first (or create one)", FLASH_WARN)
            return
        free = next((r["max"] - r["players"] for r in self.app.rooms if r["id"] == rid), 0)
        if free < len(self.app.locals):
            self.flash("Not enough free seats for all local players", FLASH_BAD)
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
            self.flash(msg["msg"], FLASH_BAD)

    def draw(self, surf):
        ui.background(surf)
        who = "  ·  ".join(f"P{lp.slot + 1} {lp.user}" for lp in self.app.locals)
        header(surf, "LOBBY", f"{who.upper()}   ·   {self.app.online} ONLINE", BLUE)
        ui.panel(surf, pygame.Rect(32, 140, 796, 466), "ROOMS · DOUBLE-CLICK TO JOIN", BLUE)
        ui.panel(surf, pygame.Rect(840, 140, 412, 344), "NEW ROOM", RED)
        ui.panel(surf, pygame.Rect(840, 494, 412, 192), "LEADERBOARD", ORANGE)
        self.draw_widgets(surf)
        status_bar(surf, self.app)


# =========================================================================== ROOM
class RoomScene(Scene):
    def __init__(self, app):
        super().__init__(app)
        self.track = self.add(Cycle((52, 198, 426, 46), "Track", TRACK_OPTS, on_change=self.changed))
        self.laps = self.add(Cycle((52, 252, 426, 46), "Laps", LAP_OPTS, on_change=self.changed))
        self.ghosts = self.add(Cycle((52, 306, 426, 46), "Ghosts", GHOST_OPTS, on_change=self.changed))
        self.ready_btn = self.add(Button((520, 600, 236, 66), "READY", self.toggle_ready, GREEN, 24))
        self.start_btn = self.add(Button((772, 600, 260, 66), "START RACE", self.start, RED, 24))
        self.add(Button((1048, 600, 200, 66), "LEAVE", self.leave, DARK_RED, 22))
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
        self.ready_btn.color = ORANGE if all_ready else GREEN
        waiting = r["status"] == "WAITING"
        self.ready_btn.enabled = waiting
        self.start_btn.enabled = waiting and all(m["ready"] for m in r["members"])
        if not waiting:
            self.ready_btn.label = "WAIT..."

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
            self.flash(msg["msg"], FLASH_BAD)
        elif t == P.S_EVENT:
            self.flash(msg["text"], FLASH_INFO)

    def draw(self, surf):
        ui.background(surf)
        r = self.room
        if not r:
            return
        header(surf, r["name"].upper(), f"ROOM #{r['id']}  ·  HOST {r['host'].upper()}  ·  "
                                        f"{'WAITING FOR PLAYERS' if r['status'] == 'WAITING' else 'RACE IN PROGRESS'}", RED)
        host_only = "" if self.track.enabled else " · HOST ONLY"
        ui.panel(surf, pygame.Rect(32, 140, 466, 540), "RACE SETTINGS" + host_only, RED)
        draw_track_preview(surf, pygame.Rect(52, 370, 426, 290), self.track.value)
        ui.panel(surf, pygame.Rect(512, 140, 740, 440), f"PLAYERS  {len(r['members'])}/{r['max']}", BLUE)
        sp = ui._sprites()
        t = pygame.time.get_ticks() / 1000.0
        y = 204
        for i, m in enumerate(r["members"]):
            row = pygame.Rect(530, y, 704, 56)
            local = m["pid"] in self.app.local_pids
            pygame.draw.rect(surf, (255, 246, 200) if local else (240, 243, 252), row, border_radius=14)
            pygame.draw.rect(surf, OUTLINE, row, 3, border_radius=14)
            col = PLAYER_COLORS[m["color"] % len(PLAYER_COLORS)]
            pygame.draw.ellipse(surf, (200, 204, 222), (row.x + 10, row.y + 36, 74, 16))   # turntable
            kart = sp.kart(m["color"], t * 1.6 + i * 0.9)
            img = sp.scaled(kart, 84, ("roomkart", m["color"], id(kart)))
            surf.blit(img, img.get_rect(midbottom=(row.x + 47, row.y + 58)))
            name = ui.outlined(m["name"], 22, fill=col, thick=3, depth=2)
            surf.blit(name, (row.x + 96, row.centery - name.get_height() // 2))
            x = row.x + 106 + name.get_width()
            if m["pid"] == r["host_pid"]:
                crown(surf, x, row.centery - 9)
                x += 36
            if local:
                pill(surf, (x, row.centery), "THIS PC", BLUE)
            ui.text(surf, m["addr"], (row.right - 168, row.centery - 10), 16, INK_SOFT, bold=True, right=True)
            pill(surf, (row.right - 12, row.centery), "READY" if m["ready"] else "NOT READY",
                 GREEN if m["ready"] else RED, 16, right=True)
            y += 62
        n_ghosts = min(r["ghosts"], C.MAX_PLAYERS_PER_ROOM - len(r["members"]))
        if n_ghosts and y < 540:
            ui.text(surf, f"+ {n_ghosts} ghost opponent(s) replaying saved runs", (536, y + 6), 18, BLUE, bold=True)
            y += 34
        if y < 536:
            hint = ("Split-screen: P1 = WASD + Space  ·  P2 = Arrows + Enter" if len(self.app.locals) == 2
                    else "Drive with WASD / arrows  ·  Space = use item  ·  F3 = network stats")
            ui.text(surf, hint, (536, 542), 17, INK_SOFT, bold=True)
        self.draw_widgets(surf)
        status_bar(surf, self.app)
