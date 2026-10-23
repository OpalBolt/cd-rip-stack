"""Tests for scripts/migrate_layout.py — synthetic old-layout trees on
tmp_path only (never a real mount), no tool dependencies.

Every case builds the old shape by hand, runs run_migration dry or applied,
and asserts on the printed plan lines plus what actually moved. The name
contracts are windows/transcode.py's, so the parse table pins that reuse.
"""

import importlib.util
import os
import pathlib

import pytest

_REPO = pathlib.Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "migrate_layout", _REPO / "scripts" / "migrate_layout.py")
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


def mkfile(path, content=b"mkv-bytes"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def run(root, apply=False):
    """run_migration wrapper; callers use capsys after it."""
    return migrate.run_migration(root, apply)


def lines(out):
    return out.splitlines()


# -- name contracts (reused from windows/transcode.py) -----------------------------
def test_parse_name_contracts():
    parse = migrate.parse_name
    assert parse("Movie (2001)_t01.mkv") == ("Movie (2001)", False)
    assert parse("Movie (2001)_t01.p2.mkv") == ("Movie (2001)", False)
    assert parse("Movie (2001)_e02.mkv") == ("Movie (2001)", False)  # legacy extra title
    assert parse("Show (2020)_S01E05.mkv") == ("Show (2020)", True)
    assert parse("Show (2020)_S01E05-E06.mkv") == ("Show (2020)", True)  # double
    assert parse("Show (2020) - Extra 03.mkv") == ("Show (2020)", True)
    assert parse("Show (2020) - Extra 03.p1.mkv") == ("Show (2020)", True)
    assert parse("B1_t00.mkv") == "junk label"
    assert parse("Some Movie_t01.mkv") == "failed rename (no title year)"
    assert parse("rip.log") == "unparseable"
    assert parse("Whatever.mkv") == "unparseable"


def test_parse_args_defaults():
    args = migrate.parse_args([])
    assert args.apply is False  # dry-run is the default
    assert args.root is None
    args = migrate.parse_args(["--apply", "--root", "/some/root"])
    assert args.apply is True and args.root == pathlib.Path("/some/root")


# -- dry run -----------------------------------------------------------------------
def test_dry_run_default_prints_plan_and_moves_nothing(tmp_path, capsys):
    lib_entry = mkfile(tmp_path / "library" / "Old Movie (1999)" / "film.mkv")
    done_file = mkfile(tmp_path / "rip-DONE" / "Movie (2001)_t01.mkv")
    flat = mkfile(tmp_path / "rip" / "movies" / "Movie (2002)_t00.mkv")

    rc = run(tmp_path)
    out = capsys.readouterr().out

    assert rc == 0
    assert f"would move {tmp_path}/library/Old Movie (1999) -> {tmp_path}/library/movies/Old Movie (1999)" in out
    assert f"would move {tmp_path}/rip-DONE -> {tmp_path}/lossless" in out
    assert (f"would move {tmp_path}/lossless/Movie (2001)_t01.mkv"
            f" -> {tmp_path}/lossless/movies/Movie (2001)/Movie (2001)_t01.mkv") in out
    assert (f"would move {tmp_path}/rip/movies/Movie (2002)_t00.mkv"
            f" -> {tmp_path}/rip/movies/Movie (2002)/Movie (2002)_t00.mkv") in out
    assert "dry-run: nothing changed (use --apply to execute)" in out
    assert "4 move(s)" in out
    # nothing moved, nothing created
    assert lib_entry.read_bytes() == b"mkv-bytes" and done_file.exists() and flat.exists()
    assert not (tmp_path / "lossless").exists()
    assert not (tmp_path / "library" / "movies").exists()
    assert not (tmp_path / "rip" / "movies" / "Movie (2002)").exists()


def test_bare_root_reports_nothing_to_do(tmp_path, capsys):
    rc = run(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert f"nothing to do under {tmp_path}" in out


def test_missing_root_is_an_error(tmp_path, capsys):
    rc = run(tmp_path / "nope")
    assert rc == 1
    assert "not a directory" in capsys.readouterr().err


# -- class 1: library --------------------------------------------------------------
def test_apply_library_root_entry_moves_into_movies(tmp_path, capsys):
    mkfile(tmp_path / "library" / "Old Movie (1999)" / "Old Movie (1999)_t01.mkv")
    stray = mkfile(tmp_path / "library" / "note.txt", b"notes")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert (tmp_path / "library" / "movies" / "Old Movie (1999)"
            / "Old Movie (1999)_t01.mkv").exists()
    assert not (tmp_path / "library" / "Old Movie (1999)").exists()
    assert stray.read_bytes() == b"notes"  # non-folder entry reported, not moved
    assert f"skip {tmp_path}/library/note.txt (not a folder -- left in place)" in out


def test_apply_tv_tree_renamed_to_tv(tmp_path, capsys):
    mkfile(tmp_path / "library" / "TV" / "Show (2010)" / "Season 01"
           / "Show (2010) - S01E01.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert (tmp_path / "library" / "tv" / "Show (2010)" / "Season 01"
            / "Show (2010) - S01E01.mkv").exists()  # whole tree rides the rename
    assert not (tmp_path / "library" / "TV").exists()
    assert f"move {tmp_path}/library/TV -> {tmp_path}/library/tv" in out


def test_apply_tv_merges_when_both_spellings_exist(tmp_path, capsys):
    mkfile(tmp_path / "library" / "TV" / "ShowA (2012)" / "a.mkv")
    mkfile(tmp_path / "library" / "tv" / "ShowB (2013)" / "b.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert (tmp_path / "library" / "tv" / "ShowA (2012)" / "a.mkv").exists()
    assert (tmp_path / "library" / "tv" / "ShowB (2013)" / "b.mkv").exists()
    assert not (tmp_path / "library" / "TV").exists()  # emptied and pruned
    assert f"prune {tmp_path}/library/TV" in out


def test_tv_tv_same_dir_is_a_noop(tmp_path, capsys, monkeypatch):
    # case-insensitive filesystem: TV and tv stat to the same directory
    show = mkfile(tmp_path / "library" / "TV" / "Show (2010)" / "ep.mkv")
    (tmp_path / "library" / "tv").mkdir(exist_ok=True)
    monkeypatch.setattr(migrate.os.path, "samefile", lambda a, b: True)

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert show.exists()  # untouched under either spelling
    assert (tmp_path / "library" / "TV" / "Show (2010)" / "ep.mkv").exists()
    assert "move " not in out and "would move" not in out
    assert "1 already-correct" in out


# -- class 2: rip-DONE -> lossless ---------------------------------------------------
def test_apply_rip_done_renamed_and_retreed(tmp_path, capsys):
    mkfile(tmp_path / "rip-DONE" / "Movie (2001)_t01.mkv")
    mkfile(tmp_path / "rip-DONE" / "Movie (2001)_t01.p2.mkv")
    mkfile(tmp_path / "rip-DONE" / "Show (2020)_S01E01.mkv")
    mkfile(tmp_path / "rip-DONE" / "Show (2020)_S01E01-E02.mkv")
    mkfile(tmp_path / "rip-DONE" / "Show (2020) - Extra 02.mkv")
    junk = mkfile(tmp_path / "rip-DONE" / "B1_t00.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert not (tmp_path / "rip-DONE").exists()
    lossless = tmp_path / "lossless"
    assert (lossless / "movies" / "Movie (2001)" / "Movie (2001)_t01.mkv").exists()
    assert (lossless / "movies" / "Movie (2001)" / "Movie (2001)_t01.p2.mkv").exists()
    assert (lossless / "tv" / "Show (2020)" / "Show (2020)_S01E01.mkv").exists()
    assert (lossless / "tv" / "Show (2020)" / "Show (2020)_S01E01-E02.mkv").exists()
    # a bare extra in the flat rip-DONE context is a guess the name cannot
    # support: carried to the lossless root, reported, never re-treed
    assert (lossless / "Show (2020) - Extra 02.mkv").exists()
    assert (f"skip {lossless}/Show (2020) - Extra 02.mkv"
            f" (ambiguous extra (no type dir: tv or movies?) -- left in place)") in out
    # junk rides the rename but is reported, never re-treed
    assert not junk.exists()
    assert (lossless / "B1_t00.mkv").exists()
    assert f"skip {lossless}/B1_t00.mkv (junk label -- left in place)" in out


def test_apply_rip_done_merges_into_existing_lossless(tmp_path):
    mkfile(tmp_path / "lossless" / "movies" / "Old (2000)" / "Old (2000)_t01.mkv")
    mkfile(tmp_path / "rip-DONE" / "Movie (2005)_t01.mkv")

    rc = run(tmp_path, apply=True)

    assert rc == 0
    assert (tmp_path / "lossless" / "movies" / "Old (2000)" / "Old (2000)_t01.mkv").exists()
    assert (tmp_path / "lossless" / "movies" / "Movie (2005)" / "Movie (2005)_t01.mkv").exists()
    assert not (tmp_path / "rip-DONE").exists()


def test_rip_done_collision_keeps_source_and_folder(tmp_path, capsys):
    kept = mkfile(tmp_path / "lossless" / "movies" / "Movie (2001)"
                  / "Movie (2001)_t01.mkv", b"kept")
    stray = mkfile(tmp_path / "rip-DONE" / "Movie (2001)_t01.mkv", b"stray")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert kept.read_bytes() == b"kept"  # never overwritten
    assert stray.read_bytes() == b"stray" and stray.exists()  # reported, left in rip-DONE
    assert f"skip {tmp_path}/rip-DONE/Movie (2001)_t01.mkv (target exists -- left in place)" in out
    assert (tmp_path / "rip-DONE").exists()  # not pruned while it holds the stray


def test_rip_done_unparsed_straggler_blocks_prune_and_is_reported(tmp_path, capsys):
    log = mkfile(tmp_path / "rip-DONE" / "rip.log", b"old text log")
    mkfile(tmp_path / "rip-DONE" / "Movie (2001)_t01.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert not log.exists()  # carried by the rename, not re-treed
    assert f"skip {tmp_path}/lossless/rip.log (old text rip.log -- left unread -- left in place)" in out
    assert (tmp_path / "lossless" / "rip.log").read_bytes() == b"old text log"
    assert (tmp_path / "rip-DONE").exists() is False  # rename carried everything


# -- class 3: staging leftovers ------------------------------------------------------
def test_apply_flat_staging_leftover_moves_into_title_folder(tmp_path):
    flat = mkfile(tmp_path / "rip" / "movies" / "Movie (2002)_t00.mkv")

    rc = run(tmp_path, apply=True)

    assert rc == 0
    assert (tmp_path / "rip" / "movies" / "Movie (2002)" / "Movie (2002)_t00.mkv").exists()
    assert not flat.exists()


def test_apply_old_disc_dir_leftover_moves_and_prunes(tmp_path, capsys):
    mkfile(tmp_path / "rip" / "tv" / "d1" / "Show (2021)_S02E05.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert (tmp_path / "rip" / "tv" / "Show (2021)" / "Show (2021)_S02E05.mkv").exists()
    assert not (tmp_path / "rip" / "tv" / "d1").exists()
    assert f"prune {tmp_path}/rip/tv/d1" in out


def test_unparseable_and_junk_leftovers_reported_and_kept(tmp_path, capsys):
    weird = mkfile(tmp_path / "rip" / "movies" / "Weird Name.mkv")
    junk = mkfile(tmp_path / "rip" / "movies" / "B2_t03.mkv")
    yearless = mkfile(tmp_path / "rip" / "movies" / "Some Movie_t01.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert weird.exists() and junk.exists() and yearless.exists()
    assert f"skip {weird} (unparseable -- left in place)" in out
    assert f"skip {junk} (junk label -- left in place)" in out
    assert f"skip {yearless} (failed rename (no title year) -- left in place)" in out


def test_staging_collision_skips_leftover(tmp_path, capsys):
    kept = mkfile(tmp_path / "rip" / "movies" / "Movie (2004)"
                  / "Movie (2004)_t01.mkv", b"kept")
    flat = mkfile(tmp_path / "rip" / "movies" / "Movie (2004)_t01.mkv", b"flat")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert kept.read_bytes() == b"kept" and flat.read_bytes() == b"flat"
    assert f"skip {flat} (target exists -- left in place)" in out


def test_unexpected_shapes_reported_and_kept(tmp_path, capsys):
    stray_dir = mkfile(tmp_path / "rip" / "movies" / "Random Dir" / "file.mkv")
    deep = mkfile(tmp_path / "rip" / "movies" / "d1" / "sub" / "file.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert stray_dir.exists() and deep.parent.exists()
    assert f"skip {tmp_path}/rip/movies/Random Dir (unexpected shape -- left in place)" in out
    assert f"skip {tmp_path}/rip/movies/d1/sub (unexpected shape -- left in place)" in out


def test_already_correct_files_are_counted_not_moved(tmp_path, capsys):
    promoted = mkfile(tmp_path / "rip" / "movies" / "Movie (2002)" / "Movie (2002)_t00.mkv")
    indisc = mkfile(tmp_path / "rip" / "tv" / "Show (2021)" / "d1" / "Show (2021)_S02E05.mkv")
    midrip = mkfile(tmp_path / "rip" / "tv" / "Show (2021)" / "d1" / "B1_t00.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert promoted.exists() and indisc.exists() and midrip.exists()
    assert "0 move(s)" in out and "2 already-correct" in out
    assert f"skip {midrip} (junk label -- left in place)" in out  # junk never counts as content


# -- rip.log contract -----------------------------------------------------------------
def test_old_rip_log_left_in_place_and_unread(tmp_path, capsys):
    root_log = mkfile(tmp_path / "rip.log", b"2026-08-28  -  skive rip movie")
    rip_log = mkfile(tmp_path / "rip" / "movies" / "rip.log", b"more old text")
    mkfile(tmp_path / "rip" / "movies" / "Movie (2002)_t00.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert root_log.read_bytes() == b"2026-08-28  -  skive rip movie"
    assert rip_log.read_bytes() == b"more old text"
    assert f"skip {root_log} (old text rip.log -- left unread -- left in place)" in out
    assert f"skip {rip_log} (old text rip.log -- left unread -- left in place)" in out


# -- empty-dir pruning ----------------------------------------------------------------
def test_dry_run_lists_would_prune_dirs(tmp_path, capsys):
    (tmp_path / "rip" / "movies" / "d2").mkdir(parents=True)
    (tmp_path / "rip" / "tv" / "Dead (1999)").mkdir(parents=True)
    (tmp_path / "rip" / "tv" / "d3" / "nested").mkdir(parents=True)
    kept = mkfile(tmp_path / "rip" / "tv" / "Alive (1998)" / "x.mkv")

    rc = run(tmp_path)
    out = capsys.readouterr().out

    assert rc == 0
    assert f"would prune {tmp_path}/rip/movies/d2" in out
    assert f"would prune {tmp_path}/rip/tv/Dead (1999)" in out
    assert f"would prune {tmp_path}/rip/tv/d3/nested" in out
    assert f"would prune {tmp_path}/rip/tv/d3" in out
    assert kept.exists()


def test_apply_prunes_empty_dirs_but_never_managed_roots(tmp_path):
    (tmp_path / "rip" / "movies" / "d2").mkdir(parents=True)
    (tmp_path / "rip" / "tv" / "Dead (1999)").mkdir(parents=True)
    (tmp_path / "rip" / "tv" / "d3" / "nested").mkdir(parents=True)
    (tmp_path / "lossless").mkdir()
    (tmp_path / "library").mkdir()

    rc = run(tmp_path, apply=True)

    assert rc == 0
    assert not (tmp_path / "rip" / "movies" / "d2").exists()
    assert not (tmp_path / "rip" / "tv" / "Dead (1999)").exists()
    assert not (tmp_path / "rip" / "tv" / "d3").exists()
    # the managed roots and their structural children survive
    for kept in (tmp_path / "rip", tmp_path / "rip" / "movies", tmp_path / "rip" / "tv",
                 tmp_path / "lossless", tmp_path / "library"):
        assert kept.exists()


def test_prune_skipped_when_files_remain(tmp_path):
    (tmp_path / "rip" / "movies" / "d2").mkdir(parents=True)
    mkfile(tmp_path / "rip" / "movies" / "d2" / "B2_t03.mkv")  # junk stays -> not empty

    rc = run(tmp_path, apply=True)

    assert rc == 0
    assert (tmp_path / "rip" / "movies" / "d2" / "B2_t03.mkv").exists()


# -- idempotence -----------------------------------------------------------------------
def test_second_apply_finds_nothing_to_do(tmp_path, capsys):
    mkfile(tmp_path / "library" / "Old Movie (1999)" / "film.mkv")
    mkfile(tmp_path / "library" / "TV" / "Show (2010)" / "Season 01" / "ep.mkv")
    mkfile(tmp_path / "rip-DONE" / "Movie (2001)_t01.mkv")
    mkfile(tmp_path / "rip" / "movies" / "Movie (2002)_t00.mkv")
    mkfile(tmp_path / "rip" / "tv" / "d1" / "Show (2021)_S02E05.mkv")

    assert run(tmp_path, apply=True) == 0
    capsys.readouterr()
    rc = run(tmp_path, apply=True)  # re-run on the migrated tree
    out = capsys.readouterr().out

    assert rc == 0
    assert "\nmove " not in out and "prune " not in out
    assert "0 move(s), 0 skipped," in out
    assert (tmp_path / "lossless" / "movies" / "Movie (2001)" / "Movie (2001)_t01.mkv").exists()
    assert (tmp_path / "rip" / "tv" / "Show (2021)" / "Show (2021)_S02E05.mkv").exists()


def test_dry_run_then_apply_matches_apply_directly(tmp_path, capsys):
    mkfile(tmp_path / "rip-DONE" / "Show (2020)_S01E01.mkv")
    dry_lines = (run(tmp_path), capsys.readouterr().out)[1]
    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out
    assert rc == 0
    for line in lines(dry_lines):
        if line.startswith("would move"):
            assert line.removeprefix("would ") in out  # the plan is what executed


# -- flat-context extras are ambiguous, typed extras are positional -----------------
def test_flat_context_extra_reported_ambiguous_and_kept(tmp_path, capsys):
    # merge mode: rip-DONE child and lossless-root straggler are both flat --
    # movie extras and TV extras share the name, no type dir positions them
    mkfile(tmp_path / "lossless" / "movies" / "Old (2000)" / "Old (2000)_t01.mkv")
    done_extra = mkfile(tmp_path / "rip-DONE" / "Movie (2001) - Extra 02.mkv")
    root_extra = mkfile(tmp_path / "lossless" / "Show (2020) - Extra 03.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert done_extra.exists() and root_extra.exists()  # reported, never re-treed
    assert (tmp_path / "rip-DONE").exists()  # the kept extra keeps rip-DONE alive
    assert (f"skip {done_extra} (ambiguous extra (no type dir: tv or movies?)"
            f" -- left in place)") in out
    assert (f"skip {root_extra} (ambiguous extra (no type dir: tv or movies?)"
            f" -- left in place)") in out


def test_typed_context_extra_keeps_positional_type(tmp_path, capsys):
    extra = mkfile(tmp_path / "rip" / "tv" / "Show (2020) - Extra 02.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert (tmp_path / "rip" / "tv" / "Show (2020)"
            / "Show (2020) - Extra 02.mkv").exists()  # type dir owns the type
    assert not extra.exists()
    assert "ambiguous" not in out


# -- symlinks are never followed out of the root -------------------------------------
def symlink_case(tmp_path):
    """A managed root under tmp_path/'videos' plus an outside dir to guard."""
    root, outside = tmp_path / "videos", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"keep")
    return root, outside


def snapshot(outside):
    """Everything visible outside the root: (name, bytes) per entry."""
    return sorted((p.name, p.stat().st_size) for p in outside.iterdir())


def assert_untouched(out, outside, before):
    """Nothing outside the root appeared, vanished, or changed; and the plan
    executed no moves or prunes at all."""
    assert snapshot(outside) == before
    assert "would move" not in out and "\nmove " not in out
    assert "would prune" not in out and "\nprune " not in out


def test_symlinked_lossless_root_is_skipped_never_followed(tmp_path, capsys):
    root, outside = symlink_case(tmp_path)
    mkfile(root / "rip-DONE" / "Movie (2001)_t01.mkv")
    (root / "lossless").symlink_to(outside, target_is_directory=True)

    for apply in (False, True):
        before = snapshot(outside)
        rc = run(root, apply=apply)
        out = capsys.readouterr().out
        assert rc == 0
        assert_untouched(out, outside, before)
        # the managed root itself and its would-be move sources are refused
        assert f"skip {root}/lossless (path traverses a symlink -- left in place)" in out
        assert (f"skip {root}/rip-DONE/Movie (2001)_t01.mkv"
                f" (path traverses a symlink -- left in place)") in out
        assert (root / "rip-DONE" / "Movie (2001)_t01.mkv").exists()
        assert (root / "lossless").is_symlink()


def test_symlinked_rip_done_is_skipped_never_followed(tmp_path, capsys):
    root, outside = symlink_case(tmp_path)
    mkfile(outside / "Movie (2001)_t01.mkv")
    (root / "rip-DONE").symlink_to(outside, target_is_directory=True)

    for apply in (False, True):
        before = snapshot(outside)
        rc = run(root, apply=apply)
        out = capsys.readouterr().out
        assert rc == 0
        assert_untouched(out, outside, before)
        assert f"skip {root}/rip-DONE (path traverses a symlink -- left in place)" in out
        assert not (root / "lossless").exists()  # the carry was refused
        assert (root / "rip-DONE").is_symlink()


def test_symlinked_rip_type_dir_is_skipped_never_followed(tmp_path, capsys):
    root, outside = symlink_case(tmp_path)
    (root / "rip").mkdir()
    mkfile(outside / "Movie (2002)_t00.mkv")
    (root / "rip" / "movies").symlink_to(outside, target_is_directory=True)

    for apply in (False, True):
        before = snapshot(outside)
        rc = run(root, apply=apply)
        out = capsys.readouterr().out
        assert rc == 0
        assert_untouched(out, outside, before)
        assert (f"skip {root}/rip/movies/Movie (2002)_t00.mkv"
                f" (path traverses a symlink -- left in place)") in out
        assert (root / "rip" / "movies").is_symlink()


def test_symlinked_title_dir_inside_lossless_is_skipped_never_followed(tmp_path, capsys):
    root, outside = symlink_case(tmp_path)
    (root / "lossless" / "tv").mkdir(parents=True)
    (root / "lossless" / "tv" / "Show (2020)").symlink_to(outside,
                                                          target_is_directory=True)
    mkfile(root / "rip-DONE" / "Show (2020)_S01E01.mkv")

    for apply in (False, True):
        before = snapshot(outside)
        rc = run(root, apply=apply)
        out = capsys.readouterr().out
        assert rc == 0
        assert_untouched(out, outside, before)
        assert (f"skip {root}/rip-DONE/Show (2020)_S01E01.mkv"
                f" (path traverses a symlink -- left in place)") in out
        assert (root / "rip-DONE" / "Show (2020)_S01E01.mkv").exists()
        assert (root / "lossless" / "tv" / "Show (2020)").is_symlink()


def test_symlink_report_names_the_hazard_not_a_collision_through_it(tmp_path, capsys):
    # overwrite bait: the symlinked title dir already contains the colliding
    # name; the skip must cite the symlink, not a "target exists" through it
    root, outside = symlink_case(tmp_path)
    (root / "lossless" / "tv").mkdir(parents=True)
    (root / "lossless" / "tv" / "Show (2020)").symlink_to(outside,
                                                          target_is_directory=True)
    mkfile(outside / "Show (2020)_S01E01.mkv", b"bait")
    mkfile(root / "rip-DONE" / "Show (2020)_S01E01.mkv", b"stray")

    for apply in (False, True):
        before = snapshot(outside)
        rc = run(root, apply=apply)
        out = capsys.readouterr().out
        assert rc == 0
        assert_untouched(out, outside, before)
        assert (outside / "Show (2020)_S01E01.mkv").read_bytes() == b"bait"
        assert (f"skip {root}/rip-DONE/Show (2020)_S01E01.mkv"
                f" (path traverses a symlink -- left in place)") in out
        assert "target exists" not in out


# -- apply-time robustness ------------------------------------------------------------
def test_failed_move_leaves_no_scaffold(tmp_path, capsys, monkeypatch):
    def boom(self, target):
        raise OSError("nope")

    monkeypatch.setattr(pathlib.Path, "rename", boom)
    flat1 = mkfile(tmp_path / "rip" / "movies" / "Movie (2002)_t00.mkv")
    flat2 = mkfile(tmp_path / "rip" / "movies" / "Movie (2002)_t01.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert flat1.exists() and flat2.exists()  # reported, left in place
    assert not (tmp_path / "rip" / "movies" / "Movie (2002)").exists()
    assert out.count("move failed: nope") == 2
    assert f"skip {flat1} (move failed: nope -- left in place)" in out


def test_failed_carry_reports_dependents_at_real_location(tmp_path, capsys,
                                                          monkeypatch):
    def boom(self, target):
        raise OSError("nope")

    monkeypatch.setattr(pathlib.Path, "rename", boom)
    log = mkfile(tmp_path / "rip-DONE" / "rip.log", b"old text log")
    flat = mkfile(tmp_path / "rip-DONE" / "Movie (2001)_t01.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert log.exists() and flat.exists()  # the carry failed, so nothing moved
    assert not (tmp_path / "lossless").exists()  # its scaffold was un-built too
    # the plan's virtual lossless/ citations are re-pointed at rip-DONE
    assert (f"skip {log} (old text rip.log -- left unread -- left in place)") in out
    assert f"skip {flat} (move failed: nope -- left in place)" in out
    assert f"skip {tmp_path}/rip-DONE (move failed: nope -- left in place)" in out
    assert "lossless" not in out  # no virtual in-root citation survives


# -- --mock: rehearse the apply on a structure-only mirror ---------------------------
def mock_source(root):
    """The rehearsal fixture: managed roots around a migration-worthy tree --
    library root movie (with a nested logs/ dir), a library-level symlink to
    rip-DONE, library/TV show, rip-DONE movie + flat " - Extra", rip/movies
    flat + junk + old d1, a top-level logs/ dir with an empty subdir -- plus
    unrelated top-level entries (music-backup/, empty-photos/) that the mock
    must never mirror (only the managed roots are walked)."""
    mkfile(root / "library" / "Old Movie (1999)" / "Old Movie (1999)_t01.mkv")
    mkfile(root / "library" / "Old Movie (1999)" / "logs" / "2024" / "encode.log")
    (root / "library" / "link-to-rip-DONE").symlink_to("../rip-DONE")
    mkfile(root / "library" / "TV" / "Show (2010)" / "Season 01"
           / "Show (2010) - S01E01.mkv")
    mkfile(root / "rip-DONE" / "Movie (2001)_t01.mkv")
    mkfile(root / "rip-DONE" / "Show (2020) - Extra 02.mkv")
    mkfile(root / "rip" / "movies" / "Movie (2002)_t00.mkv")
    mkfile(root / "rip" / "movies" / "B2_t03.mkv")
    mkfile(root / "rip" / "tv" / "d1" / "Show (2021)_S02E05.mkv")
    mkfile(root / "logs" / "mount.log")
    (root / "logs" / "2024").mkdir()  # empty dir inside a managed root
    mkfile(root / "music-backup" / "old-mix.flac")  # unrelated: never mirrored
    (root / "empty-photos").mkdir()  # unrelated: never mirrored
    return root


def tree_snapshot(root):
    """(relative path, kind, payload) for every entry below root, sorted;
    kind is dir / file (payload bytes) / symlink (payload target string).
    os.walk with followlinks=False, so a symlinked dir is one entry."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dp = pathlib.Path(dirpath)
        for name in dirnames + filenames:
            p = dp / name
            rel = str(p.relative_to(root))
            if p.is_symlink():
                entries.append((rel, "symlink", os.readlink(p)))
            elif p.is_dir():
                entries.append((rel, "dir", ""))
            else:
                entries.append((rel, "file", p.read_bytes()))
    return sorted(entries)


def test_parse_args_mock_defaults():
    args = migrate.parse_args(["--mock"])
    assert args.mock == pathlib.Path("/tmp/migrate-layout-mock")
    args = migrate.parse_args(["--mock", "/tmp/elsewhere"])
    assert args.mock == pathlib.Path("/tmp/elsewhere")
    assert migrate.parse_args([]).mock is None
    # --apply beside --mock is accepted and redundant, not an error
    args = migrate.parse_args(["--mock", "--apply"])
    assert args.mock == pathlib.Path("/tmp/migrate-layout-mock")
    assert args.apply is True


def test_mock_mirrors_structure_and_applies_inside_it(tmp_path, capsys):
    source = mock_source(tmp_path / "videos")
    mirror = tmp_path / "mock"
    before = tree_snapshot(source)

    rc = migrate.run_mock(source, mirror)
    out = capsys.readouterr().out

    assert rc == 0
    # the source was only ever read: identical names, kinds, bytes, targets
    assert tree_snapshot(source) == before
    # the mirror line (with the mirrored counts) and the closing hint
    assert (f"mock tree at {mirror} (structure only, empty files): "
            "14 dir(s), 9 file(s), 1 symlink(s)") in out
    assert f"inspect with: find {mirror} -type d | sort" in out
    # --mock implies apply: normal plan/apply lines and summary, no dry-run
    assert f"move {mirror}/rip-DONE -> {mirror}/lossless" in out
    assert f"migrate report -- {mirror}" in out
    assert "dry-run" not in out
    # migrated layout inside the mirror -- and every file zero bytes
    for zero in ("library/movies/Old Movie (1999)/Old Movie (1999)_t01.mkv",
                 "library/movies/Old Movie (1999)/logs/2024/encode.log",
                 "library/tv/Show (2010)/Season 01/Show (2010) - S01E01.mkv",
                 "lossless/movies/Movie (2001)/Movie (2001)_t01.mkv",
                 "lossless/Show (2020) - Extra 02.mkv",
                 "rip/movies/Movie (2002)/Movie (2002)_t00.mkv",
                 "rip/movies/B2_t03.mkv",
                 "rip/tv/Show (2021)/Show (2021)_S02E05.mkv"):
        f = mirror / zero
        assert f.is_file() and f.stat().st_size == 0, zero
    # the old locations are gone inside the mirror
    for gone in ("rip-DONE", "library/TV", "library/Old Movie (1999)",
                 "rip/tv/d1"):
        assert not (mirror / gone).exists(), gone
    # only the managed roots were walked: unrelated top-level entries did not
    # cross over
    assert not (mirror / "music-backup").exists()
    assert not (mirror / "empty-photos").exists()
    # a managed root the migration never touches is mirrored too, empty dirs
    # and empty files included -- and survives the apply untouched
    assert (mirror / "logs" / "mount.log").is_file()
    assert (mirror / "logs" / "mount.log").stat().st_size == 0
    assert (mirror / "logs" / "2024").is_dir()
    # symlinks inside a managed root are mirrored (dangling after rip-DONE's
    # rename) and the rehearsed apply refuses to move through them
    assert (mirror / "library" / "link-to-rip-DONE").is_symlink()
    assert os.readlink(mirror / "library" / "link-to-rip-DONE") == "../rip-DONE"
    assert (f"skip {mirror}/library/link-to-rip-DONE"
            " (path traverses a symlink -- left in place)") in out


def test_mock_tree_mirrors_only_the_managed_roots(tmp_path):
    """_build_mock_tree walks the managed roots only, never the source root:
    every managed root that exists is mirrored (structure, empty files,
    symlinks -- and empty ones included), while unrelated top-level entries
    (music-backup/, stray files) are not; a symlinked root is mirrored as
    the link alone and never descended into."""
    source = tmp_path / "videos"
    mkfile(source / "library" / "Old (1999)" / "Old (1999)_t01.mkv", b"media")
    mkfile(source / "rip" / "movies" / "Movie (2002)_t00.mkv", b"media")
    mkfile(source / "rip-DONE" / "Movie (2001)_t01.mkv", b"media")
    (source / "rip-DONE" / "link-to-lossless").symlink_to("lossless")
    mkfile(source / "lossless" / "tv" / "Show (2020)"
           / "Show (2020) - S01E01.mkv", b"media")
    (source / "issues").mkdir()  # an empty managed root still gets mirrored
    (source / "logs-real" / "2024").mkdir(parents=True)  # unrelated target
    mkfile(source / "logs-real" / "2024" / "real.log", b"media")
    (source / "logs").symlink_to("logs-real")  # symlinked root: link alone
    mkfile(source / "music-backup" / "old-mix.flac", b"media")  # unrelated
    mkfile(source / "stray.txt", b"media")  # unrelated top-level file
    (source / "empty-photos").mkdir()  # unrelated empty dir
    before = tree_snapshot(source)

    mirror = tmp_path / "mock"
    counts = migrate._build_mock_tree(source, mirror)

    assert counts == (9, 4, 2)
    # exactly the managed roots that exist -- nothing else at the top level
    assert sorted(p.name for p in mirror.iterdir()) == sorted(
        ("library", "rip", "rip-DONE", "lossless", "issues", "logs"))
    # structure, empty files, symlinks inside them
    assert (mirror / "library" / "Old (1999)"
            / "Old (1999)_t01.mkv").stat().st_size == 0
    assert (mirror / "rip" / "movies").is_dir()  # empty dir mirrored
    assert (mirror / "rip-DONE" / "link-to-lossless").is_symlink()
    assert os.readlink(mirror / "rip-DONE" / "link-to-lossless") == "lossless"
    assert (mirror / "lossless" / "tv" / "Show (2020)"
            / "Show (2020) - S01E01.mkv").stat().st_size == 0
    # an empty managed root exists in the mirror, empty
    assert (mirror / "issues").is_dir()
    assert not any((mirror / "issues").iterdir())
    # a symlinked root: the link alone -- its target is not walked into
    assert (mirror / "logs").is_symlink()
    assert os.readlink(mirror / "logs") == "logs-real"
    assert not (mirror / "logs" / "2024").exists()
    # unrelated top-level entries never cross over
    for absent in ("music-backup", "stray.txt", "empty-photos", "logs-real"):
        assert not (mirror / absent).exists(), absent
    assert tree_snapshot(source) == before  # the source was only ever read


def test_mock_accepts_existing_empty_dir(tmp_path, capsys):
    source = mock_source(tmp_path / "videos")
    mirror = tmp_path / "mock"
    mirror.mkdir()  # empty: allowed, per the simplest safe rule

    rc = migrate.run_mock(source, mirror)
    out = capsys.readouterr().out

    assert rc == 0
    assert (mirror / "lossless" / "movies" / "Movie (2001)"
            / "Movie (2001)_t01.mkv").stat().st_size == 0
    assert "mock tree at" in out


def test_mock_refuses_existing_non_empty_path(tmp_path, capsys):
    source = mock_source(tmp_path / "videos")
    mirror = tmp_path / "mock"
    mirror.mkdir()
    keep = mkfile(mirror / "keep.txt", b"keep")
    before = tree_snapshot(source)

    rc = migrate.run_mock(source, mirror)
    captured = capsys.readouterr()

    assert rc == 1
    assert (f"{mirror} already exists and is not empty (remove it first)"
            in captured.err)
    assert keep.read_bytes() == b"keep"  # nothing touched, nothing built
    assert captured.out == ""  # no mock line, no migration output at all
    assert tree_snapshot(source) == before


# -- strays that were silently unreported ---------------------------------------------
def test_stray_dir_at_lossless_root_reported_and_kept(tmp_path, capsys):
    mkfile(tmp_path / "lossless" / "movies" / "Old (2000)" / "Old (2000)_t01.mkv")
    stray = mkfile(tmp_path / "lossless" / "Random Stuff" / "file.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert stray.exists()
    assert (f"skip {tmp_path}/lossless/Random Stuff"
            f" (unexpected shape -- left in place)") in out
    assert f"skip {tmp_path}/lossless/movies" not in out  # type dirs are structural


def test_stray_file_in_tv_merge_reported_and_keeps_tv_alive(tmp_path, capsys):
    mkfile(tmp_path / "library" / "TV" / "Show (2010)"
           / "Show (2010) - S01E01.mkv")
    stray = mkfile(tmp_path / "library" / "TV" / "rip.log", b"old text log")
    mkfile(tmp_path / "library" / "tv" / "ShowB (2013)" / "b.mkv")

    rc = run(tmp_path, apply=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert (tmp_path / "library" / "tv" / "Show (2010)"
            / "Show (2010) - S01E01.mkv").exists()  # the tree merges
    assert stray.read_bytes() == b"old text log"  # reported, not carried into tv/
    assert f"skip {stray} (unexpected shape -- left in place)" in out
    assert (tmp_path / "library" / "TV").exists()  # the stray keeps TV/ alive
    assert f"prune {tmp_path}/library/TV" not in out
