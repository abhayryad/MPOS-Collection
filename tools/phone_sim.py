"""Pretend to be mPOS phones talking to the portal's phone API (/api/device/*). Python standard library
only, so it runs on any PC that has Python.

    python tools/phone_sim.py pair http://localhost:8028 HD22_MPOS1 K7P2QX     pair one phone, keep its token
    python tools/phone_sim.py run http://localhost:8028 --minutes 10           run the phones paired above
    python tools/phone_sim.py run http://127.0.0.1:8029 --fake                 every phone of the fake portal
    python tools/phone_sim.py switch http://127.0.0.1:8029 global off --reason "Billing incident"

Each phone checks in every poll_seconds (the portal says 30; --poll overrides it to speed a test up),
starts a bill now and then, keeps it open for a while and ends it - exactly the calls the real app makes.
Every state change a phone is told about is printed, and at the end: calls made, errors, answer times,
and whether the rules held (no bill started unless ON, every bill that was open when its phone was
switched off allowed to finish).

`switch` works only on tools/fake_device_portal.py; on the real portal use its screens.
"""
import argparse
import http.client
import json
import random
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

APP_VERSION = "sim-1.0"
PHONES_FILE = Path("sim_phones.json")


class Api:
    """One phone's connection to the portal (kept open between calls, reopened if the portal closed it)."""

    def __init__(self, url, token=None, timeout=10):
        u = urlsplit(url)
        self._conn_args = ((u.hostname, u.port), timeout)
        self._cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        self._conn = None
        self.token = token

    def post(self, path, body):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(body).encode()
        for attempt in (1, 2):
            if self._conn is None:
                (host, port), timeout = self._conn_args
                self._conn = self._cls(host, port, timeout=timeout)
            try:
                self._conn.request("POST", path, data, headers)
                resp = self._conn.getresponse()
                return resp.status, json.loads(resp.read() or b"null")
            except (http.client.RemoteDisconnected, ConnectionError, http.client.CannotSendRequest):
                self._conn.close()
                self._conn = None
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")


# ------------------------------------------------------------------ results


class Results:
    def __init__(self):
        self._lock = threading.Lock()
        self.calls = Counter()            # endpoint -> calls
        self.errors = Counter()           # "endpoint status" -> count
        self.times = defaultdict(list)    # endpoint -> answer times in ms
        self.states = {}                  # terminal -> last state told
        self.changes = Counter()          # "ON -> DRAINING" -> count
        self.bills = Counter()            # started / refused / finished / finished_draining
        self.broken = []                  # rule breaks seen

    def call(self, endpoint, ms, status):
        with self._lock:
            self.calls[endpoint] += 1
            self.times[endpoint].append(ms)
            if status != 200:
                self.errors[f"{endpoint} {status}"] += 1

    def told(self, terminal, answer, quiet):
        state = answer["state"]
        with self._lock:
            before = self.states.get(terminal)
            self.states[terminal] = state
            if before and before != state:
                self.changes[f"{before} -> {state}"] += 1
                if not quiet:
                    print(f"{time.strftime('%H:%M:%S')}  {terminal:<14} {before} -> {state}  ({answer['message']})")

    def count(self, what):
        with self._lock:
            self.bills[what] += 1

    def rule_broken(self, text):
        with self._lock:
            self.broken.append(text)
        print(f"!! RULE BROKEN: {text}")


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p / 100))] if values else 0


# ------------------------------------------------------------------ one phone


class Phone(threading.Thread):
    def __init__(self, url, terminal, token, serial, args, results, stop, pair_code=None):
        super().__init__(name=terminal, daemon=True)
        self.api = Api(url, token)
        self.pair_code = pair_code  # no token yet: pair with this code first, as a new phone does
        self.terminal, self.serial, self.args, self.results, self.stop = terminal, serial, args, results, stop
        self.bill_open = False
        self.bill_ends_at = 0.0
        self.next_bill_at = time.monotonic() + random.uniform(0, 2 * args.bill_every)
        self.poll = args.poll or 30

    def call(self, endpoint, body):
        t0 = time.perf_counter()
        try:
            status, answer = self.api.post(f"/api/device/{endpoint}", body)
        except OSError as e:
            self.results.call(endpoint, (time.perf_counter() - t0) * 1000, type(e).__name__)
            return None
        self.results.call(endpoint, (time.perf_counter() - t0) * 1000, status)
        if status != 200:
            return None
        self.results.told(self.terminal, answer, self.args.quiet)
        if not self.args.poll:
            self.poll = answer["poll_seconds"]
        return answer

    def run(self):
        if self.api.token is None and not self.pair():
            return
        self.stop.wait(random.uniform(0, self.poll))  # phones do not all wake together
        while not self.stop.is_set():
            answer = self.call("heartbeat", {"bill_open": self.bill_open, "serial": self.serial,
                                             "app_version": APP_VERSION, "network": "wifi",
                                             "battery_pct": random.randint(20, 100)})
            now = time.monotonic()
            if answer and self.bill_open and now >= self.bill_ends_at:
                self.end_bill(answer["state"])
            elif answer and not self.bill_open and now >= self.next_bill_at:
                self.start_bill()
            wake = self.poll if not self.bill_open else max(0.0, min(self.poll, self.bill_ends_at - now))
            self.stop.wait(wake)
            if self.bill_open and time.monotonic() >= self.bill_ends_at and not self.stop.is_set():
                self.end_bill(self.results.states.get(self.terminal))

    def pair(self):
        t0 = time.perf_counter()
        try:
            status, answer = self.api.post("/api/device/pair", {"terminal": self.terminal, "code": self.pair_code,
                                                                "serial": self.serial, "app_version": APP_VERSION})
        except OSError as e:
            status, answer = type(e).__name__, None
        self.results.call("pair", (time.perf_counter() - t0) * 1000, status)
        if status != 200:
            print(f"{time.strftime('%H:%M:%S')}  {self.terminal:<14} pairing refused ({status}): {answer}")
            return False
        self.api.token = answer["token"]
        self.results.count("phones_paired")
        if not self.args.quiet:
            print(f"{time.strftime('%H:%M:%S')}  {self.terminal:<14} paired - {answer['state']}")
        return True

    def start_bill(self):
        self.next_bill_at = time.monotonic() + random.uniform(0, 2 * self.args.bill_every)
        answer = self.call("bill/start", {"bill_ref": f"SIM-{self.terminal}-{int(time.time())}"})
        if not answer:
            return
        if answer["allowed"] and answer["state"] != "ON":
            self.results.rule_broken(f"{self.terminal} allowed a bill while {answer['state']}")
        if answer["allowed"]:
            self.bill_open = True
            self.bill_ends_at = time.monotonic() + random.uniform(*self.args.bill_seconds)
            self.results.count("started")
        else:
            self.results.count("refused")

    def end_bill(self, state_before):
        answer = self.call("bill/end", {"outcome": random.choice(["paid"] * 9 + ["cancelled"])})
        if answer is None:
            return  # try again at the next wake-up; the bill is still open on the phone
        self.bill_open = False
        self.results.count("finished_draining" if state_before == "DRAINING" else "finished")
        if state_before == "DRAINING" and answer["state"] == "DRAINING":
            self.results.rule_broken(f"{self.terminal} still DRAINING after its bill ended")


# ------------------------------------------------------------------ commands


def load_phones(path):
    return json.loads(path.read_text()) if path.exists() else {}


def cmd_pair(args):
    serial = args.serial or f"SIM-{args.terminal.upper()}"
    status, answer = Api(args.url).post("/api/device/pair", {"terminal": args.terminal, "code": args.code,
                                                              "serial": serial, "app_version": APP_VERSION})
    if status != 200:
        sys.exit(f"Pairing refused ({status}): {answer.get('detail') if isinstance(answer, dict) else answer}")
    phones = load_phones(args.file)
    phones[answer["terminal_id"]] = {"url": args.url, "token": answer["token"], "serial": serial}
    args.file.write_text(json.dumps(phones, indent=2))
    print(f"Paired {answer['terminal_id']} - now {answer['state']} ({answer['message']}). Token kept in {args.file}")


def cmd_run(args):
    if args.fake:
        with urlopen(args.url.rstrip("/") + "/sim/live", timeout=10) as r:
            phones = {tid: {"token": f"sim-{tid}", "serial": f"SN-{tid}"}
                      for tid, p in json.load(r)["phones"].items() if p.get("premade", True)}
    else:
        phones = load_phones(args.file)
        if not phones:
            sys.exit(f"No paired phones in {args.file} - pair one first, or use --fake with the fake portal")
    results, stop = Results(), threading.Event()
    threads = [Phone(args.url, tid, p["token"], p["serial"], args, results, stop) for tid, p in phones.items()]
    print(f"{len(threads)} phone(s) against {args.url} for {args.minutes} min. Ctrl+C stops early.")
    for t in threads:
        t.start()
    started, end = time.monotonic(), time.monotonic() + args.minutes * 60
    running = set(phones)
    try:
        tick = 0
        while time.monotonic() < end:
            time.sleep(min(5, max(0.1, end - time.monotonic())))
            if args.fake:  # terminals added in the live view get a phone of their own
                for t in new_phones(args, running, results, stop):
                    threads.append(t)
                    t.start()
            tick += 1
            if tick % 2:
                continue
            with results._lock:
                states = Counter(results.states.values())
                calls = sum(results.calls.values())
            print(f"{time.strftime('%H:%M:%S')}  {calls / (time.monotonic() - started):6.1f} calls/s   "
                  + "  ".join(f"{k} {v}" for k, v in sorted(states.items())))
    except KeyboardInterrupt:
        print("Stopping...")
    stop.set()
    for t in threads:
        t.join(timeout=15)
    report(results, len(threads))
    return 1 if results.broken or results.errors else 0


def new_phones(args, running, results, stop):
    """Phones for terminals added on the fake portal with autopair, each pairing with its code."""
    try:
        with urlopen(args.url.rstrip("/") + "/sim/live", timeout=10) as r:
            live = json.load(r)["phones"]
    except OSError:
        return []
    fresh = [tid for tid, p in live.items() if tid not in running and p.get("autopair") and p.get("pair_code")]
    running.update(fresh)
    if fresh:
        print(f"{time.strftime('%H:%M:%S')}  {len(fresh)} new terminal(s) - pairing a phone to each")
    return [Phone(args.url, tid, None, f"SN-{tid}", args, results, stop, pair_code=live[tid]["pair_code"])
            for tid in fresh]


def report(results, phones):
    print(f"\n==== {phones} phones ====")
    print(f"{'call':<12}{'count':>8}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}")
    for ep in sorted(results.calls):
        ts = results.times[ep]
        print(f"{ep:<12}{results.calls[ep]:>8}{pct(ts, 50):>9.1f}{pct(ts, 95):>9.1f}{max(ts):>9.1f}")
    print("errors:       " + (", ".join(f"{k} x{v}" for k, v in results.errors.items()) or "none"))
    print("bills:        " + ", ".join(f"{k} {v}" for k, v in sorted(results.bills.items())))
    print("state changes " + (", ".join(f"{k} x{v}" for k, v in sorted(results.changes.items())) or "none"))
    print("final states  " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(results.states.values()).items())))
    print("rules:        " + ("held" if not results.broken else f"{len(results.broken)} BROKEN"))


def cmd_switch(args):
    status, answer = Api(args.url).post("/sim/switch", {"level": args.level, "target": args.target,
                                                         "on": args.on == "on", "reason": args.reason})
    print(answer if status == 200 else f"Refused ({status}): {answer}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="phone_sim", description="Pretend to be mPOS phones")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pair", help="pair one phone with a code from the portal and keep its token")
    p.add_argument("url")
    p.add_argument("terminal", help="e.g. HD22_MPOS1")
    p.add_argument("code", help="the 6-character pairing code")
    p.add_argument("--serial", help="phone serial to report (default SIM-<terminal>)")
    p.add_argument("--file", type=Path, default=PHONES_FILE)
    p.set_defaults(func=cmd_pair)

    p = sub.add_parser("run", help="run phones: check-ins and bills")
    p.add_argument("url")
    p.add_argument("--fake", action="store_true", help="run every phone of tools/fake_device_portal.py")
    p.add_argument("--file", type=Path, default=PHONES_FILE)
    p.add_argument("--minutes", type=float, default=5)
    p.add_argument("--poll", type=float, help="seconds between check-ins (default: what the portal says, 30)")
    p.add_argument("--bill-every", type=float, default=120, help="average seconds between bills per phone")
    p.add_argument("--bill-seconds", type=float, nargs=2, default=(20, 90), metavar=("MIN", "MAX"),
                   help="how long a bill stays open")
    p.add_argument("--quiet", action="store_true", help="do not print each phone's state change")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("switch", help="flip a switch on the FAKE portal")
    p.add_argument("url")
    p.add_argument("level", choices=["global", "store", "terminal", "veto", "lock"])
    p.add_argument("target", nargs="?", help="store code or terminal ID (not for global)")
    p.add_argument("on", choices=["on", "off"])
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_switch)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
