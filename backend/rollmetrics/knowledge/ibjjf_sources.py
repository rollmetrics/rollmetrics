"""IBJJF Rulebook v6.1 and Official Rules Video corpus builder."""

from __future__ import annotations

import logging
import re
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from rollmetrics.knowledge.schema import KnowledgeDocument, sanitize_text

logger = logging.getLogger(__name__)

IBJJF_BOOKS_VIDEOS_URL = "https://ibjjf.com/books-videos"
IBJJF_DEFAULT_PDF_URL = (
    "https://ibjjf.com/rails/active_storage/blobs/redirect/"
    "eyJfcmFpbHMiOnsibWVzc2FnZSI6IkJBaHBBbTRaIiwiZXhwIjpudWxsLCJwdXIiOiJibG9iX2lkIn19--"
    "c53798f1b94f5ebc202702cb44e9428a7606a19b/2024JUN_IBJJF_Rules_EN.pdf"
)
SCRATCH_IBJJF_VIDEOS_TXT = Path(
    "/Users/korymath/.gemini/antigravity-cli/brain/178829f3-7bbf-4423-82f2-e81ac2a20887/"
    "scratch/rollmetrics_kb/ibjjf_official_videos.txt"
)
SCRATCH_CACHED_PDF = Path(
    "/Users/korymath/.gemini/antigravity-cli/brain/97eb0c03-24f7-4b55-b5db-9c5c97ddd981/"
    "scratch/kb_probe/2024JUN_IBJJF_Rules_EN.pdf"
)

# Structured, article-cited IBJJF Rulebook entries (aligned with IBJJF Rulebook v6.1)
STRUCTURED_IBJJF_ARTICLES: list[dict[str, Any]] = [
    {
        "doc_id": "ibjjf-art-1-1-referee-authority",
        "title": "IBJJF Art. 1.1-1.3: Referee Authority, Match Intervention, and Gestures",
        "section_or_belt": "IBJJF Art. 1.1 - 1.3",
        "position": "all",
        "text": (
            "The central referee holds supreme authority over match conduct, safety interventions, "
            "and score announcements. Under Art. 1.3, the referee intervenes immediately when an athlete "
            "commits a serious or severe foul, entangles fingers inside a sleeve or pant cuff, exits the "
            "safety area, or faces imminent joint or cervical injury in youth and white belt divisions."
        ),
        "source_url": "https://www.youtube.com/watch?v=1iJWvTMAr3Y",
        "tags": ["referee", "authority", "gestures", "art 1.1", "art 1.3", "intervention"],
        "metadata": {"article": "IBJJF Art. 1.1-1.3", "pdf_pages": [5, 6, 7, 8, 9, 10, 11, 12, 13, 14]},
    },
    {
        "doc_id": "ibjjf-art-2-2-tie-breakers-and-decisions",
        "title": "IBJJF Art. 2.2: Victory Conditions and Regulation Tie-Breaker Hierarchy",
        "section_or_belt": "IBJJF Art. 2.2",
        "position": "all",
        "text": (
            "Matches conclude by Submission (Art. 2.2.1: physical tap, verbal tap, or scream), "
            "Stoppage/Loss of Consciousness, Disqualification, or Regulation Scoreboard Hierarchy. "
            "When regulation time expires without submission or DQ, victory goes strictly in order to: "
            "(1) Most Points, (2) Most Advantages if points are tied, (3) Fewest Penalties if points "
            "and advantages are both tied, and (4) Referee Decision (Art. 2.2.5) if points, advantages, "
            "and penalties are completely equal (for example 0-0 pts, 1-1 penalty adv, 2-2 pen). "
            "In a Referee Decision, referees evaluate which athlete displayed greater offensive initiative, "
            "closer near-scoring sweeps or passes, and credible submission attempts across regulation."
        ),
        "source_url": "https://www.youtube.com/watch?v=HxXVhWRK7zc",
        "tags": ["tie breaker", "referee decision", "submission", "art 2.2", "points", "advantages", "penalties"],
        "metadata": {"article": "IBJJF Art. 2.2", "pdf_pages": [15]},
    },
    {
        "doc_id": "ibjjf-art-3-1-match-durations-and-stabilization",
        "title": "IBJJF Art. 3.1 & Art. 4: 3-Second Stabilization Rule and Match Durations",
        "section_or_belt": "IBJJF Art. 3.1 & Art. 4",
        "position": "all",
        "text": (
            "All IBJJF point-scoring positions (Takedown, Sweep, Knee on Belly, Guard Pass, Mount, and "
            "Back Control) require 3 full seconds of clear positional stabilization inside the match area "
            "before points are awarded. Points are cumulative when an athlete chains distinct scoring actions "
            "(for example +2 Sweep directly into +4 Mount = +6 total points after 3s mount stabilization). "
            "Crucial submission-defense exception: if the bottom athlete locks a credible submission hold "
            "during a pass or sweep, points are withheld until the top athlete completely frees themselves "
            "from the submission and stabilizes for 3 seconds. Regulation durations: Adult White 5m, Blue 6m, "
            "Purple 7m, Brown 8m, Black 10m; Master divisions and Juvenile divisions run 5 minutes."
        ),
        "source_url": "https://www.youtube.com/watch?v=NkB3me-Vdqw",
        "tags": ["3 seconds", "stabilization", "cumulative points", "duration", "art 3.1", "art 4", "submission"],
        "metadata": {"article": "IBJJF Art. 4", "stabilization_sec": 3.0, "pdf_pages": [16, 17, 41]},
    },
    {
        "doc_id": "ibjjf-art-4-1-takedown",
        "title": "IBJJF Art. 4.1: Takedown (2 Points)",
        "section_or_belt": "IBJJF Art. 4.1",
        "position": "takedowns",
        "text": (
            "Awarded 2 points when an athlete initiates a projection or takedown from standing (both "
            "athletes on their feet, or countering a guard pull before the guard puller sits cleanly) "
            "and holds the opponent on their back, side, or Seated/Turtle with top control for 3 seconds "
            "in-bounds. If the opponent lands on all fours (Turtle), the attacking athlete must control "
            "behind the shoulder/hip line for 3 seconds to score 2 points. Takedowns initiated in-bounds "
            "that land out-of-bounds with clear control still score or earn an advantage per Art. 4.1.4; "
            "stabilizing for less than 3 seconds (0.8s to 2.9s) downgrades to +1 Advantage under Art. 5.2."
        ),
        "source_url": "https://www.youtube.com/watch?v=bjg_LwKKja4",
        "tags": ["takedown", "2 points", "art 4.1", "turtle", "standing", "single leg", "double leg"],
        "metadata": {"article": "IBJJF Art. 4.1", "points": 2, "pdf_pages": [18]},
    },
    {
        "doc_id": "ibjjf-art-4-2-guard-pass",
        "title": "IBJJF Art. 4.2: Guard Pass (3 Points)",
        "section_or_belt": "IBJJF Art. 4.2",
        "position": "standing_pass",
        "text": (
            "Awarded 3 points when the athlete in top position clears the bottom athlete's legs (closed, "
            "open, half, butterfly, or spider guard) and establishes perpendicular Side Control, North-South, "
            "Mount, or Knee on Belly for 3 full seconds with the opponent's back or side pinned and free of "
            "any submission lock. If the bottom athlete turns to Turtle before 3 seconds of back-pinned "
            "control elapse, no 3-point pass is scored; instead, the top passer receives +1 Advantage under "
            "Art. 5.2 for forcing the defensive turtle turn."
        ),
        "source_url": "https://www.youtube.com/watch?v=-ZCXlsP-bZE",
        "tags": ["guard pass", "3 points", "art 4.2", "side control", "north south", "turtle turn"],
        "metadata": {"article": "IBJJF Art. 4.2", "points": 3, "pdf_pages": [19, 20]},
    },
    {
        "doc_id": "ibjjf-art-4-3-knee-on-belly",
        "title": "IBJJF Art. 4.3: Knee on Belly (2 Points)",
        "section_or_belt": "IBJJF Art. 4.3",
        "position": "side_control",
        "text": (
            "Awarded 2 points when the top athlete places the knee or shin nearest the opponent's hips "
            "across the opponent's abdomen/chest while posting the outside foot flat on the mat (toes "
            "engaged, knee off the mat) facing the opponent and maintaining posture for 3 seconds. "
            "Keeping the outside knee on the mat or holding for less than 3 seconds yields +1 Advantage "
            "under Art. 5.2 rather than 2 points."
        ),
        "source_url": "https://www.youtube.com/watch?v=5stRpzfX2P4",
        "tags": ["knee on belly", "kob", "2 points", "art 4.3", "side control"],
        "metadata": {"article": "IBJJF Art. 4.3", "points": 2, "pdf_pages": [20, 25]},
    },
    {
        "doc_id": "ibjjf-art-4-4-mount",
        "title": "IBJJF Art. 4.4: Mount and S-Mount / Back Mount (4 Points)",
        "section_or_belt": "IBJJF Art. 4.4",
        "position": "mount",
        "text": (
            "Awarded 4 points when the top athlete straddles the opponent's torso (opponent on back, side, "
            "or flat stomach) with both knees or one knee and one foot touching the mat for 3 seconds, "
            "without being trapped in half guard and with at least one opponent arm maximum inside the legs. "
            "Trapping BOTH of the opponent's arms inside the legs (double-arm high mount) scores 0 points; "
            "standard Mount, Technical Mount, and S-Mount over a single shoulder score 4 points."
        ),
        "source_url": "https://www.youtube.com/watch?v=vOXo06x1qaE",
        "tags": ["mount", "s-mount", "back mount", "4 points", "art 4.4"],
        "metadata": {"article": "IBJJF Art. 4.4", "points": 4, "pdf_pages": [21]},
    },
    {
        "doc_id": "ibjjf-art-4-5-back-control",
        "title": "IBJJF Art. 4.5: Back Control with Hooks or Body Triangle (4 Points)",
        "section_or_belt": "IBJJF Art. 4.5",
        "position": "back_control",
        "text": (
            "Awarded 4 points when the athlete controls the opponent's back and places both heels inside "
            "the opponent's inner thighs (hooks) or locks a Figure-Four Body Triangle around the waist "
            "for 3 full seconds, without crossing the ankles over each other between the thighs and without "
            "trapping both of the opponent's arms inside the legs. Securing only one hook or losing the "
            "second hook before 3 seconds elapse awards +1 Advantage."
        ),
        "source_url": "https://www.youtube.com/watch?v=AQfy0Kfxq7Y",
        "tags": ["back control", "hooks", "body triangle", "4 points", "art 4.5"],
        "metadata": {"article": "IBJJF Art. 4.5", "points": 4, "pdf_pages": [22]},
    },
    {
        "doc_id": "ibjjf-art-4-6-sweep",
        "title": "IBJJF Art. 4.6: Sweep from Guard (2 Points)",
        "section_or_belt": "IBJJF Art. 4.6",
        "position": "closed_guard",
        "text": (
            "Awarded 2 points when the bottom athlete, starting with any form of guard (closed, half, "
            "butterfly, open, or single-leg wrestle-up initiated from guard), inverts top and bottom "
            "positions and stabilizes on top for 3 seconds in-bounds. When a wrestle-up from guard forces "
            "the top player onto hands and knees in Turtle, the sweeping athlete must clear any remaining "
            "leg entanglement and control behind the opponent's hips/shoulders for 3 seconds (or flatten "
            "them) to earn 2 sweep points. Reversals from bottom Side Control, Mount, or bottom Turtle "
            "without first establishing guard score 0 sweep points."
        ),
        "source_url": "https://www.youtube.com/watch?v=S3cSBABL9LQ",
        "tags": ["sweep", "2 points", "art 4.6", "wrestle up", "closed guard", "half guard", "butterfly"],
        "metadata": {"article": "IBJJF Art. 4.6", "points": 2, "pdf_pages": [23]},
    },
    {
        "doc_id": "ibjjf-art-5-2-advantages",
        "title": "IBJJF Art. 5.1 & 5.2: Advantage Scoring Criteria and Sub-3s Downgrades",
        "section_or_belt": "IBJJF Art. 5.2",
        "position": "all",
        "text": (
            "An Advantage (+1 adv) is awarded when an athlete: (1) achieves a point-scoring position "
            "(Takedown, Sweep, Pass, Knee on Belly, Mount, or Back) but holds it for less than 3 seconds "
            "before the opponent escapes or time expires; (2) forces the opponent out of bounds mid-sweep "
            "or mid-pass; (3) forces the opponent into defensive Turtle or half-guard squeeze to prevent a "
            "pass; or (4) applies a real submission threat that places the opponent in genuine danger and "
            "forces active defensive escape (assessed after the hold ends). Merely grabbing the head for "
            "an arm-out gi guillotine without real choking threat scores 0 technical advantages."
        ),
        "source_url": "https://www.youtube.com/watch?v=v8OYKn3XtGM",
        "tags": ["advantage", "art 5.2", "near sweep", "near pass", "sub 3s", "submission threat"],
        "metadata": {"article": "IBJJF Art. 5.2", "pdf_pages": [24, 25]},
    },
    {
        "doc_id": "ibjjf-art-5-3-7-2-penalty-cascade-and-stalling",
        "title": "IBJJF Art. 5.3, 6.3.2 & 7.2: Penalty Cascade, 20s Stalling, and Double Stalling",
        "section_or_belt": "IBJJF Art. 5.3 / 6.3.2 / 7.2",
        "position": "all",
        "text": (
            "Minor / Serious Technical Fouls follow the strict IBJJF 4-step Penalty Cascade (Art. 5.3 & 7.2): "
            "1st Penalty = recorded penalty on offender (0 bonus to opponent); "
            "2nd Penalty = recorded penalty + opponent receives +1 Advantage; "
            "3rd Penalty = recorded penalty + opponent receives +2 Points; "
            "4th Penalty = Immediate Disqualification (DQ) of offender. "
            "Lack of Combativeness / Stalling (Art. 6.3.2 A/B): when one or both athletes stall without "
            "seeking positional progression or submission for 20 continuous seconds, the referee issues "
            "stalling penalties. In Double Stalling (for example top player stalling head-down in closed "
            "guard while bottom player holds closed guard without attacking for 20s), BOTH athletes receive "
            "simultaneous penalties: 1st double stalling -> 1-1 pen (0-0 adv); 2nd double stalling -> "
            "2-2 pen and BOTH athletes automatically receive +1 penalty advantage (1-1 adv)."
        ),
        "source_url": "https://www.youtube.com/watch?v=oKkIEcbUc0g",
        "tags": [
            "penalty",
            "penalty cascade",
            "stalling",
            "double stalling",
            "20 seconds",
            "art 5.3",
            "art 6.3.2",
            "art 7.2",
        ],
        "metadata": {
            "article": "IBJJF Art. 5.3 / 6.3.2 / 7.2",
            "stalling_sec": 20.0,
            "video_stalling_url": "https://www.youtube.com/watch?v=iGbn75D1ma0",
            "pdf_pages": [24, 26, 27, 34],
        },
    },
    {
        "doc_id": "ibjjf-art-6-3-2-illegal-grips-and-guard-pull",
        "title": "IBJJF Art. 6.3.2 (C/F/G/J): Cuff Grips, Guard Pull Without Grip, Out-of-Bounds, and No-Gi Cloth",
        "section_or_belt": "IBJJF Art. 6.3.2",
        "position": "standing",
        "text": (
            "Common IBJJF Technical / Minor Fouls penalized via the Art. 7.2 cascade rather than instant DQ: "
            "(1) Art. 6.3.2 (F) Fingers Inside Sleeve or Pant Cuff: hooking one or more fingers inside the "
            "opening of the opponent's kimono sleeve cuff or pant leg hem causes immediate referee grip-strip, "
            "neutral grip reset, and 1 minor foul penalty (use a four-finger outside pistol or pocket grip). "
            "(2) Art. 6.3.2 (J) Guard Pull Without Contact Grip: sitting down or pulling guard without an "
            "established physical grip on the opponent draws a referee stand-up and 1 penalty. "
            "(3) Art. 6.3.2 (C) Fleeing Mat Bounds: deliberately Crawling/running out of the match area to "
            "avoid a takedown, sweep, or pass draws 1 penalty (+2 pts to opponent if avoiding clean score), "
            "whereas fleeing an active locked submission out of bounds is an immediate DQ under Art. 6.2.3 (D). "
            "(4) Art. 6.3.2 (G) No-Gi Cloth Gripping: grabbing rashguard, shorts, or belt in No-Gi draws 1 penalty."
        ),
        "source_url": "https://www.youtube.com/watch?v=_Us_4765FBo",
        "tags": [
            "fingers inside sleeve",
            "pant cuff",
            "guard pull without grip",
            "fleeing bounds",
            "out of bounds",
            "rashguard",
            "shorts",
            "art 6.3.2",
            "minor foul",
        ],
        "metadata": {"article": "IBJJF Art. 6.3.2", "pdf_pages": [14, 26, 27, 28]},
    },
    {
        "doc_id": "ibjjf-art-6-2-universal-severe-fouls",
        "title": "IBJJF Art. 6.2.3 (A/B/F/J/N/V): Universal Severe Technical Fouls (Immediate DQ All Belts)",
        "section_or_belt": "IBJJF Art. 6.2.3 (All Belts)",
        "position": "all",
        "text": (
            "The following techniques trigger immediate Disqualification (DQ) across ALL age divisions "
            "(Juvenile, Adult, Master), ALL belt ranks (White through Black), and BOTH Gi and No-Gi: "
            "(A) Slamming an opponent onto the mat from guard or to escape a submission (`slam`); "
            "(B/F) Spinal locks, neck cranks, crucifix cranks, or Twister without an accompanying choke "
            "(`spinal_lock_without_choke`); "
            "(J) Scissor takedown / Kani Basami (`scissor_takedown`); "
            "(V) Suplex landing the opponent on their head or neck (`suplex_head_neck`); "
            "(N) Small-joint manipulation bending fewer than four fingers or toes (`small_joint_manipulation`)."
        ),
        "source_url": "https://www.youtube.com/watch?v=9Bc6FTevca4",
        "tags": [
            "slam",
            "spinal lock",
            "neck crank",
            "twister",
            "scissor takedown",
            "kani basami",
            "suplex",
            "small joint",
            "art 6.2.3",
            "dq",
        ],
        "metadata": {"article": "IBJJF Art. 6.2.3", "pdf_pages": [29, 30, 31, 33]},
    },
    {
        "doc_id": "ibjjf-art-6-2-2-white-juvenile-blue-restrictions",
        "title": "IBJJF Art. 6.2.2 (#5, #12, #21): White Belt Jumping Guard, Wristlocks, and Head-Outside Single Leg",
        "section_or_belt": "IBJJF Art. 6.2.2 (White / Juvenile / Blue+)",
        "position": "takedowns",
        "text": (
            "Division-specific IBJJF prohibitions under Art. 6.2.2 Table of Illegal Moves: "
            "(1) Jumping Closed Guard (#5, `jumping_closed_guard`): ILLEGAL (Immediate DQ) at White Belt "
            "(Juvenile, Adult, and Master, Gi & No-Gi) whenever both feet leave the mat to jump into closed "
            "guard against a standing opponent, including flying triangles and flying armbars. Legal from "
            "Blue Belt upward. White belts MUST keep one foot planted or frame a foot on the hip while sitting. "
            "(2) Wristlocks (#12, `wristlock`): ILLEGAL (Immediate DQ) for ALL White Belts and ALL Juvenile "
            "divisions (including Juvenile Blue/Purple); LEGAL for Adult and Master Blue, Purple, Brown, and Black belts. "
            "(3) Single-Leg Takedown with Head Outside (#21, `single_leg_head_outside`): ILLEGAL (Immediate DQ) "
            "for White Belts and Juveniles when driving the opponent's head/neck downward with the attacker's "
            "head to the outside of the hip; LEGAL for Adult and Master Blue Belt and above."
        ),
        "source_url": "https://www.youtube.com/watch?v=fqKb6EZw6f0",
        "tags": [
            "jumping closed guard",
            "white belt",
            "wristlock",
            "single leg head outside",
            "juvenile",
            "blue belt",
            "art 6.2.2",
            "dq",
        ],
        "metadata": {"article": "IBJJF Art. 6.2.2", "pdf_pages": [29, 30, 31]},
    },
    {
        "doc_id": "ibjjf-art-6-2-2-brown-black-compression-and-leglocks",
        "title": "IBJJF Art. 6.2.2 (#14-17): Kneebar, Toe Hold, Bicep Slicer, Calf Slicer, and Straight Footlock",
        "section_or_belt": "IBJJF Art. 6.2.2 (Brown & Black Belt)",
        "position": "open_guard",
        "text": (
            "Under IBJJF Art. 6.2.2 (#14-#17), Bicep Slicer (`bicep_slicer`), Calf Slicer (`calf_slicer`), "
            "Straight Kneebar (`kneebar`), and Toe Hold (`toe_hold`) are LEGAL in Adult and Master Brown Belt "
            "and Black Belt divisions (both Gi and No-Gi), and ILLEGAL (Immediate DQ) at White, Blue, and "
            "Purple Belt as well as all Juvenile divisions. Straight Ankle / Foot Lock (#13) is legal from "
            "White Belt upward ONLY when turning outward toward the unattacked side without reaping across "
            "the centerline."
        ),
        "source_url": "https://www.youtube.com/watch?v=9Bc6FTevca4",
        "tags": [
            "kneebar",
            "toe hold",
            "bicep slicer",
            "calf slicer",
            "straight ankle lock",
            "brown belt",
            "black belt",
            "purple belt",
            "art 6.2.2",
        ],
        "metadata": {"article": "IBJJF Art. 6.2.2", "pdf_pages": [29, 30, 31]},
    },
    {
        "doc_id": "ibjjf-art-6-2-3-heel-hook-and-reaping-matrix",
        "title": "IBJJF Art. 6.2.3 (K/M): Heel Hooks and Knee Reaping - Adult Brown/Black No-Gi Exception",
        "section_or_belt": "IBJJF Art. 6.2.3 (Adult Brown/Black No-Gi Only)",
        "position": "open_guard",
        "text": (
            "Inside and Outside Heel Hooks (`heel_hook`, Art. 6.2.3 K) and Knee Reaping (`knee_reap`, "
            "Art. 6.2.3 M: crossing the outside thigh from outside-in across the opponent's trapped/standing "
            "leg past the hip-knee line forcing inward knee rotation) are LEGAL EXCLUSIVELY in Adult Brown "
            "Belt No-Gi and Adult Black Belt No-Gi divisions. They remain an IMMEDIATE DISQUALIFICATION (DQ) "
            "in ALL Gi divisions (including Adult Black Belt Gi), ALL Master divisions (including Master 1-7 "
            "Brown/Black No-Gi), ALL Juvenile divisions, and ALL White, Blue, and Purple Belt divisions."
        ),
        "source_url": "https://www.youtube.com/watch?v=nTT-OrtbOwU",
        "tags": [
            "heel hook",
            "knee reap",
            "reaping",
            "nogi",
            "no-gi",
            "adult brown",
            "adult black",
            "master",
            "gi",
            "art 6.2.3",
        ],
        "metadata": {"article": "IBJJF Art. 6.2.3 (K/M)", "pdf_pages": [30, 31, 32, 33]},
    },
    {
        "doc_id": "ibjjf-art-8-uniform-and-disciplinary-rules",
        "title": "IBJJF Art. 6.1 & Art. 8: Gi Inspection, No-Gi Attire, and Disciplinary Conduct",
        "section_or_belt": "IBJJF Art. 6.1 & Art. 8",
        "position": "all",
        "text": (
            "Under IBJJF Art. 8, Gi competitors must wear clean white, royal blue, or black woven cotton "
            "kimonos passing the Official IBJJF Gi Checker Tool (4-finger internal sleeve clearance, sleeve "
            "reaching within 5 cm of wrist, pants within 5 cm of ankle bone, belt tied with double knot and "
            "20-30 cm tails). No-Gi athletes must wear a ranked rashguard showing at least 10% belt color "
            "and board shorts free of pockets, zippers, or metal. Under Art. 6.1, unsportsmanlike conduct, "
            "disrespecting officials, or coaching inside the barricaded area results in disciplinary DQ."
        ),
        "source_url": "https://www.youtube.com/watch?v=typhIhcOIcs",
        "tags": ["gi checker", "uniform", "disciplinary", "rashguard", "art 8", "art 6.1"],
        "metadata": {"article": "IBJJF Art. 6.1 & Art. 8", "pdf_pages": [26, 35, 36, 37, 38]},
    },
]

# Complete official IBJJF YouTube Rules playlist (1-18) + official IBJJF competition tutorials
OFFICIAL_IBJJF_VIDEOS: list[dict[str, Any]] = [
    {
        "video_id": "LdQErGspI7s",
        "duration": "0:26",
        "title": "IBJJF Official Rules Video 1: Principles of the Rules",
        "article": "IBJJF Art. 1.1",
        "position": "all",
        "summary": "Official IBJJF introduction to rulebook objectives: safety, fairness, and progression toward submission.",
    },
    {
        "video_id": "v8OYKn3XtGM",
        "duration": "3:18",
        "title": "IBJJF Official Rules Video 2: Principles of the Match",
        "article": "IBJJF Art. 1.2 - 2.2",
        "position": "all",
        "summary": "Official IBJJF breakdown of match dynamics, positional hierarchy, combativeness, and victory conditions.",
    },
    {
        "video_id": "bjg_LwKKja4",
        "duration": "5:23",
        "title": "IBJJF Official Rules Video 3: Takedown (2 Points)",
        "article": "IBJJF Art. 4.1",
        "position": "takedowns",
        "summary": "Official IBJJF video examples of 2-point takedowns, 3-second top stabilization, out-of-bounds projections, and counter-pull timing.",
    },
    {
        "video_id": "-ZCXlsP-bZE",
        "duration": "5:15",
        "title": "IBJJF Official Rules Video 4: Guard Pass (3 Points)",
        "article": "IBJJF Art. 4.2",
        "position": "standing_pass",
        "summary": "Official IBJJF examples of 3-point guard passes, 3-second side/north-south stabilization, turtle-turn advantages, and submission lock holds.",
    },
    {
        "video_id": "5stRpzfX2P4",
        "duration": "2:06",
        "title": "IBJJF Official Rules Video 5: Knee on Belly (2 Points)",
        "article": "IBJJF Art. 4.3",
        "position": "side_control",
        "summary": "Official IBJJF criteria for 2-point Knee on Belly: near knee on torso, far foot posted with knee off the mat facing opponent for 3 seconds.",
    },
    {
        "video_id": "vOXo06x1qaE",
        "duration": "3:04",
        "title": "IBJJF Official Rules Video 6: Mount (4 Points)",
        "article": "IBJJF Art. 4.4",
        "position": "mount",
        "summary": "Official IBJJF examples of 4-point Mount, S-Mount, Back Mount, and why trapping both arms inside the legs scores 0 points.",
    },
    {
        "video_id": "AQfy0Kfxq7Y",
        "duration": "1:48",
        "title": "IBJJF Official Rules Video 7: Back Control (4 Points)",
        "article": "IBJJF Art. 4.5",
        "position": "back_control",
        "summary": "Official IBJJF criteria for 4-point Back Control: two inner-thigh hooks or body triangle held 3 seconds without crossed ankles.",
    },
    {
        "video_id": "S3cSBABL9LQ",
        "duration": "4:29",
        "title": "IBJJF Official Rules Video 8: Sweep (2 Points)",
        "article": "IBJJF Art. 4.6",
        "position": "closed_guard",
        "summary": "Official IBJJF examples of 2-point sweeps initiated from guard, wrestle-ups against turtling opponents, and 0-point bottom-side reversals.",
    },
    {
        "video_id": "NkB3me-Vdqw",
        "duration": "2:34",
        "title": "IBJJF Official Rules Video 9: Cumulative Points",
        "article": "IBJJF Art. 3.1",
        "position": "all",
        "summary": "Official IBJJF demonstration of cumulative point transitions such as Sweep (+2) directly into Mount (+4) for 6 total points.",
    },
    {
        "video_id": "RAwwWq7cI_o",
        "duration": "1:48",
        "title": "IBJJF Official Rules Video 10: Disciplinary Penalties",
        "article": "IBJJF Art. 6.1",
        "position": "all",
        "summary": "Official IBJJF video on sportsmanship, respect toward referees and opponents, and disciplinary disqualification.",
    },
    {
        "video_id": "oKkIEcbUc0g",
        "duration": "0:43",
        "title": "IBJJF Official Rules Video 11: Sequence of Penalties",
        "article": "IBJJF Art. 5.3 & Art. 7.2",
        "position": "all",
        "summary": "Official IBJJF penalty cascade walkthrough: 1st penalty verbal/mark, 2nd +1 opponent advantage, 3rd +2 opponent points, 4th disqualification.",
    },
    {
        "video_id": "iGbn75D1ma0",
        "duration": "6:04",
        "title": "IBJJF Official Rules Video 12: Lack of Combativeness (Stalling)",
        "article": "IBJJF Art. 6.3.2 (A/B)",
        "position": "closed_guard",
        "summary": "Official IBJJF 20-second stalling rules covering closed guard stalling, double stalling, top side/mount stalling exceptions, and stand-up passivity.",
    },
    {
        "video_id": "_Us_4765FBo",
        "duration": "8:02",
        "title": "IBJJF Official Rules Video 13: Serious Fouls (Technical Penalties)",
        "article": "IBJJF Art. 6.3.2",
        "position": "all",
        "summary": "Official IBJJF guide to serious/minor technical fouls: fingers inside sleeve or pant cuff, guard pull without grip, fleeing bounds, and belt/cloth infractions.",
    },
    {
        "video_id": "9Bc6FTevca4",
        "duration": "8:09",
        "title": "IBJJF Official Rules Video 14: Severe Fouls (Immediate Disqualification)",
        "article": "IBJJF Art. 6.2",
        "position": "all",
        "summary": "Official IBJJF guide to severe fouls by belt rank: slams, neck cranks, jumping closed guard at white belt, wristlocks, kneebars, and reaping.",
    },
    {
        "video_id": "HxXVhWRK7zc",
        "duration": "3:07",
        "title": "IBJJF Official Rules Video 15: Rendering a Decision",
        "article": "IBJJF Art. 2.2",
        "position": "all",
        "summary": "Official IBJJF explanation of tie-breakers (points, advantages, penalties) and how referees judge offensive initiative in a Referee Decision.",
    },
    {
        "video_id": "1iJWvTMAr3Y",
        "duration": "5:39",
        "title": "IBJJF Official Rules Video 16: Referee Gestures",
        "article": "IBJJF Art. 1.3",
        "position": "all",
        "summary": "Official IBJJF hand gestures for 2, 3, and 4 points, advantages, penalties, sleeve-cuff grip strip, mat boundary resets, and disqualification.",
    },
    {
        "video_id": "auXdi3HBJrk",
        "duration": "3:25",
        "title": "IBJJF Official Rules Video 17: Referee Format (1 vs 3 Referees)",
        "article": "IBJJF Art. 1.1",
        "position": "all",
        "summary": "Official IBJJF mechanics for single-referee and three-referee corner consensus panels on scoring, fouls, and decisions.",
    },
    {
        "video_id": "3qL1_cI7mKI",
        "duration": "3:03",
        "title": "IBJJF Official Rules Video 18: Referee Duties",
        "article": "IBJJF Art. 1.2",
        "position": "all",
        "summary": "Official IBJJF pre-match inspection, mat safety management, scoreboard verification, and post-match winner declaration.",
    },
    {
        "video_id": "fqKb6EZw6f0",
        "duration": "6:19",
        "title": "IBJJF Tutorials: Serious Foul Rules Video",
        "article": "IBJJF Art. 6.3.2",
        "position": "all",
        "summary": "IBJJF tutorial detailing technical fouls, guard pull without contact grip, sleeve-cuff violations, and penalty escalation.",
    },
    {
        "video_id": "nTT-OrtbOwU",
        "duration": "7:52",
        "title": "IBJJF Tutorials: Severe Foul Rules Video (Adult Brown/Black No-Gi Heel Hook & Reaping Update)",
        "article": "IBJJF Art. 6.2.3 (K/M)",
        "position": "open_guard",
        "summary": "Official IBJJF update explaining legal heel hooks and knee reaping in Adult Brown and Black Belt No-Gi while maintaining DQ enforcement in Gi and Master divisions.",
    },
    {
        "video_id": "OwuW9LwB4EM",
        "duration": "1:58",
        "title": "IBJJF First-Time Competitor Guide: What to Expect at Your First IBJJF Tournament",
        "article": "IBJJF Art. 8",
        "position": "all",
        "summary": "Official IBJJF walkthrough of bullpen check-in, gi inspection, weigh-ins right before first match, and mat etiquette.",
    },
    {
        "video_id": "typhIhcOIcs",
        "duration": "2:20",
        "title": "IBJJF Official Gi Checker Tool Inspection Standards",
        "article": "IBJJF Art. 8.1",
        "position": "all",
        "summary": "Official IBJJF demonstration of kimono sleeve width, lapel thickness, and sleeve/pant length measurements using the official Gi Checker tool.",
    },
    {
        "video_id": "oNKwGUd9DZg",
        "duration": "2:37",
        "title": "IBJJF Technique Feature: Tainan Dalpra Gator Roll Guard Pass",
        "article": "IBJJF Art. 4.2",
        "position": "standing_pass",
        "summary": "Official IBJJF channel breakdown of Tainan Dalpra passing open guard to stabilized 3-point side control.",
    },
]

# Key page clusters from IBJJF Rulebook v6.1 PDF to index verbatim excerpts when PDF is available
PDF_SECTION_CLUSTERS: list[tuple[str, str, str, list[int]]] = [
    ("ibjjf-pdf-sec-2-decisions", "IBJJF Rulebook PDF v6.1 Excerpt: Rendering Decisions (Art. 2)", "IBJJF Art. 2", [15, 16]),
    ("ibjjf-pdf-sec-4-scoring", "IBJJF Rulebook PDF v6.1 Excerpt: Point Scoring Positions (Art. 4.1-4.6)", "IBJJF Art. 4.1 - 4.6", [18, 19, 20, 21, 22, 23]),
    ("ibjjf-pdf-sec-5-advantages", "IBJJF Rulebook PDF v6.1 Excerpt: Advantages (Art. 5)", "IBJJF Art. 5", [24, 25]),
    ("ibjjf-pdf-sec-6-stalling-fouls", "IBJJF Rulebook PDF v6.1 Excerpt: Lack of Combativeness and Illegal Grips (Art. 6.3)", "IBJJF Art. 6.3", [26, 27, 28]),
    ("ibjjf-pdf-sec-6-2-illegal-moves", "IBJJF Rulebook PDF v6.1 Excerpt: Prohibited Techniques and Knee Reaping (Art. 6.2)", "IBJJF Art. 6.2", [29, 30, 31, 32, 33]),
    ("ibjjf-pdf-sec-7-penalties", "IBJJF Rulebook PDF v6.1 Excerpt: Sequence of Penalties (Art. 7)", "IBJJF Art. 7", [34]),
]


def _load_or_fetch_rulebook_pdf_bytes(fetch_online: bool = True, pdf_path: Path | None = None) -> bytes | None:
    """Loads cached IBJJF Rulebook PDF bytes or downloads from ibjjf.com if reachable."""
    for candidate in (pdf_path, SCRATCH_CACHED_PDF):
        if candidate and candidate.exists():
            data = candidate.read_bytes()
            if data.startswith(b"%PDF"):
                return data
    if not fetch_online:
        return None

    pdf_url = IBJJF_DEFAULT_PDF_URL
    try:
        req = urllib.request.Request(IBJJF_BOOKS_VIDEOS_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        blobs = re.findall(r"/rails/active_storage/blobs/redirect/[^\s\"'<>]+Rules_EN\.pdf", html)
        if blobs:
            pdf_url = "https://ibjjf.com" + blobs[0]
    except (OSError, ValueError) as exc:
        logger.debug("IBJJF books-videos discovery skipped: %s", exc)

    try:
        req = urllib.request.Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        if data.startswith(b"%PDF"):
            return data
    except (OSError, ValueError) as exc:
        logger.warning("IBJJF Rulebook PDF download unavailable (%s); using curated article corpus.", exc)
    return None


def _clean_pdf_page_text(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if (
            "INTERNATIONAL BRAZILIAN JIU-JITSU FEDERATION" in upper
            or "VICTOR GRUZMAN" in upper
            or "ALL RIGHTS RESERVED" in upper
            or "IVAN TRINDADE" in upper
        ):
            continue
        lines.append(stripped)
    return sanitize_text(" ".join(lines))


def _parse_pdf_documents(pdf_bytes: bytes) -> list[KnowledgeDocument]:
    docs: list[KnowledgeDocument] = []
    reader = PdfReader(BytesIO(pdf_bytes))
    num_pages = len(reader.pages)
    for doc_id, title, article, page_nums in PDF_SECTION_CLUSTERS:
        chunks = []
        valid_pages = []
        for pg in page_nums:
            if 1 <= pg <= num_pages:
                cleaned = _clean_pdf_page_text(reader.pages[pg - 1].extract_text() or "")
                if cleaned:
                    chunks.append(f"[Page {pg}] {cleaned[:900]}")
                    valid_pages.append(pg)
        if chunks:
            docs.append(
                KnowledgeDocument(
                    doc_id=doc_id,
                    corpus="ibjjf",
                    title=title,
                    section_or_belt=article,
                    position="all",
                    text=" ".join(chunks)[:2600],
                    source_url=IBJJF_DEFAULT_PDF_URL,
                    timestamp_sec=None,
                    tags=["ibjjf", "rulebook pdf", article.lower()],
                    metadata={
                        "article": article,
                        "source_kind": "official_rulebook_pdf",
                        "pdf_version": "v6.1-2024JUN",
                        "pdf_pages": valid_pages,
                    },
                )
            )
    return docs


def _parse_extra_scratch_ibjjf_videos(existing_ids: set[str]) -> list[KnowledgeDocument]:
    if not SCRATCH_IBJJF_VIDEOS_TXT.exists():
        return []
    extra_docs: list[KnowledgeDocument] = []
    for raw_line in SCRATCH_IBJJF_VIDEOS_TXT.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = [p.strip() for p in raw_line.split("|")]
        if len(parts) < 4:
            continue
        vid, dur, _channel, raw_title = parts[0], parts[1], parts[2], parts[3]
        if not vid or vid in existing_ids:
            continue
        existing_ids.add(vid)
        clean_title = sanitize_text(raw_title)
        extra_docs.append(
            KnowledgeDocument(
                doc_id=f"ibjjf-video-{vid.lower()}",
                corpus="ibjjf",
                title=f"IBJJF Official Video: {clean_title}",
                section_or_belt="IBJJF Official Video",
                position="all",
                text=f"Official IBJJF YouTube rules and competition resource ({dur}): {clean_title}.",
                source_url=f"https://www.youtube.com/watch?v={vid}",
                timestamp_sec=0,
                tags=["ibjjf", "official video", "youtube"],
                metadata={
                    "video_id": vid,
                    "duration": dur,
                    "source_kind": "official_youtube_video",
                    "video_url": f"https://www.youtube.com/watch?v={vid}",
                },
            )
        )
    return extra_docs


def build_ibjjf_corpus(
    fetch_online_pdf: bool = True,
    pdf_path: Path | None = None,
) -> tuple[list[KnowledgeDocument], dict[str, Any]]:
    """Builds the structured IBJJF Rulebook + Official YouTube Rules Video corpus."""
    docs: list[KnowledgeDocument] = []

    for item in STRUCTURED_IBJJF_ARTICLES:
        docs.append(
            KnowledgeDocument(
                doc_id=item["doc_id"],
                corpus="ibjjf",
                title=item["title"],
                section_or_belt=item["section_or_belt"],
                position=item["position"],
                text=item["text"],
                source_url=item["source_url"],
                timestamp_sec=0 if "youtube.com" in item["source_url"] else None,
                tags=item["tags"],
                metadata={
                    **item["metadata"],
                    "source_kind": "structured_article",
                    "video_url": item["source_url"] if "youtube.com" in item["source_url"] else "",
                },
            )
        )

    seen_video_ids: set[str] = set()
    for vid_item in OFFICIAL_IBJJF_VIDEOS:
        vid = vid_item["video_id"]
        seen_video_ids.add(vid)
        url = f"https://www.youtube.com/watch?v={vid}"
        docs.append(
            KnowledgeDocument(
                doc_id=f"ibjjf-video-{vid.lower()}",
                corpus="ibjjf",
                title=vid_item["title"],
                section_or_belt=vid_item["article"],
                position=vid_item["position"],
                text=(
                    f"{vid_item['summary']} Referenced rule: {vid_item['article']}. "
                    f"Duration: {vid_item['duration']}."
                ),
                source_url=url,
                timestamp_sec=0,
                tags=["ibjjf", "official video", vid_item["article"].lower()],
                metadata={
                    "article": vid_item["article"],
                    "video_id": vid,
                    "duration": vid_item["duration"],
                    "source_kind": "official_youtube_video",
                    "video_url": url,
                },
            )
        )

    docs.extend(_parse_extra_scratch_ibjjf_videos(seen_video_ids))

    pdf_bytes = _load_or_fetch_rulebook_pdf_bytes(fetch_online=fetch_online_pdf, pdf_path=pdf_path)
    pdf_parsed_count = 0
    if pdf_bytes:
        pdf_docs = _parse_pdf_documents(pdf_bytes)
        pdf_parsed_count = len(pdf_docs)
        docs.extend(pdf_docs)

    summary = {
        "structured_articles": len(STRUCTURED_IBJJF_ARTICLES),
        "official_videos": len(seen_video_ids),
        "pdf_excerpts": pdf_parsed_count,
        "pdf_ingested": pdf_parsed_count > 0,
        "total_ibjjf_docs": len(docs),
    }
    return docs, summary
