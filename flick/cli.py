"""Inspection CLI: flick observe / dump / shot / apps."""

import argparse
import json
import os
import subprocess
import sys

from .device import IdbDevice
from .executor import execute_step
from .observer import observe_stable, snapshot_to_json


def _device(args) -> IdbDevice:
    udid = args.udid or os.environ.get("IDB_UDID")
    if not udid:
        sys.exit("set --udid or IDB_UDID")
    return IdbDevice(udid)


def cmd_observe(args) -> None:
    dev = _device(args)
    dev.ensure_booted()
    if args.app:
        dev.launch(args.app)
    snap = observe_stable(dev)
    if args.json:
        print(json.dumps(snapshot_to_json(snap), ensure_ascii=False, indent=1))
        return
    print(f"# app={snap.front_app} pid={snap.front_pid} fp={snap.fingerprint} "
          f"raw={snap.raw_count} elements={len(snap.elements)} modal={snap.modal}")
    for e in snap.elements:
        x, y = e.center
        ops = ",".join(e.operations)
        uid = f" uid={e.uid}" if e.uid else ""
        print(f"[{e.index:>3}] {e.role:<10} ({x:>3},{y:>3}) {ops:<14} "
              f"{e.label[:60]!r}{(' = ' + repr(e.value[:30])) if e.value else ''}{uid}")


def cmd_dump(args) -> None:
    dev = _device(args)
    dev.ensure_booted()
    raw = dev.describe_all_raw()
    with open(args.out, "w") as f:
        json.dump(raw, f, ensure_ascii=False)
    print(f"wrote {len(raw)} nodes to {args.out}", file=sys.stderr)


def cmd_shot(args) -> None:
    dev = _device(args)
    dev.ensure_booted()
    dev.screenshot(args.out)
    print(f"wrote {args.out}", file=sys.stderr)


def cmd_apps(args) -> None:
    dev = _device(args)
    env = {**os.environ, "IDB_UDID": dev.udid}
    p = subprocess.run(["idb", "list-apps"], capture_output=True, text=True, check=False, env=env)
    for line in p.stdout.splitlines():
        if args.filter.lower() in line.lower():
            print(line)


def cmd_run(args) -> None:
    if args.api_key:
        os.environ["TYPESAFE_API_KEY"] = args.api_key
    dev = _device(args)
    result = execute_step(dev, goal=args.goal, completion=args.completion, text=args.text)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(prog="flick")
    ap.add_argument("--udid", help="simulator UDID (or IDB_UDID env)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("observe", help="read and clean the current screen")
    p.add_argument("--app", help="bring bundle id to foreground first")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_observe)

    p = sub.add_parser("dump", help="save raw axbridge tree")
    p.add_argument("out")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("shot", help="screenshot via simctl")
    p.add_argument("out")
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser("apps", help="list installed apps")
    p.add_argument("--filter", default="")
    p.set_defaults(func=cmd_apps)

    p = sub.add_parser("run", help="execute a single bounded task step via Jev")
    p.add_argument("goal", help="The micro-goal for this step")
    p.add_argument("--completion", required=True, help="Observable evidence of completion")
    p.add_argument("--text", default="", help="Text to input if TYPE_TEXT is chosen")
    p.add_argument("--api-key", help="TypeSafe API Key for BYOK (or use TYPESAFE_API_KEY env)")
    p.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
