import * as Device from 'expo-device';
import * as SecureStore from 'expo-secure-store';

/** Phone app: the login token lives in the device keychain/keystore. (The website uses platform.web.ts.) */
export const tokenStore = {
  get: (key: string) => SecureStore.getItemAsync(key),
  set: (key: string, value: string) => SecureStore.setItemAsync(key, value),
  remove: (key: string) => SecureStore.deleteItemAsync(key),
};

/** Shown in the Sessions list. The model ("iPhone 14"), not the phone's own name, which is often personal. */
export const deviceName = () => Device.modelName ?? Device.osName ?? 'Unknown device';

/** The phone app talks to the hosted server. */
export const defaultApiUrl = 'https://phone-book.odumacorp.com';

export const isWeb = false;
