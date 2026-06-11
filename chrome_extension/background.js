const DEFAULT_CONFLICT_ACTION = "uniquify";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.target === "offscreen") {
    return false;
  }

  handleMessage(message, sender)
    .then((payload) => sendResponse({ success: true, ...payload }))
    .catch((error) => sendResponse({ success: false, message: error.message || String(error) }));

  return true;
});

async function handleMessage(message) {
  switch (message?.type) {
    case "FETCH_TEXT":
      return fetchText(message.url);
    case "DOWNLOAD_URLS":
      return downloadUrls(message.items || []);
    case "DOWNLOAD_HLS":
      return downloadHls(message.item || message);
    default:
      throw new Error("Unknown message type");
  }
}

async function fetchText(url) {
  if (!isAllowedUrl(url)) {
    throw new Error("Only HTTPS URLs can be fetched.");
  }

  const response = await fetch(url, {
    credentials: "include",
    redirect: "follow",
    cache: "no-store",
    headers: {
      "Accept": "text/html,application/xhtml+xml,application/xml,text/plain,*/*"
    }
  });

  const contentType = response.headers.get("content-type") || "";
  const shouldReadText = isTextResponse(contentType) || looksLikeTextUrl(response.url);
  const text = shouldReadText ? await response.text() : "";

  return {
    url: response.url,
    status: response.status,
    contentType,
    text
  };
}

async function downloadUrls(items) {
  const downloads = [];

  for (const item of items) {
    if (!isAllowedUrl(item.url)) {
      downloads.push({ ok: false, url: item.url, message: "Invalid URL" });
      continue;
    }

    try {
      const downloadId = await downloadOne(item);
      downloads.push({ ok: true, url: item.url, id: downloadId });
    } catch (error) {
      downloads.push({ ok: false, url: item.url, message: error.message || String(error) });
    }
  }

  return {
    requested: items.length,
    succeeded: downloads.filter((item) => item.ok).length,
    failed: downloads.filter((item) => !item.ok).length,
    downloads
  };
}

async function downloadHls(item) {
  if (!isAllowedUrl(item.url)) {
    throw new Error("Invalid HLS URL");
  }

  const hlsResult = await sendOffscreenMessage({
    type: "BUILD_HLS_OBJECT_URL",
    url: item.url
  });

  const filename = ensureMediaExtension(item.filename, hlsResult.extension || "ts");
  const downloadId = await downloadOne({
    url: hlsResult.objectUrl,
    filename
  });

  return {
    id: downloadId,
    segmentCount: hlsResult.segmentCount,
    bytes: hlsResult.bytes,
    extension: hlsResult.extension || "ts"
  };
}

function downloadOne(item) {
  const options = {
    url: item.url,
    conflictAction: DEFAULT_CONFLICT_ACTION,
    saveAs: false
  };

  const filename = normalizeDownloadPath(item.filename);
  if (filename) {
    options.filename = filename;
  }

  return new Promise((resolve, reject) => {
    chrome.downloads.download(options, (downloadId) => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        reject(new Error(lastError.message));
      } else {
        resolve(downloadId);
      }
    });
  });
}

function isAllowedUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return url.protocol === "https:" || url.protocol === "blob:";
  } catch {
    return false;
  }
}

async function ensureOffscreenDocument() {
  if (!chrome.offscreen) {
    throw new Error("This Chrome version does not support offscreen documents.");
  }

  if (await chrome.offscreen.hasDocument()) {
    return;
  }

  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["BLOBS"],
    justification: "Create a Blob URL for browser-only HLS video downloads."
  });
}

async function sendOffscreenMessage(message) {
  await ensureOffscreenDocument();

  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({ target: "offscreen", ...message }, (response) => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        reject(new Error(lastError.message));
      } else if (!response?.success) {
        reject(new Error(response?.message || "Offscreen request failed"));
      } else {
        resolve(response);
      }
    });
  });
}

function ensureMediaExtension(filename, extension) {
  const safeExtension = String(extension || "ts").replace(/^\./, "");
  const normalized = normalizeDownloadPath(filename || `video.${safeExtension}`);
  const withoutKnownMediaExtension = normalized.replace(/\.(m3u8|mp4|m4v|mov|webm|ts)$/i, "");
  return `${withoutKnownMediaExtension}.${safeExtension}`;
}

function normalizeDownloadPath(path) {
  if (!path || typeof path !== "string") return "";

  return path
    .split("/")
    .map((part) => sanitizePathPart(part))
    .filter(Boolean)
    .join("/")
    .slice(0, 240);
}

function sanitizePathPart(value) {
  return String(value)
    .replace(/[<>:"\\|?*\u0000-\u001f]/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^\.+$/, "_")
    .slice(0, 120);
}

function isTextResponse(contentType) {
  return /text\/|html|xml|json|javascript/i.test(contentType || "");
}

function looksLikeTextUrl(url) {
  return /\.(html?|php|m3u8)(?:[?#]|$)/i.test(url || "");
}
