import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';

import {
  ApiError,
  CodeFlow,
  Contact,
  Me,
  PHONE_FORMAT,
  changePin,
  clearToken,
  confirmCode,
  getContacts,
  getMe,
  isValidPhone,
  loadToken,
  login,
  requestCode,
  setName,
  setPriority,
  setShowOthers,
  syncContacts,
} from './src/api';
import { readDeviceContacts } from './src/deviceContacts';

const USSD_CODE = '*384*88041#';
const BATCH_SIZE = 1000;

type View_ = 'login' | 'register' | 'reset' | 'sync' | 'changePin';

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [view, setView] = useState<View_>('login');
  const [notice, setNotice] = useState('');
  const [booting, setBooting] = useState(true);

  useEffect(() => {
    (async () => {
      const saved = await loadToken();
      if (saved) {
        try {
          setMe(await getMe(saved));
          setToken(saved);
          setView('sync');
        } catch (e) {
          if (e instanceof ApiError && e.status === 401) await clearToken();
          else {
            setToken(saved); // offline: keep the session, refresh later
            setView('sync');
          }
        }
      }
      setBooting(false);
    })();
  }, []);

  const loggedIn = async (t: string, message = '') => {
    setToken(t);
    setNotice(message);
    setView('sync');
    setMe(await getMe(t).catch(() => null));
  };

  const logout = async () => {
    await clearToken();
    setToken(null);
    setMe(null);
    setNotice('');
    setView('login');
  };

  let content;
  if (booting) content = <ActivityIndicator size="large" color={COLORS.primary} />;
  else if (view === 'register' || view === 'reset')
    content = (
      <CodeScreen
        key={view}
        flow={view}
        onDone={(t) =>
          loggedIn(t, view === 'register' ? 'Account created. Tap Sync contacts to upload them.' : 'PIN reset. You are logged in.')
        }
        onCancel={() => setView('login')}
      />
    );
  else if (!token)
    content = (
      <LoginScreen onLogin={loggedIn} onRegister={() => setView('register')} onForgot={() => setView('reset')} />
    );
  else if (view === 'changePin')
    content = (
      <ChangePinScreen
        token={token}
        onDone={(t) => loggedIn(t, 'PIN changed.')}
        onCancel={() => setView('sync')}
        onLogout={logout}
      />
    );
  else
    content = (
      <SyncScreen
        token={token}
        me={me}
        setMe={setMe}
        notice={notice}
        onChangePin={() => {
          setNotice('');
          setView('changePin');
        }}
        onLogout={logout}
      />
    );

  return (
    <View style={styles.screen}>
      <StatusBar style="dark" />
      <Text style={styles.title}>Phone Book</Text>
      {content}
    </View>
  );
}

function LoginScreen({
  onLogin,
  onRegister,
  onForgot,
}: {
  onLogin: (token: string) => Promise<void>;
  onRegister: () => void;
  onForgot: () => void;
}) {
  const [phone, setPhone] = useState('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (!isValidPhone(phone)) return setError(`Enter your phone number as ${PHONE_FORMAT}.`);
    setBusy(true);
    setError('');
    try {
      await onLogin(await login(phone, pin));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Login failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.card}>
      <PhoneField value={phone} onChange={setPhone} />
      <PinField label="4-digit PIN" value={pin} onChange={setPin} />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title="Log in" onPress={submit} busy={busy} disabled={!phone || pin.length !== 4} />
      <Link title="Forgot PIN?" onPress={onForgot} disabled={busy} />
      <Link title="New here? Register your number" onPress={onRegister} disabled={busy} />
    </KeyboardAvoidingView>
  );
}

const CODE_SCREEN_TEXT: Record<CodeFlow, { heading: string; intro: string; button: string }> = {
  register: {
    heading: 'Register your number',
    intro: `We will text a 6-digit code to confirm the number is yours. You will then use it with your PIN here and on ${USSD_CODE}.`,
    button: 'Create account',
  },
  reset: { heading: 'Reset your PIN', intro: 'We will text a 6-digit code to your number.', button: 'Reset PIN' },
};

/** Register a new number, or reset a forgotten PIN: phone -> SMS code -> new PIN. */
function CodeScreen({ flow, onDone, onCancel }: { flow: CodeFlow; onDone: (token: string) => void; onCancel: () => void }) {
  const text = CODE_SCREEN_TEXT[flow];
  const [phone, setPhone] = useState('');
  const [name, setNameText] = useState('');
  const [codeSent, setCodeSent] = useState(false);
  const [code, setCode] = useState('');
  const [pin, setPin] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  };

  const sendCode = () => {
    if (flow === 'register' && !name.trim()) return setError('Enter your name.');
    if (!isValidPhone(phone)) return setError(`Enter your phone number as ${PHONE_FORMAT}.`);
    run(async () => {
      await requestCode(flow, phone);
      setCodeSent(true);
    });
  };

  const finish = () => {
    if (pin !== confirm) return setError('PINs do not match.');
    run(async () => onDone(await confirmCode(flow, phone, code, pin, flow === 'register' ? name : undefined)));
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.card}>
      <Text style={styles.heading}>{text.heading}</Text>
      {!codeSent ? (
        <>
          <Text style={styles.hint}>{text.intro}</Text>
          {flow === 'register' ? (
            <>
              <Text style={styles.label}>Your name</Text>
              <TextInput
                style={styles.input}
                value={name}
                onChangeText={setNameText}
                placeholder="e.g. Austin"
                autoComplete="name"
                maxLength={30}
              />
              <Text style={styles.hint}>Added to the end of every SMS you send from {USSD_CODE}.</Text>
            </>
          ) : null}
          <PhoneField value={phone} onChange={setPhone} />
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button
            title="Send code"
            onPress={sendCode}
            busy={busy}
            disabled={!phone || (flow === 'register' && !name.trim())}
          />
        </>
      ) : (
        <>
          <Text style={styles.hint}>
            Enter the code sent to {phone} and choose a {flow === 'register' ? '4-digit' : 'new'} PIN.
          </Text>
          <Text style={styles.label}>6-digit code</Text>
          <TextInput
            style={styles.input}
            value={code}
            onChangeText={(v) => setCode(v.replace(/\D/g, '').slice(0, 6))}
            placeholder="123456"
            keyboardType="number-pad"
            autoComplete="sms-otp"
            textContentType="oneTimeCode"
          />
          <PinField label={flow === 'register' ? 'PIN' : 'New PIN'} value={pin} onChange={setPin} />
          <PinField label={flow === 'register' ? 'Confirm PIN' : 'Confirm new PIN'} value={confirm} onChange={setConfirm} />
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button
            title={text.button}
            onPress={finish}
            busy={busy}
            disabled={code.length !== 6 || pin.length !== 4 || confirm.length !== 4}
          />
          <Link title="Send a new code" onPress={sendCode} disabled={busy} />
        </>
      )}
      <Link title="Back to log in" onPress={onCancel} disabled={busy} />
    </KeyboardAvoidingView>
  );
}

function ChangePinScreen({
  token,
  onDone,
  onCancel,
  onLogout,
}: {
  token: string;
  onDone: (token: string) => void;
  onCancel: () => void;
  onLogout: () => void;
}) {
  const [current, setCurrent] = useState('');
  const [pin, setPin] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (pin !== confirm) return setError('New PINs do not match.');
    setBusy(true);
    setError('');
    try {
      onDone(await changePin(token, current, pin));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401 && e.message.startsWith('Please log in')) return onLogout();
      setError(e instanceof Error ? e.message : 'Could not change PIN.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.card}>
      <Text style={styles.heading}>Change PIN</Text>
      <PinField label="Current PIN" value={current} onChange={setCurrent} />
      <PinField label="New PIN" value={pin} onChange={setPin} />
      <PinField label="Confirm new PIN" value={confirm} onChange={setConfirm} />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button
        title="Change PIN"
        onPress={submit}
        busy={busy}
        disabled={current.length !== 4 || pin.length !== 4 || confirm.length !== 4}
      />
      <Text style={styles.hint}>Your new PIN works on USSD ({USSD_CODE}) too. Other devices will be logged out.</Text>
      <Link title="Cancel" onPress={onCancel} disabled={busy} />
    </KeyboardAvoidingView>
  );
}

/** Home: sync, the USSD visibility switch, and the synced phone book with priority stars. */
function SyncScreen({
  token,
  me,
  setMe,
  notice,
  onChangePin,
  onLogout,
}: {
  token: string;
  me: Me | null;
  setMe: (me: Me) => void;
  notice: string;
  onChangePin: () => void;
  onLogout: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(notice);
  const [error, setError] = useState('');
  const [contacts, setContacts] = useState<Contact[] | null>(null);
  const [maxPriority, setMaxPriority] = useState(5);
  const [showOthers, setShowOthersState] = useState(true);
  const [query, setQuery] = useState('');
  const [name, setNameText] = useState(me?.name ?? '');
  const [savedName, setSavedName] = useState(me?.name ?? '');

  useEffect(() => {
    setNameText(me?.name ?? '');
    setSavedName(me?.name ?? '');
  }, [me?.name]);

  const saveName = async () => {
    setError('');
    try {
      const res = await setName(token, name);
      setNameText(res.name);
      setSavedName(res.name);
      setStatus('Name saved.');
    } catch (e) {
      failed(e, 'Could not save your name.');
    }
  };

  const failed = (e: unknown, fallback: string) => {
    if (e instanceof ApiError && e.status === 401) return onLogout();
    setError(e instanceof Error ? e.message : fallback);
  };

  const loadContacts = useCallback(async () => {
    try {
      const list = await getContacts(token);
      setContacts(list.contacts);
      setMaxPriority(list.max_priority);
      setShowOthersState(list.show_others);
    } catch (e) {
      failed(e, 'Could not load your contacts.');
    }
  }, [token]);

  useEffect(() => {
    loadContacts();
  }, [loadContacts]);

  const sync = async () => {
    setBusy(true);
    setError('');
    setStatus('Reading your contacts…');
    try {
      const device = await readDeviceContacts();
      if (device === null) {
        setStatus('');
        setError('Contacts permission is needed. Allow it in Settings → Apps → Phone Book.');
        return;
      }
      let added = 0;
      for (let i = 0; i < device.length; i += BATCH_SIZE) {
        setStatus(`Uploading ${Math.min(i + BATCH_SIZE, device.length)} of ${device.length}…`);
        added += (await syncContacts(token, device.slice(i, i + BATCH_SIZE))).added;
      }
      setMe(await getMe(token));
      await loadContacts();
      setStatus(added ? `Done. ${added} new contact${added === 1 ? '' : 's'} uploaded.` : 'Done. Everything was already synced.');
    } catch (e) {
      setStatus('');
      failed(e, 'Sync failed.');
    } finally {
      setBusy(false);
    }
  };

  const priorityCount = contacts?.filter((c) => c.priority).length ?? 0;

  const togglePriority = async (contact: Contact) => {
    const want = !contact.priority;
    if (want && priorityCount >= maxPriority) {
      return setError(`You can have up to ${maxPriority} priority contacts. Remove one first.`);
    }
    setError('');
    const update = (priority: boolean) =>
      setContacts((list) => list && list.map((c) => (c.id === contact.id ? { ...c, priority } : c)));
    update(want); // optimistic; undone if the server refuses
    try {
      await setPriority(token, contact.id, want);
    } catch (e) {
      update(!want);
      failed(e, 'Could not update the contact.');
    }
  };

  const toggleShowOthers = async (value: boolean) => {
    setShowOthersState(value);
    setError('');
    try {
      await setShowOthers(token, value);
    } catch (e) {
      setShowOthersState(!value);
      failed(e, 'Could not save the setting.');
    }
  };

  const visible = useMemo(() => {
    const sorted = [...(contacts ?? [])].sort(
      (a, b) => Number(b.priority) - Number(a.priority) || a.name.localeCompare(b.name),
    );
    const q = query.trim().toLowerCase();
    return q ? sorted.filter((c) => c.name.toLowerCase().includes(q) || c.phone.includes(q)) : sorted;
  }, [contacts, query]);

  const header = (
    <>
      <View style={styles.card}>
        <Text style={styles.label}>Logged in as</Text>
        <Text style={styles.value}>{me?.phone ?? '…'}</Text>
        <Text style={styles.label}>Your name (signs your SMS)</Text>
        <View style={styles.nameRow}>
          <TextInput
            style={[styles.input, styles.nameInput]}
            value={name}
            onChangeText={setNameText}
            placeholder="Add your name"
            autoComplete="name"
            maxLength={30}
          />
          {name.trim() && name.trim() !== savedName ? <Link title="Save" onPress={saveName} /> : null}
        </View>
        <Button title="Sync contacts" onPress={sync} busy={busy} />
        {status ? <Text style={styles.status}>{status}</Text> : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <View style={styles.switchRow}>
          <View style={styles.switchText}>
            <Text style={styles.switchLabel}>Show other contacts on USSD</Text>
            <Text style={styles.hint}>
              {showOthers
                ? `${USSD_CODE} shows all your contacts, priority ones first.`
                : `${USSD_CODE} shows only your ${maxPriority} priority contacts.`}
            </Text>
          </View>
          <Switch value={showOthers} onValueChange={toggleShowOthers} trackColor={{ true: COLORS.primary }} />
        </View>
        <View style={styles.linkRow}>
          <Link title="Change PIN" onPress={onChangePin} disabled={busy} />
          <Link title="Log out" onPress={onLogout} disabled={busy} />
        </View>
      </View>
      <Text style={styles.sectionTitle}>
        Your contacts{contacts ? ` (${contacts.length})` : ''} · ★ {priorityCount}/{maxPriority} priority
      </Text>
      <Text style={styles.hint}>Tap ☆ to pick up to {maxPriority} priority contacts.</Text>
      <TextInput
        style={[styles.input, styles.search]}
        value={query}
        onChangeText={setQuery}
        placeholder="Search name or number"
        autoCorrect={false}
      />
    </>
  );

  return (
    <FlatList
      style={styles.list}
      data={visible}
      keyExtractor={(c) => String(c.id)}
      ListHeaderComponent={header}
      keyboardShouldPersistTaps="handled"
      ListEmptyComponent={
        contacts === null ? (
          <ActivityIndicator color={COLORS.primary} />
        ) : (
          <Text style={styles.hint}>
            {query ? 'No contacts match.' : 'No contacts yet. Tap Sync contacts to upload your phone\'s contacts.'}
          </Text>
        )
      }
      renderItem={({ item }) => (
        <View style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowName} numberOfLines={1}>
              {item.name}
            </Text>
            <Text style={styles.rowPhone}>{item.phone}</Text>
          </View>
          <Pressable
            onPress={() => togglePriority(item)}
            hitSlop={12}
            accessibilityRole="button"
            accessibilityLabel={item.priority ? `Remove ${item.name} from priority` : `Make ${item.name} a priority`}
          >
            <Text style={[styles.star, item.priority && styles.starOn]}>{item.priority ? '★' : '☆'}</Text>
          </Pressable>
        </View>
      )}
    />
  );
}

function PhoneField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <>
      <Text style={styles.label}>Phone number</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={(v) => onChange(v.replace(/[^\d+\s-]/g, ''))}
        placeholder="0712 345 678 or +254 712 345 678"
        keyboardType="phone-pad"
        autoComplete="tel"
      />
    </>
  );
}

function PinField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={(v) => onChange(v.replace(/\D/g, '').slice(0, 4))}
        placeholder="••••"
        keyboardType="number-pad"
        secureTextEntry
      />
    </>
  );
}

function Button({
  title,
  onPress,
  busy,
  disabled,
}: {
  title: string;
  onPress: () => void;
  busy?: boolean;
  disabled?: boolean;
}) {
  const off = busy || disabled;
  return (
    <Pressable style={[styles.button, off && styles.buttonOff]} onPress={onPress} disabled={off}>
      {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>{title}</Text>}
    </Pressable>
  );
}

function Link({ title, onPress, disabled }: { title: string; onPress: () => void; disabled?: boolean }) {
  return (
    <Pressable onPress={onPress} disabled={disabled}>
      <Text style={styles.link}>{title}</Text>
    </Pressable>
  );
}

const COLORS = { primary: '#0B6E4F', text: '#1A1A1A', muted: '#6B6B6B', border: '#D5D5D5', error: '#B3261E' };

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#F6F7F6', paddingHorizontal: 20, paddingTop: 80 },
  title: { fontSize: 28, fontWeight: '700', color: COLORS.text, marginBottom: 24 },
  heading: { fontSize: 20, fontWeight: '600', color: COLORS.text },
  card: { backgroundColor: '#fff', borderRadius: 12, padding: 20, gap: 8 },
  label: { fontSize: 14, color: COLORS.muted, marginTop: 4 },
  value: { fontSize: 20, fontWeight: '600', color: COLORS.text, marginBottom: 8 },
  input: {
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 18,
    color: COLORS.text,
  },
  button: {
    backgroundColor: COLORS.primary,
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 12,
  },
  buttonOff: { opacity: 0.5 },
  buttonText: { color: '#fff', fontSize: 17, fontWeight: '600' },
  status: { color: COLORS.text, marginTop: 8 },
  error: { color: COLORS.error, marginTop: 8 },
  hint: { color: COLORS.muted, fontSize: 13, marginTop: 12 },
  link: { color: COLORS.primary, fontSize: 15, marginTop: 16, textAlign: 'center' },
  linkRow: { flexDirection: 'row', justifyContent: 'space-around' },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  nameInput: { flex: 1 },
  list: { flex: 1 },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 16 },
  switchText: { flex: 1 },
  switchLabel: { fontSize: 16, color: COLORS.text, fontWeight: '500' },
  sectionTitle: { fontSize: 17, fontWeight: '600', color: COLORS.text, marginTop: 24 },
  search: { marginTop: 12, marginBottom: 8, backgroundColor: '#fff' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: COLORS.border,
  },
  rowText: { flex: 1, marginRight: 12 },
  rowName: { fontSize: 16, color: COLORS.text },
  rowPhone: { fontSize: 14, color: COLORS.muted, marginTop: 2 },
  star: { fontSize: 26, color: COLORS.muted },
  starOn: { color: '#D99A00' },
});
