"""The audio quota must bound what is actually on disk.

Shipped measuring only DB-referenced files, it reported 49.5 MB against a 50 MB
limit while the folder held 591 MB — 320 unreferenced files it could not see.
"""
from __future__ import annotations

import time

from spokengo.storage import Storage


def _wav(store: Storage, kb: int, name: str = None) -> str:
    """Write a file straight into the audio dir, bypassing the DB."""
    p = store.audio_dir / (name or f"rec_{int(time.time()*1e6)}.wav")
    p.write_bytes(b"\0" * (kb * 1024))
    return str(p)


def test_size_counts_unreferenced_files(tmp_path):
    s = Storage(tmp_path)
    _wav(s, 200, "orphan.wav")           # on disk, no row
    assert s.audio_size_mb() > 0.15, "quota must see files the DB does not know"


def test_purge_removes_orphans_but_keeps_referenced(tmp_path):
    s = Storage(tmp_path)
    kept = _wav(s, 50, "kept.wav")
    _wav(s, 50, "orphan.wav")
    s.add(text="x", provider="p", model="m", duration=1.0, audio_path=kept)
    old = time.time() + 10_000        # pretend the grace period has elapsed
    assert s.purge_orphans(now=old) == 1
    assert (s.audio_dir / "kept.wav").exists()
    assert not (s.audio_dir / "orphan.wav").exists()


def test_purge_spares_files_inside_the_grace_window(tmp_path):
    """save_audio() writes before the row exists — that window is not garbage."""
    s = Storage(tmp_path)
    s.save_audio(b"\0" * 4096)        # exactly the in-flight case
    assert s.purge_orphans() == 0
    assert len(s._audio_files()) == 1


def test_eviction_reclaims_orphans_before_deleting_history(tmp_path):
    s = Storage(tmp_path)
    kept = _wav(s, 300, "kept.wav")
    s.add(text="keep me", provider="p", model="m", duration=1.0, audio_path=kept)
    for i in range(6):
        _wav(s, 300, f"orphan{i}.wav")          # 1.8 MB of junk
    assert s.audio_size_mb() > 2.0

    s.purge_orphans(now=time.time() + 10_000)   # grace elapsed
    s.evict_oldest(1.0)

    assert s.get(1) is not None, "history must survive when junk explains the overage"
    assert s.audio_size_mb() < 1.0


def test_eviction_still_trims_real_history_when_needed(tmp_path):
    s = Storage(tmp_path)
    for i in range(5):
        p = _wav(s, 300, f"rec{i}.wav")
        s.add(text=f"t{i}", provider="p", model="m", duration=1.0,
              audio_path=p, ts=1000.0 + i)
    assert s.evict_oldest(0.9) >= 1
    assert s.audio_size_mb() <= 0.9
    left = [r.text for r in s.recent(50)]
    assert "t4" in left and "t0" not in left, "oldest goes first"
