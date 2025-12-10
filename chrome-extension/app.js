// Main app script for Chrome extension
// Consolidated from popup.js with enhanced features

let currentTaskId = null;
let coursesData = [];
let currentYear = null;
let currentSemester = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    // Check login status and load UI
    await checkLoginStatus();
    
    // Load feature toggle states
    await loadToggleStates();
    
    // Set up event listeners
    setupEventListeners();
    
    // Auto-refresh on visibility change (window focus)
    document.addEventListener('visibilitychange', async () => {
        if (!document.hidden && document.getElementById('mainPage') && !document.getElementById('mainPage').classList.contains('hidden')) {
            // Auto-refresh courses when page becomes visible
            await discoverAndLoadCourses(false);
        }
    });
    
    // Auto-refresh courses when navigating to main page
    const navMain = document.getElementById('navMain');
    if (navMain) {
        navMain.addEventListener('click', async () => {
            await discoverAndLoadCourses(false);
        });
    }
});

// Setup event listeners
function setupEventListeners() {
    // Login
    const loginBtn = document.getElementById('loginBtn');
    if (loginBtn) {
        loginBtn.addEventListener('click', handleLogin);
    }
    
    // Navigation
    document.getElementById('navMain')?.addEventListener('click', () => showMainPage());
    document.getElementById('navVideos')?.addEventListener('click', () => showVideosPage());
    document.getElementById('navSettings')?.addEventListener('click', () => showSettingsPage());
    
    // Course actions
    document.getElementById('refreshBtn')?.addEventListener('click', () => discoverAndLoadCourses(true));
    document.getElementById('selectAllBtn')?.addEventListener('click', selectAll);
    document.getElementById('selectNoneBtn')?.addEventListener('click', selectNone);
    document.getElementById('downloadBtn')?.addEventListener('click', startDownload);
    document.getElementById('logoutBtn')?.addEventListener('click', handleLogout);
    
    // Semester navigation
    document.getElementById('toggleSemesterBtn')?.addEventListener('click', toggleSemesterNav);
    document.getElementById('loadSemesterBtn')?.addEventListener('click', loadCustomSemester);
    
    // Videos page
    document.getElementById('refreshVideosBtn')?.addEventListener('click', loadVideos);
    
    // Settings
    document.getElementById('saveDownloadSettingsBtn')?.addEventListener('click', saveDownloadSettings);
    document.getElementById('saveLLMSettingsBtn')?.addEventListener('click', saveLLMSettings);
    
    // Toggle switches
    document.getElementById('toggleInlineButtons')?.addEventListener('click', toggleInlineButtons);
    document.getElementById('toggleAssignmentAlerts')?.addEventListener('click', toggleAssignmentAlerts);
    
    // Assignment dashboard filters
    document.getElementById('hideCompleted')?.addEventListener('change', () => loadDashboard());
    document.getElementById('hideIgnored')?.addEventListener('change', () => loadDashboard());
}

// Load toggle states from storage
async function loadToggleStates() {
    const stored = await chrome.storage.local.get(['enable_inline_buttons', 'enable_assignment_alerts']);
    const toggleInline = document.getElementById('toggleInlineButtons');
    const toggleAlerts = document.getElementById('toggleAssignmentAlerts');
    
    if (toggleInline) {
        toggleInline.classList.toggle('active', stored.enable_inline_buttons !== false);
    }
    if (toggleAlerts) {
        toggleAlerts.classList.toggle('active', stored.enable_assignment_alerts !== false);
    }
}

// Toggle inline buttons
async function toggleInlineButtons() {
    const toggle = document.getElementById('toggleInlineButtons');
    const enabled = !toggle.classList.contains('active');
    
    toggle.classList.toggle('active');
    await chrome.storage.local.set({ enable_inline_buttons: enabled });
    
    // Reload LearnUs tabs to apply
    chrome.tabs.query({ url: 'https://ys.learnus.org/*' }, (tabs) => {
        tabs.forEach(tab => chrome.tabs.reload(tab.id));
    });
}

// Toggle assignment alerts
async function toggleAssignmentAlerts() {
    const toggle = document.getElementById('toggleAssignmentAlerts');
    const enabled = !toggle.classList.contains('active');
    
    toggle.classList.toggle('active');
    await chrome.storage.local.set({ enable_assignment_alerts: enabled });
    
    // Update dashboard visibility
    const urgentSection = document.getElementById('urgentSection');
    const dashboardSection = document.getElementById('assignmentDashboard');
    if (enabled) {
        await loadDashboard();
        urgentSection?.classList.remove('hidden');
        dashboardSection?.classList.remove('hidden');
    } else {
        urgentSection?.classList.add('hidden');
        dashboardSection?.classList.add('hidden');
    }
}

// Check login status
async function checkLoginStatus() {
    try {
        const response = await chrome.runtime.sendMessage({ type: 'CHECK_LOGIN_STATUS' });
        if (response && response.success && response.logged_in) {
            showMainPage();
            await discoverAndLoadCourses(false);
        } else {
            showLoginPage();
        }
    } catch (error) {
        console.error('Error checking login status:', error);
        showLoginPage();
    }
}

// Show login page
function showLoginPage() {
    document.getElementById('loginSection')?.classList.remove('hidden');
    document.getElementById('navBar')?.classList.add('hidden');
    document.getElementById('mainPage')?.classList.add('hidden');
    document.getElementById('videosPage')?.classList.add('hidden');
    document.getElementById('settingsPage')?.classList.add('hidden');
}

// Show main page
function showMainPage() {
    document.getElementById('loginSection')?.classList.add('hidden');
    document.getElementById('navBar')?.classList.remove('hidden');
    document.getElementById('mainPage')?.classList.remove('hidden');
    document.getElementById('videosPage')?.classList.add('hidden');
    document.getElementById('settingsPage')?.classList.add('hidden');
    
    updateNavButton('navMain');
}

// Show videos page
async function showVideosPage() {
    document.getElementById('mainPage')?.classList.add('hidden');
    document.getElementById('videosPage')?.classList.remove('hidden');
    document.getElementById('settingsPage')?.classList.add('hidden');
    
    updateNavButton('navVideos');
    await loadVideos();
}

// Show settings page
async function showSettingsPage() {
    document.getElementById('mainPage')?.classList.add('hidden');
    document.getElementById('videosPage')?.classList.add('hidden');
    document.getElementById('settingsPage')?.classList.remove('hidden');
    
    updateNavButton('navSettings');
    await loadSettings();
}

function updateNavButton(activeId) {
    ['navMain', 'navVideos', 'navSettings'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) {
            btn.classList.toggle('active', id === activeId);
        }
    });
}

// Handle login
async function handleLogin() {
    const username = document.getElementById('username')?.value;
    const password = document.getElementById('password')?.value;
    const messageDiv = document.getElementById('loginMessage');
    
    if (!username || !password) {
        showMessage(messageDiv, 'Enter both username and password', 'error');
        return;
    }
    
    showMessage(messageDiv, 'Logging in...', 'info');
    
    try {
        const response = await chrome.runtime.sendMessage({
            type: 'LOGIN',
            username: username,
            password: password
        });
        
        if (response && response.success) {
            showMessage(messageDiv, 'Login successful!', 'success');
            setTimeout(async () => {
                showMainPage();
                await discoverAndLoadCourses(false);
            }, 1000);
        } else {
            showMessage(messageDiv, response?.message || 'Login failed', 'error');
        }
    } catch (error) {
        console.error('Login error:', error);
        showMessage(messageDiv, 'Login error: ' + error.message, 'error');
    }
}

// Handle logout
async function handleLogout() {
    await chrome.storage.local.remove(['auth_session']);
    showLoginPage();
    document.getElementById('username').value = '';
    document.getElementById('password').value = '';
}

// Discover and load courses
async function discoverAndLoadCourses(forceRefresh = false) {
    const loadingSection = document.getElementById('loadingSection');
    const mainPage = document.getElementById('mainPage');
    
    loadingSection?.classList.remove('hidden');
    mainPage?.classList.add('hidden');
    
    try {
        const response = await chrome.runtime.sendMessage({
            type: 'FETCH_COURSES',
            force_refresh: forceRefresh
        });
        
        if (response && response.success) {
            coursesData = response.courses || [];
            displayCourses(coursesData);
            await loadDashboard(); // Load assignment dashboard if enabled
        } else {
            showMessage(document.getElementById('courseMessage'), response?.message || 'Failed to load courses', 'error');
        }
    } catch (error) {
        console.error('Error loading courses:', error);
        showMessage(document.getElementById('courseMessage'), 'Error: ' + error.message, 'error');
    } finally {
        loadingSection?.classList.add('hidden');
        mainPage?.classList.remove('hidden');
    }
}

// Display courses
function displayCourses(courses) {
    const coursesList = document.getElementById('coursesList');
    if (!coursesList) return;
    
    if (!courses || courses.length === 0) {
        coursesList.innerHTML = '<p>No courses found.</p>';
        return;
    }
    
    coursesList.innerHTML = '';
    
    courses.forEach(course => {
        const courseDiv = document.createElement('div');
        courseDiv.className = 'course-group';
        courseDiv.innerHTML = `
            <h3>${course.course_name || 'Unknown Course'}</h3>
            <p style="color: #666; font-size: 0.9em;">${course.professor || ''} | ${course.year || ''} ${course.semester || ''}</p>
            <div class="lecture-list" id="lectures-${course.course_id}"></div>
        `;
        
        const lectureDiv = courseDiv.querySelector(`#lectures-${course.course_id}`);
        if (course.lectures && course.lectures.length > 0) {
            course.lectures.forEach(lecture => {
                const lectureItem = document.createElement('div');
                lectureItem.className = 'lecture-item';
                const statusClass = lecture.status === 'Done' ? 'status-done' : 'status-new';
                lectureItem.innerHTML = `
                    <input type="checkbox" class="lecture-checkbox" data-lecture-id="${lecture.lecture_id}" data-course-id="${course.course_id}">
                    <div class="lecture-info">
                        <div class="lecture-title">${lecture.title || 'Unknown'}</div>
                        <div class="lecture-meta">${lecture.week || ''} | <span class="status-badge ${statusClass}">${lecture.status || 'New'}</span></div>
                    </div>
                `;
                lectureDiv.appendChild(lectureItem);
            });
        } else {
            lectureDiv.innerHTML = '<p style="color: #999; font-style: italic;">No lectures found.</p>';
        }
        
        coursesList.appendChild(courseDiv);
    });
}

// Select all lectures
function selectAll() {
    document.querySelectorAll('.lecture-checkbox').forEach(cb => cb.checked = true);
}

// Deselect all lectures
function selectNone() {
    document.querySelectorAll('.lecture-checkbox').forEach(cb => cb.checked = false);
}

// Start download
async function startDownload() {
    const checkboxes = document.querySelectorAll('.lecture-checkbox:checked');
    const downloadNewOnly = document.getElementById('downloadNewOnlyCheckbox')?.checked || false;
    
    if (checkboxes.length === 0) {
        showMessage(document.getElementById('courseMessage'), 'Select at least one lecture', 'error');
        return;
    }
    
    const lectureIds = Array.from(checkboxes).map(cb => ({
        lecture_id: cb.dataset.lectureId,
        course_id: cb.dataset.courseId
    }));
    
    try {
        const response = await chrome.runtime.sendMessage({
            type: 'DOWNLOAD_LECTURES',
            lecture_ids: lectureIds,
            download_new_only: downloadNewOnly
        });
        
        if (response && response.success) {
            currentTaskId = response.task_id;
            showProgressSection();
            startProgressPolling(response.task_id);
        } else {
            showMessage(document.getElementById('courseMessage'), response?.message || 'Download failed', 'error');
        }
    } catch (error) {
        console.error('Download error:', error);
        showMessage(document.getElementById('courseMessage'), 'Error: ' + error.message, 'error');
    }
}

// Show progress section
function showProgressSection() {
    document.getElementById('progressSection')?.classList.remove('hidden');
}

// Start polling for progress
function startProgressPolling(taskId) {
    const interval = setInterval(async () => {
        try {
            const response = await chrome.runtime.sendMessage({
                type: 'GET_TASK_STATUS',
                task_id: taskId
            });
            
            if (response && response.success && response.status) {
                updateProgress(response.status);
                
                if (response.status.status === 'completed' || response.status.status === 'error' || response.status.status === 'stopped') {
                    clearInterval(interval);
                    if (response.status.status === 'completed') {
                        setTimeout(() => {
                            document.getElementById('progressSection')?.classList.add('hidden');
                            loadVideos(); // Refresh videos list
                        }, 3000);
                    }
                }
            }
        } catch (error) {
            console.error('Progress polling error:', error);
            clearInterval(interval);
        }
    }, 1000);
}

// Update progress display
function updateProgress(status) {
    const progressFill = document.getElementById('progressFill');
    const taskLog = document.getElementById('taskLog');
    const pauseBtn = document.getElementById('pauseBtn');
    const resumeBtn = document.getElementById('resumeBtn');
    const stopBtn = document.getElementById('stopBtn');
    
    if (progressFill) {
        const percent = status.progress || 0;
        progressFill.style.width = `${percent}%`;
        progressFill.textContent = `${percent}%`;
    }
    
    if (taskLog && status.logs) {
        taskLog.innerHTML = status.logs.map(log => `<div>${log}</div>`).join('');
        taskLog.scrollTop = taskLog.scrollHeight;
    }
    
    // Update control buttons
    if (pauseBtn) pauseBtn.style.display = status.status === 'running' ? 'inline-block' : 'none';
    if (resumeBtn) resumeBtn.style.display = status.status === 'paused' ? 'inline-block' : 'none';
    if (stopBtn) stopBtn.style.display = (status.status === 'running' || status.status === 'paused') ? 'inline-block' : 'none';
}

// Pause task
async function pauseTask() {
    if (!currentTaskId) return;
    await chrome.runtime.sendMessage({ type: 'PAUSE_TASK', task_id: currentTaskId });
}

// Resume task
async function resumeTask() {
    if (!currentTaskId) return;
    await chrome.runtime.sendMessage({ type: 'RESUME_TASK', task_id: currentTaskId });
}

// Stop task
async function stopTask() {
    if (!currentTaskId) return;
    await chrome.runtime.sendMessage({ type: 'STOP_TASK', task_id: currentTaskId });
}

// Load videos
async function loadVideos() {
    const videosMessage = document.getElementById('videosMessage');
    const videosContainer = document.getElementById('videosContainer');
    
    showMessage(videosMessage, 'Loading videos...', 'info');
    
    try {
        const response = await chrome.runtime.sendMessage({ type: 'LIST_VIDEOS' });
        
        if (response && response.success) {
            const videos = response.videos || [];
            
            if (videos.length === 0) {
                videosContainer.innerHTML = '<p>No downloaded videos found.</p>';
            } else {
                videosContainer.innerHTML = videos.map(video => `
                    <div class="lecture-item">
                        <div class="lecture-info">
                            <div class="lecture-title">${video.title || 'Unknown'}</div>
                            <div class="lecture-meta">${video.path || ''}</div>
                        </div>
                    </div>
                `).join('');
            }
            
            showMessage(videosMessage, `Found ${videos.length} video(s)`, 'success');
        } else {
            showMessage(videosMessage, response?.message || 'Failed to load videos', 'error');
        }
    } catch (error) {
        console.error('Error loading videos:', error);
        showMessage(videosMessage, 'Error: ' + error.message, 'error');
    }
}

// Semester navigation
function toggleSemesterNav() {
    const menu = document.getElementById('semesterNavMenu');
    const icon = document.getElementById('navToggleIcon');
    if (menu && icon) {
        menu.classList.toggle('hidden');
        icon.textContent = menu.classList.contains('hidden') ? '▶' : '▼';
    }
}

async function loadCustomSemester() {
    const year = document.getElementById('customYear')?.value;
    const semester = document.getElementById('customSemester')?.value;
    
    if (!year || !semester) {
        showMessage(document.getElementById('courseMessage'), 'Select both year and semester', 'error');
        return;
    }
    
    currentYear = year;
    currentSemester = semester;
    
    await discoverAndLoadCourses(true);
}

// Load settings
async function loadSettings() {
    const stored = await chrome.storage.local.get([
        'download_dir',
        'llm_provider',
        'openai_api_key',
        'google_api_key',
        'ollama_url'
    ]);
    
    if (stored.download_dir) {
        document.getElementById('downloadDir').value = stored.download_dir;
    }
    
    if (stored.llm_provider) {
        document.getElementById('llmProvider').value = stored.llm_provider;
    }
    
    if (stored.openai_api_key) {
        document.getElementById('openaiApiKey').value = stored.openai_api_key;
    }
    
    if (stored.google_api_key) {
        document.getElementById('googleApiKey').value = stored.google_api_key;
    }
    
    if (stored.ollama_url) {
        document.getElementById('ollamaUrl').value = stored.ollama_url;
    }
}

// Save download settings
async function saveDownloadSettings() {
    const downloadDir = document.getElementById('downloadDir')?.value.trim();
    const messageDiv = document.getElementById('featureSettingsMessage');
    
    await chrome.storage.local.set({
        download_dir: downloadDir || ''
    });
    
    showMessage(messageDiv, 'Download settings saved!', 'success');
}

// Save LLM settings
async function saveLLMSettings() {
    const provider = document.getElementById('llmProvider')?.value;
    const openaiKey = document.getElementById('openaiApiKey')?.value;
    const googleKey = document.getElementById('googleApiKey')?.value;
    const ollamaUrl = document.getElementById('ollamaUrl')?.value;
    const messageDiv = document.getElementById('llmSettingsMessage');
    
    await chrome.storage.local.set({
        llm_provider: provider,
        openai_api_key: openaiKey || '',
        google_api_key: googleKey || '',
        ollama_url: ollamaUrl || ''
    });
    
    showMessage(messageDiv, 'LLM settings saved!', 'success');
}

// Load assignment dashboard
async function loadDashboard() {
    const stored = await chrome.storage.local.get(['assignment_data']);
    const assignmentData = stored.assignment_data || [];
    
    if (assignmentData.length === 0) {
        document.getElementById('urgentList').innerHTML = '<p style="color: #666;">과제 정보가 없습니다.</p>';
        document.getElementById('assignmentList').innerHTML = '<p style="color: #666;">과제 정보가 없습니다.</p>';
        return;
    }
    
    // Filter assignments
    const hideCompleted = document.getElementById('hideCompleted')?.checked;
    const hideIgnored = document.getElementById('hideIgnored')?.checked;
    
    let filtered = assignmentData;
    if (hideCompleted) {
        filtered = filtered.filter(a => !a.completed);
    }
    if (hideIgnored) {
        filtered = filtered.filter(a => !a.ignored);
    }
    
    // Render urgent assignments (due within 3 days)
    const now = new Date();
    const urgent = filtered.filter(a => {
        if (!a.due_date) return false;
        const due = new Date(a.due_date);
        const diffDays = (due - now) / (1000 * 60 * 60 * 24);
        return diffDays >= 0 && diffDays <= 3 && !a.completed;
    });
    
    renderUrgentList(urgent);
    renderAssignmentList(filtered);
}

function renderUrgentList(assignments) {
    const urgentList = document.getElementById('urgentList');
    if (!urgentList) return;
    
    if (assignments.length === 0) {
        urgentList.innerHTML = '<p style="color: #666;">임박한 과제가 없습니다.</p>';
        return;
    }
    
    urgentList.innerHTML = assignments.map(a => `
        <div style="padding: 10px; margin: 5px 0; background: white; border-radius: 4px; border-left: 4px solid #dc3545;">
            <strong>${a.title || 'Unknown'}</strong><br>
            <small>${a.course || ''} | 마감: ${a.due_date || 'N/A'}</small>
        </div>
    `).join('');
}

function renderAssignmentList(assignments) {
    const assignmentList = document.getElementById('assignmentList');
    if (!assignmentList) return;
    
    if (assignments.length === 0) {
        assignmentList.innerHTML = '<p style="color: #666;">과제가 없습니다.</p>';
        return;
    }
    
    assignmentList.innerHTML = assignments.map(a => `
        <div style="padding: 10px; margin: 5px 0; background: white; border-radius: 4px; border: 1px solid #ddd;">
            <strong>${a.title || 'Unknown'}</strong>
            ${a.completed ? '<span class="status-badge status-done">완료</span>' : ''}
            ${a.ignored ? '<span class="status-badge" style="background: #6c757d; color: white;">숨김</span>' : ''}
            <br>
            <small>${a.course || ''} | 마감: ${a.due_date || 'N/A'}</small>
        </div>
    `).join('');
}

// Helper: Show message
function showMessage(element, text, type) {
    if (!element) return;
    element.textContent = text;
    element.className = `message message-${type}`;
    setTimeout(() => {
        element.textContent = '';
        element.className = '';
    }, 5000);
}

