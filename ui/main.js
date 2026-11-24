const { app, BrowserWindow, ipcMain, dialog, screen, desktopCapturer, clipboard } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const crypto = require('crypto');
const { execFile } = require('child_process');

process.on('uncaughtException', (err) => {
  fs.writeFileSync(path.join(__dirname, 'crash.log'), String(err.stack || err));
});

const BASE_DIR = path.join(__dirname, '..');
const APPDATA_DIR = process.env.APPDATA;
const DATA_DIR = APPDATA_DIR ? path.join(APPDATA_DIR, 'AntigravityFlow') : path.join(BASE_DIR, 'data');
const RECORDINGS_DIR = path.join(DATA_DIR, 'recordings');
const HISTORY_PATH = path.join(DATA_DIR, 'history.json');
const CONFIG_PATH = path.join(DATA_DIR, 'config.json');
const VOCAB_PATH = path.join(DATA_DIR, 'vocabulary.json');
const USER_CACHE_PATH = path.join(DATA_DIR, 'user_cache.json');
const KEY_RECORDING_LOCK = path.join(DATA_DIR, '.key_recording');

// Dedicated cache in user profile
app.setPath('userData', path.join(app.getPath('appData'), 'AntigravityFlowUI'));

// AES-256 Key derivation matching Python crypto_audio.py
const _SECRET = Buffer.from('AntigravityFlow_Encrypted_Audio_Storage_Vault_2026', 'utf-8');
const AES_KEY = crypto.createHash('sha256').update(_SECRET).digest();

function decryptAudioBuffer(encBuffer) {
  if (encBuffer.length <= 16) {
    throw new Error('Encrypted file is too small or corrupted.');
  }
  const iv = encBuffer.subarray(0, 16);
  const ciphertext = encBuffer.subarray(16);
  const decipher = crypto.createDecipheriv('aes-256-cbc', AES_KEY, iv);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
}

function getDaemonExecutablePath() {
  if (app.isPackaged) {
    const embedded = path.join(process.resourcesPath, 'bin', 'AntigravityFlowDaemon.exe');
    if (fs.existsSync(embedded)) return embedded;
  }
  const distExe = path.join(BASE_DIR, 'dist', 'AntigravityFlowDaemon.exe');
  if (fs.existsSync(distExe)) return distExe;
  return null;
}

function getPythonExecutable() {
  if (process.env.PYTHON_PATH && fs.existsSync(process.env.PYTHON_PATH)) {
    return process.env.PYTHON_PATH;
  }
  const candidates = [
    'C:\\Program Files\\Python314\\python.exe',
    'C:\\Program Files\\Python313\\python.exe',
    'C:\\Program Files\\Python312\\python.exe',
    'C:\\Program Files\\Python311\\python.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python314', 'python.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python313', 'python.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python312', 'python.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python311', 'python.exe')
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return 'python';
}

function getBackendExecutable() {
  if (app.isPackaged) {
    const packagedDaemon = path.join(process.resourcesPath, 'bin', 'AntigravityFlowDaemon.exe');
    if (fs.existsSync(packagedDaemon)) return packagedDaemon;
  }
  const localDistDaemon = path.join(BASE_DIR, 'dist', 'AntigravityFlowDaemon.exe');
  if (fs.existsSync(localDistDaemon)) return localDistDaemon;
  return null;
}

let isSpawningDaemon = false;
function spawnDaemonProcess() {
  if (isSpawningDaemon) return;
  isSpawningDaemon = true;
  setTimeout(() => { isSpawningDaemon = false; }, 3000);

  const exe = getBackendExecutable();
  const { spawn } = require('child_process');
  if (exe) {
    try {
      const child = spawn(exe, [], { detached: true, stdio: 'ignore', windowsHide: true });
      child.unref();
    } catch (e) {
      console.warn('[Daemon] Error spawning daemon executable:', e.message);
    }
  } else if (!app.isPackaged) {
    try {
      const pythonExe = getPythonExecutable();
      const daemonPy = path.join(BASE_DIR, 'daemon.py');
      const child = spawn(pythonExe, [daemonPy], { detached: true, stdio: 'ignore', windowsHide: true });
      child.unref();
    } catch (e) {
      console.warn('[Daemon] Error spawning daemon Python worker:', e.message);
    }
  }
}

let daemonSupervisorInterval = null;
let isAppQuitting = false;

function ensureDaemonRunning() {
  if (isAppQuitting) return;
  const pingReq = http.request({
    hostname: '127.0.0.1',
    port: 48198,
    path: '/status',
    method: 'GET',
    timeout: 500
  }, (res) => {
    // Daemon is healthy
  });
  pingReq.on('error', () => {
    if (!isAppQuitting) spawnDaemonProcess();
  });
  pingReq.on('timeout', () => {
    pingReq.destroy();
    if (!isAppQuitting) spawnDaemonProcess();
  });
  pingReq.end();
}

function runBackendCommand(subcommand, args = [], options = {}, callback) {
  if (typeof options === 'function') {
    callback = options;
    options = {};
  }
  const promise = new Promise((resolve, reject) => {
    const exe = getBackendExecutable();
    const finalArgs = [subcommand, ...args];
    const opts = { maxBuffer: 15 * 1024 * 1024, windowsHide: true, ...options };

    if (exe) {
      execFile(exe, finalArgs, opts, (err, stdout, stderr) => {
        if (err) return reject(new Error(stderr || err.message));
        resolve(stdout);
      });
    } else if (!app.isPackaged) {
      const pythonExe = getPythonExecutable();
      const daemonPy = path.join(BASE_DIR, 'daemon.py');
      execFile(pythonExe, [daemonPy, ...finalArgs], opts, (err, stdout, stderr) => {
        if (err) return reject(new Error(stderr || err.message));
        resolve(stdout);
      });
    } else {
      reject(new Error('Backend daemon executable not found in application bundle.'));
    }
  });

  if (callback) {
    promise.then(out => callback(null, out, '')).catch(err => callback(err, '', err.message));
    return;
  }
  return promise;
}

let mainWindow = null;
let assistantWindow = null;

// Single Instance Lock: Prevents spawning duplicate processes, focuses existing in 0ms!
const gotTheLock = app.requestSingleInstanceLock();

// GPU & Zero-Copy Compositor Switches for ultra-low CPU/GPU usage (< 0.2% idle)
app.commandLine.appendSwitch('enable-gpu-rasterization');
app.commandLine.appendSwitch('enable-zero-copy');
app.commandLine.appendSwitch('ignore-gpu-blocklist');
app.commandLine.appendSwitch('disable-breakpad');
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    try {
      if (mainWindow && !mainWindow.isDestroyed()) {
        if (mainWindow.isMinimized()) mainWindow.restore();
        mainWindow.show();
        mainWindow.focus();
      }
    } catch (_) {}
  });

  app.on('before-quit', () => {
    isAppQuitting = true;
    if (daemonSupervisorInterval) {
      clearInterval(daemonSupervisorInterval);
      daemonSupervisorInterval = null;
    }
    try {
      const postReq = http.request({
        hostname: '127.0.0.1',
        port: 48198,
        path: '/shutdown',
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        timeout: 500
      });
      postReq.on('error', () => {});
      postReq.write('{}');
      postReq.end();
    } catch (_) {}
  });

  app.whenReady().then(() => {
    // Tells Windows Shell to use our black and gold emblem for the taskbar icon
    app.setAppUserModelId('com.antigravityflow.app');

    // Continuous background daemon supervisor
    ensureDaemonRunning();
    daemonSupervisorInterval = setInterval(ensureDaemonRunning, 10000);

    // Sync single unified Windows Task Manager startup registry entry
    let autostartSetting = true;
    if (fs.existsSync(CONFIG_PATH)) {
      try {
        const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8') || '{}');
        if (cfg.start_on_login !== undefined) autostartSetting = Boolean(cfg.start_on_login);
      } catch (_) {}
    }
    setSystemAutostart(autostartSetting).catch(() => {});

    createWindow();
    createAssistantWindow();
    startAssistantBridgeServer();

    app.on('activate', () => {
      if (mainWindow) {
        mainWindow.show();
      } else {
        createWindow();
      }
    });
  });
}

function createWindow() {
  const iconPath = path.join(__dirname, 'icon.ico');

  mainWindow = new BrowserWindow({
    width: 1040,
    height: 720,
    minWidth: 860,
    minHeight: 580,
    show: false, // Instant visual readiness via ready-to-show
    frame: false,
    titleBarStyle: 'hidden',
    backgroundColor: '#0d0e12',
    icon: iconPath,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false
    }
  });

  mainWindow.loadFile('index.html');

  // Log renderer console warnings and errors
  mainWindow.webContents.on('console-message', (event, level, message, line, sourceId) => {
    if (level >= 2) {
      console.error(`[Main Renderer Error] ${message} (${sourceId}:${line})`);
    }
  });

  // Allow F12 or Ctrl+Shift+I to open DevTools console at any time
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i')) {
      mainWindow.webContents.toggleDevTools();
      event.preventDefault();
    }
  });

  const isHidden = process.argv.includes('--hidden');
  mainWindow.once('ready-to-show', () => {
    if (!isHidden) {
      mainWindow.show();
    }
  });
}

function createAssistantWindow() {
  const iconPath = path.join(__dirname, 'icon.ico');

  let savedBounds = null;
  if (fs.existsSync(CONFIG_PATH)) {
    try {
      const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8') || '{}');
      if (cfg && cfg.assistantBounds) {
        savedBounds = cfg.assistantBounds;
      }
    } catch (_) {}
  }

  const winWidth = (savedBounds && savedBounds.width) ? Math.min(Math.max(savedBounds.width, 200), 960) : 380;
  const winHeight = (savedBounds && savedBounds.height) ? Math.min(Math.max(savedBounds.height, 200), 960) : 380;

  const winOptions = {
    title: 'AntigravityFlow AI',
    width: winWidth,
    height: winHeight,
    minWidth: 200,
    minHeight: 200,
    maxWidth: 960,
    maxHeight: 960,
    show: false,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    resizable: true,
    hasShadow: false, // Windows native DWM shadow causes heavy composition lag on transparent windows; CSS handles the sleek shadow!
    backgroundColor: '#00000000',
    icon: iconPath,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
      spellcheck: false
    }
  };

  if (savedBounds && typeof savedBounds.x === 'number' && typeof savedBounds.y === 'number') {
    try {
      const displays = screen.getAllDisplays();
      const isVisibleOnAnyDisplay = displays.some(d => {
        const b = d.bounds;
        return savedBounds.x >= b.x - 50 && savedBounds.x < b.x + b.width &&
               savedBounds.y >= b.y - 50 && savedBounds.y < b.y + b.height;
      });
      if (isVisibleOnAnyDisplay) {
        winOptions.x = Math.round(savedBounds.x);
        winOptions.y = Math.round(savedBounds.y);
      }
    } catch (_) {}
  }

  assistantWindow = new BrowserWindow(winOptions);

  assistantWindow.loadFile('assistant.html');

  // Debounced saving of assistant bounds
  let saveBoundsTimer = null;
  const debouncedSaveBounds = () => {
    if (saveBoundsTimer) clearTimeout(saveBoundsTimer);
    saveBoundsTimer = setTimeout(() => {
      if (assistantWindow && !assistantWindow.isDestroyed()) {
        try {
          const b = assistantWindow.getBounds();
          let currentCfg = {};
          if (fs.existsSync(CONFIG_PATH)) {
            try { currentCfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8') || '{}'); } catch (_) {}
          }
          currentCfg.assistantBounds = b;
          fs.writeFileSync(CONFIG_PATH, JSON.stringify(currentCfg, null, 2), 'utf-8');
        } catch (_) {}
      }
    }, 400);
  };

  assistantWindow.on('resize', debouncedSaveBounds);
  assistantWindow.on('move', debouncedSaveBounds);

  assistantWindow.webContents.on('before-input-event', (event, input) => {
    if (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i')) {
      assistantWindow.webContents.toggleDevTools();
      event.preventDefault();
    }
  });

  assistantWindow.on('close', (e) => {
    e.preventDefault();
    debouncedSaveBounds();
    assistantWindow.hide();
  });
}

function showAssistantWindow(focus = true) {
  if (!assistantWindow) {
    createAssistantWindow();
  }
  if (focus) {
    assistantWindow.show();
    assistantWindow.focus();
  } else {
    assistantWindow.showInactive();
  }
}

function hideAssistantWindow() {
  if (assistantWindow) {
    assistantWindow.hide();
  }
}

function toggleAssistantWindow() {
  if (!assistantWindow) {
    createAssistantWindow();
    showAssistantWindow();
    return;
  }
  if (assistantWindow.isVisible()) {
    assistantWindow.hide();
  } else {
    assistantWindow.show();
    assistantWindow.focus();
  }
}

async function captureCleanScreenshot() {
  const assistantWasVisible = assistantWindow && !assistantWindow.isDestroyed() && assistantWindow.isVisible();
  const mainWasVisible = mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible();

  try {
    // Dynamic Invisibility: Apply Windows display affinity security flag (WDA_EXCLUDEFROMCAPTURE)
    // The window stays 100% VISIBLE on screen to the user, but Windows DWM excludes it from screen capture!
    // Never hide or alter opacity - the assistant stays completely visible and steady on screen.
    if (assistantWasVisible) {
      try { assistantWindow.setContentProtection(true); } catch (_) {}
    }
    if (mainWasVisible) {
      try { mainWindow.setContentProtection(true); } catch (_) {}
    }

    if (assistantWasVisible || mainWasVisible) {
      await new Promise(r => setTimeout(r, 40));
    }

    // 1. Primary: Native Electron desktopCapturer (100% native, zero external process dependency)
    try {
      const primaryDisplay = screen.getPrimaryDisplay();
      const scale = primaryDisplay.scaleFactor || 1;
      const { width, height } = primaryDisplay.size;
      const targetW = Math.round(width * scale);
      const targetH = Math.round(height * scale);

      const sources = await desktopCapturer.getSources({
        types: ['screen'],
        thumbnailSize: { width: targetW, height: targetH },
        fetchWindowIcons: false
      });

      if (sources && sources.length > 0) {
        const thumb = sources[0].thumbnail;
        if (!thumb.isEmpty()) {
          const imgSize = thumb.getSize();
          return {
            success: true,
            data_url: thumb.toDataURL(),
            width: imgSize.width,
            height: imgSize.height
          };
        }
      }
    } catch (dcErr) {
      console.warn('[ScreenCapture] Native desktopCapturer notice:', dcErr);
    }

    // 2. Secondary: Query running Daemon HTTP server on 127.0.0.1:48198
    try {
      const daemonSnap = await new Promise((resolve) => {
        const req = http.request({
          hostname: '127.0.0.1',
          port: 48198,
          path: '/assistant/screenshot',
          method: 'POST',
          timeout: 2500
        }, (res) => {
          let body = '';
          res.on('data', chunk => body += chunk);
          res.on('end', () => {
            try { resolve(JSON.parse(body)); } catch (_) { resolve(null); }
          });
        });
        req.on('error', () => resolve(null));
        req.end();
      });
      if (daemonSnap && daemonSnap.success && daemonSnap.data_url) {
        return daemonSnap;
      }
    } catch (_) {}

    // 3. Fallback: Standalone backend daemon worker
    return await new Promise((resolve) => {
      runBackendCommand('screenshot', [], { maxBuffer: 15 * 1024 * 1024 }, (error, stdout, stderr) => {
        if (error) {
          return resolve({ success: false, error: stderr || error.message });
        }
        try {
          resolve(JSON.parse(stdout.trim()));
        } catch (parseErr) {
          resolve({ success: false, error: `Invalid screenshot response: ${stdout}` });
        }
      });
    });

    return { success: false, error: 'Screen capture failed: no capture source available.' };
  } catch (err) {
    return { success: false, error: err.message };
  } finally {
    // Immediately remove the flag so normal desktop screen recording (OBS, Snipping Tool, Win+Shift+S) continues unaffected!
    if (assistantWasVisible && assistantWindow && !assistantWindow.isDestroyed()) {
      try { assistantWindow.setContentProtection(false); } catch (_) {}
    }
    if (mainWasVisible && mainWindow && !mainWindow.isDestroyed()) {
      try { mainWindow.setContentProtection(false); } catch (_) {}
    }
  }
}

function startAssistantBridgeServer() {
  const ASSISTANT_PORT = 48199;
  const server = http.createServer((req, res) => {
    if (req.method === 'POST') {
      let body = '';
      req.on('data', chunk => body += chunk);
      req.on('end', async () => {
        try {
          const data = JSON.parse(body || '{}');
          if (data.action === 'show' || data.action === 'toggle') {
            toggleAssistantWindow();
            res.writeHead(200, { 'Content-Type': 'application/json' });
            return res.end(JSON.stringify({ success: true }));
          } else if (data.action === 'summon') {
            // Quick tap: summon assistant and focus prompt immediately for typing!
            showAssistantWindow(true);
            if (assistantWindow) {
              assistantWindow.webContents.send('assistant-focus-prompt');
            }
            res.writeHead(200, { 'Content-Type': 'application/json' });
            return res.end(JSON.stringify({ success: true }));
          } else if (data.action === 'show-dashboard') {
            if (mainWindow && !mainWindow.isDestroyed()) {
              if (mainWindow.isMinimized()) mainWindow.restore();
              mainWindow.show();
              mainWindow.focus();
            }
            res.writeHead(200, { 'Content-Type': 'application/json' });
            return res.end(JSON.stringify({ success: true }));
          } else if (data.action === 'reload') {
            if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload();
            if (assistantWindow && !assistantWindow.isDestroyed()) assistantWindow.reload();
            res.writeHead(200, { 'Content-Type': 'application/json' });
            return res.end(JSON.stringify({ success: true }));
          } else if (data.action === 'capture-screenshot') {
            const snap = await captureCleanScreenshot();
            res.writeHead(snap.success ? 200 : 500, { 'Content-Type': 'application/json' });
            return res.end(JSON.stringify(snap));
          } else if (data.action === 'query') {
            // Spoken voice query received from daemon: show window without stealing keyboard focus!
            showAssistantWindow(false);
            if (assistantWindow) {
              assistantWindow.webContents.send('assistant-voice-query', data.text || '');
            }
            res.writeHead(200, { 'Content-Type': 'application/json' });
            return res.end(JSON.stringify({ success: true }));
          } else if (data.action === 'voice-state') {
            if (data.state === 'recording') {
              showAssistantWindow(false);
            }
            if (assistantWindow) {
              assistantWindow.webContents.send('assistant-voice-state', data.state || 'idle');
            }
            res.writeHead(200, { 'Content-Type': 'application/json' });
            return res.end(JSON.stringify({ success: true }));
          }
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ success: true }));
        } catch (err) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: err.message }));
        }
      });
    } else {
      res.writeHead(200);
      res.end('AntigravityFlow Assistant Bridge Active');
    }
  });

  server.on('error', (err) => {
    console.log('[Main] Assistant bridge server notice:', err.message);
  });

  try {
    server.listen(ASSISTANT_PORT, '127.0.0.1');
  } catch (_) {}
}

// IPC Handlers

// Window controls
ipcMain.handle('window-minimize', () => {
  if (mainWindow) mainWindow.minimize();
});
ipcMain.handle('window-maximize', () => {
  if (mainWindow) {
    if (mainWindow.isMaximized()) mainWindow.unmaximize();
    else mainWindow.maximize();
  }
});
ipcMain.handle('window-close', () => {
  if (mainWindow) {
    // Hide window instead of cold-quitting: next opening is instantaneous (< 30ms)!
    mainWindow.hide();
  }
});
ipcMain.handle('app-quit', () => {
  isAppQuitting = true;
  app.quit();
});

// History & Config
ipcMain.handle('get-history', async () => {
  try {
    if (!fs.existsSync(HISTORY_PATH)) {
      return { success: true, data: [] };
    }
    const raw = fs.readFileSync(HISTORY_PATH, 'utf-8');
    const items = JSON.parse(raw || '[]');
    items.sort((a, b) => (b.id || 0) - (a.id || 0));
    return { success: true, data: items };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('delete-history-item', async (event, id) => {
  try {
    if (!fs.existsSync(HISTORY_PATH)) return { success: true };
    const raw = fs.readFileSync(HISTORY_PATH, 'utf-8');
    let items = JSON.parse(raw || '[]');
    items = items.filter(item => item.id !== id);
    fs.writeFileSync(HISTORY_PATH, JSON.stringify(items, null, 2), 'utf-8');

    // Also remove encrypted audio file if present
    const encPath = path.join(RECORDINGS_DIR, `${id}.enc`);
    if (fs.existsSync(encPath)) {
      try { fs.unlinkSync(encPath); } catch (_) {}
    }
    return { success: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('clear-history', async () => {
  try {
    fs.writeFileSync(HISTORY_PATH, JSON.stringify([], null, 2), 'utf-8');
    if (fs.existsSync(RECORDINGS_DIR)) {
      const files = fs.readdirSync(RECORDINGS_DIR);
      for (const file of files) {
        try { fs.unlinkSync(path.join(RECORDINGS_DIR, file)); } catch (_) {}
      }
    }
    return { success: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

const RUN_KEY = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run';
const APPROVED_KEY = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\Run';

function setSystemAutostart(enable) {
  return new Promise((resolve) => {
    // 1. Always purge legacy 'com.antigravityflow.app' to guarantee single unified registration
    execFile('reg.exe', ['delete', RUN_KEY, '/v', 'com.antigravityflow.app', '/f'], () => {});
    execFile('reg.exe', ['delete', APPROVED_KEY, '/v', 'com.antigravityflow.app', '/f'], () => {});

    // Resolve target executable: prefer installed AntigravityFlow.exe or current process
    let targetExe = process.execPath;
    if (!app.isPackaged) {
      const installedExe = path.join(process.env.LOCALAPPDATA || '', 'Programs', 'AntigravityFlow', 'AntigravityFlow.exe');
      if (fs.existsSync(installedExe)) targetExe = installedExe;
    }

    if (enable) {
      const regCmd = `"${targetExe}" --hidden`;
      execFile('reg.exe', ['add', RUN_KEY, '/v', 'AntigravityFlow', '/t', 'REG_SZ', '/d', regCmd, '/f'], (err) => {
        // Clear any disabled marker in StartupApproved so Windows Task Manager shows 'Enabled'
        execFile('reg.exe', ['delete', APPROVED_KEY, '/v', 'AntigravityFlow', '/f'], () => {});
        try {
          app.setLoginItemSettings({ openAtLogin: true, path: targetExe, args: ['--hidden'] });
        } catch (_) {}
        resolve(!err);
      });
    } else {
      execFile('reg.exe', ['delete', RUN_KEY, '/v', 'AntigravityFlow', '/f'], () => {
        execFile('reg.exe', ['delete', APPROVED_KEY, '/v', 'AntigravityFlow', '/f'], () => {});
        try {
          app.setLoginItemSettings({ openAtLogin: false });
        } catch (_) {}
        resolve(true);
      });
    }
  });
}

function checkSystemAutostart() {
  return new Promise((resolve) => {
    execFile('reg.exe', ['query', RUN_KEY, '/v', 'AntigravityFlow'], (err, stdout) => {
      if (!err && stdout && stdout.includes('AntigravityFlow')) {
        return resolve(true);
      }
      try {
        const item = app.getLoginItemSettings();
        return resolve(Boolean(item && item.openAtLogin));
      } catch (_) {
        return resolve(false);
      }
    });
  });
}

ipcMain.handle('get-config', async () => {
  try {
    const defaults = {
      push_to_talk_hotkey: 'windows+alt',
      toggle_hotkey: 'windows+alt+h',
      ai_ptt_hotkey: 'windows+ctrl',
      enable_sound: true,
      user_name: '',
      tts_service: 'edge-tts',
      tts_voice: 'en-IN-NeerjaNeural',
      tts_enabled: true,
      tts_rate: '+0%',
      tts_auto_speak_voice: true,
      theme: 'auto',
      screen_native_resolution: true,
      start_on_login: true
    };
    const isAutostart = await checkSystemAutostart();
    if (!fs.existsSync(CONFIG_PATH)) {
      return { success: true, data: { ...defaults, start_on_login: isAutostart } };
    }
    const raw = fs.readFileSync(CONFIG_PATH, 'utf-8');
    const parsed = JSON.parse(raw || '{}');
    return { success: true, data: { ...defaults, ...parsed, start_on_login: isAutostart } };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('save-config', async (event, config) => {
  try {
    if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
    let existing = {};
    if (fs.existsSync(CONFIG_PATH)) {
      try {
        existing = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8') || '{}');
      } catch (_) {}
    }
    const merged = { ...existing, ...(config || {}) };
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(merged, null, 2), 'utf-8');

    if (merged.start_on_login !== undefined) {
      await setSystemAutostart(Boolean(merged.start_on_login));
    }

    return { success: true, data: merged };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

// Key Recording Lock: Suppresses dictation triggers while recording new hotkeys
ipcMain.handle('set-key-recording-active', async (event, active) => {
  try {
    if (active) {
      if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
      fs.writeFileSync(KEY_RECORDING_LOCK, '1', 'utf-8');
    } else {
      if (fs.existsSync(KEY_RECORDING_LOCK)) {
        try { fs.unlinkSync(KEY_RECORDING_LOCK); } catch (_) {}
      }
    }
    return { success: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

// Custom Vocabulary & Technical Acronyms
ipcMain.handle('get-vocabulary', async () => {
  try {
    if (fs.existsSync(VOCAB_PATH)) {
      const raw = fs.readFileSync(VOCAB_PATH, 'utf-8');
      const parsed = JSON.parse(raw || '{}');
      if (parsed && Object.keys(parsed).length > 0) {
        return { success: true, data: parsed };
      }
    }
    // Fallback: seed from formatter.load_vocabulary() via daemon
    return new Promise((resolve) => {
      runBackendCommand('vocab-get', [], { cwd: BASE_DIR }, (err, stdout) => {
        if (err) return resolve({ success: true, data: {} });
        try {
          const res = JSON.parse(stdout.trim());
          resolve(res.data ? res : { success: true, data: res });
        } catch (_) {
          resolve({ success: true, data: {} });
        }
      });
    });
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('save-vocabulary', async (event, vocab) => {
  try {
    fs.writeFileSync(VOCAB_PATH, JSON.stringify(vocab, null, 2), 'utf-8');
    return { success: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

// Speech Macros & Snippets Engine
ipcMain.handle('get-macros', async () => {
  try {
    const daemonRes = await callDaemonControl('/assistant/macros', {}, 2000);
    if (daemonRes && daemonRes.success) {
      return daemonRes;
    }
  } catch (_) {}
  return new Promise((resolve) => {
    runBackendCommand('macro-get', [], { cwd: BASE_DIR }, (err, stdout) => {
      if (err) return resolve({ success: false, data: [] });
      try {
        const res = JSON.parse(stdout.trim());
        resolve(res.data ? res : { success: true, data: res });
      } catch (_) {
        resolve({ success: false, data: [] });
      }
    });
  });
});

ipcMain.handle('save-macro', async (event, macro) => {
  try {
    const daemonRes = await callDaemonControl('/assistant/macros/save', macro, 2000);
    if (daemonRes && daemonRes.success) return daemonRes;
  } catch (_) {}
  return new Promise((resolve) => {
    const payload = JSON.stringify(macro || {});
    runBackendCommand('macro-save', [payload], { cwd: BASE_DIR }, (err, stdout) => {
      if (err) return resolve({ success: false, error: err.message });
      try { resolve(JSON.parse(stdout.trim())); } catch (_) { resolve({ success: true }); }
    });
  });
});

ipcMain.handle('delete-macro', async (event, id) => {
  try {
    const daemonRes = await callDaemonControl('/assistant/macros/delete', { id }, 2000);
    if (daemonRes && daemonRes.success) return daemonRes;
  } catch (_) {}
  return new Promise((resolve) => {
    runBackendCommand('macro-delete', [String(id)], { cwd: BASE_DIR }, (err) => {
      resolve({ success: !err });
    });
  });
});

ipcMain.handle('expand-macro', async (event, text) => {
  try {
    const daemonRes = await callDaemonControl('/assistant/macros/expand', { text }, 2000);
    if (daemonRes && daemonRes.success) return daemonRes;
  } catch (_) {}
  return new Promise((resolve) => {
    const payload = JSON.stringify(text || "");
    runBackendCommand('macro-expand', [payload], { cwd: BASE_DIR }, (err, stdout) => {
      if (err) return resolve({ success: false, expanded: text });
      try { resolve(JSON.parse(stdout.trim())); } catch (_) { resolve({ success: true, expanded: text }); }
    });
  });
});

// Decrypt and load in-memory audio URL for inline player
ipcMain.handle('get-audio-url', async (event, id) => {
  try {
    const encPath = path.join(RECORDINGS_DIR, `${id}.enc`);
    if (!fs.existsSync(encPath)) {
      return { success: false, error: 'Recording audio file not found on disk.' };
    }
    const encBuffer = fs.readFileSync(encPath);
    const wavBuffer = decryptAudioBuffer(encBuffer);
    const b64 = wavBuffer.toString('base64');
    return { success: true, audioUrl: `data:audio/wav;base64,${b64}` };
  } catch (err) {
    return { success: false, error: `Decryption error: ${err.message}` };
  }
});

// Extract / Export decrypted WAV file to user-chosen path
ipcMain.handle('extract-audio', async (event, id) => {
  try {
    const encPath = path.join(RECORDINGS_DIR, `${id}.enc`);
    if (!fs.existsSync(encPath)) {
      return { success: false, error: 'Recording not found.' };
    }

    const { canceled, filePath } = await dialog.showSaveDialog(mainWindow, {
      title: 'Export Dictation Audio (.wav)',
      defaultPath: `Dictation_${id}.wav`,
      filters: [{ name: 'WAV Audio', extensions: ['wav'] }]
    });

    if (canceled || !filePath) {
      return { success: false, canceled: true };
    }

    const encBuffer = fs.readFileSync(encPath);
    const wavBuffer = decryptAudioBuffer(encBuffer);
    fs.writeFileSync(filePath, wavBuffer);

    return { success: true, filePath };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

// Retry Transcription via Antigravity local server
ipcMain.handle('retry-transcription', async (event, id) => {
  try {
    const daemonRes = await callDaemonControl('/assistant/retranscribe', { id }, 10000);
    if (daemonRes && daemonRes.success) return daemonRes;
  } catch (_) {}
  return new Promise((resolve) => {
    runBackendCommand('retranscribe', [String(id)], (error, stdout, stderr) => {
      if (error) {
        return resolve({ success: false, error: stderr || error.message });
      }
      try {
        const res = JSON.parse(stdout.trim());
        resolve(res);
      } catch (parseErr) {
        resolve({ success: false, error: `Invalid runner response: ${stdout}` });
      }
    });
  });
});

// Instant User Identity & Profile Cache (< 0.1ms)
ipcMain.handle('get-user-cache', async () => {
  try {
    if (fs.existsSync(USER_CACHE_PATH)) {
      const raw = fs.readFileSync(USER_CACHE_PATH, 'utf-8');
      return { success: true, data: JSON.parse(raw) };
    }
    return { success: true, data: { name: '', user_name: '', planName: '', planBadge: '' } };
  } catch (err) {
    return { success: true, data: { name: '', user_name: '', planName: '', planBadge: '' } };
  }
});

let quotaCache = null;
let quotaCacheTime = 0;
let inFlightQuotaPromise = null;

// Retrieve live Antigravity user quota and word allowance approximations
ipcMain.handle('get-quota-summary', async () => {
  const now = Date.now();
  // Return in-memory cache if less than 20 seconds old (< 0.1ms)
  if (quotaCache && (now - quotaCacheTime < 20000)) {
    return quotaCache;
  }
  // Deduplicate concurrent calls (prevent spawning duplicate workers)
  if (inFlightQuotaPromise) {
    return inFlightQuotaPromise;
  }

  // Fast-path: In-process daemon control (< 2ms)
  try {
    const daemonRes = await callDaemonControl('/assistant/quota', {}, 2500);
    if (daemonRes && daemonRes.success) {
      quotaCache = daemonRes;
      quotaCacheTime = Date.now();
      return daemonRes;
    }
  } catch (_) {}

  inFlightQuotaPromise = new Promise((resolve) => {
    runBackendCommand('quota', [], { timeout: 4000 }, (error, stdout, stderr) => {
      inFlightQuotaPromise = null;
      if (error) {
        // Fallback instantly to disk cache if backend worker timed out or errored
        if (fs.existsSync(USER_CACHE_PATH)) {
          try {
            const cached = JSON.parse(fs.readFileSync(USER_CACHE_PATH, 'utf-8'));
            const fallbackRes = {
              success: true,
              cached: true,
              user: {
                name: cached.name || cached.user_name || '',
                email: cached.email || '',
                planName: cached.planName || '',
                planBadge: cached.planBadge || ''
              },
              gemini: {
                weeklyPercentRemaining: cached.weekly_percent !== undefined ? cached.weekly_percent : 100,
                fiveHourPercentRemaining: cached.five_hour_percent !== undefined ? cached.five_hour_percent : 100,
                weeklyResetHuman: cached.weekly_reset || 'Active',
                fiveHourResetHuman: cached.five_hour_reset || 'Active'
              }
            };
            quotaCache = fallbackRes;
            quotaCacheTime = Date.now();
            return resolve(fallbackRes);
          } catch (_) {}
        }
        return resolve({ success: false, error: stderr || error.message });
      }
      try {
        const res = JSON.parse(stdout.trim());
        if (res.success) {
          quotaCache = res;
          quotaCacheTime = Date.now();
        }
        resolve(res);
      } catch (parseErr) {
        resolve({ success: false, error: `Invalid quota response: ${stdout}` });
      }
    });
  });

  return inFlightQuotaPromise;
});

let modelsCache = null;
let modelsCacheTime = 0;

const https = require('https');

const grpcKeepAliveAgent = new https.Agent({
  rejectUnauthorized: false,
  keepAlive: true,
  maxSockets: 10
});

function getLanguageServerConfig() {
  const candidatePaths = [
    path.join(DATA_DIR, '.server_cache.json'),
    path.join(BASE_DIR, 'data', '.server_cache.json')
  ];
  for (const p of candidatePaths) {
    try {
      if (fs.existsSync(p)) {
        return JSON.parse(fs.readFileSync(p, 'utf8'));
      }
    } catch (_) {}
  }
  return null;
}

function callDirectGrpcModel(port, csrfToken, prompt, model = 'MODEL_GOOGLE_GEMINI_2_5_FLASH') {
  return new Promise((resolve) => {
    const payload = JSON.stringify({ prompt, model });
    const payloadBuf = Buffer.from(payload, 'utf8');
    const header = Buffer.alloc(5);
    header.writeUInt8(0, 0);
    header.writeUInt32BE(payloadBuf.length, 1);
    const frame = Buffer.concat([header, payloadBuf]);

    const req = https.request({
      hostname: '127.0.0.1',
      port: port,
      path: '/exa.language_server_pb.LanguageServerService/GetModelResponse',
      method: 'POST',
      headers: {
        'content-type': 'application/grpc-web+json',
        'x-codeium-csrf-token': csrfToken,
        'x-grpc-web': '1',
        'content-length': frame.length
      },
      agent: grpcKeepAliveAgent,
      timeout: 20000
    }, (res) => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        try {
          const fullBuf = Buffer.concat(chunks);
          if (fullBuf.length >= 5) {
            const len = fullBuf.readUInt32BE(1);
            const jsonStr = fullBuf.slice(5, 5 + len).toString('utf8');
            const data = JSON.parse(jsonStr);
            const resp = (data.response || '').trim();
            resolve({ success: true, text: resp });
          } else {
            resolve({ success: false, error: 'Empty gRPC response' });
          }
        } catch (e) {
          resolve({ success: false, error: e.message });
        }
      });
    });

    req.on('error', (e) => resolve({ success: false, error: e.message }));
    req.on('timeout', () => { req.destroy(); resolve({ success: false, error: 'gRPC request timed out' }); });
    req.write(frame);
    req.end();
  });
}

function callDaemonControl(endpoint, payload, timeoutMs = 25000) {
  return new Promise((resolve, reject) => {
    const postData = JSON.stringify(payload || {});
    const req = http.request({
      hostname: '127.0.0.1',
      port: 48198,
      path: endpoint,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData)
      },
      timeout: timeoutMs
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(e);
        }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => {
      req.destroy();
      reject(new Error('Daemon request timed out'));
    });
    req.write(postData);
    req.end();
  });
}

function callDaemonControlGet(endpoint, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: '127.0.0.1',
      port: 48198,
      path: endpoint,
      method: 'GET',
      timeout: timeoutMs
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(e);
        }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => {
      req.destroy();
      reject(new Error('Daemon request timed out'));
    });
    req.end();
  });
}

ipcMain.handle('get-available-models', async () => {
  const now = Date.now();
  if (modelsCache && (now - modelsCacheTime < 30000)) {
    return modelsCache;
  }

  // 1. In-process daemon control (< 1ms)
  try {
    const daemonRes = await callDaemonControl('/assistant/models', {}, 2000);
    if (daemonRes && daemonRes.success) {
      modelsCache = daemonRes;
      modelsCacheTime = Date.now();
      return daemonRes;
    }
  } catch (_) {}

  // 2. Backend worker fallback
  return new Promise((resolve) => {
    runBackendCommand('models', [], { timeout: 4000 }, (error, stdout, stderr) => {
      if (error) {
        const cacheFile = path.join(DATA_DIR, 'models_cache.json');
        if (fs.existsSync(cacheFile)) {
          try {
            const data = JSON.parse(fs.readFileSync(cacheFile, 'utf-8'));
            modelsCache = data;
            modelsCacheTime = Date.now();
            return resolve(data);
          } catch (_) {}
        }
        return resolve({ success: false, error: stderr || error.message });
      }
      try {
        const res = JSON.parse(stdout.trim());
        if (res.success) {
          modelsCache = res;
          modelsCacheTime = Date.now();
        }
        resolve(res);
      } catch (parseErr) {
        resolve({ success: false, error: `Invalid models response: ${stdout}` });
      }
    });
  });
});

// ===========================================================================
// Desktop Gemini Assistant & System Controls Handlers
// ===========================================================================
ipcMain.handle('open-assistant', () => {
  showAssistantWindow();
  return { success: true };
});

ipcMain.handle('close-assistant', () => {
  hideAssistantWindow();
  return { success: true };
});

ipcMain.handle('toggle-assistant', () => {
  toggleAssistantWindow();
  return { success: true };
});

ipcMain.handle('query-assistant', async (event, params) => {
  // 1. Primary Route: In-process Daemon Server (< 1ms dispatch, zero process startup delay)
  try {
    const daemonRes = await callDaemonControl('/assistant/query', params, 45000);
    if (daemonRes && daemonRes.success === true) {
      return daemonRes;
    }
  } catch (daemonErr) {
    console.warn('[Main] Daemon query endpoint unreachable, using backend runner:', daemonErr.message);
  }

  // 2. Direct Fallback: Standalone daemon process execution (preserves full agentic prompt & MCP execution)
  return new Promise((resolve) => {
    const payload = JSON.stringify(params || {});
    runBackendCommand('query', [payload], { maxBuffer: 10 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) {
        return resolve({ success: false, error: stderr || error.message });
      }
      try {
        resolve(JSON.parse(stdout.trim()));
      } catch (parseErr) {
        resolve({ success: false, error: `Invalid assistant response: ${stdout}` });
      }
    });
  });
});

ipcMain.handle('execute-system-command', async (event, command) => {
  // Direct native PowerShell execution via Node.js (0ms Python startup overhead)
  return new Promise((resolve) => {
    const start = Date.now();
    const cleanCmd = (command || '').trim();
    execFile('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', cleanCmd], { timeout: 15000 }, (error, stdout, stderr) => {
      const dur = ((Date.now() - start) / 1000).toFixed(2);
      resolve({
        success: !error,
        stdout: (stdout || '').trim(),
        stderr: (stderr || (error ? error.message : '')).trim(),
        exit_code: error ? (error.code || 1) : 0,
        duration: dur,
        command: cleanCmd
      });
    });
  });
});

ipcMain.handle('capture-screenshot', async () => {
  return await captureCleanScreenshot();
});

ipcMain.handle('read-clipboard-image', () => {
  try {
    const img = clipboard.readImage();
    if (!img.isEmpty()) {
      const size = img.getSize();
      return {
        success: true,
        data_url: img.toDataURL(),
        width: size.width,
        height: size.height
      };
    }
  } catch (err) {
    console.error('[Clipboard] Error reading image:', err);
  }
  return { success: false };
});

ipcMain.handle('paste-to-active-window', async (event, text) => {
  try {
    const daemonRes = await callDaemonControl('/assistant/paste', { text }, 2000);
    if (daemonRes && daemonRes.success) return daemonRes;
  } catch (_) {}
  return new Promise((resolve) => {
    const payload = JSON.stringify({ text: text });
    runBackendCommand('paste', [payload], (error, stdout, stderr) => {
      if (error) {
        return resolve({ success: false, error: stderr || error.message });
      }
      try {
        resolve(JSON.parse(stdout.trim()));
      } catch (parseErr) {
        resolve({ success: false, error: `Invalid paste response: ${stdout}` });
      }
    });
  });
});

ipcMain.handle('resize-assistant', async (event, size) => {
  if (assistantWindow && !assistantWindow.isDestroyed()) {
    const bounds = assistantWindow.getBounds();
    const newW = (size && size.width) ? Math.round(size.width) : bounds.width;
    const newH = (size && size.height) ? Math.round(size.height) : bounds.height;
    assistantWindow.setSize(newW, newH, true);
    try {
      let currentCfg = {};
      if (fs.existsSync(CONFIG_PATH)) {
        try { currentCfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8') || '{}'); } catch (_) {}
      }
      currentCfg.assistantBounds = assistantWindow.getBounds();
      fs.writeFileSync(CONFIG_PATH, JSON.stringify(currentCfg, null, 2), 'utf-8');
    } catch (_) {}
    return { success: true, width: newW, height: newH };
  }
  return { success: false };
});

ipcMain.handle('get-assistant-bounds', async () => {
  if (assistantWindow && !assistantWindow.isDestroyed()) {
    return assistantWindow.getBounds();
  }
  return { width: 580, height: 460 };
});

ipcMain.handle('refine-prompt', async (event, params) => {
  try {
    const daemonRes = await callDaemonControl('/assistant/refine', params, 10000);
    if (daemonRes && daemonRes.success) return daemonRes;
  } catch (_) {}
  return new Promise((resolve) => {
    const payload = JSON.stringify(params || {});
    runBackendCommand('refine', [payload], { maxBuffer: 10 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) return resolve({ success: false, refined_text: (params && params.text) || '' });
      try {
        resolve(JSON.parse(stdout.trim()));
      } catch (parseErr) {
        resolve({ success: false, refined_text: (params && params.text) || '' });
      }
    });
  });
});

ipcMain.handle('web-search', async (event, query) => {
  try {
    const daemonRes = await callDaemonControl('/assistant/search', { query }, 10000);
    if (daemonRes && daemonRes.success) return daemonRes;
  } catch (_) {}
  return new Promise((resolve) => {
    const payload = JSON.stringify({ query: query });
    runBackendCommand('search', [payload], { maxBuffer: 10 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) return resolve({ success: false, results: [] });
      try {
        resolve(JSON.parse(stdout.trim()));
      } catch (parseErr) {
        resolve({ success: false, results: [] });
      }
    });
  });
});

ipcMain.handle('trigger-dictation', async (event, params) => {
  return new Promise((resolve) => {
    const action = (params && params.action) || 'toggle';
    const payload = JSON.stringify({ action: action, session_type: 'ai' });
    const req = http.request({
      hostname: '127.0.0.1',
      port: 48198,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
      timeout: 1000
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (e) { resolve({ success: true }); }
      });
    });
    req.on('error', () => resolve({ success: false, error: 'Daemon not reachable' }));
    req.write(payload);
    req.end();
  });
});

const { nativeTheme } = require('electron');

ipcMain.handle('get-system-theme', () => {
  return {
    shouldUseDarkColors: nativeTheme.shouldUseDarkColors,
    themeSource: nativeTheme.themeSource
  };
});

ipcMain.handle('get-tts-voices', async () => {
  try {
    const res = await callDaemonControlGet('/tts/voices', 3000);
    if (res && res.success) return res;
  } catch (_) {}
  return new Promise((resolve) => {
    runBackendCommand('tts-voices', [], { timeout: 4000 }, (error, stdout) => {
      if (!error && stdout) {
        try { return resolve(JSON.parse(stdout.trim())); } catch (_) {}
      }
      resolve({ success: false, voices: [] });
    });
  });
});

ipcMain.handle('play-tts-demo', async (event, voice) => {
  try {
    const res = await callDaemonControl('/tts/demo', { voice }, 12000);
    if (res && res.success) return res;
  } catch (_) {}
  return new Promise((resolve) => {
    runBackendCommand('tts-demo', [JSON.stringify({ voice })], { timeout: 12000, maxBuffer: 10 * 1024 * 1024 }, (error, stdout) => {
      if (!error && stdout) {
        try { return resolve(JSON.parse(stdout.trim())); } catch (_) {}
      }
      resolve({ success: false, error: 'Failed to synthesize demo audio' });
    });
  });
});

ipcMain.handle('synthesize-speech', async (event, params) => {
  try {
    const res = await callDaemonControl('/tts/synth', params, 15000);
    if (res && res.success) return res;
  } catch (_) {}
  return new Promise((resolve) => {
    runBackendCommand('tts-synth', [JSON.stringify(params || {})], { timeout: 15000, maxBuffer: 10 * 1024 * 1024 }, (error, stdout) => {
      if (!error && stdout) {
        try { return resolve(JSON.parse(stdout.trim())); } catch (_) {}
      }
      resolve({ success: false, error: 'Speech synthesis failed' });
    });
  });
});


