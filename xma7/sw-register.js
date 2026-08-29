if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('./sw.js').catch((error) => {
      console.warn('XMA7 service worker registration failed:', error);
    });
  });
}
