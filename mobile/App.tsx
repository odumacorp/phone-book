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

import { ApiError, Me, clearToken, getMe, loadToken, login, syncContacts } from './src/api';
import { readDeviceContacts } from './src/deviceContacts';

const USSD_CODE = '*384*88041#';
const BATCH_SIZE = 1000;

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [booting, setBooting] = useState(true);

  useEffect(() => {
    (async () => {
      const saved = await loadToken();
      if (saved) {
        try {
          setMe(await getMe(saved));
          setToken(saved);
        } catch (e) {
          if (e instanceof ApiError && e.status === 401) await clearToken();
          else setToken(saved); // offline: keep the session, refresh later
        }
      }
      setBooting(false);
    })();
  }, []);

  const logout = async () => {
    await clearToken();
    setToken(null);
    setMe(null);
  };

  return (
    <View style={styles.screen}>
      <StatusBar style="dark" />
      <Text style={styles.title}>Phone Book</Text>
      {booting ? (
        <ActivityIndicator size="large" color={COLORS.primary} />
      ) : token ? (
        <SyncScreen token={token} me={me} setMe={setMe} onLogout={logout} />
      ) : (
        <LoginScreen
          onLogin={async (t) => {
            setToken(t);
            setMe(await getMe(t).catch(() => null));
          }}
        />
      )}
    </View>
  );
}

function LoginScreen({ onLogin }: { onLogin: (token: string) => Promise<void> }) {
  const [phone, setPhone] = useState('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
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
      <Text style={styles.label}>Phone number</Text>
      <TextInput
        style={styles.input}
        value={phone}
        onChangeText={setPhone}
        placeholder="0712 345 678"
        keyboardType="phone-pad"
        autoComplete="tel"
      />
      <Text style={styles.label}>4-digit PIN</Text>
      <TextInput
        style={styles.input}
        value={pin}
        onChangeText={(v) => setPin(v.replace(/\D/g, '').slice(0, 4))}
        placeholder="••••"
        keyboardType="number-pad"
        secureTextEntry
      />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title="Log in" onPress={submit} busy={busy} disabled={!phone || pin.length !== 4} />
      <Text style={styles.hint}>
        No PIN yet? Dial {USSD_CODE} from this phone, enter your number and create one.
      </Text>
    </KeyboardAvoidingView>
  );
}

function SyncScreen({
  token,
  me,
  setMe,
  onLogout,
}: {
  token: string;
  me: Me | null;
  setMe: (me: Me) => void;
  onLogout: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
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
      <Pressable onPress={onLogout} disabled={busy}>
        <Text style={styles.link}>Log out</Text>
      </Pressable>
    </View>
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

const COLORS = { primary: '#0B6E4F', text: '#1A1A1A', muted: '#6B6B6B', border: '#D5D5D5', error: '#B3261E' };

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#F6F7F6', paddingHorizontal: 20, paddingTop: 80 },
  title: { fontSize: 28, fontWeight: '700', color: COLORS.text, marginBottom: 24 },
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
