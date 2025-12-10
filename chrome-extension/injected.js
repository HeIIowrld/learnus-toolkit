// Injected script - adds download buttons and AI assistant to LearnUs pages
// Consolidated with content.js functionality

(function() {
    'use strict';
    
    let isInitialized = false;
    
    // Make functions available globally for background script (from content.js)
    window.parseCourses = parseCourses;
    window.parseLectures = parseLectures;
    window.extractVideoUrl = extractVideoUrl;
    
    // Listen for messages from background (from content.js)
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        if (request.type === 'PARSE_COURSES') {
            const courses = parseCourses();
            sendResponse({ success: true, courses });
        } else if (request.type === 'PARSE_LECTURES') {
            const lectures = parseLectures();
            sendResponse({ success: true, lectures });
        } else if (request.type === 'EXTRACT_VIDEO_URL') {
            const videoUrl = extractVideoUrl();
            sendResponse({ success: true, videoUrl });
        }
        
        return true;
    });
    
    // Initialize on page load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    
    // Re-run when page content changes (SPA navigation)
    const observer = new MutationObserver(() => {
        if (!isInitialized) {
            setTimeout(init, 500);
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    
    async function init() {
        if (document.querySelector('.learnus-extension-enabled')) {
            isInitialized = true;
            return;
        }
        
        // Check opt-in settings
        const stored = await chrome.storage.local.get(['enable_inline_buttons', 'enable_assignment_alerts']);
        const enableInlineButtons = stored.enable_inline_buttons !== false; // Default: enabled
        const enableAssignmentAlerts = stored.enable_assignment_alerts !== false; // Default: enabled
        
        document.body.classList.add('learnus-extension-enabled');
        isInitialized = true;
        
        if (enableInlineButtons) {
            injectVideoButtons();
            injectSectionDownloadButtons();
            injectCourseDownloadButton();
        }
        
        if (enableAssignmentAlerts) {
            parseAssignmentsForDashboard();
        }
    }
    
    // Add download buttons next to video/assignment/file links
    function injectVideoButtons() {
        const activityInstances = document.querySelectorAll('div.activityinstance');
        
        activityInstances.forEach(activity => {
            // Skip if already has button
            if (activity.querySelector('.learnus-inline-btn')) return;
            
            const link = activity.querySelector('a');
            if (!link) return;
            
            const href = link.href || '';
            const onclick = link.getAttribute('onclick') || '';
            
            // Check for VOD (video)
            const isVod = href.includes('mod/vod') || onclick.includes('mod/vod');
            // Check for file/resource
            const isFile = href.includes('mod/resource') || href.includes('mod/folder');
            // Check for assignment
            const isAssignment = href.includes('mod/assign') || onclick.includes('mod/assign');
            // Check for quiz
            const isQuiz = href.includes('mod/quiz') || onclick.includes('mod/quiz');
            
            if (isVod || isFile || isAssignment || isQuiz) {
                const btn = createInlineDownloadButton(activity, link, {
                    type: isVod ? 'video' : (isAssignment ? 'assignment' : (isQuiz ? 'quiz' : 'file')),
                    href: href,
                    onclick: onclick
                });
                
                // Insert button next to the link
                const instancename = activity.querySelector('span.instancename');
                if (instancename) {
                    instancename.style.display = 'inline-flex';
                    instancename.style.alignItems = 'center';
                    instancename.style.gap = '8px';
                    instancename.appendChild(btn);
                } else {
                    // Fallback: insert after link
                    link.parentNode.insertBefore(btn, link.nextSibling);
                }
            }
        });
    }
    
    // Create inline download button
    function createInlineDownloadButton(activity, link, info) {
        const btn = document.createElement('button');
        btn.className = 'learnus-inline-btn';
        btn.innerHTML = '⬇️';
        btn.title = info.type === 'video' ? '동영상 다운로드' : 
                   (info.type === 'assignment' ? '과제 다운로드' : 
                   (info.type === 'quiz' ? '퀴즈 다운로드' : '파일 다운로드'));
        
        // Check if restricted (not yet accessible)
        const isRestricted = activity.querySelector('img[alt*="제한"], img[alt*="Restricted"]') ||
                            activity.textContent.includes('제한') ||
                            activity.textContent.includes('Restricted');
        
        if (isRestricted) {
            btn.classList.add('restricted');
            btn.title += ' (제한됨 - 접근 불가)';
            btn.disabled = true;
            btn.style.opacity = '0.5';
        }
        
        btn.onclick = async (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            if (isRestricted) {
                alert('이 항목은 아직 접근할 수 없습니다. (제한됨)');
                return;
            }
            
            btn.innerHTML = '⏳';
            btn.disabled = true;
            
            const lectureId = getLectureIdFromLink(link, info);
            const itemData = {
                type: info.type,
                url: info.href || window.location.href,
                lecture_id: lectureId,
                title: activity.querySelector('span.instancename')?.textContent.trim() || 'Unknown',
                course_url: window.location.href
            };
            
            try {
                const response = await chrome.runtime.sendMessage({
                    type: 'DOWNLOAD_ITEM_INLINE',
                    ...itemData
                });
                
                if (response && response.success) {
                    btn.innerHTML = '✅';
                    setTimeout(() => {
                        btn.innerHTML = '⬇️';
                        btn.disabled = false;
                    }, 2000);
                    
                    // If assignment, open AI panel
                    if (info.type === 'assignment') {
                        setTimeout(() => openAIPanel(itemData.url, itemData.title), 1000);
                    }
                } else {
                    btn.innerHTML = '❌';
                    setTimeout(() => {
                        btn.innerHTML = '⬇️';
                        btn.disabled = false;
                    }, 2000);
                    alert(response?.message || '다운로드 실패');
                }
            } catch (error) {
                console.error('Download error:', error);
                btn.innerHTML = '⬇️';
                btn.disabled = false;
                alert('다운로드 중 오류가 발생했습니다.');
            }
        };
        
        return btn;
    }
    
    // Extract lecture ID from link
    function getLectureIdFromLink(link, info) {
        // Try href first
        const hrefMatch = (info.href || link.href || '').match(/[?&]id=(\d+)/);
        if (hrefMatch) return hrefMatch[1];
        
        // Try onclick
        const onclickMatch = (info.onclick || link.getAttribute('onclick') || '').match(/id[=:](\d+)/);
        if (onclickMatch) return onclickMatch[1];
        
        // Try module ID from activity
        const activity = link.closest('div.activityinstance, div.activity');
        if (activity) {
            const moduleMatch = activity.id?.match(/module-(\d+)/);
            if (moduleMatch) return moduleMatch[1];
        }
        
        return null;
    }
    
    // Add section (week) download buttons
    function injectSectionDownloadButtons() {
        const sections = document.querySelectorAll('li.section.main, li[id^="section-"]');
        
        sections.forEach(section => {
            // Skip if already has button
            if (section.querySelector('.learnus-batch-btn')) return;
            
            const titleArea = section.querySelector('.content, .sectionname');
            if (!titleArea) return;
            
            const batchBtn = document.createElement('button');
            batchBtn.className = 'learnus-batch-btn';
            batchBtn.innerHTML = '📂 이 주차 모두 다운로드';
            batchBtn.title = '이 주차의 모든 동영상 다운로드';
            
            batchBtn.onclick = async (e) => {
                e.preventDefault();
                e.stopPropagation();
                
                // Collect all video links in this section
                const vodLinks = section.querySelectorAll('a[href*="mod/vod"], a[onclick*="mod/vod"]');
                const lectureIds = [];
                
                vodLinks.forEach(link => {
                    const id = getLectureIdFromLink(link, { href: link.href, onclick: link.getAttribute('onclick') });
                    if (id) lectureIds.push(id);
                });
                
                if (lectureIds.length === 0) {
                    alert('이 주차에는 다운로드할 동영상이 없습니다.');
                    return;
                }
                
                batchBtn.innerHTML = `⏳ ${lectureIds.length}개 다운로드 중...`;
                batchBtn.disabled = true;
                
                try {
                    const response = await chrome.runtime.sendMessage({
                        type: 'DOWNLOAD_SECTION',
                        lecture_ids: lectureIds,
                        course_url: window.location.href,
                        section_title: section.querySelector('.sectionname')?.textContent.trim() || 'Unknown Week'
                    });
                    
                    if (response && response.success) {
                        batchBtn.innerHTML = '✅ 다운로드 시작됨';
                        setTimeout(() => {
                            batchBtn.innerHTML = '📂 이 주차 모두 다운로드';
                            batchBtn.disabled = false;
                        }, 3000);
                    } else {
                        batchBtn.innerHTML = '📂 이 주차 모두 다운로드';
                        batchBtn.disabled = false;
                        alert(response?.message || '다운로드 실패');
                    }
                } catch (error) {
                    console.error('Section download error:', error);
                    batchBtn.innerHTML = '📂 이 주차 모두 다운로드';
                    batchBtn.disabled = false;
                }
            };
            
            // Insert at the beginning of section
            if (titleArea) {
                titleArea.insertBefore(batchBtn, titleArea.firstChild);
            } else {
                section.insertBefore(batchBtn, section.firstChild);
            }
        });
    }
    
    // Add course-wide download button
    function injectCourseDownloadButton() {
        // Only on course main page
        if (!window.location.href.includes('course/view.php')) return;
        
        // Check if button already exists
        if (document.querySelector('.learnus-course-download-btn')) return;
        
        const courseIdMatch = window.location.href.match(/[?&]id=(\d+)/);
        if (!courseIdMatch) return;
        
        const btn = document.createElement('button');
        btn.className = 'learnus-course-download-btn';
        btn.innerHTML = '📥 강좌 전체 다운로드';
        btn.title = '이 강좌의 모든 동영상 다운로드';
        
        btn.onclick = async () => {
            btn.innerHTML = '⏳ 준비 중...';
            btn.disabled = true;
            
            try {
                const response = await chrome.runtime.sendMessage({
                    type: 'DOWNLOAD_COURSE',
                    course_id: courseIdMatch[1],
                    course_url: window.location.href
                });
                
                if (response && response.success) {
                    btn.innerHTML = '✅ 다운로드 시작됨';
                    setTimeout(() => {
                        btn.innerHTML = '📥 강좌 전체 다운로드';
                        btn.disabled = false;
                    }, 3000);
                } else {
                    btn.innerHTML = '📥 강좌 전체 다운로드';
                    btn.disabled = false;
                    alert(response?.message || '다운로드 실패');
                }
            } catch (error) {
                console.error('Course download error:', error);
                btn.innerHTML = '📥 강좌 전체 다운로드';
                btn.disabled = false;
            }
        };
        
        document.body.appendChild(btn);
    }
    
    // Parse assignments for dashboard
    function parseAssignmentsForDashboard() {
        const assignments = [];
        const activities = document.querySelectorAll('div.activity.assign, div.activity.quiz, div.activityinstance');
        
        activities.forEach(act => {
            const link = act.querySelector('a[href*="mod/assign"], a[href*="mod/quiz"]');
            if (!link) return;
            
            const idMatch = act.id?.match(/module-(\d+)/) || link.href.match(/[?&]id=(\d+)/);
            const titleElem = act.querySelector('.instancename, .activityinstance a');
            
            if (!titleElem) return;
            
            // Check completion status
            let status = '미제출';
            const completionCheck = act.querySelector('img[src*="completion-auto-y"], img[alt*="완료"]');
            if (completionCheck) {
                status = '제출완료';
            }
            
            // Check if restricted
            const isRestricted = act.querySelector('img[alt*="제한"], img[alt*="Restricted"]') ||
                                act.textContent.includes('제한') ||
                                act.textContent.includes('Restricted');
            
            assignments.push({
                id: idMatch ? idMatch[1] : null,
                title: titleElem.textContent.trim(),
                type: link.href.includes('quiz') ? 'Quiz' : 'Assignment',
                status: status,
                restricted: isRestricted,
                course_id: window.location.href.match(/[?&]id=(\d+)/)?.[1] || '',
                url: link.href,
                course_url: window.location.href
            });
        });
        
        // Send to background for storage
        if (assignments.length > 0) {
            chrome.runtime.sendMessage({
                type: 'UPDATE_ASSIGNMENT_DATA',
                data: assignments
            });
        }
    }
    
    // AI Panel (for assignment help)
    let aiPanel = null;
    
    function openAIPanel(assignmentUrl, assignmentTitle) {
        if (!aiPanel) {
            createAIPanel();
        }
        aiPanel.classList.add('open');
        
        loadAssignmentContext(assignmentUrl, assignmentTitle);
    }
    
    function createAIPanel() {
        aiPanel = document.createElement('div');
        aiPanel.className = 'learnus-ai-panel';
        aiPanel.innerHTML = `
            <div class="learnus-ai-header">
                <h3>🤖 AI 과제 도우미</h3>
                <button class="learnus-ai-close" onclick="this.closest('.learnus-ai-panel').classList.remove('open')">×</button>
            </div>
            <div class="learnus-ai-content" id="aiContent">
                <div class="learnus-ai-message assistant">
                    안녕하세요! 과제를 도와드리겠습니다. 강의 자료를 기반으로 답변해드립니다.
                </div>
            </div>
            <div class="learnus-ai-input-area">
                <textarea class="learnus-ai-input" id="aiInput" placeholder="과제에 대해 질문하세요... (Ctrl+Enter로 전송)"></textarea>
                <button class="learnus-ai-send-btn" id="aiSendBtn">전송</button>
            </div>
        `;
        document.body.appendChild(aiPanel);
        
        const sendBtn = aiPanel.querySelector('#aiSendBtn');
        const input = aiPanel.querySelector('#aiInput');
        
        sendBtn.onclick = () => sendAIMessage(input.value);
        input.onkeydown = (e) => {
            if (e.key === 'Enter' && e.ctrlKey) {
                sendAIMessage(input.value);
            }
        };
    }
    
    function loadAssignmentContext(assignmentUrl, assignmentTitle) {
        const content = document.getElementById('aiContent');
        content.innerHTML = `
            <div class="learnus-ai-message assistant">
                <strong>과제:</strong> ${assignmentTitle}<br>
                <small>강의 자료를 불러오는 중...</small>
            </div>
        `;
        
        chrome.runtime.sendMessage({
            type: 'GET_ASSIGNMENT_CONTEXT',
            assignment_url: assignmentUrl,
            course_url: window.location.href
        }, (response) => {
            if (response && response.success) {
                addAIMessage('assistant', '강의 자료를 불러왔습니다. 과제에 대해 질문해주세요!');
            }
        });
    }
    
    function sendAIMessage(message) {
        if (!message.trim()) return;
        
        const input = document.getElementById('aiInput');
        input.value = '';
        
        addAIMessage('user', message);
        
        const content = document.getElementById('aiContent');
        const loading = document.createElement('div');
        loading.className = 'learnus-ai-loading';
        loading.textContent = '답변 생성 중...';
        content.appendChild(loading);
        
        chrome.runtime.sendMessage({
            type: 'ASK_AI',
            question: message,
            context: {
                course_url: window.location.href,
                assignment_url: window.location.href
            }
        }, (response) => {
            loading.remove();
            if (response && response.success) {
                addAIMessage('assistant', response.answer);
            } else {
                addAIMessage('assistant', '죄송합니다. 답변을 생성하는 중 오류가 발생했습니다.');
            }
        });
    }
    
    function addAIMessage(role, text) {
        const content = document.getElementById('aiContent');
        const message = document.createElement('div');
        message.className = `learnus-ai-message ${role}`;
        message.textContent = text;
        content.appendChild(message);
        content.scrollTop = content.scrollHeight;
    }
    
    // Functions from content.js - for background script access
    function parseCourses() {
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
                        }
                        courseName = h3.textContent.trim();
                        courseName = courseName.replace(/\s*\([^)]*\)\s*$/, '').trim();
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
    
    function parseLectures() {
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
            
            lectures.push({
                lecture_id: `${courseId}_${lectureCounter}`,
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
    
    function extractVideoUrl() {
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
        
        const videos = document.querySelectorAll('video');
        for (const video of videos) {
            const src = video.getAttribute('src');
            if (src && (src.endsWith('.mp4') || src.endsWith('.m3u8'))) {
                return src.startsWith('http') ? src : `https:${src}`;
            }
        }
        
        const html = document.documentElement.outerHTML;
        const m3u8Match = html.match(/(https?:\/\/[^\s"'<>]+\.m3u8[^\s"'<>]*)/i);
        if (m3u8Match) return m3u8Match[1];
        
        const mp4Match = html.match(/(https?:\/\/[^\s"'<>]+\.mp4[^\s"'<>]*)/i);
        if (mp4Match) return mp4Match[1];
        
        return null;
    }
})();
