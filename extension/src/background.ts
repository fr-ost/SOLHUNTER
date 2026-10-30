// Background service worker: makes the toolbar icon open the side panel.
const enableSidePanel = (): void => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((err: unknown) => {
    console.warn("sidePanel.setPanelBehavior failed", err);
  });
};

chrome.runtime.onInstalled.addListener(enableSidePanel);
chrome.runtime.onStartup.addListener(enableSidePanel);
enableSidePanel();
