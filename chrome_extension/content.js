const PANEL_ID = "learnus-downloader-panel";
const DIRECT_FILE_RE = /\.(pdf|pptx?|docx?|xlsx?|zip|7z|rar|hwp|hwpx|txt|csv|ipynb|py|r|rmd|c|cpp|h|java|sql|dat|md|json|xml|html?|png|jpe?g|gif|mp4|mov|m4v|webm|m3u8)(?:[?#]|$)/i;
const MATERIAL_FILE_RE = /\.(pdf|pptx?|docx?|xlsx?|zip|7z|rar|hwp|hwpx|txt|csv|ipynb|py|r|rmd|c|cpp|h|java|sql|dat|md|json|xml|html?|png|jpe?g|gif)(?:[?#]|$)/i;
const VIDEO_FILE_RE = /\.(mp4|mov|m4v|webm|m3u8)(?:[?#]|$)/i;

const state = {
  courseId: "",
  courseTitle: "",
  year: "",
  semester: "",
  materials: [],
  videos: [],
  busy: false
};

init();

function init() {
  state.courseId = new URL(location.href).searchParams.get("id") || "";
  if (!state.courseId) return;

  state.courseTitle = getCourseTitle();
  const termInfo = getCourseTermInfo();
  state.year = termInfo.year;
  state.semester = termInfo.semester;
  scanCoursePage();
  injectPanel();
  injectInlineVideoButtons();
}

function scanCoursePage() {
  const seenMaterials = new Set();
  const seenVideos = new Set();
  const links = Array.from(document.querySelectorAll("a[href]"));

  state.materials = [];
  state.videos = [];

  for (const link of links) {
    const url = toAbsoluteUrl(link.getAttribute("href"));
    if (!url || !url.startsWith("https://")) continue;
    if (url.includes("/course/view.php")) continue;

    const title = getLinkTitle(link);
    if (!title) continue;

    if (looksLikeVideo(link, url, title)) {
      const key = normalizeUrlKey(url);
      if (!seenVideos.has(key)) {
        seenVideos.add(key);
        state.videos.push(createItem("video", link, url, title));
      }
      continue;
    }

    if (looksLikeMaterial(link, url, title)) {
      const key = normalizeUrlKey(url);
      if (!seenMaterials.has(key)) {
        seenMaterials.add(key);
        const type = looksLikeAssignment(link, url, title) ? "assignment" : "material";
        state.materials.push(createItem(type, link, url, title));
      }
    }
  }
}

function injectPanel() {
  const existing = document.getElementById(PANEL_ID);
  if (existing) existing.remove();

  const panel = document.createElement("section");
  panel.id = PANEL_ID;
  panel.innerHTML = `
    <div class="learnus-downloader-header">
      <div class="learnus-downloader-title" title="${escapeHtml(state.courseTitle)}">LearnUs Downloader</div>
      <button class="learnus-downloader-button secondary" data-action="toggle" type="button">접기</button>
    </div>
    <div class="learnus-downloader-body">
      <div class="learnus-downloader-row">
        <span>자료 <strong data-count="materials">${state.materials.length}</strong>개</span>
        <span>영상 <strong data-count="videos">${state.videos.length}</strong>개</span>
      </div>
      <div class="learnus-downloader-path" title="${escapeHtml(buildCourseFolderPath())}">
        저장 경로: ${escapeHtml(buildCourseFolderPath())}
      </div>
      <div class="learnus-downloader-actions">
        <button class="learnus-downloader-button" data-action="download-materials" type="button">자료 일괄 다운로드</button>
        <button class="learnus-downloader-button" data-action="download-videos" type="button">영상 일괄 다운로드</button>
        <button class="learnus-downloader-button secondary" data-action="refresh" type="button">다시 스캔</button>
      </div>
      <details>
        <summary>감지된 영상</summary>
        <div class="learnus-downloader-list" data-video-list></div>
      </details>
      <div class="learnus-downloader-status" data-status>강의 페이지에서 다운로드할 항목을 찾았습니다.</div>
    </div>
  `;

  panel.addEventListener("click", onPanelClick);
  document.documentElement.appendChild(panel);
  renderVideoList();
}

function injectInlineVideoButtons() {
  document.querySelectorAll(".learnus-inline-video-button").forEach((button) => button.remove());

  for (const video of state.videos) {
    if (!video.element || !video.element.isConnected) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "learnus-inline-video-button";
    button.textContent = "다운로드";
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      await downloadSingleVideo(video, button);
    });

    video.element.insertAdjacentElement("afterend", button);
  }
}

async function onPanelClick(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;

  const action = button.dataset.action;
  if (action === "toggle") {
    const panel = document.getElementById(PANEL_ID);
    panel.classList.toggle("learnus-downloader-minimized");
    button.textContent = panel.classList.contains("learnus-downloader-minimized") ? "펼치기" : "접기";
    return;
  }

  if (state.busy) return;

  if (action === "refresh") {
    scanCoursePage();
    updatePanelCounts();
    renderVideoList();
    injectInlineVideoButtons();
    setStatus("강의 페이지를 다시 스캔했습니다.");
  } else if (action === "download-materials") {
    await downloadMaterials();
  } else if (action === "download-videos") {
    await downloadVideos();
  } else if (action === "download-video") {
    const video = state.videos.find((item) => item.id === button.dataset.videoId);
    if (video) await downloadSingleVideo(video, button);
  }
}

async function downloadMaterials() {
  if (!state.materials.length) {
    setStatus("다운로드할 자료를 찾지 못했습니다.");
    return;
  }

  await withBusy(async () => {
    const downloads = [];
    let scanned = 0;

    for (const material of state.materials) {
      scanned += 1;
      setStatus(`자료 링크 확인 중 (${scanned}/${state.materials.length})\n${material.title}`);
      const resolved = await resolveMaterial(material);
      downloads.push(...resolved.map((item) => ({
        url: item.url,
        filename: buildDownloadPath("materials", material, item.title || material.title, item.url)
      })));
    }

    const uniqueDownloads = uniqueByUrl(downloads);
    if (!uniqueDownloads.length) {
      setStatus("직접 다운로드할 수 있는 자료 링크를 찾지 못했습니다.");
      return;
    }

    setStatus(`자료 ${uniqueDownloads.length}개 다운로드를 시작합니다.`);
    const result = await sendMessage({ type: "DOWNLOAD_URLS", items: uniqueDownloads });
    setStatus(`자료 다운로드 요청 완료\n성공 ${result.succeeded}개, 실패 ${result.failed}개`);
  });
}

async function downloadVideos() {
  if (!state.videos.length) {
    setStatus("다운로드할 영상을 찾지 못했습니다.");
    return;
  }

  await withBusy(async () => {
    const directDownloads = [];
    const hlsDownloads = [];
    let scanned = 0;

    for (const video of state.videos) {
      scanned += 1;
      setStatus(`영상 링크 확인 중 (${scanned}/${state.videos.length})\n${video.title}`);
      const resolved = await resolveVideo(video);
      if (resolved) {
        const downloadItem = {
          url: resolved.url,
          filename: buildDownloadPath("videos", video, video.title, resolved.url)
        };

        if (resolved.kind === "hls") {
          hlsDownloads.push(downloadItem);
        } else {
          directDownloads.push(downloadItem);
        }
      }
    }

    const uniqueDirectDownloads = uniqueByUrl(directDownloads);
    const uniqueHlsDownloads = uniqueByUrl(hlsDownloads);
    const totalDownloads = uniqueDirectDownloads.length + uniqueHlsDownloads.length;

    if (!totalDownloads) {
      setStatus("직접 다운로드할 수 있는 영상 파일 링크를 찾지 못했습니다.");
      return;
    }

    let succeeded = 0;
    let failed = 0;

    if (uniqueDirectDownloads.length) {
      setStatus(`직접 영상 ${uniqueDirectDownloads.length}개 다운로드를 시작합니다.`);
      const result = await sendMessage({ type: "DOWNLOAD_URLS", items: uniqueDirectDownloads });
      succeeded += result.succeeded;
      failed += result.failed;
    }

    for (let index = 0; index < uniqueHlsDownloads.length; index += 1) {
      const item = uniqueHlsDownloads[index];
      setStatus(`HLS 영상 병합 중 (${index + 1}/${uniqueHlsDownloads.length})\n브라우저 안에서 m3u8 조각을 합치는 중입니다.`);
      try {
        await sendMessage({ type: "DOWNLOAD_HLS", item });
        succeeded += 1;
      } catch (error) {
        failed += 1;
        console.error("HLS download failed:", error);
      }
    }

    setStatus(`영상 다운로드 요청 완료\n성공 ${succeeded}개, 실패 ${failed}개`);
  });
}

async function downloadSingleVideo(video, button) {
  const originalText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "확인 중";
  }

  try {
    await withBusy(async () => {
      setStatus(`영상 링크 확인 중\n${video.title}`);
      const resolved = await resolveVideo(video);
      if (!resolved) {
        setStatus(`영상 파일 링크를 찾지 못했습니다.\n${video.title}`);
        return;
      }

      if (resolved.kind === "hls") {
        setStatus(`HLS 영상 병합 중\n${video.title}`);
        await sendMessage({
          type: "DOWNLOAD_HLS",
          item: {
            url: resolved.url,
            filename: buildDownloadPath("videos", video, video.title, resolved.url)
          }
        });
        setStatus("HLS 영상 다운로드 요청 완료");
        return;
      }

      const result = await sendMessage({
        type: "DOWNLOAD_URLS",
        items: [{
          url: resolved.url,
          filename: buildDownloadPath("videos", video, video.title, resolved.url)
        }]
      });
      setStatus(`영상 다운로드 요청 완료\n성공 ${result.succeeded}개, 실패 ${result.failed}개`);
    });
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText || "다운로드";
    }
  }
}

async function resolveMaterial(material) {
  if (isDirectMaterialUrl(material.url)) {
    return [{ url: material.url, title: material.title }];
  }

  const page = await fetchText(material.url);
  if (page.url && page.url !== material.url && isDirectMaterialUrl(page.url)) {
    return [{ url: page.url, title: material.title }];
  }

  if (!page.text) {
    return page.url ? [{ url: page.url, title: material.title }] : [];
  }

  const baseUrl = page.url || material.url;
  const doc = parseHtml(page.text, baseUrl);
  const candidates = collectMaterialLinks(doc, baseUrl, material.title);

  if (/\/mod\/ubboard\//i.test(material.url) || /\/mod\/ubboard\//i.test(baseUrl)) {
    const postUrls = collectBoardPostUrls(doc, baseUrl);
    for (const postUrl of postUrls.slice(0, 50)) {
      try {
        const postPage = await fetchText(postUrl);
        if (!postPage.text) continue;
        const postBaseUrl = postPage.url || postUrl;
        const postDoc = parseHtml(postPage.text, postBaseUrl);
        candidates.push(...collectMaterialLinks(postDoc, postBaseUrl, material.title));
      } catch (error) {
        console.warn("LearnUs board post parse failed:", error);
      }
    }
  }

  return uniqueByUrl(candidates);
}

function collectMaterialLinks(doc, baseUrl, fallbackTitle) {
  return Array.from(doc.querySelectorAll("a[href]"))
    .map((link) => ({
      url: toAbsoluteUrl(link.getAttribute("href"), baseUrl),
      title: getLinkTitle(link) || fallbackTitle
    }))
    .filter((item) => item.url && isDirectMaterialUrl(item.url));
}

function collectBoardPostUrls(doc, baseUrl) {
  const urls = Array.from(doc.querySelectorAll("a[href]"))
    .map((link) => toAbsoluteUrl(link.getAttribute("href"), baseUrl))
    .filter((url) => {
      if (!url || !/\/mod\/ubboard\//i.test(url)) return false;
      try {
        const parsed = new URL(url);
        if (parsed.searchParams.has("bwid") || parsed.searchParams.has("article") || parsed.searchParams.has("post")) {
          return true;
        }
        return parsed.pathname !== new URL(baseUrl).pathname && /(?:article|post|view)/i.test(parsed.pathname);
      } catch {
        return /(?:bwid|article|post)/i.test(url);
      }
    });
  return uniqueByUrl(urls.map((url) => ({ url }))).map((item) => item.url);
}

async function resolveVideo(video) {
  const visited = new Set();
  const candidates = await collectVideoCandidates(video.url, visited, 0);
  if (!candidates.length && isDirectVideoUrl(video.url)) {
    return { url: video.url, kind: isHlsUrl(video.url) ? "hls" : "direct" };
  }

  const unique = uniqueByUrl(candidates);
  const directVideo = unique.find((item) => !isHlsUrl(item.url) && /\.(mp4|m4v|mov|webm)(?:[?#]|$)/i.test(item.url));
  if (directVideo) return { ...directVideo, kind: "direct" };

  const hlsVideo = unique.find((item) => isHlsUrl(item.url));
  return hlsVideo ? { ...hlsVideo, kind: "hls" } : null;
}

async function collectVideoCandidates(url, visited, depth) {
  if (!url || visited.has(url) || depth > 2) return [];
  visited.add(url);

  if (isDirectVideoUrl(url)) return [{ url }];

  const page = await fetchText(url);
  const baseUrl = page.url || url;
  const candidates = [];

  if (isDirectVideoUrl(baseUrl)) {
    candidates.push({ url: baseUrl });
  }

  if (page.text) {
    candidates.push(...extractMediaUrls(page.text, baseUrl).map((mediaUrl) => ({ url: mediaUrl })));

    const doc = parseHtml(page.text, baseUrl);
    const mediaElements = Array.from(doc.querySelectorAll("video[src], source[src], a[href]"));
    for (const element of mediaElements) {
      const direct = toAbsoluteUrl(element.getAttribute("src") || element.getAttribute("href"), baseUrl);
      if (isDirectVideoUrl(direct)) {
        candidates.push({ url: direct });
      }
    }

    const frameUrls = Array.from(doc.querySelectorAll("iframe[src], frame[src], embed[src]"))
      .map((frame) => toAbsoluteUrl(frame.getAttribute("src"), baseUrl))
      .filter(Boolean);

    for (const frameUrl of frameUrls.slice(0, 4)) {
      candidates.push(...await collectVideoCandidates(frameUrl, visited, depth + 1));
    }
  }

  return candidates.filter((item) => isDirectVideoUrl(item.url));
}

function extractMediaUrls(html, baseUrl) {
  const normalized = decodeHtml(html).replace(/\\\//g, "/");
  const urls = new Set();
  const absoluteRe = /https?:\/\/[^"'<>\\\s]+?\.(?:mp4|m4v|mov|webm|m3u8)(?:\?[^"'<>\\\s]*)?/gi;
  const relativeRe = /["']([^"']+\.(?:mp4|m4v|mov|webm|m3u8)(?:\?[^"']*)?)["']/gi;

  for (const match of normalized.matchAll(absoluteRe)) {
    urls.add(cleanExtractedUrl(match[0]));
  }

  for (const match of normalized.matchAll(relativeRe)) {
    const url = toAbsoluteUrl(cleanExtractedUrl(match[1]), baseUrl);
    if (url) urls.add(url);
  }

  return Array.from(urls).filter(isDirectVideoUrl);
}

function renderVideoList() {
  const list = document.querySelector(`#${PANEL_ID} [data-video-list]`);
  if (!list) return;

  if (!state.videos.length) {
    list.innerHTML = `<div class="learnus-downloader-status">감지된 영상이 없습니다.</div>`;
    return;
  }

  list.innerHTML = state.videos.map((video) => `
    <div class="learnus-downloader-video">
      <div class="learnus-downloader-video-title" title="${escapeHtml(video.title)}">${escapeHtml(video.title)}</div>
      <button class="learnus-downloader-button secondary" data-action="download-video" data-video-id="${escapeHtml(video.id)}" type="button">다운로드</button>
    </div>
  `).join("");
}

function updatePanelCounts() {
  const panel = document.getElementById(PANEL_ID);
  if (!panel) return;
  panel.querySelector('[data-count="materials"]').textContent = String(state.materials.length);
  panel.querySelector('[data-count="videos"]').textContent = String(state.videos.length);
}

async function withBusy(work) {
  if (state.busy) return;
  state.busy = true;
  setButtonsDisabled(true);
  try {
    await work();
  } catch (error) {
    setStatus(`오류: ${error.message || String(error)}`);
  } finally {
    state.busy = false;
    setButtonsDisabled(false);
  }
}

function setButtonsDisabled(disabled) {
  document
    .querySelectorAll(`#${PANEL_ID} .learnus-downloader-button`)
    .forEach((button) => {
      if (button.dataset.action !== "toggle") button.disabled = disabled;
    });
}

function setStatus(message) {
  const status = document.querySelector(`#${PANEL_ID} [data-status]`);
  if (status) status.textContent = message;
}

function createItem(type, element, url, title) {
  return {
    type,
    id: getActivityId(url) || `${type}-${hashString(url)}`,
    title,
    url,
    week: getWeekTitle(element),
    element
  };
}

function looksLikeMaterial(link, url, title) {
  if (looksLikeVideo(link, url, title)) return false;
  return (
    /\/mod\/(resource|folder|assign|ubfile|ubboard|url)\/view\.php/i.test(url) ||
    /\/(?:webservice\/)?pluginfile\.php/i.test(url) ||
    MATERIAL_FILE_RE.test(url) ||
    /\b(pdf|ppt|pptx|doc|docx|xls|xlsx|hwp|hwpx|zip|csv|ipynb|py|sql)\b/i.test(title)
  );
}

function looksLikeAssignment(link, url, title) {
  const classText = `${link.className || ""} ${link.closest(".activity")?.className || ""}`;
  return (
    /\/mod\/assign\/view\.php/i.test(url) ||
    /(?:assign|assignment|과제|제출)/i.test(title) ||
    /(?:assign|assignment)/i.test(classText)
  );
}

function looksLikeVideo(link, url, title) {
  const classText = `${link.className || ""} ${link.closest(".activity")?.className || ""}`;
  return (
    /\/mod\/(vod|vplayer|video|kalvidres)\/view\.php/i.test(url) ||
    /(?:vod|video|vplayer|ubicast|media|commons)/i.test(url) ||
    /(?:vod|video|동영상|영상|녹화)/i.test(title) ||
    /(?:vod|video|media)/i.test(classText)
  );
}

function isDirectMaterialUrl(url) {
  return Boolean(url && (/\/(?:webservice\/)?pluginfile\.php/i.test(url) || MATERIAL_FILE_RE.test(url)));
}

function isDirectVideoUrl(url) {
  return Boolean(url && VIDEO_FILE_RE.test(url));
}

function isHlsUrl(url) {
  return Boolean(url && /\.m3u8(?:[?#]|$)/i.test(url));
}

function buildDownloadPath(kind, item, title, url) {
  const folder = kind === "videos" ? "" : (item.type === "assignment" ? "Assignments" : "Materials");
  const extension = getExtensionFromUrl(url) || (kind === "videos" ? "mp4" : "");
  const baseName = stripKnownExtension(title || item.title || "download");
  const filename = extension ? `${baseName}.${extension}` : baseName;
  return [
    "LearnUs",
    state.year || "UnknownYear",
    state.semester || "UnknownSemester",
    state.courseTitle || `course-${state.courseId}`,
    item.week || "general",
    folder,
    filename
  ].map(sanitizePathPart).filter(Boolean).join("/");
}

function getExtensionFromUrl(url) {
  try {
    const path = new URL(url).pathname;
    const match = path.match(/\.([a-z0-9]{2,5})$/i);
    return match ? match[1].toLowerCase() : "";
  } catch {
    const match = String(url).match(/\.([a-z0-9]{2,5})(?:[?#]|$)/i);
    return match ? match[1].toLowerCase() : "";
  }
}

function stripKnownExtension(value) {
  return sanitizePathPart(String(value || "download").replace(DIRECT_FILE_RE, ""));
}

function buildCourseFolderPath() {
  return [
    "LearnUs",
    state.year || "UnknownYear",
    state.semester || "UnknownSemester",
    state.courseTitle || `course-${state.courseId}`
  ].map(sanitizePathPart).filter(Boolean).join("/");
}

function sanitizePathPart(value) {
  return String(value || "")
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^\.+$/, "_")
    .slice(0, 100);
}

function getCourseTitle() {
  const candidates = [
    ".page-header-headings h1",
    "h1",
    ".course-content h2",
    "title"
  ];

  for (const selector of candidates) {
    const element = document.querySelector(selector);
    const text = selector === "title" ? document.title : element?.textContent;
    const cleaned = cleanText(text);
    if (cleaned) return cleaned.replace(/\s*-\s*LearnUs.*$/i, "");
  }

  return `course-${state.courseId || "unknown"}`;
}

function getCourseTermInfo() {
  const textSources = [
    document.querySelector(".page-header-headings")?.innerText,
    document.querySelector(".breadcrumb")?.innerText,
    document.querySelector(".navbar")?.innerText,
    document.body?.innerText
  ].filter(Boolean);
  const text = cleanText(textSources.join(" ")).slice(0, 20000);

  const pairedPatterns = [
    /(20\d{2})\s*(?:학년도|년)?\s*(1학기|2학기|여름학기|겨울학기)/,
    /(20\d{2})[-_\s]*(1|2)\s*학기/,
    /(20\d{2})[-_\s]*(여름|겨울)\s*학기/
  ];

  for (const pattern of pairedPatterns) {
    const match = text.match(pattern);
    if (match) {
      return {
        year: match[1],
        semester: normalizeSemesterLabel(match[2])
      };
    }
  }

  return {
    year: findYear(text) || "UnknownYear",
    semester: findSemester(text) || "UnknownSemester"
  };
}

function findYear(text) {
  const match = String(text || "").match(/\b(20\d{2})\b/);
  return match ? match[1] : "";
}

function findSemester(text) {
  const match = String(text || "").match(/(1학기|2학기|여름학기|겨울학기|1\s*학기|2\s*학기|여름\s*학기|겨울\s*학기)/);
  return match ? normalizeSemesterLabel(match[1]) : "";
}

function normalizeSemesterLabel(value) {
  const text = cleanText(value).replace(/\s+/g, "");
  if (text === "1" || text === "1학기") return "1학기";
  if (text === "2" || text === "2학기") return "2학기";
  if (text === "여름" || text === "여름학기") return "여름학기";
  if (text === "겨울" || text === "겨울학기") return "겨울학기";
  return text || "UnknownSemester";
}

function getLinkTitle(link) {
  const title =
    link.getAttribute("aria-label") ||
    link.getAttribute("title") ||
    link.querySelector(".instancename")?.textContent ||
    link.textContent ||
    "";
  return cleanText(title).replace(/\s*파일\s*$/i, "");
}

function getWeekTitle(element) {
  const section = element.closest("li.section, section, .course-section, .section, .activity-item, .activity");
  const sectionTitle =
    section?.querySelector(".sectionname, .section-title, h3, h4")?.textContent ||
    section?.closest("li.section, section, .course-section, .section")?.querySelector(".sectionname, .section-title, h3, h4")?.textContent ||
    "";
  return cleanText(sectionTitle) || "general";
}

function getActivityId(url) {
  try {
    const parsed = new URL(url);
    return parsed.searchParams.get("id") || parsed.pathname.split("/").filter(Boolean).pop() || "";
  } catch {
    return "";
  }
}

function normalizeUrlKey(url) {
  try {
    const parsed = new URL(url);
    parsed.hash = "";
    return parsed.toString();
  } catch {
    return url;
  }
}

function toAbsoluteUrl(rawUrl, baseUrl = location.href) {
  if (!rawUrl || /^javascript:/i.test(rawUrl) || rawUrl === "#") return "";
  try {
    return new URL(rawUrl, baseUrl).toString();
  } catch {
    return "";
  }
}

async function fetchText(url) {
  const response = await sendMessage({ type: "FETCH_TEXT", url });
  if (response.status >= 400) {
    throw new Error(`HTTP ${response.status}: ${url}`);
  }
  return response;
}

function sendMessage(payload) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(payload, (response) => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        reject(new Error(lastError.message));
      } else if (!response?.success) {
        reject(new Error(response?.message || "Extension request failed"));
      } else {
        resolve(response);
      }
    });
  });
}

function parseHtml(html, baseUrl) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const base = doc.createElement("base");
  base.href = baseUrl;
  doc.head.prepend(base);
  return doc;
}

function cleanExtractedUrl(url) {
  return decodeHtml(String(url || ""))
    .replace(/\\\//g, "/")
    .replace(/\\u0026/g, "&")
    .replace(/&amp;/g, "&")
    .trim()
    .replace(/[),;]+$/, "");
}

function decodeHtml(value) {
  const textArea = document.createElement("textarea");
  textArea.innerHTML = value || "";
  return textArea.value;
}

function cleanText(value) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim();
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function uniqueByUrl(items) {
  const seen = new Set();
  const result = [];
  for (const item of items) {
    if (!item?.url) continue;
    const key = normalizeUrlKey(item.url);
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}

function hashString(value) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
  }
  return Math.abs(hash).toString(36);
}
