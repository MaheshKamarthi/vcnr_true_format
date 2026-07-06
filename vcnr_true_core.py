import json
import hashlib
import os
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


MAGIC = b"VCNRCMP3"
VERSION = 3
SALT_SIZE = 16
KEY_SIZE = 32
NONCE_SIZE = 12
CHUNK_SIZE = 1024 * 1024
KDF_ITERATIONS = 600_000
END_MARKER = 0xFFFFFFFF
MAX_HEADER_SIZE = 1024 * 1024
MAX_CHUNK_SIZE = CHUNK_SIZE + 16
HTTP_TIMEOUT = 30

# VCNR v3:
# MAGIC (8), VERSION (uint32 BE), HEADER_LEN (uint32 BE), SALT (16), HEADER JSON
# Repeated encrypted blocks:
#   CHUNK_ID (uint32 BE), PLAIN_LEN (uint32 BE), NONCE (12),
#   CIPHER_LEN (uint32 BE), AES-GCM CIPHER
# END_MARKER (uint32 BE)


def derive_key(
    passcode: str, salt: bytes, iterations: int = KDF_ITERATIONS
) -> bytes:
    if not passcode:
        raise ValueError("Passcode required")
    if not 100_000 <= iterations <= 2_000_000:
        raise ValueError("Invalid PBKDF2 iteration count")
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=iterations,
    ).derive(passcode.encode("utf-8"))


def _tool_available(name: str) -> bool:
    try:
        subprocess.run(
            [name, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def check_ffmpeg() -> bool:
    return _tool_available("ffmpeg")


def check_ffplay() -> bool:
    return _tool_available("ffplay")


def _run_ffmpeg(cmd, log=None):
    if log:
        log("Compressing with FFmpeg...")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    tail = []
    assert process.stderr is not None
    for line in process.stderr:
        tail.append(line)
        tail = tail[-80:]
        if log and ("frame=" in line or "time=" in line or "error" in line.lower()):
            log(line.strip())
    return_code = process.wait()
    if return_code:
        raise RuntimeError("FFmpeg compression failed:\n" + "".join(tail))


def _compress_to_fragmented_mp4(
    input_video: str,
    output_path: str,
    quality: int,
    preset: str,
    max_width: int,
    log=None,
):
    video_filter = f"scale='min(iw,{max_width})':-2" if max_width > 0 else "null"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_video,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-level:v",
        "4.0",
        "-tag:v",
        "avc1",
        "-preset",
        preset,
        "-crf",
        str(quality),
        "-pix_fmt",
        "yuv420p",
        "-force_key_frames",
        "expr:gte(t,n_forced*2)",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof",
        "-f",
        "mp4",
        output_path,
    ]
    _run_ffmpeg(cmd, log)


def create_true_vcnr(
    input_video: str,
    output_vcnr: str,
    passcode: str,
    title: str = "",
    owner: str = "",
    copy_id: str = "",
    quality: int = 23,
    preset: str = "medium",
    max_width: int = 1920,
    progress: Optional[Callable[[int, str], None]] = None,
    log: Optional[Callable[[str], None]] = None,
    **_legacy_options,
):
    if not os.path.isfile(input_video):
        raise FileNotFoundError(input_video)
    if not passcode:
        raise ValueError("Passcode required")
    if not 0 <= quality <= 51:
        raise ValueError("Quality (CRF) must be between 0 and 51")
    if preset not in {
        "ultrafast", "superfast", "veryfast", "faster", "fast",
        "medium", "slow", "slower", "veryslow",
    }:
        raise ValueError("Unsupported H.264 preset")
    if not check_ffmpeg():
        raise RuntimeError("FFmpeg not found. Install FFmpeg and add it to PATH.")

    temp_dir = tempfile.mkdtemp(prefix="vcnr_v3_")
    compressed_path = os.path.join(temp_dir, "stream.mp4")
    part_path = output_vcnr + ".part"

    try:
        if progress:
            progress(5, "Compressing H.264/AAC media")
        _compress_to_fragmented_mp4(
            input_video, compressed_path, quality, preset, max_width, log
        )
        compressed_size = os.path.getsize(compressed_path)
        if not compressed_size:
            raise RuntimeError("FFmpeg created an empty media stream")
        if progress:
            progress(70, "Encrypting compressed media")

        salt = os.urandom(SALT_SIZE)
        chunk_count = (compressed_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        header = {
            "format": "VCNR Browser Streaming Media",
            "version": VERSION,
            "title": title or Path(input_video).stem,
            "owner": owner,
            "copy_id": copy_id,
            "original_name": os.path.basename(input_video),
            "created_unix": int(time.time()),
            "passcode_stored": False,
            "encryption": "AES-256-GCM per chunk",
            "kdf": "PBKDF2-HMAC-SHA256",
            "kdf_iterations": KDF_ITERATIONS,
            "video_codec": "H.264",
            "audio_codec": "AAC",
            "media_container": "fragmented MP4 stream",
            "quality_crf": quality,
            "preset": preset,
            "max_width": max_width,
            "plain_size": compressed_size,
            "chunk_size": CHUNK_SIZE,
            "chunk_count": chunk_count,
        }
        header_bytes = json.dumps(
            header, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        header_auth = hashlib.sha256(header_bytes).digest()
        aes = AESGCM(derive_key(passcode, salt, KDF_ITERATIONS))

        with open(compressed_path, "rb") as source, open(part_path, "wb") as output:
            output.write(MAGIC)
            output.write(struct.pack(">I", VERSION))
            output.write(struct.pack(">I", len(header_bytes)))
            output.write(salt)
            output.write(header_bytes)

            for chunk_id in range(chunk_count):
                plain = source.read(CHUNK_SIZE)
                nonce = os.urandom(NONCE_SIZE)
                aad = header_auth + struct.pack(">II", VERSION, chunk_id)
                cipher = aes.encrypt(nonce, plain, aad)
                output.write(struct.pack(">II", chunk_id, len(plain)))
                output.write(nonce)
                output.write(struct.pack(">I", len(cipher)))
                output.write(cipher)
                if progress:
                    progress(
                        70 + int(29 * (chunk_id + 1) / chunk_count),
                        f"Encrypting chunk {chunk_id + 1}/{chunk_count}",
                    )

            output.write(struct.pack(">I", END_MARKER))

        os.replace(part_path, output_vcnr)
        if progress:
            progress(100, "Done")
        if log:
            log(f"Created compressed encrypted VCNR: {output_vcnr}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass


def _read_prefix(file_obj):
    magic = file_obj.read(len(MAGIC))
    if magic != MAGIC:
        raise ValueError("Not a browser-compatible VCNR v3 file")
    raw_version = file_obj.read(4)
    raw_header_len = file_obj.read(4)
    if len(raw_version) != 4 or len(raw_header_len) != 4:
        raise ValueError("Truncated VCNR file")
    version = struct.unpack(">I", raw_version)[0]
    header_len = struct.unpack(">I", raw_header_len)[0]
    if version != VERSION:
        raise ValueError(f"Unsupported VCNR version: {version}")
    if header_len > MAX_HEADER_SIZE:
        raise ValueError("Invalid VCNR header size")
    salt = file_obj.read(SALT_SIZE)
    header_bytes = file_obj.read(header_len)
    if len(salt) != SALT_SIZE or len(header_bytes) != header_len:
        raise ValueError("Truncated VCNR header")
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Damaged VCNR header") from exc
    return salt, header, hashlib.sha256(header_bytes).digest()


def is_http_url(source: str) -> bool:
    return source.lower().startswith(("http://", "https://"))


def _open_source(source: str):
    if is_http_url(source):
        request = urllib.request.Request(
            source,
            headers={
                "User-Agent": "VCNR-Player/2",
                "Accept": "application/octet-stream",
            },
        )
        try:
            return urllib.request.urlopen(request, timeout=HTTP_TIMEOUT)
        except Exception as exc:
            raise ConnectionError(f"Could not open VCNR URL: {exc}") from exc
    return open(source, "rb")


def read_header(path_or_url: str) -> Dict[str, Any]:
    with _open_source(path_or_url) as file_obj:
        _, header, _ = _read_prefix(file_obj)
        return header


class TrueVCNRReader:
    def __init__(self, path_or_url: str, passcode: str):
        self.path = path_or_url
        self.file = _open_source(path_or_url)
        self._iteration_started = False
        self._first_plain = None
        try:
            self.salt, self.header, self.header_auth = _read_prefix(self.file)
            self.aes = AESGCM(
                derive_key(
                    passcode,
                    self.salt,
                    int(self.header.get("kdf_iterations", 0)),
                )
            )
            self._verify_first_chunk()
        except Exception:
            self.file.close()
            raise

    def _read_chunk_record(self):
        raw_id = self.file.read(4)
        if len(raw_id) != 4:
            raise ValueError("Truncated VCNR data")
        chunk_id = struct.unpack(">I", raw_id)[0]
        if chunk_id == END_MARKER:
            return None
        fixed = self.file.read(4 + NONCE_SIZE + 4)
        if len(fixed) != 4 + NONCE_SIZE + 4:
            raise ValueError("Truncated VCNR chunk")
        plain_len = struct.unpack(">I", fixed[:4])[0]
        nonce = fixed[4:4 + NONCE_SIZE]
        cipher_len = struct.unpack(">I", fixed[-4:])[0]
        if plain_len > CHUNK_SIZE or cipher_len > MAX_CHUNK_SIZE:
            raise ValueError("Invalid VCNR chunk size")
        if cipher_len != plain_len + 16:
            raise ValueError("Damaged VCNR chunk")
        cipher = self.file.read(cipher_len)
        if len(cipher) != cipher_len:
            raise ValueError("Truncated VCNR chunk payload")
        return chunk_id, plain_len, nonce, cipher

    def _decrypt_record(self, record) -> bytes:
        chunk_id, plain_len, nonce, cipher = record
        aad = self.header_auth + struct.pack(">II", VERSION, chunk_id)
        try:
            plain = self.aes.decrypt(nonce, cipher, aad)
        except Exception as exc:
            raise ValueError("Wrong passcode or damaged VCNR file") from exc
        if len(plain) != plain_len:
            raise ValueError("Damaged VCNR chunk length")
        return plain

    def _verify_first_chunk(self):
        record = self._read_chunk_record()
        if record is None:
            raise ValueError("VCNR contains no media")
        if record[0] != 0:
            raise ValueError("VCNR first chunk is missing")
        self._first_plain = self._decrypt_record(record)

    def iter_decrypted_chunks(self) -> Iterator[bytes]:
        if self._iteration_started:
            raise RuntimeError("VCNR stream can only be read once")
        self._iteration_started = True
        if self._first_plain is None:
            raise ValueError("VCNR contains no media")
        yield self._first_plain
        expected_id = 1
        while True:
            record = self._read_chunk_record()
            if record is None:
                break
            if record[0] != expected_id:
                raise ValueError("VCNR chunks are missing or out of order")
            yield self._decrypt_record(record)
            expected_id += 1
        if expected_id != self.header.get("chunk_count"):
            raise ValueError("VCNR chunk count does not match header")

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
