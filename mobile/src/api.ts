import { defaultApiUrl, deviceName, tokenStore } from './platform';

export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? defaultApiUrl;

const TOKEN_KEY = 'phonebook.token';

const DEVICE_NAME = deviceName();

export type SyncContact = { name: string; phone: string };
export type SyncResult = { added: number; skipped: number; total: number };
export type Me = {
  phone: string;
  contacts: number;
  show_others: boolean;
  sms_all?: boolean; // what USSD "SMS me my contacts" sends: all contacts, or only priority ones
  name: string | null;
  email: string | null; // the first email (older servers)
  emails?: EmailAddress[];
  max_emails?: number;
  features?: { email_verification?: boolean }; // switched on by the admin
  unread_notifications?: number;
  ussd_code?: string; // the USSD code users dial
  organisations?: number; // organisations this person belongs to
  platform_access?: boolean; // can manage an organisation at /platform
  memberships?: Membership[]; // organisations, with role, title and departments
  last_login?: { at: number; how: 'app' | 'USSD'; where: string } | null; // the login before this one
};
/** The user's place in one organisation, for the profile card. */
export type Membership = {
  id: number;
  name: string;
  roles: string[];
  title: string | null;
  member_no: string | null;
  groups: string[];
  joined_at: number | null;
};
/** An email on the account (up to max_emails). Any of them can be used to log in. */
export type EmailAddress = { id: number; email: string; verified: boolean; notify?: boolean };
export type Contact = { id: number; name: string; phone: string; priority: boolean };
export type ContactList = { contacts: Contact[]; max_priority: number; show_others: boolean; sms_all?: boolean };
export type UssdLogin = {
  at: number; // unix seconds
  phone: string; // the phone that dialled
  own_phone: boolean;
  network: string;
  success: boolean;
  reason: 'ok' | 'wrong_pin' | 'locked' | 'resumed';
};

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}, token?: string | null): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API_URL + path, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        'X-Device-Name': DEVICE_NAME,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
  } catch {
    throw new ApiError('Could not reach the server. Check your internet connection.', 0);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(body.error ?? `Server error (${res.status}).`, res.status);
  }
  return body as T;
}

export const loadToken = () => tokenStore.get(TOKEN_KEY);
export const clearToken = () => tokenStore.remove(TOKEN_KEY);

/** Same rule as the server: +254XXXXXXXXX or 0XXXXXXXXX (spaces and dashes are ignored). */
export const PHONE_FORMAT = '+254XXXXXXXXX or 0XXXXXXXXX';
export const isValidPhone = (phone: string) => /^(\+254\d{9}|0\d{9})$/.test(phone.replace(/[\s\-().]/g, ''));

async function saveToken(path: string, body: object, token?: string): Promise<string> {
  const res = await request<{ token: string }>(path, { method: 'POST', body: JSON.stringify(body) }, token);
  await tokenStore.set(TOKEN_KEY, res.token);
  return res.token;
}

/** A login that needs the SMS code (only when OTP is switched on by the platform). */
export type OtpChallenge = { otp_required: true; ticket: string; phone_hint: string };

/** Log in with an email address or a phone number, plus the password chosen at registration.
 * Returns the token, or an OtpChallenge to finish with loginOtp(). */
export async function login(login: string, password: string): Promise<string | OtpChallenge> {
  const res = await request<{ token?: string } & Partial<OtpChallenge>>('/api/login', {
    method: 'POST',
    body: JSON.stringify({ login, password }),
  });
  if (res.otp_required && res.ticket) return { otp_required: true, ticket: res.ticket, phone_hint: res.phone_hint ?? '' };
  await tokenStore.set(TOKEN_KEY, res.token!);
  return res.token!;
}

/** Login step 2 when OTP is on: the 6-digit code texted to the account's phone. */
export const loginOtp = (ticket: string, code: string) => saveToken('/api/login/otp', { ticket, code });

let ussdCodeValue = '*384*88041#';
/** The USSD code users dial (set on the server; loaded at startup by loadConfig). */
export const ussdCode = () => ussdCodeValue;
/** Public settings from the server. Offline or older servers: keep the defaults. */
export async function loadConfig() {
  try {
    const config = await request<{ ussd_code?: string }>('/api/config');
    if (config.ussd_code) ussdCodeValue = config.ussd_code;
  } catch {
    // keep the default
  }
}

export type Registration = {
  phone: string;
  code: string;
  name: string;
  email: string;
  password: string;
  pin: string;
  org_id?: number; // joining an organisation from the list...
  org_code?: string; // ...or by its code; neither = an individual account
};
/** What happened with the organisation chosen at sign-up. */
export type JoinResult = { name: string; status: 'member' | 'pending' | 'already' };
/** An organisation people can choose at sign-up. */
export type PublicOrg = { id: number; name: string; kind: string };

/** Organisations listed for sign-up (invite-only ones aren't listed: they're joined with their code). */
export const getPublicOrgs = () => request<{ orgs: PublicOrg[] }>('/api/orgs/public');

/** Register, step 1: checks the phone (and email, if given) and texts a 6-digit code to the phone. */
export const requestRegistration = (phone: string, email: string) =>
  request<{ sent: boolean }>('/api/register/request', { method: 'POST', body: JSON.stringify({ phone, email }) });

/** Register, step 2: the code plus name, optional email, app password and USSD PIN. */
export async function register(r: Registration): Promise<{ token: string; organisation?: JoinResult }> {
  const res = await request<{ token: string; organisation?: JoinResult }>('/api/register/confirm', {
    method: 'POST',
    body: JSON.stringify(r),
  });
  await tokenStore.set(TOKEN_KEY, res.token);
  return res;
}

/** Forgot password: a code goes by SMS to the account's phone, found by email or phone number. */
export const requestPasswordReset = (login: string) =>
  request<{ sent: boolean; phone: string }>('/api/password/reset/request', { method: 'POST', body: JSON.stringify({ login }) });

export const resetPassword = (login: string, code: string, newPassword: string) =>
  saveToken('/api/password/reset/confirm', { login, code, new_password: newPassword });

/** Changing the password or PIN signs out other devices; the returned token keeps this one logged in. */
export const changePassword = (token: string, currentPassword: string, newPassword: string) =>
  saveToken('/api/password/change', { current_password: currentPassword, new_password: newPassword }, token);

/** New USSD PIN, confirmed with the app password (works even if the old PIN is forgotten). */
export const changePin = (token: string, password: string, newPin: string) =>
  saveToken('/api/pin/change', { password, new_pin: newPin }, token);

export const PASSWORD_MIN = 8;

export const getMe = (token: string) => request<Me>('/api/me', {}, token);

/** USSD login attempts on this account, newest first (kept for `keep_days`). */
/** Times are unix seconds; logged_out is null while the session is active. */
type SessionTimes = {
  logged_in: number;
  logged_out: number | null;
  duration_seconds: number;
  active: boolean;
  last_active: number;
  end_reason: string | null;
};
export type AppSession = SessionTimes & { id: string; device: string; current: boolean };
export type UssdSession = SessionTimes & {
  id: number;
  phone: string; // the phone that dialled
  own_phone: boolean;
  network: string;
  expires_at: number | null; // when an active session ends if left idle
};
export type Sessions = { app: AppSession[]; ussd: UssdSession[]; ussd_timeout_seconds: number; history_days: number };

/** App devices and USSD logins: active ones first, then ended ones from the last `history_days`. */
export const getSessions = (token: string) => request<Sessions>('/api/sessions', {}, token);

export const logoutAppSession = (token: string, id: string) =>
  request<{ current: boolean }>(`/api/sessions/app/${encodeURIComponent(id)}/logout`, { method: 'POST' }, token);

/** Ends one USSD login: if it's open, it stops on its next screen, and the next dial needs a full login. */
export const logoutUssdSession = (token: string, id: number) =>
  request<{ logged_out: number }>(`/api/sessions/ussd/${id}/logout`, { method: 'POST' }, token);

/** Revokes this device's login on the server, or every app login plus USSD when `everywhere`. */
export const logoutApp = (token: string, everywhere = false) =>
  request<{ logged_out: string }>('/api/logout', { method: 'POST', body: JSON.stringify({ everywhere }) }, token);

export const getLogins = (token: string) => request<{ logins: UssdLogin[]; keep_days: number }>('/api/logins', {}, token);

export const getContacts = (token: string) => request<ContactList>('/api/contacts', {}, token);

export const setPriority = (token: string, id: number, priority: boolean) =>
  request<{ priority: boolean }>(`/api/contacts/${id}/priority`, { method: 'POST', body: JSON.stringify({ priority }) }, token);

/** The name that signs the SMS the user sends from USSD. */
export const setName = (token: string, name: string) =>
  request<{ name: string }>('/api/settings', { method: 'POST', body: JSON.stringify({ name }) }, token);

/** Whether non-priority contacts appear on USSD (*384*88041#). */
export const setShowOthers = (token: string, showOthers: boolean) =>
  request<{ show_others: boolean }>('/api/settings', { method: 'POST', body: JSON.stringify({ show_others: showOthers }) }, token);

/** Whether USSD "SMS me my contacts" texts all contacts (true) or only the priority ones (false). */
export const setSmsAll = (token: string, smsAll: boolean) =>
  request<{ sms_all: boolean }>('/api/settings', { method: 'POST', body: JSON.stringify({ sms_all: smsAll }) }, token);

export type EmergencyNumber = { number: string; label: string; saved: boolean };

/** Free emergency and help lines; `saved` says whether each is already in the contacts. */
export const getEmergencyNumbers = (token: string) =>
  request<{ numbers: EmergencyNumber[] }>('/api/emergency', {}, token);

export const addContact = (token: string, name: string, phone: string) =>
  request<Contact>('/api/contacts', { method: 'POST', body: JSON.stringify({ name, phone }) }, token);

export const deleteContacts = (token: string, ids: number[]) =>
  request<{ deleted: number[] }>('/api/contacts/delete', { method: 'POST', body: JSON.stringify({ ids }) }, token);

export const editContact = (token: string, id: number, name: string, phone: string) =>
  request<Contact>(`/api/contacts/${id}`, { method: 'PATCH', body: JSON.stringify({ name, phone }) }, token);

export const deleteContact = (token: string, id: number) =>
  request<{ deleted: number }>(`/api/contacts/${id}`, { method: 'DELETE' }, token);

/** Same rule as the server, so the app can tell which phone contacts are already in the phone book.
 * Short service numbers (999, 1199...) are kept as they are. */
export function normalizePhone(raw: string): string | null {
  const phone = raw.replace(/[\s\-().]/g, '');
  if (/^\d{3,6}$/.test(phone)) return phone;
  if (!/^\+?\d{9,15}$/.test(phone)) return null;
  if (phone.startsWith('+')) return phone;
  if (phone.startsWith('0')) return '+254' + phone.slice(1);
  return '+' + phone;
}

export const addEmail = (token: string, email: string) =>
  request<EmailAddress>('/api/emails', { method: 'POST', body: JSON.stringify({ email }) }, token);

export const deleteEmail = (token: string, id: number) =>
  request<{ deleted: number }>(`/api/emails/${id}`, { method: 'DELETE' }, token);

/** Email verification (only when the admin has turned it on): a 6-digit code goes to that email. */
export const requestEmailCode = (token: string, id: number) =>
  request<{ sent: boolean; email: string }>('/api/email/verify/request', { method: 'POST', body: JSON.stringify({ id }) }, token);

export const confirmEmailCode = (token: string, id: number, code: string) =>
  request<EmailAddress>('/api/email/verify/confirm', { method: 'POST', body: JSON.stringify({ id, code }) }, token);

export type AppNotification = {
  id: number;
  kind: 'security' | 'messages';
  title: string;
  body: string;
  created_at: number;
  read: boolean;
};
/** Where notifications go: the in-app inbox and/or each email; and which kinds. */
export type NotificationPrefs = {
  inapp: boolean;
  security: boolean;
  messages: boolean;
  emails: EmailAddress[];
  email_available: boolean; // the server can send email
  verified_only: boolean; // only verified emails get them
};
export type Inbox = { notifications: AppNotification[]; unread: number; prefs: NotificationPrefs; keep_days: number };

export const getNotifications = (token: string) => request<Inbox>('/api/notifications', {}, token);

export const markNotificationsRead = (token: string) =>
  request<{ unread: number }>('/api/notifications/read', { method: 'POST' }, token);

export const clearNotifications = (token: string) =>
  request<{ cleared: boolean }>('/api/notifications/clear', { method: 'POST' }, token);

export type PrefsChange =
  | { inapp?: boolean; security?: boolean; messages?: boolean }
  | { all: boolean }
  | { email_id: number; notify: boolean };

export const setNotificationPrefs = (token: string, change: PrefsChange) =>
  request<NotificationPrefs>('/api/notifications/prefs', { method: 'POST', body: JSON.stringify(change) }, token);

export type Complaint = {
  id: number;
  message: string;
  status: 'open' | 'resolved';
  reply: string | null;
  created_at: number;
  updated_at: number;
};

/** Report a problem. Without a login, `contact` (phone or email) says where to reply. */
export const sendComplaint = (message: string, contact: string, token?: string | null) =>
  request<{ sent: boolean }>('/api/complaints', { method: 'POST', body: JSON.stringify({ message, contact }) }, token);

/** The user's own reports, newest first, with the admin's reply. */
export const getComplaints = (token: string) => request<{ complaints: Complaint[] }>('/api/complaints', {}, token);

export const syncContacts = (token: string, contacts: SyncContact[]) =>
  request<SyncResult>('/api/contacts/sync', { method: 'POST', body: JSON.stringify({ contacts }) }, token);

/** An organisation the user belongs to (school, SACCO, employer...). */
export type OrgSummary = { id: number; name: string; notices: number; urgent: number };
export type OrgContact = { id: number; name: string; phone: string; title: string | null; group: string | null };
export type OrgNotice = { id: number; title: string; body: string; urgent: boolean; pinned: boolean; at: number };
export type OrgDetail = {
  org: { id: number; name: string };
  notices: OrgNotice[];
  contacts: OrgContact[];
  emergency: OrgContact[];
};

/** The user's organisations, with how many notices each has for them. */
export const getOrgs = (token: string) => request<{ orgs: OrgSummary[] }>('/api/orgs', {}, token);

/** What an organisation shares with this user: notices, contacts and emergency contacts. */
export const getOrg = (token: string, id: number) => request<OrgDetail>(`/api/orgs/${id}`, {}, token);
