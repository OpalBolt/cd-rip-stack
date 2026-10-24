"""Tests for scripts/backfill_years.py — synthetic trees on tmp_path only,
TMDB replaced by a scripted picker (no network, no TTY).

Every case builds staged leftovers by hand, runs run_backfill with a fake
picker, and asserts on the printed plan plus what actually got renamed.
The name contracts are windows/transcode.py's, reused like migrate_layout's.
"""

import importlib.util
import pathlib
import sys

_REPO = pathlib.Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "backfill_years", _REPO / "scripts" / "backfill_years.py")
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)

from skive.tmdb import TMDBPick


def mkfile(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mkv")
    return path


def picker_for(catalog):
    """A search_loop stand-in keyed by prefill (the cleaned title)."""
    def picker(kind, prefill=""):
        return catalog[prefill]
    return picker


def pick(title, year, id_="1"):
    return TMDBPick(title=title, year=year, original_language="en",
                    type="movie", id=id_)


# -- parse: what counts as a failed rename -------------------------------
def test_parse_yearless_contracts():
    parse = backfill.parse_yearless
    assert parse("Some Movie_t00.mkv") == ("Some Movie", "_t00")
    assert parse("Title-B1_t02.mkv") == ("Title", "_t02")  # glued tag stripped
    assert parse("Movie (2001)_t00.mkv") is None  # conforming
    assert parse("B1_t00.mkv") is None  # junk label: mid-rip, not ours
    assert parse("Whatever.mkv") is None
    assert parse("Show_S01E05.mkv") == ("Show", "_S01E05")
    assert parse("Show_S01E05-E06.mkv") == ("Show", "_S01E05-E06")
    assert parse("Show - Extra 03.mkv") == ("Show", " - Extra 03")
    assert parse("Movie_e02.mkv") == ("Movie", "_e02")  # legacy extra title
    assert parse("Movie (2001)_t00.p2.mkv") is None


def test_glued_tag_needs_the_disc_shape():
    # "Kingsman- The Golden Circle": the hyphen is title text, not a tag
    assert backfill.parse_yearless("Kingsman- The Golden Circle_t00.mkv") == \
        ("Kingsman- The Golden Circle", "_t00")


def test_missing_root_is_an_error(tmp_path, capsys):
    assert backfill.run_backfill(tmp_path / "nope", False) == 1
    assert "is not a directory" in capsys.readouterr().err


def test_nothing_to_backfill(tmp_path, capsys):
    (tmp_path / "rip" / "movies").mkdir(parents=True)
    assert backfill.run_backfill(tmp_path, False,
                                 picker_for({})) == 0
    assert "nothing to backfill" in capsys.readouterr().out


def test_dry_run_prints_plan_and_renames_nothing(tmp_path, capsys):
    flat = mkfile(tmp_path / "rip" / "movies" / "The Favourite_t00.mkv")
    tagged = mkfile(tmp_path / "rip" / "movies" / "Murder-B1_t02.mkv")
    rc = backfill.run_backfill(tmp_path, False, picker_for({
        "The Favourite": pick("The Favourite", "2018"),
        "Murder": pick("Murder on the Orient Express", "2017"),
    }))
    out = capsys.readouterr().out
    assert rc == 0
    assert f"would rename {flat} -> " in out
    assert "The Favourite (2018)_t00.mkv" in out
    assert "Murder on the Orient Express (2017)_t02.mkv" in out
    assert "dry-run: nothing changed" in out
    assert flat.exists() and tagged.exists()


def test_apply_renames_in_place_canonically(tmp_path, capsys):
    flat = mkfile(tmp_path / "rip" / "movies" / "Kingsman- The Golden Circle_t00.mkv")
    disc = mkfile(tmp_path / "rip" / "movies" / "d2" / "Cloudy-B1_t14.mkv")
    rc = backfill.run_backfill(tmp_path, True, picker_for({
        "Kingsman- The Golden Circle": pick("Kingsman: The Golden Circle", "2017"),
        "Cloudy": pick("Cloudy with a Chance of Meatballs", "2009"),
    }))
    out = capsys.readouterr().out
    assert rc == 0
    # colon sanitized, glued tag gone, _tNN kept, d{disc} file renamed in place
    assert (tmp_path / "rip" / "movies" /
            "Kingsman The Golden Circle (2017)_t00.mkv").exists()
    assert (tmp_path / "rip" / "movies" / "d2" /
            "Cloudy with a Chance of Meatballs (2009)_t14.mkv").exists()
    assert not flat.exists() and not disc.exists()
    assert "re-run migrate_layout.py" in out


def test_two_groups_one_pick_become_parts(tmp_path, capsys):
    g1 = mkfile(tmp_path / "rip" / "movies" / "Besttelsen DVD 1-B1_t00.mkv")
    g2 = mkfile(tmp_path / "rip" / "movies" / "Besttelsen DVD 2-B1_t00.mkv")
    same = pick("The Legacy", "2014")
    rc = backfill.run_backfill(tmp_path, True, picker_for({
        "Besttelsen DVD 1": same, "Besttelsen DVD 2": same,
    }))
    assert rc == 0
    p1 = tmp_path / "rip" / "movies" / "The Legacy (2014)_t00.p1.mkv"
    p2 = tmp_path / "rip" / "movies" / "The Legacy (2014)_t00.p2.mkv"
    assert p1.exists() and p2.exists()
    assert not g1.exists() and not g2.exists()


def test_existing_part_suffix_is_kept(tmp_path):
    mkfile(tmp_path / "rip" / "movies" / "Show A_t00.p1.mkv")
    mkfile(tmp_path / "rip" / "movies" / "Show A_t00.p2.mkv")
    backfill.run_backfill(tmp_path, True, picker_for({
        "Show A": pick("Show A", "2001"),
    }))
    assert (tmp_path / "rip" / "movies" / "Show A (2001)_t00.p1.mkv").exists()
    assert (tmp_path / "rip" / "movies" / "Show A (2001)_t00.p2.mkv").exists()


def test_target_collision_is_skipped(tmp_path, capsys):
    src = mkfile(tmp_path / "rip" / "movies" / "The Favourite_t00.mkv")
    mkfile(tmp_path / "rip" / "movies" / "The Favourite (2018)_t00.mkv")
    rc = backfill.run_backfill(tmp_path, True, picker_for({
        "The Favourite": pick("The Favourite", "2018"),
    }))
    out = capsys.readouterr().out
    assert rc == 0
    assert f"skip {src} (target exists -- left in place)" in out
    assert src.exists()


def test_rerun_after_apply_finds_nothing(tmp_path, capsys):
    mkfile(tmp_path / "rip" / "movies" / "The Favourite_t00.mkv")
    backfill.run_backfill(tmp_path, True, picker_for({
        "The Favourite": pick("The Favourite", "2018"),
    }))
    capsys.readouterr()
    assert backfill.run_backfill(tmp_path, False, picker_for({})) == 0
    assert "nothing to backfill" in capsys.readouterr().out


def test_tv_episode_group_searches_tv(tmp_path):
    mkfile(tmp_path / "rip" / "tv" / "Some Show_S01E05.mkv")
    kinds = []

    def picker(kind, prefill=""):
        kinds.append(kind)
        return pick("Some Show", "2020")

    backfill.run_backfill(tmp_path, True, picker)
    assert kinds == ["tv"]
    assert (tmp_path / "rip" / "tv" / "Some Show (2020)_S01E05.mkv").exists()


# -- a TMDB entry with no release date -------------------------------
def test_yearless_pick_prompts_for_manual_year(tmp_path, monkeypatch):
    mkfile(tmp_path / "rip" / "movies" / "Doc_t00.mkv")
    answers = iter(["nope", "2005"])  # typo first, then a real year
    monkeypatch.setattr("builtins.input", lambda _p: next(answers))
    backfill.run_backfill(tmp_path, True, picker_for({"Doc": pick("Doc", "-")}))
    assert (tmp_path / "rip" / "movies" / "Doc (2005)_t00.mkv").exists()


def test_yearless_pick_enter_skips_the_group(tmp_path, monkeypatch, capsys):
    src = mkfile(tmp_path / "rip" / "movies" / "Doc_t00.mkv")
    monkeypatch.setattr("builtins.input", lambda _p: "")
    rc = backfill.run_backfill(tmp_path, True, picker_for({"Doc": pick("Doc", "-")}))
    out = capsys.readouterr().out
    assert rc == 0
    assert src.exists()
    assert 'skip group "Doc" (1 file(s), no year -- left in place)' in out
    assert "1 group(s) skipped (no year)" in out


def test_yearless_pick_skipped_group_never_planned(tmp_path, monkeypatch):
    # the skipped group must not crash planning for the picked one
    mkfile(tmp_path / "rip" / "movies" / "Doc_t00.mkv")
    other = mkfile(tmp_path / "rip" / "movies" / "Fine_t00.mkv")
    monkeypatch.setattr("builtins.input", lambda _p: "")
    rc = backfill.run_backfill(tmp_path, True, picker_for({
        "Doc": pick("Doc", "-"), "Fine": pick("Fine", "2001"),
    }))
    assert rc == 0
    assert not other.exists()
    assert (tmp_path / "rip" / "movies" / "Fine (2001)_t00.mkv").exists()
