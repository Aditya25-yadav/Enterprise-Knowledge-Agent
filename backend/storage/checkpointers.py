"""
LangGraph Checkpointer Providers & Thread Persistence Layer (Phase 9+).

Provides persistent conversation state storage and checkpointing across multi-turn sessions:
  1. InMemorySaver: High-speed volatile in-memory checkpointer for testing and unit tests.
  2. SqliteCheckpointSaver: Embedded, zero-dependency disk-persistent checkpointer for local REPL and servers.
  3. Factory function `get_checkpointer(mode, db_path)` for seamless switching between storage backends.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    PendingWrite,
    SerializerProtocol,
    get_checkpoint_id,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

logger = logging.getLogger(__name__)


class SqliteCheckpointSaver(BaseCheckpointSaver[str]):
    """
    Embedded SQLite-backed CheckpointSaver for persistent LangGraph conversation sessions.
    Stores checkpoint snapshots, serialized messages, channel values, and pending writes
    into a local SQLite database without requiring external server dependencies.
    """

    def __init__(
        self,
        db_path: Union[str, Path] = "./data/chat_sessions.db",
        serde: Optional[SerializerProtocol] = None,
    ) -> None:
        if serde is None:
            allowed = [
                ("backend.models.graph", "NodeLabel"),
                ("backend.models.graph", "RelType"),
                ("backend.models.graph", "NodeType"),
                ("backend.models.graph", "GraphNode"),
                ("backend.models.graph", "GraphRelationship"),
                ("backend.models.okf", "OKFConcept"),
                ("backend.models.okf", "OKFPermissions"),
                ("backend.models.document", "Document"),
            ]
            serde = JsonPlusSerializer(allowed_msgpack_modules=allowed)
        super().__init__(serde=serde)
        self.db_path = str(db_path)
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Returns a connection configured for robust concurrent checkpoint writes."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        """Initializes tables for checkpoints and pending task writes."""
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    parent_checkpoint_id TEXT,
                    type TEXT NOT NULL,
                    checkpoint_bytes BLOB NOT NULL,
                    meta_type TEXT NOT NULL,
                    metadata_bytes BLOB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS writes (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    val_type TEXT NOT NULL,
                    value_bytes BLOB NOT NULL,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_checkpoints_thread 
                ON checkpoints(thread_id, checkpoint_ns, created_at DESC);
                """
            )
            conn.commit()

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """
        Fetches the latest or specific checkpoint tuple for the configured thread_id.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        with self._get_conn() as conn:
            if checkpoint_id:
                row = conn.execute(
                    """
                    SELECT checkpoint_id, parent_checkpoint_id, type, checkpoint_bytes, meta_type, metadata_bytes
                    FROM checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?;
                    """,
                    (thread_id, checkpoint_ns, checkpoint_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT checkpoint_id, parent_checkpoint_id, type, checkpoint_bytes, meta_type, metadata_bytes
                    FROM checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ?
                    ORDER BY created_at DESC, checkpoint_id DESC
                    LIMIT 1;
                    """,
                    (thread_id, checkpoint_ns),
                ).fetchone()

            if not row:
                return None

            c_id, p_id, c_type, c_bytes, m_type, m_bytes = row
            checkpoint = self.serde.loads_typed((c_type, c_bytes))
            metadata = self.serde.loads_typed((m_type, m_bytes)) if m_bytes else {}

            writes_rows = conn.execute(
                """
                SELECT task_id, channel, val_type, value_bytes
                FROM writes
                WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                ORDER BY idx ASC;
                """,
                (thread_id, checkpoint_ns, c_id),
            ).fetchall()

            pending_writes: List[PendingWrite] = [
                (t_id, ch, self.serde.loads_typed((v_type, v_bytes)))
                for t_id, ch, v_type, v_bytes in writes_rows
            ]

            parent_config: Optional[RunnableConfig] = (
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": p_id,
                    }
                }
                if p_id
                else None
            )

            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": c_id,
                    }
                },
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_config,
                pending_writes=pending_writes,
            )

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """Lists historical checkpoint tuples for the thread in reverse chronological order."""
        if not config or "configurable" not in config or "thread_id" not in config["configurable"]:
            return iter([])

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")

        query = """
            SELECT checkpoint_id, parent_checkpoint_id, type, checkpoint_bytes, meta_type, metadata_bytes
            FROM checkpoints
            WHERE thread_id = ? AND checkpoint_ns = ?
            ORDER BY created_at DESC, checkpoint_id DESC
        """
        params: List[Any] = [thread_id, checkpoint_ns]
        if limit:
            query += f" LIMIT {int(limit)}"

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        results: List[CheckpointTuple] = []
        for c_id, p_id, c_type, c_bytes, m_type, m_bytes in rows:
            checkpoint = self.serde.loads_typed((c_type, c_bytes))
            metadata = self.serde.loads_typed((m_type, m_bytes)) if m_bytes else {}
            parent_config = (
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": p_id,
                    }
                }
                if p_id
                else None
            )
            results.append(
                CheckpointTuple(
                    config={
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": checkpoint_ns,
                            "checkpoint_id": c_id,
                        }
                    },
                    checkpoint=checkpoint,
                    metadata=metadata,
                    parent_config=parent_config,
                    pending_writes=[],
                )
            )
        return iter(results)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Dict[str, Any],
    ) -> RunnableConfig:
        """Saves a checkpoint snapshot into the SQLite database."""
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_checkpoint_id = config["configurable"].get("checkpoint_id")

        c_type, c_bytes = self.serde.dumps_typed(checkpoint)
        m_type, m_bytes = self.serde.dumps_typed(metadata)

        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints (
                    thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
                    type, checkpoint_bytes, meta_type, metadata_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id,
                    parent_checkpoint_id,
                    c_type,
                    c_bytes,
                    m_type,
                    m_bytes,
                ),
            )
            conn.commit()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Stores intermediate node write blobs into the writes table."""
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        with self._get_conn() as conn:
            for idx, (channel, value) in enumerate(writes):
                v_type, v_bytes = self.serde.dumps_typed(value)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO writes (
                        thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, val_type, value_bytes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, v_type, v_bytes),
                )
            conn.commit()

    def get_all_threads(self) -> List[str]:
        """Returns list of all unique thread IDs currently saved in the database."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY created_at DESC;").fetchall()
        return [r[0] for r in rows]

    def delete_thread(self, thread_id: str) -> None:
        """Deletes all checkpoints and pending writes for a specific thread ID."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM checkpoints WHERE thread_id = ?;", (thread_id,))
            conn.execute("DELETE FROM writes WHERE thread_id = ?;", (thread_id,))
            conn.commit()


def get_checkpointer(
    mode: str = "memory",
    db_path: Union[str, Path] = "./data/chat_sessions.db",
) -> BaseCheckpointSaver:
    """
    Factory function for instantiating the requested LangGraph Checkpointer.

    Args:
        mode: Checkpointer backend ('memory' for volatile in-memory, 'sqlite' for persistent disk).
        db_path: Local filesystem path to SQLite database file when mode is 'sqlite'.

    Returns:
        Configured BaseCheckpointSaver instance.
    """
    mode_lower = mode.strip().lower()
    if mode_lower == "sqlite":
        return SqliteCheckpointSaver(db_path=db_path)
    return MemorySaver()
