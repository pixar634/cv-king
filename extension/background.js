// The toolbar button (or Alt+Shift+J) opens the side panel, which stays open while you move between pages.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
