// Background service worker for LearnUs Downloader

let authSession = null;
let coursesCache = [];
let lecturesCache = [];
let taskStatus = {};

// Handle extension icon click - open app in new tab (no popup)
chrome.action.onClicked.addListener((tab) => {
    // Check if app.html is already open
    chrome.tabs.query({ url: chrome.runtime.getURL('app.html') }, (tabs) => {
        if (tabs && tabs.length > 0) {
            // Focus existing tab
            chrome.tabs.update(tabs[0].id, { active: true });
            chrome.windows.update(tabs[0].windowId, { focused: true });
        } else {
            // Create new tab
    chrome.tabs.create({
        url: chrome.runtime.getURL('app.html')
            });
        }
    });
});

// Helper functions
function sanitizeFilename(filename) {
    filename = filename.replace(/[<>:"/\\|?*]/g, '_');
    filename = filename.trim().replace(/^\.+|\.+$/g, '');
    filename = filename.replace(/[_\s]+/g, '_');
    return filename.length > 200 ? filename.substring(0, 200) : filename;
}

function getOutputPath(year, semester, courseName, week, title, extension = 'mp4') {
    const yearClean = sanitizeFilename(year || 'Unknown');
    const semesterClean = sanitizeFilename(semester || 'Unknown');
    const courseClean = sanitizeFilename(courseName);
    const weekClean = sanitizeFilename(week);
    const titleClean = sanitizeFilename(title);
    const dirPath = `${yearClean}-${semesterClean}-${courseClean}`;
    const filename = `${weekClean}_${titleClean}.${extension}`;
    return `${dirPath}/${filename}`;
}

// Listen for messages from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    (async () => {
        try {
            switch (request.type) {
                case 'LOGIN':
                    const loginResult = await handleLogin(request.username, request.password);
                    sendResponse(loginResult);
                    break;
                    
                case 'FETCH_COURSES':
                    const forceRefresh = request.force_refresh || false;
                    const coursesResult = await fetchAllCourses(forceRefresh);
                    sendResponse(coursesResult);
                    break;
                    
                case 'CLEAR_CACHE':
                    await chrome.storage.local.remove(['courses_cache', 'lectures_cache', 'cache_timestamp']);
                    sendResponse({ success: true, message: 'Cache cleared' });
                    break;
                    
                case 'DOWNLOAD_LECTURES':
                    const downloadResult = await startDownload(request);
                    sendResponse(downloadResult);
                    break;
                    
                case 'GET_TASK_STATUS':
                    const status = taskStatus[request.task_id] || { status: 'not_found' };
                    sendResponse({ success: true, status });
                    break;
                    
                case 'PAUSE_TASK':
                    const pauseResult = pauseTask(request.task_id);
                    sendResponse(pauseResult);
                    break;
                    
                case 'RESUME_TASK':
                    const resumeResult = resumeTask(request.task_id);
                    sendResponse(resumeResult);
                    break;
                    
            case 'STOP_TASK':
                const stopResult = stopTask(request.task_id);
                sendResponse(stopResult);
                break;
                
            case 'CHECK_LOGIN_STATUS':
                const statusResult = await checkLoginStatus();
                sendResponse(statusResult);
                break;
                    
                case 'LIST_VIDEOS':
                    const videosResult = await listVideos();
                    sendResponse(videosResult);
                    break;
                    
                case 'DOWNLOAD_ITEM':
                case 'DOWNLOAD_ITEM_INLINE':
                    const downloadItemResult = await downloadItem(request);
                    sendResponse(downloadItemResult);
                    break;
                    
                case 'DOWNLOAD_SECTION':
                    const downloadSectionResult = await downloadSection(request);
                    sendResponse(downloadSectionResult);
                    break;
                    
                case 'DOWNLOAD_COURSE':
                    const downloadCourseResult = await downloadCourse(request);
                    sendResponse(downloadCourseResult);
                    break;
                    
                case 'UPDATE_ASSIGNMENT_DATA':
                    await updateAssignmentData(request.data);
                    sendResponse({ success: true });
                    break;
                    
                case 'GET_ASSIGNMENT_CONTEXT':
                    const contextResult = await getAssignmentContext(request);
                    sendResponse(contextResult);
                    break;
                    
                case 'ASK_AI':
                    const aiResult = await askAI(request);
                    sendResponse(aiResult);
                    break;
                    
                default:
                    console.error('Unknown request type:', request.type);
                    sendResponse({ success: false, message: 'Unknown request type' });
            }
        } catch (error) {
            console.error('Error handling message:', error);
            sendResponse({ success: false, message: error.message || 'Unknown error occurred' });
        }
    })();
    
    return true; // Keep channel open for async response
});

async function handleLogin(username, password) {
    console.log('handleLogin called with username:', username);
    
    if (!username || !password) {
        return { success: false, message: 'Username and password are required' };
    }
    
    try {
        // Direct login with RSA encryption (standalone extension)
        console.log('Attempting direct login...');
        const result = await performDirectLogin(username, password);
        console.log('Direct login result:', result);
        return result;
    } catch (error) {
        console.error('Login error:', error);
        return { success: false, message: 'Login error: ' + error.message };
    }
}

async function performDirectLogin(username, password) {
    const LEARNUS_ORIGIN = 'https://ys.learnus.org';
    const INFRA_ORIGIN = 'https://infra.yonsei.ac.kr';
    
    try {
        // Step 1: Get initial login page
        const response1 = await fetch(`${LEARNUS_ORIGIN}/passni/sso/spLogin2.php`, {
            headers: { 'Referer': 'https://ys.learnus.org' }
        });
        const html1 = await response1.text();
        
        // Parse S1 token from form
        const s1Match = html1.match(/name="S1"\s+value="([^"]+)"/);
        if (!s1Match) {
            return { success: false, message: 'Failed to get S1 token' };
        }
        const s1 = s1Match[1];
        
        // Step 2: Get SSO challenge and RSA key
        const formData2 = new URLSearchParams({
            app_id: 'ednetYonsei',
            retUrl: 'https://ys.learnus.org',
            failUrl: 'https://ys.learnus.org',
            baseUrl: 'https://ys.learnus.org',
            S1: s1,
            refererUrl: 'https://ys.learnus.org'
        });
        
        const response2 = await fetch(`${INFRA_ORIGIN}/sso/PmSSOService`, {
            method: 'POST',
            body: formData2,
            credentials: 'include'
        });
        const html2 = await response2.text();
        
        // Extract SSO challenge and RSA key
        const ssoChallengeMatch = html2.match(/var ssoChallenge\s*=\s*'([^']+)'/);
        const keyMatch = html2.match(/rsa\.setPublic\(\s*'([^']+)',\s*'([^']+)'/i);
        
        if (!ssoChallengeMatch || !keyMatch) {
            return { success: false, message: 'Failed to extract SSO challenge or RSA key' };
        }
        
        const ssoChallenge = ssoChallengeMatch[1];
        const keyModulus = keyMatch[1];
        const keyExponent = keyMatch[2];
        
        // Step 3: Encrypt credentials using RSA
        const loginData = {
            userid: username,
            userpw: password,
            ssoChallenge: ssoChallenge
        };
        
        // Use jsencrypt library for RSA encryption
        console.log('Encrypting login data...');
        const encrypted = await rsaEncrypt(JSON.stringify(loginData), keyModulus, keyExponent);
        if (!encrypted) {
            console.error('RSA encryption failed');
            return { 
                success: false, 
                message: 'RSA encryption failed. Check console for details or try logging in again.' 
            };
        }
        console.log('Encryption successful, length:', encrypted.length);
        
        const E2 = bytesToHex(encrypted);
        
        // Step 4: Authenticate
        const formData3 = new URLSearchParams({
            app_id: 'ednetYonsei',
            retUrl: 'https://ys.learnus.org',
            failUrl: 'https://ys.learnus.org',
            baseUrl: 'https://ys.learnus.org',
            loginType: 'invokeID',
            E2: E2,
            refererUrl: 'https://ys.learnus.org'
        });
        
        const response3 = await fetch(`${INFRA_ORIGIN}/sso/PmSSOAuthService`, {
            method: 'POST',
            body: formData3,
            credentials: 'include'
        });
        const html3 = await response3.text();
        
        // Parse E3 and E4 from response
        const e3Match = html3.match(/name="E3"\s+value="([^"]+)"/);
        const e4Match = html3.match(/name="E4"\s+value="([^"]+)"/);
        
        if (!e3Match || !e4Match) {
            return { success: false, message: 'Authentication failed - invalid response' };
        }
        
        const E3 = e3Match[1];
        const E4 = e4Match[1];
        
        // Step 5: Complete login
        const formData4 = new URLSearchParams({
            E3: E3,
            E4: E4
        });
        
        const response4 = await fetch(`${LEARNUS_ORIGIN}/passni/sso/spLoginData.php`, {
            method: 'POST',
            body: formData4,
            credentials: 'include'
        });
        
        // Step 6: Final verification
        const response5 = await fetch(`${LEARNUS_ORIGIN}/`, {
            credentials: 'include'
        });
        
        if (response5.ok) {
            // Store username only (NOT password - Chrome Web Store policy violation)
            // Password should never be stored in plaintext
            await chrome.storage.local.set({
                learnus_username: username,
                lastLoginTime: Date.now()
                // Password is NOT stored for security and policy compliance
            });
            return { success: true, message: 'Login successful', isLoggedIn: true };
        } else {
            return { success: false, message: 'Login verification failed' };
        }
    } catch (error) {
        return { success: false, message: 'Login error: ' + error.message };
    }
}

// RSA encryption helper using jsencrypt
async function rsaEncrypt(message, modulusHex, exponentHex) {
    try {
        console.log('Attempting RSA encryption...');
        console.log('Modulus length:', modulusHex.length, 'Exponent length:', exponentHex.length);
        
        // Load jsencrypt from local file if not available
        if (typeof JSEncrypt === 'undefined') {
            console.log('Loading jsencrypt library from local file...');
            try {
                // Import from local file in extension package
                await importScripts('/jsencrypt.min.js');
                console.log('jsencrypt loaded successfully');
            } catch (importError) {
                console.error('Failed to load jsencrypt:', importError);
                console.error('Ensure jsencrypt.min.js is in the extension root directory');
                return null;
            }
        }
        
        if (typeof JSEncrypt !== 'undefined') {
            const key = new JSEncrypt();
            
            // jsencrypt.setPublicKey expects a PEM format key
            // We need to construct it properly from modulus and exponent
            // Using the setPublic method directly with hex strings
            try {
                // Method 1: Try using setPublic with hex strings directly
                key.getKey().setPublic(modulusHex, exponentHex);
                const encrypted = key.encrypt(message);
                
                if (encrypted) {
                    console.log('RSA encryption successful');
                    return base64ToBytes(encrypted);
                } else {
                    console.log('RSA encryption returned null, trying alternative method...');
                }
            } catch (e) {
                console.log('First encryption method failed:', e.message);
            }
            
            // Method 2: Try constructing PEM key manually
            try {
                const modulusB64 = hexToBase64(modulusHex);
                const exponentB64 = hexToBase64(exponentHex);
                
                // Create a proper RSA public key in PEM format
                // This is a simplified version - proper ASN.1 encoding would be better
                const pemKey = `-----BEGIN RSA PUBLIC KEY-----
${modulusB64}
${exponentB64}
-----END RSA PUBLIC KEY-----`;
                
                key.setPublicKey(pemKey);
                const encrypted = key.encrypt(message);
                
                if (encrypted) {
                    console.log('RSA encryption successful (method 2)');
                    return base64ToBytes(encrypted);
                }
            } catch (e) {
                console.error('Second encryption method failed:', e);
            }
        } else {
            console.error('JSEncrypt is still undefined after import attempt');
        }
        
        console.error('All RSA encryption methods failed');
        return null;
    } catch (error) {
        console.error('RSA encryption error:', error);
        return null;
    }
}

function hexToBase64(hex) {
    const bytes = [];
    for (let i = 0; i < hex.length; i += 2) {
        bytes.push(parseInt(hex.substr(i, 2), 16));
    }
    const binary = String.fromCharCode(...bytes);
    return btoa(binary);
}

function base64ToBytes(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

function bytesToHex(bytes) {
    return Array.from(bytes)
        .map(b => b.toString(16).padStart(2, '0'))
        .join('')
        .toUpperCase();
}

async function fetchAllCourses(forceRefresh = false) {
    let mainTab = null;
    try {
        // Get or create LearnUs main page tab
        let tabs = await chrome.tabs.query({ url: 'https://ys.learnus.org/' });
        
        if (tabs.length === 0) {
            // Create tab and show it briefly so user can see what's happening
            mainTab = await chrome.tabs.create({ url: 'https://ys.learnus.org', active: true });
            // Wait for page to load
            await new Promise(resolve => {
                chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
                    if (tabId === mainTab.id && info.status === 'complete') {
                        chrome.tabs.onUpdated.removeListener(listener);
                        resolve();
                    }
                });
            });
        } else {
            mainTab = tabs[0];
            // Activate existing tab so user can see it
            await chrome.tabs.update(mainTab.id, { active: true });
        }
        
        // Inject content script to parse courses
        const courseResults = await chrome.scripting.executeScript({
            target: { tabId: mainTab.id },
            func: () => {
                // Parse courses from page
                const courses = [];
                const courseLists = document.querySelectorAll('ul.my-course-lists');
                
                courseLists.forEach(courseList => {
                    const courseBoxes = courseList.querySelectorAll('div.course-box');
                    
                    courseBoxes.forEach(courseBox => {
                        const courseLink = courseBox.querySelector('a.course-link');
                        if (!courseLink) return;
                        
                        const courseUrl = courseLink.href;
                        const courseIdMatch = courseUrl.match(/id=(\d+)/);
                        const courseId = courseIdMatch ? courseIdMatch[1] : '';
                        
                        const courseTitleDiv = courseBox.querySelector('div.course-title');
                        let courseName = 'Unknown Course';
                        let semester = '';
                        
                        if (courseTitleDiv) {
                            const h3 = courseTitleDiv.querySelector('h3');
                            if (h3) {
                                const semesterSpan = h3.querySelector('span.semester-name');
                                if (semesterSpan) {
                                    semester = semesterSpan.textContent.trim().replace(/[()]/g, '');
                                    semesterSpan.remove();
                                }
                                courseName = h3.textContent.trim();
                                // Remove course code in parentheses
                                courseName = courseName.replace(/\s*\([A-Z0-9.-]+\)\s*$/, '').trim();
                            }
                        }
                        
                        const profSpan = courseBox.querySelector('span.prof');
                        const professor = profSpan ? profSpan.textContent.trim() : '';
                        
                        courses.push({
                            course_id: courseId,
                            course_name: courseName,
                            course_url: courseUrl,
                            year: new Date().getFullYear().toString(),
                            semester: semester,
                            professor: professor
                        });
                    });
                });
                
                return courses;
            }
        });
        
        if (!courseResults || !courseResults[0] || !courseResults[0].result) {
            return { success: false, message: 'Failed to parse courses. Ensure you are on the LearnUs main page.' };
        }
        
        const courses = courseResults[0].result;
        
        // Check cache first (unless force refresh)
        if (!forceRefresh) {
            const stored = await chrome.storage.local.get(['courses_cache', 'lectures_cache', 'cache_timestamp']);
            const CACHE_DURATION = 60 * 60 * 1000; // 1 hour cache
            const now = Date.now();
            
            if (stored.courses_cache && stored.lectures_cache && stored.cache_timestamp) {
                const cacheAge = now - stored.cache_timestamp;
                if (cacheAge < CACHE_DURATION) {
                    console.log('Using cached courses data (age:', Math.round(cacheAge / 1000 / 60), 'minutes)');
                    coursesCache = stored.courses_cache;
                    lecturesCache = stored.lectures_cache;
                    
                    return {
                        success: true,
                        courses: stored.courses_cache.map(c => ({
                            course_id: c.course_id,
                            course_name: c.course_name,
                            year: c.year,
                            semester: c.semester,
                            professor: c.professor,
                            lectures: c.lectures ? c.lectures.map(l => ({
                                id: l.lecture_id || l.id,
                                title: l.title,
                                week: l.week,
                                status: l.status,
                                activity_url: l.activity_url,
                                course_name: l.course_name,
                                course_id: l.course_id
                            })) : [],
                            lecture_count: c.lectures ? c.lectures.length : 0
                        })),
                        total_courses: stored.courses_cache.length,
                        total_lectures: stored.lectures_cache.length,
                        from_cache: true
                    };
                }
            }
        }
        
        const coursesWithVideos = [];
        let allLectures = [];
        
        // Parallel processing: Create all tabs first, then parse in parallel
        const courseTabs = [];
        for (const course of courses) {
            const courseTab = await chrome.tabs.create({ 
                url: course.course_url, 
                active: false // All tabs in background
            });
            courseTabs.push({ course, tab: courseTab });
        }
            
        // Wait for all tabs to load in parallel
        await Promise.all(courseTabs.map(({ tab }) => {
            return new Promise(resolve => {
                chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
                    if (tabId === tab.id && info.status === 'complete') {
                        chrome.tabs.onUpdated.removeListener(listener);
                        resolve();
                    }
                });
            });
        }));
            
        // Parse all courses in parallel
        const parsePromises = courseTabs.map(async ({ course, tab }) => {
            try {
            const lectureResults = await chrome.scripting.executeScript({
                    target: { tabId: tab.id },
                func: () => {
                    const lectures = [];
                    const activityInstances = document.querySelectorAll('div.activityinstance');
                    let lectureCounter = 1;
                    
                    const courseIdMatch = window.location.href.match(/id=(\d+)/);
                    const courseId = courseIdMatch ? courseIdMatch[1] : '';
                    const courseName = document.title.replace(/강좌:\s*/, '');
                    
                    activityInstances.forEach(activityDiv => {
                        const link = activityDiv.querySelector('a[href*="mod/vod"]');
                        if (!link) return;
                        
                        const href = link.href;
                        const onclick = link.getAttribute('onclick') || '';
                        
                        let viewerUrl = null;
                        if (onclick.includes('mod/vod/viewer.php')) {
                            const match = onclick.match(/mod\/vod\/viewer\.php\?id=(\d+)/);
                            if (match) {
                                viewerUrl = `https://ys.learnus.org/mod/vod/viewer.php?id=${match[1]}`;
                            }
                        } else if (href.includes('mod/vod/view.php')) {
                            const match = href.match(/mod\/vod\/view\.php\?id=(\d+)/);
                            if (match) {
                                viewerUrl = `https://ys.learnus.org/mod/vod/viewer.php?id=${match[1]}`;
                            }
                        } else if (href.includes('mod/vod/viewer.php')) {
                            viewerUrl = href;
                        }
                        
                        if (!viewerUrl) return;
                        
                        const instancename = activityDiv.querySelector('span.instancename');
                        let title = instancename ? instancename.textContent.trim() : 'Unknown Lecture';
                        title = title.replace(/\s*동영상\s*$/, '');
                        
                        let week = 'Unknown Week';
                        const section = activityDiv.closest('li[id^="section-"], div[id^="section-"]');
                        if (section) {
                            const sectionId = section.id;
                            const sectionMatch = sectionId.match(/section-(\d+)/);
                            if (sectionMatch) {
                                week = `Week ${sectionMatch[1]}`;
                            }
                        }
                        
                        let status = 'New';
                        const actions = activityDiv.nextElementSibling;
                        if (actions && actions.classList.contains('actions')) {
                            const completionImg = actions.querySelector('img[src*="completion-auto-y"]');
                            if (completionImg) {
                                status = 'Done';
                            }
                        }
                        
                        const lectureId = `${courseId}_${lectureCounter}`;
                        lectures.push({
                            id: lectureId,
                            lecture_id: lectureId,
                            title: title,
                            week: week,
                            status: status,
                            activity_url: viewerUrl,
                            course_name: courseName,
                            course_id: courseId
                        });
                        
                        lectureCounter++;
                    });
                    
                    return lectures;
                }
            });
            
            // Close course tab after scraping (except first one which user can see)
            if (courses.indexOf(course) > 0) {
                chrome.tabs.remove(courseTab.id).catch(err => {
                    console.log('Error closing course tab:', err);
                });
            }
            
            if (lectureResults && lectureResults[0] && lectureResults[0].result) {
                const lectures = lectureResults[0].result;
                if (lectures.length > 0) {
                    course.lectures = lectures;
                    coursesWithVideos.push(course);
                    allLectures = allLectures.concat(lectures);
                }
            }
        }
        
        coursesCache = coursesWithVideos;
        lecturesCache = allLectures;
        
        // Save to cache
        await chrome.storage.local.set({
            courses_cache: coursesWithVideos,
            lectures_cache: allLectures,
            cache_timestamp: Date.now()
        });
        
        // Close main tab if we created it (keep it if it was already open)
        if (mainTab) {
            // Check if this tab was created by us (not already existing)
            const existingTabs = await chrome.tabs.query({ url: 'https://ys.learnus.org/' });
            if (existingTabs.length > 0 && existingTabs[0].id === mainTab.id) {
                // Tab was already open, don't close it
                // But we can minimize it by switching to extension tab
                try {
                    const extensionTabs = await chrome.tabs.query({ url: chrome.runtime.getURL('app.html') });
                    if (extensionTabs.length > 0) {
                        await chrome.tabs.update(extensionTabs[0].id, { active: true });
                    }
                } catch (e) {
                    // Ignore errors
                }
            } else {
                // We created this tab, close it
                chrome.tabs.remove(mainTab.id).catch(err => {
                    console.log('Error closing main tab:', err);
                });
            }
        }
        
        return {
            success: true,
            courses: coursesWithVideos.map(c => ({
                course_id: c.course_id,
                course_name: c.course_name,
                year: c.year,
                semester: c.semester,
                professor: c.professor,
                lectures: c.lectures.map(l => ({
                    id: l.lecture_id,
                    title: l.title,
                    week: l.week,
                    status: l.status,
                    activity_url: l.activity_url,
                    course_name: l.course_name,
                    course_id: l.course_id
                })),
                lecture_count: c.lectures.length
            })),
            total_courses: coursesWithVideos.length,
            total_lectures: allLectures.length
        };
    } catch (error) {
        return { success: false, message: 'Error fetching courses: ' + error.message };
    }
}

async function startDownload(request) {
    try {
        const { lecture_ids } = request;
        
        const taskId = `download_${Date.now()}`;
        taskStatus[taskId] = {
            status: 'running',
            progress: 0,
            total: lecture_ids.length,
            completed: 0,
            failed: 0,
            messages: [],
            paused: false,
            stopped: false,
            current_lecture_index: 0
        };
        
        // Start download in background
        downloadTask(taskId, lecture_ids);
        
        return { success: true, task_id: taskId };
    } catch (error) {
        return { success: false, message: 'Error starting download: ' + error.message };
    }
}

async function downloadTask(taskId, lectureIds) {
    try {
        let completed = 0;
        let failed = 0;
        
        for (let idx = 0; idx < lectureIds.length; idx++) {
            // Check if task is stopped (check status object directly)
            if (taskStatus[taskId] && taskStatus[taskId].stopped) {
                taskStatus[taskId].messages.push('Download stopped by user');
                taskStatus[taskId].status = 'stopped';
                break;
            }
            
            // Wait if paused
            while (taskStatus[taskId] && taskStatus[taskId].paused && !taskStatus[taskId].stopped) {
                await new Promise(resolve => setTimeout(resolve, 500));
            }
            
            // Check again after pause
            if (taskStatus[taskId] && taskStatus[taskId].stopped) {
                taskStatus[taskId].messages.push('Download stopped by user');
                taskStatus[taskId].status = 'stopped';
                break;
            }
            
            try {
                // Update current lecture index
                taskStatus[taskId].current_lecture_index = idx;
                
                // Get lecture ID from array
                const lectureId = lectureIds[idx];
                if (!lectureId) {
                    taskStatus[taskId].messages.push(`No lecture ID at index ${idx}`);
                    failed++;
                    continue;
                }
                
                // Find lecture (check both id and lecture_id for compatibility)
                let lecture = lecturesCache.find(l => l.lecture_id === lectureId || l.id === lectureId);
                if (!lecture) {
                    taskStatus[taskId].messages.push(`Lecture ${lectureId} not found in cache. Cache has ${lecturesCache.length} lectures. Refreshing cache...`);
                    // Try to refresh cache and retry
                    const refreshResult = await fetchAllCourses();
                    if (refreshResult.success) {
                        lecture = lecturesCache.find(l => l.lecture_id === lectureId || l.id === lectureId);
                        if (!lecture) {
                            taskStatus[taskId].messages.push(`Lecture ${lectureId} still not found after cache refresh`);
                            failed++;
                            continue;
                        }
                        taskStatus[taskId].messages.push(`Found lecture after cache refresh: ${lecture.title}`);
                    } else {
                        taskStatus[taskId].messages.push(`Failed to refresh cache: ${refreshResult.message || 'Unknown error'}`);
                        failed++;
                        continue;
                    }
                }
                
                // Find course info
                const course = coursesCache.find(c => c.course_id === lecture.course_id);
                const year = course ? course.year : new Date().getFullYear().toString();
                const semester = course ? course.semester : 'Unknown';
                
                // Extract video URL by opening viewer page
                taskStatus[taskId].messages.push(`Extracting video URL for: ${lecture.title}`);
                const videoTab = await chrome.tabs.create({ url: lecture.activity_url, active: false });
                
                // Wait for page to load
                await new Promise(resolve => {
                    chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
                        if (tabId === videoTab.id && info.status === 'complete') {
                            chrome.tabs.onUpdated.removeListener(listener);
                            resolve();
                        }
                    });
                });
                
                // Extract video URL
                const videoResults = await chrome.scripting.executeScript({
                    target: { tabId: videoTab.id },
                    func: () => {
                        // Look for source tags with m3u8
                        const sources = document.querySelectorAll('source[src]');
                        for (const source of sources) {
                            const src = source.getAttribute('src');
                            const type = source.getAttribute('type') || '';
                            if (src.includes('.m3u8') || type.includes('mpegURL') || type.includes('mpegurl')) {
                                return src.startsWith('http') ? src : `https:${src}`;
                            }
                            if (src.endsWith('.mp4') || type.includes('mp4')) {
                                return src.startsWith('http') ? src : `https:${src}`;
                            }
                        }
                        
                        // Look for video tags
                        const videos = document.querySelectorAll('video');
                        for (const video of videos) {
                            const src = video.getAttribute('src');
                            if (src && (src.endsWith('.mp4') || src.endsWith('.m3u8'))) {
                                return src.startsWith('http') ? src : `https:${src}`;
                            }
                        }
                        
                        // Look in HTML content
                        const html = document.documentElement.outerHTML;
                        const m3u8Match = html.match(/(https?:\/\/[^\s"'<>]+\.m3u8[^\s"'<>]*)/i);
                        if (m3u8Match) return m3u8Match[1];
                        
                        const mp4Match = html.match(/(https?:\/\/[^\s"'<>]+\.mp4[^\s"'<>]*)/i);
                        if (mp4Match) return mp4Match[1];
                        
                        return null;
                    }
                });
                
                // Close video tab after extracting URL (except first one which user can see)
                if (idx > 0) {
                    chrome.tabs.remove(videoTab.id).catch(err => {
                        console.log('Error closing video tab:', err);
                    });
                } else {
                    // For first video, switch back to extension tab after a short delay
                    setTimeout(async () => {
                        try {
                            const extensionTabs = await chrome.tabs.query({ url: chrome.runtime.getURL('app.html') });
                            if (extensionTabs.length > 0) {
                                await chrome.tabs.update(extensionTabs[0].id, { active: true });
                            }
                        } catch (e) {
                            // Ignore errors
                        }
                    }, 2000);
                }
                
                const videoUrl = videoResults && videoResults[0] && videoResults[0].result 
                    ? videoResults[0].result 
                    : null;
                
                if (!videoUrl) {
                    taskStatus[taskId].messages.push(`Failed to extract video URL for: ${lecture.title}`);
                    failed++;
                    continue;
                }
                
                // Check if it's an m3u8 (HLS) stream
                const isM3U8 = videoUrl.includes('.m3u8') || videoUrl.endsWith('.m3u8');
                
                if (isM3U8) {
                    // HLS streams require special handling
                    taskStatus[taskId].messages.push(`⚠️ Detected HLS stream (.m3u8) for: ${lecture.title}`);
                    taskStatus[taskId].messages.push(`❌ HLS streams (.m3u8) cannot be downloaded directly by browser extensions.`);
                    taskStatus[taskId].messages.push(`💡 HLS is a streaming format that requires server-side processing with ffmpeg.`);
                    taskStatus[taskId].messages.push(`For HLS videos, use the Python version (python main.py --web) with ffmpeg support.`);
                    failed++;
                    
                    // Update progress
                    taskStatus[taskId].progress = Math.round(((idx + 1) / lectureIds.length) * 100);
                    taskStatus[taskId].failed = failed;
                    continue;
                }
                
                // Generate output path
                const outputPath = getOutputPath(
                    year, semester, lecture.course_name,
                    lecture.week, lecture.title
                );
                
                // Download video using Chrome downloads API
                taskStatus[taskId].messages.push(`Downloading: ${lecture.title}`);
                const downloadId = await downloadVideo(videoUrl, outputPath);
                
                if (downloadId) {
                    completed++;
                    taskStatus[taskId].messages.push(`Downloaded: ${lecture.title}`);
                } else {
                    failed++;
                    taskStatus[taskId].messages.push(`Failed to download: ${lecture.title}`);
                }
                
                // Update progress
                taskStatus[taskId].progress = Math.round(((idx + 1) / lectureIds.length) * 100);
                taskStatus[taskId].completed = completed;
                taskStatus[taskId].failed = failed;
                
            } catch (error) {
                failed++;
                const currentLectureId = lectureIds[idx] || 'unknown';
                taskStatus[taskId].messages.push(`Error processing lecture ${currentLectureId}: ${error.message}`);
            }
        }
        
        taskStatus[taskId].status = 'completed';
        taskStatus[taskId].messages.push('All downloads completed!');
        
    } catch (error) {
        taskStatus[taskId].status = 'error';
        taskStatus[taskId].messages.push(`Task error: ${error.message}`);
    }
}

async function downloadVideo(videoUrl, outputPath) {
    try {
        // Check if it's m3u8 - should have been handled earlier, but double-check
        if (videoUrl.includes('.m3u8')) {
            console.error('m3u8 file should be handled before downloadVideo');
            return null;
        }
        
        // Get download directory setting
        const stored = await chrome.storage.local.get(['download_dir']);
        const downloadDir = stored.download_dir || '';
        
        // Construct full path
        let fullPath = outputPath;
        if (downloadDir) {
            // Prepend download directory to path
            // Remove any leading/trailing slashes from downloadDir
            const cleanDir = downloadDir.replace(/^[/\\]+|[/\\]+$/g, '');
            fullPath = `${cleanDir}/${outputPath}`;
        }
        
        console.log('Downloading video to:', fullPath);
        console.log('Video URL:', videoUrl);
        
        // For regular video files (not m3u8), use Chrome downloads API
        // Chrome will use the browser's cookies automatically for authenticated downloads
        return new Promise((resolve) => {
            chrome.downloads.download({
                url: videoUrl,
                filename: fullPath,
                saveAs: false
            }, (downloadId) => {
                if (chrome.runtime.lastError) {
                    console.error('Download error:', chrome.runtime.lastError);
                    
                    // If direct download fails, try fetching with credentials
                    // This handles cases where explicit cookie passing is needed
                    fetch(videoUrl, {
                        credentials: 'include',
                        mode: 'cors'
                    }).then(response => {
                        if (!response.ok) {
                            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                        }
                        
                        // Check content type to ensure it's a video
                        const contentType = response.headers.get('content-type') || '';
                        if (!contentType.includes('video') && !contentType.includes('application/octet-stream')) {
                            console.warn('Unexpected content type:', contentType);
                        }
                        
                        return response.blob();
                    }).then(blob => {
                        // Check blob size - if it's very small, it might be an error page or m3u8 file
                        if (blob.size < 10000) { // Increased threshold to 10KB
                            console.warn('Downloaded file is very small (' + blob.size + ' bytes), checking if it\'s an m3u8 playlist...');
                            
                            // Check if it's an m3u8 playlist file
                            blob.text().then(text => {
                                if (text.includes('#EXTM3U') || text.includes('.m3u8') || text.trim().startsWith('#EXT')) {
                                    console.error('❌ Downloaded file is an m3u8 playlist file, not the actual video!');
                                    console.error('This video uses HLS streaming and requires server-side processing with ffmpeg.');
                                    console.error('For HLS videos, use the Python version (python main.py --web) with ffmpeg support.');
                                } else if (text.includes('error') || text.includes('Error') || text.includes('404')) {
                                    console.error('❌ Downloaded file appears to be an error page');
                                } else {
                                    console.warn('⚠️ File is small but might be valid. Size:', blob.size, 'bytes');
                                }
                            }).catch(() => {
                                // If text() fails, it might be binary - check size
                                if (blob.size < 1000) {
                                    console.error('❌ File is too small to be a valid video file');
                                }
                            });
                            
                            // Don't download if it's too small (likely m3u8 or error)
                            if (blob.size < 1000) {
                                resolve(null);
                                return;
                            }
                        }
                        
                        // Additional check: verify it's actually a video file
                        // Check MIME type if available
                        const contentType = response.headers.get('content-type') || '';
                        if (contentType && !contentType.includes('video') && !contentType.includes('application/octet-stream') && !contentType.includes('binary')) {
                            console.warn('⚠️ Unexpected content type:', contentType);
                        }
                        
                        // Convert blob to data URL for download
                        const reader = new FileReader();
                        reader.onloadend = () => {
                            const dataUrl = reader.result;
                            chrome.downloads.download({
                                url: dataUrl,
                                filename: fullPath,
                                saveAs: false
                            }, (downloadId2) => {
                                if (chrome.runtime.lastError) {
                                    console.error('Data URL download error:', chrome.runtime.lastError);
                                    resolve(null);
                                } else {
                                    console.log('Downloaded via blob/data URL method');
                                    resolve(downloadId2);
                                }
                            });
                        };
                        reader.onerror = () => {
                            console.error('FileReader error');
                            resolve(null);
                        };
                        reader.readAsDataURL(blob);
                    }).catch(fetchError => {
                        console.error('Fetch fallback error:', fetchError);
                        resolve(null);
                    });
                } else {
                    console.log('Download started with ID:', downloadId);
                    resolve(downloadId);
                }
            });
        });
    } catch (error) {
        console.error('Error downloading video:', error);
        return null;
    }
}

function pauseTask(taskId) {
    if (!taskStatus[taskId]) {
        return { success: false, message: 'Task not found' };
    }
    
    if (taskStatus[taskId].status !== 'running') {
        return { success: false, message: 'Task is not running' };
    }
    
    taskStatus[taskId].paused = true;
    taskStatus[taskId].status = 'paused';
    taskStatus[taskId].messages.push('Download paused by user');
    
    return { success: true, message: 'Task paused' };
}

function resumeTask(taskId) {
    if (!taskStatus[taskId]) {
        return { success: false, message: 'Task not found' };
    }
    
    if (taskStatus[taskId].status !== 'paused') {
        return { success: false, message: 'Task is not paused' };
    }
    
    taskStatus[taskId].paused = false;
    taskStatus[taskId].status = 'running';
    taskStatus[taskId].messages.push('Download resumed');
    
    return { success: true, message: 'Task resumed' };
}

function stopTask(taskId) {
    if (!taskStatus[taskId]) {
        return { success: false, message: 'Task not found' };
    }
    
    if (['completed', 'error', 'stopped'].includes(taskStatus[taskId].status)) {
        return { success: false, message: 'Task is already finished' };
    }
    
    taskStatus[taskId].stopped = true;
    taskStatus[taskId].paused = false;
    taskStatus[taskId].status = 'stopped';
    taskStatus[taskId].messages.push('Download stopped by user');
    
    return { success: true, message: 'Task stopped' };
}

// Check login status using session (like yontil-main)
async function checkLoginStatus() {
    try {
        // Always check actual session status by fetching LearnUs main page
        // This works even if user logged in via browser (not through extension)
        const response = await fetch('https://ys.learnus.org/', {
            method: 'GET',
            credentials: 'include',
            signal: AbortSignal.timeout(5000)
        });
        
        if (!response.ok) {
            return { isLoggedIn: false };
        }
        
        const text = await response.text();
        
        // Check if we're on login page (not logged in) or main page (logged in)
        // yontil-main logic: if logout link exists, we're logged in
        // If the page contains logout.php link, user is logged in
        const isLoginPage = !text.includes('https://ys.learnus.org/login/logout.php');
        
        if (isLoginPage) {
            // User is on login page, not logged in
            return { isLoggedIn: false };
        }
        
        // User is logged in - update last login time
        await chrome.storage.local.set({ lastLoginTime: Date.now() });
        
        return { isLoggedIn: true };
    } catch (error) {
        console.error('Error checking login status:', error);
        // On error, check if we have a recent login time as fallback
        const stored = await chrome.storage.local.get(['lastLoginTime']);
        const lastLoginTime = stored.lastLoginTime;
        
        if (lastLoginTime && Date.now() - lastLoginTime < 30 * 60 * 1000) {
            return { isLoggedIn: true };
        }
        
        return { isLoggedIn: false };
    }
}

async function listVideos() {
    try {
        // Get download directory setting
        const stored = await chrome.storage.local.get(['download_dir']);
        const downloadDir = stored.download_dir || '';
        
        // Query Chrome downloads to find video files
        const downloads = await new Promise((resolve) => {
            chrome.downloads.search({}, resolve);
        });
        
        const videos = [];
        const videoExtensions = ['.mp4', '.avi', '.mkv', '.mov', '.webm', '.flv'];
        
        // Filter for video files and get metadata
        for (const download of downloads) {
            if (download.state === 'complete' && download.filename) {
                // Filter by download directory if set
                if (downloadDir) {
                    // Check if filename starts with the download directory
                    const normalizedPath = download.filename.replace(/\\/g, '/');
                    const normalizedDir = downloadDir.replace(/\\/g, '/');
                    if (!normalizedPath.includes(normalizedDir)) {
                        continue; // Skip files not in the download directory
                    }
                } else {
                    // If no download_dir is set, only show files that match our pattern
                    // (year-semester-course_name structure)
                    const pathParts = download.filename.split(/[/\\]/);
                    const hasLearnUsPattern = pathParts.some(part => {
                        // Check for year-semester-course pattern
                        return /^\d{4}-.+/.test(part);
                    });
                    if (!hasLearnUsPattern) {
                        // Also check if it's in a subdirectory that might be our download folder
                        const lastDir = pathParts[pathParts.length - 2];
                        if (!lastDir || !lastDir.includes('-')) {
                            continue; // Skip files that don't match our pattern
                        }
                    }
                }
                
                const filename = download.filename.split(/[/\\]/).pop();
                const ext = filename.substring(filename.lastIndexOf('.')).toLowerCase();
                
                if (videoExtensions.includes(ext)) {
                    // Extract course info from path
                    const pathParts = download.filename.split(/[/\\]/);
                    let courseInfo = 'Unknown';
                    if (pathParts.length >= 2) {
                        // Look for year-semester-course_name pattern
                        const dirName = pathParts[pathParts.length - 2];
                        if (dirName.includes('-')) {
                            courseInfo = dirName;
                        }
                    }
                    
                    // Check for transcript and analysis files
                    const basePath = download.filename.substring(0, download.filename.lastIndexOf('.'));
                    const transcriptPath = basePath + '.txt';
                    const transcriptJsonPath = basePath + '.json';
                    const analysisPath = basePath + '_analysis/frame_analysis.json';
                    
                    // File existence check requires local server API
                    const hasTranscript = false;
                    const hasAnalysis = false; // Would need to check via API
                    
                    videos.push({
                        name: filename,
                        path: download.filename,
                        size: download.totalBytes || 0,
                        modified: download.endTime ? new Date(download.endTime).getTime() / 1000 : Date.now() / 1000,
                        course: courseInfo,
                        has_transcript: hasTranscript,
                        has_analysis: hasAnalysis
                    });
                }
            }
        }
        
        // Sort by modified time (newest first)
        videos.sort((a, b) => b.modified - a.modified);
        
        return {
            success: true,
            videos: videos,
            count: videos.length
        };
    } catch (error) {
        return { success: false, message: 'Error listing videos: ' + error.message };
    }
}

// Download single item inline (from injected script)
async function downloadItem(request) {
    try {
        const { type, url, lecture_id, title, course_url } = request;
        
        if (type === 'video') {
            // For video, we need to extract the video URL first
            // Open video page in background tab
            const tab = await chrome.tabs.create({ url: url, active: false });
            await new Promise(resolve => {
                chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
                    if (tabId === tab.id && info.status === 'complete') {
                        chrome.tabs.onUpdated.removeListener(listener);
                        resolve();
                    }
                });
            });
            
            // Extract video URL
            const results = await chrome.scripting.executeScript({
                target: { tabId: tab.id },
                func: () => {
                    const sources = document.querySelectorAll('source[src]');
                    for (const source of sources) {
                        const src = source.getAttribute('src');
                        if (src && (src.includes('.m3u8') || src.includes('.mp4'))) {
                            return src.startsWith('http') ? src : `https:${src}`;
                        }
                    }
                    const videos = document.querySelectorAll('video');
                    for (const video of videos) {
                        const src = video.getAttribute('src');
                        if (src && (src.includes('.mp4') || src.includes('.m3u8'))) {
                            return src.startsWith('http') ? src : `https:${src}`;
                        }
                    }
                    return null;
                }
            });
            
            chrome.tabs.remove(tab.id);
            
            if (results && results[0] && results[0].result) {
                const videoUrl = results[0].result;
                
                // Check if m3u8 - show warning
                if (videoUrl.includes('.m3u8')) {
                    return {
                        success: false,
                        message: '이 동영상은 스트리밍 방식(m3u8)입니다. Python 버전을 사용하거나 수동으로 다운로드해주세요.'
                    };
                }
                
                // Start download using Chrome downloads API
                const stored = await chrome.storage.local.get(['download_dir']);
                const downloadDir = stored.download_dir || '';
                
                // Generate filename
                const sanitizedTitle = title.replace(/[<>:"/\\|?*]/g, '_').substring(0, 100);
                const filename = downloadDir 
                    ? `${downloadDir}/${sanitizedTitle}.mp4`
                    : `${sanitizedTitle}.mp4`;
                
                chrome.downloads.download({
                    url: videoUrl,
                    filename: filename,
                    saveAs: false
                });
                
                return { success: true, message: '다운로드가 시작되었습니다.' };
            } else {
                return { success: false, message: '동영상 URL을 찾을 수 없습니다.' };
            }
        } else if (type === 'assignment' || type === 'file' || type === 'quiz') {
            // Direct download for files
            const sanitizedTitle = title.replace(/[<>:"/\\|?*]/g, '_').substring(0, 100);
            const ext = url.includes('.pdf') ? 'pdf' : (url.includes('.doc') ? 'doc' : 'pdf');
            
            chrome.downloads.download({
                url: url,
                filename: `${sanitizedTitle}.${ext}`,
                saveAs: false
            });
            
            return { success: true, message: '다운로드가 시작되었습니다.' };
        }
        
        return { success: false, message: '알 수 없는 항목 타입입니다.' };
    } catch (error) {
        console.error('Error downloading item:', error);
        return { success: false, message: error.message };
    }
}

// Download section (week) - all videos in a section
async function downloadSection(request) {
    try {
        const { lecture_ids, course_url, section_title } = request;
        
        if (!lecture_ids || lecture_ids.length === 0) {
            return { success: false, message: '다운로드할 항목이 없습니다.' };
        }
        
        // Use existing download task system
        const downloadRequest = {
            lecture_ids: lecture_ids,
            course_url: course_url
        };
        
        const result = await startDownload(downloadRequest);
        return result;
    } catch (error) {
        console.error('Error downloading section:', error);
        return { success: false, message: error.message };
    }
}

// Update assignment data for dashboard
async function updateAssignmentData(assignments) {
    try {
        const stored = await chrome.storage.local.get(['assignments_cache']);
        const existing = stored.assignments_cache || [];
        
        // Merge with existing (avoid duplicates)
        const merged = [...existing];
        assignments.forEach(newItem => {
            const existingIndex = merged.findIndex(a => a.id === newItem.id && a.course_id === newItem.course_id);
            if (existingIndex >= 0) {
                merged[existingIndex] = { ...merged[existingIndex], ...newItem };
            } else {
                merged.push(newItem);
            }
        });
        
        await chrome.storage.local.set({ assignments_cache: merged });
        return { success: true };
    } catch (error) {
        console.error('Error updating assignment data:', error);
        return { success: false, message: error.message };
    }
}

// RAG Helper class (from rag-helper.js)
class RAGHelper {
    constructor() {
        this.dbName = 'LearnUsMaterials';
        this.dbVersion = 1;
        this.db = null;
    }
    
    async init() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(this.dbName, this.dbVersion);
            
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                this.db = request.result;
                resolve();
            };
            
            request.onupgradeneeded = (event) => {
                const db = event.target.result;
                
                if (!db.objectStoreNames.contains('materials')) {
                    const materialsStore = db.createObjectStore('materials', { keyPath: 'id', autoIncrement: true });
                    materialsStore.createIndex('courseId', 'courseId', { unique: false });
                    materialsStore.createIndex('type', 'type', { unique: false });
                }
                
                if (!db.objectStoreNames.contains('transcripts')) {
                    const transcriptsStore = db.createObjectStore('transcripts', { keyPath: 'id', autoIncrement: true });
                    transcriptsStore.createIndex('lectureId', 'lectureId', { unique: false });
                    transcriptsStore.createIndex('courseId', 'courseId', { unique: false });
                }
                
                if (!db.objectStoreNames.contains('assignments')) {
                    const assignmentsStore = db.createObjectStore('assignments', { keyPath: 'id', autoIncrement: true });
                    assignmentsStore.createIndex('courseId', 'courseId', { unique: false });
                }
            };
        });
    }
    
    async storeMaterial(courseId, type, content, metadata = {}) {
        if (!this.db) await this.init();
        
        const transaction = this.db.transaction(['materials'], 'readwrite');
        const store = transaction.objectStore('materials');
        
        return store.add({
            courseId,
            type,
            content,
            metadata,
            timestamp: Date.now()
        });
    }
    
    async getCourseMaterials(courseId) {
        if (!this.db) await this.init();
        
        const transaction = this.db.transaction(['materials', 'transcripts'], 'readonly');
        const materialsStore = transaction.objectStore('materials');
        const transcriptsStore = transaction.objectStore('transcripts');
        
        const materials = [];
        const transcripts = [];
        
        const materialsIndex = materialsStore.index('courseId');
        const materialsRequest = materialsIndex.getAll(courseId);
        materialsRequest.onsuccess = () => {
            materials.push(...materialsRequest.result);
        };
        
        const transcriptsIndex = transcriptsStore.index('courseId');
        const transcriptsRequest = transcriptsIndex.getAll(courseId);
        transcriptsRequest.onsuccess = () => {
            transcripts.push(...transcriptsRequest.result);
        };
        
        return new Promise((resolve) => {
            transaction.oncomplete = () => {
                resolve({ materials, transcripts });
            };
        });
    }
    
    async searchRelevantContent(courseId, query, limit = 5) {
        const { materials, transcripts } = await this.getCourseMaterials(courseId);
        const allContent = [...materials, ...transcripts];
        
        const queryLower = query.toLowerCase();
        const queryWords = queryLower.split(/\s+/);
        
        const scored = allContent.map(item => {
            const contentText = (item.content || '').toLowerCase();
            let score = 0;
            
            queryWords.forEach(word => {
                if (contentText.includes(word)) {
                    score += 1;
                }
            });
            
            return { item, score };
        });
        
        return scored
            .filter(s => s.score > 0)
            .sort((a, b) => b.score - a.score)
            .slice(0, limit)
            .map(s => s.item);
    }
    
    async buildContext(courseId, query) {
        const relevant = await this.searchRelevantContent(courseId, query);
        
        let context = '강의 자료 컨텍스트:\n\n';
        
        relevant.forEach((item, index) => {
            context += `[${index + 1}] ${item.type}: ${item.metadata.title || '제목 없음'}\n`;
            context += `${item.content.substring(0, 500)}...\n\n`;
        });
        
        return context;
    }
}

// Initialize RAG helper
let ragHelper = new RAGHelper();

// Get assignment context for AI
async function getAssignmentContext(request) {
    try {
        const { assignment_url, course_url } = request;
        const courseIdMatch = course_url.match(/[?&]id=(\d+)/);
        const courseId = courseIdMatch ? courseIdMatch[1] : '';
        
        if (!courseId) {
            return { success: false, message: '강좌 ID를 찾을 수 없습니다.' };
        }
        
        // Get course materials from IndexedDB
        const context = await ragHelper.buildContext(courseId, '과제');
        
        return { success: true, context };
    } catch (error) {
        console.error('Error getting assignment context:', error);
        return { success: false, message: error.message };
    }
}

// Ask AI (placeholder)
async function askAI(request) {
    try {
        const { question, context } = request;
        const stored = await chrome.storage.local.get(['llm_provider', 'openai_api_key', 'google_api_key', 'ollama_url']);
        
        // Placeholder for LLM API
        return {
            success: true,
            answer: `질문: "${question}"\n\nAI 기능은 아직 구현되지 않았습니다.`
        };
    } catch (error) {
        console.error('Error asking AI:', error);
        return { success: false, message: error.message };
    }
}

