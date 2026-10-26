/** Website: the login token lives in the browser's local storage. */
export const tokenStore = {
  get: async (key: string) => {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null; // storage blocked (private mode): stay logged out
    }
  },
  set: async (key: string, value: string) => {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // storage blocked: the login only lasts until the page is closed
    }
  },
  remove: async (key: string) => {
    try {
      window.localStorage.removeItem(key);
    } catch {
      // nothing stored
    }
  },
};

/** Shown in the Sessions list, e.g. "Chrome on Android". */
export function deviceName() {
  const ua = navigator.userAgent;
  const browser = /Edg\//.test(ua)
    ? 'Edge'
    : /OPR\/|Opera/.test(ua)
      ? 'Opera'
      : /SamsungBrowser/.test(ua)
        ? 'Samsung Internet'
        : /Firefox\//.test(ua)
          ? 'Firefox'
          : /Chrome\//.test(ua)
            ? 'Chrome'
            : /Safari\//.test(ua)
              ? 'Safari'
              : 'Browser';
  const os = /Android/.test(ua)
    ? 'Android'
    : /iPhone|iPod/.test(ua)
      ? 'iPhone'
      : /iPad|Macintosh/.test(ua) && 'ontouchend' in document
        ? 'iPad'
        : /Windows/.test(ua)
          ? 'Windows'
          : /Mac OS X/.test(ua)
            ? 'Mac'
            : /Linux/.test(ua)
              ? 'Linux'
              : 'web';
  return `${browser} on ${os} (website)`;
}

/** The website is served by the same server as the API, so requests go to the same address. */
export const defaultApiUrl = '';

export const isWeb = true;
