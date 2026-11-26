const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  // Window controls
  minimize: () => ipcRenderer.invoke('window-minimize'),
  maximize: () => ipcRenderer.invoke('window-maximize'),
  close: () => ipcRenderer.invoke('window-close'),
  quitApp: () => ipcRenderer.invoke('app-quit'),

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
  saveVocabulary: (vocab) => ipcRenderer.invoke('save-vocabulary', vocab),

  // Quota & User Allowance Summary
  getQuotaSummary: () => ipcRenderer.invoke('get-quota-summary'),
  getUserCache: () => ipcRenderer.invoke('get-user-cache'),
  getAvailableModels: () => ipcRenderer.invoke('get-available-models'),
  getDaemonStatus: () => ipcRenderer.invoke('get-daemon-status'),

  // Speech Macros & Snippets Engine
  getMacros: () => ipcRenderer.invoke('get-macros'),
  saveMacro: (macro) => ipcRenderer.invoke('save-macro', macro),
  deleteMacro: (id) => ipcRenderer.invoke('delete-macro', id),
  expandMacro: (text) => ipcRenderer.invoke('expand-macro', text),

  // Desktop Gemini Assistant & System Controls
  openAssistant: () => ipcRenderer.invoke('open-assistant'),
  closeAssistant: () => ipcRenderer.invoke('close-assistant'),
  toggleAssistant: () => ipcRenderer.invoke('toggle-assistant'),
  resizeAssistant: (size) => ipcRenderer.invoke('resize-assistant', size),
  getAssistantBounds: () => ipcRenderer.invoke('get-assistant-bounds'),
  refinePrompt: (params) => ipcRenderer.invoke('refine-prompt', params),
  webSearch: (query) => ipcRenderer.invoke('web-search', query),
  triggerDictation: (params) => ipcRenderer.invoke('trigger-dictation', params),
  queryAssistant: (params) => ipcRenderer.invoke('query-assistant', params),
  executeSystemCommand: (command) => ipcRenderer.invoke('execute-system-command', command),
  captureScreenshot: () => ipcRenderer.invoke('capture-screenshot'),
  readClipboardImage: () => ipcRenderer.invoke('read-clipboard-image'),
  pasteToActiveWindow: (text) => ipcRenderer.invoke('paste-to-active-window', text),
  // Edge TTS Online Speech Engine
  getTtsVoices: () => ipcRenderer.invoke('get-tts-voices'),
  playTtsDemo: (payload) => ipcRenderer.invoke('play-tts-demo', payload),
  synthesizeSpeech: (params) => ipcRenderer.invoke('synthesize-speech', params),
  getSystemTheme: () => ipcRenderer.invoke('get-system-theme'),

  // Event Listeners
  onAssistantVoiceQuery: (callback) => ipcRenderer.on('assistant-voice-query', (_, text) => callback(text)),
  onAssistantVoiceState: (callback) => ipcRenderer.on('assistant-voice-state', (_, state) => callback(state)),
  onAssistantFocusPrompt: (callback) => ipcRenderer.on('assistant-focus-prompt', (_, data) => callback(data)),
  onConfigUpdated: (callback) => ipcRenderer.on('config-updated', (_, cfg) => callback(cfg))
});
