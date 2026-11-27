"""IBJJF Prohibited Techniques & Technical Foul Matrix (Rulebook Section 6.2 & 6.3).

Covers Juvenile, Adult, and Master divisions across White, Blue, Purple, Brown, and Black belts
for both Gi and No-Gi competition.
"""

from __future__ import annotations

from dataclasses import dataclass

from rollmetrics.rules.schema import AgeDivision, BeltRank, UniformType


@dataclass(frozen=True)
class FoulVerdict:
    technique_slug: str
    severity: str  # "legal" | "minor_foul" | "serious_foul_dq"
    article: str
    explanation: str


# Canonical technique slugs detected by the vision adjudication layer
FOUL_CATALOG: dict[str, dict] = {
    # --- Universal Severe / Technical Disqualifications (All Belts, Gi & No-Gi) ---
    "slam": {
        "article": "IBJJF Art. 6.2.3 (A)",
        "label": "Slamming opponent from guard or submission",
        "rule": "always_dq",
    },
    "spinal_lock_without_choke": {
        "article": "IBJJF Art. 6.2.3 (B/F)",
        "label": "Spinal lock / neck crank / twister without choke",
        "rule": "always_dq",
    },
    "scissor_takedown": {
        "article": "IBJJF Art. 6.2.3 (J)",
        "label": "Scissor takedown (Kani Basami)",
        "rule": "always_dq",
    },
    "suplex_head_neck": {
        "article": "IBJJF Art. 6.2.3 (V)",
        "label": "Suplex landing on opponent's head or neck",
        "rule": "always_dq",
    },
    "small_joint_manipulation": {
        "article": "IBJJF Art. 6.2.3 (N)",
        "label": "Bending fewer than four fingers or toes",
        "rule": "always_dq",
    },
    # --- White-Belt Specific Prohibitions ---
    "jumping_closed_guard": {
        "article": "IBJJF Art. 6.2.2 (White Belt Table #5)",
        "label": "Jumping into closed guard against a standing opponent",
        "rule": "white_only_dq",
    },
    "wristlock": {
        "article": "IBJJF Art. 6.2.2 (Table #12)",
        "label": "Wristlock submission",
        "rule": "blue_and_above_adult_master",
    },
    "single_leg_head_outside": {
        "article": "IBJJF Art. 6.2.2 (Table #21)",
        "label": "Single-leg takedown with head outside forcing opponent head-down",
        "rule": "blue_and_above_adult_master",
    },
    # --- Upper-Belt Leg/Muscle Compression Locks ---
    "bicep_slicer": {
        "article": "IBJJF Art. 6.2.2 (Table #14)",
        "label": "Bicep slicer",
        "rule": "brown_black_adult_master",
    },
    "calf_slicer": {
        "article": "IBJJF Art. 6.2.2 (Table #15)",
        "label": "Calf slicer",
        "rule": "brown_black_adult_master",
    },
    "kneebar": {
        "article": "IBJJF Art. 6.2.2 (Table #16)",
        "label": "Straight kneebar",
        "rule": "brown_black_adult_master",
    },
    "toe_hold": {
        "article": "IBJJF Art. 6.2.2 (Table #17)",
        "label": "Toe hold / figure-four footlock",
        "rule": "brown_black_adult_master",
    },
    # --- Reaping & Heel Hooks (Legal ONLY in Adult Brown & Black No-Gi!) ---
    "knee_reap": {
        "article": "IBJJF Art. 6.2.3 (M) / Adult Brown-Black No-Gi Exception",
        "label": "Knee reaping across standing/trapped leg",
        "rule": "adult_brown_black_nogi_only",
    },
    "heel_hook": {
        "article": "IBJJF Art. 6.2.3 (K) / Adult Brown-Black No-Gi Exception",
        "label": "Inside or outside heel hook",
        "rule": "adult_brown_black_nogi_only",
    },
    # --- Minor / Technical Fouls (Penalty Cascade, Not Immediate DQ) ---
    "fingers_inside_sleeve_or_pant_cuff": {
        "article": "IBJJF Art. 6.3.2 (F)",
        "label": "Hooking fingers inside opponent's sleeve or pant cuff",
        "rule": "minor_foul",
    },
    "guard_pull_without_grip": {
        "article": "IBJJF Art. 6.3.2 (J)",
        "label": "Sitting/pulling guard without an established contact grip",
        "rule": "minor_foul",
    },
    "stalling": {
        "article": "IBJJF Art. 6.3.2 (A/B)",
        "label": "Lack of combativeness / stalling for 20 seconds",
        "rule": "minor_foul",
    },
    "fleeing_mat_bounds": {
        "article": "IBJJF Art. 6.3.2 (C)",
        "label": "Deliberately exiting match area to avoid sweep or pass",
        "rule": "minor_foul",
    },
    "grabbing_rashguard_or_shorts_nogi": {
        "article": "IBJJF Art. 6.3.2 (G - No-Gi)",
        "label": "Gripping opponent's rashguard or shorts in No-Gi",
        "rule": "nogi_minor_foul",
    },
}


SLUG_ALIASES: dict[str, str] = {
    "kani_basami": "scissor_takedown",
    "flying_scissor_takedown": "scissor_takedown",
    "neck_crank": "spinal_lock_without_choke",
    "twister": "spinal_lock_without_choke",
    "suplex": "suplex_head_neck",
    "finger_lock": "small_joint_manipulation",
    "jumping_guard": "jumping_closed_guard",
    "reap": "knee_reap",
    "reaping": "knee_reap",
    "inside_heel_hook": "heel_hook",
    "outside_heel_hook": "heel_hook",
    "fingers_in_sleeve": "fingers_inside_sleeve_or_pant_cuff",
    "fingers_in_pant_cuff": "fingers_inside_sleeve_or_pant_cuff",
    "pulling_guard_without_grip": "guard_pull_without_grip",
    "fleeing": "fleeing_mat_bounds",
    "grabbing_shorts": "grabbing_rashguard_or_shorts_nogi",
    "grabbing_rashguard": "grabbing_rashguard_or_shorts_nogi",
}


def check_technique_legality(
    technique_slug: str,
    belt: BeltRank,
    uniform: UniformType,
    age_division: AgeDivision,
) -> FoulVerdict:
    raw_slug = (technique_slug or "").strip().lower()
    slug = SLUG_ALIASES.get(raw_slug, raw_slug)
    if not slug or slug not in FOUL_CATALOG:
        return FoulVerdict(
            technique_slug=raw_slug or "standard_exchange",
            severity="legal",
            article="IBJJF Art. 4",
            explanation="Permitted technique for this division.",
        )

    spec = FOUL_CATALOG[slug]
    out_slug = raw_slug or slug
    rule = spec["rule"]
    art = spec["article"]
    label = spec["label"]

    if rule == "always_dq":
        return FoulVerdict(out_slug, "serious_foul_dq", art, f"Immediate DQ in all divisions: {label}.")

    if rule == "white_only_dq":
        if belt == BeltRank.WHITE:
            return FoulVerdict(out_slug, "serious_foul_dq", art, f"Immediate DQ at White Belt: {label}.")
        return FoulVerdict(out_slug, "legal", art, f"Legal above White Belt ({belt.value.title()}): {label}.")

    if rule == "blue_and_above_adult_master":
        if belt == BeltRank.WHITE or age_division == AgeDivision.JUVENILE:
            return FoulVerdict(
                out_slug,
                "serious_foul_dq",
                art,
                f"Illegal for {age_division.value.title()} {belt.value.title()} Belt: {label}.",
            )
        return FoulVerdict(
            out_slug,
            "legal",
            art,
            f"Legal at {age_division.value.title()} {belt.value.title()} Belt.",
        )

    if rule == "brown_black_adult_master":
        if age_division != AgeDivision.JUVENILE and belt in (BeltRank.BROWN, BeltRank.BLACK):
            return FoulVerdict(out_slug, "legal", art, f"Legal at {belt.value.title()} Belt: {label}.")
        return FoulVerdict(
            out_slug,
            "serious_foul_dq",
            art,
            f"Immediate DQ below Brown Belt (current: {belt.value.title()}): {label}.",
        )

    if rule == "adult_brown_black_nogi_only":
        if (
            uniform == UniformType.NOGI
            and age_division == AgeDivision.ADULT
            and belt in (BeltRank.BROWN, BeltRank.BLACK)
        ):
            return FoulVerdict(
                out_slug,
                "legal",
                art,
                f"Legal in Adult {belt.value.title()} No-Gi: {label}.",
            )
        return FoulVerdict(
            out_slug,
            "serious_foul_dq",
            art,
            f"Immediate DQ in {age_division.value.title()} {belt.value.title()} {uniform.value.upper()}: {label} (permitted only in Adult Brown/Black No-Gi).",
        )

    if rule == "minor_foul":
        return FoulVerdict(out_slug, "minor_foul", art, f"Technical foul penalty: {label}.")

    if rule == "nogi_minor_foul":
        if uniform == UniformType.NOGI:
            return FoulVerdict(out_slug, "minor_foul", art, f"No-Gi technical foul penalty: {label}.")
        return FoulVerdict(out_slug, "legal", art, "Cloth gripping is legal in Gi divisions.")

    return FoulVerdict(out_slug, "legal", art, label)
