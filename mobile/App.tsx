import { StatusBar } from 'expo-status-bar';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import {
  ApiError,
  Me,
  PHONE_FORMAT,
  changePin,
  clearToken,
  confirmPinReset,
  getMe,
  isValidPhone,
  loadToken,
  login,
  requestPinReset,
  syncContacts,
} from './src/api';
import { readDeviceContacts } from './src/deviceContacts';

const USSD_CODE = '*384*88041#';
const BATCH_SIZE = 1000;

type View_ = 'login' | 'reset' | 'sync' | 'changePin';

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
  else if (view === 'reset')
    content = <ResetPinScreen onDone={(t) => loggedIn(t, 'PIN reset. You are logged in.')} onCancel={() => setView('login')} />;
  else if (!token) content = <LoginScreen onLogin={loggedIn} onForgot={() => setView('reset')} />;
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

function LoginScreen({ onLogin, onForgot }: { onLogin: (token: string) => Promise<void>; onForgot: () => void }) {
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
      <Text style={styles.hint}>
        No PIN yet? Dial {USSD_CODE} from this phone, enter your number and create one.
      </Text>
    </KeyboardAvoidingView>
  );
}

function ResetPinScreen({ onDone, onCancel }: { onDone: (token: string) => void; onCancel: () => void }) {
  const [phone, setPhone] = useState('');
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
    if (!isValidPhone(phone)) return setError(`Enter your phone number as ${PHONE_FORMAT}.`);
    run(async () => {
      await requestPinReset(phone);
      setCodeSent(true);
    });
  };

  const reset = () => {
    if (pin !== confirm) return setError('New PINs do not match.');
    run(async () => onDone(await confirmPinReset(phone, code, pin)));
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.card}>
      <Text style={styles.heading}>Reset your PIN</Text>
      {!codeSent ? (
        <>
          <Text style={styles.hint}>We will text a 6-digit code to your number.</Text>
          <PhoneField value={phone} onChange={setPhone} />
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button title="Send code" onPress={sendCode} busy={busy} disabled={!phone} />
        </>
      ) : (
        <>
          <Text style={styles.hint}>Enter the code sent to {phone} and choose a new PIN.</Text>
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
          <PinField label="New PIN" value={pin} onChange={setPin} />
          <PinField label="Confirm new PIN" value={confirm} onChange={setConfirm} />
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button
            title="Reset PIN"
            onPress={reset}
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

  const sync = async () => {
    setBusy(true);
    setError('');
    setStatus('Reading your contacts…');
    try {
      const contacts = await readDeviceContacts();
      if (contacts === null) {
        setStatus('');
        setError('Contacts permission is needed. Allow it in Settings → Apps → Phone Book.');
        return;
      }
      let added = 0;
      for (let i = 0; i < contacts.length; i += BATCH_SIZE) {
        setStatus(`Uploading ${Math.min(i + BATCH_SIZE, contacts.length)} of ${contacts.length}…`);
        added += (await syncContacts(token, contacts.slice(i, i + BATCH_SIZE))).added;
      }
      setMe(await getMe(token));
      setStatus(added ? `Done. ${added} new contact${added === 1 ? '' : 's'} uploaded.` : 'Done. Everything was already synced.');
    } catch (e) {
      setStatus('');
      if (e instanceof ApiError && e.status === 401) return onLogout();
      setError(e instanceof Error ? e.message : 'Sync failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.card}>
      <Text style={styles.label}>Logged in as</Text>
      <Text style={styles.value}>{me?.phone ?? '…'}</Text>
      <Text style={styles.label}>Contacts in your phone book</Text>
      <Text style={styles.value}>{me ? me.contacts : '…'}</Text>
      <Button title="Sync contacts" onPress={sync} busy={busy} />
      {status ? <Text style={styles.status}>{status}</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Text style={styles.hint}>
        Synced contacts are available any time by dialling {USSD_CODE}, even without internet.
      </Text>
      <Link title="Change PIN" onPress={onChangePin} disabled={busy} />
      <Link title="Log out" onPress={onLogout} disabled={busy} />
    </View>
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
});
