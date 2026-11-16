"""Headless bot client - a REAL network client (TCP + UDP) driven by the AI.

Useful for testing the server and for filling seats during development:
    python tools/bot_client.py --host 127.0.0.1 --name Bot1 --start-at 2
It logs in as a guest, joins the first waiting room (or creates one), readies up,
drives the race with the AI, uses items, and re-readies after each race.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.net import NetClient  # noqa: E402
from common import config as C  # noqa: E402
from common import protocol as P  # noqa: E402
from common.physics import AIDriver, KartPhysics  # noqa: E402
from common.track import Track  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--tcp", type=int, default=C.TCP_PORT)
    ap.add_argument("--udp", type=int, default=C.UDP_PORT)
    ap.add_argument("--name", default="Bot")
    ap.add_argument("--pw", default="", help="log in with this password (registered account) instead of guest")
    ap.add_argument("--skill", type=float, default=0.9)
    ap.add_argument("--start-at", type=int, default=0, help="if host: start when this many players are in")
    ap.add_argument("--ghosts", type=int, default=0)
    ap.add_argument("--track", default="sunny")
    ap.add_argument("--laps", type=int, default=1)
    ap.add_argument("--races", type=int, default=0, help="quit after N races (0 = forever)")
    ap.add_argument("--crash-after", type=float, default=0,
                    help="simulate a crash: kill the process N seconds after GO (tests disconnection)")
    args = ap.parse_args()

    if ":" in args.host:
        host, tcp, udp = P.parse_address(args.host, args.tcp, args.udp)
    else:
        host, tcp, udp = args.host, args.tcp, args.udp
    net = NetClient(host, tcp, udp)
    net.connect()
    if args.pw:
        net.send(P.C_LOGIN, user=args.name, pw=args.pw)
    else:
        net.send(P.C_LOGIN, user=args.name, guest=True)
    room = None
    kart = None
    ai = None
    track = None
    racing = False
    go_time = None
    races = 0
    last_send = 0
    last_tick = time.time()
    while True:
        now = time.time()
        dt = now - last_tick
        last_tick = now
        net.heartbeat()
        for m in net.poll():
            t = m["type"]
            if t == "_DISCONNECTED":
                print("disconnected:", m["reason"])
                return
            if t == P.S_LOGIN_OK:
                print("logged in as", m["user"], "pid", m["pid"])
            elif t == P.S_ROOM_LIST and room is None:
                waiting = [r for r in m["rooms"] if r["status"] == "WAITING" and r["players"] < r["max"]]
                if waiting:
                    net.send(P.C_JOIN_ROOM, room_id=waiting[0]["id"])
                else:
                    net.send(P.C_CREATE_ROOM, name=f"{args.name}'s room", track=args.track, laps=args.laps,
                             ghosts=args.ghosts)
                room = "pending"
            elif t == P.S_ROOM_STATE:
                room = m["room"]
                me = next((x for x in room["members"] if x["pid"] == net.pid), None)
                if me and not me["ready"] and room["status"] == "WAITING":
                    net.send(P.C_SET_READY, ready=True)
                if (room["host_pid"] == net.pid and args.start_at and room["status"] == "WAITING"
                        and len(room["members"]) >= args.start_at and all(x["ready"] for x in room["members"])):
                    net.send(P.C_START_GAME)
            elif t == P.S_RACE_START:
                track = Track(m["track"])
                g = next(x for x in m["grid"] if x["pid"] == net.pid)
                kart = KartPhysics(g["x"], g["y"], g["a"])
                ai = AIDriver(track, args.skill, lane=(net.pid % 3 - 1) * 0.12)
                racing = False
                print("race starting on", track.title)
            elif t == P.S_GO:
                racing = True
                go_time = now
            elif t == P.S_ITEM_GOT:
                if m["item"] != C.ITEM_NONE:
                    net.send(P.C_USE_ITEM)
            elif t == P.S_EFFECT:
                if m["kind"] == "spin":
                    kart.spin()
                elif m["kind"] == "boost":
                    kart.boost()
            elif t == P.S_EVENT:
                print("event:", m["text"])
            elif t == P.S_RACE_RESULTS:
                racing = False
                races += 1
                print("results:", [(r["rank"], r["name"], r["time"], r["status"]) for r in m["results"]])
                if args.races and races >= args.races:
                    net.send(P.C_LOGOUT)
                    time.sleep(0.2)
                    net.close()
                    return
            elif t == P.S_ERROR:
                print("error:", m["msg"])
        if not net.alive:
            return
        if racing and kart:
            if args.crash_after and now - go_time > args.crash_after:
                print("simulating crash (process exit without closing sockets)")
                os._exit(1)
            th, br, st = ai.control(kart)
            kart.step(min(dt, 0.05), th, br, st, track, speed_factor=args.skill)
            if now - last_send >= 1.0 / C.CLIENT_STATE_HZ:
                last_send = now
                flags = (P.F_BOOST if kart.boost_t > 0 else 0) | (P.F_SPIN if kart.spin_t > 0 else 0)
                net.send_state(kart.x, kart.y, kart.angle, kart.speed, flags)
        time.sleep(1 / 60)


if __name__ == "__main__":
    main()
