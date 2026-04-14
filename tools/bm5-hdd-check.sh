#!/bin/bash
# BM5 HDD Check — detect a BeoMaster 5 / BeoSound 5 hard drive and show library stats
# Run on a BS5c (Raspberry Pi) with the original HDD connected via USB/SATA.

set -euo pipefail

echo "=== BeoMaster 5 HDD Check ==="
echo

# --- Step 1: Find NTFS partitions ---
NTFS_PARTS=$(lsblk -rno NAME,FSTYPE,SIZE,MOUNTPOINT 2>/dev/null | awk '$2 ~ /ntfs/' || true)

if [ -z "$NTFS_PARTS" ]; then
    echo "No NTFS partitions found."
    echo "Make sure the BM5 hard drive is connected via USB or SATA."
    exit 1
fi

echo "NTFS partitions found:"
echo "$NTFS_PARTS" | while read -r name fs size mp; do
    echo "  /dev/$name  ${size}  ${mp:-(not mounted)}"
done
echo

# --- Step 2: Find BM5 marker (BM-Share/Music) ---
BM5_MOUNT=""
PROBE_MOUNT=""  # track temp mount so we can clean up

# Check already-mounted partitions first (including service mounts at /mnt/beo-usb-*)
while read -r name fs size mp; do
    if [ -n "$mp" ] && [ -d "$mp/BM-Share/Music" ]; then
        BM5_MOUNT="$mp"
        BM5_DEV="/dev/$name"
        break
    fi
done <<< "$NTFS_PARTS"

# Try mounting unmounted ones (temporary probe — cleaned up on exit)
if [ -z "$BM5_MOUNT" ]; then
    while read -r name fs size mp; do
        [ -n "$mp" ] && continue
        TMP="/tmp/bm5-probe-$name"
        mkdir -p "$TMP"
        if sudo mount -t ntfs3 -o "ro,uid=$(id -u),gid=$(id -g)" "/dev/$name" "$TMP" 2>/dev/null || \
           sudo mount -t ntfs-3g -o "ro,uid=$(id -u),gid=$(id -g)" "/dev/$name" "$TMP" 2>/dev/null; then
            if [ -d "$TMP/BM-Share/Music" ]; then
                BM5_MOUNT="$TMP"
                BM5_DEV="/dev/$name"
                PROBE_MOUNT="$TMP"
                echo "Mounted $BM5_DEV at $BM5_MOUNT (temporary probe)"
                break
            fi
            sudo umount "$TMP" 2>/dev/null
        fi
        rmdir "$TMP" 2>/dev/null || true
    done <<< "$NTFS_PARTS"
fi

# Clean up temp probe mount on exit
cleanup() {
    if [ -n "$PROBE_MOUNT" ]; then
        echo
        echo "Cleaning up temporary mount at $PROBE_MOUNT..."
        sudo umount "$PROBE_MOUNT" 2>/dev/null
        rmdir "$PROBE_MOUNT" 2>/dev/null || true
    fi
}
trap cleanup EXIT

if [ -z "$BM5_MOUNT" ]; then
    echo "No BM5 drive found (no NTFS partition has BM-Share/Music)."
    echo "This drive may not be from a BeoMaster 5 / BeoSound 5."
    exit 1
fi

echo "BM5 drive: $BM5_DEV -> $BM5_MOUNT"
echo

# --- Step 3: Database stats ---
DB="$BM5_MOUNT/Cache/Data/nmusic.db"

if [ ! -f "$DB" ]; then
    echo "Warning: nmusic.db not found — showing filesystem stats only."
    echo
else
    echo "=== Library (from nmusic.db) ==="
    python3 << PYEOF
import sqlite3, os

db_path = "$DB"
db_size = os.path.getsize(db_path) / (1024 * 1024)

conn = sqlite3.connect(f"file://{db_path}?mode=ro", uri=True)
_coll = lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower())
conn.create_collation("i_en_UK", _coll)
conn.create_collation("i_sv_SE", _coll)

def q(sql):
    return conn.execute(sql).fetchone()[0]

tracks    = q("SELECT COUNT(*) FROM track")
albums    = q("SELECT COUNT(*) FROM album")
artists   = q("SELECT COUNT(*) FROM album_artist")
playlists = q("SELECT COUNT(*) FROM playlist")
favorites = q("SELECT COUNT(*) FROM favorite_list WHERE custom_size > 0") if True else 0

# Duration (stored in seconds)
total_s   = q("SELECT COALESCE(SUM(duration), 0) FROM track")
hours     = total_s // 3600
mins      = (total_s % 3600) // 60

# Play counts
played    = q("SELECT COUNT(*) FROM track WHERE play_count > 0")
top_plays = q("SELECT MAX(play_count) FROM track")

# Year range
min_year  = q("SELECT MIN(album_release_year) FROM track WHERE album_release_year > 0")
max_year  = q("SELECT MAX(album_release_year) FROM track WHERE album_release_year > 0")

# Genres
genres = conn.execute(
    "SELECT genre, COUNT(*) FROM track WHERE genre IS NOT NULL "
    "GROUP BY genre ORDER BY COUNT(*) DESC LIMIT 10"
).fetchall()

# Top artists
top_artists = conn.execute(
    "SELECT a.name, COUNT(al.id) AS n FROM album_artist a "
    "JOIN album al ON al.album_artist_id = a.id "
    "GROUP BY a.id ORDER BY n DESC LIMIT 10"
).fetchall()

# Playlists with items
pl_info = conn.execute(
    "SELECT p.name, COUNT(pi.id) FROM playlist p "
    "LEFT JOIN playlist_item pi ON pi.playlist_id = p.id "
    "GROUP BY p.id ORDER BY COUNT(pi.id) DESC"
).fetchall()

conn.close()

print(f"  Tracks:      {tracks:,}")
print(f"  Albums:      {albums:,}")
print(f"  Artists:     {artists:,}")
days = hours // 24
rh = hours % 24
print(f"  Duration:    {days}d {rh}h {mins}m" if days else f"  Duration:    {hours}h {mins}m")
print(f"  Years:       {min_year or '?'} - {max_year or '?'}")
print(f"  Played:      {played:,} / {tracks:,} tracks ({100*played//tracks}%)")
print(f"  Most played: {top_plays}x")
print(f"  Database:    {db_size:.1f} MB")
print()

if playlists:
    print(f"  Playlists ({playlists}):")
    for name, count in pl_info:
        if count > 0:
            print(f"    {name}: {count} tracks")
    print()

if genres:
    print(f"  Top genres:")
    for genre, count in genres:
        print(f"    {genre}: {count}")
    print()

if top_artists:
    print(f"  Top artists (by albums):")
    for name, count in top_artists:
        print(f"    {name}: {count} albums")
PYEOF
fi

# --- Step 4: Filesystem stats ---
echo
echo "=== Filesystem ==="
MUSIC="$BM5_MOUNT/BM-Share/Music"
if [ -d "$MUSIC" ]; then
    WMA=$(find "$MUSIC" -iname '*.wma' | wc -l)
    MP3=$(find "$MUSIC" -iname '*.mp3' | wc -l)
    FLAC=$(find "$MUSIC" -iname '*.flac' | wc -l)
    DIRS=$(find "$MUSIC" -type d | wc -l)
    SIZE=$(du -sh "$MUSIC" 2>/dev/null | cut -f1)
    echo "  Music path:  $MUSIC"
    echo "  Total size:  $SIZE"
    echo "  WMA files:   $WMA"
    [ "$MP3" -gt 0 ] && echo "  MP3 files:   $MP3"
    [ "$FLAC" -gt 0 ] && echo "  FLAC files:  $FLAC"
    echo "  Folders:     $DIRS"
else
    echo "  Music directory not found at $MUSIC"
fi

# Other databases
echo
echo "=== Other databases ==="
for db in "$BM5_MOUNT/Cache/Data/"*.db; do
    [ -f "$db" ] && echo "  $(basename "$db")  $(du -h "$db" 2>/dev/null | cut -f1)"
done

echo
echo "Done."
