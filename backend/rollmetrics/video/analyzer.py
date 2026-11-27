"""Pre-recorded BJJ match analyzer combining vision, IBJJF state machine, and CTA knowledge."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

from rollmetrics.config import Settings, get_settings
from rollmetrics.providers.factory import get_vision_provider
from rollmetrics.rules.base import get_ruleset_engine
from rollmetrics.rules.schema import (
    AdjudicatedEvent,
    AthleteId,
    EventKind,
    MatchDivisionConfig,
    RawObservedEvent,
    ScoreboardSnapshot,
)
from rollmetrics.video.ingest import ingest_video_source

FALLBACK_CTA_BY_POSITION: dict[str, list[dict[str, Any]]] = {
    "closed guard": [
        {
            "title": "CTA Closed Guard Overhook & Shoulder Clamp System",
            "position": "Closed Guard",
            "text": (
                "Break posture with knees pulling to chest first, wrap deep cross-collar or overhook "
                "clamp, cut 45-degree hip angle to isolate the trapped shoulder for choke, triangle, "
                "or pendulum-to-armbar transition."
            ),
            "source_url": "https://www.youtube.com/watch?v=xJ2f9kP3z9A&t=120s",
        },
        {
            "title": "CTA Closed Guard Posture Defense & Safe Opening",
            "position": "Closed Guard",
            "text": (
                "Frame hands on biceps and hip bones with spine aligned, stand square to stagger-step "
                "knees between tailbone, and open at the ankles before initiating knee-cut or torreando."
            ),
            "source_url": "https://www.youtube.com/watch?v=xJ2f9kP3z9A&t=420s",
        },
    ],
    "half guard": [
        {
            "title": "CTA Knee-Shield Half Guard Frame & Underhook Recovery",
            "position": "Half Guard",
            "text": (
                "Stay on your side off flat shoulders, frame cross-shoulder with top forearm, "
                "shoot deep far-side underhook to waist, and drive knee-tap or dogfight sweep."
            ),
            "source_url": "https://www.youtube.com/watch?v=yL8w4mQ2n1B&t=180s",
        }
    ],
    "side control": [
        {
            "title": "CTA Side Control Hip-Knee Frame Escape",
            "position": "Side Control",
            "text": (
                "Establish forearm frames at near hip and cross neck-line, bridge slightly to create "
                "wedge space, shrimp hips back, and thread bottom knee across hip line to closed guard."
            ),
            "source_url": "https://www.youtube.com/watch?v=zK9r3vT5m8C&t=95s",
        }
    ],
    "mount": [
        {
            "title": "CTA High Mount Arm Isolation & Cross Choke / Armbar",
            "position": "Mount",
            "text": (
                "Walk knees up into armpits as opponent frames, pinch knees tight, hug elbow across "
                "centerline, and keep heels heavy downward on S-mount armbar."
            ),
            "source_url": "https://www.youtube.com/watch?v=mP4d7sN6k2D&t=240s",
        }
    ],
    "back control": [
        {
            "title": "CTA Seatbelt Back Control & Weak-Side Choke Mechanics",
            "position": "Back Control",
            "text": (
                "Glue chest between shoulder blades with ear-to-ear head position, trap choking-side "
                "wrist before falling to underhook side, and finish sliding collar or RNC."
            ),
            "source_url": "https://www.youtube.com/watch?v=bQ7t2xR9v4E&t=310s",
        }
    ],
    "standing": [
        {
            "title": "CTA Grip Fighting & Structured Guard Pull Entry",
            "position": "Standing",
            "text": (
                "Win inside sleeve and collar grip before sitting; place same-side foot on hip with "
                "live tension to off-balance immediately into closed guard or collar-sleeve."
            ),
            "source_url": "https://www.youtube.com/watch?v=sW3n8kL1p5F&t=60s",
        }
    ],
}


RECOVERABLE_KB_ERRORS = (
    ImportError,
    OSError,
    RuntimeError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    sqlite3.Error,
)


def load_optional_retriever(settings: Settings) -> Any | None:
    """Lazy-loads KnowledgeRetriever from rollmetrics.knowledge.retriever when available."""
    try:
        from rollmetrics.knowledge.retriever import KnowledgeRetriever

        return KnowledgeRetriever(
            knowledge_dir=settings.knowledge_dir,
            version=settings.active_index_version,
            settings=settings,
        )
    except RECOVERABLE_KB_ERRORS:
        return None


def lookup_cta_recommendations(
    retriever: Any | None,
    position: str,
    situation: str,
    belt: str,
    top_k: int = 2,
) -> list[dict[str, Any]]:
    if retriever is not None:
        with contextlib.suppress(*RECOVERABLE_KB_ERRORS):
            recs = retriever.recommend_cta_for_position(
                position=position,
                situation=situation,
                belt=belt,
                top_k=top_k,
            )
            if recs:
                return list(recs)

    pos_key = (position or "standing").strip().lower()
    for candidate_key, items in FALLBACK_CTA_BY_POSITION.items():
        if candidate_key in pos_key or pos_key in candidate_key:
            return [dict(item) for item in items[:top_k]]
    return [dict(FALLBACK_CTA_BY_POSITION["standing"][0])]


def lookup_ibjjf_citations(
    retriever: Any | None,
    query: str,
    fallback_article: str,
    fallback_reason: str,
    top_k: int = 2,
) -> list[dict[str, Any]]:
    if retriever is not None:
        with contextlib.suppress(*RECOVERABLE_KB_ERRORS):
            cites = retriever.cite_ibjjf_rule(query, top_k=top_k)
            if cites:
                return list(cites)

    return [
        {
            "title": fallback_article or "IBJJF Official Rules",
            "section_or_belt": fallback_article,
            "text": fallback_reason,
            "source_url": "https://ibjjf.com/books-videos",
        }
    ]


class MatchAnalyzer:
    """Runs pre-recorded video and structured event timelines through IBJJF & CTA pipelines."""

    def __init__(self, settings: Settings | None = None, retriever: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._explicit_retriever = retriever
        self._lazy_retriever_loaded = False
        self._lazy_retriever: Any | None = None

    @property
    def retriever(self) -> Any | None:
        if self._explicit_retriever is not None:
            return self._explicit_retriever
        if not self._lazy_retriever_loaded:
            self._lazy_retriever = load_optional_retriever(self.settings)
            self._lazy_retriever_loaded = True
        return self._lazy_retriever

    def _build_coaching_summary(
        self,
        snap: ScoreboardSnapshot,
        locked: AthleteId,
        extra_vision_notes: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        locked_name = (
            snap.division.athlete_a_name
            if locked == AthleteId.ATHLETE_A
            else snap.division.athlete_b_name
        )
        my_score = snap.athlete_a if locked == AthleteId.ATHLETE_A else snap.athlete_b
        opp_score = snap.athlete_b if locked == AthleteId.ATHLETE_A else snap.athlete_a

        summary_cards: list[dict[str, Any]] = [
            {
                "category": "Match Outcome & Scoreline",
                "athlete": locked_name,
                "position": snap.current_position,
                "headline": (
                    f"{locked_name} ({locked.value}): {my_score.points} pts, "
                    f"{my_score.advantages} adv, {my_score.penalties} pen vs "
                    f"{opp_score.points} pts, {opp_score.advantages} adv, {opp_score.penalties} pen "
                    f"[{snap.win_method or snap.winner}]"
                ),
                "cta_detail": (
                    "Prioritize 3.0s chest-to-chest or hook stabilization in-bounds on every transition "
                    "so advantages convert into full IBJJF board points."
                ),
                "cta_links": [],
            }
        ]

        seen_positions: list[str] = []
        for adj in snap.timeline:
            # Extract tactical lessons from penalties, unstabilized advantages, and key exchanges
            is_mine = adj.actor == locked
            if adj.awarded_penalties > 0 and is_mine:
                recs = lookup_cta_recommendations(
                    self.retriever,
                    position=snap.current_position,
                    situation=f"avoid penalty {adj.ruling_reason}",
                    belt=snap.division.belt.value,
                    top_k=2,
                )
                summary_cards.append(
                    {
                        "category": "Rule Discipline Fix",
                        "timestamp_sec": adj.timestamp_sec,
                        "match_clock_str": adj.match_clock_str,
                        "position": snap.current_position,
                        "headline": f"Eliminate penalty at {adj.match_clock_str}: {adj.rule_article}",
                        "cta_detail": (
                            "Establish clean sleeve/collar grips before sitting and maintain active "
                            "hip-angle attacks every 10-15s to stay clear of IBJJF Art. 6.3 penalties."
                        ),
                        "cta_links": recs,
                    }
                )
            elif adj.awarded_advantages > 0 and adj.raw_kind in (
                EventKind.TAKEDOWN,
                EventKind.SWEEP,
                EventKind.GUARD_PASS,
                EventKind.MOUNT,
                EventKind.BACK_CONTROL,
                EventKind.KNEE_ON_BELLY,
            ) and is_mine:
                recs = lookup_cta_recommendations(
                    self.retriever,
                    position=adj.raw_kind.value.replace("_", " "),
                    situation="stabilize 3 seconds heavy pressure",
                    belt=snap.division.belt.value,
                    top_k=2,
                )
                summary_cards.append(
                    {
                        "category": "3-Second Stabilization Conversion",
                        "timestamp_sec": adj.timestamp_sec,
                        "match_clock_str": adj.match_clock_str,
                        "position": adj.raw_kind.value.replace("_", " ").title(),
                        "headline": (
                            f"Convert {adj.raw_kind.value} advantage at {adj.match_clock_str} into full points"
                        ),
                        "cta_detail": adj.ruling_reason,
                        "cta_links": recs,
                    }
                )
            elif not is_mine and (adj.awarded_points > 0 or adj.raw_kind == EventKind.SUBMISSION):
                pos_label = adj.raw_kind.value.replace("_", " ").title()
                if pos_label not in seen_positions:
                    seen_positions.append(pos_label)
                    recs = lookup_cta_recommendations(
                        self.retriever,
                        position=pos_label,
                        situation=f"early frame defense escape {adj.raw_kind.value}",
                        belt=snap.division.belt.value,
                        top_k=2,
                    )
                    summary_cards.append(
                        {
                            "category": "Defensive Frame & Retention Priority",
                            "timestamp_sec": adj.timestamp_sec,
                            "match_clock_str": adj.match_clock_str,
                            "position": pos_label,
                            "headline": f"Deny opponent {adj.raw_kind.value} at {adj.match_clock_str}",
                            "cta_detail": (
                                "Build knee-elbow connection and inside bicep/collar frames before "
                                "opponent settles chest weight for the 3-second count."
                            ),
                            "cta_links": recs,
                        }
                    )

        if len(summary_cards) == 1:
            base_pos = snap.current_position or "Closed Guard"
            recs = lookup_cta_recommendations(
                self.retriever,
                position=base_pos,
                situation="chain attack finishing mechanics",
                belt=snap.division.belt.value,
                top_k=3,
            )
            summary_cards.append(
                {
                    "category": "CTA Curriculum Study Prescription",
                    "position": base_pos,
                    "headline": f"Sharpen {base_pos} offensive chains for {locked_name}",
                    "cta_detail": (
                        "Drill angle creation, posture control, and secondary submission transitions "
                        "from your primary scoring position."
                    ),
                    "cta_links": recs,
                }
            )

        for v_note in extra_vision_notes or []:
            if isinstance(v_note, dict) and v_note.get("headline"):
                pos = str(v_note.get("position", snap.current_position))
                recs = lookup_cta_recommendations(
                    self.retriever,
                    position=pos,
                    situation=str(v_note.get("headline", "")),
                    belt=snap.division.belt.value,
                    top_k=2,
                )
                summary_cards.append(
                    {
                        "category": "Video Technical Note",
                        "timestamp_sec": v_note.get("timestamp_sec", 0.0),
                        "position": pos,
                        "headline": str(v_note["headline"]),
                        "cta_detail": str(v_note.get("cta_detail", "")),
                        "cta_links": recs,
                    }
                )

        for card in summary_cards:
            card.setdefault("title", card.get("headline", "CTA Coaching Note"))
            card.setdefault("text", card.get("cta_detail", ""))
            links = card.get("cta_links") or []
            if links and isinstance(links[0], dict) and links[0].get("source_url"):
                card.setdefault("source_url", links[0]["source_url"])

        return summary_cards

    def analyze_event_stream(
        self,
        events: list[RawObservedEvent] | list[dict[str, Any]],
        division: MatchDivisionConfig | dict[str, Any],
        source_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Deterministically scores an event stream and attaches IBJJF citations + CTA coaching."""
        div = (
            MatchDivisionConfig.model_validate(division)
            if isinstance(division, dict)
            else division
        )
        parsed_events = [
            RawObservedEvent.model_validate(e) if isinstance(e, dict) else e
            for e in events
        ]
        ordered_events = sorted(parsed_events, key=lambda item: item.timestamp_sec)

        engine = get_ruleset_engine(
            div.ruleset,
            division=div,
            index_version=self.settings.active_index_version,
        )
        locked = div.locked_athlete or AthleteId.ATHLETE_A
        rule_citations_by_event: dict[str, list[dict[str, Any]]] = {}

        enriched_timeline: list[AdjudicatedEvent] = []
        for ev in ordered_events:
            adj = engine.ingest_event(ev)
            pos_context = ev.position_after or engine.current_position or "Standing"
            role_hint = (
                f"offense finish {ev.kind.value} {ev.technique_slug}".strip()
                if ev.actor == locked
                else f"defense counter escape {ev.kind.value}".strip()
            )
            cta_recs = lookup_cta_recommendations(
                self.retriever,
                position=pos_context,
                situation=role_hint,
                belt=div.belt.value,
                top_k=2,
            )
            ibjjf_cites = lookup_ibjjf_citations(
                self.retriever,
                query=f"{adj.rule_article} {adj.raw_kind.value} {ev.technique_slug}".strip(),
                fallback_article=adj.rule_article,
                fallback_reason=adj.ruling_reason,
                top_k=2,
            )
            rule_citations_by_event[adj.event_id] = ibjjf_cites
            adj_enriched = adj.model_copy(update={"cta_recommendations": cta_recs})
            enriched_timeline.append(adj_enriched)

        engine.timeline = enriched_timeline
        snap = engine.finalize_regulation()
        extra_notes = (source_meta or {}).get("vision_coaching_notes")
        coaching_summary = self._build_coaching_summary(
            snap,
            locked=locked,
            extra_vision_notes=extra_notes if isinstance(extra_notes, list) else None,
        )
        snap = snap.model_copy(update={"coaching_summary": coaching_summary})

        dumped = snap.model_dump(mode="json")
        return {
            **dumped,
            "ruleset": engine.ruleset_id,
            "scoreboard": dumped,
            "rule_citations": rule_citations_by_event,
            "locked_athlete": locked.value,
            "source_meta": source_meta or {},
        }

    async def analyze_video(
        self,
        source: str | Path,
        division: MatchDivisionConfig | dict[str, Any],
        profile: str | None = None,
        sample_fps: float = 0.5,
        use_agentic_loop: bool = True,
    ) -> dict[str, Any]:
        """Ingests a local file or URL, runs the selected vision provider, and scores the match."""
        div = (
            MatchDivisionConfig.model_validate(division)
            if isinstance(division, dict)
            else division
        )
        active_profile = (profile or self.settings.profile or "local").strip().lower()
        ingest_dir = self.settings.runtime_dir / "ingest"
        meta = ingest_video_source(source, work_dir=ingest_dir, normalize=True)

        provider = get_vision_provider(profile=active_profile, settings=self.settings)
        if active_profile == "local" and hasattr(provider, "analyze_video_agentic"):
            vision_res = await provider.analyze_video(
                video_path=meta.path,
                division=div,
                sample_fps=sample_fps,
                use_agentic_loop=use_agentic_loop,
            )
        else:
            vision_res = await provider.analyze_video(
                video_path=meta.path,
                division=div,
                sample_fps=sample_fps,
            )

        source_meta: dict[str, Any] = {
            "provider_profile": active_profile,
            "model_used": vision_res.model_used,
            "model_id": vision_res.model_id,
            "adjudicator_model_used": vision_res.adjudicator_model_used,
            "latency_ms": vision_res.latency_ms,
            "input_tokens": vision_res.input_tokens,
            "output_tokens": vision_res.output_tokens,
            "telemetry": vision_res.telemetry,
            "agentic_windows_inspected": vision_res.telemetry.get("agentic_windows_inspected", []),
            "vision_summary": vision_res.summary,
            "dispute_rulings": vision_res.dispute_rulings,
            "vision_coaching_notes": vision_res.coaching_notes,
            "video_path": str(meta.path),
            "video_metadata": meta.model_dump(mode="json"),
        }
        return self.analyze_event_stream(
            events=vision_res.events,
            division=div,
            source_meta=source_meta,
        )
