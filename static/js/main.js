// SmartAttend Core Application Logic
// Antigravity Relational Update

let currentUser = JSON.parse(localStorage.getItem('currentUser')) || { role: 'guest', username: 'Syncing...', name: 'Syncing...' };
let currentView = null;
let viewHistory = [];
let isBackNavigation = false;
let isStreaming = false;
let sessionClassId = null;
let sessionSubjectId = null;
let sessionSection = null;
let sessionDate = null;
let expectedStudents = []; // Cache for the current filter group
let facultyAssignments = []; // Cache for teacher-subject mappings
let runtimeConfig = { camera_mode: 'server', server_streaming_supported: true };
let browserCameraVideo = null;
let browserCameraStream = null;
let browserRecognitionInterval = null;
let browserRegVideo = null;
let browserRegStream = null;
let browserRegInterval = null;

async function loadRuntimeConfig() {
    try {
        const res = await fetch('/api/runtime_config');
        if (res.ok) {
            runtimeConfig = await res.json();
        }
    } catch (e) {
        runtimeConfig = { camera_mode: 'server', server_streaming_supported: true };
    }
}

function getCameraErrorMessage(err) {
    const name = err && err.name ? err.name : '';
    const code = err && err.message ? err.message : '';

    if (code === 'INSECURE_CONTEXT') {
        return 'Camera needs HTTPS secure context. Open the Render URL directly in browser.';
    }
    if (code === 'NOT_SUPPORTED') {
        return 'Browser camera API not supported on this device/browser.';
    }
    if (name === 'NotAllowedError' || name === 'SecurityError') {
        return 'Camera blocked. Allow camera in browser site settings and reload once.';
    }
    if (name === 'NotReadableError' || name === 'TrackStartError') {
        return 'Camera is busy in another app/tab. Close other camera apps and retry.';
    }
    if (name === 'OverconstrainedError' || name === 'ConstraintNotSatisfiedError') {
        return 'Requested camera mode not available. Retrying with default camera may help.';
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        return 'No camera detected on this device.';
    }
    return 'Unable to start camera. Please retry or reopen the page.';
}

async function getUserCameraStream() {
    if (!window.isSecureContext) {
        throw new Error('INSECURE_CONTEXT');
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('NOT_SUPPORTED');
    }

    const attempts = [
        { video: { facingMode: { ideal: 'user' }, width: { ideal: 640 }, height: { ideal: 480 } }, audio: false },
        { video: true, audio: false }
    ];

    let lastError = null;
    for (const constraints of attempts) {
        try {
            return await navigator.mediaDevices.getUserMedia(constraints);
        } catch (e) {
            lastError = e;
        }
    }
    throw lastError || new Error('CAMERA_INIT_FAILED');
}

// Initialize Dashboard
document.addEventListener('DOMContentLoaded', async () => {
    await loadRuntimeConfig();

    // 1. Check server session sync
    try {
        const res = await fetch('/api/whoami');
        if(res.ok) {
            const serverUser = await res.json();
            if(JSON.stringify(serverUser) !== JSON.stringify(currentUser)) {
                currentUser = serverUser;
                localStorage.setItem('currentUser', JSON.stringify(currentUser));
            }
        }
    } catch(e) { console.warn('Session sync failed'); }

    // 2. Refresh UI
    const urlParams = new URLSearchParams(window.location.search);
    const startView = urlParams.get('view');

    updateUserUI();
    if(currentUser.role === 'student') {
        showView('student-dashboard');
        loadStudentData();
    } else if (currentUser.role === 'faculty') {
        showView(startView || 'class-selection');
    } else if (startView) {
        showView(startView);
        if(startView === 'dashboard') loadInitialData();
    } else {
        showView('dashboard');
        loadInitialData();
    }

    // 3. Initialize Date Picker for Faculty
    const dateEl = document.getElementById('faculty-date-select');
    if(dateEl) {
        dateEl.value = new Date().toISOString().split('T')[0];
    }

    // 4. Sync Dynamic Sidebar & Stats
    updateSidebarDepartments();
    // Camera health: poll every 30s (was 3s) — each call hits Flask which is free,
    // but reduces server load and avoids any cascading Firestore touches.
    setInterval(updateCameraHealth, 30000);
    updateCameraHealth(); // Run once immediately
});

async function updateCameraHealth() {
    if(runtimeConfig.camera_mode === 'browser') {
        const dot = document.getElementById('camera-status-dot');
        if(dot) {
            dot.style.background = '#22c55e';
            dot.title = 'Browser Camera Mode';
        }
        return;
    }

    try {
        const res = await fetch('/api/camera_health');
        const data = await res.json();
        const dot = document.getElementById('camera-status-dot');
        if(dot) {
            dot.style.background = data.status === 'healthy' ? '#22c55e' : '#ef4444';
            dot.title = data.status === 'healthy' ? 'Hardware Online' : 'Hardware Error';
        }
    } catch(e) {}
}

async function rebootCamera() {
    if(runtimeConfig.camera_mode === 'browser') {
        showToast('Restarting browser camera...');
        stopCamera();
        setTimeout(() => {
            if(currentView === 'dashboard' && sessionClassId && sessionSubjectId) {
                startCamera();
            }
        }, 300);
        return;
    }

    showToast('Rebooting Camera Hardware...');
    try {
        await fetch('/api/camera_reboot', { method: 'POST' });
        setTimeout(() => {
            // Force refresh feeds if they were active
            const f1 = document.getElementById('camera-feed');
            const f2 = document.getElementById('reg-camera-feed');
            if(f1 && f1.src.includes('video_feed')) f1.src = f1.src.split('&t=')[0] + '&t=' + Date.now();
            if(f2 && f2.src.includes('video_feed')) f2.src = f2.src.split('&t=')[0] + '&t=' + Date.now();
            showToast('Camera Subsystem Ready');
        }, 2000);
    } catch(e) {
        showToast('Failed to reboot camera');
    }
}

function updateUserUI() {
    const roleBadge = document.getElementById('display-role-badge');
    const usernameDisplay = document.getElementById('display-username');
    const initials = document.getElementById('user-initials');

    usernameDisplay.innerText = currentUser.name || currentUser.username;
    initials.innerText = (currentUser.name || currentUser.username).substring(0, 2).toUpperCase();

    const isAdmin = currentUser.role === 'admin';
    const isFaculty = currentUser.role === 'faculty';
    const isStudent = currentUser.role === 'student';

    roleBadge.innerText = isAdmin ? 'ADMIN ACCESS' : (isFaculty ? 'FACULTY PORTAL' : 'STUDENT PORTAL');
    roleBadge.style.color = isStudent ? '#10b981' : (isAdmin ? 'var(--primary)' : 'var(--success)');

    // Toggle Sidebar Items
    document.querySelectorAll('.admin-only').forEach(el => el.style.display = isAdmin ? 'block' : 'none');
    document.querySelectorAll('.faculty-only').forEach(el => el.style.display = (isAdmin || isFaculty) ? 'flex' : 'none');
    document.querySelectorAll('.student-only').forEach(el => el.style.display = isStudent ? 'flex' : 'none');
    document.querySelectorAll('.not-student-only').forEach(el => el.style.display = isStudent ? 'none' : 'block');
    
    // Hide standard dashboard stuff if student
    if(isStudent) {
        document.querySelectorAll('.nav-item:not(.student-only)').forEach(el => el.style.display = 'none');
    }
}

function showView(viewId) {
    if(currentView && currentView !== viewId && !isBackNavigation) {
        viewHistory.push(currentView);
        if(viewHistory.length > 30) viewHistory.shift();
    }

    // Stop all active camera feeds when switching views to prevent hardware lock
    const dashboardFeed = document.getElementById('camera-feed');
    const regFeed = document.getElementById('reg-camera-feed');
    if(dashboardFeed) dashboardFeed.src = '';
    if(regFeed) regFeed.src = '';
    if(browserRegInterval) {
        clearInterval(browserRegInterval);
        browserRegInterval = null;
    }
    if(browserRegStream) {
        browserRegStream.getTracks().forEach(track => track.stop());
        browserRegStream = null;
    }
    if(browserRegVideo) {
        browserRegVideo.srcObject = null;
        browserRegVideo.style.display = 'none';
    }
    
    document.querySelectorAll('.view').forEach(v => v.style.display = 'none');
    document.querySelectorAll('.nav-item').forEach(v => v.classList.remove('active'));
    
    const activeView = document.getElementById(viewId + '-view');
    if(activeView) activeView.style.display = 'block';
    
    const activeNav = document.querySelector(`.nav-item[onclick*="${viewId}"]`);
    if(activeNav) activeNav.classList.add('active');

    if(viewId === 'faculty-registration') {
        const randomId = 'FID-' + new Date().getFullYear() + '-' + Math.floor(1000 + Math.random() * 9000);
        const idField = document.getElementById('tch-id');
        if(idField) idField.value = randomId;
        
        // Load dependencies for the form
        if(typeof loadTeachers === 'function') loadTeachers();
        if(typeof loadSubjectsForFacultyReg === 'function') loadSubjectsForFacultyReg();
    }
    
    const titleMap = {
        'dashboard': 'Control & Intelligence Dashboard',
        'student-dashboard': 'Academic Presence Portal',
        'manage-classes': 'Class Master Management',
        'manage-teachers': 'Faculty Directory',
        'faculty-registration': 'Faculty Onboarding Portal',
        'teacher-assignments': 'Teacher Assignments',
        'registration': 'Biometric Registration',
        'manage-students': 'Student Database',
        'class-selection': 'Attendance Portal',
        'attendance-view': 'Local Logs',
        'reports': 'Global Attendance Logs',
        'date-wise-attendance': 'Date wise Intelligence',
        'day-wise-attendance': 'Day wise Insights',
        'student-profile': 'Student Academic Profile'
    };
    
    document.getElementById('current-view-title').innerText = titleMap[viewId] || 'Portal';
    currentView = viewId;
    isBackNavigation = false;
    updateBackButtonVisibility();

    // Load dynamic data per view
    if (viewId === 'manage-classes') loadClasses();
    if (viewId === 'manage-teachers') { loadTeachers(); }
    if (viewId === 'faculty-registration') { loadSubjectsForFacultyReg(); loadTeachers(); }
    if (viewId === 'manage-subjects') loadSubjectsGlobally();
    if (viewId === 'teacher-assignments') { loadClasses(); loadTeachers(); loadSubjectsForAssignments(); loadAssignments(); }
    if (viewId === 'registration') loadClasses();
    if (viewId === 'manage-students') {
        loadStudentsGlobally();
        populateCourseFilter();
    }
    if (viewId === 'class-selection') { loadAssignedClasses(); }
    if (viewId === 'reports') loadAttendanceLogs();
    if (viewId === 'student-profile') loadStudentProfile();

    // Student Dashboard Autorefresh — 60s (was 8s) to conserve Firestore reads
    if(viewId === 'student-dashboard') {
        if(window.studentInterval) clearInterval(window.studentInterval);
        window.studentInterval = setInterval(loadStudentData, 60000);
    } else {
        if(window.studentInterval) clearInterval(window.studentInterval);
    }

    if(viewId === 'date-wise-attendance') {
        // Reset table if needed or leave as is
    }
}

function updateBackButtonVisibility() {
    const backBtn = document.getElementById('back-view-btn');
    if(!backBtn) return;
    backBtn.disabled = viewHistory.length === 0;
}

function goBackView() {
    const prevView = viewHistory.pop();
    if(!prevView) {
        updateBackButtonVisibility();
        return;
    }
    isBackNavigation = true;
    showView(prevView);
}

async function populateCourseFilter() {
    try {
        const res = await fetch('/api/classes');
        const classes = await res.json();
        const filter = document.getElementById('student-course-filter');
        if(!filter) return;
        
        // Deduplicate by ShortName
        const uniqueShortNames = [...new Set(classes.map(c => c.ShortName).filter(Boolean))].sort();

        filter.innerHTML = '<option value="">All Registrations</option>';
        uniqueShortNames.forEach(sn => {
            const classObj = classes.find(c => c.ShortName === sn);
            filter.innerHTML += `<option value="${classObj.id}" style="background: var(--bg-dark);">${sn}</option>`;
        });
    } catch(e) { console.error('Failed to populate filter', e); }
}

async function updateSidebarDepartments() {
    try {
        const res = await fetch('/api/classes');
        const classes = await res.json();
        const submenu = document.getElementById('dept-submenu');
        if(!submenu) return;

        // Sort classes by name
        classes.sort((a, b) => a.ShortName.localeCompare(b.ShortName));

        submenu.innerHTML = classes.map(c => `
            <a href="#" class="nav-item nav-subitem" onclick="showView('manage-students'); filterStudentsByClass('${c.id}')">
                <i class="fas fa-circle-dot"></i> <span>${c.ShortName}</span>
            </a>
        `).join('');
    } catch(e) { console.error('Sidebar sync failed'); }
}

window.filterStudentsByClass = async (classId) => {
    // Switch to view if not there
    showView('manage-students');
    // Set the filter select if it exists
    const filter = document.getElementById('student-course-filter');
    if(filter) filter.value = classId;
    
    // Perform filtering
    loadStudentsGlobally(classId);
};


async function loadInitialData() {
    // 1. Load summary stats
    try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        const totalEl = document.getElementById('stat-total');
        const todayEl = document.getElementById('stat-today');
        if(totalEl) totalEl.innerText = data.total_students || 0;
        if(todayEl) todayEl.innerText = data.today_attendance || 0;
    } catch(e) { console.error('Stat load failed'); }

    // 2. Load Explorer Options (Admin Only)
    if (currentUser.role !== 'admin') return;
    try {
        const res = await fetch('/api/classes');
        const classes = await res.json();
        const select = document.getElementById('dash-course-select');
        if(select) {
            // Deduplicate by ShortName
            const uniqueShortNames = [...new Set(classes.map(c => c.ShortName))].sort();
            select.innerHTML = '<option value="">All Students (Global)</option>' + 
                uniqueShortNames.map(sn => {
                    const classObj = classes.find(c => c.ShortName === sn);
                    return `<option value="${classObj.id}">${sn}</option>`;
                }).join('');
        }
        
        // Initial load of all students in explorer
        loadStudentsForExplorer("");
    } catch(e) { console.error('Explorer init failed', e); }
}

async function loadStudentsForExplorer(filterClassId) {
    const container = document.getElementById('explorer-student-list');
    const badge = document.getElementById('dept-total-badge');
    if(!container) return;

    container.innerHTML = `
        <div style="grid-column: 1/-1; text-align:center; padding:3rem; color:#94a3b8; font-size:0.85rem;">
            <i class="fas fa-satellite-dish fa-spin" style="font-size:1.5rem; margin-bottom:1rem; display:block; color: var(--primary);"></i>
            Fetching course records...
        </div>`;

    try {
        const res = await fetch('/api/students');
        let students = await res.json();
        
        if(filterClassId) {
            students = students.filter(s => String(s.ClassId) === String(filterClassId));
        }

        // Sort by Name
        students.sort((a,b) => a.name.localeCompare(b.name));

        if(badge) badge.innerText = `${students.length} PROFILES FOUND`;

        if(!students.length) {
            container.innerHTML = `
                <div style="grid-column: 1/-1; text-align:center; padding:3rem; color:#94a3b8; font-size:0.85rem; background: rgba(0,0,0,0.02); border-radius: 12px; border: 1px dashed rgba(0,0,0,0.05);">
                    <i class="fas fa-folder-open" style="font-size:2rem; margin-bottom:1rem; display:block; opacity: 0.2;"></i>
                    No profiles matching this sequence.
                </div>`;
            return;
        }

        container.innerHTML = students.map(s => `
            <div class="student-explorer-card" style="background: rgba(255,255,255,0.7); border: 1px solid rgba(37,99,235,0.08); border-radius: 12px; padding: 1rem; display: flex; align-items: center; gap: 12px; transition: all 0.2s ease;">
                <div style="width: 38px; height: 38px; background: rgba(37,99,235,0.1); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--primary); font-weight: 800; font-size: 0.8rem; flex-shrink: 0;">
                    ${s.name.substring(0,1)}
                </div>
                <div style="overflow: hidden;">
                    <div style="font-weight: 700; font-size: 0.85rem; color: #1e293b; white-space: nowrap; text-overflow: ellipsis; overflow: hidden;">${s.name}</div>
                    <div style="font-size: 0.65rem; color: #64748b; margin-top: 1px; display: flex; align-items: center; gap: 6px;">
                        <span style="color: var(--primary); font-weight: 800;">${s.EnrollmentNo}</span>
                        <span style="opacity: 0.3;">|</span>
                        <span>${s.ClassName || 'General'}</span>
                    </div>
                </div>
            </div>
        `).join('');

    } catch(e) {
        console.error('Explorer load failed', e);
        container.innerHTML = '<div style="grid-column: 1/-1; color: var(--danger); text-align: center; padding: 2rem;">Critical sync error.</div>';
    }
}


async function loadStudentData() {
    try {
        const statsRes = await fetch('/api/student/summary');
        const stats = await statsRes.json();
        document.getElementById('std-stat-total').innerText = `${stats.present} Sessions`;

        const logRes = await fetch('/api/student/attendance');
        const logs = await logRes.json();
        tbody.innerHTML = logs.length ? logs.map(l => `
            <tr>
                <td>${new Date(l.DateTime).toLocaleString()}</td>
                <td>${l.ClassName}</td>
                <td><span class="badge" style="background: var(--primary); color: #fff; font-size: 0.7rem; padding: 2px 8px; border-radius: 6px;">${l.SubjectName || 'General'}</span></td>
                <td>${l.TeacherName}</td>
                <td><span style="color: var(--success); font-weight: 600;">PRESENT</span></td>
            </tr>
        `).join('') : '<tr><td colspan="5" style="text-align:center; padding: 2rem; color: var(--muted);">No attendance records found yet.</td></tr>';
    } catch(e) { console.error('Failed to load student dashboard', e); }
}

async function loadDateWiseAttendance(date) {
    if(!date) return;
    const tbody = document.getElementById('date-wise-body');
    if(!tbody) return;
    
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 2rem;">Searching logs...</td></tr>';
    
    try {
        const res = await fetch(`/api/student/attendance/date?date=${date}`);
        const logs = await res.json();
        
        if(!logs.length) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 2rem; color: var(--text-muted);">No records found for the selected date.</td></tr>';
            return;
        }
        
        tbody.innerHTML = logs.map(l => `
            <tr>
                <td style="font-weight: 600;">${l.SubjectName}</td>
                <td><span style="color: var(--success); font-weight: 700;">PRESENT</span></td>
                <td>${l.Time || 'N/A'}</td>
                <td>${l.TeacherName}</td>
            </tr>
        `).join('');
    } catch(e) {
        console.error('Date-wise load failed', e);
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color: var(--danger);">Failed to sync records.</td></tr>';
    }
}

async function loadDayWiseAttendance(day) {
    if(!day) return;
    const tbody = document.getElementById('day-wise-body');
    if(!tbody) return;
    
    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding: 2rem;">Analyzing patterns...</td></tr>';
    
    try {
        const res = await fetch(`/api/student/attendance/day?day=${day}`);
        const stats = await res.json();
        
        if(!stats.length) {
            tbody.innerHTML = `<tr><td colspan="3" style="text-align:center; padding: 2rem; color: var(--text-muted);">No attendance recorded on ${day}s yet.</td></tr>`;
            return;
        }
        
        tbody.innerHTML = stats.map(s => `
            <tr>
                <td style="font-weight: 600;">${s.SubjectName}</td>
                <td style="text-align: center;"><span class="badge" style="background: var(--primary); color: #fff;">${s.Frequency} Times</span></td>
                <td>${s.LastPresent}</td>
            </tr>
        `).join('');
    } catch(e) {
        console.error('Day-wise load failed', e);
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color: var(--danger);">Failed to analyze patterns.</td></tr>';
    }
}

// --- Class Management ---
async function loadClasses() {
    const res = await fetch('/api/classes');
    const classes = await res.json();
    
    // Fill selects
    const selects = ['asgn-class', 'reg-class'];
    selects.forEach(id => {
        const el = document.getElementById(id);
        if(!el) return;
        el.innerHTML = '<option value="">Select Class...</option>';
        classes.forEach(c => {
            el.innerHTML += `<option value="${c.id}">${c.ClassName} (${c.ShortName})</option>`;
        });
    });

    // Fill table
    const tbody = document.getElementById('table-classes');
    if(tbody) {
        tbody.innerHTML = classes.map(c => `
            <tr>
                <td>${c.id}</td>
                <td>${c.ClassName}</td>
                <td>${c.ShortName}</td>
                <td>${c.student_count || 0}</td>
                <td>
                    <button class="btn-danger" style="padding: 2px 8px; font-size: 0.7rem;" onclick="deleteClass(${c.id})">Delete</button>
                </td>
            </tr>
        `).join('');
    }
}

async function addClass(e) {
    e.preventDefault();
    const name = document.getElementById('cls-name').value;
    const short = document.getElementById('cls-short').value;
    const res = await fetch('/api/classes', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, short_name: short})
    });
    if(res.ok) {
        showToast('Class Created Successfully');
        loadClasses();
        updateSidebarDepartments();
        e.target.reset();
    }
}

// --- Teacher Management ---
async function loadTeachers() {
    console.log("[UI] Synchronizing faculty directory...");
    try {
        const tRes = await fetch('/api/faculty_db');
        const teachers = await tRes.json();
        
        // Update Badge
        const badge = document.getElementById('faculty-count-badge');
        if(badge) badge.innerText = `${teachers.length} AUTHORIZED FACULTY`;

        // Fill table
        const tbody = document.getElementById('table-teachers');
        if(tbody) {
            if(!teachers.length) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:3rem; color:var(--text-muted);">No faculty records found.</td></tr>';
                return;
            }

            tbody.innerHTML = teachers.map(t => {
                const displayName = (t.Name || t.name || 'Unknown Faculty').trim();
                const initials = displayName.split(' ').filter(Boolean).map(n => n[0]).join('').toUpperCase().substring(0, 2) || 'NA';

                const tAsgn = Array.isArray(t.assignments) ? t.assignments : [];
                const asgnHtml = tAsgn.length > 0 
                    ? tAsgn.map(a => `
                        <div style="margin-bottom: 4px; font-size: 0.75rem;">
                            <span style="font-weight: 700; color: var(--primary);">${a.class_name}:</span>
                            <span class="badge" style="background: rgba(37,99,235,0.1); color: var(--primary); font-size: 0.7rem; font-weight: 700; border: 1px solid rgba(37,99,235,0.2);">
                                ${a.subject_code ? '[' + a.subject_code + '] ' : ''}${a.subject_name}
                            </span>
                        </div>
                    `).join('')
                    : '<span style="color: var(--text-muted); font-size: 0.75rem; font-style: italic;">No Subjects Allocated</span>';

                return `
                <tr>
                    <td>
                        <div style="display: flex; align-items: center; gap: 12px;">
                            <div style="width: 40px; height: 40px; border-radius: 12px; background: var(--primary-light); color: var(--primary); display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 0.85rem; border: 1px solid rgba(37,99,235,0.1);">
                                ${initials}
                            </div>
                            <div>
                                <div style="font-weight: 700; color: #1e293b;">${displayName}</div>
                                <div style="font-size: 0.7rem; color: var(--text-muted);">ID: ${t.id}</div>
                            </div>
                        </div>
                    </td>
                    <td><code style="background: #f1f5f9; padding: 4px 8px; border-radius: 6px; font-weight: 600; font-size: 0.8rem;">@${t.Username}</code></td>
                    <td>${asgnHtml}</td>
                    <td>
                        <span style="display: inline-flex; align-items: center; gap: 6px; color: ${t.IsActive ? 'var(--success)' : 'var(--danger)'}; font-size: 0.7rem; font-weight: 800; background: ${t.IsActive ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)'}; padding: 4px 12px; border-radius: 50px;">
                            <span style="width: 6px; height: 6px; background: currentColor; border-radius: 50%;"></span>
                            ${t.IsActive ? 'ACTIVE' : 'LOCKED'}
                        </span>
                    </td>
                    <td>
                        <div style="display: flex; gap: 8px;">
                            <button class="btn btn-danger" onclick="deleteFaculty('${t.id}')" style="padding: 6px 10px; font-size: 0.7rem;" title="Revoke Access">
                                <i class="fas fa-user-slash"></i>
                            </button>
                        </div>
                    </td>
                </tr>
                `;
            }).join('');
        }
    } catch (err) {
        console.error('Failed to load faculty directory:', err);
    }
}

async function deleteFaculty(id) {
    if(!confirm('Are you sure you want to revoke access for this faculty? This will permanently remove their record.')) return;
    
    try {
        const res = await fetch(`/api/teacher/${id}`, { method: 'DELETE' });
        const result = await res.json();
        if(result.success) {
            showToast('Faculty access revoked successfully', 'success');
            loadTeachers();
        } else {
            showToast(result.message || 'Failed to revoke access', 'error');
        }
    } catch(err) {
        console.error('Delete faculty error:', err);
        showToast('System error while revoking access', 'error');
    }
}

async function loadSubjectsForFacultyReg() {
    const container = document.getElementById('subjects-checkbox-list');
    if(!container) return;
    
    // Get selected departments to filter subjects
    const selectedDepts = Array.from(document.querySelectorAll('input[name="tch-dept-check"]:checked'))
                               .map(cb => cb.value.toUpperCase());
    
    // Mapping of Dept to Subject Code Prefixes (BTech -> BT, etc.)
    const prefixMap = {
        'BCA': ['BCA'],
        'MCA': ['MCA'],
        'BTECH': ['BT', 'BTECH'],
        'MTECH': ['MT', 'MTECH']
    };

    let allowedPrefixes = [];
    selectedDepts.forEach(dept => {
        if(prefixMap[dept]) allowedPrefixes = allowedPrefixes.concat(prefixMap[dept]);
    });

    try {
        const res = await fetch('/api/subjects');
        let subjects = await res.json();
        
        // Filter based on selected departments
        if(allowedPrefixes.length > 0) {
            subjects = subjects.filter(s => {
                if(!s.SubjectCode) return false;
                const code = s.SubjectCode.toUpperCase();
                return allowedPrefixes.some(pref => code.startsWith(pref));
            });
        } else {
            // If none selected, show instructions
            subjects = []; 
        }
        
        if(!subjects.length) {
            container.innerHTML = '<div style="padding: 1rem; text-align: center; color: #64748b; font-size: 0.8rem; grid-column: 1/-1;">' + 
                (selectedDepts.length > 0 ? '<i class="fas fa-exclamation-circle"></i> No subjects found for selected courses.' : '<i class="fas fa-info-circle"></i> Select Department / Courses above to view available subjects.') + 
                '</div>';
            return;
        }

        // Sort alphabetically
        subjects.sort((a,b) => a.SubjectName.localeCompare(b.SubjectName));

        container.innerHTML = subjects.map(s => `
            <label class="subject-checkbox-item">
                <input type="checkbox" name="faculty-subjects" value="${s.id}">
                <span>${s.SubjectName} (${s.SubjectCode || 'CS'})</span>
            </label>
        `).join('');
    } catch(e) {
        console.error('Failed to load subjects for registration', e);
        container.innerHTML = '<div style="padding: 10px; color: #ef4444; font-size: 0.8rem;">Error loading subjects.</div>';
    }
}
async function addTeacher(e) {
    e.preventDefault();
    
    // Collect selected subjects
    const subjectCheckboxes = document.querySelectorAll('input[name="faculty-subjects"]:checked');
    const subjectIds = Array.from(subjectCheckboxes).map(cb => cb.value);

    // Collect selected departments/courses
    const deptCheckboxes = document.querySelectorAll('input[name="tch-dept-check"]:checked');
    const departments = Array.from(deptCheckboxes).map(cb => cb.value).join(', ');

    const data = {
        faculty_id: document.getElementById('tch-id').value,
        name: document.getElementById('tch-name').value,
        department: departments || 'General',
        username: document.getElementById('tch-user').value,
        email: document.getElementById('tch-email').value.trim().toLowerCase(),
        phone: document.getElementById('tch-phone').value.trim().replace(/\s+/g, ''),
        password: document.getElementById('tch-pass').value,
        specialization: document.getElementById('tch-spec').value,
        subject_ids: subjectIds
    };

    const gmailRegex = /^[A-Za-z0-9._%+-]+@gmail\.com$/;
    const indianPhoneRegex = /^\+91\d{10}$/;
    if(!gmailRegex.test(data.email)) {
        showToast('Email must be a valid @gmail.com address', 'error');
        return;
    }
    if(!indianPhoneRegex.test(data.phone)) {
        showToast('Contact number must be in +91XXXXXXXXXX format', 'error');
        return;
    }
    
    showToast('Registering faculty...', 'info');
    try {
        const res = await fetch('/api/teachers', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
        
        const result = await res.json();
        
        if(res.ok && result.success) {
            showToast('Faculty Authorized successfully', 'success');
            loadTeachers();
            e.target.reset();
            // Clear checkboxes
            subjectCheckboxes.forEach(cb => cb.checked = false);
            deptCheckboxes.forEach(cb => cb.checked = false);
        } else {
            showToast(result.message || 'Authorization failed. Please check inputs.', 'error');
        }
    } catch(err) {
        console.error('Teacher add error:', err);
        // Try to parse error message if it's a response error
        showToast('System connectivity error: Unable to contact authentication server', 'error');
    }
}

function toggleTchPass() {
    const passInput = document.getElementById('tch-pass');
    const icon = document.getElementById('tch-pass-icon');
    if (passInput.type === "password") {
        passInput.type = "text";
        icon.classList.remove('fa-eye');
        icon.classList.add('fa-eye-slash');
    } else {
        passInput.type = "password";
        icon.classList.remove('fa-eye-slash');
        icon.classList.add('fa-eye');
    }
}

// --- Subject Management ---
async function loadSubjectsGlobally() {
    const res = await fetch('/api/subjects');
    let subjects = await res.json();
    
    // Filter: Keep only subjects with a standard alphanumeric code
    subjects = subjects.filter(s => s.SubjectCode && /^[a-zA-Z]+\d+$/.test(s.SubjectCode.trim()));
    const tbody = document.getElementById('table-subjects');
    if(tbody) {
        tbody.innerHTML = subjects.map(s => `
            <tr>
                <td>${s.id.substring(0,8)}</td>
                <td>${s.SubjectCode}</td>
                <td>${s.SubjectName}</td>
                <td><button class="btn-danger" style="padding: 2px 8px; font-size: 0.7rem;">Remove</button></td>
            </tr>
        `).join('') || '<tr><td colspan="4" style="text-align:center">No subjects found</td></tr>';
    }
}

async function addSubject(e) {
    e.preventDefault();
    const name = document.getElementById('sub-name').value;
    const code = document.getElementById('sub-code').value;
    const res = await fetch('/api/subjects', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, code})
    });
    if(res.ok) {
        showToast('Subject Added');
        loadSubjectsGlobally();
        e.target.reset();
    }
}

// --- Student Database Management ---
async function loadStudentsGlobally(filterClassId = null) {
    console.log("[UI] Loading global student database...");
    const tbody = document.getElementById('student-db-body');
    if(!tbody) return;
    
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 1.5rem;">Syncing intelligence records...</td></tr>';
    
    try {
        const res = await fetch('/api/students');
        let students = await res.json();
        
        // Apply professional filter if provided
        if (filterClassId) {
            students = students.filter(s => s.ClassId == filterClassId);
            const titleEl = document.getElementById('current-view-title');
            const selectedClass = students.length > 0 ? students[0].ClassName : "Selected Course";
            if (titleEl) titleEl.innerText = `Students: ${selectedClass}`;
        } else {
            const titleEl = document.getElementById('current-view-title');
            if (titleEl) titleEl.innerText = `Global Student Registry`;
        }

        if(!students.length) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 2.5rem; color: #64748b;">
                <i class="fas fa-search" style="font-size: 2rem; opacity: 0.2; margin-bottom: 1rem; display: block;"></i>
                No matching student profiles found.
            </td></tr>`;
            return;
        }

        // Sort students by name
        students.sort((a,b) => a.name.localeCompare(b.name));

        tbody.innerHTML = students.map(s => `
            <tr>
                <td>#${s.id}</td>
                <td style="font-weight: 700;">${s.name}</td>
                <td><code style="background: rgba(37,99,235,0.05); color: var(--primary); padding: 4px 8px; border-radius: 6px; font-weight: 700;">${s.EnrollmentNo}</code></td>
                <td>${s.ClassName || 'N/A'}</td>
                <td>
                    <span style="display: inline-flex; align-items: center; gap: 6px; color: ${s.IsActive ? '#10b981' : '#ef4444'}; font-size: 0.7rem; font-weight: 800; background: ${s.IsActive ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)'}; padding: 4px 10px; border-radius: 50px;">
                        <span style="width: 6px; height: 6px; background: currentColor; border-radius: 50%;"></span>
                        ${s.IsActive ? 'VERIFIED' : 'SUSPENDED'}
                    </span>
                </td>
                <td>
                    <button class="btn btn-outline" style="padding: 4px 10px; font-size: 0.65rem;" onclick="toggleStudentStatus(${s.id}, ${!s.IsActive})">
                        <i class="fas ${s.IsActive ? 'fa-user-slash' : 'fa-user-check'}"></i> ${s.IsActive ? 'Alt State' : 'Activate'}
                    </button>
                </td>
            </tr>
        `).join('');
    } catch(e) {
        console.error('Failed to load students', e);
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color: #ef4444; padding: 1.5rem;">Intelligence link failure. Contact system admin.</td></tr>';
    }
}

async function toggleStudentStatus(sid, status) {
    showToast('Updating student status...');
    try {
        const res = await fetch('/api/students/status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: sid, active: status})
        });
        if(res.ok) {
            showToast('Student Profile Updated', 'success');
            loadStudentsGlobally();
        } else {
            showToast('Permission Denied', 'error');
        }
    } catch(e) {
        showToast('Network timeout', 'error');
    }
}

// --- Assignment Management ---
async function loadAssignments() {
    const res = await fetch('/api/assignments');
    const list = await res.json();
    const tbody = document.getElementById('table-assignments');
    if(tbody) {
        tbody.innerHTML = list.map(a => `
            <tr>
                <td>${a.id.substring(0,8)}</td>
                <td>${a.teacher_name}</td>
                <td>${a.class_name}</td>
                <td><span class="badge" style="background: var(--primary); color: #000; font-weight: 700;">${a.subject_name}</span></td>
                <td><button class="btn-danger" style="padding: 2px 8px; font-size: 0.7rem;">Unlink</button></td>
            </tr>
        `).join('') || '<tr><td colspan="5" style="text-align:center">No allocations recorded</td></tr>';
    }
}

async function loadSubjectsForAssignments() {
    try {
        const res = await fetch('/api/subjects');
        const subjects = await res.json();
        const el = document.getElementById('asgn-subject');
        if(el) {
            el.innerHTML = '<option value="">Select Subject...</option>';
            subjects.forEach(s => {
                el.innerHTML += `<option value="${s.id}">${s.SubjectName} (${s.SubjectCode})</option>`;
            });
        }
    } catch(e) {
        console.error('Failed to load subjects for assignments:', e);
    }
}

async function assignTeacher(e) {
    e.preventDefault();
    const tid = document.getElementById('asgn-teacher').value;
    const cid = document.getElementById('asgn-class').value;
    const sid = document.getElementById('asgn-subject').value;
    
    showToast('Linking faculty to curriculum...', 'info');
    const res = await fetch('/api/assign', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({teacher_id: tid, class_id: cid, subject_id: sid})
    });
    if(res.ok) {
        showToast('Teacher Linked Successfully', 'success');
        loadAssignments();
    }
}

// --- Faculty Specific ---
async function loadAssignedClasses() {
    console.log("[UI] Loading assigned classes...");
    try {
        const res = await fetch('/api/assigned_classes');
        if(!res.ok) throw new Error(`HTTP Error: ${res.status}`);
        const classes = await res.json();
        console.log(`[UI] Received ${classes.length} classes.`);
        
        const el = document.getElementById('faculty-class-select');
        if(el) {
            if(!classes.length) {
                el.innerHTML = '<option value="">No classes found in DB</option>';
                return;
            }
            facultyAssignments = classes; // Cache globally
            el.innerHTML = '<option value="">Choose Your Assigned Course</option>';
            
            // Deduplicate classes for the dropdown by class_id
            const uniqueClasses = [];
            const seenClassIds = new Set();
            classes.forEach(c => {
                const name = c.class_name || c.ClassName || c.ShortName || c.name || "Unnamed Course";
                const cid = c.class_id || c.id; 
                
                if(cid && !seenClassIds.has(String(cid))) {
                    uniqueClasses.push({ id: String(cid), name: name });
                    seenClassIds.add(String(cid));
                }
            });

            uniqueClasses.forEach(c => {
                el.innerHTML += `<option value="${c.id}">${c.name}</option>`;
            });
        }
    } catch(e) {
        console.error('[UI] Load assigned classes failed:', e);
        showToast('Failed to load classes. Check console.');
    }
}

async function loadSessionPrerequisites() {
    const cid = document.getElementById('faculty-class-select').value;
    const subEl = document.getElementById('faculty-subject-select');
    const container = document.getElementById('roster-list-container');
    
    console.log(`[UI] Loading prerequisites for Class: ${cid}`);
    if(!cid) {
        if(container) container.style.display = 'none';
        return;
    }
    
    try {
        subEl.innerHTML = '<option value="">Loading Subjects...</option>';
        let subjects = [];
        const isFaculty = currentUser.role === 'faculty';

        console.log(`[UI] Filtering for View: ${window.currentView}, User Role: ${currentUser.role}`);
        
        if(isFaculty) {
            console.log(`[UI] Faculty filtering for class_id: ${cid}. Total assignments: ${facultyAssignments.length}`);
            // Filter assignments for this specific class
            subjects = facultyAssignments
                .filter(a => String(a.class_id || a.id) === String(cid))
                .map(a => ({
                    id: String(a.subject_id || a.SubjectId),
                    SubjectName: a.subject_name || a.SubjectName || "Unknown Subject",
                    SubjectCode: a.subject_code || a.SubjectCode || '' 
                }));
            
            console.log(`[UI] Filtered subjects:`, subjects);
        } 
        
        // If not faculty, or if filtering returned NOTHING (indicating no specific allotment yet)
        // If not faculty, or if filtering returned NOTHING (indicating no specific allotment yet)
        if (!isFaculty || (isFaculty && subjects.length === 0)) {
            if (isFaculty) console.warn("[UI] No specific assignments found for this faculty. Fetching class-wide subjects.");
            
            const classSelect = document.getElementById('faculty-class-select');
            
            // Get all subjects
            const subRes = await fetch(`/api/subjects`);
            subjects = await subRes.json();
            
            // Get the short code from the selected course name (e.g. "MCA")
            const selectedText = classSelect.options[classSelect.selectedIndex].text;
            const match = selectedText.match(/\(([^)]+)\)/);
            const targetCode = match ? match[1].toUpperCase() : selectedText.substring(0, 3).toUpperCase();
            
            console.log(`[UI] Fallback filtering subjects for target: ${targetCode}`);
            
            subjects = subjects.filter(s => {
                if(!s.SubjectCode) return false;
                const code = s.SubjectCode.toUpperCase();
                // Match against BCA, MCA, BTECH, etc.
                return code.startsWith(targetCode) || code.includes(targetCode);
            });
        }
        
        if (subjects.length > 0) {
            subEl.innerHTML = '<option value="">Select Subject</option>' + 
                subjects.map(s => {
                    const code = s.SubjectCode || s.subject_code || s.code || '';
                    const name = s.SubjectName || s.subject_name || s.name || '';
                    const display = code ? `${code} - ${name}` : name;
                    return `<option value="${s.id}">${display}</option>`;
                }).join('');
            
            if (subjects.length === 1) {
                subEl.value = subjects[0].id;
            }
        } else {
            subEl.innerHTML = '<option value="GEN">General Session / No Allocation</option>';
        }

        // Reset Section to All (Hidden)
        const secEl = document.getElementById('faculty-section-select');
        if(secEl) {
            secEl.innerHTML = '<option value="">All Sections</option>';
            secEl.value = "";
        }

        // Don't auto-load roster here, wait for subject selection unless a subject was auto-selected
        if (subEl.value) loadRosterList();
        else { if(container) container.style.display = 'none'; }

    } catch(e) {
        console.error('[UI] Load prerequisites failed:', e);
        showToast('Connection error loading subjects/sections');
    }
}

async function loadRosterList() {
    const cid = document.getElementById('faculty-class-select').value;
    const sid = document.getElementById('faculty-subject-select').value;
    const sec = document.getElementById('faculty-section-select').value;
    const container = document.getElementById('roster-list-container');
    const list = document.getElementById('roster-list');

    if(!cid || !sid) {
        if(container) container.style.display = 'none';
        return;
    }

    try {
        list.innerHTML = '<div style="padding: 2rem; text-align: center;"><i class="fas fa-spinner fa-spin"></i> Retrieving Roster...</div>';
        container.style.display = 'block';

        const res = await fetch(`/api/students/attendance_list?class_id=${cid}&section=${sec}`);
        if(!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || 'Failed to load student list');
        }
        const students = await res.json();
        if(!Array.isArray(students)) {
            throw new Error('Invalid student list response');
        }
        
        // Cache globally for attendance tracking
        expectedStudents = students.map(s => ({
            id: String(s.id),
            name: s.name,
            EnrollmentNo: s.EnrollmentNo
        }));

        console.log(`[UI] Roster Loaded. Total Students: ${expectedStudents.length}`);

        // Initialize expected/scanned panes immediately after class+subject selection.
        updateAttendanceList([]);
        renderAbsentList([]);
        
        if(!students.length) {
            list.innerHTML = '<div style="padding: 2rem; color: var(--text-muted); text-align: center;"><i class="fas fa-user-slash" style="display:block; font-size: 1.5rem; margin-bottom: 10px;"></i>No students found.</div>';
            return;
        }

        list.innerHTML = students.map(s => `
            <div class="roster-item" id="roster-student-${s.id}" style="display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid rgba(0,0,0,0.03); background: #fff; margin-bottom: 5px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.02); transition: 0.3s;" onmouseover="this.style.transform='translateX(5px)'; this.style.borderColor='var(--primary)'" onmouseout="this.style.transform='none'; this.style.borderColor='transparent'">
                <div style="display: flex; align-items: center; gap: 12px; flex: 1; min-width: 0;">
                    <div style="width: 36px; height: 36px; background: linear-gradient(135deg, var(--primary), var(--secondary)); color: #fff; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 0.8rem;">
                        ${s.name ? s.name[0] : '?'}
                    </div>
                    <div>
                        <div style="font-size: 0.9rem; font-weight: 700; color: #1e293b;">${s.name}</div>
                        <div style="font-size: 0.7rem; color: #64748b; font-weight: 500;">Roll: ${s.EnrollmentNo}</div>
                    </div>
                </div>
                <div class="attendance-status" id="status-${s.id}" style="text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 4px; flex-shrink: 0; min-width: 92px;">
                    <div style="font-size: 0.6rem; color: #ef4444; font-weight: 800; margin-bottom: 4px; letter-spacing: 0.5px;">AWAITING...</div>
                    <button onclick="markManualAttendance('${s.id}', this)" class="btn" style="background: rgba(37,99,235,0.05); color: var(--primary); border: 1px solid rgba(37,99,235,0.2); padding: 4px 10px; font-size: 0.65rem; font-weight: 700; line-height: 1; border-radius: 6px; transition: 0.3s; display: inline-flex; align-items: center; justify-content: center; gap: 4px; white-space: nowrap; min-width: 82px; box-sizing: border-box;">
                        <i class="fas fa-check"></i> Mark
                    </button>
                </div>
            </div>
        `).join('');
    } catch(e) {
        console.error('[UI] Failed to load roster:', e);
        list.innerHTML = '<div style="color: var(--danger); text-align: center;">Error loading roster.</div>';
    }
}

async function markManualAttendance(studentId, btn) {
    const cid = document.getElementById('faculty-class-select').value;
    const sub = document.getElementById('faculty-subject-select').value;
    const sec = document.getElementById('faculty-section-select').value;
    const date = document.getElementById('faculty-date-select').value;

    if(!sub) {
        showToast('Please select a subject first!', 'error');
        return;
    }

    try {
        const originalText = btn.innerText;
        btn.innerText = 'Marking...';
        btn.disabled = true;

        const res = await fetch('/api/attendance/manual', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                student_id: studentId,
                class_id: cid,
                subject_id: sub,
                section: sec,
                date: date
            })
        });

        const data = await res.json();
        if(data.success) {
            btn.innerHTML = '<i class="fas fa-check"></i> Present';
            btn.style.background = 'var(--success)';
            btn.style.color = '#fff';
            btn.style.borderColor = 'var(--success)';
            btn.style.minWidth = '82px';
            btn.style.whiteSpace = 'nowrap';
            showToast('Attendance logged manually');
            await refreshAttendanceNow();
        } else {
            showToast(data.message || 'Already marked today', 'info');
            btn.innerText = originalText;
            btn.disabled = false;
        }
    } catch(e) {
        console.error('[UI] Manual attendance failed:', e);
        showToast('System Error');
        btn.disabled = false;
    }
}

async function loadSubjects() { /* Replaced by loadSessionPrerequisites */ }

async function loadAbsentStudents() {
    if(!sessionClassId) return; // section can be empty for 'All Sections'
    const res = await fetch(`/api/students/attendance_list?class_id=${sessionClassId}&section=${sessionSection || ''}`);
    const data = await res.json();
    expectedStudents = data.map(s => ({
        id: s.id,
        name: s.name,
        EnrollmentNo: s.EnrollmentNo
    }));
    renderAbsentList([]); // Initially all are absent
}

function startFacultyAttendance() {
    const cid = document.getElementById('faculty-class-select').value;
    const sid = document.getElementById('faculty-subject-select').value;
    const section = document.getElementById('faculty-section-select').value || "";
    const date = document.getElementById('faculty-date-select').value;

    if(!cid) return showToast('Please select a class');
    if(!sid) return showToast('Please select a subject');
    if(!date) return showToast('Please select a date');

    // Force-reset any previous session/poller before starting a new one.
    if(attendanceInterval) {
        clearInterval(attendanceInterval);
        attendanceInterval = null;
    }
    isStreaming = false;

    sessionClassId = cid;
    sessionSubjectId = sid;
    sessionSection = section;
    sessionDate = date;

    const classText = document.getElementById('faculty-class-select').selectedOptions[0].text;
    const subText = document.getElementById('faculty-subject-select').selectedOptions[0].text;
    
    document.getElementById('active-session-label').innerHTML = `
        <span style="color:#fff">${classText}</span> | 
        <span style="color:var(--primary)">${subText}</span>`;

    loadAbsentStudents();
    showView('dashboard');
    startCamera();
}

// --- Student Registration ---
async function handleRegister(e) {
    e.preventDefault();
    const data = {
        enrollment_no: document.getElementById('reg-sid').value,
        name: document.getElementById('reg-name').value,
        class_id: document.getElementById('reg-class').value,
        username: document.getElementById('reg-user').value,
        password: document.getElementById('reg-pass').value,
        section: document.getElementById('reg-section').value,
        batch: document.getElementById('reg-batch').value
    };

    const res = await fetch('/api/register', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    });

    if(res.ok) {
        const result = await res.json();
        showToast('Info saved. Starting Biometric Capture...');
        startCapture(result.id, data.class_id);
    } else {
        const error = await res.json();
        showToast('Registration Error: ' + (error.message || 'Check fields'));
    }
}

function startCapture(sid, cid) {
    const container = document.getElementById('reg-camera-container');
    const fill = document.getElementById('progress-fill');
    const text = document.getElementById('progress-text');
    const feed = document.getElementById('reg-camera-feed');
    
    container.style.display = 'block';
    fill.style.width = '0%';
    text.innerText = 'Initializing sensor...';

    if(runtimeConfig.camera_mode === 'browser') {
        startBrowserRegistrationCapture(sid, cid);
        return;
    }
    
    // Ensure camera is started before setting src
    const url = `/video_feed?action=capture&student_id=${sid}&class_id=${cid}&t=${Date.now()}`;
    feed.src = url;

    let errorCount = 0;
    const poller = setInterval(async () => {
        try {
            const res = await fetch('/api/capture_status');
            if(!res.ok) throw new Error('Status check failed');
            
            const status = await res.json();
            
            // Check for security errors (Level 1 Security: De-duplication)
            if(status.error === 'DUPLICATE') {
                clearInterval(poller);
                text.innerText = "SECURITY ALERT: Face Already Registered";
                text.style.color = "#ef4444";
                fill.style.background = "#ef4444";
                showToast('Registration BLOCKED: Face profile already exists.', 'error');
                
                // Keep the frame frozen for a moment so the user sees the 'X' then stop
                setTimeout(() => {
                    feed.src = '';
                }, 3000);
                return;
            }

            fill.style.width = status.progress + '%';
            
            if(status.count > 0) {
                text.innerText = `Scanning: ${status.count} / 100 samples`;
            } else {
                text.innerText = `Waiting for face detection...`;
            }
            
            if(!status.capturing && status.count >= 100) {
                clearInterval(poller);
                showToast('Biometric Capture Complete');
                feed.src = '';
                container.style.display = 'none';
                if(window.currentView === 'manage-students') loadStudentsGlobally(); 
            }
            errorCount = 0;
        } catch(e) {
            errorCount++;
            if(errorCount > 10) {
                clearInterval(poller);
                showToast('Registration Timed Out. Please retry.');
            }
        }
    }, 800);
}

function ensureBrowserRegistrationVideo() {
    if (browserRegVideo) return browserRegVideo;
    const container = document.getElementById('reg-camera-container');
    if (!container) return null;

    const video = document.createElement('video');
    video.id = 'reg-browser-camera-feed';
    video.autoplay = true;
    video.playsInline = true;
    video.muted = true;
    video.style.width = '100%';
    video.style.height = '100%';
    video.style.objectFit = 'cover';
    video.style.display = 'none';

    container.appendChild(video);
    browserRegVideo = video;
    return browserRegVideo;
}

async function startBrowserRegistrationCapture(sid, cid) {
    const fill = document.getElementById('progress-fill');
    const text = document.getElementById('progress-text');
    const feed = document.getElementById('reg-camera-feed');
    const video = ensureBrowserRegistrationVideo();

    if (!video) {
        showToast('Camera container not available. Reload and try again.');
        return;
    }

    if (browserRegStream) {
        browserRegStream.getTracks().forEach(track => track.stop());
        browserRegStream = null;
    }

    try {
        browserRegStream = await getUserCameraStream();
    } catch (e) {
        showToast(getCameraErrorMessage(e));
        return;
    }

    if(feed) {
        feed.src = '';
        feed.style.display = 'none';
    }
    video.srcObject = browserRegStream;
    video.style.display = 'block';

    if (browserRegInterval) clearInterval(browserRegInterval);
    browserRegInterval = setInterval(async () => {
        if (!browserRegVideo || browserRegVideo.readyState < 2) return;

        const canvas = document.createElement('canvas');
        canvas.width = browserRegVideo.videoWidth || 640;
        canvas.height = browserRegVideo.videoHeight || 480;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(browserRegVideo, 0, 0, canvas.width, canvas.height);

        try {
            const res = await fetch('/api/capture_frame', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    image: canvas.toDataURL('image/jpeg', 0.75),
                    student_id: sid,
                    class_id: cid
                })
            });
            if (!res.ok) return;
            const status = await res.json();
            if (!status.success) return;

            fill.style.width = status.progress + '%';
            text.innerText = `Scanning: ${status.count} / 100 samples`;

            if (status.done || status.progress >= 100) {
                clearInterval(browserRegInterval);
                browserRegInterval = null;
                if (browserRegStream) {
                    browserRegStream.getTracks().forEach(track => track.stop());
                    browserRegStream = null;
                }
                if (browserRegVideo) {
                    browserRegVideo.srcObject = null;
                    browserRegVideo.style.display = 'none';
                }
                showToast('Biometric Capture Complete');
                const container = document.getElementById('reg-camera-container');
                if(container) container.style.display = 'none';
                if(window.currentView === 'manage-students') loadStudentsGlobally();
            }
        } catch (e) {}
    }, 500);
}

// --- Recognition Session ---
function startCamera() {
    if(runtimeConfig.camera_mode === 'browser') {
        startBrowserCameraSession();
        return;
    }

    const feed = document.getElementById('camera-feed');
    const placeholder = document.getElementById('placeholder');
    const scanLine = document.getElementById('scan-line');
    const stopBtn = document.getElementById('stop-btn');
    
    feed.style.display = 'block';
    placeholder.style.display = 'none';
    scanLine.style.display = 'block';
    stopBtn.style.display = 'block';

    const url = `/video_feed?action=recognize&class_id=${sessionClassId}&subject_id=${sessionSubjectId}&section=${sessionSection}&date=${sessionDate}`;
    feed.src = url;
    isStreaming = true;

    // Start polling attendance list
    startAttendancePolling();
    refreshAttendanceNow();
}

function ensureBrowserVideoElement() {
    if(browserCameraVideo) return browserCameraVideo;

    const scanner = document.querySelector('.scanner-container');
    if(!scanner) return null;

    const video = document.createElement('video');
    video.id = 'browser-camera-feed';
    video.autoplay = true;
    video.playsInline = true;
    video.muted = true;
    video.style.display = 'none';
    video.style.width = '100%';
    video.style.height = '100%';
    video.style.objectFit = 'cover';
    video.style.borderRadius = '12px';

    scanner.appendChild(video);
    browserCameraVideo = video;
    return browserCameraVideo;
}

async function postBrowserFrame() {
    if(!browserCameraVideo || !browserCameraStream || browserCameraVideo.readyState < 2) return;
    if(!sessionClassId || !sessionSubjectId) return;

    const canvas = document.createElement('canvas');
    canvas.width = browserCameraVideo.videoWidth || 640;
    canvas.height = browserCameraVideo.videoHeight || 480;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(browserCameraVideo, 0, 0, canvas.width, canvas.height);

    const image = canvas.toDataURL('image/jpeg', 0.75);
    try {
        await fetch('/api/recognize_frame', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                image,
                class_id: sessionClassId,
                subject_id: sessionSubjectId,
                section: sessionSection,
                date: sessionDate
            })
        });
    } catch(e) {
        console.warn('Frame upload failed', e);
    }
}

async function startBrowserCameraSession() {
    const feed = document.getElementById('camera-feed');
    const placeholder = document.getElementById('placeholder');
    const scanLine = document.getElementById('scan-line');
    const stopBtn = document.getElementById('stop-btn');
    const videoEl = ensureBrowserVideoElement();

    if(!videoEl) {
        showToast('Camera container not available. Reload and try again.');
        return;
    }

    if (browserCameraStream) {
        browserCameraStream.getTracks().forEach(track => track.stop());
        browserCameraStream = null;
    }

    try {
        browserCameraStream = await getUserCameraStream();
    } catch(e) {
        showToast(getCameraErrorMessage(e));
        return;
    }

    videoEl.srcObject = browserCameraStream;
    videoEl.style.display = 'block';
    if(feed) {
        feed.src = '';
        feed.style.display = 'none';
    }
    if(placeholder) placeholder.style.display = 'none';
    if(scanLine) scanLine.style.display = 'block';
    if(stopBtn) stopBtn.style.display = 'block';

    isStreaming = true;
    startAttendancePolling();
    refreshAttendanceNow();

    if(browserRecognitionInterval) clearInterval(browserRecognitionInterval);
    browserRecognitionInterval = setInterval(postBrowserFrame, 1400);
}

function stopCamera() {
    const feed = document.getElementById('camera-feed');
    const placeholder = document.getElementById('placeholder');
    const scanLine = document.getElementById('scan-line');
    const stopBtn = document.getElementById('stop-btn');

    feed.src = '';
    feed.style.display = 'none';
    if(browserRecognitionInterval) {
        clearInterval(browserRecognitionInterval);
        browserRecognitionInterval = null;
    }
    if(browserCameraStream) {
        browserCameraStream.getTracks().forEach(track => track.stop());
        browserCameraStream = null;
    }
    if(browserCameraVideo) {
        browserCameraVideo.srcObject = null;
        browserCameraVideo.style.display = 'none';
    }
    placeholder.style.display = 'flex';
    scanLine.style.display = 'none';
    stopBtn.style.display = 'none';
    isStreaming = false;
    
    if(attendanceInterval) clearInterval(attendanceInterval);
}

let attendanceInterval = null;
function startAttendancePolling() {
    if(attendanceInterval) clearInterval(attendanceInterval);
    // Fast sync so recognized student moves from expected -> scanned almost instantly.
    attendanceInterval = setInterval(refreshAttendanceNow, 2000);
}

async function refreshAttendanceNow() {
    if(!sessionClassId || !sessionSubjectId) return;
    try {
        const res = await fetch(`/api/attendance_log?class_id=${sessionClassId}&subject_id=${sessionSubjectId}&section=${sessionSection}&date=${sessionDate}`);
        if(!res.ok) return;
        const data = await res.json();
        updateAttendanceList(Array.isArray(data) ? data : []);
    } catch(e) {
        console.warn('Attendance poll failed (quota?)', e);
    }
}

function updateAttendanceList(presentList) {
    const el = document.getElementById('attendance-list');
    const presentCount = document.getElementById('present-count');
    
    const safeList = Array.isArray(presentList) ? presentList : [];
    if(presentCount) presentCount.innerText = safeList.length;
    
    if(!safeList.length) {
        if(el) el.innerHTML = '<div class="empty-state" style="padding:1rem; text-align:center; color:var(--muted); font-size:0.8rem;"><p>Waiting for detection...</p></div>';
        renderAbsentList([]);
        return;
    }

    if(el) {
        el.innerHTML = safeList.map(item => `
            <div class="log-item" style="display:grid; grid-template-columns: 40px 1fr 100px 30px; align-items:center; gap:1rem; padding:0.75rem 1rem; border-bottom:1px solid rgba(16, 185, 129, 0.1); background: rgba(16, 185, 129, 0.02); transition: 0.2s; border-radius: 8px; margin-bottom: 4px;">
                <div class="avatar" style="width:32px; height:32px; background:linear-gradient(135deg, #10b981, #059669); border-radius:50%; display:flex; align-items:center; justify-content:center; color:#fff; font-weight:800; font-size:0.75rem; box-shadow: 0 4px 10px rgba(16, 185, 129, 0.2);">
                    ${item.FullName ? item.FullName.substring(0,1) : '?'}
                </div>
                <div class="info">
                    <p style="font-weight:700; font-size:0.85rem; margin:0; color: #1e293b;">${item.FullName}</p>
                    <p style="font-size:0.65rem; color:var(--text-muted); margin:0;">Roll: ${item.EnrollmentNo}</p>
                </div>
                <div class="status" style="font-size: 0.6rem; text-align: right;">
                    <p style="margin:0; font-weight:800; color: var(--success);">PRESENT</p>
                    <p style="margin:0; color: var(--text-muted); opacity: 0.8;">${item.DateTime ? item.DateTime.split(' ')[1] : ''}</p>
                </div>
                <i class="fas fa-check-double" style="color:var(--success); font-size:0.85rem;"></i>
            </div>
        `).join('');
    }

    // Sync with Manual Roster List
    safeList.forEach(p => {
        const sid = String(p.StudentId || p.id || '');
        const statusEl = document.getElementById(`status-${sid}`);
        if(statusEl) {
            statusEl.innerHTML = '<span class="badge" style="background: var(--success); color: #fff; font-size: 0.6rem; padding: 4px 8px; border-radius: 6px;">VERIFIED</span>';
            const row = document.getElementById(`roster-student-${sid}`);
            if(row) row.style.background = 'rgba(16, 185, 129, 0.05)';
        }
    });

    renderAbsentList(safeList);
}

function renderAbsentList(presentList) {
    const el = document.getElementById('absent-list');
    const absentCount = document.getElementById('absent-count');
    
    const presentIds = new Set((presentList || []).map(p => String(p.StudentId || p.id || '')));
    const absentStudents = expectedStudents.filter(s => !presentIds.has(s.id));
    
    absentCount.innerText = absentStudents.length;

    if(!absentStudents.length && expectedStudents.length > 0) {
        el.innerHTML = '<div class="empty-state" style="padding:1rem; text-align:center; color:var(--success); font-size:0.8rem;"><i class="fas fa-check-double"></i><p>All present!</p></div>';
        return;
    }

    if(!absentStudents.length) {
        el.innerHTML = '<div class="empty-state" style="padding:1rem; text-align:center; color:var(--muted); font-size:0.8rem;"><p>Group empty</p></div>';
        return;
    }

    el.innerHTML = absentStudents.map(s => `
        <div class="log-item" style="display:grid; grid-template-columns: 40px 1fr 80px; align-items:center; gap:1rem; padding:0.75rem 1rem; border-bottom:1px solid rgba(239, 68, 68, 0.06); background: rgba(239, 68, 68, 0.01); border-radius: 8px; margin-bottom: 4px; transition: 0.2s;">
            <div class="avatar" style="width:32px; height:32px; background:rgba(239, 68, 68, 0.1); border-radius:50%; display:flex; align-items:center; justify-content:center; color:#ef4444; font-weight:700; font-size:0.75rem; border: 1px dashed rgba(239, 68, 68, 0.3);">
                ${s.name.substring(0,1)}
            </div>
            <div class="info">
                <p style="font-weight:700; font-size:0.85rem; margin:0; color: #475569; opacity: 0.8;">${s.name}</p>
                <p style="font-size:0.65rem; color:var(--text-muted); margin:0;">Roll: ${s.EnrollmentNo}</p>
            </div>
            <div class="status" style="font-size: 0.6rem; text-align: right; margin-right: 10px;">
                <p style="margin:0; font-weight:800; color: #ef4444; background: rgba(239, 68, 68, 0.1); padding: 2px 6px; border-radius: 4px;">ABSENT</p>
                <p style="margin:0; color: var(--text-muted); font-size: 0.55rem; margin-top: 2px;">Awaiting Recognition</p>
            </div>
            <button onclick="markPresenceManual(${s.id})" style="background: var(--primary-light); border: 1px solid var(--primary); color: var(--primary); font-size: 0.6rem; padding: 4px 10px; border-radius: 8px; cursor: pointer; font-weight: 700; transition: 0.3s; display: flex; align-items: center; gap: 4px;" onmouseover="this.style.background='var(--primary)'; this.style.color='#fff'" onmouseout="this.style.background='var(--primary-light)'; this.style.color='var(--primary)'">
                <i class="fas fa-check"></i> Mark
            </button>
        </div>
    `).join('');
}

async function markPresenceManual(studentId) {
    if(!sessionClassId || !sessionSubjectId) return;
    
    try {
        const res = await fetch('/api/attendance/manual', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                student_id: studentId,
                class_id: sessionClassId,
                subject_id: sessionSubjectId,
                section: sessionSection
            })
        });

        if(res.ok) {
            showToast('Attendance marked manually');
            await refreshAttendanceNow();
        } else {
            showToast('Failed to mark attendance');
        }
    } catch(e) {
        showToast('System Error');
    }
}

async function loadAttendanceLogs() {
    const res = await fetch('/api/reports');
    const data = await res.json();
    const tbody = document.getElementById('attendance-table-body');
        tbody.innerHTML = data.map(r => `
            <tr>
                <td>${r.id}</td>
                <td>${r.student_name}</td>
                <td>${r.class_name}</td>
                <td><span style="background: rgba(37,99,235,0.1); color: var(--primary); padding: 4px 10px; border-radius: 50px; font-weight: 800; font-size: 0.65rem;">${r.subject_name || 'N/A'}</span></td>
                <td>${r.teacher_name}</td>
                <td>${r.date_time}</td>
                <td><span style="color:var(--success); font-weight: 800;">VERIFIED</span></td>
            </tr>
        `).join('');
}

function showToast(msg, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.style.background = type === 'success' ? 'var(--success)' : 
                            type === 'error' ? 'var(--danger)' : 'var(--primary)';
    toast.style.color = '#fff';
    toast.style.transform = 'translateY(0)';
    setTimeout(() => toast.style.transform = 'translateY(200%)', 4000);
}

async function fetchClasses() {
    const res = await fetch('/get_classes');
    const classes = await res.json();
    document.getElementById('table-classes').innerHTML = classes.map(c => `
        <tr>
            <td>#${c.id.substring(0,6)}</td>
            <td><strong>${c.name}</strong></td>
            <td><code style="background: rgba(0,242,255,0.1); color: var(--primary); padding: 2px 6px; border-radius: 4px;">${c.short_name}</code></td>
            <td>—</td>
            <td><button class="btn btn-danger" style="padding: 4px 8px; font-size: 0.6rem;" onclick="deleteItem('classes', '${c.id}')"><i class="fas fa-trash"></i></button></td>
        </tr>
    `).join('');
}

async function fetchTeachers() {
    const res = await fetch('/get_teachers');
    const teachers = await res.json();
    document.getElementById('table-teachers').innerHTML = teachers.map(t => `
        <tr>
            <td>#${t.id.substring(0,6)}</td>
            <td>${t.Name}</td>
            <td>@${t.Username}</td>
            <td>${t.Specialization}</td>
            <td><button class="btn btn-danger" style="padding: 4px 8px; font-size: 0.6rem;" onclick="deleteItem('teachers', '${t.id}')"><i class="fas fa-trash"></i></button></td>
        </tr>
    `).join('');
}

async function loadAssignmentPrerequisites() {
    try {
        const [resT, resC] = await Promise.all([
            fetch('/api/teachers'),
            fetch('/api/classes')
        ]);
        const teachers = await resT.json();
        const classes = await resC.json();
        
        // Deduplicate teachers by username
        const uniqueTeachers = [];
        const seenUsers = new Set();
        teachers.forEach(t => {
            if(!seenUsers.has(t.Username)) {
                uniqueTeachers.push(t);
                seenUsers.add(t.Username);
            }
        });

        // Deduplicate classes by ClassName
        const uniqueClasses = [];
        const seenClasses = new Set();
        classes.forEach(c => {
            const name = c.ClassName || c.ShortName;
            if(!seenClasses.has(name)) {
                uniqueClasses.push({ id: c.id, name: name });
                seenClasses.add(name);
            }
        });

        document.getElementById('asgn-teacher').innerHTML = uniqueTeachers.map(t => `<option value="${t.Username}">${t.Name}</option>`).join('');
        document.getElementById('asgn-class').innerHTML = uniqueClasses.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
    } catch(e) {
        console.error("Assignment init failed", e);
    }
}

async function fetchAssignments() {
    const res = await fetch('/get_all_assignments');
    const data = await res.json();
    document.getElementById('table-assignments').innerHTML = data.map(a => `
        <tr>
            <td>#${a.id.substring(0,6)}</td>
            <td>${a.teacher_name}</td>
            <td>${a.class_name}</td>
            <td><button class="btn btn-danger" style="padding: 4px 8px; font-size: 0.6rem;" onclick="deleteItem('assignments', '${a.id}')"><i class="fas fa-trash"></i></button></td>
        </tr>
    `).join('');
}

async function loadStudentDashboard() {
    const res = await fetch(`/api/student/attendance`);
    const logs = await res.json();
    document.getElementById('std-stat-total').textContent = `${logs.length} Sessions`;
    document.getElementById('student-attendance-body').innerHTML = logs.map(l => `
        <tr>
            <td>${new Date(l.DateTime).toLocaleString()}</td>
            <td>${l.ClassName}</td>
            <td><span class="badge" style="background: var(--primary); color: #fff; font-size: 0.7rem; padding: 2px 8px; border-radius: 6px;">${l.SubjectName || 'General'}</span></td>
            <td>${l.TeacherName}</td>
            <td><span style="color: var(--success); font-weight: 700;">PRESENT</span></td>
        </tr>
    `).join('');
    
    loadCumulativeReport();
}

async function loadCumulativeReport() {
    try {
        const res = await fetch('/api/student/cumulative');
        const data = await res.json();
        const tbody = document.getElementById('student-cumulative-body');
        
        if(!data.length) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 2rem; color: var(--text-muted);">No academic data available for this session.</td></tr>';
            return;
        }

        tbody.innerHTML = data.map((item, index) => {
            const percent = item.percentage;
            // Eligibility logic: Good (>=75), Warning (60-75), Critical (<60)
            const eligibility = percent >= 75 ? 'Good' : percent >= 60 ? 'Warning' : 'Critical';
            const color = eligibility === 'Good' ? '#10b981' : (eligibility === 'Warning' ? '#f59e0b' : '#ef4444');
            
            const isToday = item.today_status === 'PRESENT';
            const statusColor = isToday ? '#10b981' : '#ef4444';
            const statusText = isToday ? 'PRESENT TODAY' : 'NOT MARKED';

            return `
                <tr>
                    <td style="color: var(--text-muted); font-weight: 600;">${index + 1}</td>
                    <td>
                        <div style="font-weight: 700; color: #1e293b; font-size: 0.85rem;">${item.subject_code} - ${item.subject_name}</div>
                        <div style="font-size: 0.65rem; color: var(--text-muted);">Last attended: ${item.last_attended}</div>
                    </td>
                    <td style="text-align: center; font-weight: 600;">${item.held}</td>
                    <td style="text-align: center; font-weight: 600; color: var(--primary);">${item.present}</td>
                    <td>
                        <div style="display: flex; align-items: center; gap: 10px; min-width: 160px;">
                            <div style="flex: 1; height: 8px; background: rgba(0,0,0,0.05); border-radius: 10px; overflow: hidden; border: 1px solid rgba(0,0,0,0.03);">
                                <div style="width: ${percent}%; height: 100%; background: ${color}; border-radius: 10px; box-shadow: 0 0 10px ${color}44;"></div>
                            </div>
                            <div style="display: flex; align-items: center; gap: 5px;">
                                <span style="font-weight: 800; font-size: 0.82rem; color: #1e293b;">${percent}%</span>
                                <span class="badge" style="background: ${color}15; color: ${color}; border: 1px solid ${color}44; font-size: 0.55rem; padding: 1px 6px; border-radius: 50px; font-weight: 800;">${eligibility}</span>
                            </div>
                        </div>
                    </td>
                    <td style="text-align: center;">
                        <span class="badge" style="background: ${statusColor}15; color: ${statusColor}; border: 1px solid ${statusColor}44; font-size: 0.65rem; padding: 4px 10px; border-radius: 50px; font-weight: 800; display: inline-flex; align-items: center; gap: 4px;">
                            <i class="fas ${isToday ? 'fa-check-circle' : 'fa-clock'}"></i>
                            ${statusText}
                        </span>
                    </td>
                </tr>
            `;
        }).join('');
    } catch(e) {
        console.error("Error loading cumulative report:", e);
    }
}

async function fetchGlobalReports() {
    const res = await fetch('/get_all_attendance');
    const data = await res.json();
    document.getElementById('attendance-table-body').innerHTML = data.map(l => `
        <tr>
            <td>#${l.id.substring(0,6)}</td>
            <td>${l.student_name}</td>
            <td>${l.class_name}</td>
            <td><span class="badge" style="background: rgba(37,99,235,0.1); color: var(--primary); padding: 4px 10px; border-radius: 50px; font-weight: 800; font-size: 0.65rem;">${l.subject_name || 'N/A'}</span></td>
            <td>${l.teacher_name}</td>
            <td>${l.timestamp}</td>
            <td><span class="badge" style="background: var(--success); color: #fff; padding: 2px 8px; border-radius: 10px; font-size: 0.7rem;">VERIFIED</span></td>
        </tr>
    `).join('');
}

// --- Departments Dropdown ---
function toggleDeptDropdown(event) {
    event.preventDefault();
    const submenu = document.getElementById('dept-submenu');
    const arrow = document.getElementById('dept-arrow');
    submenu.classList.toggle('open');
    arrow.classList.toggle('rotated');
}

function filterDept(courseCode) {
    // Highlight the active sub-item
    document.querySelectorAll('.nav-subitem').forEach(el => el.classList.remove('active-sub'));
    const clicked = Array.from(document.querySelectorAll('.nav-subitem')).find(el => el.textContent.trim() === courseCode);
    if (clicked) clicked.classList.add('active-sub');

    // Update the view title to show which department is selected
    const titleEl = document.getElementById('current-view-title');
    if (titleEl) titleEl.innerText = `Department: ${courseCode}`;
}

// --- Missing Implementations added by Antigravity ---

async function loadStudentData() {
    console.log("[UI] Triggering Student Intelligence Sync...");
    await loadStudentDashboard();
}

async function loadDateWiseAttendance(date) {
    if(!date) return;
    console.log(`[UI] Loading date-wise intelligence for: ${date}`);
    const tbody = document.getElementById('date-wise-body');
    if(!tbody) return;
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 2rem;">Fetching records...</td></tr>';
    
    try {
        const res = await fetch(`/api/student/attendance/date?date=${date}`);
        if(!res.ok) throw new Error("API Link Failure");
        const data = await res.json();
        
        if(!data.length) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 2rem; color: var(--text-muted);">No attendance records found for this date.</td></tr>';
            return;
        }

        tbody.innerHTML = data.map(l => `
            <tr>
                <td style="font-weight: 700;">${l.SubjectName || 'N/A'}</td>
                <td><span style="color: var(--success); font-weight: 800;">PRESENT</span></td>
                <td>${l.DateTime ? new Date(l.DateTime).toLocaleTimeString() : 'N/A'}</td>
                <td>${l.TeacherName || 'System'}</td>
            </tr>
        `).join('');
    } catch(e) {
        console.error("Failed to load date-wise attendance:", e);
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color: var(--danger);">Link error.</td></tr>';
    }
}

async function loadStudentProfile() {
    console.log("[UI] Accessing Student Intelligence Records...");
    try {
        const res = await fetch('/api/student/profile');
        let data;
        const contentType = res.headers.get("content-type");
        
        if (contentType && contentType.indexOf("application/json") !== -1) {
            data = await res.json();
        } else {
            const text = await res.text();
            console.error("Non-JSON response received:", text);
            throw new Error(`Server returned HTML instead of Data (Status: ${res.status})`);
        }

        if(!res.ok) {
            throw new Error(data.error || `Error ${res.status}`);
        }
        
        const s = data;
        document.getElementById('profile-name').innerText = s.FullName || s.full_name || s.name || 'Anonymous';
        document.getElementById('profile-enrollment').innerText = `#${s.EnrollmentNo || s.enrollment_no || '000000'}`;
        document.getElementById('profile-course').innerText = s.ClassName || s.class_id || 'N/A';
        document.getElementById('profile-dob').innerText = s.DOB || s.dob || 'N/A';
        document.getElementById('profile-email').innerText = s.EmailId || s.email || 'N/A';
        document.getElementById('profile-phone').innerText = s.PhoneNo || s.phone || 'N/A';
        document.getElementById('profile-username').innerText = `@${s.Username || s.username || 'null'}`;
        document.getElementById('profile-id').innerText = s.id || 'N/A';
        document.getElementById('profile-address').innerText = s.Address || s.address || 'Not Provided';
        
        const avatar = document.getElementById('profile-avatar');
        if(s.photo_url) {
            avatar.innerHTML = `<img src="${s.photo_url}" style="width: 100%; height: 100%; object-fit: cover; border-radius: 50%;">`;
            avatar.style.background = "none";
            avatar.style.border = "2px solid var(--accent-cyan)";
        } else {
            avatar.innerText = (s.FullName || s.full_name || s.name || '?')[0].toUpperCase();
            avatar.style.background = "radial-gradient(circle at top left, var(--accent-cyan), var(--primary))";
        }
        
    } catch(e) {
        console.error("Profile load failed:", e);
        showToast(`Access Denied: ${e.message}`, "error");
    }
}

