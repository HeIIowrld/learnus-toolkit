const OBJECT_URL_TTL_MS = 10 * 60 * 1000;

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.target !== "offscreen") return false;

  if (message.type === "BUILD_HLS_OBJECT_URL") {
    buildHlsObjectUrl(message.url)
      .then((result) => sendResponse({ success: true, ...result }))
      .catch((error) => sendResponse({ success: false, message: error.message || String(error) }));
    return true;
  }

  return false;
});

async function buildHlsObjectUrl(url) {
  const playlist = await resolveMediaPlaylist(url);
  const parsed = parseMediaPlaylist(playlist.text, playlist.url);

  if (!parsed.segments.length) {
    throw new Error("No HLS media segments were found.");
  }

  const keyCache = new Map();
  const chunks = [];

  if (parsed.map) {
    chunks.push(await fetchSegment(parsed.map));
  }

  for (let index = 0; index < parsed.segments.length; index += 1) {
    const segment = parsed.segments[index];
    let buffer = await fetchSegment(segment);

    if (segment.key && segment.key.method !== "NONE") {
      buffer = await decryptSegment(buffer, segment.key, index, parsed.mediaSequence, keyCache, playlist.url);
    }

    chunks.push(buffer);
  }

  const extension = parsed.outputType === "mp4" ? "mp4" : "ts";
  const mimeType = extension === "mp4" ? "video/mp4" : "video/mp2t";
  const bytes = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const blob = new Blob(chunks, { type: mimeType });
  const objectUrl = URL.createObjectURL(blob);

  setTimeout(() => URL.revokeObjectURL(objectUrl), OBJECT_URL_TTL_MS);

  return {
    objectUrl,
    extension,
    mimeType,
    bytes,
    segmentCount: parsed.segments.length
  };
}

async function resolveMediaPlaylist(url, depth = 0) {
  if (depth > 4) {
    throw new Error("Too many nested HLS playlists.");
  }

  const response = await fetch(url, {
    credentials: "include",
    redirect: "follow",
    cache: "no-store"
  });

  if (!response.ok) {
    throw new Error(`HLS playlist request failed: HTTP ${response.status}`);
  }

  const text = await response.text();
  if (!text.includes("#EXTM3U")) {
    throw new Error("The selected URL is not an HLS playlist.");
  }

  const variantUrl = pickBestVariant(text, response.url);
  if (variantUrl) {
    return resolveMediaPlaylist(variantUrl, depth + 1);
  }

  return {
    url: response.url,
    text
  };
}

function pickBestVariant(text, baseUrl) {
  const lines = normalizeLines(text);
  const variants = [];

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.startsWith("#EXT-X-STREAM-INF")) continue;

    const attributes = parseAttributeList(line.slice(line.indexOf(":") + 1));
    const nextUri = findNextUri(lines, index + 1);
    if (!nextUri) continue;

    variants.push({
      url: toAbsoluteUrl(nextUri, baseUrl),
      bandwidth: Number(attributes.BANDWIDTH || attributes["AVERAGE-BANDWIDTH"] || 0),
      resolutionPixels: parseResolutionPixels(attributes.RESOLUTION || "")
    });
  }

  if (!variants.length) return "";

  variants.sort((a, b) => (
    (b.bandwidth - a.bandwidth) ||
    (b.resolutionPixels - a.resolutionPixels)
  ));
  return variants[0].url;
}

function parseMediaPlaylist(text, playlistUrl) {
  const lines = normalizeLines(text);
  const segments = [];
  let mediaSequence = 0;
  let currentKey = null;
  let currentMap = null;
  let pendingByteRange = null;
  let nextByteRangeOffset = 0;
  let outputType = "ts";

  for (const line of lines) {
    if (line.startsWith("#EXT-X-MEDIA-SEQUENCE")) {
      mediaSequence = Number(valueAfterColon(line)) || 0;
      continue;
    }

    if (line.startsWith("#EXT-X-KEY")) {
      const attributes = parseAttributeList(valueAfterColon(line));
      currentKey = {
        method: attributes.METHOD || "NONE",
        uri: attributes.URI ? toAbsoluteUrl(attributes.URI, playlistUrl) : "",
        iv: attributes.IV || ""
      };

      if (currentKey.method && !["NONE", "AES-128"].includes(currentKey.method)) {
        throw new Error(`Unsupported HLS encryption method: ${currentKey.method}`);
      }
      continue;
    }

    if (line.startsWith("#EXT-X-MAP")) {
      const attributes = parseAttributeList(valueAfterColon(line));
      const mapByteRange = parseByteRange(attributes.BYTERANGE || "");
      if (mapByteRange && mapByteRange.offset == null) {
        mapByteRange.offset = 0;
      }
      currentMap = {
        url: toAbsoluteUrl(attributes.URI, playlistUrl),
        byteRange: mapByteRange
      };
      outputType = "mp4";
      continue;
    }

    if (line.startsWith("#EXT-X-BYTERANGE")) {
      pendingByteRange = parseByteRange(valueAfterColon(line));
      continue;
    }

    if (!line || line.startsWith("#")) continue;

    let byteRange = pendingByteRange;
    if (byteRange && byteRange.offset == null) {
      byteRange = { ...byteRange, offset: nextByteRangeOffset };
    }
    if (byteRange) {
      nextByteRangeOffset = byteRange.offset + byteRange.length;
    }

    const segmentUrl = toAbsoluteUrl(line, playlistUrl);
    if (/\.(m4s|mp4)(?:[?#]|$)/i.test(segmentUrl)) {
      outputType = "mp4";
    }

    segments.push({
      url: segmentUrl,
      byteRange,
      key: currentKey ? { ...currentKey } : null
    });
    pendingByteRange = null;
  }

  return {
    mediaSequence,
    map: currentMap,
    segments,
    outputType
  };
}

async function fetchSegment(segment) {
  const headers = {};
  if (segment.byteRange) {
    const start = segment.byteRange.offset;
    const end = start + segment.byteRange.length - 1;
    headers.Range = `bytes=${start}-${end}`;
  }

  const response = await fetch(segment.url, {
    credentials: "include",
    redirect: "follow",
    cache: "no-store",
    headers
  });

  if (!response.ok && response.status !== 206) {
    throw new Error(`Segment request failed: HTTP ${response.status}`);
  }

  return response.arrayBuffer();
}

async function decryptSegment(buffer, keyInfo, segmentIndex, mediaSequence, keyCache, playlistUrl) {
  if (keyInfo.method !== "AES-128") {
    return buffer;
  }

  if (!keyInfo.uri) {
    throw new Error("Encrypted HLS segment is missing a key URI.");
  }

  let cryptoKey = keyCache.get(keyInfo.uri);
  if (!cryptoKey) {
    const keyResponse = await fetch(toAbsoluteUrl(keyInfo.uri, playlistUrl), {
      credentials: "include",
      redirect: "follow",
      cache: "no-store"
    });

    if (!keyResponse.ok) {
      throw new Error(`HLS key request failed: HTTP ${keyResponse.status}`);
    }

    cryptoKey = await crypto.subtle.importKey(
      "raw",
      await keyResponse.arrayBuffer(),
      { name: "AES-CBC" },
      false,
      ["decrypt"]
    );
    keyCache.set(keyInfo.uri, cryptoKey);
  }

  const iv = keyInfo.iv ? parseHexIv(keyInfo.iv) : sequenceIv(mediaSequence + segmentIndex);
  return crypto.subtle.decrypt({ name: "AES-CBC", iv }, cryptoKey, buffer);
}

function normalizeLines(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function findNextUri(lines, startIndex) {
  for (let index = startIndex; index < lines.length; index += 1) {
    const line = lines[index];
    if (line && !line.startsWith("#")) return line;
  }
  return "";
}

function parseAttributeList(value) {
  const attributes = {};
  const pattern = /([A-Z0-9-]+)=("[^"]*"|[^,]*)/gi;
  for (const match of value.matchAll(pattern)) {
    attributes[match[1].toUpperCase()] = String(match[2] || "").replace(/^"|"$/g, "");
  }
  return attributes;
}

function parseByteRange(value) {
  if (!value) return null;

  const match = String(value).replace(/^"|"$/g, "").match(/^(\d+)(?:@(\d+))?$/);
  if (!match) return null;

  return {
    length: Number(match[1]),
    offset: match[2] == null ? null : Number(match[2])
  };
}

function valueAfterColon(line) {
  const colon = line.indexOf(":");
  return colon === -1 ? "" : line.slice(colon + 1).trim();
}

function parseResolutionPixels(value) {
  const match = String(value).match(/^(\d+)x(\d+)$/i);
  return match ? Number(match[1]) * Number(match[2]) : 0;
}

function parseHexIv(value) {
  const hex = String(value).replace(/^0x/i, "").padStart(32, "0");
  const bytes = new Uint8Array(16);
  for (let index = 0; index < 16; index += 1) {
    bytes[index] = Number.parseInt(hex.slice(index * 2, index * 2 + 2), 16);
  }
  return bytes;
}

function sequenceIv(sequence) {
  const bytes = new Uint8Array(16);
  let value = BigInt(sequence);
  for (let index = 15; index >= 0; index -= 1) {
    bytes[index] = Number(value & 0xffn);
    value >>= 8n;
  }
  return bytes;
}

function toAbsoluteUrl(rawUrl, baseUrl) {
  if (!rawUrl) return "";
  try {
    return new URL(rawUrl, baseUrl).toString();
  } catch {
    return rawUrl;
  }
}
