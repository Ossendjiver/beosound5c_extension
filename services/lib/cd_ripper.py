from __future__ import annotations

"""Managed audio-CD ripping for the BeoSound 5c CD source.

The manager deliberately owns the optical drive while a rip is active.  CD
playback can then switch to completed FLAC files instead of running a second
reader against the same drive.
"""

import asyncio
import errno
import json
import logging
import os
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Awaitable, Callable, Optional

from lib.background_tasks import BackgroundTaskSet


log = logging.getLogger(__name__)

UpdateCallback = Callable[[dict], Optional[Awaitable[None]]]
TrackCallback = Callable[[int, Path], Optional[Awaitable[None]]]


def safe_component(value: str, fallback: str = "Unknown") -> str:
    """Return a portable, human-readable filesystem component."""
    cleaned = "".join(
        character if character.isalnum() or character in " .-_()[]" else "_"
        for character in str(value or "")
    ).strip(" .")
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned[:160] or fallback


def parse_duration(value, fallback: float = 240.0) -> float:
    if isinstance(value, (int, float)):
        return max(1.0, float(value))
    text = str(value or "").strip()
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return fallback
    if len(parts) == 2:
        return max(1.0, parts[0] * 60 + parts[1])
    if len(parts) == 3:
        return max(1.0, parts[0] * 3600 + parts[1] * 60 + parts[2])
    return fallback


class CDRipManager:
    """Rip one track at a time and expose structured progress."""

    def __init__(
        self,
        *,
        state_file: str,
        default_destination: str,
        default_automatic: bool = False,
        default_autoplay: bool = True,
        default_autoeject: bool = True,
        on_update: UpdateCallback | None = None,
        on_track_ready: TrackCallback | None = None,
    ):
        self.state_file = Path(state_file)
        self.on_update = on_update
        self.on_track_ready = on_track_ready
        self.settings = {
            "automatic": bool(default_automatic),
            "autoplay": bool(default_autoplay),
            "autoeject": bool(default_autoeject),
            "destination": str(default_destination),
        }
        self.status = self._idle_status()
        self._task: asyncio.Task | None = None
        self._tasks = BackgroundTaskSet(log, label="cd-rip")
        self._process: asyncio.subprocess.Process | None = None
        self._cancel_requested = False
        self._track_paths: dict[int, Path] = {}
        self._load_settings()

    def _idle_status(self) -> dict:
        return {
            "active": False,
            "status": "idle",
            "phase": "idle",
            "message": "Ready to rip",
            "error": "",
            "percent": 0.0,
            "track": 0,
            "track_title": "",
            "total_tracks": 0,
            "completed_tracks": 0,
            "destination": "",
            "album_path": "",
            "playback_source": "disc",
            "current_speed_x": 0.0,
            "current_speed_message": "Drive idle",
            "resumable": False,
            "started_at": 0,
            "finished_at": 0,
            "prompt": None,
        }

    def _load_settings(self):
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self.settings["automatic"] = bool(payload.get("automatic", False))
                if "autoplay" in payload:
                    self.settings["autoplay"] = bool(payload["autoplay"])
                if "autoeject" in payload:
                    self.settings["autoeject"] = bool(payload["autoeject"])
                destination = str(payload.get("destination") or "").strip()
                if destination:
                    self.settings["destination"] = destination
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return

    def save_settings(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(self.settings, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp_path, self.state_file)

    async def set_settings(
        self, *, automatic=None, autoplay=None, autoeject=None, destination=None
    ):
        if automatic is not None:
            self.settings["automatic"] = bool(automatic)
        if autoplay is not None:
            self.settings["autoplay"] = bool(autoplay)
        if autoeject is not None:
            self.settings["autoeject"] = bool(autoeject)
        if destination is not None:
            self.settings["destination"] = str(destination)
        await asyncio.to_thread(self.save_settings)
        await self._notify()

    def snapshot(self) -> dict:
        encoder = "flac" if shutil.which("flac") else (
            "ffmpeg" if shutil.which("ffmpeg") else ""
        )
        return {
            **self.status,
            "active": self.active,
            "settings": dict(self.settings),
            "capabilities": {
                "cdparanoia": bool(shutil.which("cdparanoia")),
                "flac": bool(encoder),
                "encoder": encoder,
            },
        }

    async def reset_for_disc(self):
        """Clear disc-specific state without changing persisted settings."""
        if self.active:
            await self.cancel()
        self._track_paths = {}
        self.status = self._idle_status()
        await self._notify()

    async def discover_partial(self, metadata: dict, destination: str | None = None) -> bool:
        """Restore completed-track state for an interrupted rip of this disc."""
        if self.active or not metadata:
            return False

        destination_path = Path(
            destination or self.settings["destination"]
        ).expanduser()
        artist = safe_component(metadata.get("artist"), "Unknown Artist")
        album = safe_component(metadata.get("title"), "Unknown Album")
        year = str(metadata.get("year") or "").strip()
        album_folder = safe_component(f"{album} ({year})" if year else album)
        disc_id = str(metadata.get("disc_id") or "")
        base = destination_path / artist / album_folder
        candidates = [base]
        alternate = destination_path / artist / (
            f"{album_folder} [CD {safe_component(disc_id[:8], 'alternate')}]"
        )
        if alternate != base:
            candidates.append(alternate)

        album_path = None
        for candidate in candidates:
            if not candidate.is_dir():
                continue
            manifest = {}
            try:
                manifest = json.loads(
                    (candidate / ".bs5c-disc.json").read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                pass
            same_disc = bool(disc_id and manifest.get("disc_id") == disc_id)
            legacy_partial = (
                (candidate / ".bs5c-rip-work").is_dir()
                and (not manifest.get("disc_id") or same_disc)
            )
            manifest_partial = same_disc and manifest.get("status") == "partial"
            if manifest_partial or legacy_partial:
                album_path = candidate
                break

        if album_path is None:
            return False

        tracks = list(metadata.get("tracks") or [])
        restored_paths: dict[int, Path] = {}
        completed_duration = 0.0
        total_duration = sum(parse_duration(track.get("duration")) for track in tracks)
        for track_number, track in enumerate(tracks, start=1):
            title = safe_component(track.get("title"), f"Track {track_number}")
            expected = album_path / f"{track_number:02d} - {title}.flac"
            existing = self._find_completed_track(album_path, track_number, expected)
            if existing:
                restored_paths[track_number] = existing
                completed_duration += parse_duration(track.get("duration"))

        self._track_paths = restored_paths
        completed = len(restored_paths)
        self.status.update(
            active=False,
            status="partial",
            phase="idle",
            message=f"Partial rip found — {completed} of {len(tracks)} tracks complete",
            error="",
            percent=min(99.0, completed_duration / max(1.0, total_duration) * 100),
            track=0,
            track_title="",
            total_tracks=len(tracks),
            completed_tracks=completed,
            destination=str(destination_path),
            album_path=str(album_path),
            playback_source="ripped_file" if completed else "disc",
            current_speed_x=0.0,
            current_speed_message="Ready to resume",
            resumable=True,
            prompt=None,
        )
        await self._notify()
        return True

    def set_prompt(self, prompt: dict | None):
        self.status["prompt"] = prompt
        if prompt:
            self.status.update(
                status="awaiting_confirmation",
                phase="duplicate_check",
                message=prompt.get("message") or "Confirmation required",
            )
        elif self.status.get("status") == "awaiting_confirmation":
            self.status.update(status="idle", phase="idle", message="Ready to rip")

    async def notify_prompt(self, prompt: dict | None):
        self.set_prompt(prompt)
        await self._notify()

    async def mark_skipped(self, message: str):
        self.status.update(
            active=False,
            status="skipped",
            phase="complete",
            message=message,
            error="",
            prompt=None,
            finished_at=time.time(),
        )
        await self._notify()

    async def _notify(self):
        if not self.on_update:
            return
        result = self.on_update(self.snapshot())
        if asyncio.iscoroutine(result):
            await result

    async def _track_ready(self, track_number: int, path: Path):
        self._track_paths[track_number] = path
        if not self.on_track_ready:
            return
        result = self.on_track_ready(track_number, path)
        if asyncio.iscoroutine(result):
            await result

    def completed_track_path(self, track_number: int) -> Path | None:
        path = self._track_paths.get(int(track_number))
        return path if path and path.exists() else None

    @property
    def active(self) -> bool:
        return bool(self._task and not self._task.done())

    async def wait_for_track(self, track_number: int, timeout: float = 180.0) -> Path | None:
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        while asyncio.get_running_loop().time() < deadline:
            ready = self.completed_track_path(track_number)
            if ready:
                return ready
            if not self.active and self.status.get("status") in {"error", "cancelled"}:
                return None
            await asyncio.sleep(0.25)
        return self.completed_track_path(track_number)

    async def start(
        self,
        *,
        metadata: dict,
        device_path: str,
        destination: str | None = None,
        start_track: int = 1,
    ) -> bool:
        if self.active:
            return False
        if not shutil.which("cdparanoia"):
            await self._fail("cdparanoia is not installed")
            return False
        if not (shutil.which("flac") or shutil.which("ffmpeg")):
            await self._fail("Neither flac nor ffmpeg is installed")
            return False

        self._cancel_requested = False
        self._track_paths = {}
        destination_path = Path(destination or self.settings["destination"]).expanduser()
        tracks = list((metadata or {}).get("tracks") or [])
        self.status.update(
            active=True,
            status="preparing",
            phase="preparing",
            message="Preparing CD rip",
            error="",
            percent=0.0,
            track=0,
            track_title="",
            total_tracks=len(tracks),
            completed_tracks=0,
            destination=str(destination_path),
            album_path="",
            playback_source="preparing_ripped_file",
            current_speed_x=0.0,
            current_speed_message="Preparing optical drive",
            resumable=False,
            started_at=time.time(),
            finished_at=0,
            prompt=None,
        )
        self._task = self._tasks.spawn(
            self._run(
                metadata=dict(metadata or {}),
                device_path=device_path,
                destination=destination_path,
                start_track=max(1, int(start_track or 1)),
            ),
            name="cd_rip",
        )
        await self._notify()
        return True

    async def cancel(self):
        self._cancel_requested = True
        process = self._process
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.status.update(
            active=False,
            status="cancelled",
            phase="complete",
            message="Rip cancelled",
            error="",
            prompt=None,
            current_speed_x=0.0,
            current_speed_message="Drive idle",
            resumable=bool(self.status.get("album_path")),
            finished_at=time.time(),
        )
        await self._notify()

    async def _run(
        self,
        *,
        metadata: dict,
        device_path: str,
        destination: Path,
        start_track: int,
    ):
        try:
            tracks = list(metadata.get("tracks") or [])
            if not tracks:
                raise RuntimeError("No CD track list is available")

            artist = safe_component(metadata.get("artist"), "Unknown Artist")
            album = safe_component(metadata.get("title"), "Unknown Album")
            year = str(metadata.get("year") or "").strip()
            album_folder = safe_component(f"{album} ({year})" if year else album)
            album_path = await asyncio.to_thread(
                self._choose_album_path,
                destination,
                artist,
                album_folder,
                str(metadata.get("disc_id") or ""),
            )
            work_path = album_path / ".bs5c-rip-work"
            await asyncio.to_thread(work_path.mkdir, parents=True, exist_ok=True)
            # Persist the disc identity before track one. If power is lost or a
            # rip is cancelled, the next run can safely reopen this exact album
            # directory and reuse every completed FLAC.
            await asyncio.to_thread(
                self._write_manifest, album_path, metadata, complete=False)
            # Put conventional folder artwork in place immediately. A cancelled
            # or interrupted rip should still leave the completed tracks with a
            # useful cover for filesystem players such as Music Assistant.
            await asyncio.to_thread(self._copy_artwork, album_path, metadata)

            total_duration = sum(parse_duration(track.get("duration")) for track in tracks)
            completed_duration = 0.0
            order = list(range(start_track, len(tracks) + 1)) + list(range(1, start_track))

            self.status.update(
                active=True,
                status="preparing",
                phase="preparing",
                message="Preparing CD rip",
                error="",
                percent=0.0,
                track=0,
                track_title="",
                total_tracks=len(tracks),
                completed_tracks=0,
                destination=str(destination),
                album_path=str(album_path),
                playback_source="preparing_ripped_file",
                current_speed_x=0.0,
                current_speed_message="Preparing optical drive",
                resumable=True,
                started_at=time.time(),
                finished_at=0,
                prompt=None,
            )
            await self._notify()

            for completed_count, track_number in enumerate(order):
                if self._cancel_requested:
                    raise asyncio.CancelledError
                track = tracks[track_number - 1]
                duration = parse_duration(track.get("duration"))
                title = safe_component(track.get("title"), f"Track {track_number}")
                final_path = album_path / f"{track_number:02d} - {title}.flac"
                existing_path = self._find_completed_track(
                    album_path, track_number, final_path)
                if existing_path:
                    completed_duration += duration
                    self.status.update(
                        completed_tracks=completed_count + 1,
                        percent=min(99.0, completed_duration / total_duration * 100),
                        message=f"Using existing track {track_number}",
                        current_speed_x=0.0,
                        current_speed_message="Completed file reused",
                    )
                    await self._track_ready(track_number, existing_path)
                    await self._notify()
                    continue

                wav_path = work_path / f"track-{track_number:02d}.wav"
                part_path = work_path / f"track-{track_number:02d}.flac.part"
                for stale in (wav_path, part_path):
                    try:
                        stale.unlink()
                    except FileNotFoundError:
                        pass

                await self._extract_track(
                    device_path=device_path,
                    track_number=track_number,
                    track_title=title,
                    wav_path=wav_path,
                    duration=duration,
                    completed_duration=completed_duration,
                    total_duration=total_duration,
                )
                await self._encode_track(
                    metadata=metadata,
                    track=track,
                    track_number=track_number,
                    wav_path=wav_path,
                    part_path=part_path,
                )
                final_path = await asyncio.to_thread(
                    self._commit_track, part_path, final_path)
                completed_duration += duration
                self.status.update(
                    phase="ripping",
                    completed_tracks=completed_count + 1,
                    percent=min(99.0, completed_duration / total_duration * 100),
                    message=f"Track {track_number} ready",
                    playback_source="ripped_file",
                )
                await self._track_ready(track_number, final_path)
                await self._notify()

            await asyncio.to_thread(self._finish_album, album_path, metadata)
            self.status.update(
                active=False,
                status="completed",
                phase="complete",
                message="CD rip complete",
                error="",
                percent=100.0,
                completed_tracks=len(tracks),
                playback_source="ripped_file",
                current_speed_x=0.0,
                current_speed_message="Rip complete",
                resumable=False,
                finished_at=time.time(),
            )
            await self._notify()
        except asyncio.CancelledError:
            if not self._cancel_requested:
                raise
        except Exception as exc:
            log.exception("CD rip failed")
            await self._fail(str(exc))
        finally:
            self._process = None
            self.status["active"] = False

    async def _extract_track(
        self,
        *,
        device_path: str,
        track_number: int,
        track_title: str,
        wav_path: Path,
        duration: float,
        completed_duration: float,
        total_duration: float,
    ):
        self.status.update(
            status="ripping",
            phase="ripping",
            track=track_number,
            track_title=track_title,
            message=f"Ripping track {track_number}: {track_title}",
            current_speed_x=0.0,
            current_speed_message="Starting optical read",
        )
        await self._notify()
        self._process = await asyncio.create_subprocess_exec(
            "cdparanoia",
            "-d",
            device_path,
            "-w",
            str(track_number),
            str(wav_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        expected_bytes = max(44, int(duration * 176400) + 44)
        loop = asyncio.get_running_loop()
        sample_time = loop.time()
        sample_bytes = wav_path.stat().st_size if wav_path.exists() else 0
        smoothed_speed = 0.0
        while self._process.returncode is None:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                current_bytes = wav_path.stat().st_size if wav_path.exists() else 0
                now = loop.time()
                elapsed = max(0.001, now - sample_time)
                instant_speed = max(0.0, current_bytes - sample_bytes) / 176400 / elapsed
                if instant_speed > 0:
                    smoothed_speed = (
                        instant_speed if smoothed_speed <= 0
                        else smoothed_speed * 0.65 + instant_speed * 0.35
                    )
                sample_time = now
                sample_bytes = current_bytes
                current_fraction = min(0.99, current_bytes / expected_bytes)
                progress_duration = completed_duration + duration * current_fraction
                self.status["percent"] = min(99.0, progress_duration / total_duration * 100)
                self.status["current_speed_x"] = round(smoothed_speed, 1)
                self.status["current_speed_message"] = (
                    f"Reading at {smoothed_speed:.1f}x real time"
                    if smoothed_speed > 0 else "Reading disc"
                )
                await self._notify()
        stderr = (await self._process.stderr.read()).decode("utf-8", "replace")
        if self._cancel_requested:
            raise asyncio.CancelledError
        if self._process.returncode != 0:
            detail = " ".join(stderr.strip().splitlines()[-3:])
            raise RuntimeError(f"cdparanoia failed on track {track_number}: {detail or 'unknown error'}")
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            raise RuntimeError(f"cdparanoia produced no audio for track {track_number}")

    async def _encode_track(
        self,
        *,
        metadata: dict,
        track: dict,
        track_number: int,
        wav_path: Path,
        part_path: Path,
    ):
        self.status.update(
            phase="encoding",
            message=f"Encoding track {track_number} to FLAC",
            current_speed_x=0.0,
            current_speed_message="Encoding completed audio",
        )
        await self._notify()
        tags = {
            "TITLE": track.get("title") or f"Track {track_number}",
            "ARTIST": metadata.get("artist") or "Unknown Artist",
            "ALBUMARTIST": metadata.get("artist") or "Unknown Artist",
            "ALBUM": metadata.get("title") or "Unknown Album",
            "TRACKNUMBER": str(track_number),
            "TRACKTOTAL": str(len(metadata.get("tracks") or [])),
            "DATE": str(metadata.get("year") or ""),
            "MUSICBRAINZ_RELEASEID": str(metadata.get("release_id") or ""),
            "DISCID": str(metadata.get("disc_id") or ""),
        }
        artwork = Path(str(metadata.get("artwork_file") or "").strip())
        has_artwork = artwork.is_file()
        if shutil.which("flac"):
            command = ["flac", "--silent", "--verify", "--best", "--output-name", str(part_path)]
            command.extend(f"--tag={key}={value}" for key, value in tags.items() if value)
            if has_artwork:
                command.append(f"--picture={artwork}")
            command.append(str(wav_path))
        else:
            command = ["ffmpeg", "-v", "error", "-y", "-i", str(wav_path)]
            if has_artwork:
                command.extend(("-i", str(artwork), "-map", "0:a", "-map", "1:v"))
            command.extend(("-c:a", "flac", "-compression_level", "12"))
            if has_artwork:
                command.extend((
                    "-c:v", "copy",
                    "-disposition:v", "attached_pic",
                    "-metadata:s:v", "title=Album cover",
                    "-metadata:s:v", "comment=Cover (front)",
                ))
            for key, value in tags.items():
                if value:
                    command.extend(("-metadata", f"{key}={value}"))
            command.extend(("-f", "flac", str(part_path)))
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await self._process.communicate()
        if self._process.returncode != 0:
            detail = " ".join(stderr.decode("utf-8", "replace").strip().splitlines()[-3:])
            raise RuntimeError(f"FLAC encoding failed on track {track_number}: {detail or 'unknown error'}")
        try:
            wav_path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _commit_track(part_path: Path, final_path: Path) -> Path:
        """Atomically publish a track, falling back on limited filesystems."""
        try:
            os.replace(part_path, final_path)
            return final_path
        except OSError as exc:
            # Some CIFS/FAT-backed MASS libraries reject otherwise valid
            # Unicode names at rename time. Keep the exact title in FLAC tags
            # and use an ASCII filename only for that specific limitation.
            if exc.errno not in {errno.EINVAL, errno.EILSEQ}:
                raise
            ascii_stem = unicodedata.normalize('NFKD', final_path.stem)
            ascii_stem = ascii_stem.encode('ascii', 'ignore').decode('ascii')
            fallback_name = f"{safe_component(ascii_stem, 'Track')}{final_path.suffix}"
            fallback_path = final_path.with_name(fallback_name)
            if fallback_path == final_path:
                raise
            log.warning(
                "Library filesystem rejected %s; using filename %s",
                final_path.name,
                fallback_path.name,
            )
            os.replace(part_path, fallback_path)
            return fallback_path

    @staticmethod
    def _find_completed_track(
        album_path: Path, track_number: int, expected_path: Path
    ) -> Path | None:
        """Find a reusable completed FLAC, including ASCII fallback names."""
        candidates = [expected_path]
        candidates.extend(sorted(album_path.glob(f"{track_number:02d} - *.flac")))
        seen = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            try:
                if candidate.is_file() and candidate.stat().st_size > 4096:
                    return candidate
            except OSError:
                continue
        return None

    @staticmethod
    def _choose_album_path(destination: Path, artist: str, album: str, disc_id: str) -> Path:
        base = destination / artist / album
        if not base.exists():
            base.mkdir(parents=True, exist_ok=True)
            return base
        manifest_path = base / ".bs5c-disc.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if disc_id and manifest.get("disc_id") == disc_id:
                return base
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        # Older interrupted rips predate the early manifest above. Their work
        # directory is still a strong, BS5C-specific resume marker.
        if (base / ".bs5c-rip-work").is_dir() or not any(base.iterdir()):
            return base
        suffix = safe_component(disc_id[:8], "alternate")
        alternate = destination / artist / f"{album} [CD {suffix}]"
        alternate.mkdir(parents=True, exist_ok=True)
        return alternate

    @staticmethod
    def _finish_album(album_path: Path, metadata: dict):
        CDRipManager._write_manifest(album_path, metadata, complete=True)
        work_path = album_path / ".bs5c-rip-work"
        try:
            work_path.rmdir()
        except OSError:
            pass

        CDRipManager._copy_artwork(album_path, metadata)

    @staticmethod
    def _write_manifest(album_path: Path, metadata: dict, *, complete: bool):
        manifest = {
            "disc_id": metadata.get("disc_id") or "",
            "release_id": metadata.get("release_id") or "",
            "artist": metadata.get("artist") or "Unknown Artist",
            "album": metadata.get("title") or "Unknown Album",
            "year": metadata.get("year") or "",
            "track_count": len(metadata.get("tracks") or []),
            "status": "complete" if complete else "partial",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if complete:
            manifest["ripped_at"] = manifest["updated_at"]
        manifest_path = album_path / ".bs5c-disc.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    @staticmethod
    def _copy_artwork(album_path: Path, metadata: dict):
        artwork = str(metadata.get("artwork_file") or "").strip()
        if not artwork or not Path(artwork).is_file():
            return
        # cover.jpg is the common Linux/MASS convention; folder.jpg also
        # covers players and file browsers that only look for that name.
        for filename in ("cover.jpg", "folder.jpg"):
            destination = album_path / filename
            if Path(artwork).resolve() != destination.resolve():
                shutil.copyfile(artwork, destination)

    async def _fail(self, message: str):
        self.status.update(
            active=False,
            status="error",
            phase="error",
            message="CD rip failed",
            error=str(message),
            prompt=None,
            current_speed_x=0.0,
            current_speed_message="Drive idle",
            resumable=bool(self.status.get("album_path")),
            finished_at=time.time(),
        )
        await self._notify()
