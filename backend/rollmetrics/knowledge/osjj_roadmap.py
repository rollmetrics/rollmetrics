"""Public Open Source Jiu-Jitsu (OSJJ) and open-syllabus video-backed curriculum corpus."""

from __future__ import annotations

from typing import Any

from rollmetrics.knowledge.schema import KnowledgeDocument

OSJJ_ACADEMY_ID = "osjj-roadmap"
OSJJ_ACADEMY_NAME = "Open Source Jiu-Jitsu & Roadmap"

_OSJJ_ROADMAP_MODULES: list[dict[str, Any]] = [
    {
        "slug": "roadmap-positional-hierarchy",
        "title": "BJJ Roadmap: The 6 Core Positions and Positional Hierarchy",
        "position": "all",
        "belt": "white",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "oeXg51hrgqE",
        "timestamp_sec": 15,
        "tags": ["roadmap", "hierarchy", "fundamentals", "strategy", "osjj"],
        "cue": "Advance position before submission: Escapes -> Guard -> Pass -> Side Control -> Mount -> Back.",
        "text": (
            "Stephan Kesting's BJJ Roadmap organizes grappling into six interconnected major positions: "
            "Closed Guard, Open Guard, Half Guard, Side Control, Mount, and Rear Mount (Back Control). "
            "Top players advance from guard passing to pins and submissions; bottom players protect frames, "
            "recover guard, and sweep to top before hunting finishes."
        ),
    },
    {
        "slug": "osjj-closed-guard-posture-break",
        "title": "OSJJ Closed Guard: Breaking Posture with Collar Tie and Hip Pull",
        "position": "closed_guard",
        "belt": "white",
        "instructor": "Jon Thomas (JonThomasBJJ)",
        "video_id": "ZKsfnBbBdjk",
        "timestamp_sec": 35,
        "tags": ["closed_guard", "posture_break", "collar_grip", "osjj", "roadmap"],
        "cue": "Pull knees toward your chest while stripping sleeve grips before climbing a high collar grip.",
        "text": (
            "From Closed Guard bottom, use both legs together to bring the top player's hips forward while "
            "breaking their biceps or lapel grips. Once their posture collapses below 45 degrees, anchor "
            "one hand deep in the collar or behind the neck to set up sweeps and submissions."
        ),
    },
    {
        "slug": "roadmap-closed-guard-scissor-sweep",
        "title": "BJJ Roadmap Closed Guard: Classic Scissor Sweep and Hip-Out Angle",
        "position": "closed_guard",
        "belt": "white",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "nLonQ45zYy8",
        "timestamp_sec": 20,
        "tags": ["closed_guard", "scissor_sweep", "sweep", "roadmap"],
        "cue": "Escape your hips out to the side, lay your top shin across their belt line, and pull them onto your shin before scissoring.",
        "text": (
            "Secure a cross-collar grip and same-side sleeve grip in Closed Guard. Shrimp onto your side, "
            "place the top shin diagonally across the opponent's torso and the bottom leg flat on the mat "
            "blocking their knee, load their weight forward, and scissor cleanly into full Mount."
        ),
    },
    {
        "slug": "roadmap-closed-guard-hip-bump-kimura",
        "title": "BJJ Roadmap Closed Guard: Hip Bump Sweep to Kimura and Guillotine Chain",
        "position": "closed_guard",
        "belt": "white",
        "instructor": "Stephan Kesting & Jon Thomas (GrappleArts)",
        "video_id": "86eSdQYSjxA",
        "timestamp_sec": 30,
        "tags": ["closed_guard", "hip_bump", "kimura", "guillotine", "roadmap"],
        "cue": "When your opponent postures upright, open guard, post on your far hand, drive hips high into their chest, and trap their posting arm.",
        "text": (
            "Against an upright top player in Closed Guard, sit up onto your elbow and then palm, drive "
            "your hips explosively across their centerline to block their post and sweep to Mount. If they "
            "post a hand wide on the mat, immediately switch to the Kimura figure-four or Guillotine choke."
        ),
    },
    {
        "slug": "osjj-closed-guard-cross-collar-choke",
        "title": "OSJJ Closed Guard: Deep Cross-Collar Choke Mechanics",
        "position": "closed_guard",
        "belt": "white",
        "instructor": "Lachlan Giles (Absolute MMA)",
        "video_id": "jPFymrhqj0Y",
        "timestamp_sec": 25,
        "tags": ["closed_guard", "cross_choke", "submission", "osjj"],
        "cue": "Feed the first hand thumb-deep behind the neck, keep wrists straight, and flare elbows back toward your ribs instead of pushing.",
        "text": (
            "Break posture first so the first palm-up grip reaches the back tag of the collar. Feed the "
            "second grip either palm-up underneath or palm-down over the shoulder, pull the opponent's "
            "chest down with your knees, and squeeze wrists straight across both carotid arteries."
        ),
    },
    {
        "slug": "roadmap-closed-guard-armbar-triangle",
        "title": "BJJ Roadmap Closed Guard: Armbar and Triangle Choke Dilemma",
        "position": "closed_guard",
        "belt": "white",
        "instructor": "Lachlan Giles (Absolute MMA)",
        "video_id": "ns_YcYn6wRc",
        "timestamp_sec": 40,
        "tags": ["closed_guard", "armbar", "triangle", "submission", "roadmap"],
        "cue": "Control one arm across centerline; if they pull the trapped arm out, shoot hips high for the Triangle and cut a 90-degree angle.",
        "text": (
            "Isolate one arm across your torso with a high guard climb. Pivot perpendicular to throw the "
            "leg over the face for the straight armbar; if they stack or yank the arm free while leaving "
            "one arm in and one arm out, lock the Triangle behind the knee and angle off toward the trapped shoulder."
        ),
    },
    {
        "slug": "osjj-half-guard-underhook-knee-shield",
        "title": "OSJJ Half Guard: Knee Shield Frame to Underhook Dogfight Sweep",
        "position": "half_guard",
        "belt": "white",
        "instructor": "Lachlan Giles (Absolute MMA)",
        "video_id": "pW2YL_n8Q_U",
        "timestamp_sec": 30,
        "tags": ["half_guard", "knee_shield", "underhook", "dogfight", "osjj"],
        "cue": "Stay on your side with a diagonal knee shield, block the crossface first, then pummel a deep hip underhook to come up into dogfight.",
        "text": (
            "Never lie flat on your back in Half Guard. Maintain a diagonal top knee shield and top hand "
            "collar frame to deny the crossface. Shoot the bottom arm underneath their far armpit to grab "
            "their far hip or thigh, hide your head low on their chest, and build base to sweep or take the back."
        ),
    },
    {
        "slug": "roadmap-half-guard-old-school-sweep",
        "title": "BJJ Roadmap Half Guard: Old School Sweep and Plan-B Roll-Through",
        "position": "half_guard",
        "belt": "blue",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "l12Rm-4yvOA",
        "timestamp_sec": 20,
        "tags": ["half_guard", "old_school_sweep", "sweep", "roadmap"],
        "cue": "Pass the trapped foot to your outside hand, block their posting foot, and drive forward into their far hip.",
        "text": (
            "Once you win the underhook in Half Guard, reach underneath their trapped knee to capture the "
            "toes of their trapped foot. Drive forward across their base; if they whizzer hard and lean "
            "back into you, tuck your head and roll underneath into the Plan-B sweep."
        ),
    },
    {
        "slug": "roadmap-side-control-elbow-knee-escape",
        "title": "BJJ Roadmap Side Control Escape: Neck-Hip Frames and Shrimp to Guard",
        "position": "side_control",
        "belt": "white",
        "instructor": "Lachlan Giles (Absolute MMA)",
        "video_id": "by85KB6wf8A",
        "timestamp_sec": 35,
        "tags": ["side_control", "escape", "shrimp", "frames", "roadmap"],
        "cue": "Build two rigid frames on their neck and near hip, bridge to create space, shrimp your hips away, and insert the bottom knee.",
        "text": (
            "Keep both elbows glued to your ribs under Side Control so the top player cannot isolate an arm. "
            "Wedge your top forearm across their throat and bottom forearm into their hip crease, bridge "
            "to lighten their chest pressure, hip escape outward, and thread your bottom knee through to recover guard."
        ),
    },
    {
        "slug": "osjj-side-control-underhook-escape",
        "title": "OSJJ Side Control Escape: Far-Side Underhook to Knees or Single Leg",
        "position": "side_control",
        "belt": "white",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "9uHXRvD8zmM",
        "timestamp_sec": 25,
        "tags": ["side_control", "underhook", "escape", "osjj"],
        "cue": "When they block your knee recovery by driving toward your hips, swim your top arm inside for a deep underhook and turn onto your knees.",
        "text": (
            "If the top player switches hips or crowds your lower body to stop the classic shrimp, bridge "
            "toward them to open space beneath their far armpit. Pummel your top arm through for a tight "
            "underhook, tuck your chin, and turn to your knees into a low single-leg finish or turtle stand-up."
        ),
    },
    {
        "slug": "roadmap-side-control-top-pin-submissions",
        "title": "BJJ Roadmap Side Control Top: Crossface Pin to Americana and Kimura",
        "position": "side_control",
        "belt": "white",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "DydAyqTlBWU",
        "timestamp_sec": 15,
        "tags": ["side_control", "americana", "kimura", "top_pressure", "roadmap"],
        "cue": "Turn their chin away with shoulder pressure before attacking the far elbow with an Americana or stepping over for a Kimura.",
        "text": (
            "Stabilize Side Control for the full 3 seconds with a deep crossface and far hip block. "
            "When the bottom player pushes on your chest, pin their wrist to the mat with a monkey grip "
            "for the Americana keylock, or transition to North-South Kimura if they straighten the arm."
        ),
    },
    {
        "slug": "roadmap-mount-trap-and-roll-upa",
        "title": "BJJ Roadmap Mount Escape: Trap and Roll (Upa) Bridge Escape",
        "position": "mount",
        "belt": "white",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "HFkTqCqd6wg",
        "timestamp_sec": 45,
        "tags": ["mount", "upa", "trap_and_roll", "escape", "roadmap"],
        "cue": "Trap one arm and the same-side ankle tightly, then bridge explosively over your shoulder rather than straight up.",
        "text": (
            "When mounted, keep elbows tight and wait for the top player to post a hand or reach for your "
            "collar. Clamp that arm to your chest, hook the same-side foot with your heel so they cannot "
            "base out, and bridge diagonally over your shoulder to land inside their Closed Guard."
        ),
    },
    {
        "slug": "osjj-mount-elbow-knee-escape",
        "title": "OSJJ Mount Escape: Elbow-Knee Escape to Half Guard and Full Guard",
        "position": "mount",
        "belt": "white",
        "instructor": "Lachlan Giles (Absolute MMA)",
        "video_id": "45kbVdNfIbI",
        "timestamp_sec": 35,
        "tags": ["mount", "elbow_knee", "escape", "half_guard", "osjj"],
        "cue": "Flatten one leg to the mat, frame on their hip and knee, shrimp onto your side, and scoop their ankle into Half Guard.",
        "text": (
            "Pair the Elbow-Knee escape with the Trap-and-Roll. Turn onto your side while keeping your "
            "bottom leg flat on the mat, wedge your elbow inside their knee, bridge slightly, and slide "
            "your bottom knee underneath their leg to capture Half Guard and then recover Closed Guard."
        ),
    },
    {
        "slug": "roadmap-mount-top-high-mount-attacks",
        "title": "BJJ Roadmap Mount Top: Climbing to High Mount for Cross Choke and Armbar",
        "position": "mount",
        "belt": "white",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "B_dOCcWymv4",
        "timestamp_sec": 30,
        "tags": ["mount", "high_mount", "armbar", "cross_choke", "roadmap"],
        "cue": "Walk your knees into their armpits as soon as their elbows lift off the mat to shut down bridges and open the S-mount armbar.",
        "text": (
            "After securing 4 points in Mount, grapevine or widen your base to absorb the initial bridge. "
            "Threaten the cross-collar choke to force their hands upward, walk your knees high under their "
            "armpits, transition to S-mount, and finish the mounted armbar with tight knees."
        ),
    },
    {
        "slug": "roadmap-back-control-seatbelt-rnc",
        "title": "BJJ Roadmap Back Control: Seatbelt Grip, Over-Under Side, and Rear Naked Choke",
        "position": "back_control",
        "belt": "white",
        "instructor": "Lachlan Giles (Absolute MMA)",
        "video_id": "0Q4pp5eF9D0",
        "timestamp_sec": 30,
        "tags": ["back_control", "seatbelt", "rnc", "submission", "roadmap"],
        "cue": "Keep chest glued to their upper back, fall to the underhook side to trap their bottom arm, and align your elbow directly under their chin.",
        "text": (
            "Lock both hooks inside the thighs without crossing your ankles to earn 4 points. Maintain a "
            "tight seatbelt grip with the choking arm over the shoulder, fight two-on-one to clear their "
            "defensive hands, and finish the Rear Naked Choke or bow-and-arrow collar choke."
        ),
    },
    {
        "slug": "osjj-back-escape-shoulder-to-mat",
        "title": "OSJJ Back Escape: Two-on-One Hand Fight and Back-of-Head to the Mat",
        "position": "back_control",
        "belt": "white",
        "instructor": "Lachlan Giles (Absolute MMA)",
        "video_id": "k-lCzVAzJpg",
        "timestamp_sec": 25,
        "tags": ["back_control", "escape", "hand_fighting", "osjj"],
        "cue": "Control the choking wrist with both hands first, peel the bottom hook, and slide your shoulders flat to the mat on the underhook side.",
        "text": (
            "Survival comes before escaping hooks. Immediately tuck your chin and grip the choking wrist "
            "two-on-one. Shift toward the non-choking (underhook) side, clear the bottom leg hook, drive "
            "the back of your head and shoulders to the mat, and turn into their top Half Guard."
        ),
    },
    {
        "slug": "osjj-turtle-defense-granby-and-sitout",
        "title": "OSJJ Turtle Bottom: Protecting Collar Space, Sit-Out, and Granby Guard Recovery",
        "position": "turtle",
        "belt": "white",
        "instructor": "Lachlan Giles (Absolute MMA)",
        "video_id": "61unSJ7vu4I",
        "timestamp_sec": 30,
        "tags": ["turtle", "granby", "sit_out", "escape", "osjj"],
        "cue": "Seal your elbows to your knees so hooks cannot enter, control their near wrist, and either sit out or roll through to guard.",
        "text": (
            "Never stay static in bottom Turtle where opponents can insert hooks for 4 points or lock a "
            "clock choke. Keep elbows glued to thighs, defend the collar grip with two hands, and use a "
            "far-shoulder Granby roll or sit-out turn-in to recover Closed or Half Guard."
        ),
    },
    {
        "slug": "roadmap-turtle-top-seatbelt-chair-sit",
        "title": "BJJ Roadmap Turtle Top: Seatbelt Entry and Chair-Sit Back Take",
        "position": "turtle",
        "belt": "blue",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "_ZIC3w_Dg3Y",
        "timestamp_sec": 20,
        "tags": ["turtle", "back_take", "chair_sit", "roadmap"],
        "cue": "Insert your near knee behind their hip, lock the seatbelt grip, sit to your hip behind them, and pull them across your lap for both hooks.",
        "text": (
            "Attacking Turtle begins with chest-to-back weight and a deep seatbelt harness. Instead of "
            "forcing hooks from straight behind, wedge your bottom knee into their hip pocket, sit to your "
            "side (chair sit), pull them onto your bottom hook, and insert the top hook cleanly."
        ),
    },
    {
        "slug": "roadmap-butterfly-guard-hook-sweep",
        "title": "BJJ Roadmap Butterfly Guard: Underhook Hook Sweep and Shoulder Crunch",
        "position": "butterfly_guard",
        "belt": "white",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "9cDA8S6Om_g",
        "timestamp_sec": 15,
        "tags": ["butterfly_guard", "hook_sweep", "sweep", "roadmap"],
        "cue": "Sit forward with head lower than theirs, clamp their posting arm, fall onto your ear rather than your back, and elevate with the top hook.",
        "text": (
            "Keep active seated posture in Butterfly Guard with both insteps hooked inside the thighs. "
            "Win an underhook or overhook to kill their posting arm on one side, drop your shoulder toward "
            "the mat at a 45-degree angle, and lift with your top butterfly hook directly into Mount or Side Control."
        ),
    },
    {
        "slug": "osjj-open-guard-four-points-of-contact",
        "title": "OSJJ Open Guard: Three-to-Four Points of Contact and Angle Retention",
        "position": "open_guard",
        "belt": "white",
        "instructor": "Jon Thomas (JonThomasBJJ)",
        "video_id": "ToKDHylpgDY",
        "timestamp_sec": 40,
        "tags": ["open_guard", "retention", "collar_sleeve", "dlr", "osjj"],
        "cue": "Keep at least three points of contact (grips and feet on hips/biceps) and square your hips continuously whenever they circle.",
        "text": (
            "Open Guard succeeds through constant tension between hands and feet. Maintain collar-sleeve "
            "or De La Riva frames so the passer cannot control both pant cuffs simultaneously, and use "
            "hip movement plus shin-to-shin frames to off-balance them before they clear your knee line."
        ),
    },
    {
        "slug": "roadmap-standing-pass-toreando-knee-cut",
        "title": "BJJ Roadmap Guard Passing: Toreando Bullfighter and Knee Cut Pass",
        "position": "standing_pass",
        "belt": "white",
        "instructor": "Jon Thomas (JonThomasBJJ)",
        "video_id": "lOPh9K5kOcE",
        "timestamp_sec": 45,
        "tags": ["standing_pass", "toreando", "knee_cut", "guard_pass", "roadmap"],
        "cue": "Pin their knees to the mat before stepping around on Toreando, or win the far underhook before sliding your shin across for Knee Cut.",
        "text": (
            "Effective guard passing alternates outside speed passes (Toreando) with inside pressure passes "
            "(Knee Cut and Over-Under). Control the feet and hips first, clear the knee line past their "
            "frames, and settle chest-to-chest for 3 full seconds to score 3 points."
        ),
    },
    {
        "slug": "osjj-standing-pass-over-under-pressure",
        "title": "OSJJ Guard Passing: Over-Under Pressure Pass and Hip Switch",
        "position": "standing_pass",
        "belt": "blue",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "YCzIGSS1Hkc",
        "timestamp_sec": 25,
        "tags": ["standing_pass", "over_under", "pressure_pass", "osjj"],
        "cue": "Pin the trapped leg to your chest with head pressure in their far hip, tripod to clear the foot, and switch hips into Side Control.",
        "text": (
            "The Over-Under pass neutralizes flexible open guards by immobilizing one leg on the mat while "
            "wedging your shoulder into the opponent's lower abdomen. Keep your head glued to the far hip "
            "to prevent triangles, step over the bottom shin, and establish heavy Side Control."
        ),
    },
    {
        "slug": "roadmap-takedowns-collar-drag-ankle-pick",
        "title": "BJJ Roadmap Takedowns: Low-Risk Collar Drag and Cross-Collar Ankle Pick",
        "position": "takedowns",
        "belt": "white",
        "instructor": "Stephan Kesting (GrappleArts)",
        "video_id": "ltTzGSGqgLg",
        "timestamp_sec": 20,
        "tags": ["takedowns", "collar_drag", "ankle_pick", "standing", "roadmap"],
        "cue": "Win inside collar control, circle to make them step heavy onto the lead foot, and snap their head down as you scoop the ankle.",
        "text": (
            "For Jiu-Jitsu competition, low-risk Gi takedowns minimize guillotine exposure. Snap the "
            "opponent's posture down with a deep collar grip; as their weight loads onto the forward heel, "
            "drop your level cleanly to block the ankle or whip them past your hip with a Collar Drag for 2 points."
        ),
    },
    {
        "slug": "osjj-takedowns-single-leg-and-sprawl",
        "title": "OSJJ Takedowns: Head-Inside Single Leg Finish and Fundamental Sprawl Defense",
        "position": "takedowns",
        "belt": "white",
        "instructor": "Lachlan Giles (Absolute MMA)",
        "video_id": "6lXwED3LW-w",
        "timestamp_sec": 25,
        "tags": ["takedowns", "single_leg", "sprawl", "standing", "osjj"],
        "cue": "Keep your ear glued to their chest and posture tall on the Single Leg; on defense, shoot hips heavy to the mat and crossface.",
        "text": (
            "Shoot with a proper level change rather than bending at the waist, keeping your head tightly "
            "pressed inside their torso so they cannot lock a Guillotine. Finish by running the pipe or "
            "sweeping the far ankle, and always defend opponent shots by sprawling hips heavy onto their shoulders."
        ),
    },
]


def _youtube_timestamp_url(video_id: str, timestamp_sec: int) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&t={timestamp_sec}s"


def build_osjj_roadmap_corpus() -> tuple[list[KnowledgeDocument], dict[str, Any]]:
    """Builds the video-backed Open Source Jiu-Jitsu & BJJ Roadmap public academy corpus."""
    docs: list[KnowledgeDocument] = []
    for item in _OSJJ_ROADMAP_MODULES:
        ts = int(item["timestamp_sec"])
        vid = str(item["video_id"])
        docs.append(
            KnowledgeDocument(
                doc_id=f"osjj-{item['slug']}",
                corpus="cta",
                academy_id=OSJJ_ACADEMY_ID,
                title=item["title"],
                section_or_belt=item["belt"],
                position=item["position"],
                text=item["text"],
                source_url=_youtube_timestamp_url(vid, ts),
                timestamp_sec=ts,
                tags=list(item["tags"]),
                metadata={
                    "academy_id": OSJJ_ACADEMY_ID,
                    "academy_name": OSJJ_ACADEMY_NAME,
                    "instructor": item["instructor"],
                    "video_id": vid,
                    "min_belt": item["belt"],
                    "coaching_cue": item["cue"],
                    "source_kind": "public_academy_curriculum",
                    "license_note": (
                        "MIT-licensed RollMetrics coaching cues paired with public YouTube instructional "
                        "timestamp jump-links (Stephan Kesting / GrappleArts, Lachlan Giles / Absolute MMA, Jon Thomas)."
                    ),
                },
            )
        )
    meta = {
        "academy_id": OSJJ_ACADEMY_ID,
        "academy_name": OSJJ_ACADEMY_NAME,
        "total_osjj_roadmap_docs": len(docs),
        "video_backed_docs": sum(1 for d in docs if d.timestamp_sec is not None),
    }
    return docs, meta
