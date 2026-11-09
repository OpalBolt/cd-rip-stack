import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "requeue", pathlib.Path(__file__).parent.parent / "scripts" / "requeue_missing_subs.py")
rq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rq)

T = "Movie (2000)"


def share(tmp_path):
    for d, files in {
        "lossless/movies": [f"{T}_t00.mkv"],
        "library/movies": [f"{T}.mkv", f"{T}.eng.srt", "validation.json"],
    }.items():
        p = tmp_path / d / T
        p.mkdir(parents=True)
        for f in files:
            (p / f).write_text(f)
    (tmp_path / "library/movies" / T / "extras").mkdir()
    return tmp_path


def want(*names):
    return lambda src, title, ff: {f"{T}.{n}.srt" for n in names}


def test_dry_run_changes_nothing(tmp_path):
    root = share(tmp_path)
    out = rq.requeue(T, str(root), "ff", False, want("eng", "dan"))
    assert "dan.srt" in out and "requeued" not in out
    assert (root / "lossless/movies" / T / f"{T}_t00.mkv").exists()
    assert (root / "library/movies" / T / f"{T}.mkv").exists()


def test_run_moves_source_and_clears_library(tmp_path):
    root = share(tmp_path)
    assert rq.requeue(T, str(root), "ff", True, want("eng", "dan")).endswith("requeued")
    rip = root / "rip/movies" / T
    assert (rip / f"{T}_t00.mkv").exists() and (rip / "validation.json").exists()
    assert not (root / "lossless/movies" / T).exists()
    lib = root / "library/movies" / T
    assert sorted(p.name for p in lib.iterdir()) == ["extras"]


def test_complete_title_is_left_alone(tmp_path):
    root = share(tmp_path)
    assert rq.requeue(T, str(root), "ff", True, want("eng")) == ""
    assert (root / "lossless/movies" / T).exists()


def test_skips_when_rip_already_staged(tmp_path):
    root = share(tmp_path)
    (root / "rip/movies" / T).mkdir(parents=True)
    (root / "rip/movies" / T / f"{T}_t00.mkv").write_text("x")
    assert "already holds" in rq.requeue(T, str(root), "ff", True, want("dan"))
    assert (root / "library/movies" / T / f"{T}.mkv").exists()


def test_only_danish_counts_as_missing(monkeypatch):
    from skive.subs import Track
    monkeypatch.setattr(rq.subs, "inventory", lambda *a, **k: [
        Track(0, "eng", "s", False), Track(1, "dan", "s", False), Track(2, "nor", "s", False)])
    assert rq.expected("src", T, "ff") == {f"{T}.dan.srt"}
