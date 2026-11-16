"""The race scene: local simulation, UDP state streaming, snapshot interpolation,
split-screen Mode-7 rendering and HUD."""
import math
import time

import pygame

from client import ui
from client.render.mode7 import Mode7View, TrackArt
from client.render.sprites import KART_GROUND_Y, KART_WORLD, SpriteBank, scenery_sprites
from client.ui import ACCENT, ACCENT2, BAD, GOOD, MUTED, PLAYER_COLORS, TEXT, Scene
from common import config as C
from common import protocol as P
from common.physics import AIDriver, KartPhysics
from common.track import Track, wrap_angle

_ART_CACHE = {}
_SPRITES = None


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def fmt_time(t):
    if t is None:
        return "--"
    return f"{int(t // 60)}:{t % 60:06.3f}"


class LocalRacer:
    def __init__(self, lp, grid_entry, track):
        self.lp = lp
        self.kart = KartPhysics(grid_entry["x"], grid_entry["y"], grid_entry["a"])
        self.color = grid_entry["color"]
        self.cam_angle = grid_entry["a"]
        self.item = C.ITEM_NONE
        self.item_count = 0
        self.rank = 0
        self.lap = 1
        self.shield = False
        self.finished = False
        self.finish_rank = None
        self.finish_time = None
        self.auto = AIDriver(track, 0.8, 0.0)
        self.next_send = 0.0
        self.hit_flash = 0.0
        self.item_roll = 0.0
        self.view = None
        self.rect = None


class RaceScene(Scene):
    def __init__(self, app, start):
        super().__init__(app)
        global _SPRITES
        if _SPRITES is None:
            _SPRITES = SpriteBank()
        self.sprites = _SPRITES
        self.track = Track(start["track"])
        if start["track"] not in _ART_CACHE:
            _ART_CACHE[start["track"]] = TrackArt(self.track)
        self.art = _ART_CACHE[start["track"]]
        self.scenery_imgs = scenery_sprites(self.track.theme)
        self.scenery = self.art.track.scenery()
        self.laps = start["laps"]
        self.info = {g["pid"]: g for g in start["grid"]}
        self.sprites.prewarm({g["color"] for g in start["grid"]})   # ~0.1 s per colour, hidden by the countdown
        self.racers = []
        for lp in app.locals:
            g = self.info.get(lp.pid)
            if g:
                self.racers.append(LocalRacer(lp, g, self.track))
        for lp in app.locals:
            lp.net.reset_snapshots()
        self.countdown_end = time.time() + start["countdown"]
        self.go = False
        self.go_time = None
        self.race_clock = 0.0
        self.feed = []
        self.results = None
        self.results_time = None
        self.show_net = False
        self.esc_armed = 0.0
        self.remote = {}
        self.objs = {}
        self.boxes = (1 << len(self.track.box_positions)) - 1
        self.clock_offset = None
        self.last_snap = None
        self.layout()
        self.flash("Get ready!  " + ("P1: WASD + Space   P2: Arrows + Enter" if len(self.racers) == 2
                                     else "Arrows/WASD to drive, Space to use items"), ACCENT2, 4)

    # ------------------------------------------------------------- layout
    def layout(self):
        W, H = self.app.W, self.app.H
        if len(self.racers) <= 1:
            rects = [pygame.Rect(0, 0, W, H)]
            internal = [(640, 360)]
        else:
            h = H // 2 - 2
            rects = [pygame.Rect(0, 0, W, h), pygame.Rect(0, H - h, W, h)]
            internal = [(640, h // 2)] * 2
        for r, rect, (iw, ih) in zip(self.racers, rects, internal):
            r.rect = rect
            r.view = Mode7View(iw, ih, self.art, self.art.surface)
            r.surf = pygame.Surface((iw, ih)).convert()

    def drop_local(self, lp):
        self.racers = [r for r in self.racers if r.lp is not lp]
        self.layout()

    def racer_of(self, lp):
        return next((r for r in self.racers if r.lp is lp), None)

    # ------------------------------------------------------------- network messages
    def on_message(self, lp, msg):
        t = msg["type"]
        r = self.racer_of(lp)
        if t == P.S_GO:
            if not self.go:
                self.go = True
                self.go_time = time.time()
        elif t == P.S_ITEM_GOT and r:
            if msg["item"] != C.ITEM_NONE and r.item == C.ITEM_NONE:
                r.item_roll = 0.8   # short "slot machine" animation
            r.item, r.item_count = msg["item"], msg.get("count", 1)
        elif t == P.S_EFFECT and r:
            k = msg["kind"]
            if k == "spin":
                r.kart.spin()
                r.hit_flash = 0.6
            elif k == "boost":
                r.kart.boost()
            elif k == "shield":
                r.shield = True
        elif t == P.S_EVENT:
            if lp.slot == 0 or "FINAL" in msg["text"] or "incoming" in msg["text"]:
                self.add_feed(msg["text"])
        elif t == P.S_FINISHED:
            if lp.slot == 0:
                pass
            for rr in self.racers:
                if rr.lp.pid == msg["pid"]:
                    rr.finished = True
                    rr.finish_rank = msg["rank"]
                    rr.finish_time = msg["time"]
        elif t == P.S_PLAYER_LEFT and lp.slot == 0:
            self.remote.pop(msg["pid"], None)
        elif t == P.S_RACE_RESULTS and lp.slot == 0:
            self.results = msg
            self.results_time = time.time()
        elif t == P.S_LEFT_ROOM and lp.slot == 0:
            from client.scenes import LobbyScene
            self.app.go(LobbyScene(self.app))
        elif t == P.S_ERROR:
            self.flash(msg["msg"])

    def add_feed(self, text):
        self.feed.append((text, time.time() + 4.5))
        self.feed = self.feed[-5:]

    # ------------------------------------------------------------- input
    def handle_event(self, ev):
        if ev.type != pygame.KEYDOWN:
            return
        if ev.key == pygame.K_F3:
            self.show_net = not self.show_net
            return
        if self.results:
            if ev.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_ESCAPE, pygame.K_KP_ENTER):
                self.leave_results()
            return
        if ev.key == pygame.K_ESCAPE:
            if time.time() < self.esc_armed:
                for lp in self.app.locals:
                    lp.net.send(P.C_LEAVE_ROOM)
            else:
                self.esc_armed = time.time() + 2.0
                self.flash("Press Esc again to quit the race", ACCENT, 2)
            return
        for r in self.racers:
            if ev.key in r.lp.controls(len(self.racers))["item"] and self.go and r.item != C.ITEM_NONE \
                    and r.item_roll <= 0 and not r.finished:
                r.lp.net.send(P.C_USE_ITEM)
                break

    def leave_results(self):
        from client.scenes import LobbyScene, RoomScene
        if self.app.room:
            self.app.go(RoomScene(self.app))
        else:
            self.app.go(LobbyScene(self.app))

    # ------------------------------------------------------------- update
    def update(self, dt):
        now = time.time()
        if self.go:
            self.race_clock = now - self.go_time
        self.interpolate(now)
        keys = pygame.key.get_pressed()
        others = [(v["x"], v["y"]) for pid, v in self.remote.items()
                  if pid not in self.app.local_pids and not v["flags"] & P.F_GHOST]
        for r in self.racers:
            k = r.kart
            if self.go and not r.finished:
                ctl = r.lp.controls(len(self.racers))
                th = any(keys[c] for c in ctl["up"])
                br = any(keys[c] for c in ctl["down"])
                st = (1 if any(keys[c] for c in ctl["right"]) else 0) - (1 if any(keys[c] for c in ctl["left"]) else 0)
                k.step(dt, th, br, st, self.track)
            elif self.go and r.finished:
                th, br, st = r.auto.control(k)       # victory lap on autopilot
                k.step(dt, th, br, st, self.track, speed_factor=0.7)
            if self.go:
                for (ox, oy) in others + [(o.kart.x, o.kart.y) for o in self.racers if o is not r]:
                    k.collide_with(ox, oy)
            r.cam_angle += wrap_angle(k.angle - r.cam_angle) * min(1.0, dt * (3.0 if k.spin_t > 0 else 9.0))
            r.hit_flash = max(0.0, r.hit_flash - dt)
            r.item_roll = max(0.0, r.item_roll - dt)
            if now >= r.next_send:
                r.next_send = now + 1.0 / C.CLIENT_STATE_HZ
                flags = (P.F_BOOST if k.boost_t > 0 else 0) | (P.F_SPIN if k.spin_t > 0 else 0)
                r.lp.net.send_state(k.x, k.y, k.angle, k.speed, flags)
            mine = self.remote.get(r.lp.pid)
            if mine:
                r.rank, r.lap = mine["rank"], mine["lap"]
                r.shield = bool(mine["flags"] & P.F_SHIELD)

    def interpolate(self, now):
        """Entity interpolation: render remote karts INTERP_DELAY in the past, between two snapshots."""
        if not self.app.locals:
            return
        snaps = self.app.primary.get_snapshots()
        if not snaps:
            return
        # estimate the offset between the local clock and the server race clock
        offs = [rt - s["time"] for rt, s in snaps[-20:]]
        target = min(offs)
        self.clock_offset = target if self.clock_offset is None else self.clock_offset + (target - self.clock_offset) * 0.1
        rt = now - self.clock_offset - self.app.primary.interp_delay
        latest = snaps[-1][1]
        self.last_snap = latest
        self.boxes = latest["boxes"]
        a = b = None
        for _, s in snaps:
            if s["time"] <= rt:
                a = s
            elif a is not None:
                b = s
                break
        if a is None:
            a = snaps[0][1]
        if b is None:
            b = a
        span = b["time"] - a["time"]
        f = 0.0 if span <= 0 else max(0.0, min(1.0, (rt - a["time"]) / span))
        kb = {k["pid"]: k for k in b["karts"]}
        out = {}
        for k in a["karts"]:
            k2 = kb.get(k["pid"], k)
            out[k["pid"]] = {"x": k["x"] + (k2["x"] - k["x"]) * f, "y": k["y"] + (k2["y"] - k["y"]) * f,
                             "a": k["a"] + wrap_angle(k2["a"] - k["a"]) * f, "flags": k2["flags"],
                             "rank": k2["rank"], "lap": k2["lap"], "speed": k2["speed"]}
        for k in latest["karts"]:   # rank / lap / flags always from the newest data
            if k["pid"] in out:
                out[k["pid"]].update(rank=k["rank"], lap=k["lap"], flags=k["flags"])
            else:
                out[k["pid"]] = dict(k)
        self.remote = out
        ob = {o["id"]: o for o in b["objs"]}
        objs = {}
        for o in a["objs"]:
            o2 = ob.get(o["id"], o)
            objs[o["id"]] = (o["kind"], o["x"] + (o2["x"] - o["x"]) * f, o["y"] + (o2["y"] - o["y"]) * f)
        for o in latest["objs"]:
            if o["id"] not in objs and o["id"] in ob:
                objs[o["id"]] = (o["kind"], o["x"], o["y"])
        latest_ids = {o["id"] for o in latest["objs"]}
        self.objs = {i: v for i, v in objs.items() if i in latest_ids}

    # ------------------------------------------------------------- render
    def draw(self, surf):
        surf.fill((0, 0, 0))
        for r in self.racers:
            self.draw_view(r)
            pygame.transform.scale(r.surf, r.rect.size, surf.subsurface(r.rect))
            self.draw_hud(surf, r)
        self.draw_minimap(surf)
        if not self.results:
            self.draw_feed(surf)
        if self.show_net:
            self.draw_netstats(surf)
        if self.results:
            self.draw_results(surf)
        self.draw_widgets(surf)

    def draw_view(self, r):
        v = r.view
        k = r.kart
        cx = k.x - math.cos(r.cam_angle) * v.cam_back
        cy = k.y - math.sin(r.cam_angle) * v.cam_back
        ca = r.cam_angle
        s = r.surf
        v.render(s, cx, cy, ca)
        items = []
        sp = self.sprites
        # scenery
        for (x, y, var) in self.scenery:
            p = v.project(cx, cy, ca, x, y)
            if p is None or p[3] > 1700:
                continue
            idx = int(var * len(self.scenery_imgs)) % len(self.scenery_imgs)
            img, wh = self.scenery_imgs[idx]
            hpx = wh * p[2]
            w = img.get_width() * hpx / img.get_height()
            items.append((p[3], "img", img, ("sc", idx), w, p[0], p[1], 1.0))
        # item boxes
        t = time.time()
        frame = int(t * 8) % len(sp.box_frames)
        for i, (bx, by) in enumerate(self.track.box_positions):
            if not (self.boxes >> i) & 1:
                continue
            p = v.project(cx, cy, ca, bx, by)
            if p and p[3] < 1600:
                bob = math.sin(t * 3 + i) * 3 * p[2]
                items.append((p[3], "img", sp.box_frames[frame], ("box", frame), 40 * p[2], p[0], p[1] - bob, 0.86))
        # bananas / shells
        for oid, (kind, ox, oy) in self.objs.items():
            p = v.project(cx, cy, ca, ox, oy)
            if p and p[3] < 1600:
                img = {P.OBJ_BANANA: sp.banana, P.OBJ_SHELL: sp.shell, P.OBJ_HOMING: sp.homing}[kind]
                items.append((p[3], "img", img, ("obj", kind), 28 * p[2], p[0], p[1], 0.85))
        # karts: remote (interpolated) + local (live state)
        karts = []
        for pid, st in self.remote.items():
            if pid in self.app.local_pids:
                continue
            g = self.info.get(pid, {})
            karts.append((pid, st["x"], st["y"], st["a"], g.get("color", 0), bool(st["flags"] & P.F_GHOST),
                          bool(st["flags"] & P.F_SHIELD), g.get("name", "?"), bool(st["flags"] & P.F_BOOST)))
        for o in self.racers:
            karts.append((o.lp.pid, o.kart.x, o.kart.y, o.kart.angle, o.color, False, o.shield,
                          o.lp.user if o is not r else None, o.kart.boost_t > 0))
        for pid, x, y, a, col, ghost, shield, name, boost in karts:
            p = v.project(cx, cy, ca, x, y)
            if p is None or p[3] > 1800:
                continue
            bearing = math.atan2(y - cy, x - cx)
            rel = wrap_angle(a - bearing)
            img = sp.kart(col, rel, ghost)
            items.append((p[3], "kart", img, (pid, ghost), KART_WORLD * p[2], p[0], p[1], KART_GROUND_Y,
                          shield, name, ghost, boost, sp.frame_index(rel)))
        items.sort(key=lambda it: -it[0])
        font = ui.font(12, True)
        for it in items:
            _d, kind, img, key, w, sx, sy, gy = it[:8]
            if w < 2:
                continue
            simg = sp.scaled(img, w, (key, id(img)))
            rect = simg.get_rect()
            rect.midtop = (int(sx), int(sy - rect.h * gy))
            s.blit(simg, rect)
            if kind == "kart":
                shield, name, ghost, boost, fidx = it[8], it[9], it[10], it[11], it[12]
                if boost and sp.exhaust_anchors:
                    fl = sp.flame(rect.w * 0.16, t + (key[0] % 7) * 0.13)
                    for fx, fy, depth in sp.exhaust_anchors[fidx]:
                        if depth < -4:      # exhaust pipes face the camera -> flames visible
                            s.blit(fl, fl.get_rect(center=(int(rect.x + fx * rect.w), int(rect.y + fy * rect.h))))
                if shield:
                    sh = sp.scaled(sp.shield, w * 0.9, ("shield",))
                    s.blit(sh, sh.get_rect(center=(int(sx), int(sy - rect.h * 0.35))))
                if name and _d < 900:
                    tag = font.render(name, True, (200, 210, 255) if ghost else TEXT)
                    s.blit(tag, tag.get_rect(midbottom=(int(sx), rect.y + rect.h * 0.15)))

    def draw_hud(self, surf, r):
        R = r.rect
        small = R.h < 500
        sz = 0.7 if small else 1.0
        k = r.kart
        # spin flash
        if r.hit_flash > 0:
            fl = pygame.Surface(R.size, pygame.SRCALPHA)
            fl.fill((255, 40, 40, int(120 * r.hit_flash)))
            surf.blit(fl, R.topleft)
        # name + lap + timer (top-left)
        ui.text(surf, f"P{r.lp.slot + 1}  {r.lp.user}", (R.x + 16, R.y + 10), int(18 * sz) + 2,
                PLAYER_COLORS[r.color % len(PLAYER_COLORS)], bold=True, shadow=True)
        ui.text(surf, f"LAP {min(r.lap, self.laps)}/{self.laps}", (R.x + 16, R.y + 10 + 30 * sz), int(34 * sz),
                TEXT, bold=True, shadow=True)
        ui.text(surf, fmt_time(r.finish_time if r.finished else self.race_clock), (R.x + 16, R.y + 10 + 72 * sz),
                int(24 * sz), TEXT, shadow=True)
        # item slot (top-center)
        box = pygame.Rect(0, 0, int(88 * sz), int(88 * sz))
        box.midtop = (R.centerx, R.y + 10)
        pygame.draw.rect(surf, (0, 0, 0), box.inflate(8, 8), border_radius=14)
        pygame.draw.rect(surf, (250, 250, 250), box.inflate(8, 8), 3, border_radius=14)
        item = r.item
        if r.item_roll > 0:
            item = 1 + int(time.time() * 14) % 6
        if item != C.ITEM_NONE:
            ic = pygame.transform.smoothscale(self.sprites.icons[item], (int(box.w * 0.85), int(box.h * 0.85)))
            surf.blit(ic, ic.get_rect(center=box.center))
            if r.item_roll <= 0:
                label = C.ITEM_NAMES[r.item] + (f" x{r.item_count}" if r.item == C.ITEM_TRIPLE else "")
                ui.text(surf, label, (box.centerx, box.bottom + 14), int(18 * sz), TEXT, bold=True, center=True,
                        shadow=True)
        # position (bottom-right)
        if r.rank:
            col = [(255, 215, 0), (200, 210, 230), (220, 140, 60)][r.rank - 1] if r.rank <= 3 else TEXT
            rx = R.right - (210 if len(self.racers) == 2 else 24)   # keep clear of the split-screen minimap
            ui.text(surf, ordinal(r.rank), (rx, R.bottom - int(110 * sz)), int(84 * sz), col, bold=True,
                    right=True, shadow=True)
        # speed bar (bottom-left)
        frac = max(0.0, min(1.0, abs(k.speed) / C.KART_BOOST_SPEED))
        bar = pygame.Rect(R.x + 16, R.bottom - 34, int(220 * sz), 16)
        pygame.draw.rect(surf, (0, 0, 0), bar.inflate(4, 4), border_radius=6)
        pygame.draw.rect(surf, ACCENT if k.boost_t > 0 else GOOD, (bar.x, bar.y, int(bar.w * frac), bar.h),
                         border_radius=6)
        ui.text(surf, f"{abs(k.speed) * 0.4:.0f} km/h", (bar.right + 10, bar.y - 6), int(18 * sz), TEXT, shadow=True)
        if not k.on_road and self.go and not r.finished:
            ui.text(surf, "OFF ROAD", (R.centerx, R.bottom - 40), int(22 * sz), ACCENT, bold=True, center=True, shadow=True)
        # countdown / finish banners
        now = time.time()
        if not self.go or (self.go_time and now - self.go_time < 1.0):
            left = self.countdown_end - now
            txt = "GO!" if self.go else (str(int(math.ceil(left))) if left > 0 else "...")
            ui.text(surf, txt, R.center, int(120 * sz), ACCENT if self.go else TEXT, bold=True, center=True, shadow=True)
        if r.finished and not self.results:
            ui.text(surf, f"FINISHED  {ordinal(r.finish_rank or r.rank)}!", (R.centerx, R.centery - 20),
                    int(64 * sz), ACCENT, bold=True, center=True, shadow=True)
            ui.text(surf, "waiting for the other racers...", (R.centerx, R.centery + 30), int(22 * sz), TEXT,
                    center=True, shadow=True)

    def draw_minimap(self, surf):
        mm = self.art.minimap
        W, H = self.app.W, self.app.H
        pos = (W - mm.get_width() - 14, H // 2 - mm.get_height() // 2) if len(self.racers) == 2 \
            else (W - mm.get_width() - 14, H - mm.get_height() - 120)
        surf.blit(mm, pos)
        sc = self.art.mm_scale
        dots = []
        for pid, st in self.remote.items():
            if pid in self.app.local_pids:
                continue
            g = self.info.get(pid, {})
            dots.append((st["x"], st["y"], g.get("color", 0), bool(st["flags"] & P.F_GHOST), False))
        for r in self.racers:
            dots.append((r.kart.x, r.kart.y, r.color, False, True))
        for x, y, col, ghost, me in dots:
            p = (int(pos[0] + 8 + x * sc), int(pos[1] + 8 + y * sc))
            c = PLAYER_COLORS[col % len(PLAYER_COLORS)]
            if me:
                pygame.draw.circle(surf, (255, 255, 255), p, 7)
            pygame.draw.circle(surf, c if not ghost else (170, 180, 255), p, 5 if not ghost else 4)

    def draw_feed(self, surf):
        now = time.time()
        self.feed = [(t, until) for t, until in self.feed if until > now]
        y = self.app.H // 2 - 70 if len(self.racers) == 2 else 130
        for t, _u in self.feed[-4:]:
            ui.text(surf, t, (self.app.W // 2, y), 20, (255, 240, 200), bold=True, center=True, shadow=True)
            y += 26 if len(self.racers) != 2 else 0
            if len(self.racers) == 2:
                break

    def draw_netstats(self, surf):
        lines = []
        for lp in self.app.locals:
            n = lp.net
            lines.append(f"P{lp.slot + 1} {lp.user}  pid={lp.pid}")
            lines.append(f"  TCP RTT: {n.rtt * 1000:.1f} ms" if n.rtt is not None else "  TCP RTT: ...")
            lines.append(f"  UDP snapshots recv: {n.udp_recv}  lost: {n.udp_lost} ({n.loss_pct:.1f}%)  "
                         f"out-of-order dropped: {n.out_of_order}")
            lines.append(f"  UDP states sent: {n.udp_sent}  seq={n.state_seq}  bytes recv: {n.udp_bytes // 1024} KB")
            lines.append(f"  local {n.local_tcp} / udp :{n.local_udp_port}  ->  server sees TCP {n.seen_tcp}, "
                         f"UDP {n.seen_udp}")
        n = self.app.primary
        lines.append(f"Server {n.server_ip}  TCP :{n.tcp_port}  UDP :{n.udp_port}   jitter {n.jitter * 1000:.1f} ms   "
                     f"interp delay {n.interp_delay * 1000:.0f} ms")
        lines.append(f"Snapshot rate: {C.SNAPSHOT_HZ} Hz   state rate: {C.CLIENT_STATE_HZ} Hz")
        lines.append(f"FPS: {self.app.clock.get_fps():.0f}")
        box = pygame.Surface((800, 22 * len(lines) + 16), pygame.SRCALPHA)
        box.fill((0, 0, 0, 170))
        surf.blit(box, (10, self.app.H - box.get_height() - 10))
        y = self.app.H - box.get_height() - 2
        for ln in lines:
            ui.text(surf, ln, (20, y), 16, (180, 255, 200))
            y += 22

    def draw_results(self, surf):
        W, H = self.app.W, self.app.H
        shade = pygame.Surface((W, H), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        surf.blit(shade, (0, 0))
        rect = pygame.Rect(0, 0, 820, 520)
        rect.center = (W // 2, H // 2)
        ui.panel(surf, rect)
        ui.text(surf, "RACE RESULTS", (rect.centerx, rect.y + 36), 40, ACCENT, bold=True, center=True)
        ui.text(surf, f"Winner: {self.results['winner']}", (rect.centerx, rect.y + 80), 26, (255, 215, 0), bold=True,
                center=True)
        cols = [("#", 50), ("Racer", 270), ("Time", 140), ("Best lap", 130), ("Pts", 70), ("", 120)]
        x = rect.x + 40
        for c, w in cols:
            ui.text(surf, c, (x, rect.y + 116), 18, MUTED, bold=True)
            x += w
        y = rect.y + 146
        for res in self.results["results"][:9]:
            local = res["pid"] in self.app.local_pids
            col = ACCENT if local else (ACCENT2 if res["ghost"] else TEXT)
            cells = [str(res["rank"]), res["name"] + ("  (you)" if local else ""), fmt_time(res["time"]),
                     fmt_time(res["best_lap"]), str(res["points"]),
                     "ghost" if res["ghost"] else ("" if res["status"] == "FINISHED" else res["status"])]
            x = rect.x + 40
            for (c, w), cell in zip(cols, cells):
                ui.text(surf, cell, (x, y), 21, BAD if cell in ("DNF", "DISCONNECTED", "LEFT") else col, bold=local)
                x += w
            y += 34
        ui.text(surf, "Press Enter to continue", (rect.centerx, rect.bottom - 34), 20, MUTED, center=True)
