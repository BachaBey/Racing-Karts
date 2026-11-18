"""Client application: window, main loop, local players, message routing."""
import json
import os
import sys

import pygame

from common import config as C
from common import protocol as P

if getattr(sys, "frozen", False):
    # packaged .exe: code lives in a temp folder, so keep settings next to the executable
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(_BASE_DIR, "client_settings.json")

CONTROLS = [
    # P1 (also accepts arrows when playing alone)
    # AZERTY ZQSD (Z up, Q left, S down, D right); QWERTY WASD still works for friends
    {"up": [pygame.K_z, pygame.K_w], "down": [pygame.K_s], "left": [pygame.K_q, pygame.K_a], "right": [pygame.K_d],
     "item": [pygame.K_SPACE, pygame.K_LSHIFT]},
    # P2
    {"up": [pygame.K_UP], "down": [pygame.K_DOWN], "left": [pygame.K_LEFT], "right": [pygame.K_RIGHT],
     "item": [pygame.K_RETURN, pygame.K_RCTRL, pygame.K_RSHIFT, pygame.K_KP_ENTER]},
]
SOLO_CONTROLS = {k: CONTROLS[0][k] + CONTROLS[1][k] for k in CONTROLS[0]}


class LocalPlayer:
    """A player sitting at this PC. Each one has its OWN TCP+UDP connection, so for the
    server a split-screen player is indistinguishable from a player on another PC."""

    def __init__(self, slot, net):
        self.slot = slot
        self.net = net
        self.user = None
        self.pid = None
        self.stats = {}
        self.pending_user = None
        self.pending_pw = None

    def controls(self, n_locals):
        return SOLO_CONTROLS if n_locals == 1 else CONTROLS[self.slot]


class App:
    W, H = 1280, 720

    def __init__(self, host=None, fullscreen=False):
        pygame.init()
        pygame.key.set_repeat(400, 35)
        flags = pygame.SCALED | pygame.RESIZABLE
        if fullscreen:
            flags |= pygame.FULLSCREEN
        self.screen = pygame.display.set_mode((self.W, self.H), flags)
        pygame.display.set_caption(C.GAME_NAME)
        self.clock = pygame.time.Clock()
        self.settings = self._load_settings()
        if host:
            self.settings["host"] = host
        self.locals = []
        self.room = None
        self.rooms = []
        self.online = 0
        self.leaderboard = []
        self.running = True
        from client.scenes import ConnectScene
        self.scene = ConnectScene(self)

    # ------------------------------------------------------------- settings
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=1)
        except OSError:
            pass

    # ------------------------------------------------------------- players
    @property
    def primary(self):
        return self.locals[0].net

    @property
    def local_pids(self):
        return {lp.pid for lp in self.locals}

    def add_local(self, slot, net):
        lp = LocalPlayer(slot, net)
        self.locals.append(lp)
        return lp

    def disconnect_all(self):
        for lp in self.locals:
            lp.net.close()
        self.locals = []
        self.room = None

    def logout(self):
        for lp in self.locals:
            lp.net.send(P.C_LOGOUT)
        self.disconnect_all()
        from client.scenes import ConnectScene
        self.go(ConnectScene(self))

    def go(self, scene):
        self.scene = scene

    def quit(self):
        self.running = False

    # ------------------------------------------------------------- networking
    def route(self, lp, msg):
        t = msg["type"]
        if t in ("_DISCONNECTED", P.S_SHUTDOWN):
            reason = msg.get("reason") or msg.get("msg") or "Disconnected"
            if lp.slot == 0 or len(self.locals) == 1:
                self.disconnect_all()
                from client.scenes import ConnectScene
                self.go(ConnectScene(self, error=f"Disconnected from server: {reason}"))
            else:
                lp.net.close()
                self.locals.remove(lp)
                self.scene.flash(f"Player 2 lost connection: {reason}")
                if hasattr(self.scene, "drop_local"):
                    self.scene.drop_local(lp)
            return
        if t == P.S_ROOM_LIST:
            self.rooms = msg["rooms"]
            self.online = msg.get("online", self.online)
        elif t == P.S_LEADERBOARD:
            self.leaderboard = msg["rows"]
        elif t == P.S_ROOM_STATE and lp.slot == 0:
            self.room = msg["room"]
            # split-screen: Player 2 follows Player 1 into the same room
            member_pids = {m["pid"] for m in self.room["members"]}
            for other in self.locals[1:]:
                if other.pid not in member_pids and self.room["status"] == "WAITING" \
                        and not getattr(other, "joining", None) == self.room["id"]:
                    other.joining = self.room["id"]
                    other.net.send(P.C_JOIN_ROOM, room_id=self.room["id"])
        elif t == P.S_LEFT_ROOM:
            if lp.slot == 0:
                self.room = None
            lp.joining = None
        self.scene.on_message(lp, msg)

    # ------------------------------------------------------------- main loop
    def run(self):
        while self.running:
            dt = min(0.05, self.clock.tick(60) / 1000.0)
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()
                else:
                    self.scene.handle_event(ev)
            for lp in list(self.locals):
                lp.net.heartbeat()
                for msg in lp.net.poll():
                    if lp not in self.locals:
                        break
                    self.route(lp, msg)
            self.scene.update(dt)
            self.scene.draw(self.screen)
            pygame.display.flip()
        for lp in self.locals:
            lp.net.send(P.C_LOGOUT)
            lp.net.close()
        pygame.quit()
