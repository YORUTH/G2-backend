#!/usr/bin/env python3
"""
ZKTeco SenseFace 2A ADMS / Push Protocol Controller & Live Face Monitor
Listens on port 8088 for both ZKTeco ADMS Push protocol and a Web UI.
"""

import sys
import os
import json
import time
import subprocess
import threading
import urllib.parse
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime

# Setup safe stdout/stderr logging when running headless via pythonw
if sys.executable.lower().endswith("pythonw.exe") or sys.stdout is None:
    try:
        _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zk_server.log")
        _log_fh = open(_log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = _log_fh
        sys.stderr = _log_fh
    except Exception:
        pass

PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zk_server.pid")

def write_pid():
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

def remove_pid():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass

PORT = int(os.environ.get("ZK_PORT", 8088))
DEVICE_IP = os.environ.get("ZK_DEVICE_IP", "192.168.1.201")
PC_IP = os.environ.get("ZK_PC_IP", "192.168.1.200")

# Cloud API for pushing check-ins and device status to the deployed web app
CLOUD_API_URL = os.environ.get("ZK_CLOUD_API_URL", "https://api.jitugym.com").rstrip("/")
DEVICE_API_KEY = os.environ.get("ZK_DEVICE_API_KEY", "g2gym_zk_secret_2026")

# ── Auto check-in push ──────────────────────────────────────────────
_checkin_debounce = {}  # pin -> last push timestamp

def push_checkin_to_cloud(pin):
    """POST face check-in to the deployed API in a background thread.
    Debounces: won't re-push the same PIN within 30 seconds."""
    if not pin or pin in ("SYSTEM", "UNKNOWN", "Realtime Event"):
        return
    now = time.time()
    last = _checkin_debounce.get(pin, 0)
    if now - last < 30:
        return
    _checkin_debounce[pin] = now

    def _do_push():
        url = f"{CLOUD_API_URL}/api/members/checkin/face/{pin}/"
        try:
            req = urllib.request.Request(
                url,
                data=b'{}',
                headers={
                    "Content-Type": "application/json",
                    "X-Device-Key": DEVICE_API_KEY,
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                result = json.loads(resp.read().decode())
                name = result.get("member", pin)
                if result.get("duplicate"):
                    print(f"[CLOUD CHECKIN] {name} (PIN {pin}) — already checked in today")
                else:
                    print(f"[CLOUD CHECKIN] ✓ {name} (PIN {pin}) — checked in successfully!")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:200]
            print(f"[CLOUD CHECKIN] ✗ PIN {pin} — HTTP {e.code}: {body}")
        except Exception as e:
            print(f"[CLOUD CHECKIN] ✗ PIN {pin} — {e}")
        # Immediately trigger heartbeat sync after scan so cloud gets updated logs
        sync_heartbeat_to_cloud()

    threading.Thread(target=_do_push, daemon=True).start()

def sync_heartbeat_to_cloud():
    """Push live scanner status to cloud backend so deployed web app sees online status & events."""
    if not CLOUD_API_URL:
        return
    url = f"{CLOUD_API_URL}/api/members/scanner/status/"
    try:
        check_ping()
        payload = json.dumps({
            "ping_ok": server_state.get("ping_ok", False),
            "ping_latency_ms": server_state.get("ping_latency_ms"),
            "device_ip": DEVICE_IP,
            "pc_ip": PC_IP,
            "devices": server_state.get("devices", {}),
            "logs": server_state.get("logs", [])[:25],
            "events": server_state.get("events", [])[:15],
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Device-Key": DEVICE_API_KEY,
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            pass
    except Exception:
        pass

# Global state
server_state = {
    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "device_ip": DEVICE_IP,
    "pc_ip": PC_IP,
    "ping_ok": False,
    "ping_latency_ms": None,
    "last_ping_check": None,
    "devices": {}, # sn -> {info, last_seen, options}
    "command_queue": {}, # sn -> list of {"id": int, "cmd": str, "sent": bool, "created": str}
    "command_history": [],
    "logs": [], # list of face scan / attlog records
    "events": [], # Machine notifications: member_created, member_enrolled
    "pin_names": {}, # pin -> member full name
    "enrolled_pins": [], # list of pins with confirmed face enrollment
    "cmd_counter": 1,
    "scan_enabled": True
}

def record_machine_event(event_type, pin, name=None):
    if not pin or str(pin) in ("SYSTEM", "UNKNOWN", "Realtime Event"):
        return
    pin = str(pin).strip()
    
    # Store or lookup name
    if name:
        server_state.setdefault("pin_names", {})[pin] = name
    else:
        name = server_state.get("pin_names", {}).get(pin, "")

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # Anti-duplicate event debounce: don't record if event for same PIN occurred within the last 10 seconds
    recent_events = server_state.setdefault("events", [])
    for ev in recent_events[:15]:
        if str(ev.get("pin")) == pin:
            ev_time_str = ev.get("time")
            if ev_time_str:
                try:
                    ev_time = datetime.strptime(ev_time_str, "%Y-%m-%d %H:%M:%S")
                    if (now - ev_time).total_seconds() < 10:
                        return  # Debounce within 10 seconds
                except Exception:
                    pass

    event_id = len(recent_events) + 1
    display_name = f" ({name})" if name else ""
    msg = f"User {pin}{display_name} enrolled successfully"

    if pin not in server_state.setdefault("enrolled_pins", []):
        server_state["enrolled_pins"].append(pin)

    ev_entry = {
        "id": event_id,
        "type": event_type,
        "pin": pin,
        "name": name,
        "time": now_str,
        "message": msg
    }
    recent_events.insert(0, ev_entry)
    if len(recent_events) > 100:
        server_state["events"] = recent_events[:100]

    print(f"[MACHINE NOTIFICATION] *** {event_type.upper()}: {msg} ***")
    threading.Thread(target=sync_heartbeat_to_cloud, daemon=True).start()

_last_ping_time = 0.0
_ping_cache_interval = 10.0  # seconds

def check_ping(force=False):
    global _last_ping_time
    now = time.time()
    if not force and (now - _last_ping_time) < _ping_cache_interval:
        return server_state.get("ping_ok", False)
    _last_ping_time = now

    try:
        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "timeout": 2.0,
        }
        if sys.platform == "win32":
            cmd = ["ping", "-n", "1", "-w", "1000", DEVICE_IP]
            # CRITICAL: Prevent any console/terminal window from popping up on Windows
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = startupinfo
        else:
            cmd = ["ping", "-c", "1", "-W", "1", DEVICE_IP]

        res = subprocess.run(cmd, **kwargs)
        server_state["last_ping_check"] = datetime.now().strftime("%H:%M:%S")
        stdout = res.stdout or ""
        is_windows_error = "Destination host unreachable" in stdout or "Request timed out" in stdout
        if res.returncode == 0 and not is_windows_error and ("time=" in stdout or "time<" in stdout or "bytes=" in stdout):
            server_state["ping_ok"] = True
            for line in stdout.splitlines():
                if "time=" in line or "time<" in line:
                    try:
                        if "time<" in line:
                            server_state["ping_latency_ms"] = 0.5
                        else:
                            ms = line.split("time=")[1].split()[0].replace("ms", "")
                            server_state["ping_latency_ms"] = float(ms)
                    except:
                        server_state["ping_latency_ms"] = 0.5
                    break
        else:
            server_state["ping_ok"] = False
            server_state["ping_latency_ms"] = None
    except Exception as e:
        server_state["ping_ok"] = False

# Run initial ping check
check_ping()

DEFAULT_SN = "NYU7262300803"

def touch_device(sn, ip="192.168.1.201"):
    if not sn or sn == "UNKNOWN":
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if sn not in server_state["devices"]:
        print(f"[DEVICE CONNECTED] Detected terminal SN: {sn} from {ip}")
        server_state["devices"][sn] = {
            "sn": sn,
            "first_seen": now_str,
            "last_seen": now_str,
            "ip": ip,
            "info": {"Model": "SenseFace 2A"}
        }
        server_state["cmd_counter"] += 1
        server_state["command_queue"].setdefault(sn, []).append({
            "id": server_state["cmd_counter"],
            "cmd": "INFO",
            "sent": False,
            "created": datetime.now().strftime("%H:%M:%S")
        })
    else:
        server_state["devices"][sn]["last_seen"] = now_str
        server_state["devices"][sn]["ip"] = ip

def get_target_sn(requested_sn=None):
    if requested_sn and requested_sn != "UNKNOWN":
        return requested_sn
    if server_state["devices"]:
        return list(server_state["devices"].keys())[0]
    return DEFAULT_SN

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class ZKHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Clean custom logging - filter out frequent polling
        if len(args) > 0 and "/api/status" in str(args[0]):
            return
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]} - {args[1]}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, X-Device-Key, Accept")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. ZKTeco ADMS handshake endpoint
        if path == "/iclock/cdata":
            self.handle_adms_cdata_get(query)
            return

        # 1b. ZKTeco ADMS registry endpoint
        if path == "/iclock/registry":
            self.handle_adms_registry(query, "")
            return

        # 1c. ZKTeco ADMS ping / querydata endpoint
        if path == "/iclock/ping" or path == "/iclock/querydata":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK\n")
            return

        # 2. ZKTeco ADMS command polling endpoint
        if path == "/iclock/getrequest":
            self.handle_adms_getrequest(query)
            return

        # 3. Web Dashboard REST API: Status
        if path == "/api/status":
            check_ping()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(server_state, indent=2).encode("utf-8"))
            return

        # 3b. Web Dashboard REST API: Reload Server
        if path == "/api/reload":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"ok": true, "msg": "Reloading zk_server..."}')
            def _restart():
                time.sleep(0.5)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            import threading
            threading.Thread(target=_restart, daemon=True).start()
            return

        # 3c. Web Dashboard REST API: Test Event Notification Trigger
        if path == "/api/test_event":
            pin = query.get("pin", ["10"])[0]
            etype = query.get("type", ["member_enrolled"])[0]
            record_machine_event(etype, pin)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return

        # 4. Web Dashboard UI
        if path == "/" or path == "/index.html":
            self.serve_dashboard()
            return


        # 404 for unknown endpoints
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not Found")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8", errors="ignore") if content_len > 0 else ""

        # 0. ZKTeco ADMS device registration
        if path == "/iclock/registry":
            self.handle_adms_registry(query, body)
            return

        # 0b. ZKTeco ADMS ping & querydata
        if path == "/iclock/ping" or path == "/iclock/querydata":
            sn = query.get("SN", ["UNKNOWN"])[0]
            server_state["last_querydata"] = {"query": query, "body": body}
            print(f"[ADMS QUERYDATA from {sn}]: query={query}\n--- BODY ---\n{body}\n--- END BODY ---")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK\n")
            return

        # 1. ZKTeco ADMS attendance & face log upload
        if path == "/iclock/cdata":
            self.handle_adms_cdata_post(query, body)
            return

        # 2. ZKTeco ADMS command result execution feedback
        if path == "/iclock/devicecmd":
            self.handle_adms_devicecmd_post(query, body)
            return

        # 3. ZKTeco newer push protocol
        if path == "/iclock/push":
            self.handle_adms_push_post(query, body)
            return

        # 4. ZKTeco photo / face data upload
        if path == "/iclock/fdata":
            sn = query.get("SN", ["UNKNOWN"])[0]
            touch_device(sn, self.client_address[0])
            pin = query.get("PIN", query.get("pin", [""]))[0]
            if not pin:
                fname = query.get("filename", [""])[0] or self.headers.get("X-Filename", "")
                if fname:
                    base = os.path.basename(fname).split(".")[0]
                    parts = base.split("_")
                    if parts and parts[0].isdigit():
                        pin = parts[0]
            if not pin and body:
                for line in body.splitlines():
                    if "pin=" in line.lower():
                        for token in line.split():
                            if token.lower().startswith("pin="):
                                pin = token.split("=")[1].strip()
                                break
            if pin:
                record_machine_event("member_enrolled", pin)

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK\n")
            return

        # 5. REST API: Trigger Face Enrollment / Create User Profile
        if path == "/api/enroll":
            try:
                data = json.loads(body)
                pin = str(data.get("pin", "2")).strip()
                name = str(data.get("name", f"User_{pin}")).strip()
                sn = get_target_sn(data.get("sn"))
                touch_device(sn)

                if sn:
                    # Record member_created notification event immediately
                    record_machine_event("member_created", pin, name=name)

                    # Create / update user on terminal
                    server_state["cmd_counter"] += 1
                    cmd_user = {
                        "id": server_state["cmd_counter"],
                        "cmd": f"DATA UPDATE user PIN={pin}\tName={name}\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=1",
                        "sent": False,
                        "created": datetime.now().strftime("%H:%M:%S")
                    }
                    server_state["command_queue"].setdefault(sn, []).append(cmd_user)
                    server_state["command_history"].append({
                        "id": cmd_user["id"],
                        "sn": sn,
                        "cmd": f"DATA UPDATE user PIN={pin} ({name})",
                        "status": "Queued",
                        "time": datetime.now().strftime("%H:%M:%S")
                    })
                    print(f"[ACTION] Queued User Profile creation for User {pin} ({name}) on {sn}")
                    self.send_json({
                        "ok": True,
                        "pin": pin,
                        "name": name,
                        "msg": f"Profile for User {pin} ({name}) pushed to SenseFace 2A!\n\nTo capture face:\n1. On terminal screen, tap Menu -> User Mgt -> All Users\n2. Select '{name}' and tap 'Face'\n3. Look at screen for 1 second to complete capture!\n\nOR use the 'Photo Upload' option to enroll remotely."
                    })
                else:
                    self.send_json({"ok": False, "msg": "No device connected yet"})
            except Exception as e:
                self.send_json({"ok": False, "msg": str(e)}, status=500)
            return


        # 5b. REST API: Upload Face Photo to Enroll
        if path == "/api/enroll_photo":
            try:
                data = json.loads(body)
                pin = str(data.get("pin", "2")).strip()
                name = str(data.get("name", f"User_{pin}")).strip()
                photo_b64 = data.get("photo", "")
                if "," in photo_b64:
                    photo_b64 = photo_b64.split(",", 1)[1]
                
                sn = get_target_sn(data.get("sn"))
                touch_device(sn)

                if sn and photo_b64:
                    import base64
                    size = len(base64.b64decode(photo_b64))

                    # 1. Update user
                    server_state["cmd_counter"] += 1
                    cmd_user = {
                        "id": server_state["cmd_counter"],
                        "cmd": f"DATA UPDATE user PIN={pin}\tName={name}\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=1",
                        "sent": False,
                        "created": datetime.now().strftime("%H:%M:%S")
                    }
                    # 2. Upload Biophoto (Face template generator)
                    server_state["cmd_counter"] += 1
                    cmd_photo = {
                        "id": server_state["cmd_counter"],
                        "cmd": f"DATA UPDATE biophoto PIN={pin}\tFileName={pin}.jpg\tType=9\tSize={size}\tContent={photo_b64}\tFormat=0\tPostBackTmpFlag=0",
                        "sent": False,
                        "created": datetime.now().strftime("%H:%M:%S")
                    }
                    server_state["command_queue"].setdefault(sn, []).extend([cmd_user, cmd_photo])
                    server_state["command_history"].extend([
                        {
                            "id": cmd_user["id"],
                            "sn": sn,
                            "cmd": f"DATA UPDATE user PIN={pin} ({name})",
                            "status": "Queued",
                            "time": datetime.now().strftime("%H:%M:%S")
                        },
                        {
                            "id": cmd_photo["id"],
                            "sn": sn,
                            "cmd": f"Upload Face Photo User {pin} ({name})",
                            "status": "Queued",
                            "time": datetime.now().strftime("%H:%M:%S")
                        }
                    ])
                    print(f"[ACTION] Queued Photo Face Enrollment for User {pin} ({name}) on {sn} ({size} bytes)")
                    self.send_json({"ok": True, "msg": f"Face photo uploaded for User {pin}! The terminal is extracting the facial template."})
                else:
                    self.send_json({"ok": False, "msg": "Missing photo or device"})
            except Exception as e:
                self.send_json({"ok": False, "msg": str(e)}, status=500)
            return

        # 6. REST API: Trigger Verification Pulse / Access Unlock
        if path == "/api/verify":
            try:
                data = json.loads(body) if body else {}
                sn = get_target_sn(data.get("sn"))
                touch_device(sn)

                if sn:
                    server_state["cmd_counter"] += 1
                    cmd_item = {
                        "id": server_state["cmd_counter"],
                        "cmd": "AC_UNLOCK",
                        "sent": False,
                        "created": datetime.now().strftime("%H:%M:%S")
                    }
                    server_state["command_queue"].setdefault(sn, []).append(cmd_item)
                    server_state["command_history"].append({
                        "id": cmd_item["id"],
                        "sn": sn,
                        "cmd": "AC_UNLOCK (Test Verification / Open Door)",
                        "status": "Queued",
                        "time": datetime.now().strftime("%H:%M:%S")
                    })
                    print(f"[ACTION] Queued Verification / AC_UNLOCK on {sn}")
                    self.send_json({"ok": True, "msg": "Verification / Access pulse sent to SenseFace 2A!"})
                else:
                    self.send_json({"ok": False, "msg": "No device connected yet"})
            except Exception as e:
                self.send_json({"ok": False, "msg": str(e)}, status=500)
            return

        # 7. REST API: Queue generic command
        if path == "/api/send_command":
            try:
                data = json.loads(body)
                sn = get_target_sn(data.get("sn"))
                touch_device(sn)
                cmd_text = data.get("command", "INFO")

                if sn:
                    server_state["cmd_counter"] += 1
                    cmd_id = server_state["cmd_counter"]
                    item = {
                        "id": cmd_id,
                        "cmd": cmd_text,
                        "sent": False,
                        "created": datetime.now().strftime("%H:%M:%S")
                    }
                    server_state["command_queue"].setdefault(sn, []).append(item)
                    server_state["command_history"].append({
                        "id": cmd_id,
                        "sn": sn,
                        "cmd": cmd_text,
                        "status": "Queued",
                        "time": datetime.now().strftime("%H:%M:%S")
                    })
                    self.send_json({"ok": True, "msg": f"Command {cmd_text} queued as #{cmd_id}"})
                else:
                    self.send_json({"ok": False, "msg": "No device connected yet"})
            except Exception as e:
                self.send_json({"ok": False, "msg": str(e)}, status=500)
            return

        # 8. REST API: Trigger Ping Check
        if path == "/api/ping":
            check_ping()
            self.send_json({
                "ok": True,
                "ping_ok": server_state["ping_ok"],
                "latency_ms": server_state["ping_latency_ms"]
            })
        # 9. REST API: Toggle Facial Scanner (Enable/Disable 24/7 scanning)
        if path == "/api/toggle_scan":
            try:
                data = json.loads(body) if body else {}
                enable = bool(data.get("enabled", True))
                sn = data.get("sn")
                if not sn and server_state["devices"]:
                    sn = list(server_state["devices"].keys())[0]

                if sn:
                    server_state["scan_enabled"] = enable
                    cmd_val = "0" if enable else "1"
                    server_state["cmd_counter"] += 1
                    cmd_id = server_state["cmd_counter"]
                    cmd_text = f"SET OPTION StopAllVerify={cmd_val}"
                    server_state["command_queue"].setdefault(sn, []).append({
                        "id": cmd_id,
                        "cmd": cmd_text,
                        "sent": False,
                        "created": datetime.now().strftime("%H:%M:%S")
                    })
                    server_state["command_history"].append({
                        "id": cmd_id,
                        "sn": sn,
                        "cmd": f"Scanner {'ACTIVATED' if enable else 'PAUSED'} (StopAllVerify={cmd_val})",
                        "status": "Queued",
                        "time": datetime.now().strftime("%H:%M:%S")
                    })
                    status_text = "ENABLED (Scanning active)" if enable else "PAUSED (Passersby ignored)"
                    print(f"[ACTION] Facial Scanner {status_text} on {sn}")
                    self.send_json({"ok": True, "enabled": enable, "msg": f"Facial scanner set to {status_text}!"})
                else:
                    self.send_json({"ok": False, "msg": "No device connected yet"})
            except Exception as e:
                self.send_json({"ok": False, "msg": str(e)}, status=500)
            return

        self.send_response(404)
        self.end_headers()

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def handle_adms_registry(self, query, body):
        """Device registration endpoint (/iclock/registry)"""
        sn = query.get("SN", ["UNKNOWN"])[0]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[ADMS REGISTRY from {sn}]: {body[:150]}")
        
        if sn not in server_state["devices"]:
            server_state["devices"][sn] = {
                "sn": sn,
                "first_seen": now_str,
                "last_seen": now_str,
                "ip": self.client_address[0],
                "info": {"Model": "SenseFace 2A"}
            }
        else:
            server_state["devices"][sn]["last_seen"] = now_str
            server_state["devices"][sn]["ip"] = self.client_address[0]

        # Extract device parameters
        dev_info = server_state["devices"][sn].setdefault("info", {})
        if body:
            items = body.replace("\n", ",").split(",")
            for item in items:
                if "=" in item:
                    k, v = item.split("=", 1)
                    dev_info[k.strip().lstrip("~")] = v.strip()

        # Queue INFO and CHECK commands
        server_state["cmd_counter"] += 1
        server_state["command_queue"].setdefault(sn, []).append({
            "id": server_state["cmd_counter"],
            "cmd": "INFO",
            "sent": False,
            "created": datetime.now().strftime("%H:%M:%S")
        })

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"RegistryCode=OK\n")

    def handle_adms_cdata_get(self, query):
        """Device handshake / check-in"""
        sn = query.get("SN", ["UNKNOWN"])[0]
        pushver = query.get("pushver", [""])[0]
        options = query.get("options", [""])[0]
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if sn not in server_state["devices"]:
            server_state["devices"][sn] = {
                "sn": sn,
                "first_seen": now_str,
                "last_seen": now_str,
                "pushver": pushver,
                "ip": self.client_address[0],
                "info": {
                    "Model": "SenseFace 2A",
                    "PushVersion": pushver
                }
            }
            # Automatically queue INFO and CHECK commands
            server_state["cmd_counter"] += 1
            info_cmd = {
                "id": server_state["cmd_counter"],
                "cmd": "INFO",
                "sent": False,
                "created": datetime.now().strftime("%H:%M:%S")
            }
            server_state["command_queue"][sn] = [info_cmd]
        else:
            server_state["devices"][sn]["last_seen"] = now_str
            server_state["devices"][sn]["ip"] = self.client_address[0]
            if pushver:
                server_state["devices"][sn]["pushver"] = pushver

        # Standard ZKTeco ADMS configuration response
        resp_lines = [
            f"GET OPTION FROM: {sn}",
            "Stamp=0",
            "OpStamp=0",
            "PhotoStamp=0",
            "ErrorDelay=30",
            "Delay=2",
            f"DateTime={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "TransTimes=00:00 23:59",
            "TransInterval=1",
            "TransFlag=111111111111",
            "TimeZone=3",
            "Realtime=1",
            "Encrypt=0",
            "ServerVersion=3.1.2",
            "SupportPing=1",
            "PushOptionsFlag=1",
            "RegistryCode=OK",
            ""
        ]
        resp_text = "\n".join(resp_lines)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(resp_text.encode("utf-8"))

    def handle_adms_getrequest(self, query):
        """Device long-polling for queued commands"""
        sn = query.get("SN", ["UNKNOWN"])[0]
        touch_device(sn, self.client_address[0])

        # Check if there is a pending command for this device
        queue = server_state["command_queue"].get(sn, [])
        unsent = [c for c in queue if not c["sent"]]

        if unsent:
            cmd_item = unsent[0]
            cmd_item["sent"] = True
            cmd_str = f"C:{cmd_item['id']}:{cmd_item['cmd']}\n"
            print(f"[ADMS] Dispatching command to {sn}: {cmd_str.strip()}")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(cmd_str.encode("utf-8"))
        else:
            # No command pending
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK\n")

    def handle_adms_cdata_post(self, query, body):
        """Device uploads punch logs (face scans) or operations"""
        sn = query.get("SN", ["UNKNOWN"])[0]
        touch_device(sn, self.client_address[0])
        table = query.get("table", ["ATTLOG"])[0]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        count = 0
        lines = body.strip().split("\n")
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            count += 1
            parts = line.split("\t")
            
            # Parse key-value tokens (e.g. time=... pin=... event=...)
            kv = {}
            for p in parts:
                if "=" in p:
                    k, v = p.split("=", 1)
                    kv[k.strip().lower()] = v.strip()

            table_lower = table.lower()
            tablename_lower = query.get("tablename", [""])[0].lower()

            # 1. Ignore high-frequency hardware sensor polling state
            if table_lower == "rtstate":
                continue

            # 2. Check for face biometric enrollment upload (table=BIODATA, BIOPHOTO, or tablename=biodata/biophoto)
            if (
                table_lower in ("biophoto", "biodata", "templatev10", "biotemplate")
                or tablename_lower in ("biophoto", "biodata", "templatev10", "biotemplate")
            ):
                pin = kv.get("pin") or kv.get("PIN")
                if not pin and parts:
                    for p in parts:
                        if p.isdigit():
                            pin = p
                            break
                if pin:
                    record_machine_event("member_enrolled", pin)
                continue

            # 3. Check for user profile upload from device
            if table_lower in ("user", "userdata") or tablename_lower in ("user", "userdata"):
                pin = kv.get("pin") or kv.get("PIN")
                uname = kv.get("name") or kv.get("NAME") or ""
                if pin:
                    record_machine_event("member_created", pin, name=uname)
                continue

            # 4. Ignore other tabledata
            if table_lower == "tabledata":
                continue

            # 5. Process face scan attendance events (ATTLOG / rtlog)
            if table in ("ATTLOG", "rtlog") or "time" in kv or "pin" in kv:
                event_code = "0"
                verify_type = "15"
                if kv:
                    punch_time = kv.get("time", now_str)
                    pin = kv.get("pin", "1")
                    event_code = kv.get("event", "0")
                    verify_type = kv.get("verifytype", "15")
                    event_map = {
                        "0": "Face (Visible Light)",
                        "3": "Face (Visible Light Verified)",
                        "27": "Visible Light Face",
                        "200": "Door Open",
                        "206": "Face Detected / Realtime",
                        "255": "Unknown Face"
                    }
                    v_name = event_map.get(event_code, f"Biometric Face (Code {event_code})")
                    v_status = "Access Granted" if event_code in ("0", "3", "27") else f"Event {event_code}"
                else:
                    pin = parts[0] if len(parts) > 0 else "Unknown"
                    punch_time = parts[1] if len(parts) > 1 else now_str
                    status = parts[2] if len(parts) > 2 else "0"
                    verify_type = parts[3] if len(parts) > 3 else "15"
                    verify_map = {
                        "0": "Password",
                        "1": "Fingerprint",
                        "2": "Card",
                        "15": "Face (Visible Light)",
                        "20": "Face / Palm",
                        "25": "Face Recognition"
                    }
                    v_name = verify_map.get(verify_type, f"Biometric Type {verify_type}")
                    v_status = "Access Granted" if status in ("0", "1") else f"Code {status}"

                photo_url = None

                log_entry = {
                    "id": len(server_state["logs"]) + 1,
                    "sn": sn,
                    "pin": pin,
                    "time": punch_time,
                    "type": v_name,
                    "status": v_status,
                    "photo": photo_url,
                    "raw": line
                }
                server_state["logs"].insert(0, log_entry)
                print(f"[ADMS FACE SCAN] *** USER {pin} SCANNED FACE AT {punch_time} ({v_name}) ***")

                # Auto-push check-in to the deployed cloud API (only valid enrolled member PINs > 0)
                if pin and pin not in ("SYSTEM", "UNKNOWN", "Realtime Event", "0") and pin.isdigit() and int(pin) > 0:
                    push_checkin_to_cloud(pin)

                # If this is a verified face recognition punch and face was not verified yet
                if (event_code in ("0", "3", "27") or str(verify_type) in ("15", "20", "25")):
                    if pin and pin not in ("SYSTEM", "UNKNOWN", "Realtime Event", "0") and pin not in server_state.setdefault("face_verified_pins", []):
                        server_state["face_verified_pins"].append(pin)
                        record_machine_event("member_enrolled", pin)
            else:
                # Other operations (including OPLOG)
                if table_lower in ("oplog", "operlog") and ("10" in line or "1" in line):
                    # Check for operation log enrollment
                    for p in parts:
                        if p.isdigit() and int(p) > 0 and p != "10" and p != "1":
                            record_machine_event("member_enrolled", p)
                            break

                log_entry = {
                    "id": len(server_state["logs"]) + 1,
                    "sn": sn,
                    "pin": "SYSTEM",
                    "time": now_str,
                    "type": f"Table: {table}",
                    "status": line[:50],
                    "raw": line
                }
                server_state["logs"].insert(0, log_entry)

        # Cap logs at 500
        if len(server_state["logs"]) > 500:
            server_state["logs"] = server_state["logs"][:500]

        resp = f"OK: {count}\n" if count > 0 else "OK\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(resp.encode("utf-8"))

    def handle_adms_devicecmd_post(self, query, body):
        """Device reports back result of a server command (INFO, CHECK, etc.)"""
        sn = query.get("SN", ["UNKNOWN"])[0]
        touch_device(sn, self.client_address[0])
        print(f"[ADMS CMD RESULT from {sn}]: {body[:200]}")
        
        # Parse return payload: ID=X&Return=0&CMD=INFO...
        params = urllib.parse.parse_qs(body.replace("\n", "&"))
        cmd_id = params.get("ID", [""])[0]
        
        # Update command history
        for item in server_state["command_history"]:
            if str(item["id"]) == cmd_id:
                item["status"] = "Completed"
                item["response"] = body[:200]
                # If DATA UPDATE user completed successfully on terminal
                if "DATA UPDATE user" in item.get("cmd", "") and ("Return=0" in body or params.get("Return", [""])[0] == "0"):
                    cmd_str = item["cmd"]
                    import re
                    match = re.search(r"PIN=(\w+)(?:\s*\((.*?)\))?", cmd_str)
                    if match:
                        pin = match.group(1)
                        name = match.group(2) or ""
                        record_machine_event("member_enrolled", pin, name=name)

                # If face photo upload completed successfully on terminal
                if ("biophoto" in item.get("cmd", "").lower() or "upload face photo" in item.get("cmd", "").lower()) and ("Return=0" in body or params.get("Return", [""])[0] == "0"):
                    cmd_str = item["cmd"]
                    import re
                    match = re.search(r"(?:PIN=|User\s+)(\w+)(?:\s*\((.*?)\))?", cmd_str)
                    if match:
                        pin = match.group(1)
                        name = match.group(2) or ""
                        record_machine_event("member_enrolled", pin, name=name)

        # Extract device info if INFO command
        if sn in server_state["devices"]:
            dev_info = server_state["devices"][sn].setdefault("info", {})
            for key, val in params.items():
                if key not in ("ID", "Return", "CMD"):
                    dev_info[key] = val[0] if val else ""
            
            # Also parse lines if formatted as Key=Value
            for line in body.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    dev_info[k.strip()] = v.strip()

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK\n")

    def handle_adms_push_post(self, query, body):
        """Newer push log payload"""
        sn = query.get("SN", ["UNKNOWN"])[0]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = {
            "id": len(server_state["logs"]) + 1,
            "sn": sn,
            "pin": "Realtime Event",
            "time": now_str,
            "type": "Face/Push Event",
            "status": "Received",
            "raw": body[:100]
        }
        server_state["logs"].insert(0, log_entry)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK\n")

    def serve_dashboard(self):
        """Renders the HTML web interface"""
        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ZKTeco SenseFace 2A Terminal Controller</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: rgba(22, 30, 49, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-hover: rgba(28, 38, 62, 0.9);
      --primary: #3b82f6;
      --primary-glow: rgba(59, 130, 246, 0.35);
      --success: #10b981;
      --success-glow: rgba(16, 185, 129, 0.3);
      --warning: #f59e0b;
      --danger: #ef4444;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --accent: #8b5cf6;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      background-color: var(--bg);
      background-image: 
        radial-gradient(at 0% 0%, rgba(59, 130, 246, 0.12) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(139, 92, 246, 0.1) 0px, transparent 50%);
      color: var(--text);
      font-family: 'Outfit', sans-serif;
      min-height: 100vh;
      padding: 24px;
      line-height: 1.5;
    }

    .container {
      max-width: 1280px;
      margin: 0 auto;
    }

    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--card-border);
    }

    .logo-group {
      display: flex;
      align-items: center;
      gap: 16px;
    }

    .device-badge {
      background: linear-gradient(135deg, #2563eb, #7c3aed);
      color: white;
      font-weight: 700;
      padding: 8px 16px;
      border-radius: 10px;
      letter-spacing: 0.5px;
      box-shadow: 0 4px 15px var(--primary-glow);
    }

    .title-block h1 {
      font-size: 1.5rem;
      font-weight: 700;
      color: #fff;
    }

    .title-block p {
      font-size: 0.875rem;
      color: var(--text-muted);
    }

    .header-pills {
      display: flex;
      gap: 12px;
    }

    .pill {
      display: flex;
      align-items: center;
      gap: 8px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      padding: 6px 14px;
      border-radius: 999px;
      font-size: 0.85rem;
      backdrop-filter: blur(8px);
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
    }

    .dot.green {
      background: var(--success);
      box-shadow: 0 0 8px var(--success-glow);
    }

    .dot.yellow {
      background: var(--warning);
      box-shadow: 0 0 8px rgba(245, 158, 11, 0.4);
    }

    .grid-top {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      margin-bottom: 24px;
    }

    @media (max-width: 900px) {
      .grid-top {
        grid-template-columns: 1fr;
      }
    }

    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 20px;
      backdrop-filter: blur(12px);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25);
    }

    .card-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
      font-size: 1.1rem;
      font-weight: 600;
      color: #e2e8f0;
    }

    .stat-row {
      display: flex;
      justify-content: space-between;
      padding: 8px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      font-size: 0.9rem;
    }

    .stat-row:last-child {
      border-bottom: none;
    }

    .stat-label {
      color: var(--text-muted);
    }

    .stat-value {
      font-family: 'JetBrains Mono', monospace;
      font-weight: 500;
      color: #fff;
    }

    .tag-badge {
      background: rgba(59, 130, 246, 0.15);
      border: 1px solid rgba(59, 130, 246, 0.3);
      color: #60a5fa;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 0.75rem;
    }

    .instruction-box {
      background: rgba(30, 41, 59, 0.6);
      border: 1px solid rgba(59, 130, 246, 0.2);
      border-radius: 12px;
      padding: 16px;
      margin-top: 12px;
      font-size: 0.9rem;
    }

    .instruction-box ol {
      padding-left: 20px;
      margin-top: 8px;
    }

    .instruction-box li {
      margin-bottom: 6px;
    }

    .highlight {
      color: #38bdf8;
      font-weight: 600;
      font-family: 'JetBrains Mono', monospace;
      background: rgba(56, 189, 248, 0.1);
      padding: 1px 6px;
      border-radius: 4px;
    }

    .actions-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 16px;
    }

    button {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: white;
      border: none;
      padding: 10px 18px;
      border-radius: 10px;
      font-weight: 600;
      font-size: 0.85rem;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s ease;
      font-family: 'Outfit', sans-serif;
    }

    button:hover {
      transform: translateY(-1px);
      box-shadow: 0 4px 15px var(--primary-glow);
    }

    button.secondary {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid var(--card-border);
      color: #e2e8f0;
    }

    button.secondary:hover {
      background: rgba(255, 255, 255, 0.12);
      box-shadow: none;
    }

    .feed-section {
      margin-top: 24px;
    }

    .feed-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }

    .feed-header h2 {
      font-size: 1.25rem;
      font-weight: 700;
    }

    .log-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
    }

    .log-table th {
      text-align: left;
      padding: 12px 16px;
      color: var(--text-muted);
      border-bottom: 1px solid var(--card-border);
      font-weight: 500;
    }

    .log-table td {
      padding: 12px 16px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    }

    .log-table tr:hover td {
      background: rgba(255, 255, 255, 0.02);
    }

    .face-badge {
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #34d399;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    .empty-state {
      padding: 40px;
      text-align: center;
      color: var(--text-muted);
    }

    .pulse {
      animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    .input-group {
      margin-bottom: 14px;
    }

    .input-label {
      font-size: 0.8rem;
      color: var(--text-muted);
      margin-bottom: 6px;
      display: block;
      font-weight: 500;
    }

    input[type="text"], input[type="number"] {
      width: 100%;
      background: rgba(15, 23, 42, 0.7);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 10px 14px;
      color: #fff;
      font-family: 'Outfit', sans-serif;
      font-size: 0.9rem;
      outline: none;
      transition: all 0.2s ease;
    }

    input[type="text"]:focus, input[type="number"]:focus {
      border-color: #3b82f6;
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
    }

    .btn-enroll {
      background: linear-gradient(135deg, #8b5cf6, #6d28d9);
      box-shadow: 0 4px 15px rgba(139, 92, 246, 0.35);
      width: 100%;
      justify-content: center;
      padding: 12px;
      font-size: 0.95rem;
    }

    .btn-verify {
      background: linear-gradient(135deg, #10b981, #059669);
      box-shadow: 0 4px 15px rgba(16, 185, 129, 0.35);
      width: 100%;
      justify-content: center;
      padding: 12px;
      font-size: 0.95rem;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="logo-group">
        <div class="device-badge">SenseFace 2A</div>
        <div class="title-block">
          <h1>ZKTeco Facial Recognition Terminal Controller</h1>
          <p>Direct Ethernet Link &bull; Active ADMS Push Server on Port 8088</p>
        </div>
      </div>
      <div class="header-pills">
        <div class="pill">
          <div class="dot green" id="pingDot"></div>
          <span id="pingStatus">Ethernet: Active (0.35 ms)</span>
        </div>
        <div class="pill">
          <div class="dot green" id="admsDot"></div>
          <span id="admsStatus">Device Online: NYU7262300803</span>
        </div>
      </div>
    </header>

    <div class="grid-top">
      <!-- PANEL 1: FACE ENROLLMENT (REGISTRATION) -->
      <div class="card" style="border-top: 3px solid #8b5cf6;">
        <div class="card-title">
          <span>👤 Face Enrollment (Registration)</span>
          <span class="tag-badge" style="background: rgba(139, 92, 246, 0.15); color: #a78bfa; border-color: rgba(139, 92, 246, 0.3);">Enrollment</span>
        </div>
        
        <!-- Tab navigation -->
        <div style="display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid var(--card-border); padding-bottom: 10px;">
          <button id="tabBtnTouch" class="secondary" onclick="switchEnrollTab('touch')" style="flex: 1; justify-content: center; padding: 8px 12px; font-size: 0.85rem; border-radius: 8px; background: rgba(139, 92, 246, 0.2); border-color: rgba(139, 92, 246, 0.5); color: #fff;">
            📱 1. Touchscreen Scan
          </button>
          <button id="tabBtnPhoto" class="secondary" onclick="switchEnrollTab('photo')" style="flex: 1; justify-content: center; padding: 8px 12px; font-size: 0.85rem; border-radius: 8px;">
            📸 2. Photo Upload (Remote)
          </button>
        </div>

        <!-- TAB 1: TOUCHSCREEN SCAN -->
        <div id="tabTouch">
          <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 14px;">
            Push the user profile to SenseFace 2A memory, then use the device screen to scan their face in 2 seconds.
          </p>
          <div style="display: grid; grid-template-columns: 1fr 2fr; gap: 10px;">
            <div class="input-group">
              <label class="input-label">User PIN / ID</label>
              <input type="number" id="enrollPin" value="2" min="1" max="999999" placeholder="e.g. 2">
            </div>
            <div class="input-group">
              <label class="input-label">Full Name</label>
              <input type="text" id="enrollName" value="Alex Smith" placeholder="e.g. John Doe">
            </div>
          </div>
          <button class="btn-enroll" onclick="startEnrollment()" style="margin-top: 6px;">
            👤 Push User Profile to SenseFace 2A
          </button>

          <div class="instruction-box" style="margin-top: 14px; font-size: 0.82rem; border-color: rgba(139, 92, 246, 0.25);">
            <div style="font-weight: 600; color: #a78bfa; margin-bottom: 6px;">📋 Next Steps on Device Screen:</div>
            <ol style="padding-left: 18px; margin: 0; color: #cbd5e1; line-height: 1.6;">
              <li>Click the button above to register the profile on the device.</li>
              <li>On SenseFace 2A screen: Tap <strong>Menu (Lock icon)</strong> ➡️ <strong>User Mgt</strong> ➡️ <strong>All Users</strong>.</li>
              <li>Select your user (e.g. <strong>Alex Smith</strong>) ➡️ Tap <strong>Face</strong>.</li>
              <li>Look at the green oval frame for 1 second until it beeps <em>"Success"</em>!</li>
              <li>The face template and photo are automatically synced back to this PC!</li>
            </ol>
          </div>
        </div>

        <!-- TAB 2: PHOTO UPLOAD -->
        <div id="tabPhoto" style="display: none;">
          <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 14px;">
            SenseFace 2A Visible-Light AI extracts face templates directly from JPG portrait photos. No need to touch the scanner!
          </p>
          <div style="display: grid; grid-template-columns: 1fr 2fr; gap: 10px;">
            <div class="input-group">
              <label class="input-label">User PIN / ID</label>
              <input type="number" id="photoPin" value="2" min="1" max="999999" placeholder="e.g. 2">
            </div>
            <div class="input-group">
              <label class="input-label">Full Name</label>
              <input type="text" id="photoName" value="Alex Smith" placeholder="e.g. John Doe">
            </div>
          </div>

          <div style="margin-bottom: 14px;">
            <label class="input-label">Select Front-Facing Portrait Photo (JPG / PNG)</label>
            <div id="dropZone" style="border: 2px dashed rgba(139, 92, 246, 0.4); border-radius: 12px; padding: 18px; text-align: center; background: rgba(15, 23, 42, 0.4); cursor: pointer;" onclick="document.getElementById('faceFileInput').click()">
              <input type="file" id="faceFileInput" accept="image/jpeg,image/png" style="display: none;" onchange="previewPhoto(event)">
              <div id="uploadPrompt">
                <div style="font-size: 1.8rem; margin-bottom: 4px;">🖼️</div>
                <div style="font-weight: 500; font-size: 0.88rem; color: #cbd5e1;">Click to choose portrait photo</div>
                <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 2px;">JPG or PNG &bull; Clear front-facing face photo</div>
              </div>
              <div id="previewContainer" style="display: none; align-items: center; justify-content: center; gap: 14px;">
                <img id="photoPreviewImg" src="" style="width: 64px; height: 64px; border-radius: 50%; object-fit: cover; border: 2px solid #8b5cf6; box-shadow: 0 0 12px rgba(139, 92, 246, 0.4);">
                <div style="text-align: left;">
                  <div id="photoFileName" style="font-weight: 600; font-size: 0.85rem; color: #fff;"></div>
                  <div id="photoFileSize" style="font-size: 0.75rem; color: var(--text-muted);"></div>
                  <span style="font-size: 0.75rem; color: #a78bfa; text-decoration: underline;">Change photo</span>
                </div>
              </div>
            </div>
          </div>

          <button class="btn-enroll" onclick="uploadFacePhoto()">
            🚀 Upload Face Photo & Enroll Remotely
          </button>
        </div>
      </div>

      <!-- PANEL 2: FACE VERIFICATION & ACCESS CONTROL -->
      <div class="card" style="border-top: 3px solid #10b981;">
        <div class="card-title">
          <span>🛡️ Face Verification & Live Access</span>
          <span class="tag-badge" style="background: rgba(16, 185, 129, 0.15); color: #34d399; border-color: rgba(16, 185, 129, 0.3);">Camera Live</span>
        </div>
        <p style="font-size: 0.88rem; color: var(--text-muted); margin-bottom: 16px;">
          SenseFace 2A camera matches faces in &lt;0.3s. You can pause the scanner or tune walk-by sensitivity below.
        </p>

        <!-- Scanner Active / Pause Toggle Card -->
        <div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.2); border-radius: 10px; padding: 14px; margin-bottom: 14px;">
          <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px;">
            <div style="display: flex; align-items: center; gap: 10px;">
              <div class="dot green pulse" id="scannerDot"></div>
              <strong id="scannerStatusText" style="color: #34d399; font-size: 0.9rem;">Facial Recognition: ACTIVE</strong>
            </div>
            <button id="toggleScannerBtn" onclick="toggleScanner()" class="secondary" style="padding: 5px 12px; font-size: 0.78rem; border-radius: 6px;">
              ⏸️ Pause Scanner
            </button>
          </div>
          <p id="scannerSubText" style="font-size: 0.82rem; color: #94a3b8; margin: 0;">
            Camera actively detects faces within range. Stand in front to verify, or pause if people are walking by.
          </p>
        </div>

        <button class="btn-verify" onclick="triggerVerification()">
          🔓 Test Verification Pulse (Open Door / AC_UNLOCK)
        </button>

        <div style="display: flex; gap: 10px; margin-top: 10px;">
          <button class="secondary" style="flex: 1; justify-content: center;" onclick="sendCommand('CHECK')">
            🔄 Sync Device Logs
          </button>
          <button class="secondary" style="flex: 1; justify-content: center;" onclick="sendCommand('INFO')">
            ℹ️ Query Device Info
          </button>
        </div>

        <!-- Workplace Walk-By Settings Guide -->
        <div class="instruction-box" style="margin-top: 14px; font-size: 0.8rem; border-color: rgba(16, 185, 129, 0.25);">
          <div style="font-weight: 600; color: #34d399; margin-bottom: 4px;">🏢 Prevent Walk-By Triggers (On Device Screen):</div>
          <ul style="padding-left: 16px; margin: 0; color: #cbd5e1; line-height: 1.5;">
            <li><strong>Reduce Distance:</strong> Menu ➡️ System ➡️ Biometrics ➡️ Face ➡️ Set to <em>Near (0.5m)</em> so only people standing at arm's length trigger the camera.</li>
            <li><strong>Duplicate Delay:</strong> Menu ➡️ Access Control ➡️ <em>Repeat Verify Delay: 60s – 300s</em> to prevent repeated beeps when members linger.</li>
            <li><strong>Sleep Mode:</strong> Menu ➡️ System ➡️ Display ➡️ <em>Sleep Time: 1 min</em> to sleep camera until screen is touched.</li>
          </ul>
        </div>

        <!-- Recent Commands Mini-Log -->
        <div style="margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--card-border);">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <span style="font-size: 0.78rem; color: var(--text-muted); font-weight: 500;">Recent Terminal Commands</span>
            <span id="cmdCountBadge" style="font-size: 0.72rem; color: #60a5fa; font-family: 'JetBrains Mono', monospace;">0 cmds</span>
          </div>
          <div id="cmdHistoryList" style="max-height: 80px; overflow-y: auto; font-size: 0.78rem; font-family: 'JetBrains Mono', monospace; display: flex; flex-direction: column; gap: 4px;">
            <div style="color: var(--text-muted);">No commands sent yet</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Live Scans & Attendance Feed -->
    <div class="card feed-section">
      <div class="feed-header">
        <div>
          <h2>Real-Time Face Scans & Punch Records</h2>
          <p style="font-size: 0.85rem; color: var(--text-muted);">Events pushed live whenever a face is recognized in front of the SenseFace 2A</p>
        </div>
        <span class="pill" style="font-size: 0.75rem;">
          <span class="dot green pulse"></span>
          <span id="logCount">0 events recorded</span>
        </span>
      </div>

      <div style="overflow-x: auto;">
        <table class="log-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Time</th>
              <th>User PIN</th>
              <th>Verification Mode</th>
              <th>Status</th>
              <th>Device Serial</th>
            </tr>
          </thead>
          <tbody id="logBody">
            <tr>
              <td colspan="6" class="empty-state">
                No scan events received yet. Stand in front of the SenseFace 2A camera or tap the screen to trigger an event!
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    let selectedPhotoBase64 = null;

    function switchEnrollTab(tab) {
      const touchDiv = document.getElementById('tabTouch');
      const photoDiv = document.getElementById('tabPhoto');
      const btnTouch = document.getElementById('tabBtnTouch');
      const btnPhoto = document.getElementById('tabBtnPhoto');

      if (tab === 'touch') {
        touchDiv.style.display = 'block';
        photoDiv.style.display = 'none';
        btnTouch.style.background = 'rgba(139, 92, 246, 0.2)';
        btnTouch.style.borderColor = 'rgba(139, 92, 246, 0.5)';
        btnTouch.style.color = '#fff';
        btnPhoto.style.background = 'rgba(255, 255, 255, 0.08)';
        btnPhoto.style.borderColor = 'var(--card-border)';
        btnPhoto.style.color = '#e2e8f0';
      } else {
        touchDiv.style.display = 'none';
        photoDiv.style.display = 'block';
        btnPhoto.style.background = 'rgba(139, 92, 246, 0.2)';
        btnPhoto.style.borderColor = 'rgba(139, 92, 246, 0.5)';
        btnPhoto.style.color = '#fff';
        btnTouch.style.background = 'rgba(255, 255, 255, 0.08)';
        btnTouch.style.borderColor = 'var(--card-border)';
        btnTouch.style.color = '#e2e8f0';
      }
    }

    function previewPhoto(event) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = function(e) {
        selectedPhotoBase64 = e.target.result;
        document.getElementById('photoPreviewImg').src = selectedPhotoBase64;
        document.getElementById('photoFileName').innerText = file.name;
        document.getElementById('photoFileSize').innerText = (file.size / 1024).toFixed(1) + ' KB';
        document.getElementById('uploadPrompt').style.display = 'none';
        document.getElementById('previewContainer').style.display = 'flex';
      };
      reader.readAsDataURL(file);
    }

    async function uploadFacePhoto() {
      if (!selectedPhotoBase64) {
        alert('Please select a portrait photo first!');
        return;
      }
      const pin = document.getElementById('photoPin').value || '2';
      const name = document.getElementById('photoName').value || `User_${pin}`;
      try {
        const res = await fetch('/api/enroll_photo', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({pin, name, photo: selectedPhotoBase64})
        });
        const d = await res.json();
        alert(d.msg);
        fetchStatus();
      } catch (e) {
        alert('Error uploading photo: ' + e);
      }
    }

    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        // Update Ping
        if (data.ping_ok) {
          document.getElementById('pingDot').className = 'dot green';
          document.getElementById('pingStatus').innerText = `Ethernet: Active (${data.ping_latency_ms || 0.35} ms)`;
        } else {
          document.getElementById('pingDot').className = 'dot yellow';
          document.getElementById('pingStatus').innerText = 'Ethernet: Checking...';
        }

        // Update Devices
        const sns = Object.keys(data.devices || {});
        if (sns.length > 0) {
          const sn = sns[0];
          document.getElementById('admsDot').className = 'dot green';
          document.getElementById('admsStatus').innerText = `Device Online: ${sn}`;
        }

        // Update Command History
        const cmds = data.command_history || [];
        document.getElementById('cmdCountBadge').innerText = `${cmds.length} cmds`;
        const cmdList = document.getElementById('cmdHistoryList');
        if (cmds.length > 0) {
          cmdList.innerHTML = cmds.slice(-4).reverse().map(c => `
            <div style="display: flex; justify-content: space-between; padding: 2px 0;">
              <span style="color: #cbd5e1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 220px;">#${c.id} ${c.cmd}</span>
              <span style="color: ${c.status.includes('Completed') || c.status.includes('0') ? '#34d399' : '#f59e0b'};">${c.status}</span>
            </div>
          `).join('');
        }

        // Update Logs
        const logs = data.logs || [];
        document.getElementById('logCount').innerText = `${logs.length} events recorded`;
        const logBody = document.getElementById('logBody');
        
        if (logs.length > 0) {
          logBody.innerHTML = logs.map(l => `
            <tr>
              <td><strong>#${l.id}</strong></td>
              <td style="font-family: 'JetBrains Mono', monospace;">${l.time}</td>
              <td><span style="font-weight: 600; color: #60a5fa;">User ${l.pin}</span></td>
              <td><span class="face-badge">&#x1F464; ${l.type}</span></td>
              <td><span style="color: #34d399; font-weight: 500;">${l.status}</span></td>
              <td style="font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; color: #94a3b8;">${l.sn}</td>
            </tr>
          `).join('');
        }

        // Update Scanner Status
        if (data.scan_enabled !== undefined) {
          isScannerActive = data.scan_enabled;
          const sDot = document.getElementById('scannerDot');
          const sText = document.getElementById('scannerStatusText');
          const sBtn = document.getElementById('toggleScannerBtn');
          const sSub = document.getElementById('scannerSubText');
          if (sDot && sText && sBtn && sSub) {
            if (isScannerActive) {
              sDot.className = 'dot green pulse';
              sText.innerText = 'Facial Recognition: ACTIVE';
              sText.style.color = '#34d399';
              sBtn.innerText = '⏸️ Pause Scanner';
              sBtn.style.color = '#f59e0b';
              sSub.innerText = 'Camera actively detects faces within range. Stand in front to verify, or pause if people are walking by.';
            } else {
              sDot.className = 'dot yellow';
              sText.innerText = 'Facial Recognition: PAUSED';
              sText.style.color = '#f59e0b';
              sBtn.innerText = '▶️ Resume Scanner';
              sBtn.style.color = '#34d399';
              sSub.innerText = 'Scanner is temporarily paused. People walking past will NOT trigger the camera.';
            }
          }
        }
      } catch (e) {
        console.error(e);
      }
    }

    let isScannerActive = true;

    async function toggleScanner() {
      const nextState = !isScannerActive;
      try {
        const res = await fetch('/api/toggle_scan', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({enabled: nextState})
        });
        const d = await res.json();
        alert(d.msg);
        fetchStatus();
      } catch (e) {
        alert('Error toggling scanner: ' + e);
      }
    }

    async function startEnrollment() {
      const pin = document.getElementById('enrollPin').value || '2';
      const name = document.getElementById('enrollName').value || `User_${pin}`;
      try {
        const res = await fetch('/api/enroll', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({pin, name})
        });
        const d = await res.json();
        alert(d.msg);
        fetchStatus();
      } catch (e) {
        alert('Error: ' + e);
      }
    }

    async function triggerVerification() {
      try {
        const res = await fetch('/api/verify', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({})
        });
        const d = await res.json();
        alert(d.msg);
        fetchStatus();
      } catch (e) {
        alert('Error: ' + e);
      }
    }

    async function sendCommand(cmd) {
      try {
        const res = await fetch('/api/send_command', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({command: cmd})
        });
        const d = await res.json();
        alert(d.msg);
        fetchStatus();
      } catch (e) {
        alert('Error: ' + e);
      }
    }

    // Auto poll status every 1.5s
    fetchStatus();
    setInterval(fetchStatus, 1500);
  </script>
</body>
</html>

"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

def run():
    write_pid()
    import atexit
    atexit.register(remove_pid)

    server_address = ("0.0.0.0", PORT)
    try:
        httpd = ThreadedHTTPServer(server_address, ZKHandler)
    except OSError as e:
        # 10048: Address in use, 10013: Port already bound / permission denied on Windows
        winerror = getattr(e, "winerror", None)
        err_msg = str(e).lower()
        if winerror in (10048, 10013) or "address already in use" in err_msg or "access permissions" in err_msg:
            print(f"[ALREADY RUNNING] zk_server is already running on port {PORT} (WinError {winerror}). Exiting gracefully.")
            sys.exit(0)
        raise
    print(f"===========================================================")
    print(f"  ZKTeco SenseFace 2A ADMS Controller & Face Monitor")
    print(f"  Active on http://{PC_IP}:{PORT} and http://localhost:{PORT}")
    print(f"  SenseFace 2A Terminal IP: {DEVICE_IP}")
    print(f"  Cloud Bridge URL: {CLOUD_API_URL}")
    print(f"===========================================================")

    # Background heartbeat thread to keep deployed cloud web app in sync
    def _background_heartbeat_loop():
        time.sleep(2)
        while True:
            try:
                sync_heartbeat_to_cloud()
            except Exception:
                pass
            time.sleep(8)

    threading.Thread(target=_background_heartbeat_loop, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()

if __name__ == "__main__":
    run()
