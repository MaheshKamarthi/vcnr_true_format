# VCNR Browser Streaming Media v3

VCNR v3 compresses normal video with H.264/AAC and protects the compressed
media with a passcode. It plays in both the Windows desktop player and modern
web browsers.

## File design

```text
Input video
  -> FFmpeg H.264 video + AAC audio compression
  -> fragmented media stream
  -> PBKDF2 password key + AES-256-GCM encrypted chunks
  -> .vcnr file
```

The `.vcnr` file is not directly playable by an MP4 player. A VCNR player
derives an encryption key from the entered passcode, authenticates every
chunk, and streams decrypted media directly to FFplay or the browser Media
Source engine. It does not create a permanent decrypted MP4 file.

The public header contains descriptive metadata such as title, owner, codecs,
and compression settings. The compressed audio/video content is encrypted.
The passcode is never stored in the file.

## Requirements

- Windows and Python 3
- FFmpeg and FFplay available on `PATH`
- Python package `cryptography`

Run:

```bat
setup_windows.bat
```

## Convert

Run:

```bat
VCNR_True_Converter.bat
```

Compression controls:

- `CRF 18-20`: high quality, larger file
- `CRF 23`: recommended balance
- `CRF 26-28`: smaller file, lower quality
- A slower preset improves compression but takes longer
- Maximum width prevents unnecessarily large video output

Keep the passcode safely. It cannot be recovered from the VCNR file.

## Play

Run:

```bat
VCNR_True_Player.bat
```

Select the VCNR file, enter its passcode, and choose **Unlock & Play**.
Decrypted bytes are streamed to FFplay through a pipe.

## Play directly in a browser

Run:

```bat
VCNR_Browser_Player.bat
```

This starts the included local server and opens:

```text
http://127.0.0.1:8000/web_player/
```

To let other devices on your network open the same player page, start it with:

```bat
VCNR_Browser_Player.bat --host 0.0.0.0
```

The server prints the local and LAN URLs that can reach `/web_player/`.

Choose a local `.vcnr` file, enter its passcode, and select **Unlock & play**.
The browser uses native Web Crypto to derive the key and decrypt AES-GCM
chunks in memory. The player reconstructs complete fragmented-MP4 segments
before passing them to the browser video element. It reads the exact H.264
profile and level from each file instead of assuming a fixed browser codec.

If a browser rejects fragmented Media Source initialization, the player
automatically switches to compatibility mode. It finishes decrypting the
video in browser memory and opens it as a temporary Blob. Nothing is written
to disk, although playback begins after the full file is downloaded in this
fallback mode.

The page must be opened through `http://` or `https://`; do not double-click
`index.html` and use a `file://` address because browser security features may
be unavailable there.

## Online browser streaming

You can host the browser player itself online too. Upload the contents of
`web_player/` to any normal static web host or web server, then open the hosted
`index.html` over HTTPS.

### GitHub Pages setup

This project now includes a GitHub Pages deployment path for the browser player
and optional hosted `.vcnr` files.

Files involved:

- `build_github_pages.py` builds a publishable static site into `site/`
- `.github/workflows/deploy-pages.yml` deploys that `site/` folder to GitHub Pages
- `public_vcnr/` is where you place `.vcnr` files that should be published

Workflow:

1. Put browser-playable `.vcnr` files in `public_vcnr/`
2. Push the repository to GitHub
3. Make sure the default branch is `main` or `master`
4. In GitHub, enable **Pages** with **GitHub Actions** as the source
5. Push again or run the **Deploy GitHub Pages** workflow manually

For a local preview of the generated Pages site:

```bat
py -3 build_github_pages.py
py -3 -m http.server 8020 -d site
```

Then open:

```text
http://127.0.0.1:8020/
```

If you upload `public_vcnr/movie.vcnr`, the hosted file address becomes:

```text
https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/media/movie.vcnr
```

The hosted player becomes:

```text
https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/
```

If you want the player UI to show a **Load sample file** button automatically,
publish a file as:

```text
public_vcnr/sample.vcnr
```

After deployment, the player checks for `/media/sample.vcnr` and shows the
button when that file exists.

You can also share a direct hosted-player link with the VCNR URL prefilled:

```text
https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/?url=https%3A%2F%2FYOUR-USERNAME.github.io%2FYOUR-REPOSITORY%2Fmedia%2Fmovie.vcnr
```

You can also override the sample button with a custom sample URL:

```text
https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/?sample=https%3A%2F%2Fexample.com%2Fmovie.vcnr
```

Upload the `.vcnr` file to any normal HTTP/HTTPS file host or web server. The
server does not need the passcode and does not decrypt the video.

In either the desktop or browser player, paste the complete file address:

```text
https://media.example.com/videos/movie.vcnr
```

You can also share a hosted player link with the VCNR address prefilled:

```text
https://player.example.com/index.html?url=https%3A%2F%2Fmedia.example.com%2Fvideos%2Fmovie.vcnr
```

Enter the passcode and choose **Unlock & play**. Playback starts after the
first encrypted chunk is downloaded and authenticated. Remaining chunks are
downloaded, decrypted, and sent to FFplay progressively; the entire VCNR file
does not need to be downloaded before playback begins.

Recommended server settings:

- Use HTTPS.
- Serve `.vcnr` as `application/octet-stream`.
- Allow long-running `GET` responses.
- If the player page uses HTTPS, the `.vcnr` URL must use HTTPS too.
- Disable automatic compression for `.vcnr` because its encrypted bytes
  cannot be compressed further.
- A CDN can cache and distribute the encrypted file safely; the passcode must
  be delivered separately.
- If the player page and VCNR file use different domains, the file server must
  return `Access-Control-Allow-Origin` for the player domain (or `*` for a
  public encrypted-file host).

For a quick LAN test from the directory containing a VCNR file:

```bat
py -3 -m http.server 8000
```

Other computers on the same network can use:

```text
http://YOUR-COMPUTER-IP:8000/movie.vcnr
```

Windows Firewall may ask for permission. Python's simple HTTP server is only
for testing, not production hosting.

## Secure backend streaming

This project now also includes a server-side playback MVP so the VCNR passcode
stays on the backend and authorized users stream normal video in the browser.

Files:

- `vcnr_secure_backend.py`: FastAPI app with login, catalog, and playback endpoints
- `secure_player/`: browser UI for login and playback
- `backend_config.json`: local backend config with users and private video entries
- `backend_config.example.json`: example config template
- `videos_private/`: private VCNR storage for backend-only files

Install the backend dependencies:

```bat
py -3 -m pip install -r requirements.txt
```

Add one or more private `.vcnr` files under:

```text
videos_private/
```

Then edit `backend_config.json`:

- Change the default `admin` password immediately
- Add video entries with `id`, `path`, and the server-side `passcode`
- Assign allowed video ids to each user, or use `["*"]` for full access

Example video entry:

```json
{
  "id": "movie-1",
  "path": "videos_private/movie-1.vcnr",
  "passcode": "server-only-vcnr-passcode",
  "title": "Movie 1",
  "description": "Only authorized users can stream this file."
}
```

Run:

```bat
VCNR_Secure_Backend.bat
```

Or directly:

```bat
py -3 -m uvicorn vcnr_secure_backend:app --host 127.0.0.1 --port 8030
```

Open:

```text
http://127.0.0.1:8030/
```

Current MVP behavior:

- Users sign in with a backend-managed account
- The browser only sees the normal streamed video, not the VCNR passcode
- Access is limited to videos assigned in `backend_config.json`
- Playback sessions are short-lived and created per request
- Seeking and advanced adaptive streaming are not implemented yet

Security notes:

- This is stronger than browser-side passcode entry, but it is not full DRM
- Users can still screen-record playback
- Keep `.vcnr` files in `videos_private/` and do not expose that folder publicly
- Replace the default admin password before using the backend

## Build EXE files

```bat
build_converter_exe.bat
build_player_exe.bat
```

FFmpeg and FFplay are still required unless they are separately bundled with
the application.

## Security boundary

Copying or downloading a VCNR file does not reveal playable media without the
passcode. AES-GCM also detects modified or damaged encrypted chunks.

The file host only sees encrypted VCNR bytes. Use HTTPS as well so observers
cannot see public metadata or the exact content being requested.

Like every offline media protection system, VCNR cannot prevent an authorized
viewer from recording the screen or attempting to capture media after it has
been decrypted for playback.

## Compatibility

VCNR v3 uses the `VCNRCMP3` format signature and browser-native
PBKDF2-HMAC-SHA256. The v3 players intentionally reject the older v1 and v2
prototype formats. Convert the original source video again to create v3 files.
