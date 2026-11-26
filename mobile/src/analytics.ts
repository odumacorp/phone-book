/**
 * PostHog product analytics for the app and the website, anonymously and without extra packages.
 *
 * Off unless the server turns it on (/api/config "analytics"). People are only ever an anonymous ID from the server
 * ("u_...", made from their account with a server secret): never their phone number, name or email.
 */
import { Platform } from 'react-native';

type Config = { key: string; host: string } | null;

let config: Config = null;
let distinctId = `anon_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
let identified = false;
const platform = Platform.OS === 'web' ? 'web' : Platform.OS; // web | android | ios

export function initAnalytics(c: Config | undefined) {
  config = c && c.key ? c : null;
}

/** Send one event. Properties are kinds and counts, never contact details. */
export function track(event: string, properties: Record<string, string | number | boolean | null> = {}) {
  if (!config) return;
  const body = {
    api_key: config.key,
    event,
    distinct_id: distinctId,
    properties: { ...properties, platform, source: platform === 'web' ? 'website' : 'app', $geoip_disable: true },
  };
  fetch(`${config.host}/i/v0/e/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).catch(
    () => {},
  );
}

/** After login: link what this device did before logging in to the person's anonymous ID. */
export function identify(analyticsId: string | null | undefined) {
  if (!analyticsId || (identified && distinctId === analyticsId)) return;
  const before = distinctId;
  distinctId = analyticsId;
  identified = true;
  track('$identify', { $anon_distinct_id: before });
}

/** After logout: a fresh anonymous ID, so the next person isn't counted as the last one. */
export function resetAnalytics() {
  distinctId = `anon_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  identified = false;
}

/** A screen (app) or page (website) was opened. */
export function screen(name: string) {
  if (platform === 'web') {
    const url = typeof location !== 'undefined' ? `${location.origin}/#${name}` : name;
    track('$pageview', { $current_url: url, $pathname: `/${name}`, $screen_name: name });
  } else {
    track('$screen', { $screen_name: name });
  }
}
