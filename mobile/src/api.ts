import * as SecureStore from 'expo-secure-store';

export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'https://phone-book.odumacorp.com';

const TOKEN_KEY = 'phonebook.token';

export type SyncContact = { name: string; phone: string };
export type SyncResult = { added: number; skipped: number; total: number };
export type Me = { phone: string; contacts: number };

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

export async function login(phone: string, pin: string): Promise<string> {
  const { token } = await request<{ token: string }>('/api/login', {
    method: 'POST',
    body: JSON.stringify({ phone, pin }),
  });
  await SecureStore.setItemAsync(TOKEN_KEY, token);
  return token;
}

export const getMe = (token: string) => request<Me>('/api/me', {}, token);

export const syncContacts = (token: string, contacts: SyncContact[]) =>
  request<SyncResult>('/api/contacts/sync', { method: 'POST', body: JSON.stringify({ contacts }) }, token);
