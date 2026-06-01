// DOM Elements
const systemClock = document.getElementById('system-clock');
const volumeSlider = document.getElementById('volume-slider');
const volumeVal = document.getElementById('volume-val');
const speedSlider = document.getElementById('speed-slider');
const speedVal = document.getElementById('speed-val');
const textInput = document.getElementById('announcement-text');
const engineSelect = document.getElementById('engine-selector');
const btnBroadcast = document.getElementById('btn-broadcast');
const btnClearLogs = document.getElementById('btn-clear-logs');
const fallbackToggle = document.getElementById('setting-fallback');

// Telemetry Elements
const statUptime = document.getElementById('stat-uptime');
const statQueue = document.getElementById('stat-queue');
const statCpu = document.getElementById('stat-cpu');
const statMem = document.getElementById('stat-mem');
const cpuBar = document.getElementById('cpu-bar');
const memBar = document.getElementById('mem-bar');
const vttDot = document.getElementById('vtt-dot');
const logsFeed = document.getElementById('logs-feed');
const logsCount = document.getElementById('logs-count');
const emptyStateLogs = document.getElementById('empty-logs-state');

// State Cache
let previousLogsHash = '';

// Clock tick
function updateClock() {
    const now = new Date();
    systemClock.textContent = now.toLocaleTimeString();
}
setInterval(updateClock, 1000);
updateClock();

// Sliders event listeners
volumeSlider.addEventListener('input', (e) => {
    volumeVal.textContent = Math.round(e.target.value * 100) + '%';
});

speedSlider.addEventListener('input', (e) => {
    speedVal.textContent = e.target.value;
});

// Broadcast Announcement handler
btnBroadcast.addEventListener('click', async () => {
    const text = textInput.value.trim();
    if (!text) {
        alert('Please enter announcement text before broadcasting!');
        return;
    }

    btnBroadcast.disabled = true;
    btnBroadcast.querySelector('.btn-text').textContent = 'Queueing Announcement...';

    try {
        const response = await fetch('/api/announce', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text: text,
                source: 'Web Console',
                engine: engineSelect.value,
                volume: parseFloat(volumeSlider.value),
                speed: parseInt(speedSlider.value)
            })
        });

        const data = await response.json();
        
        if (response.ok) {
            textInput.value = '';
            // Instantly refresh logs
            fetchLogs();
            fetchStatus();
        } else {
            alert('Failed to broadcast announcement: ' + (data.message || 'Unknown Error'));
        }
    } catch (e) {
        console.error('Error broadcasting:', e);
        alert('Failed to communicate with the TTS server.');
    } finally {
        btnBroadcast.disabled = false;
        btnBroadcast.querySelector('.btn-text').textContent = 'Broadcast Announcement';
    }
});

// Clear Logs handler
btnClearLogs.addEventListener('click', async () => {
    if (!confirm('Are you sure you want to clear the logs history?')) {
        return;
    }
    
    try {
        const response = await fetch('/api/clear_logs', { method: 'POST' });
        if (response.ok) {
            fetchLogs();
        }
    } catch (e) {
        console.error('Error clearing logs:', e);
    }
});

// Update Settings handler
fallbackToggle.addEventListener('change', async () => {
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                offline_fallback: fallbackToggle.checked
            })
        });
    } catch (e) {
        console.error('Error updating settings:', e);
    }
});

// Format Uptime helper
function formatUptime(seconds) {
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    if (minutes < 60) return `${minutes}m ${secs}s`;
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return `${hours}h ${mins}m`;
}

// Fetch Status & Diagnostics
async function fetchStatus() {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) return;
        const status = await response.json();

        // Update basic stats
        statUptime.textContent = formatUptime(status.uptime);
        statQueue.textContent = status.queue_length;
        
        // Update bars
        const cpu = status.tts_telemetry.cpu || 0;
        const mem = status.tts_telemetry.memory || 0;
        statCpu.textContent = Math.round(cpu) + '%';
        statMem.textContent = Math.round(mem) + '%';
        cpuBar.style.width = cpu + '%';
        memBar.style.width = mem + '%';

        // Check if VTT Client is online
        let vttOnline = false;
        if (status.clients && status.clients.raspi_vtt) {
            vttOnline = status.clients.raspi_vtt.status === 'Online';
        }

        if (vttOnline) {
            vttDot.className = 'indicator-dot online';
        } else {
            vttDot.className = 'indicator-dot offline';
        }

    } catch (e) {
        console.error('Error fetching system status:', e);
    }
}

// Fetch and Render Announcement logs
async function fetchLogs() {
    try {
        const response = await fetch('/api/logs');
        if (!response.ok) return;
        const logs = await response.json();

        // Check for content differences to avoid rebuilding DOM needlessly
        const logsHash = JSON.stringify(logs);
        if (logsHash === previousLogsHash) return;
        previousLogsHash = logsHash;

        // Render count
        logsCount.textContent = `${logs.length} Announcement${logs.length !== 1 ? 's' : ''}`;

        // Clear existing, keep empty state if none
        if (logs.length === 0) {
            logsFeed.innerHTML = '';
            logsFeed.appendChild(emptyStateLogs);
            emptyStateLogs.style.display = 'flex';
            return;
        }

        emptyStateLogs.style.display = 'none';
        
        // Rebuild list
        const fragment = document.createDocumentFragment();
        
        logs.forEach(log => {
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            
            const statusClass = log.status.toLowerCase().replace('...', '');
            
            entry.innerHTML = `
                <div class="log-header-info">
                    <div class="log-meta">
                        <span class="log-source">${log.source}</span>
                        <span class="log-time">${log.timestamp}</span>
                        <span class="log-engine-badge">${log.engine}</span>
                    </div>
                    <span class="log-status-badge ${statusClass}">${log.status}</span>
                </div>
                <div class="log-text">${log.text}</div>
            `;
            fragment.appendChild(entry);
        });

        logsFeed.innerHTML = '';
        logsFeed.appendChild(fragment);

    } catch (e) {
        console.error('Error fetching logs:', e);
    }
}

// Initial pull and periodic loop
fetchStatus();
fetchLogs();

setInterval(fetchStatus, 2000);
setInterval(fetchLogs, 1000);
