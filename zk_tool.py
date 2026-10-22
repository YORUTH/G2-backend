#!/usr/bin/env python3
"""
ZKTeco SenseFace 2A CLI Utility
Quick command-line tool to query status, test ping, and view live face scan records.
"""

import sys
import json
import urllib.request
import urllib.error

API_URL = "http://localhost:8088"

def get_status():
    try:
        with urllib.request.urlopen(f"{API_URL}/api/status", timeout=2) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"Error connecting to local ZKTeco server on port 8088: {e}")
        sys.exit(1)

def send_cmd(cmd_text):
    data = json.dumps({"command": cmd_text}).encode()
    req = urllib.request.Request(f"{API_URL}/api/send_command", data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            res = json.loads(resp.read().decode())
            print(f"Server response: {res.get('msg')}")
    except Exception as e:
        print(f"Error sending command: {e}")

def main():
    if len(sys.argv) < 2:
        cmd = "status"
    else:
        cmd = sys.argv[1].lower()

    if cmd == "status":
        s = get_status()
        print("\n--- ZKTeco SenseFace 2A Status ---")
        print(f"Physical Link (Ping): {'ONLINE (' + str(s.get('ping_latency_ms')) + ' ms)' if s.get('ping_ok') else 'OFFLINE'}")
        print(f"Device IP: {s.get('device_ip')}")
        print(f"PC Ethernet IP: {s.get('pc_ip')}")
        devs = s.get("devices", {})
        if devs:
            print(f"Connected Devices: {list(devs.keys())}")
            for sn, dev in devs.items():
                print(f"  [{sn}] Last seen: {dev.get('last_seen')} | Info: {dev.get('info')}")
        else:
            print("Connected Devices: None registered yet (waiting for Cloud Server setup on device)")
        print(f"Total Recorded Events: {len(s.get('logs', []))}")
        print("----------------------------------\n")

    elif cmd == "logs":
        s = get_status()
        logs = s.get("logs", [])
        print(f"\n--- Recorded Face Scans & Punch Records ({len(logs)}) ---")
        if not logs:
            print("No records yet. Step in front of the SenseFace 2A camera to trigger a face scan!")
        for l in logs:
            print(f"[{l.get('time')}] User: {l.get('pin')} | Type: {l.get('type')} | Status: {l.get('status')} | Device: {l.get('sn')}")
        print("--------------------------------------------------------\n")

    elif cmd == "watch":
        import time
        print("\n--- Live Face Scan Stream (Press Ctrl+C to stop) ---")
        print("Watching for face scans from SenseFace 2A...")
        seen_ids = set()
        # initial fetch
        init_s = get_status()
        for l in init_s.get("logs", []):
            seen_ids.add(l.get("id"))
        
        try:
            while True:
                time.sleep(0.8)
                s = get_status()
                logs = s.get("logs", [])
                for l in reversed(logs):
                    lid = l.get("id")
                    if lid not in seen_ids:
                        seen_ids.add(lid)
                        print(f"👉 [{l.get('time')}] FACE SCANNED: User {l.get('pin')} | {l.get('type')} | Status: {l.get('status')}")
        except KeyboardInterrupt:
            print("\nStopped watching.")

    elif cmd == "enroll":
        pin = sys.argv[2] if len(sys.argv) > 2 else "2"
        name = sys.argv[3] if len(sys.argv) > 3 else f"User_{pin}"
        data = json.dumps({"pin": pin, "name": name}).encode()
        req = urllib.request.Request(f"{API_URL}/api/enroll", data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                res = json.loads(resp.read().decode())
                print(f"\n[ENROLLMENT]: {res.get('msg')}")
                print("👉 Please have the person look into the SenseFace 2A camera now!")
                print("👉 The terminal will prompt on-screen: 'Please look at camera'\n")
        except Exception as e:
            print(f"Error: {e}")

    elif cmd == "verify":
        data = json.dumps({}).encode()
        req = urllib.request.Request(f"{API_URL}/api/verify", data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                res = json.loads(resp.read().decode())
                print(f"\n[VERIFICATION]: {res.get('msg')}")
                print("👉 Access unlock pulse sent. Stand in front of device to test facial verification!\n")
        except Exception as e:
            print(f"Error: {e}")

    elif cmd == "info":
        send_cmd("INFO")

    elif cmd == "check":
        send_cmd("CHECK")

    elif cmd == "reboot":
        send_cmd("REBOOT")

    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python3 zk_tool.py [status|logs|watch|enroll <pin> <name>|verify|info|check|reboot]")

if __name__ == "__main__":
    main()
