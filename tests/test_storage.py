import time

from spokengo.storage import STATUS_DONE, STATUS_PENDING, Storage


def test_add_and_get(tmp_path):
    st = Storage(tmp_path)
    rid = st.add("hello", "groq", "whisper", 1.2)
    rec = st.get(rid)
    assert rec.text == "hello"
    assert rec.status == STATUS_DONE
    st.close()


def test_pending_queue_and_mark_done(tmp_path):
    st = Storage(tmp_path)
    rid = st.add("", "groq", "whisper", 2.0, status=STATUS_PENDING)
    pend = st.pending()
    assert len(pend) == 1 and pend[0].id == rid
    st.mark_done(rid, "recovered text")
    assert st.pending() == []
    assert st.get(rid).text == "recovered text"
    st.close()


def test_save_audio_writes_file(tmp_path):
    st = Storage(tmp_path)
    path = st.save_audio(b"RIFFfake")
    assert path.endswith(".wav")
    with open(path, "rb") as f:
        assert f.read() == b"RIFFfake"
    st.close()


def test_autocleanup_removes_old_audio(tmp_path):
    st = Storage(tmp_path)
    old_audio = st.save_audio(b"old")
    now = time.time()
    st.add("old", "groq", "w", 1.0, audio_path=old_audio,
           ts=now - 40 * 86400)
    st.add("new", "groq", "w", 1.0, audio_path=st.save_audio(b"new"), ts=now)
    removed = st.cleanup_audio(retention_days=30, now=now)
    assert removed == 1
    import os
    assert not os.path.exists(old_audio)  # old file gone
    st.close()
