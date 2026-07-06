"use strict";

import {
  FragmentedMp4Assembler,
  joinBytes,
  mimeFromInitializationSegment,
} from "./mp4_segments.js";

const MAGIC = new TextEncoder().encode("VCNRCMP3");
const VERSION = 3;
const END_MARKER = 0xffffffff;
const MAX_HEADER = 1024 * 1024;
const MAX_CIPHER = 1024 * 1024 + 16;
const SAMPLE_CONFIG_PATH = "sample-config.json";

const elements = {
  url: document.querySelector("#url"),
  sample: document.querySelector("#sample"),
  sampleLabel: document.querySelector("#sample-label"),
  sampleLoad: document.querySelector("#sample-load"),
  file: document.querySelector("#file"),
  passcode: document.querySelector("#passcode"),
  play: document.querySelector("#play"),
  stop: document.querySelector("#stop"),
  status: document.querySelector("#status"),
  video: document.querySelector("#video"),
  metadata: document.querySelector("#metadata"),
};

let activeReader = null;
let activeMediaSource = null;
let activeObjectUrl = null;
let activeAbortController = null;
let stopped = false;
let sampleUrl = null;

class ExactReader {
  constructor(stream) {
    this.reader = stream.getReader();
    this.pending = new Uint8Array(0);
  }

  async readExact(length) {
    const output = new Uint8Array(length);
    let written = 0;
    while (written < length) {
      if (this.pending.length) {
        const count = Math.min(this.pending.length, length - written);
        output.set(this.pending.subarray(0, count), written);
        written += count;
        this.pending = this.pending.subarray(count);
        continue;
      }
      const { value, done } = await this.reader.read();
      if (done) throw new Error("The VCNR stream ended unexpectedly.");
      this.pending = value;
    }
    return output;
  }

  async uint32() {
    const bytes = await this.readExact(4);
    return new DataView(bytes.buffer, bytes.byteOffset, 4).getUint32(0, false);
  }

  cancel() {
    return this.reader.cancel().catch(() => {});
  }
}

function setStatus(message, error = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("error", error);
}

function basename(value) {
  try {
    const url = new URL(value, window.location.href);
    const parts = url.pathname.split("/");
    return decodeURIComponent(parts[parts.length - 1] || "sample.vcnr");
  } catch {
    return "sample.vcnr";
  }
}

function showSample(url) {
  sampleUrl = new URL(url, window.location.href).href;
  elements.sampleLabel.textContent = `Load ${basename(sampleUrl)} from this hosted player.`;
  elements.sample.classList.remove("hidden");
}

async function probeSampleFile() {
  const pageUrl = new URL(window.location.href);
  const configuredSample = pageUrl.searchParams.get("sample");
  if (configuredSample) {
    showSample(configuredSample);
    return;
  }

  try {
    const response = await fetch(new URL(SAMPLE_CONFIG_PATH, pageUrl).href, {
      cache: "no-store",
    });
    if (!response.ok) return;
    const config = await response.json();
    if (config && typeof config.sample === "string" && config.sample.trim()) {
      showSample(config.sample.trim());
    }
  } catch {
    // Ignore missing or invalid config files and keep the sample UI hidden.
  }
}

function loadSampleFile() {
  if (!sampleUrl) return;
  elements.file.value = "";
  elements.url.value = sampleUrl;
  setStatus(`Sample file loaded: ${basename(sampleUrl)}. Enter the passcode and press Unlock & play.`);
}

function prefillSharedUrl() {
  const pageUrl = new URL(window.location.href);
  let sharedUrl = pageUrl.searchParams.get("url");
  if (!sharedUrl && pageUrl.hash.startsWith("#url=")) {
    sharedUrl = decodeURIComponent(pageUrl.hash.slice(5));
  }
  if (sharedUrl && !elements.url.value.trim()) {
    elements.url.value = sharedUrl;
    setStatus("Shared VCNR URL loaded. Enter the passcode and press Unlock & play.");
  }
}

function u32Pair(first, second) {
  const bytes = new Uint8Array(8);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, first, false);
  view.setUint32(4, second, false);
  return bytes;
}

async function appendBuffer(sourceBuffer, bytes, description) {
  if (stopped) throw new Error("Playback stopped.");
  await new Promise((resolve, reject) => {
    const cleanup = () => {
      sourceBuffer.removeEventListener("updateend", success);
      sourceBuffer.removeEventListener("error", failure);
    };
    const success = () => { cleanup(); resolve(); };
    const failure = () => {
      cleanup();
      reject(new Error(`Browser rejected the ${description}.`));
    };
    sourceBuffer.addEventListener("updateend", success, { once: true });
    sourceBuffer.addEventListener("error", failure, { once: true });
    try {
      sourceBuffer.appendBuffer(bytes);
    } catch (error) {
      cleanup();
      reject(error);
    }
  });
}

async function openInput() {
  const file = elements.file.files[0];
  const url = elements.url.value.trim();
  if (file) return file.stream();
  if (!url) throw new Error("Choose a VCNR file or enter its URL.");
  activeAbortController = new AbortController();
  let response;
  try {
    response = await fetch(url, {
      signal: activeAbortController.signal,
      cache: "no-store",
      mode: "cors",
    });
  } catch (error) {
    if (error && error.name === "AbortError") {
      throw new Error("Playback stopped.");
    }
    if (window.location.protocol === "https:" && url.toLowerCase().startsWith("http://")) {
      throw new Error("This page uses HTTPS, so the VCNR file URL must use HTTPS too.");
    }
    throw new Error(
      "Browser could not download the VCNR URL. Check the address and confirm the file server allows CORS for this player.",
    );
  }
  if (!response.ok || !response.body) {
    throw new Error(`VCNR server returned HTTP ${response.status}.`);
  }
  return response.body;
}

async function createSourceBuffer(mime) {
  if (!("MediaSource" in window)) {
    throw new Error("This browser does not support Media Source playback.");
  }
  if (!MediaSource.isTypeSupported(mime)) {
    throw new Error(`This browser does not support the VCNR codec: ${mime}`);
  }

  const mediaSource = new MediaSource();
  activeMediaSource = mediaSource;
  activeObjectUrl = URL.createObjectURL(mediaSource);
  elements.video.src = activeObjectUrl;
  await new Promise((resolve, reject) => {
    mediaSource.addEventListener("sourceopen", resolve, { once: true });
    mediaSource.addEventListener("error", () => reject(new Error("Media player failed.")), { once: true });
  });
  try {
    return mediaSource.addSourceBuffer(mime);
  } catch (error) {
    throw new Error(`Could not create the browser decoder for ${mime}: ${error.message}`);
  }
}

function resetVideoSource() {
  elements.video.pause();
  elements.video.removeAttribute("src");
  elements.video.load();
  if (activeObjectUrl) URL.revokeObjectURL(activeObjectUrl);
  activeObjectUrl = null;
  activeMediaSource = null;
}

async function playMemoryFallback(parts) {
  resetVideoSource();
  const blob = new Blob(parts, { type: "video/mp4" });
  activeObjectUrl = URL.createObjectURL(blob);
  elements.video.src = activeObjectUrl;
  await new Promise((resolve, reject) => {
    const cleanup = () => {
      elements.video.removeEventListener("loadedmetadata", loaded);
      elements.video.removeEventListener("error", failed);
    };
    const loaded = () => { cleanup(); resolve(); };
    const failed = () => {
      cleanup();
      reject(new Error("This browser could not decode the decrypted H.264/AAC video."));
    };
    elements.video.addEventListener("loadedmetadata", loaded, { once: true });
    elements.video.addEventListener("error", failed, { once: true });
  });
  setStatus("Compatibility mode: decrypted in memory and ready to play.");
  elements.video.play().catch(() => {});
}

async function playVcnr() {
  stopPlayback();
  stopped = false;
  elements.play.disabled = true;
  try {
    if (!elements.passcode.value) throw new Error("Enter the VCNR passcode.");
    setStatus("Opening VCNR stream...");
    activeReader = new ExactReader(await openInput());

    const magic = await activeReader.readExact(8);
    if (!magic.every((value, index) => value === MAGIC[index])) {
      throw new Error("This is not a browser-compatible VCNR v3 file.");
    }
    const version = await activeReader.uint32();
    const headerLength = await activeReader.uint32();
    if (version !== VERSION || headerLength > MAX_HEADER) {
      throw new Error("Unsupported or damaged VCNR file.");
    }
    const salt = await activeReader.readExact(16);
    const headerBytes = await activeReader.readExact(headerLength);
    const header = JSON.parse(new TextDecoder().decode(headerBytes));
    elements.metadata.textContent = JSON.stringify(header, null, 2);

    setStatus("Deriving encryption key...");
    const keyMaterial = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(elements.passcode.value),
      "PBKDF2",
      false,
      ["deriveKey"],
    );
    const key = await crypto.subtle.deriveKey(
      {
        name: "PBKDF2",
        hash: "SHA-256",
        salt,
        iterations: header.kdf_iterations,
      },
      keyMaterial,
      { name: "AES-GCM", length: 256 },
      false,
      ["decrypt"],
    );
    const headerHash = new Uint8Array(await crypto.subtle.digest("SHA-256", headerBytes));
    const mp4Assembler = new FragmentedMp4Assembler();
    let sourceBuffer = null;
    let mediaAppended = false;
    let memoryFallback = [];
    let mseDisabled = false;

    let expectedId = 0;
    while (!stopped) {
      const chunkId = await activeReader.uint32();
      if (chunkId === END_MARKER) break;
      const plainLength = await activeReader.uint32();
      const nonce = await activeReader.readExact(12);
      const cipherLength = await activeReader.uint32();
      if (
        chunkId !== expectedId ||
        plainLength > 1024 * 1024 ||
        cipherLength !== plainLength + 16 ||
        cipherLength > MAX_CIPHER
      ) {
        throw new Error("Invalid or missing VCNR media chunk.");
      }
      const cipher = await activeReader.readExact(cipherLength);
      const aad = joinBytes(headerHash, u32Pair(VERSION, chunkId));
      let plain;
      try {
        plain = new Uint8Array(
          await crypto.subtle.decrypt(
            { name: "AES-GCM", iv: nonce, additionalData: aad, tagLength: 128 },
            key,
            cipher,
          ),
        );
      } catch {
        throw new Error("Wrong passcode or damaged VCNR file.");
      }
      if (memoryFallback) memoryFallback.push(plain);
      if (mseDisabled) {
        expectedId += 1;
        setStatus(
          `Compatibility mode: decrypting chunk ${expectedId}/${header.chunk_count}...`,
        );
        continue;
      }
      const segments = mp4Assembler.push(plain);
      for (const segment of segments) {
        if (!sourceBuffer) {
          const mime = mimeFromInitializationSegment(segment);
          setStatus(`Opening browser decoder: ${mime}`);
          try {
            sourceBuffer = await createSourceBuffer(mime);
            await appendBuffer(sourceBuffer, segment, "MP4 initialization segment");
            // Initialization succeeded, so progressive playback can continue
            // without retaining the entire decrypted video in memory.
            memoryFallback = null;
          } catch {
            mseDisabled = true;
            sourceBuffer = null;
            resetVideoSource();
            setStatus(
              "Media Source initialization was rejected. Switching to in-memory compatibility mode...",
            );
            break;
          }
          continue;
        }
        await appendBuffer(
          sourceBuffer,
          segment,
          `MP4 media fragment ${mp4Assembler.fragmentCount}`,
        );
        if (!mediaAppended) {
          mediaAppended = true;
          setStatus("Passcode accepted. Streaming video...");
          elements.video.play().catch(() => {});
        }
      }
      expectedId += 1;
      setStatus(`Streaming encrypted chunk ${expectedId}/${header.chunk_count}...`);
    }
    if (!stopped && expectedId !== header.chunk_count) {
      throw new Error("VCNR stream ended before all chunks arrived.");
    }
    if (mseDisabled) {
      await playMemoryFallback(memoryFallback);
    } else {
      const finalSegments = mp4Assembler.push(new Uint8Array(0), true);
      for (const segment of finalSegments) {
        if (!sourceBuffer) {
          const mime = mimeFromInitializationSegment(segment);
          sourceBuffer = await createSourceBuffer(mime);
          await appendBuffer(sourceBuffer, segment, "MP4 initialization segment");
        } else {
          await appendBuffer(sourceBuffer, segment, "final MP4 media fragment");
        }
      }
      if (!stopped && !mp4Assembler.fragmentCount) {
        throw new Error("VCNR contains no playable fragmented MP4 media.");
      }
      if (!stopped && activeMediaSource.readyState === "open") {
        activeMediaSource.endOfStream();
        setStatus("Video fully buffered.");
      }
    }
  } catch (error) {
    if (!stopped) setStatus(error.message || String(error), true);
  } finally {
    activeAbortController = null;
    elements.play.disabled = false;
  }
}

function stopPlayback() {
  stopped = true;
  if (activeAbortController) activeAbortController.abort();
  activeAbortController = null;
  if (activeReader) activeReader.cancel();
  activeReader = null;
  resetVideoSource();
}

probeSampleFile();
prefillSharedUrl();
elements.sampleLoad.addEventListener("click", loadSampleFile);
elements.play.addEventListener("click", playVcnr);
elements.stop.addEventListener("click", () => {
  stopPlayback();
  setStatus("Stopped.");
});
