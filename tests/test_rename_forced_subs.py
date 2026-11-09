import importlib.util
import pathlib

from skive.subs import Track

spec = importlib.util.spec_from_file_location(
    "rf", pathlib.Path(__file__).parent.parent / "scripts" / "rename_forced_subs.py")
rf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rf)

T = "M (2000)"


def share(tmp_path, *sidecars):
    (tmp_path / "lossless/movies" / T).mkdir(parents=True)
    (tmp_path / "lossless/movies" / T / f"{T}_t00.mkv").write_text("x")
    lib = tmp_path / "library/movies" / T
    lib.mkdir(parents=True)
    for s in sidecars:
        (lib / f"{T}.{s}.srt").write_text(s)
    return lib


def inv(*tracks):
    return lambda *_a, **_k: [Track(i, lang, "s", forced) for i, (lang, forced) in enumerate(tracks)]


def test_renames_when_source_has_no_forced_track(tmp_path):
    lib = share(tmp_path, "eng.forced", "dan")
    out = rf.rename_forced(T, str(tmp_path), "ff", True, inv(("eng", False), ("dan", False)))
    assert out == [f"{T}.eng.forced.srt -> {T}.eng.srt"]
    assert sorted(p.name for p in lib.iterdir()) == [f"{T}.dan.srt", f"{T}.eng.srt"]


def test_dry_run_and_genuine_forced_untouched(tmp_path):
    lib = share(tmp_path, "eng.forced")
    assert rf.rename_forced(T, str(tmp_path), "ff", False, inv(("eng", False)))
    assert (lib / f"{T}.eng.forced.srt").exists()
    assert rf.rename_forced(T, str(tmp_path), "ff", True, inv(("eng", False), ("eng", True))) == []
    assert (lib / f"{T}.eng.forced.srt").exists()


def cues(n):
    return "".join(f"{i}\n00:00:01,000 --> 00:00:02,000\nx\n\n" for i in range(n))


def test_existing_full_file_second_track_becomes_sdh(tmp_path):
    lib = share(tmp_path)
    (lib / f"{T}.eng.srt").write_text(cues(100))
    (lib / f"{T}.eng.forced.srt").write_text(cues(90))
    out = rf.rename_forced(T, str(tmp_path), "ff", True, inv(("eng", False), ("eng", False)))
    assert out == [f"{T}.eng.forced.srt -> {T}.eng.sdh.srt"]
    assert not (lib / f"{T}.eng.forced.srt").exists()


def test_never_clobbers_and_flags_doubtful_cases(tmp_path):
    lib = share(tmp_path)
    (lib / f"{T}.eng.srt").write_text(cues(100))
    (lib / f"{T}.eng.forced.srt").write_text(cues(10))  # below 30%
    out = rf.rename_forced(T, str(tmp_path), "ff", True, inv(("eng", False), ("eng", False)))
    assert out[0].startswith("skip") and "review" in out[0]
    (lib / f"{T}.eng.forced.srt").write_text(cues(90))
    out = rf.rename_forced(T, str(tmp_path), "ff", True, inv(("eng", False)))  # one track only
    assert out[0].startswith("skip")
    assert (lib / f"{T}.eng.forced.srt").exists() and (lib / f"{T}.eng.srt").exists()
