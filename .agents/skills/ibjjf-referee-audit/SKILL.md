---
name: ibjjf-referee-audit
description: >-
  Audit Brazilian Jiu-Jitsu match timelines, live mat sequences, and video clips against official IBJJF scoring, 3.0-second stabilization, 20.0-second stalling, penalty cascades, and belt/uniform/age legality matrices using RollMetrics. Activate this skill when verifying match scores, fouls, disqualifications, tie-breakers, or IBJJF rule citations.
---

# IBJJF Referee & Rulebook Audit

Use this workflow to adjudicate Brazilian Jiu-Jitsu exchanges deterministically against the official IBJJF Rulebook and verify every point, advantage, penalty, or disqualification with exact article citations.

## Procedure

1. **Confirm Exact Division Parameters**
   - Record `uniform` (`gi` or `nogi`), `belt` (`white`, `blue`, `purple`, `brown`, `black`), `age_division` (`juvenile`, `adult`, `master`), and regulation clock (`300s` Master/Juvenile/Adult White, `360s` Adult Blue, `420s` Adult Purple, `480s` Adult Brown, `600s` Adult Black).
   - Confirm Athlete A and Athlete B gi colors, belt markings, and scoreboard row assignments before scoring any frame.

2. **Check Technique Legality Across the Division Matrix**
   - Query `check_technique_legality(technique_slug, belt, uniform, age_division)` in `rollmetrics.rules.legality_matrix` or `POST /api/knowledge/legality-check`.
   - Enforce critical IBJJF division boundaries:
     - **Universal Immediate DQ (Art. 6.2.3)**: Slams, spinal locks without choke, scissor takedown (`kani basami`), head/neck suplex, small-joint manipulation.
     - **White Belt Specific DQ (Art. 6.2.2)**: Jumping closed guard against a standing opponent, wristlocks, head-outside single-leg driving opponent head-down.
     - **Brown/Black Adult & Master Only (Art. 6.2.2)**: Kneebars, toe holds, bicep slicers, calf slicers.
     - **Adult Brown & Black No-Gi Exception (Art. 6.2.3 K/M)**: Heel hooks and knee reaping are legal strictly in Adult Brown/Black No-Gi and remain immediate DQs in all Gi divisions, all Master divisions, and all lower belts.
     - **Technical / Minor Fouls (Art. 6.3.2)**: Fingers hooked inside sleeve or pant cuffs, guard pull without contact grip, 20-second stalling, fleeing mat bounds, grabbing rashguard/shorts in No-Gi.

3. **Run Deterministic Timeline Adjudication**
   - Feed observed exchanges (`RawObservedEvent`) through `IBJJFRefereeEngine` (`rollmetrics.rules.state_machine`) or `POST /api/analyze/events`.
   - Verify 3.0-second in-bounds stabilization for Takedown (`+2`), Sweep (`+2`), Knee-on-Belly (`+2`), Guard Pass (`+3`), Mount (`+4`), and Back Control (`+4`).
   - Verify downgrades to `+1 Advantage` (Art. 5.2) when control lasts `0.8s` to `<3.0s`, exits bounds mid-transition, or finishes trapped inside a locked opponent submission.
   - Verify the 4-step penalty cascade (Art. 5.3 / 7.2):
     - 1st penalty: warning recorded (`+0` opponent bonus)
     - 2nd penalty: `+1` Advantage to opponent
     - 3rd penalty: `+2` Points to opponent
     - 4th penalty: Disqualification
   - For simultaneous double-stalling calls in closed guard or stand-up, apply `EventKind.DOUBLE_STALLING` so each athlete advances along their independent penalty cascade.

4. **Cite Official IBJJF Articles & Rules Videos**
   - Retrieve exact prose and official IBJJF video references via `KnowledgeRetriever.cite_ibjjf_rule(query_or_article)` or:
     ```bash
     /Users/korymath/Documents/code/rollmetrics/.venv/bin/rollmetrics eval --version v0.1.0
     ```
   - Flag any exchange with vision confidence below `0.72` or mat occlusion (`needs_review: true`) for human table review before finalizing regulation tie-breakers (Points -> Advantages -> Penalties -> Referee Decision per Art. 2.2).
