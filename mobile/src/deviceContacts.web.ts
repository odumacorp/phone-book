import type { SyncContact } from './api';

/**
 * Website version of reading the phone's contacts. Browsers can't read the phonebook directly:
 * - Android Chrome offers the Contact Picker, where the user chooses contacts from their phonebook.
 * - Elsewhere (iPhone Safari, computers) the user picks a contacts file (.vcf), which phones can
 *   export from their Contacts app.
 * Must be called straight from a tap (browsers only open pickers in response to one).
 * Returns null if the user cancels.
 */

type PickerContact = { name?: string[]; tel?: string[] };
type ContactsManager = { select(props: string[], options: { multiple: boolean }): Promise<PickerContact[]> };

const picker = (): ContactsManager | undefined =>
  typeof navigator !== 'undefined' && 'contacts' in navigator ? (navigator as unknown as { contacts: ContactsManager }).contacts : undefined;

/** "phone": the browser can open the phonebook; "file": the user imports a .vcf file. */
export const contactSource: 'phone' | 'file' = picker() ? 'phone' : 'file';

export async function readDeviceContacts(): Promise<SyncContact[] | null> {
  const contacts = picker();
  if (contacts) {
    try {
      const chosen = await contacts.select(['name', 'tel'], { multiple: true });
      const list: SyncContact[] = [];
      for (const c of chosen) {
        const name = c.name?.[0]?.trim();
        for (const phone of c.tel ?? []) if (name && phone) list.push({ name, phone });
      }
      return list;
    } catch {
      return null; // closed the picker
    }
  }
  const text = await pickFile();
  return text === null ? null : parseVCards(text);
}

function pickFile(): Promise<string | null> {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.vcf,text/vcard,text/x-vcard';
    input.style.display = 'none';
    input.onchange = async () => {
      const file = input.files?.[0];
      input.remove();
      resolve(file ? await file.text() : null);
    };
    input.oncancel = () => {
      input.remove();
      resolve(null);
    };
    document.body.appendChild(input);
    input.click();
  });
}

/** Names and numbers from a vCard file (one entry per number), as exported by phones and Google Contacts. */
export function parseVCards(text: string): SyncContact[] {
  // Unfold continuation lines (a line starting with a space or tab continues the previous one)
  const lines = text.replace(/\r\n/g, '\n').replace(/\n[ \t]/g, '').split('\n');
  const list: SyncContact[] = [];
  let name = '';
  let structured = '';
  let phones: string[] = [];
  for (const line of lines) {
    const colon = line.indexOf(':');
    if (colon < 0) continue;
    const key = line.slice(0, colon).toUpperCase();
    const value = line.slice(colon + 1).trim();
    if (key === 'BEGIN' && value.toUpperCase() === 'VCARD') {
      name = structured = '';
      phones = [];
    } else if (key === 'FN' || key.startsWith('FN;')) {
      name = unescape(value);
    } else if (key === 'N' || key.startsWith('N;')) {
      const [family, given] = value.split(';');
      structured = [given, family].filter(Boolean).map(unescape).join(' ');
    } else if (key === 'TEL' || key.startsWith('TEL;') || /^ITEM\d+\.TEL/.test(key)) {
      phones.push(value.replace(/^tel:/i, ''));
    } else if (key === 'END' && value.toUpperCase() === 'VCARD') {
      const label = (name || structured).trim();
      if (label) for (const phone of phones) list.push({ name: label, phone });
    }
  }
  return list;
}

const unescape = (v: string) => v.replace(/\\,/g, ',').replace(/\\;/g, ';').replace(/\\n/gi, ' ').replace(/\\\\/g, '\\');
