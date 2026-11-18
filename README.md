# Turbo Kart Arena

Competitive online kart racing (CS411 Computer Networks, Project 2).
A pseudo-3D Mode-7 racer with item boxes and abilities, built on **raw TCP and UDP sockets**.

* 1 server, any number of clients, up to 6 racers per room
* **Split-screen**: 2 players on one PC, both playing online
* **Ghosts**: race alone against saved runs of other players (or CPU runs)
* Accounts, lobby, rooms, ready/start, real-time racing, items, rankings, persistent leaderboard
* Detects disconnections (crash, closed game, lost Wi-Fi) without crashing

Architecture, protocol justification and diagrams: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Just want to play? (Windows, nothing to install)

1. Download **[TurboKartArena.exe](https://github.com/BachaBey/Racing-Karts/raw/main/release/TurboKartArena.exe)** (about 30 MB).
2. Double-click it. If Windows SmartScreen warns, click **More info → Run anyway**: the app is not code-signed.
3. The server address is pre-filled with our cloud server (**74.148.56.100**, Azure, Austria East). Type a username and click **LOGIN**. To play on a local server instead, type its IP or click **Scan LAN**.

To rebuild the exe yourself: `pip install pyinstaller` then `python tools/build_exe.py`.

---

## 1. Install (every PC)

Python 3.10+ is required (tested on 3.12).

```bash
pip install -r requirements.txt
```

The server only uses the Python standard library. Clients need `pygame` and `numpy`.

## 2. Run

### Server (PC 1)
```bash
python run_server.py
```
It prints the IP addresses clients can use, e.g. `clients can connect to 192.168.1.20`.
Options: `--tcp 5555 --udp 5556 --name "Lab server" --data data`

**Windows firewall** (run once on the server PC, in an *administrator* terminal):
```bat
netsh advfirewall firewall add rule name="KartArena TCP" dir=in action=allow protocol=TCP localport=5555
netsh advfirewall firewall add rule name="KartArena UDP" dir=in action=allow protocol=UDP localport=5556-5557
```
You can also click *Allow* on both private and public networks when Windows asks the first time.

### Clients (PCs 2-4)
```bash
python run_client.py                    # or: python run_client.py --host 192.168.1.20
```
1. Type the server IP, or click **Scan LAN** to find it automatically (UDP broadcast).
2. Enter a username. Either **Register** with a password (stats and ghosts are saved), or leave the password empty to play as a guest.
3. For split-screen, tick **Add local Player 2** and fill in their name before logging in.
4. In the lobby, **create a room** (track, laps, number of ghost opponents) or double-click a room to **join** it.
5. Everybody clicks **READY**, then the host clicks **START RACE**.

## 3. Controls

AZERTY layout (ZQSD). On a QWERTY keyboard WASD works too.

| | Solo | Split-screen P1 | Split-screen P2 |
|---|---|---|---|
| Accelerate / brake | Z/S or ↑/↓ | Z / S | ↑ / ↓ |
| Steer | Q/D or ←/→ | Q / D | ← / → |
| Use item | Space / Enter | Space / Left Shift | Enter / Right Ctrl |

Other keys: **F3** shows network stats (RTT, UDP loss, packet counts), **Esc twice** leaves the race, **F11** toggles fullscreen.

**Items** (from the `?` boxes; worse positions get better items):
Turbo, 3x Turbo, Banana (dropped trap), Shell (fired straight ahead), Seeker (homes in on the player ahead of you), Shield (blocks one hit).
Orange chevron pads on the track give a short boost. Driving on grass or sand slows you down.

## 4. Demo-day checklist (4 PCs)

1. All PCs on the same network (hotspot or Wi-Fi). For the bonus, the server is on the cloud VM and the clients are on different networks (see below).
2. PC 1: `python run_server.py`, and keep the console visible. It logs every login, join, finish and disconnection.
3. PCs 2-4: `python run_client.py`, then Scan LAN or type the IP. Log in.
4. One client creates a room and the others join. Ready, then start. Race.
5. Show the F3 overlay (RTT, UDP loss, sequence numbers).
6. Disconnection demo: close a client window, or turn its Wi-Fi off, during a race. The server logs it ("connection reset" or "heartbeat timeout") and the others see "X disconnected". The race continues and the results show DISCONNECTED.
7. Split-screen demo: on one PC, log in with 2 players and race against the others.
8. Ghost demo: create a room with 1-3 ghost opponents and start alone.

## 5. Cross-network play (bonus): cloud server

The server runs on a **cloud VM with a public IP**. Clients connect from any network (university Wi-Fi, 4G hotspot, home) and install nothing extra.
**Step-by-step guide: [docs/CLOUD_DEPLOY.md](docs/CLOUD_DEPLOY.md)**

1. Create an Ubuntu VM (e.g. Azure for Students, B1s). Allow inbound **TCP 5555** and **UDP 5556** in its cloud firewall.
2. Deploy from Windows with one command:
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\upload.ps1 -Target azureuser@<VM-IP> -Key C:\path\key.pem
   ```
   This installs the server as a systemd service. Alternatives: `sudo bash deploy/setup_server.sh` on the VM, or the `Dockerfile`.
3. Check each client network: `python tools/netcheck.py <VM-IP>`
4. Clients run `python run_client.py --host <VM-IP>`. The address field also accepts `host:tcpport:udpport`.

In the lobby status bar and the F3 overlay, each client shows its private address next to the **public `ip:port` the server sees**. That's the NAT translation, visible live.

## 6. Development tools

Headless bot client, a real TCP+UDP client driven by the AI, useful for testing or filling seats:
```bash
python tools/bot_client.py --host 127.0.0.1 --name Bot1
python tools/bot_client.py --name Crashy --crash-after 8       # simulates a crash 8 s into the race
python tools/bot_client.py --name Solo --start-at 1 --ghosts 2  # creates a room and races ghosts
```

Network diagnostics (no login needed): DNS, TCP reachability, TCP and UDP RTT, jitter, loss, and a verdict:
```bash
python tools/netcheck.py 20.51.33.10
```

Internet emulator: a TCP+UDP proxy that adds delay, jitter and loss, to rehearse the cross-network demo locally:
```bash
python tools/netem_proxy.py --server 127.0.0.1 --listen 7555 --delay 70 --jitter 25 --loss 8
python run_client.py --host 127.0.0.1:7555
```

## 7. Project layout

```
run_server.py         server entry point
run_client.py         client entry point
common/               shared by both sides
  config.py           ports, tick rates, game constants
  protocol.py         TCP JSON framing, UDP binary packet formats
  track.py            spline tracks, progress / lap computation
  physics.py          kart physics + AI driver
server/
  server.py           sockets, threads, message dispatch, heartbeats, LAN discovery
  room.py             rooms + authoritative race (items, projectiles, laps, ranking, results)
  accounts.py         registration / login (salted PBKDF2), stats, leaderboard
  ghosts.py           ghost recording / replay, CPU runs
client/
  app.py              main loop, local players, message routing
  net.py              NetClient (TCP + UDP threads), LAN discovery
  scenes.py           Connect, Lobby, Room screens
  race.py             race: prediction, interpolation, split-screen, HUD, results
  ui.py               small pygame UI toolkit
  render/mode7.py     numpy Mode-7 renderer
  render/sprites.py   procedural sprites (3D box karts, items, scenery)
tools/bot_client.py   headless AI client for tests
tools/netcheck.py     TCP/UDP reachability + RTT/jitter/loss test
tools/netem_proxy.py  delay/jitter/loss emulating proxy
deploy/               cloud VM install script, systemd unit, Windows upload script
Dockerfile            server container image
docs/ARCHITECTURE.md  architecture, protocol choices, diagrams
docs/CLOUD_DEPLOY.md  cross-network bonus: cloud deployment + NAT explanation + demo script
data/                 (created by the server) users.json, ghosts/
```

## 8. Troubleshooting

* **"Cannot reach server"**: check the IP, that the server is running, the firewall rule, and that all PCs are on the same network or VPN. Test with `ping <server-ip>`.
* **Login works but nothing moves / "UDP waiting"** in the lobby status bar: UDP is blocked. Add the UDP firewall rule on the server.
* **Scan LAN finds nothing**: some hotspots and university networks block broadcast. Type the IP manually.
* **Low FPS**: the renderer uses numpy. Close other heavy programs. Window size does not matter (fixed internal resolution).
