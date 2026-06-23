import wave

from spokengo import audio


def _tone(n, amp=8000):
    import array, math
    a = array.array("h", [int(amp * math.sin(i / 5)) for i in range(n)])
    return a.tobytes()


def test_write_wav_is_valid(tmp_path):
    p = tmp_path / "a.wav"
    frames = _tone(1600)
    audio.write_wav(frames, 16000, str(p))
    with wave.open(str(p), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() == 1600


def test_buffer_preserves_order():
    b = audio.Buffer()
    b.add(b"\x01\x00"); b.add(b"\x02\x00"); b.add(b"\x03\x00")
    assert b.bytes() == b"\x01\x00\x02\x00\x03\x00"


def test_silence_detection():
    silence = b"\x00\x00" * 1000
    assert audio.is_silent(silence, threshold=120.0) is True
    assert audio.is_silent(_tone(1000), threshold=120.0) is False


def test_frames_to_seconds():
    one_sec = b"\x00\x00" * 16000  # 16000 int16 samples @ 16k = 1.0s
    assert abs(audio.frames_to_seconds(one_sec, 16000) - 1.0) < 1e-6


def test_autostop_on_limit():
    five_sec = b"\x00\x00" * (16000 * 5)
    assert audio.exceeds_limit(five_sec, 16000, max_seconds=3) is True
    assert audio.exceeds_limit(five_sec, 16000, max_seconds=10) is False
