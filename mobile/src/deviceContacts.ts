import { Contact, ContactField, requestPermissionsAsync } from 'expo-contacts';

import type { SyncContact } from './api';

const FIELDS = [ContactField.FULL_NAME, ContactField.PHONES] as const;

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
