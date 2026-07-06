export function joinBytes(...arrays) {
  const result = new Uint8Array(arrays.reduce((sum, item) => sum + item.length, 0));
  let offset = 0;
  for (const item of arrays) {
    result.set(item, offset);
    offset += item.length;
  }
  return result;
}

function boxType(bytes) {
  return String.fromCharCode(bytes[4], bytes[5], bytes[6], bytes[7]);
}

function findAscii(bytes, text) {
  const wanted = [...text].map((character) => character.charCodeAt(0));
  outer: for (let index = 0; index <= bytes.length - wanted.length; index += 1) {
    for (let offset = 0; offset < wanted.length; offset += 1) {
      if (bytes[index + offset] !== wanted[offset]) continue outer;
    }
    return index;
  }
  return -1;
}

export function mimeFromInitializationSegment(bytes) {
  const avcC = findAscii(bytes, "avcC");
  if (avcC < 4 || avcC + 8 > bytes.length) {
    throw new Error("MP4 initialization data has no H.264 avcC record.");
  }
  const hex = (value) => value.toString(16).padStart(2, "0").toUpperCase();
  const profile = hex(bytes[avcC + 5]);
  const compatibility = hex(bytes[avcC + 6]);
  const level = hex(bytes[avcC + 7]);
  const codecs = [`avc1.${profile}${compatibility}${level}`];
  if (findAscii(bytes, "mp4a") >= 0) codecs.push("mp4a.40.2");
  return `video/mp4; codecs="${codecs.join(", ")}"`;
}

export class FragmentedMp4Assembler {
  constructor() {
    this.pending = new Uint8Array(0);
    this.staged = [];
    this.initialized = false;
    this.fragmentCount = 0;
  }

  push(bytes, final = false) {
    this.pending = joinBytes(this.pending, bytes);
    const segments = [];

    while (this.pending.length >= 8) {
      const view = new DataView(
        this.pending.buffer,
        this.pending.byteOffset,
        this.pending.byteLength,
      );
      let size = view.getUint32(0, false);
      let headerSize = 8;

      if (size === 1) {
        if (this.pending.length < 16) break;
        const largeSize = view.getBigUint64(8, false);
        if (largeSize > BigInt(Number.MAX_SAFE_INTEGER)) {
          throw new Error("MP4 box is too large for browser playback.");
        }
        size = Number(largeSize);
        headerSize = 16;
      } else if (size === 0) {
        if (!final) break;
        size = this.pending.length;
      }

      if (size < headerSize) throw new Error("Damaged MP4 box inside VCNR.");
      if (this.pending.length < size) break;

      const box = this.pending.slice(0, size);
      this.pending = this.pending.slice(size);
      const type = boxType(box);

      if (!this.initialized) {
        this.staged.push(box);
        if (type === "moov") {
          segments.push(joinBytes(...this.staged));
          this.staged = [];
          this.initialized = true;
        }
      } else if (type !== "mfra" && type !== "mfro") {
        this.staged.push(box);
        if (type === "mdat") {
          segments.push(joinBytes(...this.staged));
          this.staged = [];
          this.fragmentCount += 1;
        }
      }
    }

    if (final) {
      if (this.pending.length) {
        throw new Error("VCNR ended in the middle of an MP4 box.");
      }
      if (!this.initialized) {
        throw new Error("VCNR does not contain an MP4 initialization segment.");
      }
      // Trailing mfra/mfro indexes are unnecessary for sequential MSE playback.
      this.staged = [];
    }
    return segments;
  }
}
