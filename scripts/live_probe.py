"""Live, reversible verification probes.

Each probe undoes its own navigation. Run against a booted simulator with the
Reminders and Settings system apps. Requires IDB_UDID.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flick.device import IdbDevice
from flick.observer import observe_stable, relocalize

UDID = os.environ["IDB_UDID"]
dev = IdbDevice(UDID)


def step(msg):
    print(f"\n=== {msg} ===")


def tap_with_hit_check(snap, label, settle=1.5):
    """The milestone-3 safety chain, hand-run: relocalize -> point check -> tap."""
    target = next((e for e in snap.elements if e.label == label), None)
    if target is None:
        raise AssertionError(f"target {label!r} not present")
    x, y = target.center
    hit = dev.describe_point_raw(x, y)
    hit_label = (hit or {}).get("AXLabel")
    print(f"target={label!r} center=({x},{y}) describe-point={hit_label!r} "
          f"type={(hit or {}).get('type')}")
    if not hit or (hit_label != label and label not in (hit_label or "")):
        raise AssertionError(f"hit check failed: point resolves to {hit_label!r}")
    dev.tap(x, y, reason=f"probe tap {label}")
    time.sleep(settle)
    return target


def probe_settings_roundtrip():
    step("launch Settings")
    dev.press_button("HOME")
    time.sleep(0.8)
    dev.launch("com.apple.Preferences")
    home = observe_stable(dev)
    print(f"home: {len(home.elements)} elements fp={home.fingerprint}")

    step("hit-check and tap 通用")
    tap_with_hit_check(home, "通用")
    inner = observe_stable(dev)
    inner_labels = [e.label for e in inner.elements]
    print("inner page labels:", inner_labels[:12])
    assert any("关于本机" in l for l in inner_labels), "did not navigate into 通用"

    step("relocalize across transition (back button) and return")
    # The back button is labelled "设置"; verify hit-check distinguishes it.
    back = next((e for e in inner.elements if e.label == "设置"), None)
    assert back is not None, "no back button labelled 设置"
    print(f"back button at {back.center} frame={back.frame}")
    tap_with_hit_check(inner, "设置")
    back_home = observe_stable(dev)
    again = relocalize(back_home, "通用", {"x": 16, "y": 380.6, "width": 370, "height": 52.4})
    assert again is not None, "通用 not found after returning"
    print(f"relocalized 通用 at {again.center} (uid={again.uid})")
    print("SETTINGS ROUNDTRIP: PASS")


def probe_swipe_and_relocate():
    step("stable settings home, swipe up one screen")
    dev.press_button("HOME")
    time.sleep(0.8)
    dev.launch("com.apple.Preferences")
    time.sleep(1.0)
    home = observe_stable(dev)
    before = [e.label for e in home.elements]
    dev.swipe(200, 700, 200, 200, duration=0.4)
    time.sleep(1.2)
    after = observe_stable(dev)
    after_labels = [e.label for e in after.elements]
    print("before:", before)
    print("after: ", after_labels)
    assert after.fingerprint != home.fingerprint, "tree did not change after swipe"
    assert set(after_labels) != set(before), "same rows after scroll"

    step("relocalize a persistent row by uid across scroll")
    uid_target = next((e for e in home.elements if e.uid), None)
    found = None
    if uid_target:
        found = next((e for e in after.elements if e.uid == uid_target.uid), None)
    print("uid cross-frame:", uid_target.label if uid_target else None,
          "->", found.label if found else "(scrolled off or absent)")

    # swipe back down for tidiness
    dev.swipe(200, 200, 200, 700, duration=0.4)
    time.sleep(1.0)
    print("SWIPE: PASS")


def probe_home_button():
    step("ui button HOME then relaunch")
    dev.press_button("HOME")
    time.sleep(1.0)
    spring = observe_stable(dev)
    print("home screen front_app:", spring.front_app, "elements:", len(spring.elements))
    assert spring.front_pid is not None
    print("HOME BUTTON: PASS")


MARKER = "JEVTEST验证探针"


def _tap_if_present(snap, needle, exact=False):
    for e in snap.elements:
        hit = (e.label == needle) if exact else (needle in e.label)
        if hit:
            dev.tap(e.center[0], e.center[1], reason=f"probe {needle}")
            time.sleep(1.0)
            return True
    return False


def probe_paste_cjk():
    step("Reminders: create -> paste CJK -> verify -> delete")
    dev.launch("com.apple.reminders")
    snap = observe_stable(dev)
    # Clear first-run / iCloud cards if they happen to be up.
    for label in ("以后", "关闭"):
        if _tap_if_present(snap, label):
            snap = observe_stable(dev)

    assert _tap_if_present(snap, "新提醒事项"), "new-reminder button missing"
    snap = observe_stable(dev)
    title = next((e for e in snap.elements if e.role == "TextView" and "标题" in e.label), None)
    assert title is not None, "title field missing"
    dev.tap(title.center[0], title.center[1], reason="focus title")
    time.sleep(0.8)

    dev.paste(MARKER)
    time.sleep(1.0)
    filled = observe_stable(dev)
    title_now = next((e for e in filled.elements if e.role == "TextView" and "标题" in e.label), None)
    print("title AXValue after paste:", repr(title_now.value if title_now else None))
    assert title_now is not None and MARKER in title_now.value.replace("\n", ""), "CJK paste failed"

    step("cleanup: save, open list, swipe-delete the probe reminder")
    if _tap_if_present(filled, "完成"):
        pass
    snap = observe_stable(dev)
    _tap_if_present(snap, "返回")
    snap = observe_stable(dev)
    # Open the default list whose label carries the item count.
    opened = _tap_if_present(snap, "提醒事项，")
    if not opened:
        print("WARNING: could not open default list for cleanup; delete manually")
        return
    snap = observe_stable(dev)
    row = next((e for e in snap.elements if MARKER in (e.value + e.label)), None)
    assert row is not None, "saved reminder not found in list"
    x, y = row.center
    # A full-width swipe performs the first destructive action directly
    # (iOS convention): the reminder is deleted without a confirm sheet.
    dev.swipe(min(x + 150, 390), y, max(x - 300, 10), y, duration=0.3)
    time.sleep(1.0)
    gone = observe_stable(dev)
    assert not any(MARKER in (e.value + e.label) for e in gone.elements), "reminder still present"
    print("CJK PASTE ROUNDTRIP: PASS")


if __name__ == "__main__":
    probes = {
        "settings": probe_settings_roundtrip,
        "swipe": probe_swipe_and_relocate,
        "home": probe_home_button,
        "paste": probe_paste_cjk,
    }
    which = sys.argv[1:] or list(probes)
    for name in which:
        probes[name]()
    print("\nALL SELECTED PROBES PASSED")
