"use strict";

const elements = {
  loginPanel: document.querySelector("#login-panel"),
  appPanel: document.querySelector("#app-panel"),
  username: document.querySelector("#username"),
  password: document.querySelector("#password"),
  login: document.querySelector("#login"),
  logout: document.querySelector("#logout"),
  welcome: document.querySelector("#welcome"),
  videoList: document.querySelector("#video-list"),
  videoTitle: document.querySelector("#video-title"),
  videoDescription: document.querySelector("#video-description"),
  player: document.querySelector("#player"),
  status: document.querySelector("#status"),
  metadata: document.querySelector("#metadata"),
};

let currentVideos = [];
let hlsPlayer = null;

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function supportsNativeHls() {
  const probe = document.createElement("video");
  return Boolean(probe.canPlayType("application/vnd.apple.mpegurl"));
}

function supportsHlsJs() {
  return Boolean(window.Hls && window.Hls.isSupported());
}

function destroyHlsPlayer() {
  if (hlsPlayer) {
    hlsPlayer.destroy();
    hlsPlayer = null;
  }
}

function resetPlayer() {
  destroyHlsPlayer();
  elements.player.pause();
  elements.player.removeAttribute("src");
  elements.player.load();
}

function setStatus(message, error = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("error", error);
}

function showApp(user) {
  elements.loginPanel.classList.add("hidden");
  elements.appPanel.classList.remove("hidden");
  elements.welcome.textContent = `Signed in as ${user.display_name} (${user.username})`;
}

function showLogin() {
  elements.appPanel.classList.add("hidden");
  elements.loginPanel.classList.remove("hidden");
  elements.videoList.innerHTML = "";
  resetPlayer();
  elements.metadata.textContent = "";
  elements.videoTitle.textContent = "Choose a video";
  elements.videoDescription.textContent = "Start a playback session from the list.";
  setStatus("Ready.");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // Keep the default HTTP detail.
    }
    throw new Error(detail);
  }
  return response.json();
}

function formatMetadata(video) {
  return JSON.stringify(video.metadata || {}, null, 2);
}

function setCurrentVideo(video) {
  elements.videoTitle.textContent = video.title;
  elements.videoDescription.textContent = video.description || "Authorized server-side playback.";
  elements.metadata.textContent = formatMetadata(video);
}

async function refreshVideos() {
  const payload = await api("/api/videos");
  currentVideos = payload.videos || [];
  elements.videoList.innerHTML = "";

  if (!currentVideos.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No videos are assigned to this account yet.";
    elements.videoList.append(empty);
    return;
  }

  for (const video of currentVideos) {
    const card = document.createElement("article");
    card.className = "video-card";

    const title = document.createElement("h3");
    title.textContent = video.title;

    const description = document.createElement("p");
    description.textContent = video.description || video.metadata.original_name || "Protected VCNR video";

    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Play";
    button.addEventListener("click", () => startPlayback(video.id));

    card.append(title, description, button);
    elements.videoList.append(card);
  }

  setCurrentVideo(currentVideos[0]);
}

async function startPlayback(videoId) {
  const video = currentVideos.find((item) => item.id === videoId);
  if (!video) return;
  setCurrentVideo(video);
  setStatus("Creating playback session...");
  resetPlayer();

  try {
    const payload = await api(`/api/videos/${encodeURIComponent(videoId)}/play`, {
      method: "POST",
      body: "{}",
    });
    const playlistUrl = payload.playlist_url;
    const fallbackUrl = payload.fallback_url || payload.playback_url;
    const canUseNativeHls = Boolean(playlistUrl) && supportsNativeHls();
    const canUseHlsJs = Boolean(playlistUrl) && supportsHlsJs();

    let playbackSuccess = false;

    if (canUseNativeHls) {
      try {
        setStatus("Preparing secure HLS stream (native)...");
        await waitForPlaylist(playlistUrl);
        elements.player.src = playlistUrl;
        await elements.player.play();
        playbackSuccess = true;
        setStatus("Streaming secure HLS video...");
      } catch (e) {
        console.warn("Native HLS failed, falling back:", e);
        resetPlayer();
      }
    }

    if (!playbackSuccess && canUseHlsJs) {
      try {
        setStatus("Preparing secure HLS stream (hls.js)...");
        await waitForPlaylist(playlistUrl);
        await attachHlsJs(playlistUrl);
        await elements.player.play();
        playbackSuccess = true;
        setStatus("Streaming secure HLS video...");
      } catch (e) {
        console.warn("hls.js failed, falling back:", e);
        resetPlayer();
      }
    }

    if (!playbackSuccess) {
      setStatus("Falling back to direct secure stream...");
      elements.player.src = fallbackUrl;
      await elements.player.play().catch(() => {});
      setStatus("Streaming decrypted video from the server...");
    }
  } catch (error) {
    setStatus(error.message || String(error), true);
  }
}

async function waitForPlaylist(url) {
  for (let attempt = 0; attempt < 24; attempt += 1) {
    const response = await fetch(url, { cache: "no-store" });
    if (response.ok) {
      return;
    }
    if (response.status !== 503) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch {
        // Keep the default HTTP detail.
      }
      throw new Error(detail);
    }
    await delay(500);
  }
  throw new Error("HLS stream did not become ready in time.");
}

function attachHlsJs(url) {
  return new Promise((resolve, reject) => {
    if (!supportsHlsJs()) {
      reject(new Error("hls.js is unavailable in this browser."));
      return;
    }

    const HlsClass = window.Hls;
    const player = new HlsClass({
      debug: false,
      enableWorker: true,
      lowLatencyMode: true,
    });
    hlsPlayer = player;
    let settled = false;

    function fail(message) {
      if (settled) return;
      settled = true;
      player.destroy();
      if (hlsPlayer === player) {
        hlsPlayer = null;
      }
      reject(new Error(message));
    }

    player.on(HlsClass.Events.MANIFEST_PARSED, () => {
      if (settled) return;
      settled = true;
      hlsPlayer = player;
      resolve();
    });

    player.on(HlsClass.Events.ERROR, (_event, data) => {
      if (!data) return;
      if (!data.fatal) {
        console.warn("Non-fatal HLS error, continuing:", data);
        return;
      }
      if (data.details === HlsClass.ErrorDetails.MANIFEST_LOAD_ERROR ||
          data.details === HlsClass.ErrorDetails.LEVEL_LOAD_ERROR ||
          data.details === HlsClass.ErrorDetails.FRAG_LOAD_ERROR) {
        player.recoverMediaError();
        return;
      }
      fail(`HLS playback failed: ${data.details || "fatal error"}.`);
    });

    player.attachMedia(elements.player);
    player.loadSource(url);
  });
}

async function handleLogin() {
  const username = elements.username.value.trim();
  const password = elements.password.value;
  if (!username || !password) {
    setStatus("Enter a username and password.", true);
    return;
  }
  elements.login.disabled = true;
  setStatus("Signing in...");
  try {
    const payload = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    elements.password.value = "";
    showApp(payload.user);
    await refreshVideos();
    setStatus("Signed in.");
  } catch (error) {
    setStatus(error.message || String(error), true);
  } finally {
    elements.login.disabled = false;
  }
}

async function handleLogout() {
  await api("/api/logout", { method: "POST", body: "{}" }).catch(() => {});
  showLogin();
}

async function boot() {
  try {
    const user = await api("/api/me");
    showApp(user);
    await refreshVideos();
  } catch {
    showLogin();
  }
}

elements.login.addEventListener("click", handleLogin);
elements.logout.addEventListener("click", handleLogout);
elements.password.addEventListener("keydown", (event) => {
  if (event.key === "Enter") handleLogin();
});
elements.player.addEventListener("error", () => {
  setStatus("Playback failed or the browser rejected the stream.", true);
});

boot();
