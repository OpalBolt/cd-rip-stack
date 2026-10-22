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
