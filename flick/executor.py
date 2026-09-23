"""Safe executor for single-step bounded actions.

Executes a Jev decision with pre-action hit-checks, post-action readbacks,
and rigid safety aborts. Returns structured observations for the calling LLM.
"""

import time
from typing import Any

from .device import IdbDevice, IdbError
from .model import choose
from .observer import observe_stable, relocalize

SENSITIVE_TERMS = {"购买", "支付", "转账", "删除", "关机", "抹掉", "退出登录", "重置", "授权", "buy", "pay", "delete", "erase"}


def _is_sensitive(label: str) -> bool:
    label_lower = label.lower()
    return any(term in label_lower for term in SENSITIVE_TERMS)


def execute_step(dev: IdbDevice, goal: str, completion: str, text: str = "") -> dict[str, Any]:
    dev.ensure_booted()
    
    # 1. 观测初始状态
    snap_before = observe_stable(dev)
    
    # 2. 调 Jev 决策
    try:
        decision = choose(snap_before, goal=goal, completion=completion)
    except Exception as e:  # noqa: BLE001
        return {
            "status": "error",
            "message": f"Jev decision failed: {e}",
            "current_screen": snap_before.as_state(),
        }
        
    op = decision["operation"]
    conf = decision["operation_confidence"]
    target_idx = decision["target"]
    
    # 3. 基础准入拦截
    if conf < 0.6:
        return {
            "status": "escalate",
            "message": f"Jev confidence too low ({conf:.2f}) for operation {op}. Please provide guidance.",
            "current_screen": snap_before.as_state(),
        }
        
    if op in {"DONE", "BLOCKED"}:
        return {
            "status": "stopped",
            "operation": op,
            "goal_satisfied_probability": decision["goal_satisfied_p"],
            "message": f"Jev chose {op}.",
            "current_screen": snap_before.as_state(),
        }
        
    target_element = None
    if target_idx:
        target_element = next((e for e in snap_before.elements if e.index == target_idx), None)
        if not target_element:
            return {
                "status": "error",
                "message": f"Jev selected unknown target index {target_idx}.",
                "current_screen": snap_before.as_state(),
            }
            
        if _is_sensitive(target_element.label):
            return {
                "status": "escalate",
                "message": f"Target '{target_element.label}' matches sensitive terms. Action aborted pending confirmation.",
                "current_screen": snap_before.as_state(),
            }

    # 4. 执行动作
    action_log = f"Executed {op}"
    if target_element:
        action_log += f" on '{target_element.label}'"
        
    try:
        if op == "WAIT":
            time.sleep(1.0)
            
        elif op == "DISMISS_MODAL":
            # 弹窗一般有明确的关闭按钮，如果没选具体的，回退到盲点 dimming 中心
            if target_element:
                cx, cy = target_element.center
            elif snap_before.modal and "frame" in snap_before.modal:
                f = snap_before.modal["frame"]
                cx, cy = round(f["x"] + f["width"]/2), round(f["y"] + f["height"]/2)
            else:
                raise ValueError("No modal target found to dismiss.")
            dev.tap(cx, cy, reason="dismiss modal")
            time.sleep(0.8)
            
        elif op == "SCROLL_UP":
            dev.swipe(200, 700, 200, 200, duration=0.4)
            time.sleep(1.0)
            
        elif op == "SCROLL_DOWN":
            dev.swipe(200, 200, 200, 700, duration=0.4)
            time.sleep(1.0)
            
        elif op in {"TAP", "TYPE_TEXT"}:
            if not target_element:
                raise ValueError(f"Operation {op} requires a target.")
                
            # 重定位与防遮挡 (Hit check)
            snap_now = observe_stable(dev)
            current_target = relocalize(snap_now, target_element.label, target_element.frame)
            if not current_target:
                return {
                    "status": "blocked",
                    "message": f"Target '{target_element.label}' moved or disappeared before execution.",
                    "current_screen": snap_now.as_state(),
                }
                
            cx, cy = current_target.center
            hit = dev.describe_point_raw(cx, cy)
            hit_label = (hit or {}).get("AXLabel") or ""
            
            # 放宽一点：如果命中元素的 label 包含目标 label，或者目标 label 包含命中元素，就算通过
            if target_element.label not in hit_label and hit_label not in target_element.label:
                return {
                    "status": "blocked",
                    "message": f"Hit check failed: point ({cx},{cy}) belongs to '{hit_label}', not '{target_element.label}'. Possible overlay/banner.",
                    "current_screen": snap_now.as_state(),
                }
                
            dev.tap(cx, cy, reason=f"{op} on {target_element.label}")
            time.sleep(0.8)
            
            if op == "TYPE_TEXT":
                if not text:
                    return {
                        "status": "error",
                        "message": "Jev chose TYPE_TEXT but no text was provided to the tool.",
                        "current_screen": snap_now.as_state(),
                    }
                dev.paste(text)
                time.sleep(1.0)
                # 读回校验
                snap_after_paste = observe_stable(dev)
                pasted_target = relocalize(snap_after_paste, target_element.label, current_target.frame)
                actual_val = (pasted_target.value if pasted_target else "").replace("\n", "")
                stripped_text = text.replace("\n", "")
                if stripped_text not in actual_val:
                    action_log += f" [Warning: read-back value '{actual_val}' does not perfectly contain requested '{text}']"
                    
    except (IdbError, ValueError) as e:
        return {
            "status": "error",
            "message": f"Execution failed: {e}",
            "current_screen": observe_stable(dev).as_state(),
        }
        
    # 5. 观测执行后的状态
    snap_after = observe_stable(dev)
    page_changed = (snap_after.fingerprint != snap_before.fingerprint)
    
    return {
        "status": "success",
        "action_taken": action_log,
        "page_changed": page_changed,
        "goal_satisfied_probability": decision["goal_satisfied_p"],
        "current_screen": snap_after.as_state(),
    }
