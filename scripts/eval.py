"""Offline evaluator for Jev decision quality.

Runs the multi-head policy over labeled snapshots and computes accuracy,
calibration (ECE), and goal-satisfied confusion matrix.
"""

import json
import os
import sys

# Insert project root to import flick
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flick.model import choose
from flick.observer import clean


def load_dataset(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)

def run_eval(dataset_path: str):
    base_dir = os.path.dirname(dataset_path)
    dataset = load_dataset(dataset_path)
    
    op_correct = 0
    target_correct = 0
    target_total = 0
    done_correct = 0  # goal_satisfied > 0.5 matches gold_op == DONE
    
    # For ECE
    op_confs = []
    op_accs = []
    
    print(f"Evaluating {len(dataset)} samples...")
    for sample in dataset:
        fix_path = os.path.join(base_dir, "fixtures", sample["fixture"])
        with open(fix_path) as f:
            raw = json.load(f)
        snap = clean(raw)
        
        gold_op = sample["gold_op"]
        gold_label = sample["gold_target_label"]
        gold_index = None
        if gold_label:
            target = next((e for e in snap.elements if e.label == gold_label), None)
            if target:
                gold_index = target.index
            else:
                print(f"WARNING: gold label {gold_label!r} not found in {sample['fixture']}")
        
        try:
            decision = choose(snap, sample["goal"], completion=sample["completion"])
        except Exception as e:  # noqa: BLE001
            print(f"ERR {sample['id']}: model call failed - {e}")
            continue
            
        pred_op = decision["operation"]
        pred_target = decision["target"]
        conf = decision["operation_confidence"]
        
        # Scoring
        op_match = (pred_op == gold_op)
        op_correct += int(op_match)
        op_confs.append(conf)
        op_accs.append(int(op_match))
        
        target_match = None
        if gold_index is not None:
            target_total += 1
            target_match = (pred_target == gold_index)
            target_correct += int(target_match)
            
        is_gold_done = (gold_op == "DONE")
        pred_done = (decision["goal_satisfied_p"] >= 0.5)
        done_correct += int(is_gold_done == pred_done)
        
        # Logging
        mark = "✓" if op_match and (target_match is None or target_match) else "✗"
        print(f"[{mark}] {sample['id']}")
        if not op_match:
            print(f"     OP mismatch: gold={gold_op}, pred={pred_op} (conf={conf:.2f})")
        if target_match is False:
            print(f"     Target mismatch: gold={gold_index}, pred={pred_target}")
        if is_gold_done != pred_done:
            print(f"     Noul mismatch: gold_done={is_gold_done}, pred_p={decision['goal_satisfied_p']:.2f}")

    # ECE computation (10 bins)
    bins = {i: {"conf": 0.0, "acc": 0, "count": 0} for i in range(10)}
    for conf, acc in zip(op_confs, op_accs):
        b = min(9, int(conf * 10))
        bins[b]["conf"] += conf
        bins[b]["acc"] += acc
        bins[b]["count"] += 1
        
    ece = 0.0
    n = len(op_confs)
    for b in bins.values():
        if b["count"] > 0:
            avg_conf = b["conf"] / b["count"]
            avg_acc = b["acc"] / b["count"]
            ece += (b["count"] / n) * abs(avg_acc - avg_conf)
            
    print("\n=== Results ===")
    print(f"Operation Acc:  {op_correct}/{n} ({op_correct/n:.1%})")
    if target_total > 0:
        print(f"Target Acc:     {target_correct}/{target_total} ({target_correct/target_total:.1%})")
    print(f"Noul Acc:       {done_correct}/{n} ({done_correct/n:.1%})")
    print(f"Operation ECE:  {ece:.4f}")

if __name__ == "__main__":
    run_eval("tests/eval_dataset.json")
