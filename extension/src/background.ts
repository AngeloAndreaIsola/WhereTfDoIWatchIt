/**
 * Background service worker for Manifest V3 extension.
 * Operates without background access to browsing history.
 */

chrome.runtime.onInstalled.addListener(() => {
  // eslint-disable-next-line no-console
  console.log('WhereTfDoIWatchIt companion extension installed.');
});
