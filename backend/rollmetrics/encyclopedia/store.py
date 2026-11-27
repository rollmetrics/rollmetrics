"""SQLite + FTS5 persistent catalog store for the IBJJF Match Encyclopedia."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Self

from rollmetrics.encyclopedia.schema import MatchAnnotationBundle, MatchRecord, VideoRecord, utc_now_iso

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    duration_sec REAL NOT NULL DEFAULT 0.0,
    upload_date TEXT NOT NULL DEFAULT '',
    view_count INTEGER NOT NULL DEFAULT 0,
    video_kind TEXT NOT NULL DEFAULT 'standalone_match',
    chapters_json TEXT NOT NULL DEFAULT '[]',
    harvest_source TEXT NOT NULL DEFAULT '',
    harvested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    url TEXT NOT NULL,
    start_sec REAL NOT NULL DEFAULT 0.0,
    end_sec REAL NOT NULL DEFAULT 0.0,
    duration_sec REAL NOT NULL DEFAULT 0.0,
    athlete_a TEXT NOT NULL DEFAULT 'Unknown',
    athlete_b TEXT NOT NULL DEFAULT 'Unknown',
    event_name TEXT NOT NULL DEFAULT 'IBJJF Tournament',
    event_year INTEGER,
    event_group TEXT NOT NULL DEFAULT '',
    division TEXT NOT NULL DEFAULT '',
    weight_class TEXT NOT NULL DEFAULT '',
    round_name TEXT NOT NULL DEFAULT '',
    belt TEXT NOT NULL DEFAULT 'unknown',
    uniform TEXT NOT NULL DEFAULT 'unknown',
    age_division TEXT NOT NULL DEFAULT 'unknown',
    source TEXT NOT NULL DEFAULT '',
    footage_type TEXT NOT NULL DEFAULT 'unknown',
    overlay_preset TEXT NOT NULL DEFAULT '',
    tier INTEGER NOT NULL DEFAULT 0,
    in_scoreboard_v1 INTEGER NOT NULL DEFAULT 0,
    gcs_raw_uri TEXT NOT NULL DEFAULT '',
    gcs_masked_uri TEXT NOT NULL DEFAULT '',
    gcs_annotation_uri TEXT NOT NULL DEFAULT '',
    title_raw TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_video_id ON matches(video_id);
CREATE INDEX IF NOT EXISTS idx_matches_belt ON matches(belt);
CREATE INDEX IF NOT EXISTS idx_matches_uniform ON matches(uniform);
CREATE INDEX IF NOT EXISTS idx_matches_footage ON matches(footage_type);
CREATE INDEX IF NOT EXISTS idx_matches_tier ON matches(tier);
CREATE INDEX IF NOT EXISTS idx_matches_year ON matches(event_year);
CREATE INDEX IF NOT EXISTS idx_matches_source ON matches(source);

CREATE TABLE IF NOT EXISTS annotations (
    match_id TEXT PRIMARY KEY,
    annotator_id TEXT NOT NULL,
    annotated_at TEXT NOT NULL,
    tier INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL,
    tags_text TEXT NOT NULL DEFAULT '',
    ocr_agreement_score REAL,
    FOREIGN KEY (match_id) REFERENCES matches(match_id)
);

CREATE TABLE IF NOT EXISTS gcs_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    gcs_uri TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    uploaded_at TEXT NOT NULL,
    UNIQUE(match_id, asset_type)
);

CREATE TABLE IF NOT EXISTS ocr_labels (
    match_id TEXT PRIMARY KEY,
    labeler_id TEXT NOT NULL,
    winner TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    event_count INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (match_id) REFERENCES matches(match_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS matches_fts USING fts5(
    match_id UNINDEXED,
    athlete_a,
    athlete_b,
    event_name,
    division,
    weight_class,
    belt,
    uniform,
    source,
    footage_type,
    title_raw,
    tags_text
);
"""


def _build_tags_text(bundle: MatchAnnotationBundle) -> str:
    tokens: list[str] = []
    for ev in bundle.scoring_events:
        tokens.append(ev.event_type)
        if ev.submission_type:
            tokens.append(ev.submission_type)
    for tech in bundle.technique_events:
        tokens.append(tech.category)
        tokens.append(tech.technique_name)
        if tech.guard_family:
            tokens.append(tech.guard_family)
    for edge in bundle.rule_edge_cases:
        tokens.append(edge.category)
        if edge.ibjjf_rule_ref:
            tokens.append(edge.ibjjf_rule_ref)
    if bundle.summary:
        tokens.append(bundle.summary)
    return " ".join(dict.fromkeys(t.strip() for t in tokens if t and t.strip()))


class EncyclopediaStore:
    """SQLite + FTS5 catalog for IBJJF match discovery, search, and GCS asset tracking."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def upsert_video(self, video: VideoRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO videos (
                video_id, channel, channel_id, url, title, description,
                duration_sec, upload_date, view_count, video_kind,
                chapters_json, harvest_source, harvested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                channel=excluded.channel,
                channel_id=CASE WHEN excluded.channel_id != '' THEN excluded.channel_id ELSE videos.channel_id END,
                url=excluded.url,
                title=excluded.title,
                description=CASE WHEN excluded.description != '' THEN excluded.description ELSE videos.description END,
                duration_sec=CASE WHEN excluded.duration_sec > 0 THEN excluded.duration_sec ELSE videos.duration_sec END,
                upload_date=CASE WHEN excluded.upload_date != '' THEN excluded.upload_date ELSE videos.upload_date END,
                view_count=MAX(videos.view_count, excluded.view_count),
                video_kind=excluded.video_kind,
                chapters_json=CASE WHEN excluded.chapters_json != '[]' THEN excluded.chapters_json ELSE videos.chapters_json END,
                harvest_source=excluded.harvest_source,
                harvested_at=excluded.harvested_at
            """,
            (
                video.video_id,
                video.channel,
                video.channel_id,
                video.url,
                video.title,
                video.description,
                video.duration_sec,
                video.upload_date,
                video.view_count,
                video.video_kind,
                json.dumps(video.chapters),
                video.harvest_source,
                video.harvested_at,
            ),
        )

    def _sync_fts_row(self, match: MatchRecord, tags_text: str = "") -> None:
        self._conn.execute("DELETE FROM matches_fts WHERE match_id = ?", (match.match_id,))
        self._conn.execute(
            """
            INSERT INTO matches_fts (
                match_id, athlete_a, athlete_b, event_name, division, weight_class,
                belt, uniform, source, footage_type, title_raw, tags_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match.match_id,
                match.athlete_a,
                match.athlete_b,
                match.event_name,
                match.division,
                match.weight_class,
                match.belt,
                match.uniform,
                match.source,
                match.footage_type,
                match.title_raw,
                tags_text,
            ),
        )

    def upsert_match(self, match: MatchRecord, commit: bool = True) -> None:
        self._conn.execute(
            """
            INSERT INTO matches (
                match_id, video_id, url, start_sec, end_sec, duration_sec,
                athlete_a, athlete_b, event_name, event_year, event_group,
                division, weight_class, round_name, belt, uniform, age_division,
                source, footage_type, overlay_preset, tier, in_scoreboard_v1,
                gcs_raw_uri, gcs_masked_uri, gcs_annotation_uri, title_raw, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                url=excluded.url,
                start_sec=excluded.start_sec,
                end_sec=excluded.end_sec,
                duration_sec=excluded.duration_sec,
                athlete_a=CASE WHEN excluded.athlete_a != 'Unknown' THEN excluded.athlete_a ELSE matches.athlete_a END,
                athlete_b=CASE WHEN excluded.athlete_b != 'Unknown' THEN excluded.athlete_b ELSE matches.athlete_b END,
                event_name=excluded.event_name,
                event_year=COALESCE(excluded.event_year, matches.event_year),
                event_group=CASE WHEN excluded.event_group != '' THEN excluded.event_group ELSE matches.event_group END,
                division=CASE WHEN excluded.division != '' THEN excluded.division ELSE matches.division END,
                weight_class=CASE WHEN excluded.weight_class != '' THEN excluded.weight_class ELSE matches.weight_class END,
                round_name=CASE WHEN excluded.round_name != '' THEN excluded.round_name ELSE matches.round_name END,
                belt=CASE WHEN excluded.belt != 'unknown' THEN excluded.belt ELSE matches.belt END,
                uniform=CASE WHEN excluded.uniform != 'unknown' THEN excluded.uniform ELSE matches.uniform END,
                age_division=CASE WHEN excluded.age_division != 'unknown' THEN excluded.age_division ELSE matches.age_division END,
                source=excluded.source,
                footage_type=CASE WHEN excluded.footage_type != 'unknown' THEN excluded.footage_type ELSE matches.footage_type END,
                overlay_preset=CASE WHEN excluded.overlay_preset != '' THEN excluded.overlay_preset ELSE matches.overlay_preset END,
                tier=MAX(matches.tier, excluded.tier),
                in_scoreboard_v1=MAX(matches.in_scoreboard_v1, excluded.in_scoreboard_v1),
                gcs_raw_uri=CASE WHEN excluded.gcs_raw_uri != '' THEN excluded.gcs_raw_uri ELSE matches.gcs_raw_uri END,
                gcs_masked_uri=CASE WHEN excluded.gcs_masked_uri != '' THEN excluded.gcs_masked_uri ELSE matches.gcs_masked_uri END,
                gcs_annotation_uri=CASE WHEN excluded.gcs_annotation_uri != '' THEN excluded.gcs_annotation_uri ELSE matches.gcs_annotation_uri END,
                title_raw=excluded.title_raw,
                notes=CASE WHEN excluded.notes != '' THEN excluded.notes ELSE matches.notes END
            """,
            (
                match.match_id,
                match.video_id,
                match.url,
                match.start_sec,
                match.end_sec,
                match.duration_sec,
                match.athlete_a,
                match.athlete_b,
                match.event_name,
                match.event_year,
                match.event_group,
                match.division,
                match.weight_class,
                match.round_name,
                match.belt,
                match.uniform,
                match.age_division,
                match.source,
                match.footage_type,
                match.overlay_preset,
                match.tier,
                int(match.in_scoreboard_v1),
                match.gcs_raw_uri,
                match.gcs_masked_uri,
                match.gcs_annotation_uri,
                match.title_raw,
                match.notes,
            ),
        )
        row = self._conn.execute("SELECT tags_text FROM annotations WHERE match_id = ?", (match.match_id,)).fetchone()
        tags_text = str(row["tags_text"]) if row else ""
        self._sync_fts_row(match, tags_text=tags_text)
        if commit:
            self._conn.commit()

    def upsert_annotation(self, bundle: MatchAnnotationBundle, gcs_uri: str = "") -> None:
        tags_text = _build_tags_text(bundle)
        self._conn.execute(
            """
            INSERT INTO annotations (match_id, annotator_id, annotated_at, tier, payload_json, tags_text, ocr_agreement_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                annotator_id=excluded.annotator_id,
                annotated_at=excluded.annotated_at,
                tier=MAX(annotations.tier, excluded.tier),
                payload_json=excluded.payload_json,
                tags_text=excluded.tags_text,
                ocr_agreement_score=COALESCE(excluded.ocr_agreement_score, annotations.ocr_agreement_score)
            """,
            (
                bundle.match_id,
                bundle.annotator_id,
                bundle.annotated_at,
                bundle.tier,
                bundle.model_dump_json(),
                tags_text,
                bundle.ocr_agreement_score,
            ),
        )
        self._conn.execute(
            """
            UPDATE matches
            SET tier = MAX(tier, ?),
                gcs_annotation_uri = CASE WHEN ? != '' THEN ? ELSE gcs_annotation_uri END
            WHERE match_id = ?
            """,
            (bundle.tier, gcs_uri, gcs_uri, bundle.match_id),
        )
        match = self.get_match(bundle.match_id)
        if match is not None:
            self._sync_fts_row(match, tags_text=tags_text)
        self._conn.commit()

    def record_gcs_asset(
        self,
        match_id: str,
        asset_type: str,
        gcs_uri: str,
        size_bytes: int = 0,
        sha256: str = "",
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO gcs_assets (match_id, asset_type, gcs_uri, size_bytes, sha256, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id, asset_type) DO UPDATE SET
                gcs_uri=excluded.gcs_uri,
                size_bytes=excluded.size_bytes,
                sha256=excluded.sha256,
                uploaded_at=excluded.uploaded_at
            """,
            (match_id, asset_type, gcs_uri, size_bytes, sha256, utc_now_iso()),
        )
        if asset_type == "raw_video":
            self._conn.execute("UPDATE matches SET gcs_raw_uri = ? WHERE match_id = ?", (gcs_uri, match_id))
        elif asset_type == "masked_video":
            self._conn.execute("UPDATE matches SET gcs_masked_uri = ? WHERE match_id = ?", (gcs_uri, match_id))
        elif asset_type == "annotation_json":
            self._conn.execute("UPDATE matches SET gcs_annotation_uri = ? WHERE match_id = ?", (gcs_uri, match_id))
        self._conn.commit()

    def upsert_ocr_label(
        self,
        match_id: str,
        *,
        labeler_id: str,
        winner: str,
        method: str,
        event_count: int,
        payload_json: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO ocr_labels (match_id, labeler_id, winner, method, event_count, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                labeler_id=excluded.labeler_id,
                winner=excluded.winner,
                method=excluded.method,
                event_count=excluded.event_count,
                payload_json=excluded.payload_json,
                created_at=excluded.created_at
            """,
            (match_id, labeler_id, winner, method, event_count, payload_json, utc_now_iso()),
        )
        self._conn.commit()

    def get_ocr_label_json(self, match_id: str) -> str | None:
        row = self._conn.execute("SELECT payload_json FROM ocr_labels WHERE match_id = ?", (match_id,)).fetchone()
        return str(row["payload_json"]) if row else None

    def get_annotation(self, match_id: str) -> MatchAnnotationBundle | None:
        row = self._conn.execute("SELECT payload_json FROM annotations WHERE match_id = ?", (match_id,)).fetchone()
        return MatchAnnotationBundle.model_validate_json(str(row["payload_json"])) if row else None

    def list_ingested_matches(
        self,
        *,
        unannotated_only: bool = False,
        limit: int = 500,
    ) -> list[MatchRecord]:
        if unannotated_only:
            sql = (
                "SELECT m.* FROM matches m "
                "LEFT JOIN annotations a ON m.match_id = a.match_id "
                "WHERE m.gcs_raw_uri != '' AND a.match_id IS NULL "
                "ORDER BY m.match_id ASC LIMIT ?"
            )
        else:
            sql = "SELECT * FROM matches WHERE gcs_raw_uri != '' ORDER BY match_id ASC LIMIT ?"
        rows = self._conn.execute(sql, (limit,)).fetchall()
        return [self._row_to_match(r) for r in rows]

    def commit(self) -> None:
        self._conn.commit()

    def _row_to_match(self, row: sqlite3.Row) -> MatchRecord:
        data = dict(row)
        data["in_scoreboard_v1"] = bool(data.get("in_scoreboard_v1", 0))
        return MatchRecord.model_validate(data)

    def get_match(self, match_id: str) -> MatchRecord | None:
        row = self._conn.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone()
        return self._row_to_match(row) if row else None

    def search(
        self,
        query: str = "",
        *,
        belt: str | None = None,
        uniform: str | None = None,
        footage_type: str | None = None,
        source: str | None = None,
        event_year: int | None = None,
        min_tier: int | None = None,
        limit: int = 25,
    ) -> list[MatchRecord]:
        clauses: list[str] = []
        params: list[Any] = []

        if query.strip():
            # Sanitize FTS5 query tokens into prefix/phrase safe terms
            terms = [tok.replace('"', "") for tok in query.strip().split() if tok.strip()]
            fts_expr = " AND ".join(f'"{t}"' for t in terms)
            sql = (
                "SELECT m.* FROM matches m "
                "JOIN matches_fts f ON m.match_id = f.match_id "
                "WHERE matches_fts MATCH ?"
            )
            params.append(fts_expr)
        else:
            sql = "SELECT m.* FROM matches m WHERE 1=1"

        if belt:
            clauses.append("m.belt = ?")
            params.append(belt)
        if uniform:
            clauses.append("m.uniform = ?")
            params.append(uniform)
        if footage_type:
            clauses.append("m.footage_type = ?")
            params.append(footage_type)
        if source:
            clauses.append("m.source = ?")
            params.append(source)
        if event_year is not None:
            clauses.append("m.event_year = ?")
            params.append(event_year)
        if min_tier is not None:
            clauses.append("m.tier >= ?")
            params.append(min_tier)

        if clauses:
            sql += " AND " + " AND ".join(clauses)
        sql += " ORDER BY m.tier DESC, m.event_year DESC, m.match_id ASC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_match(r) for r in rows]

    def select_cohort(
        self,
        *,
        limit: int = 50,
        footage_type: str | None = None,
        belt: str | None = None,
        uniform: str | None = None,
        source: str | None = None,
        exclude_ingested: bool = True,
        exclude_v1: bool = False,
        max_duration_sec: float = 1200.0,
        min_duration_sec: float = 90.0,
    ) -> list[MatchRecord]:
        clauses = ["duration_sec >= ?", "duration_sec <= ?"]
        params: list[Any] = [min_duration_sec, max_duration_sec]
        if footage_type:
            clauses.append("footage_type = ?")
            params.append(footage_type)
        if belt:
            clauses.append("belt = ?")
            params.append(belt)
        if uniform:
            clauses.append("uniform = ?")
            params.append(uniform)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if exclude_ingested:
            clauses.append("gcs_raw_uri = ''")
        if exclude_v1:
            clauses.append("in_scoreboard_v1 = 0")

        sql = (
            f"SELECT * FROM matches WHERE {' AND '.join(clauses)} "
            "ORDER BY event_year DESC, match_id ASC LIMIT ?"
        )
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_match(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        def _group(col: str, table: str = "matches") -> dict[str, int]:
            rows = self._conn.execute(
                f"SELECT COALESCE(CAST({col} AS TEXT), 'unknown') AS k, COUNT(*) AS c "
                f"FROM {table} GROUP BY k ORDER BY c DESC"
            ).fetchall()
            return {str(r["k"]): int(r["c"]) for r in rows}

        total_videos = int(self._conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0])
        total_matches = int(self._conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0])
        v1_overlap = int(self._conn.execute("SELECT COUNT(*) FROM matches WHERE in_scoreboard_v1 = 1").fetchone()[0])
        gcs_ingested = int(self._conn.execute("SELECT COUNT(*) FROM matches WHERE gcs_raw_uri != ''").fetchone()[0])
        gcs_masked = int(self._conn.execute("SELECT COUNT(*) FROM matches WHERE gcs_masked_uri != ''").fetchone()[0])
        annotated = int(self._conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0])

        return {
            "db_path": str(self.db_path),
            "total_videos": total_videos,
            "total_matches": total_matches,
            "scoreboard_v1_overlap": v1_overlap,
            "gcs_ingested_raw": gcs_ingested,
            "gcs_ingested_masked": gcs_masked,
            "annotated_matches": annotated,
            "videos_by_kind": _group("video_kind", table="videos"),
            "matches_by_source": _group("source"),
            "matches_by_belt": _group("belt"),
            "matches_by_uniform": _group("uniform"),
            "matches_by_footage_type": _group("footage_type"),
            "matches_by_tier": _group("tier"),
            "matches_by_year": _group("event_year"),
        }

    def export_matches_jsonl(self, out_path: Path) -> int:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rows = self._conn.execute("SELECT * FROM matches ORDER BY match_id ASC").fetchall()
        with out_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(self._row_to_match(row).model_dump_json() + "\n")
        return len(rows)
