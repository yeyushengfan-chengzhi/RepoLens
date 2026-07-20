import json
import sqlite3
import threading
import time
from pathlib import Path

from .schemas import IssueAiAnalysis


class AnalysisCache:
    def __init__(self, path: str | Path, max_items: int = 256) -> None:
        self.path = str(path)
        self.max_items = max_items
        self._lock = threading.Lock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS issue_analysis_cache (
                repository TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                accessed_at INTEGER NOT NULL,
                PRIMARY KEY (repository, issue_number, updated_at, prompt_version)
            )
            """
        )
        migration_time = time.time_ns()
        self._connection.execute(
            """
            UPDATE issue_analysis_cache
            SET created_at = ?, accessed_at = ?
            WHERE CAST(created_at AS TEXT) LIKE '%-%'
               OR CAST(accessed_at AS TEXT) LIKE '%-%'
            """,
            (migration_time, migration_time),
        )
        self._connection.commit()

    def get(
        self,
        repository: str,
        issue_number: int,
        updated_at: str,
        prompt_version: str,
    ) -> IssueAiAnalysis | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT analysis_json
                FROM issue_analysis_cache
                WHERE repository = ? AND issue_number = ?
                  AND updated_at = ? AND prompt_version = ?
                """,
                (repository, issue_number, updated_at, prompt_version),
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                """
                UPDATE issue_analysis_cache
                SET accessed_at = ?
                WHERE repository = ? AND issue_number = ?
                  AND updated_at = ? AND prompt_version = ?
                """,
                (
                    time.time_ns(),
                    repository,
                    issue_number,
                    updated_at,
                    prompt_version,
                ),
            )
            self._connection.commit()
        try:
            return IssueAiAnalysis.model_validate(json.loads(row[0]))
        except (json.JSONDecodeError, ValueError, TypeError):
            self.delete(repository, issue_number, updated_at, prompt_version)
            return None

    def put(
        self,
        repository: str,
        issue_number: int,
        updated_at: str,
        prompt_version: str,
        analysis: IssueAiAnalysis,
    ) -> None:
        serialized = analysis.model_dump_json()
        now = time.time_ns()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO issue_analysis_cache (
                    repository, issue_number, updated_at, prompt_version, analysis_json,
                    created_at, accessed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, issue_number, updated_at, prompt_version)
                DO UPDATE SET analysis_json = excluded.analysis_json,
                              accessed_at = excluded.accessed_at
                """,
                (
                    repository,
                    issue_number,
                    updated_at,
                    prompt_version,
                    serialized,
                    now,
                    now,
                ),
            )
            self._connection.execute(
                """
                DELETE FROM issue_analysis_cache
                WHERE rowid IN (
                    SELECT rowid FROM issue_analysis_cache
                    ORDER BY accessed_at DESC, created_at DESC, rowid DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self.max_items,),
            )
            self._connection.commit()

    def delete(
        self,
        repository: str,
        issue_number: int,
        updated_at: str,
        prompt_version: str,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                DELETE FROM issue_analysis_cache
                WHERE repository = ? AND issue_number = ?
                  AND updated_at = ? AND prompt_version = ?
                """,
                (repository, issue_number, updated_at, prompt_version),
            )
            self._connection.commit()

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM issue_analysis_cache")
            self._connection.commit()

    def count(self) -> int:
        with self._lock:
            return int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM issue_analysis_cache"
                ).fetchone()[0]
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
