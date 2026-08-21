<#
.SYNOPSIS
  Batch-transcode staged lossless MKVs to 1080p H.265 for the Jellyfin library.

.DESCRIPTION
  Standalone Windows port of lib/transcode-video.sh -- this one file is all you
  need from the repo (see README_WINDOWS.md for setup).

  Transcodes every lossless MKV in <VIDEOS>\rip\ to 1080p H.265 and files it in
  <VIDEOS>\library\<Movie (Year)>\[extras\]. TV discs (staged under
  <VIDEOS>\rip\TV\) file under <VIDEOS>\library\TV\<Show (Year)>\Season NN\ as
  "<Show> - S01E02.mkv" (season 00 as "Specials"), TV extras under extras\.
  The subdirectory is the type: TV gets no main-feature pick and no merging.
  Pulls each file to a local temp dir
  to encode off the network share, uploads the result, then deletes the lossless
  source (-Keep: moves it to rip-DONE instead -- verify the encodes, then clean
  by hand). Track selection happened at rip time, so this is a pass-through
  re-encode: every audio + subtitle track kept, video only re-encoded, nothing
  burned in.

  A film ripped in parts (<Movie (Year)>_tNN.pN.mkv) has its main feature (the
  biggest _tNN group by bytes -- tie goes to the lowest number) merged in part
  order with mkvmerge, then transcoded as one movie. Every other title is an
  independent extra, kept unique by its .pN suffix -- only the main feature
  merges, so same-index extras are never joined.

  Junk-labelled files (no "(Year)" -- a rip in progress or a failed rename on
  the Linux box) are always skipped, so transcode never races a running rip.

  Defaults to Z:\; set $env:VIDEOS to override (drive letter or UNC both work).
  Needs HandBrakeCLI.exe and mkvmerge.exe -- on PATH or in their default install
  dirs. Appends to .\rip.log in the same format as the Linux scripts.

.PARAMETER Keep
  Move lossless sources to <VIDEOS>\rip-DONE instead of deleting them; verify
  the encodes, then clean rip-DONE by hand.

.PARAMETER Encoder
  Video encoder: x265 (default -- software, identical output to the Linux
  transcode machine), nvenc (NVIDIA GPU), qsv (Intel Quick Sync) or vce (AMD).
  GPU encoders are much faster but slightly worse quality per bit.

.PARAMETER Name
  Transcode only movies whose staged filename starts with this prefix:
  "X2 (2003)" runs one movie, "The Godfather" grabs all three.

.EXAMPLE
  .\transcode-video.ps1
  .\transcode-video.ps1 -Keep
  .\transcode-video.ps1 "X2 (2003)"
  .\transcode-video.ps1 -Encoder nvenc
#>
[CmdletBinding()]
param(
  [switch]$Keep,
  # x265 = software encode, identical output to the Linux transcode box.
  # nvenc (NVIDIA) / qsv (Intel) / vce (AMD) = GPU: much faster, slightly worse
  # quality per bit. quality fixed at 28 to match the software preset's target.
  [ValidateSet('x265', 'nvenc', 'qsv', 'vce')]
  [string]$Encoder = 'x265',
  [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
  [string[]]$Name = @()
)

$ErrorActionPreference = 'Stop'

# -- config -------------------------------------------------------------------
# Z:\ by default; $env:VIDEOS overrides (drive letter or UNC both work).
# reachability check: an SSH session is a fresh logon session and does NOT
# inherit the desktop's drive mappings -- without this guard a missing Z: would
# scan as "no MKVs" and exit 0, a silent no-op.
$vid = if ($env:VIDEOS) { $env:VIDEOS } else { 'Z:\' }
if (-not (Test-Path -LiteralPath $vid)) {
  [Console]::Error.WriteLine("transcode-video.ps1: $vid is not reachable. An SSH session doesn't inherit desktop drive mappings -- run 'net use Z: \\nas\\videos' first, or set `$env:VIDEOS to the UNC path.")
  exit 1
}
$ripDir  = Join-Path $vid 'rip'
$libDir  = Join-Path $vid 'library'
$doneDir = Join-Path $vid 'rip-DONE'
$tmpDir  = if ($env:VIDEO_WORK_DIR) { $env:VIDEO_WORK_DIR } else { Join-Path $env:TEMP 'video-work' }

function Find-Tool([string]$exe, [string]$fallback) {
  $c = Get-Command $exe -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  if ($fallback -and (Test-Path -LiteralPath $fallback)) { return (Resolve-Path -LiteralPath $fallback).Path }
  [Console]::Error.WriteLine("$exe not found on PATH nor at $fallback -- see README_WINDOWS.md (Installing the tools).")
  exit 1
}
# ponytail: ProgramFiles always exists on Windows; extra fallbacks just keep the probe null-safe elsewhere.
$pf = @($env:ProgramFiles, $env:TEMP, '/tmp') -ne $null | Select-Object -First 1
$hbExe  = Find-Tool 'HandBrakeCLI.exe' (Join-Path $pf 'HandBrake\HandBrakeCLI.exe')
$mkvExe = Find-Tool 'mkvmerge.exe'     (Join-Path $pf 'MKVToolNix\mkvmerge.exe')

New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
if ($Keep) { New-Item -ItemType Directory -Force -Path $doneDir | Out-Null }

# -- logging: same 8-column line format as the Linux rip-common.sh ------------
$tsf = 'yyyy-MM-dd HH:mm:ss'
$script:LogPath = 'rip.log'
$script:RipName = '-'
$script:Phases  = 0

function Emit([string]$t1, [string]$t2, [string]$phase, [int]$min, [int]$sec, [string]$status) {
  $line = ('{0}  {1}  transcode-video.ps1  handbrake  {2}  {3}  {4}m{5:D2}  {6}' -f `
      $t1, $t2, $script:RipName, $phase, $min, $sec, $status)
  Write-Host $line
  Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8
}
function Start-Log {
  $script:T0 = Get-Date
  $script:T0ts = $script:T0.ToString($tsf)
  Emit $script:T0ts '-' '-' 0 0 'started'
}
function Start-Phase([string]$name) {
  $script:PhaseName = $name
  $script:PhaseT0 = Get-Date
}
function End-Phase {
  $now = Get-Date
  $el = ($now - $script:PhaseT0).TotalSeconds
  $script:Phases++
  Emit ($script:PhaseT0.ToString($tsf)) ($now.ToString($tsf)) $script:PhaseName `
      ([int][math]::Floor($el / 60)) ([int][math]::Floor($el % 60)) 'ok'
}
function End-Log([int]$rc) {
  # one phase that succeeded already logged start..end..status -- a total would
  # just duplicate it (mirrors rip-common.sh). failures still get a total.
  if ($rc -eq 0 -and $script:Phases -eq 1) { return }
  $now = Get-Date
  $el = ($now - $script:T0).TotalSeconds
  $status = if ($rc -eq 0) { 'ok' } else { "FAIL($rc)" }
  Emit $script:T0ts ($now.ToString($tsf)) 'total' `
      ([int][math]::Floor($el / 60)) ([int][math]::Floor($el % 60)) $status
}
function Warn([string]$msg) { [Console]::Error.WriteLine($msg) }

# strip filesystem-hostile chars; keeps ( ) for "Title (Year)".
function Sanitize([string]$s) { return ($s -replace '[/:*?"<>|]', '') }

# archive <src>: -Keep parks the lossless source in rip-DONE instead of
# deleting it (verify-then-clean). never clobbers an existing file there.
function Archive([string]$src) {
  $d = Join-Path $doneDir (Split-Path -Leaf $src)
  if (Test-Path -LiteralPath $d) { Warn "--keep: $d already exists -- source left in $ripDir" }
  else { Move-Item -LiteralPath $src -Destination $d }
}

# HandBrake pass-through re-encode -- keep every audio + subtitle track,
# re-encode video only. identical for a merged main feature and an extra.
function Invoke-Encode([string]$src, [string]$out) {
  # GPU (non-x265): override the preset's encoder -- explicit --quality so we
  # don't depend on the preset's RF carrying over to ICQ. if the GPU/encoder
  # isn't usable HandBrake errors, the phase logs FAILED, the source stays.
  $gpu = if ($Encoder -eq 'x265') { @() } else { @('--encoder', "${Encoder}_h265", '--quality', '28') }
  # | Out-Host: keep HandBrake's progress on the console but OUT of this
  # function's output stream -- a bare call would prepend its stdout lines to the
  # return value, making the boolean always truthy and failed encodes "succeed".
  & $hbExe -i $src -o $out @gpu `
    --preset 'H.265 MKV 1080p30' `
    --all-audio --aencoder copy `
    --audio-copy-mask 'aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm' `
    --audio-fallback ac3 `
    --all-subtitles --subtitle-burned=none | Out-Host
  return ($LASTEXITCODE -eq 0)
}

# -- scan + filter staged files ------------------------------------------------
# staged layout: <Movie (Year)>_tNN[.pN].mkv (titles), _eNN[.pN].mkv (extras disc),
# and under rip\TV\: <Show (Year)>_S01E02.mkv / _S01E01-E02.mkv (a double).
# junk guard: a name without a "(YYYY)" tail is a rip in progress or a failed
# rename -- never encode those (a half-written source would encode "fine", land
# in the library, and the cleanup would delete the file out from under the ripper).
$rx    = '^(?<movie>.+)_(?<sfx>[te])(?<num>\d+)(?<part>\.p(?<pn>\d+))?\.mkv$'
$rxEp  = '^(?<movie>.+)_S(?<sn>\d{2})E(?<en>\d{2})(?<tail>-E\d{2})?\.mkv$'
$tvRoot = Join-Path $ripDir 'TV'
$tvSep  = [IO.Path]::DirectorySeparatorChar
$want = @($Name)
$wantHit = @{}
$items = New-Object System.Collections.Generic.List[object]
foreach ($f in @(Get-ChildItem -LiteralPath $ripDir -Recurse -Filter '*.mkv' -File -ErrorAction SilentlyContinue)) {
  # the subdirectory is the type (settled): anything under rip\TV\ is TV.
  $isTv = $f.FullName.StartsWith($tvRoot + $tvSep, [System.StringComparison]::Ordinal)
  $sn = $null; $en = $null; $tail = ''
  if ($f.Name -cmatch $rx) {
    # grab every group at once -- any later -match clobbers $Matches.
    $movie = $Matches['movie']; $sfx = $Matches['sfx']; $numStr = $Matches['num']; $num = [int]$numStr
    $part = if ($Matches['part']) { [int]$Matches['pn'] } else { -1 }
  } elseif ($isTv -and ($f.Name -cmatch $rxEp)) {
    $movie = $Matches['movie']; $sfx = 'S'; $numStr = $Matches['en']; $num = [int]$Matches['en']
    $part = -1; $sn = $Matches['sn']; $en = $Matches['en']; $tail = $Matches['tail']
  } else {
    Warn "skip (junk label -- mid-rip or failed rename): $($f.FullName)"; continue
  }
  if ($movie -cnotmatch '\(\d{4}\)$') { Warn "skip (junk label -- mid-rip or failed rename): $($f.FullName)"; continue }
  if ($want.Count -gt 0) {
    $hit = $false
    foreach ($w in $want) { if ($f.Name.StartsWith($w, [System.StringComparison]::Ordinal)) { $hit = $true; $wantHit[$w] = $true } }
    if (-not $hit) { continue }
  }
  # NumStr keeps zero-padding (t01 stays t01); Num/Part are ints for compare/sort.
  $items.Add([pscustomobject]@{
    Path = $f.FullName; Name = $f.Name; BaseName = $f.BaseName
    Movie = $movie; Sfx = $sfx; Num = $num; NumStr = $numStr; Part = $part; Size = $f.Length
    IsTv = $isTv; Sn = $sn; En = $en; Tail = $tail
  })
}
foreach ($w in $want) { if (-not $wantHit.ContainsKey($w)) { Warn "no staged MKVs match: $w" } }
if ($items.Count -eq 0) {
  if ($want.Count -gt 0) { Warn 'no matching MKVs to transcode'; exit 1 }
  Write-Host "no MKVs in $ripDir"
  exit 0
}

# -- main feature per movie = the _tNN group with the most bytes (a split ------
# main's .pN halves sum). tie -> lowest number. "lowest _tNN" alone picked
# menu/decoy titles on real rips, so size decides. _eNN are never main.
$grp = @{}
foreach ($i in $items) {
  if ($i.Sfx -ne 't' -or $i.IsTv) { continue }   # TV never has a main feature
  $k = '{0}|{1}' -f $i.Movie, $i.Num
  if (-not $grp.ContainsKey($k)) { $grp[$k] = [pscustomobject]@{ Movie = $i.Movie; Num = $i.Num; Sz = [long]0 } }
  $grp[$k].Sz += $i.Size
}
$mainNum = @{}
foreach ($g in $grp.Values) {
  $best = $mainNum[$g.Movie]
  if ($null -eq $best -or $g.Sz -gt $best.Sz -or ($g.Sz -eq $best.Sz -and $g.Num -lt $best.Num)) { $mainNum[$g.Movie] = $g }
}

# -- the loop ------------------------------------------------------------------
$failed = 0
$handled = @{}
Start-Log
try {
  foreach ($i in $items) {
    if ($handled.ContainsKey($i.Path)) { continue }
    $script:RipName = $i.Movie
    $safe = Sanitize $i.Movie

    # -- TV: no main feature, no merge. episodes -> Season NN (00 = Specials),
    # -- _eNN -> the show's extras. destination resolved, then the shared
    # -- transcode-as-is block below does the copy/encode/upload.
    if ($i.IsTv) {
      $showDir = Join-Path (Join-Path $libDir 'TV') $safe
      if ($i.Sfx -eq 'S') {
        $seasonName = if ([int]$i.Sn -eq 0) { 'Specials' } else { 'Season ' + $i.Sn }
        $code = 'S' + $i.Sn + 'E' + $i.En + $i.Tail
        $destDir = Join-Path $showDir $seasonName
        $dest    = Join-Path $destDir ('{0} - {1}.mkv' -f $safe, $code)
      } else {
        $ext    = if ($i.Part -ge 0) { '.p' + $i.Part } else { '' }
        $destDir = Join-Path $showDir 'extras'
        $dest    = Join-Path $destDir ('{0}_{1}{2}{3}.mkv' -f $safe, $i.Sfx, $i.NumStr, $ext)
      }
      New-Item -ItemType Directory -Force -Path $destDir | Out-Null
      $localSrc = Join-Path $tmpDir $i.Name
      $localOut = Join-Path $tmpDir ($i.BaseName + '.h265.mkv')
      Start-Phase 'encode'
      Write-Host "copying $($i.Name) to local temp..."
      Copy-Item -LiteralPath $i.Path -Destination $localSrc -Force
      if (Invoke-Encode $localSrc $localOut) {
        Move-Item -LiteralPath $localOut -Destination $dest -Force
        Remove-Item -LiteralPath $localSrc -Force
        if ($Keep) { Archive $i.Path } else { Remove-Item -LiteralPath $i.Path -Force }   # lossless source
        End-Phase
      } else {
        # leave the lossless source so a re-run retries; the total logs FAIL.
        Remove-Item -LiteralPath @($localSrc, $localOut) -Force -ErrorAction SilentlyContinue
        Warn "FAILED (handbrake): $($i.Name) -- source left in place"
        $failed = 1; End-Phase
      }
      continue
    }

    # -- main feature: merge its .pN parts (if any), transcode as one movie --
    if ($i.Sfx -eq 't' -and $null -ne $mainNum[$i.Movie] -and $mainNum[$i.Movie].Num -eq $i.Num) {
      # every staged file for this (movie, title): the split main feature.
      # part order: no-suffix first (-1 -- rescues a forgotten --part on disc 1),
      # then .pN ascending.
      $siblings = @($items | Where-Object { $_.Movie -eq $i.Movie -and $_.Sfx -eq 't' -and $_.Num -eq $i.Num } | Sort-Object Part)
      $destDir = Join-Path $libDir $safe
      $dest    = Join-Path $destDir ($safe + '.mkv')
      New-Item -ItemType Directory -Force -Path $destDir | Out-Null
      Start-Phase 'encode'
      $cleanup = New-Object System.Collections.Generic.List[string]
      $localParts = @()
      if ($siblings.Count -gt 1) {
        # pull every part to local temp, mkvmerge-append in order, then encode
        # the merged file. mkvmerge: 0 ok, 1 warning (still usable), 2+ failure.
        foreach ($s in $siblings) {
          $lb = Join-Path $tmpDir $s.Name
          Write-Host "copying $($s.Name) to local temp..."
          Copy-Item -LiteralPath $s.Path -Destination $lb -Force
          $localParts += $lb; $cleanup.Add($lb)
        }
        $localSrc = Join-Path $tmpDir ('{0}_t{1}.merged.mkv' -f $safe, $i.NumStr)
        $margs = @($localParts[0])
        for ($j = 1; $j -lt $localParts.Count; $j++) { $margs += @('+', $localParts[$j]) }
        & $mkvExe -o $localSrc @margs
        if ($LASTEXITCODE -le 1) { $cleanup.Add($localSrc) }
        else {
          Warn "FAILED mkvmerge ($LASTEXITCODE): $(($siblings.Name -join ' ')) -- sources left in place (see mkvmerge output above)"
          Remove-Item -LiteralPath $cleanup.ToArray() -Force -ErrorAction SilentlyContinue
          foreach ($s in $siblings) { $handled[$s.Path] = $true }
          $failed = 1; End-Phase; continue
        }
      } else {
        $localSrc = Join-Path $tmpDir $i.Name
        Write-Host "copying $($i.Name) to local temp..."
        Copy-Item -LiteralPath $i.Path -Destination $localSrc -Force
        $cleanup.Add($localSrc)
      }

      $localOut = Join-Path $tmpDir ('{0}_t{1}.h265.mkv' -f $safe, $i.NumStr)
      if (Invoke-Encode $localSrc $localOut) {
        Move-Item -LiteralPath $localOut -Destination $dest -Force
        Remove-Item -LiteralPath $cleanup.ToArray() -Force -ErrorAction SilentlyContinue
        if ($Keep) { foreach ($s in $siblings) { Archive $s.Path } }
        else { Remove-Item -LiteralPath ($siblings.Path) -Force -ErrorAction SilentlyContinue }   # lossless sources on the NAS
        foreach ($s in $siblings) { $handled[$s.Path] = $true }
        End-Phase
      } else {
        Remove-Item -LiteralPath (@($cleanup.ToArray()) + @($localOut)) -Force -ErrorAction SilentlyContinue
        Warn "FAILED (handbrake): $($i.Name) -- sources left in place"
        foreach ($s in $siblings) { $handled[$s.Path] = $true }
        $failed = 1; End-Phase
      }
      continue
    }

    # -- extras (non-main _t title, or an _e extras-disc title): transcode as-is.
    # the .pN suffix is kept so split-disc extras never collide.
    $ext    = if ($i.Part -ge 0) { '.p' + $i.Part } else { '' }
    $destDir = Join-Path (Join-Path $libDir $safe) 'extras'
    $dest    = Join-Path $destDir ('{0}_{1}{2}{3}.mkv' -f $safe, $i.Sfx, $i.NumStr, $ext)
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    $localSrc = Join-Path $tmpDir $i.Name
    $localOut = Join-Path $tmpDir ($i.BaseName + '.h265.mkv')
    Start-Phase 'encode'
    Write-Host "copying $($i.Name) to local temp..."
    Copy-Item -LiteralPath $i.Path -Destination $localSrc -Force
    if (Invoke-Encode $localSrc $localOut) {
      Move-Item -LiteralPath $localOut -Destination $dest -Force
      Remove-Item -LiteralPath $localSrc -Force
      if ($Keep) { Archive $i.Path } else { Remove-Item -LiteralPath $i.Path -Force }   # lossless source
      End-Phase
    } else {
      # leave the lossless source so a re-run retries; the total logs FAIL.
      Remove-Item -LiteralPath @($localSrc, $localOut) -Force -ErrorAction SilentlyContinue
      Warn "FAILED (handbrake): $($i.Name) -- source left in place"
      $failed = 1; End-Phase
    }
  }
} catch {
  $failed = 1
  Warn "FATAL: $_"
} finally {
  End-Log $failed
}

if ($Keep) { Write-Host "--keep: lossless sources moved to $doneDir -- verify, then clean by hand" }
exit $failed
