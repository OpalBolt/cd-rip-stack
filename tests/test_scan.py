"""Tests for disc scanning: media sniff, the one disc scan."""

import os
import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from skive.scan import drive_media, is_recordable, iso2b, scan_disc


def test_iso2b_mapping():
    """Test ISO 639-1 to 2/B mapping."""
    assert iso2b("en") == "eng"
    assert iso2b("da") == "dan"
    assert iso2b("fr") == "fra"
    assert iso2b("xx") == "xx"  # unknown passes through
    assert iso2b("de") == "deu"
    # Full-table sanity: every key 2 lowercase letters, every value 3 lowercase
    assert iso2b("zh") == "chi"
    assert iso2b("ja") == "jpn"  # ISO 639-2/B form; MakeMKV normalizes zho too
    # full-table sanity: keys are 2 lowercase letters, values 3
    from skive.scan import _ISO2B

    assert all(re.fullmatch(r"[a-z]{2}", k) for k in _ISO2B)
    assert all(re.fullmatch(r"[a-z]{3}", v) for v in _ISO2B.values())


def test_is_recordable_known_recordable():
    """Test is_recordable returns True for known recordable types."""
    assert is_recordable("DVD+R")
    assert is_recordable("DVD+R-DL")
    assert is_recordable("DVD+RW")
    assert is_recordable("DVD-RW")
    assert is_recordable("DVD-RAM")
    assert is_recordable("BD-RE")
    assert is_recordable("CD-RW")
    assert is_recordable("DVD-R")
    assert is_recordable("DVD-R ")
    assert is_recordable("DVD-R-")
    assert is_recordable("CD-R")
    assert is_recordable("BD-R")
    assert is_recordable("BD-R ")


def test_is_recordable_pressed():
    """Test is_recordable returns False for pressed discs."""
    assert not is_recordable("DVD-ROM")
    assert not is_recordable("BD-ROM")
    assert not is_recordable("CD-ROM")
    assert not is_recordable("HD-DVD")
    assert not is_recordable("DVD")
    assert not is_recordable("BD")
    assert not is_recordable("CD")
    assert not is_recordable("")
    # DVD+RDL matches DVD+R* in bash, so IS recordable (not unknown)


def test_is_recordable_unknown():
    """Test is_recordable fails open on unknown tokens."""
    assert not is_recordable("SOMETHING-ELSE")


def test_is_recordable_dvd_rdl():
    """DVD+RDL matches DVD+R* in bash, so IS recordable."""
    assert is_recordable("DVD+RDL")


def test_drive_media_real(tmp_path):
    """Test drive_media parses DRV:0 line."""
    mock_output = 'DRV:0,2,0,0,"DVD-ROM hp CDDVDW TS-L633N"'
    mock_run = Mock(return_value=Mock(stdout=mock_output))

    media = drive_media(run=mock_run)
    assert media == "DVD-ROM"


def test_drive_media_env_override():
    """Test drive_media honors MAKEMKV_INFO."""
    mock_output = 'DRV:0,2,0,0,"BD-ROM Pioneer BDR-209"'
    with patch.dict("os.environ", {"MAKEMKV_INFO": f"echo '{mock_output}'"}):
        media = drive_media()
        assert media == "BD-ROM"


def test_drive_media_no_disc():
    """Test drive_media returns empty when no disc."""
    mock_output = ""
    mock_run = Mock(return_value=Mock(stdout=mock_output))

    media = drive_media(run=mock_run)
    assert media == ""


def test_scan_disc_parses_audio_subs_scrambled():
    """scan_disc parses audio counts, subtitle langs, and the S flag."""
    from skive.scan import scan_disc

    output = """
TINFO:0,9,0,"1:26:43"
TINFO:0,11,0,"5845346392"
TINFO:0,26,0,"1,2,3"
SINFO:0,0,1,0,"Video"
SINFO:0,0,3,0,"dan"
SINFO:0,1,1,0,"Audio"
SINFO:0,1,3,0,"dan"
SINFO:0,2,1,0,"Audio"
SINFO:0,2,3,0,"dan"
SINFO:0,3,1,0,"Audio"
SINFO:0,3,3,0,"eng"
SINFO:0,4,1,0,"Subtitles"
SINFO:0,4,3,0,"dan"
SINFO:0,5,1,0,"Subtitles"
SINFO:0,5,3,0,"eng"
TINFO:1,9,0,"0:03:33"
TINFO:1,11,0,"169400000"
TINFO:1,26,0,"3,2,1"
"""

    _, _, result = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))

    assert len(result) == 2
    t0 = result[0]
    assert t0.number == 0
    assert t0.seconds == 1 * 3600 + 26 * 60 + 43
    assert t0.audio == "dan(2),eng"
    assert t0.subtitles == "dan,eng"
    assert t0.scrambled is False

    t1 = result[1]
    assert t1.number == 1
    assert t1.scrambled is True
    assert t1.audio == ""
    assert t1.subtitles == ""


def test_scan_disc_bails_when_no_titles(tmp_path, monkeypatch, capsys):
    """A disc with media but zero parseable titles aborts instead of ripping blind."""
    import pytest

    from skive.common import RipLog
    from skive.scan import scan_disc

    output = 'DRV:0,2,999,12,"BD-RE ASUS","ROGUE_ONE","/dev/sr0"\nMSG:3307,0,2,"File 00800.mpls was added as title #1","",""\n'
    monkeypatch.setattr("skive.scan.tempfile.gettempdir", lambda: str(tmp_path))
    log = RipLog("rip-movie", "makemkv", root=str(tmp_path))
    with pytest.raises(SystemExit):
        scan_disc(120, run=Mock(return_value=Mock(stdout=output)), log=log)
    assert "no titles" in capsys.readouterr().err
    assert (tmp_path / "skive-scan-failed.txt").exists()
    import json

    lines = [json.loads(l) for l in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert any(l["phase"] == "scan" and "no titles" in l["status"] for l in lines)


def test_scan_disc_names_expired_key(tmp_path, monkeypatch, capsys):
    """Expired MakeMKV key: the refusal names the cause and the fix (ADR 0016),
    and the run log records it — fed by a real expired-key scan dump."""
    import json

    import pytest

    from skive.common import RipLog
    from skive.scan import scan_disc

    output = (Path(__file__).parent / "fixtures" / "makemkv-expired-scan.txt").read_text()
    monkeypatch.setattr("skive.scan.tempfile.gettempdir", lambda: str(tmp_path))
    log = RipLog("rip-movie", "makemkv", root=str(tmp_path))
    with pytest.raises(SystemExit):
        scan_disc(120, run=Mock(return_value=Mock(stdout=output)), log=log)
    err = capsys.readouterr().err
    assert "MakeMKV key expired" in err
    assert "forum.makemkv.com/forum/viewtopic.php?t=1053" in err
    assert (tmp_path / "skive-scan-failed.txt").exists()
    lines = [json.loads(l) for l in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert any(l["phase"] == "scan" and "key expired" in l["status"] for l in lines)


def test_disc_n_threads_through_scan_and_rip(tmp_path):
    """disc=1 uses disc:1 in commands and parses the DRV:1 line."""
    from skive.makemkv import rip_titles
    from skive.scan import scan_disc

    output = """DRV:0,256,999,0,"","",""
DRV:1,2,999,12,"BD-ROM DRIVE","MOVIE","/dev/sr1"
TINFO:0,9,0,"1:26:43"
TINFO:0,11,0,"5845346392"
"""
    mock_run = Mock(return_value=Mock(stdout=output))
    media, label, titles = scan_disc(120, run=mock_run, disc=1)
    assert media == "BD-ROM"
    assert label == "MOVIE"
    assert len(titles) == 1
    assert "disc:1" in mock_run.call_args[0][0]

    rip_run = Mock(return_value=Mock(returncode=0))
    rip_titles(None, Path("/tmp/p.xml"), 120, tmp_path, run=rip_run, disc=1)
    assert "disc:1" in rip_run.call_args[0][0]


def test_scan_disc_label_cleaning():
    """The DRV volume label becomes a cleaned search prefill."""
    from skive.scan import scan_disc

    output = 'DRV:0,2,999,12,"BD-RE ASUS","ROGUE__ONE_  A ","x"\nTINFO:0,9,0,"1:26:43"\nTINFO:0,11,0,"5845346392"\n'
    _, label, titles = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))
    assert label == "ROGUE ONE A"
    assert len(titles) == 1


def test_scan_disc_label_absent():
    """No second DRV field means no prefill."""
    from skive.scan import scan_disc

    output = (
        'DRV:0,2,0,0,"DVD-ROM hp CDDVDW TS-L633N"\nTINFO:0,9,"0:21:00"\nTINFO:0,11,"1234567890"\n'
    )
    _, label, _titles = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))
    assert label == ""


def test_scan_disc_label_strips_set_marker():
    """Trailing DISC/SIDE markers are packaging, not title."""
    from skive.scan import scan_disc

    for raw, want in [
        ("GENERATION_KILL_DISC1", "GENERATION KILL"),
        ("SHOW DISC 2 OF 3", "SHOW"),
        ("MOVIE_SIDE_A", "MOVIE"),
        ("DISC1", ""),
    ]:
        output = f'DRV:0,2,999,12,"BD-RE ASUS","{raw}","x"\nTINFO:0,9,0,"1:26:43"\nTINFO:0,11,0,"5845346392"\n'
        _, label, _titles = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))
        assert label == want, raw


def test_scan_disc():
    """One info run returns the media token + parsed titles."""
    output = """
DRV:0,2,0,0,"DVD-ROM hp CDDVDW TS-L633N"
TINFO:0,9,"0:21:00"
TINFO:0,11,"1234567890"
TINFO:1,9,"0:22:30"
TINFO:1,11,"2345678901"
TINFO:2,9,"0:15:00"
TINFO:2,11,"3456789012"
"""

    mock_run = Mock(return_value=Mock(stdout=output))

    media, _label, titles = scan_disc(120, run=mock_run)

    assert media == "DVD-ROM"
    # titles sorted by number, with seconds and size
    assert len(titles) == 3
    assert titles[0].number == 0
    assert titles[0].seconds == 21 * 60  # 21:00
    assert titles[0].size == 1234567890
    assert titles[1].number == 1
    assert titles[1].seconds == 22 * 60 + 30  # 22:30
    assert titles[2].number == 2
    assert titles[2].seconds == 15 * 60
    assert all(t.decoy_cluster == 0 for t in titles)


def test_scan_disc_makemkv_info_override():
    """Honor MAKEMKV_INFO env var."""
    output = """
DRV:0,2,0,0,"DVD-ROM"
TINFO:0,9,"0:21:00"
TINFO:0,11,"1234567890"
"""
    mock_run = Mock(return_value=Mock(stdout=output))

    with patch.dict(os.environ, {"MAKEMKV_INFO": "echo test"}):
        media, _label, titles = scan_disc(120, run=mock_run)

    assert media == "DVD-ROM"
    assert len(titles) == 1


def test_scan_disc_persists_scan_dump(tmp_path):
    """With a log, scan_disc writes its verbatim output to
    <root>/logs/scan/<run>_<label>.txt — written on the success path too,
    before anyone knows a run will go wrong."""
    from skive.common import RipLog

    output = 'DRV:0,2,0,0,"DVD-ROM drive","G1_70736D1","/dev/sr0"\nTINFO:0,9,"0:21:00"\nTINFO:0,11,"1234567890"\n'
    log = RipLog("skive rip tv", "makemkvcon", root=tmp_path)

    media, _label, titles = scan_disc(120, run=Mock(return_value=Mock(stdout=output)), log=log)

    assert (media, len(titles)) == ("DVD-ROM", 1)
    # _parse_label cleans G1_70736D1 -> "G1 70736D1" (underscores to spaces)
    dump = tmp_path / "logs" / "scan" / f"{log.run}_G1 70736D1.txt"
    assert dump.read_text(encoding="utf-8") == output


def test_scan_disc_dump_before_zero_titles_bail(tmp_path, capsys):
    """The scan dump lands before the zero-titles refusal — a scan that
    parses nothing still leaves its evidence in logs/scan/, not just the
    /tmp dump."""
    from skive.common import RipLog

    output = 'DRV:0,2,0,0,"DVD-ROM drive","BAD_DISC","/dev/sr0"\nTCOUNT:0\n'
    log = RipLog("skive rip tv", "makemkvcon", root=tmp_path)

    with pytest.raises(SystemExit):
        scan_disc(120, run=Mock(return_value=Mock(stdout=output)), log=log)

    assert (tmp_path / "logs" / "scan" / f"{log.run}_BAD DISC.txt").exists()


def test_scan_disc_without_log_writes_nothing(tmp_path):
    """No log -> no dump, no crash: library/test callers stay side-effect free."""
    output = 'DRV:0,2,0,0,"DVD-ROM drive","G1_70736D1","/dev/sr0"\nTINFO:0,9,"0:21:00"\nTINFO:0,11,"1234567890"\n'

    media, _label, titles = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))

    assert (media, len(titles)) == ("DVD-ROM", 1)
    assert not (tmp_path / "logs").exists()


def test_scan_disc_clusters_identical_segment_maps():
    """Titles sharing an identical raw segment map (>= 3) get decoy_cluster.

    Fixture (orchestrator's resolution of the ticket's ambiguity):
      t1..t5 share list-form "9,10,11,12,13" -> decoy_cluster == 5; t5 is
        SUB-FLOOR (400s) but still counts toward the cluster of 5 (clustering
        ignores all floors);
      t6 feature-length with range-form "9-13" (same cells) -> 0, the
        range/list no-merge case (mirrors the real disc's 82 list-form
        decoys vs t38's "9-26");
      t7 map "3,1,2" -> scrambled is True, cluster 0 (shuffled alone never
        clusters);
      t8 SUB-FLOOR (~400s) carrying "9-13" -> 0; together with t6 it is the
        two-sharing pair below the >= 3 threshold.
    """
    lines = []
    segmaps = {
        1: "9,10,11,12,13",
        2: "9,10,11,12,13",
        3: "9,10,11,12,13",
        4: "9,10,11,12,13",
        5: "9,10,11,12,13",
        6: "9-13",
        7: "3,1,2",
        8: "9-13",
    }
    durations = {
        1: "0:50:00",
        2: "0:50:00",
        3: "0:50:00",
        4: "0:50:00",
        5: "0:06:40",
        6: "1:30:00",
        7: "0:50:00",
        8: "0:06:40",
    }
    for t in sorted(segmaps):
        lines.append(f'TINFO:{t},9,0,"{durations[t]}"')
        lines.append(f'TINFO:{t},11,0,"1000000000"')
        lines.append(f'TINFO:{t},26,0,"{segmaps[t]}"')
    output = "\n".join(lines) + "\n"

    _, _, result = scan_disc(60, run=Mock(return_value=Mock(stdout=output)))

    assert len(result) == 8
    got = {t.number: t.decoy_cluster for t in result}
    assert got == {1: 5, 2: 5, 3: 5, 4: 5, 5: 5, 6: 0, 7: 0, 8: 0}
    t7 = next(t for t in result if t.number == 7)
    assert t7.scrambled is True
    t1 = next(t for t in result if t.number == 1)
    assert t1.scrambled is False

    # Manual check (real Flame & Citron scan dump replay, documented per
    # ticket 01; non-monotonic count in that dump: 0 — absent, as expected):
    #   $ MAKEMKV_INFO="cat '/mnt/videos/logs/scan/f2c0767a_FLAMMEN CITRONEN.txt'" \
    #     uv run python -c "from skive.scan import scan_disc; \
    #     m,l,ts=scan_disc(600); print(sum(1 for t in ts if t.decoy_cluster==82), \
    #     [(t.number,t.decoy_cluster,t.scrambled) for t in ts if t.number==38])"
    #   Expected output: "82 [(38, 0, False)]" — 82 titles at cluster size 82,
    #   t38 clean.
