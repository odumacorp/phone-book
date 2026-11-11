import { Contact, ContactField, getPermissionsAsync, requestPermissionsAsync } from 'expo-contacts';
import { Linking } from 'react-native';

import type { SyncContact } from './api';

const FIELDS = [ContactField.FULL_NAME, ContactField.PHONES] as const;

/** The phone app reads the phonebook directly (the website version may import a file instead). */
export const contactSource: 'phone' | 'file' = 'phone';

/** Reads every contact with a phone number; one entry per number. Returns null if permission is denied. */
export async function readDeviceContacts(): Promise<SyncContact[] | null> {
  const { granted } = await requestPermissionsAsync();
  if (!granted) return null;

  const details = await Contact.getAllDetails(FIELDS);
  const contacts: SyncContact[] = [];
  for (const contact of details) {
    const name = contact.fullName?.trim();
    if (!name) continue;
    for (const phone of contact.phones ?? []) {
      if (phone.number) contacts.push({ name, phone: phone.number });
    }
  }
  return contacts;
}

/** How much of the phonebook the app may read: everything, only contacts the user chose (iPhone), or nothing yet. */
export type ContactAccess = 'all' | 'limited' | 'denied' | 'unasked';

export async function getContactAccess(): Promise<ContactAccess | null> {
  const { granted, canAskAgain, accessPrivileges } = await getPermissionsAsync();
  if (granted) return accessPrivileges === 'limited' ? 'limited' : 'all';
  return canAskAgain ? 'unasked' : 'denied';
}

/** Asks for access the first time; after that only the phone's Settings can change it. */
export const askContactAccess = () => requestPermissionsAsync();

/** Opens this app's page in the phone's Settings, where contacts access is changed. */
export const openPhoneSettings = () => Linking.openSettings();

/** iPhone with "selected contacts" access: lets the user change which contacts the app can see. */
export const chooseSharedContacts = () => Contact.presentAccessPicker();
