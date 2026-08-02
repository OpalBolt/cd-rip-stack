#!/usr/bin/env bash
# sync-sd — copy the NAS music library onto the SD card currently in the slot.
# mounts NFS_MUSIC at MUSIC_MOUNT on demand (shared mount-nfs helper, same one
# sync-music uses), then rsyncs $MUSIC_MOUNT/library/ → <sd-card>/ . the source
# stays put — this is a copy to a player card, not a move like sync-music.
#
# the card is auto-detected: a mounted removable device, or one auto-mounted
# under /media|/run/media (covers internal mmcblk SD readers). if none is
# mounted, sync-sd lists the unmounted removable disks (sdx / mmcblk) so you can
# pick one to mount — and unmounts it again on exit (success, Ctrl-C, or any
# failure). cards that were already mounted are left mounted. pass a mountpoint
# as $1 to skip all detection.
# ponytail: --size-only skips already-copied files on re-run by size alone, so
# it works on any card fs — fat/exfat reject -a's timestamps+perms and choke on
# the 2s mtime granularity; size-only sidesteps the whole question.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"

mnt="${MUSIC_MOUNT:-/mnt/music}"
src="$mnt/library"
we_mounted=          # 1 if THIS run mounted the card, so the EXIT trap unmounts it
card_mnt=            # the tmpdir we mounted onto (for rmdir), only when we_mounted=1

usage() {
  cat <<EOF
usage: sync-sd [card-mountpoint]
  copies $src/ onto the SD card (source kept). mounts $mnt first
  (needs NFS_MUSIC in the env). a mounted card is auto-detected; if none is
  mounted, the unmounted removable disks are listed to pick from (and unmounted
  again on exit). pass a path to override or disambiguate.
EOF
}

# unmount + remove the mountpoint we created, if this run mounted it. best-effort
# so a busy/stuck umount never hides the rip.log total line.
_sync_sd_unmount() {
  if [ "${we_mounted:-}" = 1 ] && [ -n "${card_mnt:-}" ]; then
    sudo umount "$card_mnt" 2>/dev/null \
      || echo "sync-sd: could not unmount $card_mnt — run: sudo umount $card_mnt" >&2
    rmdir "$card_mnt" 2>/dev/null || true
  fi
}

case "${1:-}" in
  -h | --help) usage; exit 0 ;;
  '') card= ;;
  *) card=$1 ;;
esac

if [ -z "$card" ]; then
  # 1) a mounted removable card: rm flag, or auto-mounted under /media|/run/media
  #    (the path clause catches internal mmcblk readers that don't report rm).
  #    jq's `.. | objects` recurses into partitions so a child's mountpoint counts.
  mounted_filter='[.. | objects
                   | select(.mountpoint != null)
                   | select(.rm == true or (.mountpoint | test("^/(run/media/|media/)")))
                   | .mountpoint] | unique[]'
  mapfile -t mounted < <(lsblk -J -o NAME,RM,MOUNTPOINT 2>/dev/null \
    | jq -r "$mounted_filter" 2>/dev/null || true)
  if [ "${#mounted[@]}" -gt 0 ]; then
    if [ "${#mounted[@]}" -eq 1 ]; then
      card=${mounted[0]}
    else
      echo "sync-sd: several removable devices mounted — pick one:" >&2
      for i in "${!mounted[@]}"; do printf '  %d) %s\n' "$((i + 1))" "${mounted[$i]}" >&2; done
      printf 'choice (blank to abort): ' >&2
      read -r choice || choice=
      if [ -n "$choice" ] && [[ $choice =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#mounted[@]}" ]; then
        card=${mounted[$((choice - 1))]}
      else
        echo "sync-sd: no valid choice — aborting" >&2; exit 1
      fi
    fi
  else
    # 2) nothing mounted — list the unmounted removable disks (sdx / mmcblk) and
    #    let the user pick one to mount. jq only SELECTS and returns disk names
    #    (every device has .name, so nothing depends on a fragile label field);
    #    the display size/model and the partition to mount are resolved with a fresh
    #    lsblk per disk at use-time. the mount is deferred until after rip_log_start
    #    so the EXIT trap unmounts it even if a later step fails.
    mapfile -t disks < <(lsblk -J -o NAME,RM,TYPE,MOUNTPOINT 2>/dev/null | jq -r '
        .blockdevices[]
        | select(.type == "disk")
        | select(.rm == true or (.name | startswith("mmcblk")))
        | select(.mountpoint == null)
        | select(all((.children // [])[]; .mountpoint == null))
        | .name' 2>/dev/null || true)
    [ "${#disks[@]}" -gt 0 ] || {
      echo "sync-sd: no SD card found (mounted or unmounted) — insert one and re-run" >&2
      exit 1
    }
    echo "sync-sd: no card mounted — pick a device to mount:" >&2
    for i in "${!disks[@]}"; do
      read -r dsize dmodel < <(lsblk -dno SIZE,MODEL "/dev/${disks[$i]}" 2>/dev/null) || true
      [ -n "${dmodel:-}" ] && dmodel="  $dmodel"
      printf '  %d) %s  %s%s\n' "$((i + 1))" "${disks[$i]}" "${dsize:-?}" "${dmodel:-}" >&2
    done
    printf 'choice (blank to abort): ' >&2
    read -r choice || choice=
    if [ -n "$choice" ] && [[ $choice =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#disks[@]}" ]; then
      pending_disk=${disks[$((choice - 1))]}
    else
      echo "sync-sd: no valid choice — aborting" >&2; exit 1
    fi
  fi
fi

RIP_SCRIPT=sync-sd.sh
RIP_SW=rsync
RIP_NAME=-
rip_log_start
# rip_log_start sets `trap rip_log_finish EXIT`; replace it with one that logs the
# total AND unmounts a card we mounted — so Ctrl-C or a failed rsync still unmounts.
trap 'rip_log_finish; _sync_sd_unmount' EXIT

# mount the picked device now that the EXIT trap is in place. resolve the first
# partition of the disk (or the disk itself, if partitionless) with a fresh lsblk.
# -l (--list) is load-bearing: without it lsblk prints tree branches (└─sda1)
# and awk would capture that prefix as part of the partition name.
if [ -n "${pending_disk:-}" ]; then
  part=$(lsblk "/dev/$pending_disk" -lno NAME,TYPE 2>/dev/null | awk '$2 == "part" { print $1; exit }')
  [ -n "$part" ] || part=$pending_disk
  card_mnt=$(mktemp -d -t sync-sd.XXXXXX)
  # FAT/exFAT cards mount root-owned by default, so the user can't write to them.
  # mount as the current user (uid/gid + a tight umask); fall back to a plain
  # mount for filesystems that reject those options (e.g. ext4, real ownership).
  if sudo mount -o uid=$(id -u),gid=$(id -g),umask=0077 "/dev/$part" "$card_mnt" 2>/dev/null \
     || sudo mount "/dev/$part" "$card_mnt"; then
    we_mounted=1
    card=$card_mnt
    RIP_NAME=$part
  else
    rmdir "$card_mnt" 2>/dev/null || true
    echo "sync-sd: mounting /dev/$part failed (needs sudo? wrong fs / unreadable?)" >&2
    exit 1
  fi
else
  [ -d "$card" ] || { echo "sync-sd: $card is not a mounted directory" >&2; exit 1; }
  RIP_NAME=$(basename "$card")
fi

# only pay the NFS mount cost once a card is in hand.
mount-nfs "$mnt" "${NFS_MUSIC:-}"
[ -d "$src" ] || { echo "sync-sd: no library at $src (run sync-music first)" >&2; exit 1; }

rip_phase sync
rsync -r --size-only --info=progress2 "$src"/ "$card"/
rip_phase_end
