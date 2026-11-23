"""Train and evaluate the plan-adjustment model (one decision per exercise per week).

Input: a CSV in the plan_rows.csv format (from simulate.py, later exported from the real database),
with a `pt_label` column filled in by the PT: progress / hold / easier / pause.

What it does:
  1. Builds features from the weekly summary (pain, form, reps done, days done, dose vs the goal).
  2. Compares three ways of deciding, all against the PT's labels:
       - baseline: always "hold"
       - rules:    the current rule draft (rule_action column, from goal_draft)
       - tree:     a small decision tree trained on the PT's labels
     The tree is scored with patient-grouped cross-validation, so it never sees the patient it is tested on.
  3. Reports accuracy, macro F1 and two safety numbers:
       - unsafe rate:  said "progress" when the PT said "easier" or "pause"   (must be ~0)
       - too-cautious: said "easier"/"pause" when the PT said "progress"
  4. Trains the final tree on all rows and saves it with its rules as text (readable by the PT).

No PT labels yet? Use --synthetic-labels to fill pt_label with a simulated PT, only to test this pipeline.
Those numbers mean nothing clinically; the report says so.

Run:
  python simulate.py --out sim_out
  python train_plan_model.py --data sim_out/plan_rows.csv --synthetic-labels
  python train_plan_model.py --data pt_labels.csv            # once the PT has labelled
"""
import argparse
import json
import os
import random

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import GroupKFold
from sklearn.tree import DecisionTreeClassifier, export_text

import rehab_node as rn

LABELS = ["progress", "hold", "easier", "pause"]   # the four per-exercise decisions
NUMERIC = ["week_post_op", "days_checked_in", "pain_today_avg", "days_reduced", "days_rest", "days_done",
           "reps_done_share", "form_avg", "form_trend", "pain_after_avg", "pain_after_max", "high_pain_days",
           "error_total"]
DERIVED = ["sets", "reps", "dose", "dose_vs_goal", "in_goal", "weeks_before_goal_ok"]
FEATURES = NUMERIC + DERIVED


# ---------- features ----------

def add_features(df):
    """Fill gaps and add dose features. Works on CSV rows and on rows built from a weekly summary."""
    df = df.copy()
    sr = df["planned"].astype(str).str.extract(r"(\d+)\s*x\s*(\d+)").astype(float)
    df["sets"], df["reps"] = sr[0], sr[1]
    df["dose"] = df["sets"] * df["reps"]

    def goal_dose(row):
        t = rn.GOALS.get(row["goal"], rn.GOALS[rn.DEFAULT_GOAL])["targets"].get(row["exercise_id"])
        return (t[0] * t[1]) if t else np.nan
    target = df.apply(goal_dose, axis=1)
    df["in_goal"] = target.notna().astype(int)
    df["dose_vs_goal"] = (df["dose"] / target).fillna(1.0)   # < 1: still below the goal's dose
    df["weeks_before_goal_ok"] = df.apply(
        lambda r: (r["week_post_op"] or 0) - rn.GOALS.get(r["goal"], rn.GOALS[rn.DEFAULT_GOAL])["min_week"], axis=1)

    # Missing values: an exercise not done this week has no form or pain-after.
    df["form_trend"] = df["form_trend"].fillna(0.0)
    df["form_avg"] = df["form_avg"].fillna(df["form_avg"].median() if df["form_avg"].notna().any() else 0.8)
    df["pain_after_avg"] = df["pain_after_avg"].fillna(df["pain_today_avg"]).fillna(0.0)
    df["pain_after_max"] = df["pain_after_max"].fillna(df["pain_after_avg"])
    df["pain_today_avg"] = df["pain_today_avg"].fillna(0.0)
    df["week_post_op"] = df["week_post_op"].fillna(0)
    for c in FEATURES:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def rows_from_summary(summary):
    """Turn one weekly_summary() JSON into feature rows (one per exercise), for prediction in the app."""
    rows = []
    for e in summary["exercises"]:
        rows.append({
            "goal": summary["goal"], "week_post_op": summary["week_post_op"],
            "days_checked_in": summary["days_checked_in"], "pain_today_avg": summary["pain_today_avg"],
            "days_reduced": summary["days_reduced"], "days_rest": summary["days_rest"],
            "exercise_id": e["exercise_id"], "planned": e["planned"], "days_done": e["days_done"],
            "reps_done_share": e["reps_done_share"], "form_avg": e["form_avg"], "form_trend": e["form_trend"],
            "pain_after_avg": e["pain_after_avg"], "pain_after_max": e["pain_after_max"],
            "high_pain_days": e["high_pain_days"], "error_total": sum(e["error_counts"].values()),
        })
    return add_features(pd.DataFrame(rows))


# ---------- synthetic PT (testing only) ----------

def synthetic_pt_label(r, rng):
    """A made-up PT that is a little more cautious than the rules and not perfectly consistent.
    Only for testing the pipeline before real labels exist."""
    if r["high_pain_days"] >= 2 or r["pain_after_max"] >= 9:
        label = "pause"
    elif r["high_pain_days"] >= 1 or r["pain_after_avg"] >= 5 or r["form_avg"] < 0.65 or r["form_trend"] <= -0.1:
        label = "easier"
    elif (r["days_done"] >= 4 and r["form_avg"] >= 0.78 and r["pain_after_avg"] <= 3.5
          and r["reps_done_share"] >= 0.85 and r["dose_vs_goal"] < 1 and r["weeks_before_goal_ok"] >= 0):
        label = "progress"
    else:
        label = "hold"
    if rng.random() < 0.08:   # disagreement noise: move one step on the scale
        i = LABELS.index(label) + rng.choice([-1, 1])
        label = LABELS[min(max(i, 0), len(LABELS) - 1)]
    return label


# ---------- metrics ----------

def score(y_true, y_pred):
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    pt_lighter = np.isin(y_true, ["easier", "pause"])
    said_lighter = np.isin(y_pred, ["easier", "pause"])
    return {
        "accuracy": round(accuracy_score(y_true, y_pred), 3),
        "macro_f1": round(f1_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0), 3),
        "unsafe_rate": round(float(np.mean(y_pred[pt_lighter] == "progress")) if pt_lighter.any() else 0.0, 3),
        "unsafe_count": int(np.sum(pt_lighter & (y_pred == "progress"))),
        "too_cautious_rate": round(float(np.mean(said_lighter[y_true == "progress"])) if (y_true == "progress").any() else 0.0, 3),
        "confusion": confusion_matrix(y_true, y_pred, labels=LABELS).tolist(),
    }


SAFE_WEIGHTS = {"progress": 1, "hold": 1, "easier": 3, "pause": 4}  # missing a lighter call costs more


def grid(max_depth):
    return [{"max_depth": d, "min_samples_leaf": leaf, "class_weight": cw}
            for d in (3, 4, 5, 6, 8) if d <= max_depth for leaf in (5, 10) for cw in (None, "safety")]


def new_tree(cfg):
    cw = SAFE_WEIGHTS if cfg["class_weight"] == "safety" else None
    return DecisionTreeClassifier(max_depth=cfg["max_depth"], min_samples_leaf=cfg["min_samples_leaf"],
                                  class_weight=cw, random_state=0)


def objective(r):
    """Pick settings by macro F1, with unsafe calls weighted heavily."""
    return r["macro_f1"] - 3 * r["unsafe_rate"]


def cv_predict(df, cfg, folds):
    """Patient-grouped CV: every prediction comes from a tree that never saw that patient."""
    pred = np.empty(len(df), dtype=object)
    for train, test in GroupKFold(n_splits=folds).split(df, df["pt_label"], groups=df["patient_id"]):
        m = new_tree(cfg).fit(df.iloc[train][FEATURES], df.iloc[train]["pt_label"])
        pred[test] = m.predict(df.iloc[test][FEATURES])
    return apply_safety(pred, df)


def select(df, configs, folds):
    scored = [(objective(score(df["pt_label"], cv_predict(df, c, folds))), c) for c in configs]
    return max(scored, key=lambda x: x[0])[1]


def nested_cv(df, configs, folds):
    """Outer folds score the model; settings are chosen inside each outer training set only,
    so the reported numbers are not inflated by the choice of settings."""
    pred = np.empty(len(df), dtype=object)
    chosen = []
    for train, test in GroupKFold(n_splits=folds).split(df, df["pt_label"], groups=df["patient_id"]):
        tr = df.iloc[train].reset_index(drop=True)
        cfg = select(tr, configs, max(2, min(folds - 1, tr["patient_id"].nunique())))
        chosen.append(cfg)
        m = new_tree(cfg).fit(tr[FEATURES], tr["pt_label"])
        pred[test] = m.predict(df.iloc[test][FEATURES])
    return apply_safety(pred, df), chosen


def apply_safety(pred, df):
    """Hard limits on top of any model, the same ones the rules use: never progress after a high-pain day,
    with poor form, or with form getting worse."""
    pred = pred.copy()
    unsafe = ((df["high_pain_days"].to_numpy() >= 1) | (df["pain_after_max"].to_numpy() >= rn.STOP_PAIN)
              | (df["form_avg"].to_numpy() < rn.EASIER_FORM) | (df["form_trend"].to_numpy() <= rn.EASIER_TREND))
    pred[unsafe & (pred == "progress")] = "hold"
    return pred


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="sim_out/plan_rows.csv")
    ap.add_argument("--synthetic-labels", action="store_true", help="fill empty pt_label with a simulated PT (testing only)")
    ap.add_argument("--max-depth", type=int, default=6, help="deepest tree to try (deeper = harder for a PT to read)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--out", default="model_out")
    args = ap.parse_args()

    df = add_features(pd.read_csv(args.data, dtype={"pt_label": str, "rule_action": str}))
    synthetic = False
    if args.synthetic_labels:
        rng = random.Random(0)
        df["pt_label"] = df["pt_label"].fillna("").astype(str).str.strip().str.lower()
        empty = df["pt_label"] == ""
        df.loc[empty, "pt_label"] = [synthetic_pt_label(r, rng) for _, r in df[empty].iterrows()]
        synthetic = bool(empty.any())
    df = df[df["pt_label"].isin(LABELS)].reset_index(drop=True)
    if len(df) < 20 or df["patient_id"].nunique() < args.folds:
        raise SystemExit(f"Only {len(df)} labelled rows from {df['patient_id'].nunique()} patients: "
                         f"need at least 20 rows and {args.folds} patients.")

    y = df["pt_label"]
    configs = grid(args.max_depth)
    tree_pred, chosen = nested_cv(df, configs, args.folds)
    results = {
        "baseline_hold": score(y, np.full(len(df), "hold", dtype=object)),
        "rules": score(y, df["rule_action"].replace({"add": "progress"}).fillna("hold").to_numpy()),
        "tree": score(y, tree_pred),
    }

    best = select(df, configs, args.folds)        # settings for the final model, chosen on all rows
    final = new_tree(best).fit(df[FEATURES], y)
    os.makedirs(args.out, exist_ok=True)
    joblib.dump({"model": final, "features": FEATURES, "labels": LABELS, "synthetic_labels": synthetic},
                os.path.join(args.out, "plan_model.joblib"))
    rules_text = export_text(final, feature_names=FEATURES, decimals=2)
    importance = sorted(zip(FEATURES, final.feature_importances_), key=lambda x: -x[1])

    meta = {"rows": len(df), "patients": int(df["patient_id"].nunique()), "synthetic_labels": synthetic,
            "label_counts": y.value_counts().to_dict(), "folds": args.folds, "final_settings": best,
            "settings_chosen_per_fold": chosen}
    with open(os.path.join(args.out, "metrics.json"), "w") as f:
        json.dump({"data": meta, "results": results}, f, indent=2)

    lines = ["# Plan model report", ""]
    if synthetic:
        lines += ["> **Synthetic PT labels.** These numbers only show that the pipeline runs. They say nothing about clinical performance.", ""]
    lines += [f"{meta['rows']} exercise-weeks from {meta['patients']} patients. Labels: {meta['label_counts']}.",
              f"Tree scored with {args.folds}-fold patient-grouped nested cross-validation "
              f"(settings picked inside each training fold). Final settings: {best}.",
              "The tree's numbers include the safety limits (no *progress* after high pain, poor form or falling form).", "",
              "| Method | Accuracy | Macro F1 | Unsafe rate | Unsafe count | Too cautious |", "|---|---|---|---|---|---|"]
    for name, r in results.items():
        lines.append(f"| {name} | {r['accuracy']} | {r['macro_f1']} | {r['unsafe_rate']} | {r['unsafe_count']} | {r['too_cautious_rate']} |")
    lines += ["", "Unsafe = said *progress* when the PT said *easier* or *pause*. Too cautious = said *easier/pause* when the PT said *progress*.",
              "", "## Confusion matrices (rows = PT, columns = method; order " + ", ".join(LABELS) + ")", ""]
    for name in ("rules", "tree"):
        lines.append(f"**{name}**")
        lines.append("```")
        lines += [f"{LABELS[i]:>9} {row}" for i, row in enumerate(results[name]["confusion"])]
        lines.append("```")
    lines += ["", "## What the final tree learned", "", "```", rules_text.strip(), "```", "",
              "Most used features: " + ", ".join(f"{f} ({w:.2f})" for f, w in importance[:6] if w > 0)]
    with open(os.path.join(args.out, "report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")

    print(("SYNTHETIC LABELS: pipeline test only\n" if synthetic else "") +
          f"{meta['rows']} rows, {meta['patients']} patients, labels {meta['label_counts']}")
    print(f"{'method':18} {'acc':>6} {'macroF1':>8} {'unsafe':>7} {'cautious':>9}")
    for name, r in results.items():
        print(f"{name:18} {r['accuracy']:>6} {r['macro_f1']:>8} {r['unsafe_rate']:>7} {r['too_cautious_rate']:>9}")
    print("Saved:", os.path.abspath(args.out), "(plan_model.joblib, metrics.json, report.md)")


def predict(summary, model_path="model_out/plan_model.joblib"):
    """Per-exercise decision for one weekly_summary() JSON, with the safety limit applied.
    Returns {exercise_id: action}. Meant to replace the 'which action' part of goal_draft later;
    the dose step and goal additions stay in the rules."""
    bundle = joblib.load(model_path)
    rows = rows_from_summary(summary)
    pred = apply_safety(bundle["model"].predict(rows[bundle["features"]]).astype(object), rows)
    return dict(zip(rows["exercise_id"], pred))


if __name__ == "__main__":
    main()
