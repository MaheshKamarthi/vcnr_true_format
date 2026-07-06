import json
import secrets
import shutil
import subprocess
import threading
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vcnr_true_core import TrueVCNRReader, check_ffmpeg, read_header


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "backend_config.json"
STATIC_DIR = ROOT / "secure_player"
SESSION_COOKIE = "vcnr_session"
DEFAULT_SESSION_TTL = 3600
DEFAULT_PLAYBACK_TTL = 300
HLS_SEGMENT_SECONDS = 4
HLS_START_TIMEOUT = 8.0

app = FastAPI(title="VCNR Secure Backend")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

state_lock = threading.Lock()
app_state: Dict[str, Any] = {
    "config": None,
    "users": {},
    "videos": {},
    "sessions": {},
    "playback_sessions": {},
}


class LoginPayload(BaseModel):
    username: str
    password: str


def _now() -> int:
    return int(time.time())


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _cleanup_expired_sessions() -> None:
    now = _now()
    expired_playback_sessions = []
    with state_lock:
        app_state["sessions"] = {
            session_id: session
            for session_id, session in app_state["sessions"].items()
            if session["expires_at"] > now
        }
        active_playback_sessions = {}
        for session_id, session in app_state["playback_sessions"].items():
            if session["expires_at"] > now:
                active_playback_sessions[session_id] = session
            else:
                expired_playback_sessions.append(session)
        app_state["playback_sessions"] = active_playback_sessions
    for session in expired_playback_sessions:
        _destroy_playback_session(session)


def _load_backend_state() -> None:
    if not CONFIG_PATH.is_file():
        raise RuntimeError(
            f"Missing backend config: {CONFIG_PATH}. Copy and edit backend_config.json."
        )

    config = _read_json(CONFIG_PATH)
    session_ttl = int(config.get("session_ttl_seconds", DEFAULT_SESSION_TTL))
    playback_ttl = int(config.get("playback_ttl_seconds", DEFAULT_PLAYBACK_TTL))

    users = {}
    for raw_user in config.get("users", []):
        username = str(raw_user.get("username", "")).strip()
        password = str(raw_user.get("password", ""))
        if not username or not password:
            raise RuntimeError("Each backend user needs a username and password.")
        if username in users:
            raise RuntimeError(f"Duplicate backend user: {username}")
        users[username] = {
            "username": username,
            "password": password,
            "display_name": str(raw_user.get("display_name") or username),
            "videos": list(raw_user.get("videos", [])),
        }

    videos = {}
    for raw_video in config.get("videos", []):
        video_id = str(raw_video.get("id", "")).strip()
        relative_path = str(raw_video.get("path", "")).strip()
        passcode = str(raw_video.get("passcode", ""))
        if not video_id or not relative_path or not passcode:
            raise RuntimeError("Each backend video needs id, path, and passcode.")
        if video_id in videos:
            raise RuntimeError(f"Duplicate backend video id: {video_id}")
        file_path = (ROOT / relative_path).resolve()
        if not file_path.is_file():
            raise RuntimeError(f"Configured VCNR file not found: {file_path}")
        header = read_header(str(file_path))
        videos[video_id] = {
            "id": video_id,
            "path": str(file_path),
            "passcode": passcode,
            "title": str(raw_video.get("title") or header.get("title") or file_path.stem),
            "description": str(raw_video.get("description") or ""),
            "header": header,
        }

    with state_lock:
        app_state["config"] = {
            "session_ttl_seconds": session_ttl,
            "playback_ttl_seconds": playback_ttl,
        }
        app_state["users"] = users
        app_state["videos"] = videos
        app_state["sessions"] = {}
        app_state["playback_sessions"] = {}


def _current_user(request: Request) -> Dict[str, Any]:
    _cleanup_expired_sessions()
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required.")

    with state_lock:
        session = app_state["sessions"].get(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired.")
        user = app_state["users"].get(session["username"])
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
        return user


def _public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "username": user["username"],
        "display_name": user["display_name"],
    }


def _video_allowed(user: Dict[str, Any], video_id: str) -> bool:
    allowed = user.get("videos", [])
    return "*" in allowed or video_id in allowed


def _public_video(video: Dict[str, Any]) -> Dict[str, Any]:
    header = video["header"]
    return {
        "id": video["id"],
        "title": video["title"],
        "description": video["description"],
        "metadata": {
            "owner": header.get("owner", ""),
            "original_name": header.get("original_name", ""),
            "plain_size": header.get("plain_size", 0),
            "chunk_count": header.get("chunk_count", 0),
            "video_codec": header.get("video_codec", ""),
            "audio_codec": header.get("audio_codec", ""),
        },
    }


def _destroy_playback_session(playback_session: Dict[str, Any]) -> None:
    process = playback_session.get("process")
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    temp_dir = playback_session.get("temp_dir")
    if temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _start_hls_transcode(playback_id: str, video: Dict[str, Any]) -> None:
    temp_dir = tempfile.mkdtemp(prefix=f"vcnr_hls_{video['id']}_")
    playlist_path = Path(temp_dir) / "playlist.m3u8"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        str(HLS_SEGMENT_SECONDS),
        "-hls_playlist_type",
        "event",
        "-hls_flags",
        "independent_segments+temp_file",
        "-hls_segment_filename",
        str(Path(temp_dir) / "segment_%05d.ts"),
        str(playlist_path),
    ]

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    with state_lock:
        playback_session = app_state["playback_sessions"].get(playback_id)
        if not playback_session:
            _destroy_playback_session({"process": process, "temp_dir": temp_dir})
            return
        playback_session.update(
            {
                "temp_dir": temp_dir,
                "playlist_path": str(playlist_path),
                "process": process,
                "ready": False,
                "error": None,
            }
        )

    worker = threading.Thread(
        target=_run_hls_transcode,
        args=(playback_id, video, process, playlist_path),
        daemon=True,
    )
    worker.start()
    with state_lock:
        playback_session = app_state["playback_sessions"].get(playback_id)
        if playback_session:
            playback_session["worker"] = worker


def _run_hls_transcode(
    playback_id: str,
    video: Dict[str, Any],
    process: subprocess.Popen,
    playlist_path: Path,
) -> None:
    error: Optional[str] = None
    try:
        if process.stdin is None:
            raise RuntimeError("FFmpeg HLS pipeline did not expose stdin.")
        with TrueVCNRReader(video["path"], video["passcode"]) as reader:
            for chunk in reader.iter_decrypted_chunks():
                with state_lock:
                    playback_session = app_state["playback_sessions"].get(playback_id)
                if not playback_session:
                    break
                process.stdin.write(chunk)
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
    except BrokenPipeError:
        pass
    except Exception as exc:
        error = f"Could not prepare HLS stream: {exc}"
        if process.poll() is None:
            process.terminate()
    finally:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()

    stderr_output = ""
    if process.stderr is not None:
        try:
            stderr_output = process.stderr.read().decode("utf-8", errors="replace")
        except Exception:
            stderr_output = ""
    return_code = process.wait()
    if return_code and not error:
        error = stderr_output.strip() or f"FFmpeg exited with code {return_code}."

    with state_lock:
        playback_session = app_state["playback_sessions"].get(playback_id)
        if playback_session:
            playback_session["ready"] = playlist_path.is_file()
            playback_session["error"] = error


def _playback_context(playback_id: str, request: Request):
    user = _current_user(request)
    _cleanup_expired_sessions()
    with state_lock:
        playback_session = app_state["playback_sessions"].get(playback_id)
        if not playback_session or playback_session["username"] != user["username"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Playback session not found.",
            )
        video = app_state["videos"].get(playback_session["video_id"])
    if not video:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found.")
    return playback_session, video


def _resolve_hls_asset(
    playback_id: str,
    request: Request,
    asset_name: str,
    timeout_seconds: float = HLS_START_TIMEOUT,
) -> Path:
    if asset_name in {"", ".", ".."} or "/" in asset_name or "\\" in asset_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HLS asset not found.")

    deadline = time.monotonic() + timeout_seconds
    while True:
        playback_session, _video = _playback_context(playback_id, request)
        temp_dir = playback_session.get("temp_dir")
        if temp_dir:
            asset_path = (Path(temp_dir) / asset_name).resolve()
            try:
                asset_path.relative_to(Path(temp_dir).resolve())
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="HLS asset not found.",
                ) from exc
            if asset_path.is_file():
                return asset_path

        if playback_session.get("error"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=playback_session["error"],
            )

        if time.monotonic() >= deadline:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="HLS stream is still preparing. Retry in a moment.",
            )
        time.sleep(0.15)


def _iter_video_stream(video: Dict[str, Any]):
    reader = TrueVCNRReader(video["path"], video["passcode"])
    try:
        for chunk in reader.iter_decrypted_chunks():
            yield chunk
    finally:
        reader.close()


@app.on_event("startup")
def startup_event():
    _load_backend_state()


@app.on_event("shutdown")
def shutdown_event():
    with state_lock:
        playback_sessions = list(app_state["playback_sessions"].values())
        app_state["playback_sessions"] = {}
    for playback_session in playback_sessions:
        _destroy_playback_session(playback_session)


@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return _public_user(_current_user(request))


@app.post("/api/login")
def login(payload: LoginPayload, response: Response):
    _cleanup_expired_sessions()
    with state_lock:
        user = app_state["users"].get(payload.username)
        config = app_state["config"] or {}
    if not user or user["password"] != payload.password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    session_id = secrets.token_urlsafe(32)
    expires_at = _now() + int(config.get("session_ttl_seconds", DEFAULT_SESSION_TTL))
    with state_lock:
        app_state["sessions"][session_id] = {
            "username": user["username"],
            "expires_at": expires_at,
        }

    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="lax",
        max_age=int(config.get("session_ttl_seconds", DEFAULT_SESSION_TTL)),
    )
    return {"user": _public_user(user)}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        with state_lock:
            app_state["sessions"].pop(session_id, None)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/videos")
def list_videos(request: Request):
    user = _current_user(request)
    with state_lock:
        videos = list(app_state["videos"].values())
    return {
        "videos": [
            _public_video(video)
            for video in videos
            if _video_allowed(user, video["id"])
        ]
    }


@app.post("/api/videos/{video_id}/play")
def create_playback(video_id: str, request: Request):
    user = _current_user(request)
    with state_lock:
        video = app_state["videos"].get(video_id)
        config = app_state["config"] or {}
    if not video or not _video_allowed(user, video_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found.")

    playback_id = secrets.token_urlsafe(24)
    expires_in = int(config.get("playback_ttl_seconds", DEFAULT_PLAYBACK_TTL))
    with state_lock:
        app_state["playback_sessions"][playback_id] = {
            "username": user["username"],
            "video_id": video_id,
            "expires_at": _now() + expires_in,
            "temp_dir": None,
            "playlist_path": None,
            "process": None,
            "worker": None,
            "ready": False,
            "error": None,
        }

    playlist_url = None
    fallback_url = f"/api/playback/{playback_id}"
    if check_ffmpeg():
        try:
            _start_hls_transcode(playback_id, video)
            playlist_url = f"/api/playback/{playback_id}/playlist.m3u8"
        except Exception:
            playlist_url = None

    return {
        "playback_url": playlist_url or fallback_url,
        "playlist_url": playlist_url,
        "fallback_url": fallback_url,
        "stream_type": "hls" if playlist_url else "progressive",
        "expires_in": expires_in,
        "title": video["title"],
    }


@app.get("/api/playback/{playback_id}/playlist.m3u8")
def playback_playlist(playback_id: str, request: Request):
    playlist_path = _resolve_hls_asset(playback_id, request, "playlist.m3u8")
    return FileResponse(
        playlist_path,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/playback/{playback_id}/{asset_name}")
def playback_hls_asset(playback_id: str, asset_name: str, request: Request):
    if asset_name == "playlist.m3u8":
        return playback_playlist(playback_id, request)
    if not (
        asset_name.endswith(".ts")
        or asset_name.endswith(".m4s")
        or asset_name.endswith(".mp4")
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HLS asset not found.")

    asset_path = _resolve_hls_asset(playback_id, request, asset_name)
    return FileResponse(
        asset_path,
        media_type="video/mp2t" if asset_name.endswith(".ts") else "video/mp4",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/playback/{playback_id}")
def playback(playback_id: str, request: Request):
    _playback_session, video = _playback_context(playback_id, request)

    headers = {
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
        "Content-Disposition": f'inline; filename="{video["id"]}.mp4"',
    }
    return StreamingResponse(
        _iter_video_stream(video),
        media_type="video/mp4",
        headers=headers,
    )
