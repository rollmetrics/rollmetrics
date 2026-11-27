"""Match-level and event-level evaluation metrics for rollmetrics-scoreboard-v1."""

from __future__ import annotations

import statistics
from typing import Any, Literal

import numpy as np

from rollmetrics.evals.scoreboard_bench.schema import (
    GroundTruthMatchLabel,
    MatchPredictionRecord,
    PredictedScoreEvent,
    ScoreboardBenchMetrics,
    ScoreDeltaEvent,
)


def _event_signature(pts: int, adv: int, pen: int) -> tuple[str, int]:
    """Normalizes a score delta event to its primary scoring dimension and magnitude."""
    if pts > 0:
        return ("pts", pts)
    if adv > 0:
        return ("adv", adv)
    if pen > 0:
        return ("pen", pen)
    return ("none", 0)


def match_score_events_f1(
    gt_events: list[ScoreDeltaEvent],
    pred_events: list[PredictedScoreEvent],
    tolerance_sec: float = 5.0,
) -> tuple[int, int, int]:
    """Greedy min-time-offset 1-to-1 matching requiring +/-tolerance_sec, athlete, and score kind."""
    valid_gt = [
        e for e in gt_events if (e.pts_delta > 0 or e.adv_delta > 0 or e.pen_delta > 0)
    ]
    valid_pred = [
        e for e in pred_events if (e.pts_delta > 0 or e.adv_delta > 0 or e.pen_delta > 0)
    ]

    candidate_pairs: list[tuple[float, int, int]] = []
    for gi, ge in enumerate(valid_gt):
        g_sig = _event_signature(ge.pts_delta, ge.adv_delta, ge.pen_delta)
        for pi, pe in enumerate(valid_pred):
            if ge.athlete != pe.athlete:
                continue
            p_sig = _event_signature(pe.pts_delta, pe.adv_delta, pe.pen_delta)
            if g_sig != p_sig:
                continue
            dt_sec = abs(float(ge.match_elapsed_sec) - float(pe.match_elapsed_sec))
            if dt_sec <= tolerance_sec:
                candidate_pairs.append((dt_sec, gi, pi))

    candidate_pairs.sort(key=lambda item: item[0])
    matched_gt: set[int] = set()
    matched_pred: set[int] = set()
    tp = 0
    for _dt, gi, pi in candidate_pairs:
        if gi not in matched_gt and pi not in matched_pred:
            matched_gt.add(gi)
            matched_pred.add(pi)
            tp += 1

    fp = len(valid_pred) - tp
    fn = len(valid_gt) - tp
    return tp, fp, fn


def score_match_prediction(
    gt: GroundTruthMatchLabel,
    pred: MatchPredictionRecord,
    event_tolerance_sec: float = 5.0,
) -> dict[str, Any]:
    """Computes winner match, exact 6-counter score match, per-athlete MAE, and event TP/FP/FN."""
    norm_gt_winner = "draw" if gt.winner in ("draw", "referee_decision") else gt.winner
    norm_pred_winner = "draw" if pred.winner in ("draw", "referee_decision") else pred.winner
    # Exact match if both agree on A, B, or regulation tie/decision
    winner_ok = (gt.winner == pred.winner) or (norm_gt_winner == norm_pred_winner)

    exact_score_ok = (
        gt.final_score_a.points == pred.final_score_a.points
        and gt.final_score_a.advantages == pred.final_score_a.advantages
        and gt.final_score_a.penalties == pred.final_score_a.penalties
        and gt.final_score_b.points == pred.final_score_b.points
        and gt.final_score_b.advantages == pred.final_score_b.advantages
        and gt.final_score_b.penalties == pred.final_score_b.penalties
    )

    pts_errors = [
        abs(gt.final_score_a.points - pred.final_score_a.points),
        abs(gt.final_score_b.points - pred.final_score_b.points),
    ]
    adv_errors = [
        abs(gt.final_score_a.advantages - pred.final_score_a.advantages),
        abs(gt.final_score_b.advantages - pred.final_score_b.advantages),
    ]
    pen_errors = [
        abs(gt.final_score_a.penalties - pred.final_score_a.penalties),
        abs(gt.final_score_b.penalties - pred.final_score_b.penalties),
    ]

    tp, fp, fn = match_score_events_f1(
        gt.events,
        pred.events,
        tolerance_sec=event_tolerance_sec,
    )

    return {
        "match_id": gt.match_id,
        "model_id": pred.model_id,
        "winner_ok": winner_ok,
        "gt_winner": gt.winner,
        "pred_winner": pred.winner,
        "final_score_exact": exact_score_ok,
        "gt_score": {
            "A": gt.final_score_a.model_dump(),
            "B": gt.final_score_b.model_dump(),
        },
        "pred_score": {
            "A": pred.final_score_a.model_dump(),
            "B": pred.final_score_b.model_dump(),
        },
        "pts_abs_errors": pts_errors,
        "adv_abs_errors": adv_errors,
        "pen_abs_errors": pen_errors,
        "event_tp": tp,
        "event_fp": fp,
        "event_fn": fn,
        "leakage_verified": pred.leakage_verified,
        "latency_ms": round(pred.latency_ms, 1),
        "input_tokens": pred.input_tokens,
        "output_tokens": pred.output_tokens,
        "cost_usd": round(pred.cost_usd, 6),
    }


def aggregate_split_metrics(
    model_id: str,
    split: Literal["calibration", "heldout", "pilot"],
    match_evals: list[dict[str, Any]],
    event_tolerance_sec: float = 5.0,
) -> ScoreboardBenchMetrics:
    """Aggregates per-match evaluations into split-level benchmark metrics."""
    n = len(match_evals)
    if n == 0:
        return ScoreboardBenchMetrics(
            model_id=model_id,
            split=split,
            match_count=0,
            winner_accuracy=0.0,
            final_score_exact_match=0.0,
            mae_points=0.0,
            mae_advantages=0.0,
            mae_penalties=0.0,
            score_event_precision=0.0,
            score_event_recall=0.0,
            score_event_f1=0.0,
            event_tolerance_sec=event_tolerance_sec,
        )

    winner_acc = round(sum(1 for m in match_evals if m["winner_ok"]) / n, 4)
    exact_acc = round(sum(1 for m in match_evals if m["final_score_exact"]) / n, 4)

    all_pts_err = [err for m in match_evals for err in m["pts_abs_errors"]]
    all_adv_err = [err for m in match_evals for err in m["adv_abs_errors"]]
    all_pen_err = [err for m in match_evals for err in m["pen_abs_errors"]]

    mae_pts = round(float(statistics.fmean(all_pts_err)), 4) if all_pts_err else 0.0
    mae_adv = round(float(statistics.fmean(all_adv_err)), 4) if all_adv_err else 0.0
    mae_pen = round(float(statistics.fmean(all_pen_err)), 4) if all_pen_err else 0.0

    total_tp = sum(int(m["event_tp"]) for m in match_evals)
    total_fp = sum(int(m["event_fp"]) for m in match_evals)
    total_fn = sum(int(m["event_fn"]) for m in match_evals)

    if total_tp == 0 and total_fp == 0 and total_fn == 0:
        # All matches were 0-0 / quick submission with zero point/adv/pen changes and zero false alarms
        prec, rec, f1 = 1.0, 1.0, 1.0
    else:
        prec = round(total_tp / (total_tp + total_fp), 4) if (total_tp + total_fp) > 0 else 0.0
        rec = round(total_tp / (total_tp + total_fn), 4) if (total_tp + total_fn) > 0 else 0.0
        f1 = round(2 * prec * rec / (prec + rec), 4) if (prec + rec) > 0 else 0.0

    lats = [float(m["latency_ms"]) for m in match_evals]
    costs = [float(m["cost_usd"]) for m in match_evals]
    leak_ok = sum(1 for m in match_evals if m.get("leakage_verified"))

    return ScoreboardBenchMetrics(
        model_id=model_id,
        split=split,
        match_count=n,
        winner_accuracy=winner_acc,
        final_score_exact_match=exact_acc,
        mae_points=mae_pts,
        mae_advantages=mae_adv,
        mae_penalties=mae_pen,
        score_event_precision=prec,
        score_event_recall=rec,
        score_event_f1=f1,
        event_tolerance_sec=event_tolerance_sec,
        p50_latency_ms=round(float(np.percentile(lats, 50)), 1),
        p95_latency_ms=round(float(np.percentile(lats, 95)), 1),
        mean_latency_ms=round(float(statistics.fmean(lats)), 1),
        mean_cost_per_match_usd=round(float(statistics.fmean(costs)), 5),
        total_cost_usd=round(float(sum(costs)), 5),
        leakage_checks_passed=leak_ok,
    )
