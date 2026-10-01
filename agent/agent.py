#!/usr/bin/env python3
"""
Remote-Fix Agent (PC uchun). Faqat standart kutubxona, pip kerak emas.
Relay dan buyruq oladi, terminalda ko'rsatadi, tasdiqlansa bajaradi va natijani qaytaradi.
To'xtatish (kill switch): Ctrl+C.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
MAX_OUTPUT = 100_000


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(BASE / ".env")
RELAY_URL = os.environ.get("RELAY_URL", "").rstrip("/")
TOKEN = os.environ.get("AGENT_TOKEN", "")
AUTO_READONLY = os.environ.get("AUTO_READONLY", "1") == "1"
LOG_FILE = BASE / "agent.log"

if not RELAY_URL or not TOKEN:
    sys.exit("RELAY_URL va AGENT_TOKEN ni .env ga yozing")
if not RELAY_URL.startswith("https://") and not re.match(r"http://(localhost|127\.0\.0\.1)", RELAY_URL):
    sys.exit("RELAY_URL https:// bilan boshlanishi shart")

# ---- xavfsiz (faqat o'qish) buyruqlar ro'yxati ----
SIMPLE_OK = {
    "uname", "uptime", "whoami", "hostname", "hostnamectl", "pwd", "df", "free", "lsblk",
    "lsusb", "lspci", "lscpu", "lsmod", "id", "date", "ps", "ls", "sensors", "lsb_release",
}
SUBCMD_OK = {
    "ip": {"a", "addr", "address", "r", "route", "l", "link"},
    "systemctl": {"status", "is-active", "is-enabled", "list-units", "--failed"},
    "dpkg": {"-l", "--audit"},
    "journalctl": None,  # istalgan argument, lekin pipe/yo'naltirishsiz
}
SHELL_META = re.compile(r"[;&|<>`$\n\\(){}]")


def is_readonly(cmd: str) -> bool:
    if SHELL_META.search(cmd):
        return False
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if not parts:
        return False
    name = parts[0]
    if name in SIMPLE_OK:
        return True
    if name in SUBCMD_OK:
        allowed = SUBCMD_OK[name]
        return allowed is None or (len(parts) > 1 and parts[1] in allowed)
    return False


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def http(method: str, path: str, body=None, timeout: int = 15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        RELAY_URL + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


def trim(s: str) -> str:
    if len(s) <= MAX_OUTPUT:
        return s
    h, t = int(MAX_OUTPUT * 0.6), int(MAX_OUTPUT * 0.4)
    return s[:h] + f"\n...[{len(s) - MAX_OUTPUT} belgi qisqartirildi]...\n" + s[-t:]


def run(cmd: str, timeout: int, use_shell: bool):
    p = None
    try:
        p = subprocess.Popen(
            cmd if use_shell else shlex.split(cmd),
            shell=use_shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out or ""
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        return 124, (out or "") + f"\n[TIMEOUT: {timeout}s dan keyin to'xtatildi]"
    except FileNotFoundError as e:
        return 127, f"Buyruq topilmadi: {e}"
    except Exception as e:  # noqa: BLE001
        return 1, f"Bajarishda xato: {e}"


def post_result(cid: str, status: str, code, output: str) -> None:
    body = {"status": status, "exit_code": code, "output": trim(output)}
    for i in range(3):
        try:
            http("POST", f"/result/{cid}", body)
            return
        except Exception as e:  # noqa: BLE001
            log(f"post_result xato ({i + 1}/3): {e}")
            time.sleep(2)


def ask(cmd: str) -> str:
    print("\n" + "=" * 60)
    print("YANGI BUYRUQ:")
    print(f"  {cmd}")
    print("=" * 60)
    while True:
        try:
            a = input("Bajarilsinmi? [y]a / [n]o / [q]uit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "q"
        if a in ("y", "n", "q"):
            return a


def handle(job: dict) -> None:
    cid, cmd, timeout = job["id"], job["command"], int(job.get("timeout", 60))
    if AUTO_READONLY and is_readonly(cmd):
        print(f"\n[auto-readonly] $ {cmd}")
        log(f"AUTO {cid}: {cmd}")
        code, out = run(cmd, timeout, use_shell=False)
        post_result(cid, "done", code, out)
        print(f"  -> yakunlandi (kod {code})")
        return

    a = ask(cmd)
    if a == "q":
        post_result(cid, "rejected", None, "agent to'xtatildi (quit)")
        log("QUIT")
        print("Agent to'xtatildi.")
        sys.exit(0)
    if a == "n":
        post_result(cid, "rejected", None, "egasi rad etdi")
        log(f"REJECT {cid}: {cmd}")
        return
    log(f"RUN {cid}: {cmd}")
    code, out = run(cmd, timeout, use_shell=True)
    post_result(cid, "done", code, out)
    print(f"  -> yakunlandi (kod {code})")


def main() -> None:
    print(f"Agent ishga tushdi -> {RELAY_URL}")
    print(f"AUTO_READONLY={'yoqilgan' if AUTO_READONLY else 'o`chiq'} | to'xtatish: Ctrl+C")
    log("agent started")
    backoff = 3
    while True:
        try:
            job = http("GET", "/next?wait=20", timeout=35)
            backoff = 3
            if job:
                handle(job)
        except KeyboardInterrupt:
            print("\nAgent to'xtatildi.")
            log("agent stopped (Ctrl+C)")
            return
        except urllib.error.HTTPError as e:
            log(f"HTTP {e.code}")
            if e.code == 401:
                sys.exit("401: AGENT_TOKEN noto'g'ri")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except Exception as e:  # noqa: BLE001
            log(f"ulanish xatosi: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    main()
