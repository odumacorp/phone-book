import * as SecureStore from 'expo-secure-store';

export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'https://phone-book.odumacorp.com';

const TOKEN_KEY = 'phonebook.token';

export type SyncContact = { name: string; phone: string };
export type SyncResult = { added: number; skipped: number; total: number };
export type Me = { phone: string; contacts: number; show_others: boolean };
export type Contact = { id: number; name: string; phone: string; priority: boolean };
export type ContactList = { contacts: Contact[]; max_priority: number; show_others: boolean };

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

/** Same rule as the server: +254XXXXXXXXX or 07XXXXXXXX (spaces and dashes are ignored). */
export const PHONE_FORMAT = '+254XXXXXXXXX or 07XXXXXXXX';
export const isValidPhone = (phone: string) => /^(\+254\d{9}|07\d{8})$/.test(phone.replace(/[\s\-().]/g, ''));

async function saveToken(path: string, body: object, token?: string): Promise<string> {
  const res = await request<{ token: string }>(path, { method: 'POST', body: JSON.stringify(body) }, token);
  await SecureStore.setItemAsync(TOKEN_KEY, res.token);
  return res.token;
}

export const login = (phone: string, pin: string) => saveToken('/api/login', { phone, pin });

/** Changing the PIN signs out other devices; the returned token keeps this one logged in. */
export const changePin = (token: string, currentPin: string, newPin: string) =>
  saveToken('/api/pin/change', { current_pin: currentPin, new_pin: newPin }, token);

/** Register a new number or reset a forgotten PIN: both text a 6-digit code to the number first. */
export type CodeFlow = 'register' | 'reset';
const CODE_PATHS: Record<CodeFlow, string> = { register: '/api/register', reset: '/api/pin/reset' };

export const requestCode = (flow: CodeFlow, phone: string) =>
  request<{ sent: boolean }>(`${CODE_PATHS[flow]}/request`, { method: 'POST', body: JSON.stringify({ phone }) });

export const confirmCode = (flow: CodeFlow, phone: string, code: string, newPin: string) =>
  saveToken(`${CODE_PATHS[flow]}/confirm`, { phone, code, new_pin: newPin });

export const getMe = (token: string) => request<Me>('/api/me', {}, token);

export const getContacts = (token: string) => request<ContactList>('/api/contacts', {}, token);

export const setPriority = (token: string, id: number, priority: boolean) =>
  request<{ priority: boolean }>(`/api/contacts/${id}/priority`, { method: 'POST', body: JSON.stringify({ priority }) }, token);

/** Whether non-priority contacts appear on USSD (*384*88041#). */
export const setShowOthers = (token: string, showOthers: boolean) =>
  request<{ show_others: boolean }>('/api/settings', { method: 'POST', body: JSON.stringify({ show_others: showOthers }) }, token);

export const syncContacts = (token: string, contacts: SyncContact[]) =>
  request<SyncResult>('/api/contacts/sync', { method: 'POST', body: JSON.stringify({ contacts }) }, token);
