// Options page script

document.addEventListener('DOMContentLoaded', async () => {
    // Load saved settings
    const stored = await chrome.storage.local.get([
        'learnus_username', 
        'download_dir',
        'enable_inline_buttons',
        'enable_assignment_alerts'
    ]);
    
    if (stored.learnus_username) {
        document.getElementById('username').value = stored.learnus_username;
    }
    // Password field is always empty - passwords are never stored
    if (stored.download_dir) {
        document.getElementById('downloadDir').value = stored.download_dir;
    }
    
    // Load feature settings (default to true if not set)
    document.getElementById('enableInlineButtons').checked = stored.enable_inline_buttons !== false;
    document.getElementById('enableAssignmentAlerts').checked = stored.enable_assignment_alerts !== false;
    
    // Event listeners
    document.getElementById('saveBtn').addEventListener('click', saveSettings);
    document.getElementById('clearBtn').addEventListener('click', clearSettings);
    document.getElementById('saveDownloadBtn').addEventListener('click', saveDownloadSettings);
    document.getElementById('saveFeatureBtn').addEventListener('click', saveFeatureSettings);
});

async function saveSettings() {
    const username = document.getElementById('username').value;
    
    if (!username) {
        showMessage('Enter username', 'error');
        return;
    }
    
    await chrome.storage.local.set({
        learnus_username: username
    });
    
    showMessage('Username saved successfully!', 'success');
}

async function saveDownloadSettings() {
    const downloadDir = document.getElementById('downloadDir').value.trim();
    
    // Sanitize directory name (remove invalid characters)
    const sanitizedDir = downloadDir.replace(/[<>:"/\\|?*]/g, '_').trim();
    
    await chrome.storage.local.set({
        download_dir: sanitizedDir || ''
    });
    
    const message = sanitizedDir 
        ? `Download directory saved: "${sanitizedDir}"` 
        : 'Download directory set to default (browser download folder)';
    showMessage(message, 'success');
}

async function clearSettings() {
    await chrome.storage.local.remove(['learnus_username']);
    document.getElementById('username').value = '';
    document.getElementById('password').value = '';
    showMessage('Username cleared', 'success');
}

async function saveFeatureSettings() {
    const enableInlineButtons = document.getElementById('enableInlineButtons').checked;
    const enableAssignmentAlerts = document.getElementById('enableAssignmentAlerts').checked;
    
    await chrome.storage.local.set({
        enable_inline_buttons: enableInlineButtons,
        enable_assignment_alerts: enableAssignmentAlerts
    });
    
    showMessage('Feature settings saved successfully!', 'success');
    
    // Reload all LearnUs tabs to apply changes
    chrome.tabs.query({ url: 'https://ys.learnus.org/*' }, (tabs) => {
        tabs.forEach(tab => {
            chrome.tabs.reload(tab.id);
        });
    });
}

function showMessage(text, type) {
    const messageDiv = document.getElementById('message');
    messageDiv.textContent = text;
    messageDiv.className = `message message-${type}`;
    setTimeout(() => {
        messageDiv.textContent = '';
        messageDiv.className = '';
    }, 5000);
}

