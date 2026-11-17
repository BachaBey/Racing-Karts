"""The race scene: local simulation, UDP state streaming, snapshot interpolation,
split-screen Mode-7 rendering and HUD."""
import math
import random
import time

import pygame

from client import ui
from client.render.mode7 import Mode7View, TrackArt
from client.render.sprites import (KART_GROUND_Y, KART_WORLD, SpriteBank, gantry_sprite, prop_sprites,
                                   scenery_sprites, shadow_sprite, soft_dot)
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
        self.fov_k = 1.0      # field-of-view factor (widens with speed)
        self.shake = 0.0      # camera shake amplitude
        self.emit = 0.0       # particle emission timer


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
        self.prop_imgs = prop_sprites(self.track.theme)
        self.gantry_w = self.track.width + 40
        self.gantry_img, _ = gantry_sprite(self.gantry_w, self.track.theme)
        self.shadow_img = shadow_sprite()
        self.particles = []
        self._dots, self._dot_base, self._vignettes = {}, {}, {}
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
            internal = [(800, 450)]
        else:
            h = H // 2 - 2
            rects = [pygame.Rect(0, 0, W, h), pygame.Rect(0, H - h, W, h)]
            internal = [(800, int(h * 800 / W))] * 2
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
                r.shake = 7.0
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
        self.update_effects(dt)

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

    # ------------------------------------------------------------- effects (particles)
    DUST = {"grass": (150, 128, 96), "desert": (226, 196, 142), "neon": (130, 90, 210)}

    def emit(self, x, y, z, vx, vy, vz, life, color, s0, s1):
        if len(self.particles) < 260:
            self.particles.append([x, y, z, vx, vy, vz, life, life, color, s0, s1])

    def update_effects(self, dt):
        rnd = random.random
        dust = self.DUST.get(self.track.theme, (150, 128, 96))
        for r in self.racers:
            k = r.kart
            spd = abs(k.speed)
            frac = min(1.0, max(0.0, k.speed) / C.KART_BOOST_SPEED)
            fx, fy = math.cos(k.angle), math.sin(k.angle)
            rx, ry = -fy, fx
            # camera: wider FOV + slight pull-back at speed, shake on boost / hit
            target = 1.0 - 0.20 * frac - (0.06 if k.boost_t > 0 else 0.0)
            r.fov_k += (target - r.fov_k) * min(1.0, dt * 4.0)
            r.shake = max(r.shake * (1 - dt * 6), 0.0)
            if k.boost_t > 0:
                r.shake = max(r.shake, 1.6)
            if not self.go:
                continue
            r.emit += dt
            if r.emit < 1 / 45:
                continue
            r.emit = 0.0
            for side in (-1, 1):   # rear wheels
                wx = k.x - fx * 12 + rx * 11 * side
                wy = k.y - fy * 12 + ry * 11 * side
                if not k.on_road and spd > 40:
                    self.emit(wx, wy, 2, -fx * spd * 0.15 + (rnd() - .5) * 40, -fy * spd * 0.15 + (rnd() - .5) * 40,
                              18 + rnd() * 20, 0.7, dust, 10, 30)
                elif abs(k.steer) > 0.5 and spd > C.KART_MAX_SPEED * 0.7 and rnd() < 0.7:
                    self.emit(wx, wy, 2, (rnd() - .5) * 30, (rnd() - .5) * 30, 10 + rnd() * 10, 0.6,
                              (215, 215, 222), 8, 26)
            if k.boost_t > 0:
                ex, ey = k.x - fx * 25, k.y - fy * 25
                self.emit(ex, ey, 9, -fx * 120 + (rnd() - .5) * 50, -fy * 120 + (rnd() - .5) * 50, 10 + rnd() * 20,
                          0.32, (255, 150 + int(rnd() * 70), 40), 7, 2)
        alive = []
        for p in self.particles:
            p[6] -= dt
            if p[6] <= 0:
                continue
            p[0] += p[3] * dt
            p[1] += p[4] * dt
            p[2] += p[5] * dt
            p[3] *= 1 - dt * 2
            p[4] *= 1 - dt * 2
            alive.append(p)
        self.particles = alive

    def camera(self, r):
        v = r.view
        k = r.kart
        frac = min(1.0, max(0.0, k.speed) / C.KART_BOOST_SPEED)
        back = v.cam_back + 22 * frac
        ca = r.cam_angle
        cx = k.x - math.cos(ca) * back
        cy = k.y - math.sin(ca) * back
        if r.shake > 0.05:
            cx += (random.random() - 0.5) * r.shake * 2
            cy += (random.random() - 0.5) * r.shake * 2
        v.set_focal(v.base_focal * r.fov_k)
        return cx, cy, ca

    # ------------------------------------------------------------- render
    def draw(self, surf):
        surf.fill((0, 0, 0))
        for r in self.racers:
            self.draw_view(r)
            pygame.transform.smoothscale(r.surf, r.rect.size, surf.subsurface(r.rect))
            self.draw_hud(surf, r)
        self.draw_minimap(surf)
        if not self.results:
            self.draw_feed(surf)
        if self.show_net:
            self.draw_netstats(surf)
        if self.results:
            self.draw_results(surf)
        self.draw_widgets(surf)

    def _vignette(self, size):
        vg = self._vignettes.get(size)
        if vg is None:
            import numpy as np
            w, h = size
            xs = (np.arange(w) - w / 2) / (w / 2)
            ys = (np.arange(h) - h / 2) / (h / 2)
            d = np.sqrt(xs[:, None] ** 2 * 0.8 + ys[None, :] ** 2)
            a = (np.clip((d - 0.62) / 0.75, 0, 1) ** 1.6 * 150).astype(np.uint8)
            vg = pygame.Surface(size, pygame.SRCALPHA)
            vg.fill((0, 0, 0, 255))
            pygame.surfarray.pixels_alpha(vg)[:] = a
            self._vignettes[size] = vg
        return vg

    def draw_view(self, r):
        v = r.view
        k = r.kart
        cx, cy, ca = self.camera(r)
        s = r.surf
        t = time.time()
        v.render(s, cx, cy, ca, t)
        items = []
        sp = self.sprites
        proj = v.project
        # scenery (with soft shadows)
        for (x, y, var) in self.scenery:
            p = proj(cx, cy, ca, x, y)
            if p is None or p[3] > 1900 or p[3] < 70:   # skip objects right at the camera
                continue
            idx = int(var * len(self.scenery_imgs)) % len(self.scenery_imgs)
            img, wh = self.scenery_imgs[idx]
            w = img.get_width() * wh * p[2] / img.get_height()
            items.append((p[3], "img", img, ("sc", idx), w, p[0], p[1], 1.0, w * 0.8))
        # trackside props
        for (x, y, var) in self.art.props:
            p = proj(cx, cy, ca, x, y)
            if p is None or p[3] > 1300 or p[3] < 70:
                continue
            img, wh = self.prop_imgs[var]
            w = img.get_width() * wh * p[2] / img.get_height()
            items.append((p[3], "img", img, ("pr", var), w, p[0], p[1], 1.0, w * 1.1))
        # start / finish gantry
        gx, gy = self.art.gantry
        p = proj(cx, cy, ca, gx, gy)
        if p and p[3] < 2600:
            items.append((p[3], "img", self.gantry_img, ("gantry",), self.gantry_w * p[2], p[0], p[1], 1.0, 0))
        # item boxes
        frame = int(t * 8) % len(sp.box_frames)
        for i, (bx, by) in enumerate(self.track.box_positions):
            if not (self.boxes >> i) & 1:
                continue
            p = proj(cx, cy, ca, bx, by)
            if p and p[3] < 1600:
                bob = (math.sin(t * 3 + i) * 3 + 6) * p[2]
                items.append((p[3], "img", sp.box_frames[frame], ("box", frame), 40 * p[2], p[0], p[1] - bob, 0.86,
                              28 * p[2]))
        # bananas / shells
        for oid, (kind, ox, oy) in self.objs.items():
            p = proj(cx, cy, ca, ox, oy)
            if p and p[3] < 1600:
                img = {P.OBJ_BANANA: sp.banana, P.OBJ_SHELL: sp.shell, P.OBJ_HOMING: sp.homing}[kind]
                items.append((p[3], "img", img, ("obj", kind), 28 * p[2], p[0], p[1], 0.85, 0))
        # particles
        for pt in self.particles:
            p = proj(cx, cy, ca, pt[0], pt[1])
            if p is None or p[3] > 1400:
                continue
            life = pt[6] / pt[7]
            size = (pt[10] + (pt[9] - pt[10]) * life) * p[2]
            if size < 1.5:
                continue
            items.append((p[3] - 1, "pt", pt[8], None, size, p[0], p[1] - pt[2] * p[2], life))
        # karts: remote (interpolated) + local (live state)
        karts = []
        for pid, st in self.remote.items():
            if pid in self.app.local_pids:
                continue
            g = self.info.get(pid, {})
            karts.append((pid, st["x"], st["y"], st["a"], g.get("color", 0), bool(st["flags"] & P.F_GHOST),
                          bool(st["flags"] & P.F_SHIELD), g.get("name", "?"), bool(st["flags"] & P.F_BOOST),
                          st.get("speed", 0.0)))
        for o in self.racers:
            karts.append((o.lp.pid, o.kart.x, o.kart.y, o.kart.angle, o.color, False, o.shield,
                          o.lp.user if o is not r else None, o.kart.boost_t > 0, o.kart.speed))
        own = proj(cx, cy, ca, k.x, k.y)
        own_d = own[3] if own else 0.0
        for pid, x, y, a, col, ghost, shield, name, boost, spd in karts:
            p = proj(cx, cy, ca, x, y)
            if p is None or p[3] > 1900:
                continue
            if pid != r.lp.pid and p[3] < own_d - 30:
                continue   # kart behind us, between camera and player: hide it so it never covers our kart
            bearing = math.atan2(y - cy, x - cx)
            rel = wrap_angle(a - bearing)
            img = sp.kart(col, rel, ghost)
            bounce = abs(math.sin(t * 24 + pid * 1.7)) * min(1.0, abs(spd) / C.KART_MAX_SPEED) * 0.9 * p[2]
            items.append((p[3], "kart", img, (pid, ghost), KART_WORLD * p[2], p[0], p[1] - bounce, KART_GROUND_Y,
                          shield, name, ghost, boost, sp.frame_index(rel)))
        items.sort(key=lambda it: -it[0])
        font = ui.font(12, True)
        for it in items:
            _d, kind, img, key, w, sx, sy, gy = it[:8]
            if kind == "pt":
                if w < 1.5:
                    continue
                dot = self.dot_for(img, w, gy)
                s.blit(dot, dot.get_rect(center=(int(sx), int(sy))))
                continue
            if w < 2:
                continue
            if kind == "img" and it[8] > 3:
                shw = sp.scaled(self.shadow_img, it[8], ("shadow",))
                s.blit(shw, shw.get_rect(center=(int(sx), int(sy))))
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
        # speed lines at high speed / boost (drawn small, smoothed by the upscale)
        frac = max(0.0, k.speed) / C.KART_BOOST_SPEED
        if (k.boost_t > 0 or frac > 0.72) and self.go:
            n = 16 if k.boost_t > 0 else 7
            W, H = s.get_size()
            cxs, cys = W / 2, v.horizon + (H - v.horizon) * 0.35
            diag = math.hypot(W, H) / 2
            for _ in range(n):
                ang = random.uniform(0, 2 * math.pi)
                r0 = diag * random.uniform(0.55, 0.75)
                r1 = r0 + diag * random.uniform(0.15, 0.35)
                c = (235, 240, 255) if self.track.theme != "neon" else (120, 230, 255)
                pygame.draw.aaline(s, c, (cxs + math.cos(ang) * r0, cys + math.sin(ang) * r0),
                                   (cxs + math.cos(ang) * r1, cys + math.sin(ang) * r1))
        s.blit(self._vignette(s.get_size()), (0, 0))

    def dot_for(self, color, w, life):
        """Soft particle sprite: cached per colour, size bucket and fade bucket."""
        wb = max(2, int(w) // 3 * 3)
        ab = max(1, int(life * 6))
        key = (color, wb, ab)
        d = self._dots.get(key)
        if d is None:
            base = self._dot_base.get(color)
            if base is None:
                base = soft_dot(color, 48)
                self._dot_base[color] = base
            d = pygame.transform.smoothscale(base, (wb, wb))
            d.fill((255, 255, 255, int(255 * ab / 6 * 0.75)), special_flags=pygame.BLEND_RGBA_MULT)
            if len(self._dots) > 3000:
                self._dots.clear()
            self._dots[key] = d
        return d

    def draw_hud(self, surf, r):
        R = r.rect
        small = R.h < 500
        sz = 0.68 if small else 1.0
        k = r.kart
        now = time.time()
        # spin flash
        if r.hit_flash > 0:
            fl = pygame.Surface(R.size, pygame.SRCALPHA)
            fl.fill((255, 40, 40, int(110 * r.hit_flash)))
            surf.blit(fl, R.topleft)
        # top-left panel: name, lap, time
        panel = pygame.Rect(R.x + 12, R.y + 10, int(210 * sz), int(112 * sz))
        self._glass(surf, panel)
        ui.text(surf, f"P{r.lp.slot + 1}  {r.lp.user}", (panel.x + 12, panel.y + 6), int(18 * sz) + 2,
                PLAYER_COLORS[r.color % len(PLAYER_COLORS)], bold=True, shadow=True)
        ui.text(surf, f"LAP {min(r.lap, self.laps)}/{self.laps}", (panel.x + 12, panel.y + int(30 * sz)),
                int(34 * sz), TEXT, bold=True, shadow=True)
        ui.text(surf, fmt_time(r.finish_time if r.finished else self.race_clock),
                (panel.x + 12, panel.y + int(74 * sz)), int(24 * sz), (230, 236, 255), shadow=True)
        # item slot (top-center)
        box = pygame.Rect(0, 0, int(88 * sz), int(88 * sz))
        box.midtop = (R.centerx, R.y + 10)
        self._glass(surf, box.inflate(10, 10), radius=16)
        pygame.draw.rect(surf, (250, 250, 250), box.inflate(10, 10), 3, border_radius=16)
        item = r.item
        if r.item_roll > 0:
            item = 1 + int(now * 14) % 6
        if item != C.ITEM_NONE:
            ic = pygame.transform.smoothscale(self.sprites.icons[item], (int(box.w * 0.85), int(box.h * 0.85)))
            surf.blit(ic, ic.get_rect(center=box.center))
            if r.item_roll <= 0:
                label = C.ITEM_NAMES[r.item] + (f" x{r.item_count}" if r.item == C.ITEM_TRIPLE else "")
                ui.text(surf, label, (box.centerx, box.bottom + 16), int(18 * sz), TEXT, bold=True, center=True,
                        shadow=True)
        # position (bottom-right), big with a coloured glow
        if r.rank:
            col = [(255, 210, 40), (210, 220, 240), (230, 150, 70)][r.rank - 1] if r.rank <= 3 else TEXT
            rx = R.right - (210 if len(self.racers) == 2 else 28)
            y = R.bottom - int(118 * sz)
            ui.text(surf, ordinal(r.rank), (rx + 3, y + 4), int(92 * sz), (0, 0, 0), bold=True, right=True)
            ui.text(surf, ordinal(r.rank), (rx, y), int(92 * sz), col, bold=True, right=True)
        # speedometer (bottom-left)
        self._speedo(surf, (R.x + int(92 * sz), R.bottom - int(86 * sz)), int(70 * sz), k)
        if not k.on_road and self.go and not r.finished:
            ui.text(surf, "OFF ROAD", (R.centerx, R.bottom - 40), int(22 * sz), ACCENT, bold=True, center=True,
                    shadow=True)
        # start lights + countdown
        if not self.go or (self.go_time and now - self.go_time < 1.2):
            self._start_lights(surf, R, sz, now)
        if r.finished and not self.results:
            ui.text(surf, f"FINISHED  {ordinal(r.finish_rank or r.rank)}!", (R.centerx, R.centery - 20),
                    int(64 * sz), ACCENT, bold=True, center=True, shadow=True)
            ui.text(surf, "waiting for the other racers...", (R.centerx, R.centery + 30), int(22 * sz), TEXT,
                    center=True, shadow=True)

    def _glass(self, surf, rect, radius=14):
        key = ("glass", rect.size, radius)
        g = self._vignettes.get(key)
        if g is None:
            g = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(g, (8, 10, 24, 120), g.get_rect(), border_radius=radius)
            pygame.draw.rect(g, (255, 255, 255, 40), g.get_rect(), 2, border_radius=radius)
            self._vignettes[key] = g
        surf.blit(g, rect.topleft)

    def _speedo(self, surf, center, rad, k):
        key = ("speedo", rad)
        dial = self._vignettes.get(key)
        if dial is None:
            dial = pygame.Surface((rad * 2 + 8, rad * 2 + 8), pygame.SRCALPHA)
            c = (rad + 4, rad + 4)
            pygame.draw.circle(dial, (8, 10, 24, 150), c, rad + 2)
            pygame.draw.circle(dial, (255, 255, 255, 50), c, rad + 2, 2)
            for i in range(0, 11):
                a = math.radians(225 - i * 27)
                r0 = rad - (12 if i % 2 == 0 else 7)
                pygame.draw.line(dial, (230, 236, 255), (c[0] + math.cos(a) * r0, c[1] - math.sin(a) * r0),
                                 (c[0] + math.cos(a) * (rad - 3), c[1] - math.sin(a) * (rad - 3)), 2)
            self._vignettes[key] = dial
        surf.blit(dial, dial.get_rect(center=center))
        frac = max(0.0, min(1.0, abs(k.speed) / C.KART_BOOST_SPEED))
        arc_rect = pygame.Rect(0, 0, rad * 2 - 30, rad * 2 - 30)
        arc_rect.center = center
        col = (255, 140, 30) if k.boost_t > 0 else (80, 220, 120) if frac < 0.66 else (255, 210, 60)
        if frac > 0.01:
            pygame.draw.arc(surf, col, arc_rect, math.radians(225 - 270 * frac), math.radians(225), 6)
        a = math.radians(225 - 270 * frac)
        pygame.draw.line(surf, (255, 70, 60), center,
                         (center[0] + math.cos(a) * (rad - 10), center[1] - math.sin(a) * (rad - 10)), 3)
        pygame.draw.circle(surf, (230, 230, 240), center, 5)
        ui.text(surf, f"{abs(k.speed) * 0.5:.0f}", (center[0], center[1] + rad * 0.42), max(14, rad // 3), TEXT,
                bold=True, center=True, shadow=True)
        ui.text(surf, "km/h", (center[0], center[1] + rad * 0.72), max(10, rad // 6), MUTED, center=True)

    def _start_lights(self, surf, R, sz, now):
        left = self.countdown_end - now
        lit = 0 if left > 3 else (1 if left > 2 else (2 if left > 1 else 3))
        green = self.go
        w, h = int(230 * sz), int(84 * sz)
        panel = pygame.Rect(0, 0, w, h)
        panel.center = (R.centerx, R.y + int(R.h * 0.30))
        pygame.draw.rect(surf, (14, 14, 18), panel, border_radius=int(16 * sz))
        pygame.draw.rect(surf, (90, 90, 100), panel, 3, border_radius=int(16 * sz))
        for i in range(3):
            c = (panel.x + w * (i + 1) // 4, panel.centery)
            rr = int(24 * sz)
            if green:
                col = (60, 255, 110)
            elif i < lit:
                col = (255, 40, 40)
            else:
                col = (60, 20, 20)
            if green or i < lit:
                pygame.draw.circle(surf, tuple(v // 3 for v in col), c, rr + 6)
            pygame.draw.circle(surf, col, c, rr)
            pygame.draw.circle(surf, (255, 255, 255), (c[0] - rr // 3, c[1] - rr // 3), max(2, rr // 5))
        if green:
            ui.text(surf, "GO!", (R.centerx, panel.bottom + int(48 * sz)), int(90 * sz), (80, 255, 120), bold=True,
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
