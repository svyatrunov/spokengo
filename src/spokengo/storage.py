"""Local persistence: transcript history + audio files + a bounded offline retry
queue. Safety net: nothing dictated is lost, even offline. Retries are bounded
by an ``attempts`` counter so a stuck item never loops forever.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

STATUS_DONE = "done"
STATUS_PENDING = "pending"   # transient failure, eligible for manual retry
STATUS_FAILED = "failed"     # permanent failure or retries exhausted
STATUS_CANCELLED = "cancelled"  # user-cancelled; audio held briefly then purged


@dataclass
class Record:
    id: int
    ts: float
    text: str
    provider: str
    model: str
    duration: float
    audio_path: Optional[str]
    status: str
    attempts: int = 0


class Storage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.audio_dir = self.root / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "history.db"
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS transcripts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                duration REAL NOT NULL DEFAULT 0,
                audio_path TEXT,
                status TEXT NOT NULL DEFAULT 'done',
                attempts INTEGER NOT NULL DEFAULT 0
            )"""
        )
        # migrate old DBs that predate the attempts column
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(transcripts)")}
        if "attempts" not in cols:
            self._conn.execute("ALTER TABLE transcripts ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        self._conn.commit()

    def save_audio(self, frames_wav_bytes: bytes, ts: Optional[float] = None) -> str:
        ts = ts or time.time()
        path = self.audio_dir / f"rec_{int(ts*1000)}.wav"
        path.write_bytes(frames_wav_bytes)
        return str(path)

    def add(self, text, provider, model, duration, audio_path=None,
            status=STATUS_DONE, ts=None) -> int:
        ts = ts or time.time()
        cur = self._conn.execute(
            "INSERT INTO transcripts(ts,text,provider,model,duration,audio_path,status)"
            " VALUES(?,?,?,?,?,?,?)",
            (ts, text, provider, model, duration, audio_path, status))
        self._conn.commit()
        return int(cur.lastrowid)

    def mark_done(self, rec_id, text) -> None:
        self._conn.execute("UPDATE transcripts SET status=?, text=? WHERE id=?",
                           (STATUS_DONE, text, rec_id))
        self._conn.commit()

    def reset_pending(self, rec_id) -> None:
        self._conn.execute("UPDATE transcripts SET status=?, attempts=0 WHERE id=?",
                           (STATUS_PENDING, rec_id))
        self._conn.commit()

    def mark_failed(self, rec_id) -> None:
        self._conn.execute("UPDATE transcripts SET status=? WHERE id=?",
                           (STATUS_FAILED, rec_id))
        self._conn.commit()

    def bump_attempt(self, rec_id) -> int:
        self._conn.execute("UPDATE transcripts SET attempts=attempts+1 WHERE id=?",
                           (rec_id,))
        self._conn.commit()
        row = self._conn.execute("SELECT attempts FROM transcripts WHERE id=?",
                                 (rec_id,)).fetchone()
        return int(row["attempts"]) if row else 0

    def pending(self, max_attempts: int = 99) -> List[Record]:
        rows = self._conn.execute(
            "SELECT * FROM transcripts WHERE status=? AND attempts<? ORDER BY ts",
            (STATUS_PENDING, max_attempts)).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, limit: int = 50) -> List[Record]:
        rows = self._conn.execute(
            "SELECT * FROM transcripts ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, rec_id) -> Optional[Record]:
        row = self._conn.execute("SELECT * FROM transcripts WHERE id=?",
                                 (rec_id,)).fetchone()
        return self._row(row) if row else None

    def cleanup_audio(self, retention_days, now=None) -> int:
        now = now or time.time()
        cutoff = now - retention_days * 86400
        rows = self._conn.execute(
            "SELECT id, audio_path FROM transcripts WHERE audio_path IS NOT NULL"
            " AND ts < ?", (cutoff,)).fetchall()
        removed = 0
        for r in rows:
            p = r["audio_path"]
            if p and os.path.exists(p):
                os.remove(p)
            self._conn.execute("UPDATE transcripts SET audio_path=NULL WHERE id=?",
                               (r["id"],))
            removed += 1
        self._conn.commit()
        return removed

    @staticmethod
    def _row(r: sqlite3.Row) -> Record:
        keys = r.keys()
        return Record(
            id=r["id"], ts=r["ts"], text=r["text"], provider=r["provider"],
            model=r["model"], duration=r["duration"], audio_path=r["audio_path"],
            status=r["status"], attempts=r["attempts"] if "attempts" in keys else 0)

    def clear_audio(self, rec_id: int) -> None:
        row = self._conn.execute(
            "SELECT audio_path FROM transcripts WHERE id=?", (rec_id,)).fetchone()
        if row and row["audio_path"]:
            try:
                os.remove(row["audio_path"])
            except OSError:
                pass
        self._conn.execute("UPDATE transcripts SET audio_path=NULL WHERE id=?", (rec_id,))
        self._conn.commit()

    def clear_all(self) -> None:
        rows = self._conn.execute(
            "SELECT audio_path FROM transcripts WHERE audio_path IS NOT NULL").fetchall()
        for r in rows:
            p = r["audio_path"]
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        self._conn.execute("DELETE FROM transcripts")
        self._conn.commit()

    def audio_size_mb(self) -> float:
        """Total size of all stored audio files in MB."""
        rows = self._conn.execute(
            "SELECT audio_path FROM transcripts WHERE audio_path IS NOT NULL").fetchall()
        total = 0
        for r in rows:
            p = r["audio_path"]
            if p:
                try:
                    total += os.path.getsize(p)
                except OSError:
                    pass
        return total / (1024 * 1024)

    def evict_oldest(self, max_mb: float) -> int:
        """Delete oldest records (audio + row) until total audio is under max_mb.
        Returns number of records deleted. No-op when max_mb <= 0."""
        if max_mb <= 0:
            return 0
        deleted = 0
        while self.audio_size_mb() > max_mb:
            row = self._conn.execute(
                "SELECT id, audio_path FROM transcripts"
                " WHERE audio_path IS NOT NULL ORDER BY ts ASC LIMIT 1").fetchone()
            if row is None:
                break
            p = row["audio_path"]
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass
            self._conn.execute("DELETE FROM transcripts WHERE id=?", (row["id"],))
            self._conn.commit()
            deleted += 1
        return deleted

    def close(self) -> None:
        self._conn.close()
