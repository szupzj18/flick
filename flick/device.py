"""Thin idb CLI wrapper.

Subprocess-based on purpose: the idb CLI is the stable surface, and per-call
overhead (~150 ms) is acceptable next to the ~200 ms accessibility reads.
A gRPC client can replace this module later without touching the observer.
"""

import json
import os
import subprocess
import time

AXBRIDGE = "axbridge"


class IdbError(RuntimeError):
    pass


class TransientTreeError(IdbError):
    """The accessibility read failed in a way a retry may fix (transition, guest gone)."""


class NotBootedError(IdbError):
    pass


class IdbDevice:
    def __init__(self, udid: str, idb_bin: str = "idb", simctl_bin: str = "xcrun"):
        self.udid = udid
        self.idb_bin = idb_bin
        self.simctl_bin = simctl_bin

    # ------------------------------------------------------------------ low level
    def _run(self, args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
        # IDB_UDID is the reliable selector; --udid is only defined on leaf parsers.
        env = {**os.environ, "IDB_UDID": self.udid}
        return subprocess.run(
            [self.idb_bin, *args], capture_output=True, text=True, timeout=timeout, env=env,
            check=False,
        )

    def _run_simctl(self, args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.simctl_bin, "simctl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    # ------------------------------------------------------------------ lifecycle
    def is_booted(self) -> bool:
        # list-targets does not accept --udid; filter on the udid ourselves.
        env = {**os.environ, "IDB_UDID": self.udid}
        p = subprocess.run(
            [self.idb_bin, "list-targets"], capture_output=True, text=True, timeout=30.0, env=env,
            check=False,
        )
        for line in p.stdout.splitlines():
            if self.udid in line:
                return "| Booted |" in line
        return False

    def ensure_booted(self, timeout_s: float = 90.0) -> None:
        if self.is_booted():
            return
        self._run(["boot"])
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.is_booted():
                return
            time.sleep(1.0)
        raise NotBootedError(f"{self.udid} did not boot within {timeout_s}s")

    def launch(self, bundle_id: str, foreground: bool = True) -> None:
        args = ["launch"]
        if foreground:
            args.append("--foreground")
        args.append(bundle_id)
        p = self._run(args)
        if p.returncode != 0:
            raise IdbError(p.stderr.strip() or p.stdout.strip())

    # ------------------------------------------------------------------ observation
    def describe_all_raw(self, timeout: float = 25.0) -> list[dict]:
        # axbridge is mandatory: the legacy ax backend drops most SwiftUI list content
        # (measured: 15 elements vs 132 on the iOS 26 Settings root).
        p = self._run(
            ["ui", "describe-all", "--json", "--api", AXBRIDGE],
            timeout=timeout,
        )
        if p.returncode != 0:
            msg = (p.stderr or p.stdout).strip()
            # Guest injection fails during app transitions and while the device shuts down.
            if "guest" in msg or "not booted" in msg or "closed by peer" in msg:
                raise TransientTreeError(msg)
            raise IdbError(msg)
        try:
            data = json.loads(p.stdout)
        except json.JSONDecodeError as e:
            raise TransientTreeError(f"non-JSON tree: {p.stdout[:120]!r}") from e
        if not isinstance(data, list):
            raise TransientTreeError("unexpected tree shape")
        return data

    def describe_point_raw(self, x: int, y: int) -> dict | None:
        p = self._run(["ui", "describe-point", str(x), str(y), "--json", "--api", AXBRIDGE])
        if p.returncode != 0:
            return None
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------------ actions
    def tap(self, x: int, y: int, reason: str = "") -> None:
        args = ["ui", "tap", str(x), str(y), "--api", "hid"]
        if reason:
            args += ["--reason", reason[:200]]
        p = self._run(args)
        if p.returncode != 0:
            raise IdbError((p.stderr or p.stdout).strip())

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float | None = None) -> None:
        args = ["ui", "swipe", str(x1), str(y1), str(x2), str(y2)]
        if duration is not None:
            args += ["--duration", str(duration)]
        p = self._run(args)
        if p.returncode != 0:
            raise IdbError((p.stderr or p.stdout).strip())

    def press_button(self, name: str) -> None:
        p = self._run(["ui", "button", name])
        if p.returncode != 0:
            raise IdbError((p.stderr or p.stdout).strip())

    def type_ascii(self, text: str) -> None:
        """HID keycodes only. Non-ASCII raises inside idb; spaces may be localised
        (observed U+2006), so callers must read the value back and prefer paste()."""
        p = self._run(["ui", "text", text])
        if p.returncode != 0:
            raise IdbError((p.stderr or p.stdout).strip())

    def paste(self, text: str) -> None:
        """Unicode-safe text entry via the simulator pasteboard and Cmd+V.

        simctl pbcopy writes the shared pasteboard; the keyboard must already be
        up (a field focused) for the paste to land. Focus is not observable, so
        callers verify via the post-action AX value.
        """
        proc = subprocess.run(
            ["xcrun", "simctl", "pbcopy", self.udid],
            input=text,
            text=True,
            capture_output=True,
            timeout=10.0,
            check=False,
        )
        if proc.returncode != 0:
            raise IdbError(f"pbcopy failed: {proc.stderr.strip()}")
        p = self._run(["ui", "key", "25", "--command"])  # USB HID Keyboard V = 0x19
        if p.returncode != 0:
            raise IdbError((p.stderr or p.stdout).strip())

    def screenshot(self, path: str) -> None:
        # idb screenshot is broken on this companion/iOS pair; simctl works.
        p = self._run_simctl(["io", self.udid, "screenshot", path], timeout=30.0)
        if p.returncode != 0:
            raise IdbError(p.stderr.strip())
