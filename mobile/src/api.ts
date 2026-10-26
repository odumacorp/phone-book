import * as SecureStore from 'expo-secure-store';

export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'https://phone-book.odumacorp.com';

const TOKEN_KEY = 'phonebook.token';

export type SyncContact = { name: string; phone: string };
export type SyncResult = { added: number; skipped: number; total: number };
export type Me = { phone: string; contacts: number; show_others: boolean; name: string | null; email: string | null };
export type Contact = { id: number; name: string; phone: string; priority: boolean };
export type ContactList = { contacts: Contact[]; max_priority: number; show_others: boolean };
export type UssdLogin = {
  at: number; // unix seconds
  phone: string; // the phone that dialled
  own_phone: boolean;
  network: string;
  success: boolean;
  reason: 'ok' | 'wrong_pin' | 'locked';
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

export const loadToken = () => SecureStore.getItemAsync(TOKEN_KEY);
export const clearToken = () => SecureStore.deleteItemAsync(TOKEN_KEY);

/** Same rule as the server: +254XXXXXXXXX or 0XXXXXXXXX (spaces and dashes are ignored). */
export const PHONE_FORMAT = '+254XXXXXXXXX or 0XXXXXXXXX';
export const isValidPhone = (phone: string) => /^(\+254\d{9}|0\d{9})$/.test(phone.replace(/[\s\-().]/g, ''));

async function saveToken(path: string, body: object, token?: string): Promise<string> {
  const res = await request<{ token: string }>(path, { method: 'POST', body: JSON.stringify(body) }, token);
  await SecureStore.setItemAsync(TOKEN_KEY, res.token);
  return res.token;
}

/** Log in with an email address or a phone number, plus the password chosen at registration. */
export const login = (login: string, password: string) => saveToken('/api/login', { login, password });

export type Registration = {
  phone: string;
  code: string;
  name: string;
  email: string;
  password: string;
  pin: string;
};

/** Register, step 1: checks the phone (and email, if given) and texts a 6-digit code to the phone. */
export const requestRegistration = (phone: string, email: string) =>
  request<{ sent: boolean }>('/api/register/request', { method: 'POST', body: JSON.stringify({ phone, email }) });

/** Register, step 2: the code plus name, optional email, app password and USSD PIN. */
export const register = (r: Registration) => saveToken('/api/register/confirm', r);

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

export const syncContacts = (token: string, contacts: SyncContact[]) =>
  request<SyncResult>('/api/contacts/sync', { method: 'POST', body: JSON.stringify({ contacts }) }, token);
