const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { execFile } = require('child_process');

process.on('uncaughtException', (err) => {
  fs.writeFileSync(path.join(__dirname, 'crash.log'), String(err.stack || err));
});

// Hardware acceleration & performance switches
app.commandLine.appendSwitch('enable-gpu-rasterization');
app.commandLine.appendSwitch('enable-zero-copy');

const BASE_DIR = path.join(__dirname, '..');
const APPDATA_DIR = process.env.APPDATA;
const DATA_DIR = APPDATA_DIR ? path.join(APPDATA_DIR, 'AntigravityFlow') : path.join(BASE_DIR, 'data');
const RECORDINGS_DIR = path.join(DATA_DIR, 'recordings');
const HISTORY_PATH = path.join(DATA_DIR, 'history.json');
const CONFIG_PATH = path.join(DATA_DIR, 'config.json');
const VOCAB_PATH = path.join(DATA_DIR, 'vocabulary.json');
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

let mainWindow = null;

// Single Instance Lock: Prevents spawning duplicate processes, focuses existing in 0ms!
const gotTheLock = app.requestSingleInstanceLock();

if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    // Tells Windows Shell to use our black and gold emblem for the taskbar icon
    app.setAppUserModelId('com.antigravityflow.app');

    // If running from packaged installer, spawn bundled background daemon
    if (app.isPackaged) {
      const embeddedDaemon = path.join(process.resourcesPath, 'bin', 'AntigravityFlowDaemon.exe');
      if (fs.existsSync(embeddedDaemon)) {
        const { spawn } = require('child_process');
        spawn(embeddedDaemon, [], { detached: true, stdio: 'ignore' }).unref();
      }
    }

    createWindow();

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
      nodeIntegration: false
    }
  });

  mainWindow.loadFile('index.html');

  const isHidden = process.argv.includes('--hidden');
  mainWindow.once('ready-to-show', () => {
    if (!isHidden) {
      mainWindow.show();
    }
  });
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

ipcMain.handle('get-config', async () => {
  try {
    if (!fs.existsSync(CONFIG_PATH)) {
      return { success: true, data: { hotkey: 'ctrl+shift+s', enable_sound: true, user_name: 'Copy' } };
    }
    const raw = fs.readFileSync(CONFIG_PATH, 'utf-8');
    return { success: true, data: JSON.parse(raw) };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('save-config', async (event, config) => {
  try {
    if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2), 'utf-8');

    if (config.start_on_login !== undefined) {
      try {
        app.setLoginItemSettings({
          openAtLogin: Boolean(config.start_on_login),
          args: ['--hidden']
        });
      } catch (_) {}
    }

    return { success: true };
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
    if (!fs.existsSync(VOCAB_PATH)) {
      return { success: true, data: {} };
    }
    const raw = fs.readFileSync(VOCAB_PATH, 'utf-8');
    return { success: true, data: JSON.parse(raw || '{}') };
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
  return new Promise((resolve) => {
    const pythonScript = path.join(BASE_DIR, 'retranscribe.py');
    const pythonExe = 'C:\\Program Files\\Python314\\python.exe';
    
    execFile(pythonExe, [pythonScript, String(id)], (error, stdout, stderr) => {
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
