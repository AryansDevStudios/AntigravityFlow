const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  // Window controls
  minimize: () => ipcRenderer.invoke('window-minimize'),
  maximize: () => ipcRenderer.invoke('window-maximize'),
  close: () => ipcRenderer.invoke('window-close'),

  // History & Config
  getHistory: () => ipcRenderer.invoke('get-history'),
  deleteHistoryItem: (id) => ipcRenderer.invoke('delete-history-item', id),
  clearHistory: () => ipcRenderer.invoke('clear-history'),
  getConfig: () => ipcRenderer.invoke('get-config'),
  saveConfig: (cfg) => ipcRenderer.invoke('save-config', cfg),
  setKeyRecordingActive: (active) => ipcRenderer.invoke('set-key-recording-active', active),

  // Audio & Transcription
  getAudioUrl: (id) => ipcRenderer.invoke('get-audio-url', id),
  extractAudio: (id) => ipcRenderer.invoke('extract-audio', id),
  retryTranscription: (id) => ipcRenderer.invoke('retry-transcription', id),

  // Vocabulary & Technical Acronyms
  getVocabulary: () => ipcRenderer.invoke('get-vocabulary'),
  saveVocabulary: (vocab) => ipcRenderer.invoke('save-vocabulary', vocab)
});
