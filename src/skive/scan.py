"""MakeMKV disc reading: media sniffing, the one disc scan and its parsers,
and the ISO language helper."""

import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skive import ui
from skive.common import RipLog
from skive.makemkv import _qval

# ISO 639-1 → ISO 639-2 table sourced from the ISO 639-2 code list (Library
# of Congress, https://www.loc.gov/standards/iso639-2/php/code_list.php) and
# MakeMKV's published language list. B and T forms both work: MakeMKV
# normalizes 639-2 variants (a `fra` selection matches `fre` tracks, ADR 0022).
_ISO2B = {
    "aa": "aar",
    "ab": "abk",
    "ae": "ave",
    "af": "afr",
    "ak": "aka",
    "am": "amh",
    "an": "arg",
    "ar": "ara",
    "as": "asm",
    "av": "ava",
    "ay": "aym",
    "az": "aze",
    "ba": "bak",
    "be": "bel",
    "bg": "bul",
    "bh": "bih",
    "bi": "bis",
    "bm": "bam",
    "bo": "bod",
    "br": "bre",
    "bs": "bos",
    "ca": "cat",
    "ce": "che",
    "ch": "cha",
    "co": "cos",
    "cr": "cre",
    "cs": "ces",
    "cu": "chu",
    "cv": "chv",
    "cy": "cym",
    "da": "dan",
    "de": "deu",
    "dv": "div",
    "dz": "dzo",
    "ee": "ewe",
    "el": "gre",
    "en": "eng",
    "eo": "epo",
    "es": "spa",
    "et": "est",
    "eu": "eus",
    "fa": "fas",
    "ff": "ful",
    "fi": "fin",
    "fj": "fij",
    "fo": "fao",
    "fr": "fra",
    "fy": "fry",
    "ga": "gle",
    "gd": "gla",
    "gl": "glg",
    "gn": "grn",
    "gu": "guj",
    "gv": "glv",
    "ha": "hau",
    "he": "heb",
    "hi": "hin",
    "ho": "hmo",
    "hr": "hrv",
    "ht": "hat",
    "hu": "hun",
    "hy": "arm",
    "hz": "her",
    "ia": "ina",
    "id": "ind",
    "ie": "ile",
    "ig": "ibo",
    "ii": "iii",
    "ik": "ipk",
    "io": "ido",
    "is": "isl",
    "it": "ita",
    "iu": "iku",
    "ja": "jpn",
    "jv": "jav",
    "ka": "kat",
    "kg": "kon",
    "ki": "kik",
    "kj": "kua",
    "kk": "kaz",
    "kl": "kal",
    "km": "khm",
    "kn": "kan",
    "ko": "kor",
    "kr": "kau",
    "ks": "kas",
    "ku": "kur",
    "kv": "kom",
    "kw": "cor",
    "ky": "kir",
    "la": "lat",
    "lb": "ltz",
    "lg": "lug",
    "li": "lim",
    "ln": "lin",
    "lo": "lao",
    "lt": "lit",
    "lu": "lub",
    "lv": "lav",
    "mg": "mlg",
    "mh": "mah",
    "mi": "mri",
    "mk": "mkd",
    "ml": "mal",
    "mn": "mon",
    "mr": "mar",
    "ms": "msa",
    "mt": "mlt",
    "my": "bur",
    "na": "nau",
    "nb": "nob",
    "nd": "nde",
    "ne": "nep",
    "ng": "ndo",
    "nl": "nld",
    "nn": "nno",
    "no": "nor",
    "nr": "nbl",
    "nv": "nav",
    "ny": "nya",
    "oc": "oci",
    "oj": "oji",
    "om": "orm",
    "os": "oss",
    "pa": "pan",
    "pi": "pli",
    "pl": "pol",
    "ps": "pus",
    "pt": "por",
    "qu": "que",
    "rm": "roh",
    "rn": "run",
    "ro": "ron",
    "ru": "rus",
    "rw": "kin",
    "sa": "san",
    "sc": "srd",
    "sd": "snd",
    "se": "sme",
    "sg": "sag",
    "si": "sin",
    "sk": "slk",
    "sl": "slv",
    "sm": "smo",
    "sn": "sna",
    "so": "som",
    "sq": "alb",
    "sr": "srp",
    "ss": "ssw",
    "st": "sot",
    "su": "sun",
    "sv": "swe",
    "sw": "swa",
    "ta": "tam",
    "te": "tel",
    "tg": "tgk",
    "th": "tha",
    "ti": "tir",
    "tk": "tuk",
    "tl": "tgl",
    "tn": "tsn",
    "to": "ton",
    "tr": "tur",
    "ts": "tso",
    "tt": "tat",
    "tw": "twi",
    "ty": "tah",
    "ug": "uig",
    "uk": "ukr",
    "ur": "urd",
    "uz": "uzb",
    "ve": "ven",
    "vi": "vie",
    "vo": "vol",
    "wa": "wln",
    "wo": "wol",
    "xh": "xho",
    "yi": "yid",
    "yo": "yor",
    "za": "zha",
    "zh": "chi",
    "zu": "zul",
}


def iso2b(code: str) -> str:
    """ISO 639-1 → ISO 639-2 for MakeMKV; unmapped codes pass through."""
    return _ISO2B.get(code, code)


def _info(minlength: int | None, run=subprocess.run, disc: int = 0) -> str:
    """One `makemkvcon -r info` run (honoring MAKEMKV_INFO); minlength=None omits the flag."""
    override = os.environ.get("MAKEMKV_INFO")
    if override:
        # Replay captured dump via the command in override
        return run(override, shell=True, capture_output=True, text=True).stdout
    cmd = ["makemkvcon", "-r", "info"]
    if minlength is not None:
        cmd.append(f"--minlength={minlength}")
    cmd.append(f"disc:{disc}")
    return run(cmd, capture_output=True, text=True).stdout


def _parse_media(out: str, disc: int = 0) -> str:
    """Media token from the DRV:N line (first word), or "" if no disc."""
    for line in out.splitlines():
        m = re.match(rf'^DRV:{disc},2,\d+,\d+,"([^"]*)"', line)
        if m:
            token = m.group(1)
            return token.split()[0] if token else ""
    return ""


def _parse_label(out: str, disc: int = 0) -> str:
    """Disc volume label from the DRV:N line, cleaned into a search query.

    The label is a search prefill, never a name: underscores become spaces,
    whitespace collapses, and a trailing set marker (DISC1, DISC 2 OF 3,
    SIDE A) is stripped — it is packaging, not title. Empty when the disc
    reports no label.
    """
    for line in out.splitlines():
        m = re.match(rf'^DRV:{disc},2,\d+,\d+,"[^"]*","([^"]*)"', line)
        if m:
            label = re.sub(r"[ _]+", " ", m.group(1))
            label = re.sub(
                r"\s*[\(\[]?(?:(?:disc|disk)\s*\d+(?:\s*of\s*\d+)?|side\s*[a-d0-9])[\)\]]?\s*$",
                "",
                label,
                flags=re.IGNORECASE,
            )
            return label.strip()
    return ""


def _parse_device(out: str, disc: int = 0) -> str:
    """Device path from the DRV:N line (third quoted field), or "" if absent.

    makemkvcon reports the real device on its own DRV line; the common
    disc:N -> /dev/srN guess is wrong with mixed/parallel drives.
    """
    for line in out.splitlines():
        m = re.match(rf'^DRV:{disc},2,\d+,\d+,"[^"]*","[^"]*","([^"]*)"', line)
        if m:
            return m.group(1)
    return ""


def drive_media(run=subprocess.run) -> str:
    """Get disc:0's media type token.

    Honors MAKEMKV_INFO env var to replay a captured dump.

    Args:
        run: subprocess.run callable for monkeypatching

    Returns:
        Media token (first word), or "" if no disc
    """
    return _parse_media(_info(None, run))


def is_recordable(media: str) -> bool:
    """Check if media token is a recordable disc.

    Matches the bash case statement patterns:
    - DVD+R*, DVD+RW*, DVD-RW*, DVD-RAM*, BD-RE*, CD-RW*
    - Exact: DVD-R, CD-R, BD-R
    - Trailing variants: "DVD-R ", "DVD-R-", "BD-R "

    Args:
        media: Media token from drive_media()

    Returns:
        True if the media is recordable (DVD±R/RW/BD-R/CD-R), False otherwise
    """
    if not media:
        return False

    # Pressed or unknown — fail OPEN (bash: returns 1)
    if media in ("DVD-ROM", "BD-ROM", "CD-ROM", "HD-DVD", "CD", "DVD", "BD", ""):
        return False

    # Recordable families — bash patterns
    # DVD+R*, DVD+RW*, DVD-RW*, DVD-RAM*, BD-RE*, CD-RW*
    if re.match(r"^(DVD\+R|DVD\+RW|DVD-RW|DVD-RAM|BD-RE|CD-RW)", media):
        return True

    # Exact matches: DVD-R, CD-R, BD-R
    if media in ("DVD-R", "CD-R", "BD-R"):
        return True

    # Trailing variants: "DVD-R ", "DVD-R-", "BD-R "
    if re.match(r"^(DVD-R|BD-R) ", media):
        return True
    return bool(re.match(r"^DVD-R-", media))


@dataclass
class TitleInfo:
    """One disc title, as shown in the interactive menu."""

    number: int
    seconds: int
    size: int
    audio: str
    subtitles: str
    scrambled: bool
    decoy_cluster: int = 0


def _dur_seconds(val: str) -> int:
    if not val:
        return 0
    parts = val.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(parts[0]) * 60 + int(parts[1])


def _scrambled(val: str) -> bool:
    """Non-sequential segment map (TINFO 26) flags a decoy/alternate cut."""
    if not val:
        return False
    try:
        segs = [int(s) for s in val.split(",") if s.strip()]
    except ValueError:
        return False
    return any(segs[i] < segs[i - 1] for i in range(1, len(segs)))


def _stream_keys(stypes: dict, slangs: dict) -> list[str]:
    keys = set(stypes) | set(slangs)
    return sorted((k for k in keys if k.isdigit()), key=int)


def _audio_str(stypes: dict, slangs: dict) -> str:
    order: list[str] = []
    count: dict[str, int] = {}
    for st in _stream_keys(stypes, slangs):
        if stypes.get(st) == "Audio" and slangs.get(st, ""):
            lang = slangs[st]
            if lang not in count:
                order.append(lang)
            count[lang] = count.get(lang, 0) + 1
    return ",".join(l if count[l] <= 1 else f"{l}({count[l]})" for l in order)


def _subs_str(stypes: dict, slangs: dict) -> str:
    order: list[str] = []
    for st in _stream_keys(stypes, slangs):
        if stypes.get(st) == "Subtitles" and slangs.get(st, ""):
            lang = slangs[st]
            if lang not in order:
                order.append(lang)
    return ",".join(order)


def _parse_titles(out: str) -> list[TitleInfo]:
    """Parse TINFO/SINFO lines into TitleInfo per title, ascending by number.

    A title counts only when both duration (TINFO 9) and size (TINFO 10) are present.
    """
    data: dict[int, dict[str, Any]] = {}  # number -> per-title accumulators
    for line in out.splitlines():
        if line.startswith("TINFO:"):
            f = line.split(",")
            head = f[0].split(":")
            if len(head) < 2:
                continue
            try:
                t = int(head[1])
            except ValueError:
                continue
            rec = data.setdefault(
                t,
                {
                    "secs": 0,
                    "size": 0,
                    "scrambled": False,
                    "segmap": "",
                    "stypes": {},
                    "slangs": {},
                },
            )
            code = f[1] if len(f) > 1 else ""
            val = _qval(line)
            if code == "9":
                rec["secs"] = _dur_seconds(val)
            elif code == "11":
                rec["size"] = int(val) if val.isdigit() else 0
            elif code == "26":
                rec["scrambled"] = _scrambled(val)
                if val:
                    rec["segmap"] = val
        elif line.startswith("SINFO:"):
            f = line.split(",")
            head = f[0].split(":")
            if len(head) < 2:
                continue
            try:
                t = int(head[1])
            except ValueError:
                continue
            rec = data.setdefault(
                t, {"secs": 0, "size": 0, "scrambled": False, "stypes": {}, "slangs": {}}
            )
            stream = f[1] if len(f) > 1 else ""
            code = f[2] if len(f) > 2 else ""
            val = _qval(line)
            if code == "1":
                rec["stypes"][stream] = val
            elif code == "3":
                rec["slangs"][stream] = val

    # Decoy-cluster guard (CONTEXT.md): identical raw segment maps (TINFO 26)
    # across >= 3 titles mark every member as a mass-duplicated decoy. Exact
    # string equality only — no parsing or normalising, so range-form and
    # list-form renderings of the same cells cluster separately. This runs at
    # parse time, before any floor applies, so sub-floor titles count too.
    clusters = Counter(rec["segmap"] for rec in data.values() if rec["segmap"])

    result = []
    for t in sorted(data):
        rec = data[t]
        if rec["secs"] <= 0 or rec["size"] <= 0:
            continue
        result.append(
            TitleInfo(
                number=t,
                seconds=rec["secs"],
                size=rec["size"],
                audio=_audio_str(rec["stypes"], rec["slangs"]),
                subtitles=_subs_str(rec["stypes"], rec["slangs"]),
                scrambled=rec["scrambled"],
                decoy_cluster=(
                    clusters[rec["segmap"]] if rec["segmap"] and clusters[rec["segmap"]] >= 3 else 0
                ),
            )
        )
    return result


def materialize_makemkv_key(settings: Path | None = None) -> bool:
    """Write MAKEMKV_KEY from the environment into MakeMKV's settings.conf.

    ADR 0016's never-fetch rule stands; the env-declared write here — env
    always wins over the file — is the supersession ADR 0025 records. An
    undeclared key stays remind-only (the expiry branch in scan_disc).
    Idempotent: no write when the file's app_Key already equals the env key.

    Args:
        settings: settings.conf path (default ~/.MakeMKV/settings.conf); the
            test seam.

    Returns:
        True when a write happened, False when there was nothing to do.
    """
    key = os.environ.get("MAKEMKV_KEY")
    if not key:
        return False
    path = settings if settings is not None else Path.home() / ".MakeMKV" / "settings.conf"
    try:
        text = path.read_text() if path.is_file() else ""
        if re.search(rf'^app_Key\s*=\s*"{re.escape(key)}"\s*$', text, re.MULTILINE):
            return False
        if re.search(r"^app_Key\s*=", text, re.MULTILINE):
            text = re.sub(
                r"^app_Key\s*=.*$", f'app_Key = "{key}"', text, count=1, flags=re.MULTILINE
            )
        else:
            text = (
                (text.rstrip("\n") + "\n" + f'app_Key = "{key}"\n')
                if text
                else f'app_Key = "{key}"\n'
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    except OSError as e:
        ui.warn(
            f"could not update MakeMKV key file {path}: {e} — remind-only fallback still applies"
        )
        return False
    return True


def scan_disc(
    minlength: int, run=subprocess.run, disc: int = 0, log: RipLog | None = None
) -> tuple[str, str, list[TitleInfo]]:
    """One `makemkvcon info` run: media token, volume label, parsed titles.

    With a log, the verbatim output is persisted as a scan dump (CONTEXT.md:
    Scan dump) before parsing — so the zero-titles refusal below still leaves
    the evidence behind.
    """
    out = _info(minlength, run, disc)
    media, label, titles = _parse_media(out, disc), _parse_label(out, disc), _parse_titles(out)
    if log is not None:
        log.scan_dump(label, out)
    if media and not titles:
        # A disc with zero parseable titles means the info scan failed (e.g.
        # expired-eval makemkvcon, read errors). Keep the raw output for diagnosis.
        dump = Path(tempfile.gettempdir()) / "skive-scan-failed.txt"
        dump.write_text(out)
        if "Evaluation period has expired" in out or "shareware functionality unavailable" in out:
            # ADR 0016: never fetch the key ourselves. A key declared in
            # MAKEMKV_KEY was written at startup (materialize_makemkv_key);
            # an undeclared key stays remind-only.
            ui.fail("MakeMKV key expired — BD ripping needs a valid app_Key")
            ui.info("free key: https://forum.makemkv.com/forum/viewtopic.php?t=1053")
            ui.info('write app_Key = "…" into ~/.MakeMKV/settings.conf on the rip box, then re-run')
            ui.info("DVD and CD rips are unaffected")
            if log is not None:
                log.note("scan", "makemkv key expired — update app_Key (forum t=1053)", echo=False)
        else:
            ui.fail(f"disc scan returned no titles ({media}) — raw output saved to {dump}")
            ui.info("refusing to rip blind (this is how scrambled duplicates get ripped).")
            if log is not None:
                log.note("scan", f"no titles ({media}) — refusing to rip blind", echo=False)
        sys.exit(1)
    return media, label, titles


def sniff_disc(run=subprocess.Popen, disc: int = 0) -> tuple[str, str, str]:
    """Media token + volume label + device path from the DRV line, WITHOUT the
    title scan.

    `makemkvcon -r info` prints every DRV line up front and only then spends
    minutes scanning titles, so the process is killed as soon as this disc's
    DRV line has been read — media, label, and device are all on it, and the
    existing parsers (_parse_media / _parse_label / _parse_device) do the
    parsing. Honors MAKEMKV_INFO the way _info does (replay via shell, no
    kill).

    Args:
        run: process spawner for monkeypatching (default subprocess.Popen)
        disc: Disc number whose DRV line to wait for

    Returns:
        (media token, cleaned volume label, device path); ("", "", "") when
        no disc
    """
    override = os.environ.get("MAKEMKV_INFO")
    if override:
        # Replay captured dump via the command in override
        out = subprocess.run(
            override, shell=True, capture_output=True, text=True, check=False
        ).stdout
        return _parse_media(out, disc), _parse_label(out, disc), _parse_device(out, disc)
    proc = run(
        ["makemkvcon", "-r", "info", f"disc:{disc}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    drv_line = ""
    try:
        for line in proc.stdout:
            if line.startswith(f"DRV:{disc},"):
                drv_line = line
                break
    finally:
        proc.kill()
        proc.wait()
    return _parse_media(drv_line, disc), _parse_label(drv_line, disc), _parse_device(drv_line, disc)
