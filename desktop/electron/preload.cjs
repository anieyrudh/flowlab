"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("flowlabDesktop", Object.freeze({
  platform: process.platform,
  saveFiles(files) {
    return ipcRenderer.invoke("flowlab:save-files", files);
  }
}));
