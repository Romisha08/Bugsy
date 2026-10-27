from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID, uuid4

from app.domain.evidence import RequirementSource
from app.config import settings
from app.schemas.analysis import AnalysisResponse, RepairRequestPreview
from app.schemas.verification import (
    ImportStageResult, InstallationStageEnvironment, InstallationStageResult, VerificationResult,
)

DB_PATH = settings.database_path
_verification_cache: dict[str, VerificationResult] = {}
_download_approvals: dict[str, tuple[str, str]] = {}
_repair_previews: dict[str, RepairRequestPreview] = {}
_repair_candidates: dict[str, dict] = {}
_repair_preview_claims: set[tuple[str, str]] = set()
_PROCESS_INSTANCE_ID = uuid4().hex
_lock = Lock()


def _connect() -> sqlite3.Connection:
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    except (OSError, sqlite3.Error):
        raise RuntimeError(
            "Unable to open Bugsy's workflow database. Check BUGSY_DATABASE_PATH and directory write permissions."
        ) from None
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1, 2, 3, 4, 5, 6):
        connection.close()
        raise RuntimeError("Unsupported Bugsy workflow database version.")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            source_id TEXT PRIMARY KEY,
            original_bytes BLOB NOT NULL,
            source_sha256 TEXT NOT NULL,
            python_version TEXT NOT NULL,
            requirements_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS analyses (
            analysis_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            analysis_json TEXT NOT NULL,
            proposal_id TEXT,
            original_requirements_bytes BLOB,
            proposed_requirements_bytes BLOB,
            diff_bytes BLOB,
            original_sha256 TEXT,
            diff_sha256 TEXT,
            target_python TEXT NOT NULL,
            candidate_metadata_json TEXT,
            provenance_json TEXT NOT NULL,
            proposal_status TEXT
        );
        CREATE TABLE IF NOT EXISTS resolver_approvals (
            analysis_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            diff_sha256 TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('consumed')),
            consumed_at TEXT NOT NULL,
            owner_pid INTEGER,
            PRIMARY KEY (analysis_id, proposal_id)
        );
        CREATE TABLE IF NOT EXISTS verification_stage_history (
            stage_id TEXT PRIMARY KEY,
            analysis_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            diff_sha256 TEXT NOT NULL,
            stage TEXT NOT NULL CHECK (stage = 'installation'),
            status TEXT NOT NULL CHECK (status IN ('running', 'passed', 'failed', 'incomplete', 'unknown')),
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            owner_pid INTEGER NOT NULL,
            owner_instance_id TEXT NOT NULL,
            contree_operation_id TEXT,
            UNIQUE (analysis_id, proposal_id, stage)
        );
        CREATE TABLE IF NOT EXISTS verification_stage_approvals (
            stage_id TEXT PRIMARY KEY,
            analysis_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            diff_sha256 TEXT NOT NULL,
            stage TEXT NOT NULL CHECK (stage = 'installation'),
            state TEXT NOT NULL CHECK (state = 'consumed'),
            consumed_at TEXT NOT NULL,
            UNIQUE (analysis_id, proposal_id, stage)
        );
        CREATE TABLE IF NOT EXISTS verification_attempt_history (
            stage_id TEXT PRIMARY KEY,
            analysis_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            diff_sha256 TEXT NOT NULL,
            stage TEXT NOT NULL CHECK (stage IN ('installation', 'imports')),
            status TEXT NOT NULL CHECK (status IN ('running', 'passed', 'failed', 'incomplete', 'unknown')),
            result_json TEXT NOT NULL,
            request_id TEXT NOT NULL,
            sandbox_image_ref TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            owner_pid INTEGER NOT NULL,
            owner_instance_id TEXT NOT NULL,
            contree_operation_id TEXT,
            UNIQUE (analysis_id, proposal_id, stage, request_id)
        );
        CREATE TABLE IF NOT EXISTS verification_attempt_approvals (
            stage_id TEXT PRIMARY KEY,
            analysis_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            diff_sha256 TEXT NOT NULL,
            stage TEXT NOT NULL CHECK (stage IN ('installation', 'imports')),
            state TEXT NOT NULL CHECK (state = 'consumed'),
            consumed_at TEXT NOT NULL,
            FOREIGN KEY (stage_id) REFERENCES verification_attempt_history(stage_id)
        );
    """)
    if version == 1:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(resolver_approvals)")}
        if "owner_pid" not in columns:
            connection.execute("ALTER TABLE resolver_approvals ADD COLUMN owner_pid INTEGER")
    if version < 4:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(verification_stage_history)")}
        if "owner_instance_id" not in columns:
            connection.execute("ALTER TABLE verification_stage_history ADD COLUMN owner_instance_id TEXT")
        connection.execute("PRAGMA user_version = 4")
        version = 4
    if version < 5:
        # Additive migration: legacy installation history/approval rows and their
        # one-per-proposal constraint remain untouched and queryable.
        connection.execute("PRAGMA user_version = 5")
        version = 5
    if version < 6:
        # Nullable, additive fields let old stage rows remain readable without
        # changing their status or consumed approval state.
        for table in ("verification_stage_history", "verification_attempt_history"):
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if "contree_operation_id" not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN contree_operation_id TEXT")
        connection.execute("PRAGMA user_version = 6")
    return connection


@contextmanager
def _database():
    connection = _connect()
    try:
        yield connection
    finally:
        connection.close()


def _source_from_row(row: sqlite3.Row) -> RequirementSource:
    try:
        original_text = bytes(row["original_bytes"]).decode("utf-8", errors="strict")
        requirements = tuple(json.loads(row["requirements_json"]))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise RuntimeError("Stored requirements source is invalid.") from None
    return RequirementSource(
        source_id=row["source_id"], original_text=original_text,
        source_sha256=row["source_sha256"], python_version=row["python_version"],
        requirements=requirements,
    )


def _write_analysis(connection: sqlite3.Connection, analysis: AnalysisResponse) -> None:
    source_row = connection.execute(
        "SELECT original_bytes FROM sources WHERE source_id = ?", (analysis.source_id,)
    ).fetchone()
    proposal = analysis.repair_proposal
    original_bytes = bytes(source_row["original_bytes"]) if source_row else None
    proposed_bytes = proposal.proposed_requirements.encode("utf-8") if proposal and proposal.proposed_requirements is not None else None
    diff_bytes = proposal.diff.encode("utf-8") if proposal and proposal.diff is not None else None
    candidate_json = json.dumps(
        [candidate.model_dump(mode="json") for candidate in proposal.candidate_evidence],
        ensure_ascii=False, separators=(",", ":"),
    ) if proposal else None
    provenance_json = json.dumps(analysis.provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    connection.execute("""
        INSERT INTO analyses (
            analysis_id, source_id, analysis_json, proposal_id,
            original_requirements_bytes, proposed_requirements_bytes, diff_bytes,
            original_sha256, diff_sha256, target_python, candidate_metadata_json,
            provenance_json, proposal_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(analysis_id) DO UPDATE SET
            source_id=excluded.source_id, analysis_json=excluded.analysis_json,
            proposal_id=excluded.proposal_id,
            original_requirements_bytes=excluded.original_requirements_bytes,
            proposed_requirements_bytes=excluded.proposed_requirements_bytes,
            diff_bytes=excluded.diff_bytes, original_sha256=excluded.original_sha256,
            diff_sha256=excluded.diff_sha256, target_python=excluded.target_python,
            candidate_metadata_json=excluded.candidate_metadata_json,
            provenance_json=excluded.provenance_json, proposal_status=excluded.proposal_status
    """, (
        analysis.analysis_id, analysis.source_id, analysis.model_dump_json(),
        proposal.proposal_id if proposal else None, original_bytes, proposed_bytes, diff_bytes,
        proposal.original_sha256 if proposal else None, proposal.diff_sha256 if proposal else None,
        analysis.python_target, candidate_json, provenance_json,
        proposal.status if proposal else None,
    ))


def save_source(source: RequirementSource) -> None:
    raw = source.original_text.encode("utf-8")
    with _database() as connection:
        connection.execute("""
            INSERT INTO sources(source_id, original_bytes, source_sha256, python_version, requirements_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET original_bytes=excluded.original_bytes,
                source_sha256=excluded.source_sha256, python_version=excluded.python_version,
                requirements_json=excluded.requirements_json
        """, (source.source_id, raw, source.source_sha256, source.python_version,
              json.dumps(list(source.requirements), ensure_ascii=False, separators=(",", ":"))))


def get_source(source_id: str) -> RequirementSource | None:
    with _database() as connection:
        row = connection.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
    return _source_from_row(row) if row else None


def save_analysis(analysis: AnalysisResponse) -> None:
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _write_analysis(connection, analysis)
        connection.commit()


def get_analysis(analysis_id: str) -> AnalysisResponse | None:
    with _database() as connection:
        row = connection.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
        if not row:
            return None
        try:
            analysis = AnalysisResponse.model_validate_json(row["analysis_json"])
        except Exception:
            raise RuntimeError("Stored analysis is invalid.") from None
        proposal = analysis.repair_proposal
        if not proposal or proposal.status != "running":
            return analysis
        approval = connection.execute(
            "SELECT state, diff_sha256, owner_pid FROM resolver_approvals WHERE analysis_id = ? AND proposal_id = ?",
            (analysis_id, proposal.proposal_id),
        ).fetchone()
        owner_pid = approval["owner_pid"] if approval else None
        if approval and approval["state"] == "consumed" and approval["diff_sha256"] == proposal.diff_sha256 and _process_is_alive(owner_pid):
            return analysis

        # Recheck under a write lock so an active resolver result cannot be overwritten
        # by a concurrent read that noticed an old or malformed claim.
        connection.execute("BEGIN IMMEDIATE")
        latest = connection.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
        if not latest:
            connection.rollback()
            return None
        try:
            analysis = AnalysisResponse.model_validate_json(latest["analysis_json"])
        except Exception:
            connection.rollback()
            raise RuntimeError("Stored analysis is invalid.") from None
        proposal = analysis.repair_proposal
        if not proposal or proposal.status != "running":
            connection.rollback()
            return analysis
        approval = connection.execute(
            "SELECT state, diff_sha256, owner_pid FROM resolver_approvals WHERE analysis_id = ? AND proposal_id = ?",
            (analysis_id, proposal.proposal_id),
        ).fetchone()
        owner_pid = approval["owner_pid"] if approval else None
        if approval and approval["state"] == "consumed" and approval["diff_sha256"] == proposal.diff_sha256 and _process_is_alive(owner_pid):
            connection.rollback()
            return analysis
        incomplete = proposal.model_copy(update={
            "status": "unavailable",
            "summary": "Resolver outcome unknown/incomplete: the backend process ended before saving a result. The resolver approval remains consumed; no automatic retry will occur.",
            "error_code": "RESOLVER_OUTCOME_UNKNOWN",
            "resolver_evidence": proposal.resolver_evidence.model_copy(update={
                "status": "unknown", "failure_category": "unknown_failure",
            }) if proposal.resolver_evidence else None,
        })
        updated = analysis.model_copy(update={"repair_proposal": incomplete})
        _write_analysis(connection, updated)
        connection.commit()
        return updated


def _process_is_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _stage_owner_is_current(pid: int | None, instance_id: str | None) -> bool:
    return pid == os.getpid() and instance_id == _PROCESS_INSTANCE_ID


def update_analysis(analysis_id: str, **updates) -> AnalysisResponse | None:
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT analysis_json FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
        if not row:
            connection.rollback()
            return None
        try:
            existing = AnalysisResponse.model_validate_json(row["analysis_json"])
        except Exception:
            connection.rollback()
            raise RuntimeError("Stored analysis is invalid.") from None
        updated = existing.model_copy(update=updates)
        _write_analysis(connection, updated)
        connection.commit()
    return updated


def _stored_proposal_integrity(connection: sqlite3.Connection, row: sqlite3.Row,
                               analysis: AnalysisResponse, proposal_id: str,
                               diff_sha256: str,
                               allowed_statuses: tuple[str, ...] = ("awaiting_resolution",)) -> str | None:
    proposal = analysis.repair_proposal
    if not proposal:
        return "PROPOSAL_MISSING"
    source_row = connection.execute("SELECT * FROM sources WHERE source_id = ?", (analysis.source_id,)).fetchone()
    if not source_row:
        return "PROPOSAL_SOURCE_MISSING"
    from app.services.repair import validate_persisted_proposal
    source = _source_from_row(source_row)
    if source.source_sha256 != row["original_sha256"]:
        return "PROPOSAL_ORIGINAL_HASH_MISMATCH"
    if proposal.proposal_id != proposal_id or row["proposal_id"] != proposal_id:
        return "PROPOSAL_ID_MISMATCH"
    if proposal.diff_sha256 != diff_sha256 or row["diff_sha256"] != diff_sha256:
        return "PROPOSAL_DIFF_HASH_MISMATCH"
    if proposal.original_sha256 != source.source_sha256:
        return "PROPOSAL_ORIGINAL_HASH_MISMATCH"
    if bytes(source_row["original_bytes"]) != row["original_requirements_bytes"]:
        return "PROPOSAL_ORIGINAL_BYTES_MISMATCH"
    if row["proposal_status"] != proposal.status:
        return "PROPOSAL_STATUS_MISMATCH"
    return validate_persisted_proposal(
        proposal, proposal_id=row["proposal_id"], stored_diff_sha256=row["diff_sha256"],
        stored_original_sha256=row["original_sha256"],
        stored_original_bytes=row["original_requirements_bytes"],
        stored_proposed_bytes=row["proposed_requirements_bytes"], stored_diff_bytes=row["diff_bytes"],
        stored_target=row["target_python"], stored_candidates_json=row["candidate_metadata_json"],
        stored_provenance_json=row["provenance_json"], target_python=analysis.python_target,
        provenance=analysis.provenance, allowed_statuses=allowed_statuses,
    )


def claim_resolution(analysis_id: str, proposal_id: str, diff_sha256: str) -> bool:
    """Atomically consume one matching approval before any resolver call."""
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
        if not row:
            connection.rollback()
            return False
        try:
            analysis = AnalysisResponse.model_validate_json(row["analysis_json"])
        except Exception:
            connection.rollback()
            return False
        reason = _stored_proposal_integrity(connection, row, analysis, proposal_id, diff_sha256)
        if reason is not None:
            connection.rollback()
            return False
        prior = connection.execute(
            "SELECT state FROM resolver_approvals WHERE analysis_id = ? AND proposal_id = ?",
            (analysis_id, proposal_id),
        ).fetchone()
        if prior:
            connection.rollback()
            return False
        connection.execute(
            "INSERT INTO resolver_approvals(analysis_id, proposal_id, diff_sha256, state, consumed_at, owner_pid) VALUES (?, ?, ?, 'consumed', ?, ?)",
            (analysis_id, proposal_id, diff_sha256, datetime.now(UTC).isoformat(), os.getpid()),
        )
        running = analysis.repair_proposal.model_copy(update={
            "status": "running",
            "summary": "The one-shot resolver approval is consumed; the resolver outcome is pending.",
        })
        _write_analysis(connection, analysis.model_copy(update={"repair_proposal": running}))
        connection.commit()
    return True


def resolution_approval_state(analysis_id: str, proposal_id: str) -> tuple[str, str] | None:
    with _database() as connection:
        row = connection.execute(
            "SELECT state, diff_sha256 FROM resolver_approvals WHERE analysis_id = ? AND proposal_id = ?",
            (analysis_id, proposal_id),
        ).fetchone()
    return (row["state"], row["diff_sha256"]) if row else None


def _installation_stage_context_is_valid(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    analysis: AnalysisResponse,
    proposal_id: str,
    diff_sha256: str,
) -> bool:
    proposal = analysis.repair_proposal
    if not proposal or proposal.status != "ready" or not proposal.proposed_requirements:
        return False
    if proposal.resolver_evidence is None or proposal.resolver_evidence.status != "accepted":
        return False
    resolver = proposal.resolver_diagnostics
    if not resolver or not (
        resolver.execution_mode == "real" and resolver.status == "resolved"
        and resolver.exit_code == 0 and resolver.target_matches_sandbox_python is True
        and resolver.python_target == analysis.python_target
        and proposal.resolver_evidence.python_target == analysis.python_target
    ):
        return False
    return _stored_proposal_integrity(
        connection, row, analysis, proposal_id, diff_sha256, allowed_statuses=("ready",),
    ) is None


def claim_installation_stage(
    analysis_id: str,
    proposal_id: str,
    diff_sha256: str,
    stage_id: str,
    *,
    stage: str = "installation",
) -> InstallationStageResult | None:
    """Validate and atomically consume the unique installation approval."""
    if stage != "installation":
        return None
    now = datetime.now(UTC).isoformat()
    try:
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM analyses WHERE analysis_id=?", (analysis_id,)).fetchone()
            if not row:
                connection.rollback()
                return None
            try:
                analysis = AnalysisResponse.model_validate_json(row["analysis_json"])
            except Exception:
                connection.rollback()
                return None
            if not _installation_stage_context_is_valid(connection, row, analysis, proposal_id, diff_sha256):
                connection.rollback()
                return None
            existing = connection.execute(
                "SELECT 1 FROM verification_stage_approvals WHERE analysis_id=? AND proposal_id=? AND stage=?",
                (analysis_id, proposal_id, stage),
            ).fetchone()
            if existing:
                connection.rollback()
                return None
            result = InstallationStageResult(
                stage_id=stage_id, analysis_id=analysis_id, proposal_id=proposal_id,
                diff_sha256=diff_sha256, stage=stage, status="running", execution_mode="not_run",
                approval_state="consumed",
                environment=InstallationStageEnvironment(requested_python=analysis.python_target),
                limitation="Approval consumed; installation has not yet returned a result.",
                created_at=now, updated_at=now,
            )
            connection.execute(
                "INSERT INTO verification_stage_approvals(stage_id,analysis_id,proposal_id,diff_sha256,stage,state,consumed_at) VALUES (?,?,?,?,?,'consumed',?)",
                (stage_id, analysis_id, proposal_id, diff_sha256, stage, now),
            )
            connection.execute(
                "INSERT INTO verification_stage_history(stage_id,analysis_id,proposal_id,diff_sha256,stage,status,result_json,created_at,updated_at,owner_pid,owner_instance_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (stage_id, analysis_id, proposal_id, diff_sha256, stage, result.status,
                 result.model_dump_json(), now, now, os.getpid(), _PROCESS_INSTANCE_ID),
            )
            connection.commit()
            return result
    except sqlite3.IntegrityError:
        return None


def installation_stage_context(stage_id: str) -> tuple[AnalysisResponse, RequirementSource, str] | None:
    """Revalidate persisted proposal bytes immediately before the Sandbox adapter runs."""
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        stage = connection.execute(
            "SELECT * FROM verification_stage_history WHERE stage_id=?", (stage_id,)
        ).fetchone()
        if not stage or stage["status"] != "running":
            connection.rollback()
            return None
        approval = connection.execute(
            "SELECT * FROM verification_stage_approvals WHERE stage_id=?", (stage_id,)
        ).fetchone()
        row = connection.execute(
            "SELECT * FROM analyses WHERE analysis_id=?", (stage["analysis_id"],)
        ).fetchone()
        if not approval or approval["state"] != "consumed" or not row:
            connection.rollback()
            return None
        try:
            analysis = AnalysisResponse.model_validate_json(row["analysis_json"])
        except Exception:
            connection.rollback()
            return None
        if not _installation_stage_context_is_valid(
            connection, row, analysis, stage["proposal_id"], stage["diff_sha256"]
        ):
            connection.rollback()
            return None
        source_row = connection.execute(
            "SELECT * FROM sources WHERE source_id=?", (analysis.source_id,)
        ).fetchone()
        if not source_row:
            connection.rollback()
            return None
        source = _source_from_row(source_row)
        proposal = analysis.repair_proposal
        if source.source_sha256 != proposal.original_sha256 or source.original_text.encode("utf-8") != row["original_requirements_bytes"]:
            connection.rollback()
            return None
        if row["proposed_requirements_bytes"] is None:
            connection.rollback()
            return None
        try:
            requirements = bytes(row["proposed_requirements_bytes"]).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            connection.rollback()
            return None
        if requirements.encode("utf-8") != proposal.proposed_requirements.encode("utf-8"):
            connection.rollback()
            return None
        connection.commit()
        return analysis, source, requirements


def mark_installation_stage_started(stage_id: str) -> InstallationStageResult | None:
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT result_json,status FROM verification_stage_history WHERE stage_id=?", (stage_id,)
        ).fetchone()
        if not row or row["status"] != "running":
            connection.rollback()
            return None
        result = InstallationStageResult.model_validate_json(row["result_json"]).model_copy(update={
            "execution_mode": "real", "limitation": "Installation stage is running in the isolated Sandbox.",
            "updated_at": datetime.now(UTC).isoformat(),
        })
        connection.execute(
            "UPDATE verification_stage_history SET result_json=?,updated_at=?,owner_pid=?,owner_instance_id=? WHERE stage_id=? AND status='running'",
            (result.model_dump_json(), result.updated_at, os.getpid(), _PROCESS_INSTANCE_ID, stage_id),
        )
        connection.commit()
        return result


def persist_installation_operation_id(stage_id: str, operation_id: str) -> bool:
    """Persist the SDK submission UUID while its one-shot stage is still running."""
    try:
        safe_id = str(UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        return False
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for table in ("verification_attempt_history", "verification_stage_history"):
            row = connection.execute(
                f"SELECT result_json,status,stage FROM {table} WHERE stage_id=?", (stage_id,)
            ).fetchone()
            if row is None:
                continue
            if row["status"] != "running" or row["stage"] != "installation":
                connection.rollback()
                return False
            try:
                result = InstallationStageResult.model_validate_json(row["result_json"])
            except Exception:
                connection.rollback()
                return False
            if result.contree_operation_id not in (None, safe_id):
                connection.rollback()
                return False
            now = datetime.now(UTC).isoformat()
            updated = result.model_copy(update={"contree_operation_id": safe_id, "updated_at": now})
            connection.execute(
                f"UPDATE {table} SET contree_operation_id=?,result_json=?,updated_at=? "
                "WHERE stage_id=? AND status='running' AND stage='installation'",
                (safe_id, updated.model_dump_json(), now, stage_id),
            )
            connection.commit()
            return True
        connection.rollback()
        return False


def save_installation_stage_result(result: InstallationStageResult) -> bool:
    result = _sanitize_verification_result(result)
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT analysis_id,proposal_id,diff_sha256,stage,status,contree_operation_id FROM verification_stage_history WHERE stage_id=?",
            (result.stage_id,),
        ).fetchone()
        if not row or row["status"] != "running" or (
            row["analysis_id"], row["proposal_id"], row["diff_sha256"], row["stage"]
        ) != (result.analysis_id, result.proposal_id, result.diff_sha256, result.stage):
            connection.rollback()
            return False
        if result.contree_operation_id is None and row["contree_operation_id"]:
            result = result.model_copy(update={"contree_operation_id": row["contree_operation_id"]})
        connection.execute(
            "UPDATE verification_stage_history SET status=?,result_json=?,contree_operation_id=?,updated_at=? WHERE stage_id=? AND status='running'",
            (result.status, result.model_dump_json(), result.contree_operation_id, result.updated_at, result.stage_id),
        )
        connection.commit()
        return True


def get_installation_stage(stage_id: str) -> InstallationStageResult | None:
    with _database() as connection:
        row = connection.execute(
            "SELECT result_json,owner_pid,owner_instance_id,status FROM verification_stage_history WHERE stage_id=?",
            (stage_id,),
        ).fetchone()
        if not row:
            return None
        try:
            result = InstallationStageResult.model_validate_json(row["result_json"])
        except Exception:
            raise RuntimeError("Stored installation stage result is invalid.") from None
        if row["status"] != "running" or _stage_owner_is_current(row["owner_pid"], row["owner_instance_id"]):
            return result
        connection.execute("BEGIN IMMEDIATE")
        latest = connection.execute(
            "SELECT result_json,owner_pid,owner_instance_id,status FROM verification_stage_history WHERE stage_id=?", (stage_id,)
        ).fetchone()
        if not latest or latest["status"] != "running" or _stage_owner_is_current(latest["owner_pid"], latest["owner_instance_id"]):
            connection.rollback()
            return InstallationStageResult.model_validate_json(latest["result_json"]) if latest else None
        current = InstallationStageResult.model_validate_json(latest["result_json"]).model_copy(update={
            "status": "unknown", "failure_category": "unknown_failure",
            "limitation": "Installation outcome unknown/incomplete: the backend stopped before persisting a result. Approval remains consumed; no automatic retry will occur.",
            "updated_at": datetime.now(UTC).isoformat(),
        })
        connection.execute(
            "UPDATE verification_stage_history SET status='unknown',result_json=?,updated_at=? WHERE stage_id=? AND status='running'",
            (current.model_dump_json(), current.updated_at, stage_id),
        )
        connection.commit()
        return current


def latest_installation_stage(analysis_id: str, proposal_id: str) -> InstallationStageResult | None:
    """Return the persisted installation attempt for this exact proposal, if any."""
    with _database() as connection:
        row = connection.execute(
            "SELECT stage_id FROM verification_stage_history "
            "WHERE analysis_id=? AND proposal_id=? AND stage='installation' "
            "ORDER BY created_at DESC LIMIT 1",
            (analysis_id, proposal_id),
        ).fetchone()
    return get_installation_stage(row["stage_id"]) if row else None


def installation_stage_approval_state(analysis_id: str, proposal_id: str, stage: str = "installation") -> tuple[str, str] | None:
    with _database() as connection:
        row = connection.execute(
            "SELECT state,diff_sha256 FROM verification_stage_approvals WHERE analysis_id=? AND proposal_id=? AND stage=?",
            (analysis_id, proposal_id, stage),
        ).fetchone()
    return (row["state"], row["diff_sha256"]) if row else None


def claim_verification_attempt(
    analysis_id: str, proposal_id: str, diff_sha256: str, stage_id: str,
    request_id: str, *, stage: str,
) -> InstallationStageResult | ImportStageResult | None:
    """Persist a fresh, one-shot stage approval without touching legacy records."""
    if stage not in {"installation", "imports"} or not request_id or len(request_id) > 96:
        return None
    now = datetime.now(UTC).isoformat()
    try:
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM analyses WHERE analysis_id=?", (analysis_id,)).fetchone()
            if not row:
                connection.rollback()
                return None
            try:
                analysis = AnalysisResponse.model_validate_json(row["analysis_json"])
            except Exception:
                connection.rollback()
                return None
            if not _installation_stage_context_is_valid(connection, row, analysis, proposal_id, diff_sha256):
                connection.rollback()
                return None
            running = connection.execute(
                "SELECT 1 FROM verification_attempt_history WHERE analysis_id=? AND proposal_id=? AND status='running'",
                (analysis_id, proposal_id),
            ).fetchone()
            if running:
                connection.rollback()
                return None
            # A v4 installation may still be executing while a v5 backend is
            # deployed. Do not let the additive multi-attempt workflow overlap
            # that legacy operation; reading it leaves its history/approval intact.
            legacy_running = connection.execute(
                "SELECT 1 FROM verification_stage_history "
                "WHERE analysis_id=? AND proposal_id=? AND status='running' LIMIT 1",
                (analysis_id, proposal_id),
            ).fetchone()
            if legacy_running:
                connection.rollback()
                return None
            image_ref = None
            if stage == "imports":
                prior = connection.execute(
                    "SELECT status,sandbox_image_ref FROM verification_attempt_history "
                    "WHERE analysis_id=? AND proposal_id=? AND stage='installation' ORDER BY created_at DESC LIMIT 1",
                    (analysis_id, proposal_id),
                ).fetchone()
                try:
                    image_ref = str(UUID(prior["sandbox_image_ref"])) if prior and prior["status"] == "passed" and prior["sandbox_image_ref"] else None
                except (ValueError, TypeError):
                    image_ref = None
                if image_ref is None:
                    connection.rollback()
                    return None
            if stage == "installation":
                result: InstallationStageResult | ImportStageResult = InstallationStageResult(
                    stage_id=stage_id, analysis_id=analysis_id, proposal_id=proposal_id,
                    diff_sha256=diff_sha256, stage="installation", status="running",
                    execution_mode="not_run", approval_state="consumed",
                    environment=InstallationStageEnvironment(requested_python=analysis.python_target),
                    limitation="Fresh approval consumed; installation has not returned a result.",
                    created_at=now, updated_at=now,
                )
            else:
                result = ImportStageResult(
                    stage_id=stage_id, analysis_id=analysis_id, proposal_id=proposal_id,
                    diff_sha256=diff_sha256, stage="imports", status="running",
                    execution_mode="not_run", approval_state="consumed",
                    environment=InstallationStageEnvironment(requested_python=analysis.python_target),
                    limitation="Import approval consumed; no import result has been returned.",
                    created_at=now, updated_at=now,
                )
            connection.execute(
                "INSERT INTO verification_attempt_history(stage_id,analysis_id,proposal_id,diff_sha256,stage,status,result_json,request_id,sandbox_image_ref,created_at,updated_at,owner_pid,owner_instance_id) "
                "VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?)",
                (stage_id, analysis_id, proposal_id, diff_sha256, stage, "running", result.model_dump_json(),
                 request_id, now, now, os.getpid(), _PROCESS_INSTANCE_ID),
            )
            connection.execute(
                "INSERT INTO verification_attempt_approvals(stage_id,analysis_id,proposal_id,diff_sha256,stage,state,consumed_at) "
                "VALUES (?,?,?,?,?,'consumed',?)",
                (stage_id, analysis_id, proposal_id, diff_sha256, stage, now),
            )
            connection.commit()
            return result
    except sqlite3.IntegrityError:
        return None


def get_verification_attempt(stage_id: str) -> InstallationStageResult | ImportStageResult | None:
    with _database() as connection:
        row = connection.execute("SELECT * FROM verification_attempt_history WHERE stage_id=?", (stage_id,)).fetchone()
        if not row:
            return None
        approval = connection.execute("SELECT * FROM verification_attempt_approvals WHERE stage_id=?", (stage_id,)).fetchone()
        if not approval or approval["state"] != "consumed" or (
            approval["analysis_id"], approval["proposal_id"], approval["diff_sha256"], approval["stage"]
        ) != (row["analysis_id"], row["proposal_id"], row["diff_sha256"], row["stage"]):
            return None
        model = InstallationStageResult if row["stage"] == "installation" else ImportStageResult
        result = model.model_validate_json(row["result_json"])
        if row["status"] != "running" or _stage_owner_is_current(row["owner_pid"], row["owner_instance_id"]):
            return result
        connection.execute("BEGIN IMMEDIATE")
        latest = connection.execute("SELECT * FROM verification_attempt_history WHERE stage_id=?", (stage_id,)).fetchone()
        if not latest or latest["status"] != "running" or _stage_owner_is_current(latest["owner_pid"], latest["owner_instance_id"]):
            connection.rollback()
            if latest:
                result_model = InstallationStageResult if latest["stage"] == "installation" else ImportStageResult
                return result_model.model_validate_json(latest["result_json"])
            return None
        model = InstallationStageResult if latest["stage"] == "installation" else ImportStageResult
        recovered = model.model_validate_json(latest["result_json"]).model_copy(update={
            "status": "unknown", "failure_category": "unknown_failure",
            "limitation": "Outcome unknown/incomplete: backend stopped before persisting a result. Approval remains consumed; no automatic retry will occur.",
            "updated_at": datetime.now(UTC).isoformat(),
        })
        connection.execute(
            "UPDATE verification_attempt_history SET status='unknown',result_json=?,updated_at=? WHERE stage_id=? AND status='running'",
            (recovered.model_dump_json(), recovered.updated_at, stage_id),
        )
        connection.commit()
        return recovered


def list_verification_attempts(analysis_id: str, proposal_id: str, stage: str) -> list[InstallationStageResult | ImportStageResult]:
    attempt_ids: list[str] = []
    legacy_ids: list[str] = []
    with _database() as connection:
        rows = connection.execute(
            "SELECT stage_id FROM verification_attempt_history WHERE analysis_id=? AND proposal_id=? AND stage=?",
            (analysis_id, proposal_id, stage),
        ).fetchall()
        attempt_ids.extend(item["stage_id"] for item in rows)
        if stage == "installation":
            legacy = connection.execute(
                "SELECT stage_id FROM verification_stage_history WHERE analysis_id=? AND proposal_id=? AND stage='installation'",
                (analysis_id, proposal_id),
            ).fetchall()
            legacy_ids.extend(item["stage_id"] for item in legacy)
    results = []
    for item in attempt_ids:
        result = get_verification_attempt(item)
        if result is not None:
            results.append(result)
    for item in legacy_ids:
        result = get_installation_stage(item)
        if result is not None:
            results.append(result)
    return sorted(results, key=lambda item: item.created_at)


def verification_attempt_context(stage_id: str) -> tuple[AnalysisResponse, RequirementSource, str, str, str | None] | None:
    """Revalidate exact proposal bytes and the consumed approval before dispatch."""
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        attempt = connection.execute("SELECT * FROM verification_attempt_history WHERE stage_id=?", (stage_id,)).fetchone()
        if not attempt or attempt["status"] != "running":
            connection.rollback()
            return None
        approval = connection.execute("SELECT * FROM verification_attempt_approvals WHERE stage_id=?", (stage_id,)).fetchone()
        row = connection.execute("SELECT * FROM analyses WHERE analysis_id=?", (attempt["analysis_id"],)).fetchone()
        if not approval or approval["state"] != "consumed" or (
            approval["analysis_id"], approval["proposal_id"], approval["diff_sha256"], approval["stage"]
        ) != (attempt["analysis_id"], attempt["proposal_id"], attempt["diff_sha256"], attempt["stage"]) or not row:
            connection.rollback()
            return None
        try:
            analysis = AnalysisResponse.model_validate_json(row["analysis_json"])
        except Exception:
            connection.rollback()
            return None
        if not _installation_stage_context_is_valid(connection, row, analysis, attempt["proposal_id"], attempt["diff_sha256"]):
            connection.rollback()
            return None
        source_row = connection.execute("SELECT * FROM sources WHERE source_id=?", (analysis.source_id,)).fetchone()
        if not source_row or row["proposed_requirements_bytes"] is None:
            connection.rollback()
            return None
        source = _source_from_row(source_row)
        proposal = analysis.repair_proposal
        if source.original_text.encode("utf-8") != row["original_requirements_bytes"] or source.source_sha256 != proposal.original_sha256:
            connection.rollback()
            return None
        requirements = bytes(row["proposed_requirements_bytes"])
        if requirements != proposal.proposed_requirements.encode("utf-8"):
            connection.rollback()
            return None
        image_ref = attempt["sandbox_image_ref"]
        if attempt["stage"] == "imports":
            prior = connection.execute(
                "SELECT status,sandbox_image_ref FROM verification_attempt_history WHERE analysis_id=? AND proposal_id=? AND stage='installation' ORDER BY created_at DESC LIMIT 1",
                (attempt["analysis_id"], attempt["proposal_id"]),
            ).fetchone()
            try:
                valid_ref = str(UUID(prior["sandbox_image_ref"])) if prior and prior["status"] == "passed" and prior["sandbox_image_ref"] else None
            except (ValueError, TypeError):
                valid_ref = None
            if valid_ref is None or attempt["sandbox_image_ref"]:
                connection.rollback()
                return None
            image_ref = valid_ref
        connection.commit()
        return analysis, source, requirements.decode("utf-8", errors="strict"), attempt["stage"], image_ref


def mark_verification_attempt_started(stage_id: str) -> InstallationStageResult | ImportStageResult | None:
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM verification_attempt_history WHERE stage_id=?", (stage_id,)).fetchone()
        if not row or row["status"] != "running":
            connection.rollback()
            return None
        model = InstallationStageResult if row["stage"] == "installation" else ImportStageResult
        result = model.model_validate_json(row["result_json"]).model_copy(update={
            "execution_mode": "real", "limitation": f"{row['stage'].capitalize()} stage is running in the isolated Sandbox.",
            "updated_at": datetime.now(UTC).isoformat(),
        })
        connection.execute(
            "UPDATE verification_attempt_history SET result_json=?,updated_at=?,owner_pid=?,owner_instance_id=? WHERE stage_id=? AND status='running'",
            (result.model_dump_json(), result.updated_at, os.getpid(), _PROCESS_INSTANCE_ID, stage_id),
        )
        connection.commit()
        return result


def save_verification_attempt_result(
    result: InstallationStageResult | ImportStageResult, sandbox_image_ref: str | None = None,
) -> bool:
    result = _sanitize_verification_result(result)
    image_ref = None
    if isinstance(result, InstallationStageResult) and result.status == "passed" and result.environment.target_matches_sandbox_python is True and sandbox_image_ref:
        try:
            image_ref = str(UUID(sandbox_image_ref))
        except (ValueError, TypeError):
            image_ref = None
    if isinstance(result, InstallationStageResult):
        result = result.model_copy(update={"artifact_available": image_ref is not None})
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT analysis_id,proposal_id,diff_sha256,stage,status,contree_operation_id FROM verification_attempt_history WHERE stage_id=?", (result.stage_id,)).fetchone()
        if not row or row["status"] != "running" or (
            row["analysis_id"], row["proposal_id"], row["diff_sha256"], row["stage"]
        ) != (result.analysis_id, result.proposal_id, result.diff_sha256, result.stage):
            connection.rollback()
            return False
        if isinstance(result, InstallationStageResult) and result.contree_operation_id is None and row["contree_operation_id"]:
            result = result.model_copy(update={"contree_operation_id": row["contree_operation_id"]})
        connection.execute(
            "UPDATE verification_attempt_history SET status=?,result_json=?,sandbox_image_ref=?,contree_operation_id=?,updated_at=? WHERE stage_id=? AND status='running'",
            (result.status, result.model_dump_json(), image_ref, result.contree_operation_id if isinstance(result, InstallationStageResult) else None, result.updated_at, result.stage_id),
        )
        connection.commit()
        return True


_PERSISTED_FAILURE_PHASES = {
    "sdk_configuration", "image_selection", "operation_submission", "operation_wait",
    "import_image_selection", "import_operation_submission", "import_operation_wait",
    "client_configuration", "file_staging", "operation_tracking", "image_validation",
    "adapter_invocation", "setup", "resolver_version", "environment", "installation", "imports", "unknown",
}


def _safe_persisted_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    safe = re.sub(r"(?i)https?://\S+", "[redacted URL]", value)
    safe = re.sub(r"(?i)\b(?:authorization\s*[:=]\s*)?(?:bearer|basic)\s+[^\s,;]+", "[redacted credential]", safe)
    safe = re.sub(r"(?i)\b[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API[_-]?KEY|CREDENTIAL)[A-Z0-9_]*\s*[:=]\s*[^,\s;]+", "[redacted secret]", safe)
    safe = "".join(ch for ch in safe if ch.isprintable() or ch in "\t\n")
    return safe[-limit:]


def _sanitize_verification_result(result: InstallationStageResult | ImportStageResult):
    phase = result.failure_phase
    if phase is not None and phase not in _PERSISTED_FAILURE_PHASES:
        phase = "unknown"
    environment = result.environment.model_copy(update={
        "os": _safe_persisted_text(result.environment.os, 160),
        "python_version": _safe_persisted_text(result.environment.python_version, 64),
    })
    return result.model_copy(update={
        "failure_phase": phase,
        "failure_detail": _safe_persisted_text(result.failure_detail, 240),
        "stdout": _safe_persisted_text(result.stdout, 4000) or "",
        "stderr": _safe_persisted_text(result.stderr, 4000) or "",
        "limitation": _safe_persisted_text(result.limitation, 600),
        "environment": environment,
    })


def verification_attempt_approval_state(stage_id: str) -> tuple[str, str, str] | None:
    with _database() as connection:
        row = connection.execute(
            "SELECT state,diff_sha256,stage FROM verification_attempt_approvals WHERE stage_id=?",
            (stage_id,),
        ).fetchone()
    return (row["state"], row["diff_sha256"], row["stage"]) if row else None


def retained_installation_image(stage_id: str) -> str | None:
    with _database() as connection:
        row = connection.execute(
            "SELECT stage,status,sandbox_image_ref FROM verification_attempt_history WHERE stage_id=?",
            (stage_id,),
        ).fetchone()
    if not row or row["stage"] != "installation" or row["status"] != "passed" or not row["sandbox_image_ref"]:
        return None
    try:
        return str(UUID(row["sandbox_image_ref"]))
    except (ValueError, TypeError):
        return None


def persisted_proposal_is_valid(analysis_id: str, proposal_id: str, diff_sha256: str) -> bool:
    with _database() as connection:
        row = connection.execute("SELECT * FROM analyses WHERE analysis_id=?", (analysis_id,)).fetchone()
        if not row:
            return False
        try:
            analysis = AnalysisResponse.model_validate_json(row["analysis_json"])
        except Exception:
            return False
        return _installation_stage_context_is_valid(connection, row, analysis, proposal_id, diff_sha256)


def save_verification(result: VerificationResult) -> None:
    with _lock:
        _verification_cache[result.verification_id] = result


def get_verification(verification_id: str) -> VerificationResult | None:
    with _lock:
        return _verification_cache.get(verification_id)


def update_verification(verification_id: str, **updates) -> VerificationResult | None:
    with _lock:
        existing = _verification_cache.get(verification_id)
        if existing is None:
            return None
        values = existing.model_dump()
        values.update(updates)
        updated = VerificationResult.model_validate(values)
        _verification_cache[verification_id] = updated
        return updated


def active_verification(analysis_id: str, proposal_id: str) -> VerificationResult | None:
    with _lock:
        return next((job for job in _verification_cache.values() if job.analysis_id == analysis_id and job.proposal_id == proposal_id and job.status in {"queued", "running"}), None)


def approve_download(analysis_id: str, proposal_id: str, diff_sha256: str) -> None:
    with _lock:
        _download_approvals[analysis_id] = (proposal_id, diff_sha256)


def is_download_approved(analysis_id: str, proposal_id: str, diff_sha256: str) -> bool:
    with _lock:
        return _download_approvals.get(analysis_id) == (proposal_id, diff_sha256)


def save_repair_preview(analysis_id: str, preview: RepairRequestPreview, candidates: dict) -> None:
    with _lock:
        _repair_previews[analysis_id] = preview
        _repair_candidates[analysis_id] = candidates


def get_repair_candidates(analysis_id: str) -> dict:
    with _lock:
        return dict(_repair_candidates.get(analysis_id, {}))


def get_repair_preview(analysis_id: str) -> RepairRequestPreview | None:
    with _lock:
        return _repair_previews.get(analysis_id)


def consume_repair_preview(analysis_id: str, preview_sha256: str) -> dict | None:
    with _lock:
        preview = _repair_previews.get(analysis_id)
        key = (analysis_id, preview_sha256)
        if not preview or preview.preview_sha256 != preview_sha256 or key in _repair_preview_claims:
            return None
        _repair_preview_claims.add(key)
        return dict(_repair_candidates.get(analysis_id, {}))


def clear_workflow_state() -> None:
    """Clear test/workflow state, including persisted local records."""
    with _lock:
        _verification_cache.clear()
        _download_approvals.clear()
        _repair_previews.clear()
        _repair_candidates.clear()
        _repair_preview_claims.clear()
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM resolver_approvals")
        connection.execute("DELETE FROM verification_stage_approvals")
        connection.execute("DELETE FROM verification_stage_history")
        connection.execute("DELETE FROM verification_attempt_approvals")
        connection.execute("DELETE FROM verification_attempt_history")
        connection.execute("DELETE FROM analyses")
        connection.execute("DELETE FROM sources")
        connection.commit()
