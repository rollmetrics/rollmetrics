"""Public Caio Terra Association (CTA) curriculum corpus builder."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rollmetrics.knowledge.schema import KnowledgeDocument, sanitize_text

SCRATCH_SUBS_DIR = Path(
    "/Users/korymath/.gemini/antigravity-cli/brain/178829f3-7bbf-4423-82f2-e81ac2a20887/"
    "scratch/rollmetrics_kb/subs"
)
SCRATCH_STERLING_TXT = Path(
    "/Users/korymath/.gemini/antigravity-cli/brain/178829f3-7bbf-4423-82f2-e81ac2a20887/"
    "scratch/rollmetrics_kb/cta_sterling_videos.txt"
)
SCRATCH_CAIO_REEL_JSON = Path(
    "/Users/korymath/.gemini/antigravity-cli/brain/178829f3-7bbf-4423-82f2-e81ac2a20887/"
    "scratch/caio_reel/caio_reel_chapters.json"
)


def yt_timestamp_url(video_id: str, timestamp_sec: int) -> str:
    """Formats canonical YouTube timestamp jump-link."""
    sec = max(0, int(timestamp_sec))
    return f"https://www.youtube.com/watch?v={video_id}&t={sec}s"


# Curated high-precision CTA curriculum chapters across all core BJJ positions
# Derived from Caio Terra Modern Jiu-Jitsu Parts 1-4, @caioterrabjj, and CTA Academy curriculum.
CORE_CTA_CHAPTERS: list[dict[str, Any]] = [
    # --- CLOSED GUARD ---
    {
        "doc_id": "cta-cg-overhook-collar-choke",
        "title": "Caio Terra Closed Guard: Cross-Collar Overhook Choke",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "4-7AYrBCOvQ",
        "timestamp_sec": 4797,
        "cue": "Wrap deep overhook into far collar, grab back gi material thumb-up, tuck elbow under chin, hip-escape and push away with outside foot on hip.",
        "text": (
            "From Closed Guard Bottom, swim under the opponent's arm to trap an overhook and feed a deep "
            "cross-collar grip. Escape hips outside, plant foot on the hip to stop them following, reach "
            "over the shoulder to Bunch back-of-gi cloth, rotate the choking wrist THUMB-UP against the "
            "carotid, tuck the elbow underneath, and extend legs away for the tap."
        ),
        "tags": ["closed guard", "overhook", "collar choke", "cross collar", "caio terra", "part 1"],
    },
    {
        "doc_id": "cta-cg-overhook-triangle",
        "title": "Caio Terra Closed Guard: Overhook to Triangle Choke Setup",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "4-7AYrBCOvQ",
        "timestamp_sec": 5112,
        "cue": "Establish overhook + collar control; when opponent pushes your choking elbow in, clear your leg over their shoulder and lock high triangle.",
        "text": (
            "From Closed Guard Overhook when the opponent defends the cross-collar overhook choke by pushing "
            "down on your elbow, immediately thread your ipsilateral leg up and over their free shoulder to "
            "lock the triangle. Bridge onto shoulders to pass their arm across your stomach, control your "
            "choking shin, cut a 90-degree angle with foot on hip, and squeeze knees with downward hamstring bite."
        ),
        "tags": ["closed guard", "overhook", "triangle", "overhook triangle", "caio terra", "part 1"],
    },
    {
        "doc_id": "cta-cg-overhook-sweep-armbar-dilemma",
        "title": "Caio Terra Closed Guard: Overhook Pendulum Sweep & Straight Armbar Dilemma",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "4-7AYrBCOvQ",
        "timestamp_sec": 4468,
        "cue": "Overhook + lapel grip, underhook far leg pulling ear tight to knee, wing hips perpendicular and swing legs toward head (or hook neck for straight armbar if they base).",
        "text": (
            "Break posture two-on-one, wrap deep overhook to collar, and underhook the opponent's far leg "
            "pulling your head tight to their knee. Wing hips out until perpendicular and swing legs toward "
            "their head (never sideways) to come up into top mount/side control (+2 sweep). At t=4655s, if "
            "they step up to stop the sweep, switch collar hand behind their tricep and hook the neck for "
            "an immediate overhook straight armbar."
        ),
        "tags": ["closed guard", "overhook sweep", "pendulum sweep", "armbar", "underhook leg", "caio terra"],
    },
    {
        "doc_id": "cta-cg-armdrag-to-pendulum-and-back",
        "title": "CTA Closed Guard: 2-on-1 Arm Drag to Back Take or Pendulum Sweep to Mount",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "KCJ54kkfoGA",
        "timestamp_sec": 15,
        "cue": "Two-on-one cuff + tricep drag across centerline, hug lat/armpit tight so elbow cannot retract; climb to back control or underhook far leg to pendulum into Mount (+6 pts).",
        "text": (
            "Break the opponent's collar or belt grip and drag their tricep across your centerline while "
            "hip-escaping slightly to expose the lat. Clamp high over the back to take Back Control (+4 pts), "
            "or if the opponent drives back in to square up, scoop/underhook their near or far leg and "
            "pendulum your top leg toward their head to land cleanly in Mount (+2 sweep + +4 mount = +6 pts)."
        ),
        "tags": ["closed guard", "arm drag", "pendulum", "back take", "mount", "sweep"],
    },
    {
        "doc_id": "cta-cg-shoulder-clamp-anti-stack",
        "title": "CTA Closed Guard: 90-Degree Shoulder Clamp Guard (Anti-Stack & Armlock/Triangle)",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "eW8OtG8OWM8",
        "timestamp_sec": 29,
        "cue": "Cut 90 degrees onto one hip, bite top calf down over opponent's near shoulder with knees pinched (no crossed ankles needed) to kill tripod stack pressure and attack triangle/armbar/sweep.",
        "text": (
            "Against heavy tripod stands or stack pressure inside Closed Guard, transition from flat mid-back "
            "guard to a 90-degree Shoulder Clamp: overhook or pinch the opponent's near arm, pivot 90 degrees "
            "onto your outside hip, and clamp your top shin/calf tightly down over their near shoulder while "
            "your bottom knee shields across the hip line. This breaks spine alignment, prevents stacking, "
            "and chains directly into side-scissor sweeps, inside-triangle armbars, and omoplatas."
        ),
        "tags": ["closed guard", "shoulder clamp", "clamp guard", "anti stack", "tripod", "triangle", "omoplata"],
    },
    {
        "doc_id": "cta-cg-standing-double-ankle-sweep",
        "title": "Caio Terra Closed Guard: Standing Double-Ankle (Dummy) Sweep to Mount",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "4-7AYrBCOvQ",
        "timestamp_sec": 2823,
        "cue": "The moment opponent stands in closed guard, cup both ankles, drop hips low with knees pinched together, drive knees into thighs while pulling heels, and keep heel grips to technical stand into Mount.",
        "text": (
            "When the top opponent stands up in Closed Guard to break your ankles, immediately cup behind "
            "both of their heels/ankles, open your guard dropping your hips straight to the mat, pinch BOTH "
            "knees tight against their hips/inner thighs, and push with your legs while pulling both ankles. "
            "Hold their heels after they fall so they cannot scramble up, post one elbow and rear hand with "
            "shoulder turned down, and technical-stand into top position or Mount (+2 sweep + +4 mount)."
        ),
        "tags": ["closed guard", "double ankle sweep", "dummy sweep", "standing break", "mount", "caio terra"],
    },
    {
        "doc_id": "cta-cg-standing-underhook-sweep-to-mount",
        "title": "Caio Terra Closed Guard: Standing Grip-Break & Leg-Underhook (Waiter/Muscle) Sweep to Mount",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "4-7AYrBCOvQ",
        "timestamp_sec": 3247,
        "cue": "Rotate palm to strip standing sleeve grip, deep-underhook near ankle pulling ear to knee, post free palm flat near head, drive hips into their knee, and technical-stand to Mount.",
        "text": (
            "Against an opponent standing up in Closed Guard, strip their sleeve grip by rotating their palm "
            "open, underhook their opposite leg deeply at the ankle/calf, post your free palm on the mat "
            "near your head with fingers forward, lift and drive your hips sideways into their knee to collapse "
            "their base, and rotate shoulder-down over their legs directly into Mount (+6 cumulative points)."
        ),
        "tags": ["closed guard", "underhook sweep", "muscle sweep", "standing", "mount", "caio terra"],
    },
    {
        "doc_id": "cta-cg-posture-breaks-and-kimura-no-situp",
        "title": "Caio Terra Closed Guard: Posture Breaks Opening Elbows & No-Sit-Up Kimura",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "4-7AYrBCOvQ",
        "timestamp_sec": 2080,
        "cue": "Pull knees to chest while flaring both opponent elbows outward whenever they posture; for Kimura (t=3709s), pin wrist to thigh and bring your knee up to trap shoulder instead of sitting up.",
        "text": (
            "Closed guard safety depends on breaking posture before the top player stands: flare both of the "
            "opponent's elbows wide while pulling your knees toward your chest (t=2080s). At t=3709s, Caio "
            "teaches never sitting up prematurely for a Kimura (which lets the opponent posture and straighten "
            "their arm); instead, prayer-swim inside, pin their wrist to your thigh, bring your ipsilateral "
            "knee up and grab your own shin to lock their shoulder down, then kick back to figure-four your wrist."
        ),
        "tags": ["closed guard", "posture break", "open elbows", "kimura", "posture", "caio terra"],
    },
    {
        "doc_id": "cta-cg-highest-pct-armbar-and-inside-triangle-armbar",
        "title": "Caio Terra Closed Guard: Highest-Percentage Armbar & Inside-Triangle Straight Armbar",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "4-7AYrBCOvQ",
        "timestamp_sec": 2143,
        "cue": "Push attacking elbow across centerline, clamp high armpit grip, foot on hip to lock high Pit-Stop bite over shoulder, thumb UP, hug wrist to chest, and curl hamstring downward (works from Closed Diamond too).",
        "text": (
            "Break posture, push the opponent's lapel-gripping elbow across centerline with both hands, "
            "control the head and clamp deep into the far armpit so the arm cannot pull free. Step on the hip "
            "to lock high over the shoulder, rotate opponent's THUMB UP, squeeze knees together without crossing "
            "ankles on classic armbar, or finish the Inside-Triangle Straight Armbar (Sankaku Juji-Gatame) "
            "with head still trapped inside the diamond by curling the top hamstring downward onto the neck."
        ),
        "tags": ["closed guard", "armbar", "straight armbar", "inside triangle armbar", "thumb up", "caio terra"],
    },
    {
        "doc_id": "cta-cg-triangle-finishing-and-shin-pin",
        "title": "Caio Terra Closed Guard: Triangle Choke Finishing Mechanics & Shin-Pin Entry vs Belt Grips",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "4-7AYrBCOvQ",
        "timestamp_sec": 2352,
        "cue": "Turn opponent's chin toward your choking hamstring (do not pull straight down early), bridge hips to frame elbow+tricep across stomach, grab your own shin, cut angle, and squeeze knees.",
        "text": (
            "Caio Terra's signature Triangle details (t=2352s): place hamstring flush across carotid, turn "
            "the opponent's head toward the hamstring rather than pulling chin to chest prematurely, bridge "
            "hips high and frame on elbow + tricep (never forearm) to pass the arm across your stomach, "
            "grab your choking shin, post foot on hip to cut angle, and lift hips slightly while squeezing "
            "knees. Against tight dual-belt grips (t=2627s), control both cuffs, hip out, pin one bicep with "
            "lower shin, kick across belly, and lock the triangle."
        ),
        "tags": ["closed guard", "triangle", "triangle choke", "shin pin", "belt grips", "caio terra"],
    },
    {
        "doc_id": "cta-cg-double-under-stack-counter",
        "title": "Caio Terra Closed Guard: Early Frame & Triangle Counter vs Double-Under Stack Pass",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "vNaFSONOK-E",
        "timestamp_sec": 12,
        "cue": "Before hips load past 90 degrees, uncross ankles, flare knees wide, drive dual-collar stiff-arm frames into clavicles while shoulder-walking back, or overhook one arm to lock triangle.",
        "text": (
            "Never stay flat holding closed ankles once an opponent swims double underhooks: uncross ankles "
            "early, flare knees heavy, stiff-arm both collars into the opponent's clavicles while shoulder-walking "
            "backward to flatten their hips, or isolate one sleeve/tricep as they reach under to thread the "
            "opposite leg free and snap directly into a Closed Guard Triangle Choke."
        ),
        "tags": ["closed guard", "double under", "stack defense", "stack pass", "collar frame", "triangle", "caio terra"],
    },
    {
        "doc_id": "cta-cg-guillotine-hipbump-kimura-trifecta",
        "title": "Caio Terra Closed Guard: Hip-Bump Sweep, Chin-Cup Guillotine & Triangle Follow-Up",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "4-7AYrBCOvQ",
        "timestamp_sec": 4057,
        "cue": "When opponent postures high, sit up to hip-bump/Kimura threat; wrap chin-cup monkey-grip guillotine keeping head OFF mat, or kick leg over wrist-controlling arm into triangle (t=4167s).",
        "text": (
            "When an opponent postures upright out of Closed Guard, sit up onto hand/elbow threatening the "
            "Catucada / Hip-Bump Sweep (`Bv5dEguu23g`). If they drive back forward, wrap across the back of "
            "the neck into Caio's chin-cup monkey-grip Guillotine keeping your head up off the mat (t=4057s); "
            "if they fight your choking wrist (t=4167s), hip-escape wide to that same side and throw your "
            "free leg over their defending shoulder into a clean Triangle."
        ),
        "tags": ["closed guard", "hip bump", "catucada", "guillotine", "triangle", "trifecta", "caio terra"],
    },
    {
        "doc_id": "cta-cg-omoplata-modified-overhook",
        "title": "CTA Academy Closed Guard: Omoplata Sweep & Finish from Modified Overhook",
        "belt": "white",
        "position": "closed_guard",
        "video_id": "4m5J4Uq5mWo",
        "timestamp_sec": 35,
        "cue": "Secure modified overhook + collar, block posture, pivot hips 90 degrees clearing leg in front of face, sit up controlling waist/hip so opponent cannot roll, and drive chest forward at 45 degrees.",
        "text": (
            "From Closed Guard Bottom with a modified overhook, push the opponent's far head/bicep away while "
            "pivoting 90 degrees to swing your leg over the trapped shoulder into Omoplata. Immediately reach "
            "across the lower back/waist (or block the far knee per Caio Part 1 t=3076s) before sitting up, "
            "scoot hips parallel to flatten their shoulder, and lean forward diagonally for the tap or +2 sweep."
        ),
        "tags": ["closed guard", "omoplata", "overhook", "sweep", "submission", "cta sterling"],
    },
    {
        "doc_id": "cta-cg-wristlock-blue-belt",
        "title": "Rudson Mateus (CTA) Closed Guard: Goose-Neck Wristlock vs Double-Bicep Control (Blue+ Belt)",
        "belt": "blue",
        "position": "closed_guard",
        "video_id": "qTb25DfJpIs",
        "timestamp_sec": 10,
        "cue": "Blue Belt and above only: trap opponent's elbow crease in your armpit/overhook and cup back of their hand to fold wrist toward forearm while pinching knees.",
        "text": (
            "Legal in Adult/Master Blue Belt and above (IBJJF Art. 6.2.2 #12; illegal DQ at White Belt and "
            "Juvenile): when the top player posts hands on biceps or chest inside Closed Guard, trap their "
            "elbow tightly against your chest/overhook so the arm cannot straighten and compress the back of "
            "their hand into a goose-neck wristlock."
        ),
        "tags": ["closed guard", "wristlock", "blue belt", "rudson mateus", "overhook"],
    },
    # --- HALF GUARD ---
    {
        "doc_id": "cta-hg-beating-smash-pass-frames",
        "title": "Caio Terra Half Guard: Defending and Countering Leg-Hug / Smash Pass with Early Frames",
        "belt": "white",
        "position": "half_guard",
        "video_id": "v0-Dnn2X4VE",
        "timestamp_sec": 25,
        "cue": "Frame cross-collar/throat + bicep BEFORE shoulders get pinned flat, stay on your bottom hip with top knee-shield active, and pummel underhook to coyote/back-take.",
        "text": (
            "In Half Guard Bottom against heavy smash-pass or leg-hugging pressure, Caio emphasizes early "
            "skeletal frames rather than muscling from flat on your back: anchor your bottom shoulder, frame "
            "across the collar/neck and cross-face bicep before head control is conceded, clear your knee "
            "shield inside, and swim the far underhook to come up on a single leg or take the back."
        ),
        "tags": ["half guard", "knee shield", "smash pass", "frames", "guard retention", "caio terra"],
    },
    {
        "doc_id": "cta-hg-underhook-to-back-take",
        "title": "Caio Terra Half Guard: Underhook to Coyote Dogfight & Back Take",
        "belt": "white",
        "position": "half_guard",
        "video_id": "R-27keZXOn4",
        "timestamp_sec": 18,
        "cue": "Block cross-face bicep first, shoot deep low underhook around far waist/hip, torque trapped ankle outward with bottom hook, and climb behind hips to Back Control.",
        "text": (
            "From Half Guard Bottom (also demonstrated in CTA Academy `93PVjltg8aU`), deny the top player's "
            "cross-face first, punch a low underhook tight behind their hip/waist keeping your ear glued to "
            "their chest so you cannot be Darced or guillotined, switch your half-guard hook to torque their "
            "trapped knee inward, and shuck behind their shoulders for +2 sweep or +4 Back Control."
        ),
        "tags": ["half guard", "underhook", "back take", "dogfight", "sweep", "caio terra"],
    },
    {
        "doc_id": "cta-hg-thumb-down-loop-choke",
        "title": "Caio Terra Half Guard: Thumb-Down Loop Choke & Kimura/Armbar Submissions",
        "belt": "white",
        "position": "half_guard",
        "video_id": "7l_aIecS76I",
        "timestamp_sec": 35,
        "cue": "Set knee-shield half guard with shallow thumb-down cross-collar grip; bait head-down pass entry, loop free forearm behind neck, and roll under to finish loop choke.",
        "text": (
            "From Knee-Shield Half Guard (`7l_aIecS76I` and `RcYLptSeuws`), establish a thumb-down collar "
            "grip at collarbone level while framing distance with your knee shield. As the passer drops their "
            "head to weave or smash the shield, pull their head under your choking wrist, shoot your second "
            "hand deep behind their neck to lock the loop, and spin underneath for a high-percentage choke."
        ),
        "tags": ["half guard", "loop choke", "knee shield", "submission", "caio terra"],
    },
    # --- SIDE CONTROL (ESCAPES & TOP) ---
    {
        "doc_id": "cta-sc-bottom-hip-frame-knee-elbow-escape",
        "title": "Caio Terra Side Control Escape: Forearm Hip-Frame & Neck-Frame to Closed Guard Recovery",
        "belt": "white",
        "position": "side_control",
        "video_id": "b7aT6TuSX1w",
        "timestamp_sec": 120,
        "cue": "Never push straight up with extended arms; lock inside forearm across near hip crease + top forearm across clavicle, bridge slightly, shrimp hips wide, thread bottom knee across belly to re-guard.",
        "text": (
            "From Bottom Side Control (Modern Jiu-Jitsu Part 4), avoid extending your arms into keylock/armbar "
            "danger. Anchor your near forearm into the opponent's hip crease and frame your far forearm across "
            "their throat/clavicle. Bridge onto one shoulder to create a wedge, hip-escape (shrimp) away, "
            "thread your bottom knee across the belt line, and square back to Closed Guard."
        ),
        "tags": ["side control", "side control escape", "shrimp", "hip escape", "frames", "guard recovery", "caio terra"],
    },
    {
        "doc_id": "cta-sc-bottom-north-south-retention-triangle",
        "title": "CTA Side Control & North-South Retention: Frame Recovery to Triangle Choke",
        "belt": "white",
        "position": "side_control",
        "video_id": "-814JgFcQ8A",
        "timestamp_sec": 20,
        "cue": "Keep elbows glued to ribs as opponent circles toward North-South, invert/pummel top shin onto bicep, square hips, and snap directly into Triangle Choke.",
        "text": (
            "When an opponent passes toward Side Control or circles to North-South (`-814JgFcQ8A`), keep elbows "
            "tight to your ribs, frame both hands on biceps/shoulders rather than reaching overhead, bring "
            "knees to chest to re-insert a shin shield on the near bicep, and trap head + one arm inside a "
            "counter Triangle Choke."
        ),
        "tags": ["side control", "north south", "guard retention", "escape", "triangle", "cta sterling"],
    },
    {
        "doc_id": "cta-sc-top-kob-kimura-armlock",
        "title": "CTA Side Control Top: Hip Control, Knee on Belly (+2), Kimura & Spinning Armlock",
        "belt": "white",
        "position": "side_control",
        "video_id": "IyPwhQ3I2g0",
        "timestamp_sec": 30,
        "cue": "Block near hip + cross-face shoulder first; pop to Knee on Belly (+2 pts held 3s) to force defensive frames, then isolate far arm for Kimura (`IyPwhQ3I2g0`) or Kimura-to-Armbar (`dLo6FohYX_0`).",
        "text": (
            "From Top Side Control (`Yd1I9jOTQyo`, `2O5xra-lJtk`, `e-MThXL0jQM`, `IyPwhQ3I2g0`, `dLo6FohYX_0`), "
            "pin the opponent's hips before chasing submissions. Pop cleanly to Knee on Belly with far foot "
            "posted for +2 IBJJF points; when the bottom player pushes your knee, scoop the near/far arm into "
            "Kimura control, step over the head, and finish the Kimura or tight top Armlock."
        ),
        "tags": ["side control", "knee on belly", "kimura", "armbar", "mount", "top control", "cta sterling"],
    },
    # --- MOUNT (ESCAPES & TOP) ---
    {
        "doc_id": "cta-mt-bottom-hip-frame-elbow-knee-escape",
        "title": "Caio Terra Mount Escape: Low Hip-Frame Snake & Elbow-Knee Escape to Guard",
        "belt": "white",
        "position": "mount",
        "video_id": "xp6xTVThYAo",
        "timestamp_sec": 467,
        "cue": "Brace stacked hands/forearm on hip line and snake shoulders upward so opponent stays low on waist; turn onto one hip, wedge knee inside with elbow tight to ribs, and recover Half/Closed Guard.",
        "text": (
            "Caio Terra's primary Mount Escape (Modern Jiu-Jitsu Part 2 `xp6xTVThYAo` t=82s & t=467s): never "
            "let the top player climb into high mount. Place one forearm across their hip line reinforced by "
            "the second hand and snake your upper body backward until their knees sit low near your hips. "
            "Turn onto your side, trap their ankle or push their knee with elbow tight to your ribs, slide "
            "your bottom knee through into Half Guard, and square to Closed Guard."
        ),
        "tags": ["mount", "mount escape", "elbow knee", "shrimp", "frames", "caio terra", "part 2"],
    },
    {
        "doc_id": "cta-mt-bottom-upa-and-submission-defense",
        "title": "Caio Terra Mount Escape: Trap-and-Roll (Upa) & Mount Armbar Defense",
        "belt": "white",
        "position": "mount",
        "video_id": "xp6xTVThYAo",
        "timestamp_sec": 190,
        "cue": "When opponent hugs head or leans chest-low in mount, trap same-side arm above elbow + block same-side foot, bridge diagonally over shoulder into Closed Guard; vs Mount Armbar (t=873s), clasp rear thigh/gi.",
        "text": (
            "From Bottom Mount when the opponent wraps your head (t=190s) or posts low (t=267s), trap one arm "
            "at the tricep/elbow so they cannot post, block their ipsilateral foot with yours, and bridge high "
            "over your shoulder into top Closed Guard. Against a Mount Armbar (t=873s), answer-the-phone/clasp "
            "your attacked arm, hug behind their thigh or pant knee, and bridge into them to stack and clear head."
        ),
        "tags": ["mount", "upa", "trap and roll", "armbar escape", "mount defense", "caio terra"],
    },
    {
        "doc_id": "cta-mt-top-high-mount-choke-and-smount-armbar",
        "title": "Caio Terra Top Mount: Walking to High Mount, Cross-Collar Choke & S-Mount Armbar",
        "belt": "white",
        "position": "mount",
        "video_id": "xp6xTVThYAo",
        "timestamp_sec": 3072,
        "cue": "Pin wrist or pry elbow across centerline to slide knees high into armpits; feed deep thumb-in cross-collar choke (t=3072s) or pivot to S-Mount (`xp6xTVThYAo` t=3576s / `mhuXWOfEzBM`) for zero-space armbar.",
        "text": (
            "From Top Mount (+4 pts), systematically walk knees into the opponent's armpits (High Mount) by "
            "attacking keylock/cross-face grips. At t=3072s, feed a deep four-finger collar grip, open the chin "
            "with your forearm, set the second grip thumb-in or grab shoulder cloth, and drop elbows to the mat "
            "for the Cross-Collar Choke. At t=3576s (and `mhuXWOfEzBM` vs frames), slide into tight S-Mount "
            "keeping weight heavy on chest before stepping delicately over the face for the Armbar."
        ),
        "tags": ["mount", "high mount", "s-mount", "cross collar choke", "armbar", "top mount", "caio terra"],
    },
    # --- TURTLE (BOTTOM RECOVERY & TOP BREAKDOWN) ---
    {
        "doc_id": "cta-tr-bottom-turtle-guard-roll-and-sitout",
        "title": "Caio Terra Bottom Turtle: Protecting Hooks/Neck, Hip-Roll to Closed Guard & Sit-Out Reversal",
        "belt": "white",
        "position": "turtle",
        "video_id": "xp6xTVThYAo",
        "timestamp_sec": 1272,
        "cue": "Keep elbows glued inside knee-hip gap and chin tucked; tuck near hip/shoulder underneath immediately to re-lock Closed Guard (0 pts conceded), or trap opponent's tricep and sit-out/roll (`xp6xTVThYAo` t=1731s).",
        "text": (
            "In Bottom Turtle (Part 2 `xp6xTVThYAo` t=1272s, t=1619s, t=1731s), never stay frozen on hands and "
            "knees leaving space between elbows and knees where the top player inserts hooks (+4 pts) or collar "
            "grips. Seal elbows inside thighs while protecting your lapel; tuck your inside shoulder and hip "
            "underneath to roll smoothly back into Closed Guard (0 pts / 0 adv conceded), or clamp their body-lock "
            "tricep above the elbow (t=1731s 2006 Worlds reversal) and roll them across your shoulder."
        ),
        "tags": ["turtle", "bottom turtle", "guard recovery", "granby", "sit out", "front headlock", "caio terra"],
    },
    {
        "doc_id": "cta-tr-top-turtle-breakdown-hooks-and-chokes",
        "title": "Caio Terra Top Turtle: Hip-Pin Control, Single-Hook Drag to Back Control & Darce/Clock Choke",
        "belt": "white",
        "position": "turtle",
        "video_id": "xp6xTVThYAo",
        "timestamp_sec": 4067,
        "cue": "Never jump a loose arm-out gi guillotine from front headlock; stay behind elbows keeping weight on opponent's hips, establish seat-belt/lapel grip, Tilt toward top hook (`xp6xTVThYAo` t=4358s) for +4 Back Control or Darce (`RUbY8UvTgbA`).",
        "text": (
            "From Top Turtle / Front Headlock in the Gi (`xp6xTVThYAo` t=4067s & t=4358s), Caio warns against "
            "pulling guard for low-percentage arm-out gi guillotines (which forfeits top position). Keep heavy "
            "chest pressure behind their shoulder/hip line, feed collar or seat-belt control, insert the near/top "
            "hook and pull them diagonally into your lap to secure the second hook (+4 Back Control), or thread "
            "the Darce Choke (`RUbY8UvTgbA`) / Clock Choke when they shell flat."
        ),
        "tags": ["turtle", "top turtle", "front headlock", "back take", "seat belt", "clock choke", "darce", "caio terra"],
    },
    # --- BACK CONTROL (ATTACKS & ESCAPES) ---
    {
        "doc_id": "cta-bk-back-control-hooks-collar-and-rnc",
        "title": "Caio Terra Back Control: Uncrossed-Hook Retention, Sliding Collar/Bow-and-Arrow & RNC/Armbar",
        "belt": "white",
        "position": "back_control",
        "video_id": "xp6xTVThYAo",
        "timestamp_sec": 4968,
        "cue": "Keep chest glued to upper back with seat-belt (over-shoulder choking arm, under-armpit control arm) and heels uncrossed; stretch top lapel thumb-in under chin for Sliding Collar / Bow-and-Arrow (`akOd9hKZgC8`) or switch to Back Armbar (t=4825s).",
        "text": (
            "From Back Control (+4 pts, `xp6xTVThYAo` t=4437s, t=4649s, t=4825s, t=4968s plus `9NHz4FTfJ-k` and "
            "`akOd9hKZgC8`), never cross ankles low between the opponent's legs (vulnerable to ankle lock). "
            "Maintain tight chest-to-scapula connection, open and feed the deep top collar under the chin while "
            "controlling the far lapel or pant knee for Sliding Collar / Bow-and-Arrow Choke, or peel two-on-one "
            "wrist defense and throw top leg over the face into a Back Armlock (t=4825s)."
        ),
        "tags": ["back control", "collar choke", "bow and arrow", "rear naked choke", "rnc", "armbar", "caio terra"],
    },
    {
        "doc_id": "cta-bk-back-escapes-shoulder-to-mat",
        "title": "Caio Terra Back Escape: Blocking Second Hook, Collar-Grip Strip & Shoulder-to-Mat Escape",
        "belt": "white",
        "position": "back_control",
        "video_id": "xp6xTVThYAo",
        "timestamp_sec": 1936,
        "cue": "Protect neck two-on-one on choking wrist first (or peel bottom lapel grip at t=2154s), clear bottom hook, and slide BOTH shoulders flat to the mat on the underhook side to turn into Closed Guard (`IWvJyHhd9NI`).",
        "text": (
            "From Back Defense (`xp6xTVThYAo` t=1849s, t=1936s, t=2154s and CTA `IWvJyHhd9NI`), immediately "
            "fight the choking hand first (tucking ear to bicep at t=2025s or stripping the secondary collar "
            "hand at t=2154s so the choke loses anchor), shift hips over the opponent's bottom hook, put your "
            "head and outside shoulder flat on the mat on the non-choking side, and turn directly into Closed Guard."
        ),
        "tags": ["back control", "back escape", "collar choke escape", "hand fighting", "closed guard", "caio terra"],
    },
    # --- BUTTERFLY GUARD ---
    {
        "doc_id": "cta-bf-butterfly-sweeps-collar-drag-and-loop-choke",
        "title": "Caio Terra Butterfly Guard: Head-Forward Posture, Hook Sweep, Collar Drag & Loop Choke",
        "belt": "white",
        "position": "butterfly_guard",
        "video_id": "VWzT1iUMvEc",
        "timestamp_sec": 88,
        "cue": "Sit upright with head forward of hips (never flat on lower back); hit Collar Drag (t=528s), Hook Sweep with underhook/overhook (t=320s), or Caio's Unnoticed Loop Choke (t=400s).",
        "text": (
            "In Butterfly Guard (Modern Jiu-Jitsu Part 3 `VWzT1iUMvEc` t=88s-610s), keep forehead ahead of hips "
            "and hands winning inside tie-ups. Snap cross-collar + tricep for a Collar Drag to Turtle/Back "
            "(t=528s), elevate with sticky instep hook while pulling sleeve under you for the classic Hook Sweep "
            "(t=320s), or catch head-down passers in Caio's Unnoticed Loop Choke (t=400s)."
        ),
        "tags": ["butterfly guard", "hook sweep", "collar drag", "arm drag", "loop choke", "caio terra", "part 3"],
    },
    {
        "doc_id": "cta-bf-butterfly-guard-passing",
        "title": "Caio Terra Butterfly Guard Passing: Flattening Hips, Leg-Weave & Underhook Counter Pass",
        "belt": "white",
        "position": "butterfly_guard",
        "video_id": "VWzT1iUMvEc",
        "timestamp_sec": 1596,
        "cue": "Drive opponent flat onto their back first with elbows tight to hips and live toes; weave arm under top leg to clear bottom hook (t=1747s) or knee-slice middle vs underhook (t=2046s).",
        "text": (
            "Passing Butterfly Guard (`VWzT1iUMvEc` t=1596s, t=1747s, t=2046s and `_xM_Nep-Tso`): neutralize "
            "seated leverage by driving the opponent's shoulders flat to the mat with head low and elbows glued "
            "to hips. Step/tripod to extract one hook, lace under the top leg to pin knees sideways into Side "
            "Control (+3 pts), or counter their deep underhook by slicing your knee straight through the middle."
        ),
        "tags": ["butterfly guard", "guard pass", "butterfly pass", "leg weave", "side control", "caio terra"],
    },
    # --- OPEN GUARD (RDLR, COLLAR-SLEEVE, STRAIGHT FOOTLOCK) ---
    {
        "doc_id": "cta-og-rdlr-collar-sleeve-and-tripod",
        "title": "Caio Terra Open Guard: Reverse De La Riva Back Take, Collar-Sleeve Omoplata & Tripod Sweep",
        "belt": "white",
        "position": "open_guard",
        "video_id": "K77D_63NfaQ",
        "timestamp_sec": 30,
        "cue": "Maintain three points of contact (ankle cuff + collar/sleeve + active shin/RDLR hook); invert/kiss-the-dragon to Back Control (`K77D_63NfaQ`), hit Tripod Sweep (`wX4jNormznA`), or Omoplata (`MSggMpW2Pkk`).",
        "text": (
            "Against standing passers in Open Guard (`bMPn3KQVvyA`, `K77D_63NfaQ`, `wX4jNormznA`, `MSggMpW2Pkk`), "
            "anchor heel/ankle control before setting Reverse De La Riva or Collar-and-Sleeve frames. Off-balance "
            "immediately with Tripod/Sickle sweeps (+2 pts), thread Collar-Sleeve Omoplata when they tuck their "
            "elbow in combat base, or spin underneath RDLR to take Back Control (+4 pts)."
        ),
        "tags": ["open guard", "reverse de la riva", "rdlr", "collar sleeve", "tripod sweep", "omoplata", "back take", "caio terra"],
    },
    {
        "doc_id": "cta-og-caio-terra-straight-footlock",
        "title": "Caio Terra Straight Ankle / De La Riva Footlock (White-Belt Legal Outward Finish)",
        "belt": "white",
        "position": "open_guard",
        "video_id": "M_hPR8tLPNU",
        "timestamp_sec": 30,
        "cue": "Wrap wrist blade high under Achilles tendon with elbow pulled far back behind ribs, pinch knees without reaping across knee line, turn outward onto choking shoulder, and arch chest.",
        "text": (
            "The signature Caio Terra Straight Footlock (`M_hPR8tLPNU` and `mu-U87mdETU`): legal at all adult/master "
            "belt ranks including White Belt so long as your outside foot stays cleanly on the hip without reaping "
            "across the centerline and you turn outward toward the attacked foot side (IBJJF Art. 6.2.2 #13)."
        ),
        "tags": ["open guard", "straight ankle lock", "caio terra footlock", "de la riva", "leglock", "white belt legal"],
    },
    # --- STANDING PASSES ---
    {
        "doc_id": "cta-sp-standing-passes-toreando-and-kneecut",
        "title": "Caio Terra Standing Guard Passes: Distance Management, Ankle-Collar Pass, Knee-Cut & Double-Under Switch",
        "belt": "white",
        "position": "standing_pass",
        "video_id": "VWzT1iUMvEc",
        "timestamp_sec": 726,
        "cue": "Control safe outside distance until grips land first; pin one pant cuff + collar to circle past legs (t=988s), redirect foot across hip (t=1069s), drive knee-cut (t=1150s), or dump sideways off double-unders (t=1321s).",
        "text": (
            "Caio Terra's Standing Pass system (`VWzT1iUMvEc` t=726s-1477s): maintain safe distance outside seated "
            "guard until you dictate grips; control pant cuff at ankle + collar to flank into Side Control (+3 pts), "
            "push the ankle across into Leg-Drag when they follow (t=1069s), drive elbow-knee connected Knee-Cut "
            "when grips get stripped (t=1150s), or convert stalled Double-Unders by shucking legs sideways (t=1321s)."
        ),
        "tags": ["standing pass", "guard pass", "toreando", "leg drag", "knee cut", "double under", "caio terra"],
    },
    # --- TAKEDOWNS, GUARD PULLS & WRESTLE-UPS ---
    {
        "doc_id": "cta-td-collar-sleeve-guard-pull-foot-on-hip",
        "title": "Caio Terra Guard Pull Geometry: Collar + Four-Finger Cuff & Foot Always on Sleeve-Controlled Hip",
        "belt": "white",
        "position": "takedowns",
        "video_id": "VWzT1iUMvEc",
        "timestamp_sec": 2967,
        "cue": "Establish collar + four-finger outside cuff grip FIRST (never jump guard at White Belt!), plant your foot squarely on the SLEEVE-CONTROLLED hip, pull forward onto toes, and sit close under your heel into Closed Guard.",
        "text": (
            "In Modern Jiu-Jitsu Part 3 (`VWzT1iUMvEc` t=2967s), Caio teaches safe, penalty-free IBJJF guard "
            "pulling: secure collar and four-finger sleeve cuff grips first (avoiding Art. 6.3.2 J no-grip penalty "
            "and Art. 6.3.2 F inside-cuff foul), place your foot on the hip on the SAME SIDE you control their "
            "sleeve (so their free hand cannot ankle-pick your leg), pull them forward onto their toes for "
            "kuzushi, sit smoothly under your heel (zero mid-air hop, avoiding White Belt Art. 6.2.2 #5 DQ), "
            "and close guard immediately while breaking posture."
        ),
        "tags": ["takedowns", "guard pull", "foot on hip", "closed guard pull", "white belt", "sleeve hip", "caio terra"],
    },
    {
        "doc_id": "cta-td-single-leg-head-inside-and-wrestle-up",
        "title": "Caio Terra Takedowns & Wrestle-Up: Head-Inside Single Leg & Cuff-First Seated Wrestle-Up",
        "belt": "white",
        "position": "takedowns",
        "video_id": "VWzT1iUMvEc",
        "timestamp_sec": 2520,
        "cue": "Pop sleeve up, drop level keeping HEAD INSIDE glued to chest/ribs (100% legal at White Belt) while scooping low behind ankle/knee; when wrestling up from guard, secure pant cuff below knee BEFORE sitting up.",
        "text": (
            "From standing collar-and-sleeve (`VWzT1iUMvEc` t=2520s), lift the sleeve and elbow inside to level-change "
            "with your HEAD ON THE INSIDE against the opponent's sternum/ribs (avoiding White Belt Art. 6.2.2 #21 "
            "head-outside single-leg DQ), lock low behind the knee/ankle, and drive diagonally for +2 points. "
            "When wrestling up from Seated/Open Guard, grip the ankle or pant cuff below the knee FIRST with "
            "forehead glued inside so the top player cannot sprawl onto a front headlock over your turtle."
        ),
        "tags": ["takedowns", "single leg", "head inside", "wrestle up", "ankle pick", "white belt", "caio terra"],
    },
    {
        "doc_id": "cta-td-sprawl-defense-osoto-and-collar-drag",
        "title": "Caio Terra Stand-Up Defense & Throws: Double-Leg Sprawl, Single-Leg Belt Whizzer, Osoto-Gari & High-C",
        "belt": "white",
        "position": "takedowns",
        "video_id": "VWzT1iUMvEc",
        "timestamp_sec": 2423,
        "cue": "Sprawl hips heavy blocking shoulder + hip to circle behind double-leg shots (`VWzT1iUMvEc` t=2423s / `SS7LlohjKDk`); push head outside + belt whizzer vs single leg (t=2621s); chain lapel Osoto-Gari to ankle pick (t=2700s-2810s).",
        "text": (
            "Caio Terra's complete stand-up defensive & throwing sequence (`VWzT1iUMvEc` t=2423s-3013s plus "
            "`AvEeT-mZzCI` and `SS7LlohjKDk`): sprawl hips back framing shoulder + hip to spin behind double-leg "
            "shots to Turtle/Back; stuff single-leg shots by pushing the head outside with an over-the-back belt "
            "grip; and attack upright opponents with thumb-in lapel Osoto-Gari (t=2700s), Osoto-to-Ankle-Pick "
            "(t=2810s), Tomoe-Nage (t=2900s), or Double-Leg to High-C Rear Bodylock (`AvEeT-mZzCI` t=20s)."
        ),
        "tags": ["takedowns", "sprawl", "double leg defense", "single leg defense", "osoto gari", "tomoe nage", "caio terra"],
    },
]

# Non-technique promotional/workout clips in cta_sterling_videos.txt to skip so retrieval stays razor-sharp
_SKIP_STERLING_SUBSTRINGS = (
    "trx exercises",
    "500 reps",
    "heavy bag",
    "workout",
    "mma class",
    "kids",
    "little warriors",
    "piranha brazilian",
    "caio terra academy | sterling",
    "caio terre academy | sterling",
    "youness el oukili",
)


def _infer_sterling_position(title: str) -> str:
    low = title.lower()
    if "closed guard" in low or "close guard" in low and "back escape" not in low:
        return "closed_guard"
    if "half guard" in low:
        return "half_guard"
    if "side control" in low or "knee on belly" in low or "north south" in low:
        return "side_control"
    if "back attack" in low or "back escape" in low or "back defense" in low or "collar choke escape" in low:
        return "back_control"
    if "turtle" in low or "darce" in low:
        return "turtle"
    if "takedown" in low:
        return "takedowns"
    if "delariva" in low or "dela worm" in low or "50/50" in low or "omoplata" in low:
        return "open_guard"
    if "mount" in low:
        return "mount"
    return "closed_guard"


def _parse_sterling_curriculum_videos(seen_video_ids: set[str]) -> list[KnowledgeDocument]:
    if not SCRATCH_STERLING_TXT.exists():
        return []
    docs: list[KnowledgeDocument] = []
    for raw_line in SCRATCH_STERLING_TXT.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = [p.strip() for p in raw_line.split("|")]
        if len(parts) < 3:
            continue
        vid, dur, raw_title = parts[0], parts[1], parts[2]
        if not vid or vid in seen_video_ids:
            continue
        low_title = raw_title.lower()
        if any(skip in low_title for skip in _SKIP_STERLING_SUBSTRINGS):
            continue
        seen_video_ids.add(vid)
        clean_title = sanitize_text(raw_title.replace("➡️", "to"))
        pos = _infer_sterling_position(clean_title)
        belt = "adult_nogi_brown_black" if "heel hook" in low_title else "white"
        t_sec = 10
        url = yt_timestamp_url(vid, t_sec)
        docs.append(
            KnowledgeDocument(
                doc_id=f"cta-sterling-{vid.lower()}",
                corpus="cta",
                title=f"CTA Academy Curriculum: {clean_title}",
                section_or_belt=belt,
                position=pos,
                text=(
                    f"Caio Terra Academy structured curriculum lesson ({dur}): {clean_title}. "
                    f"Focuses on clean leverage, grip sequence, and positional control for {pos.replace('_', ' ')}."
                ),
                source_url=url,
                timestamp_sec=t_sec,
                tags=["cta", "cta academy", pos.replace("_", " ")],
                metadata={
                    "instructor": "Caio Terra Academy",
                    "video_id": vid,
                    "duration": dur,
                    "min_belt": "brown" if "heel hook" in low_title else "white",
                    "coaching_cue": f"Execute CTA {clean_title} sequence with early grip isolation and hip angle.",
                    "source_kind": "cta_sterling_curriculum",
                },
            )
        )
    return docs


def _parse_json3_transcript_chapters() -> tuple[list[KnowledgeDocument], int]:
    """Extracts gap-delimited spoken instruction chapters from Caio Terra Modern Jiu-Jitsu json3 subtitles."""
    if not SCRATCH_SUBS_DIR.exists():
        return [], 0

    part_specs = [
        ("4-7AYrBCOvQ.en.json3", "4-7AYrBCOvQ", "Part 1: Closed Guard", "closed_guard"),
        ("xp6xTVThYAo.en.json3", "xp6xTVThYAo", "Part 2: Mount, Turtle & Back", "mount"),
        ("VWzT1iUMvEc.en.json3", "VWzT1iUMvEc", "Part 3: Butterfly, Standing Passes & Takedowns", "butterfly_guard"),
    ]

    extracted: list[KnowledgeDocument] = []
    files_parsed = 0

    for filename, video_id, part_label, default_pos in part_specs:
        sub_path = SCRATCH_SUBS_DIR / filename
        if not sub_path.exists():
            continue
        files_parsed += 1
        payload = json.loads(sub_path.read_text(encoding="utf-8", errors="ignore"))
        events = payload.get("events", [])
        lines: list[tuple[float, float, str]] = []
        last_end = 0.0
        for ev in events:
            if "segs" not in ev:
                continue
            txt = "".join(s.get("utf8", "") for s in ev["segs"]).strip().replace("\n", " ")
            if not txt or txt.startswith("["):
                continue
            t = float(ev.get("tStartMs", 0)) / 1000.0
            dur = float(ev.get("dDurationMs", 2000)) / 1000.0
            lines.append((t, t - last_end, txt))
            last_end = max(last_end, t + dur)

        chapters: list[list[tuple[float, str]]] = []
        cur: list[tuple[float, str]] = []
        for t, gap, txt in lines:
            if gap >= 8.0 and cur:
                chapters.append(cur)
                cur = []
            cur.append((t, txt))
        if cur:
            chapters.append(cur)

        for idx, ch in enumerate(chapters):
            t0 = int(ch[0][0])
            transcript = sanitize_text(" ".join(seg_txt for _, seg_txt in ch))
            if len(transcript) < 320:
                continue
            low = transcript.lower()
            pos = default_pos
            if video_id == "xp6xTVThYAo":
                if "turtle" in low[:220]:
                    pos = "turtle"
                elif "back" in low[:260] or "hook" in low[:260] or "seat belt" in low[:260]:
                    pos = "back_control"
                else:
                    pos = "mount"
            elif video_id == "VWzT1iUMvEc":
                if t0 >= 2200:
                    pos = "takedowns"
                elif 720 <= t0 < 1590:
                    pos = "standing_pass"
                else:
                    pos = "butterfly_guard"

            mm, ss = t0 // 60, t0 % 60
            snippet = re.sub(r"\s+", " ", transcript[:750]).strip()
            extracted.append(
                KnowledgeDocument(
                    doc_id=f"cta-transcript-{video_id.lower()}-{t0:04d}s",
                    corpus="cta",
                    title=f"Caio Terra Modern Jiu-Jitsu ({part_label} @ {mm:02d}:{ss:02d})",
                    section_or_belt="white",
                    position=pos,
                    text=snippet,
                    source_url=yt_timestamp_url(video_id, t0),
                    timestamp_sec=t0,
                    tags=["caio terra", "modern jiu jitsu", "transcript", pos.replace("_", " ")],
                    metadata={
                        "instructor": "Caio Terra",
                        "video_id": video_id,
                        "part": part_label,
                        "chapter_index": idx,
                        "min_belt": "white",
                        "coaching_cue": snippet[:200],
                        "source_kind": "json3_transcript_chapter",
                    },
                )
            )
    return extracted, files_parsed


def _parse_caio_study_reel_chapters() -> list[KnowledgeDocument]:
    if not SCRATCH_CAIO_REEL_JSON.exists():
        return []
    data = json.loads(SCRATCH_CAIO_REEL_JSON.read_text(encoding="utf-8", errors="ignore"))
    docs: list[KnowledgeDocument] = []
    for ch in data.get("chapters", []):
        sec = int(ch.get("sec", 0))
        ext = ch.get("ext", "Ch").lower().replace(" ", "")
        label = sanitize_text(ch.get("label", "Closed Guard Study"))
        orig_label = sanitize_text(ch.get("orig_label", ""))
        url = yt_timestamp_url("lcvIIh9-DxM", sec)
        docs.append(
            KnowledgeDocument(
                doc_id=f"cta-study-reel-{ext}",
                corpus="cta",
                title=f"Caio Terra Closed Guard Uncut Study Reel ({ch.get('ext', '')}): {label}",
                section_or_belt="white",
                position="closed_guard",
                text=(
                    f"Uncut Caio Terra Closed Guard Masterclass chapter ({ch.get('time', '00:00')}): {label}. "
                    f"Original source lesson: {orig_label}."
                ),
                source_url=url,
                timestamp_sec=sec,
                tags=["caio terra", "closed guard", "study reel", label.lower()],
                metadata={
                    "instructor": "Caio Terra",
                    "video_id": "lcvIIh9-DxM",
                    "orig_url": ch.get("orig_url", ""),
                    "min_belt": "white",
                    "coaching_cue": label,
                    "source_kind": "caio_study_reel",
                },
            )
        )
    return docs


def build_cta_corpus() -> tuple[list[KnowledgeDocument], dict[str, Any]]:
    """Builds the complete public Caio Terra Association (CTA) curriculum corpus."""
    docs: list[KnowledgeDocument] = []
    seen_sterling_ids: set[str] = set()

    for ch in CORE_CTA_CHAPTERS:
        vid = ch["video_id"]
        t_sec = int(ch["timestamp_sec"])
        url = yt_timestamp_url(vid, t_sec)
        docs.append(
            KnowledgeDocument(
                doc_id=ch["doc_id"],
                corpus="cta",
                title=ch["title"],
                section_or_belt=ch["belt"],
                position=ch["position"],
                text=f"{ch['text']} CTA Cue: {ch['cue']}",
                source_url=url,
                timestamp_sec=t_sec,
                tags=ch["tags"],
                metadata={
                    "instructor": "Caio Terra",
                    "video_id": vid,
                    "min_belt": ch["belt"],
                    "coaching_cue": ch["cue"],
                    "source_kind": "curated_cta_masterclass",
                },
            )
        )

    sterling_docs = _parse_sterling_curriculum_videos(seen_sterling_ids)
    docs.extend(sterling_docs)

    reel_docs = _parse_caio_study_reel_chapters()
    docs.extend(reel_docs)

    transcript_docs, subs_files_parsed = _parse_json3_transcript_chapters()
    docs.extend(transcript_docs)

    positions_covered = sorted({d.position for d in docs})
    summary = {
        "curated_masterclass_chapters": len(CORE_CTA_CHAPTERS),
        "sterling_academy_videos": len(sterling_docs),
        "study_reel_chapters": len(reel_docs),
        "json3_transcript_chapters": len(transcript_docs),
        "json3_files_parsed": subs_files_parsed,
        "positions_covered": positions_covered,
        "total_cta_docs": len(docs),
    }
    return docs, summary
