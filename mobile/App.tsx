import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';

import {
  ApiError,
  Contact,
  Me,
  PASSWORD_MIN,
  PHONE_FORMAT,
  changePassword,
  changePin,
  clearToken,
  getContacts,
  getMe,
  isValidPhone,
  loadToken,
  login,
  register,
  requestPasswordReset,
  requestRegistration,
  resetPassword,
  setName,
  setPriority,
  setShowOthers,
  syncContacts,
} from './src/api';
import { readDeviceContacts } from './src/deviceContacts';

const USSD_CODE = '*384*88041#';
const BATCH_SIZE = 1000;

type View_ = 'login' | 'register' | 'reset' | 'sync' | 'changePin' | 'changePassword';

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

  const back = () => setView(token ? 'sync' : 'login');

  let content;
  if (booting) content = <ActivityIndicator size="large" color={COLORS.primary} />;
  else if (view === 'register')
    content = <RegisterScreen onDone={(t) => loggedIn(t, 'Account created. Tap Sync contacts to upload them.')} onCancel={back} />;
  else if (view === 'reset')
    content = <ResetPasswordScreen onDone={(t) => loggedIn(t, 'Password reset. You are logged in.')} onCancel={back} />;
  else if (!token)
    content = <LoginScreen onLogin={loggedIn} onRegister={() => setView('register')} onForgot={() => setView('reset')} />;
  else if (view === 'changePassword')
    content = <ChangePasswordScreen token={token} onDone={(t) => loggedIn(t, 'Password changed.')} onCancel={back} onLogout={logout} />;
  else if (view === 'changePin')
    content = <ChangePinScreen token={token} onDone={(t) => loggedIn(t, 'USSD PIN changed.')} onCancel={back} onLogout={logout} />;
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
        onChangePassword={() => {
          setNotice('');
          setView('changePassword');
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

/** Runs a form action with a busy flag and shows its error message. */
function useForm() {
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
  return { busy, error, setError, run };
}

/** Scrollable card for forms, so long ones stay usable with the keyboard open. */
function Form({ children }: { children: React.ReactNode }) {
  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.list}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.formScroll}>
        <View style={styles.card}>{children}</View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const isEmail = (v: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v.trim());

function LoginScreen({
  onLogin,
  onRegister,
  onForgot,
}: {
  onLogin: (token: string) => Promise<void>;
  onRegister: () => void;
  onForgot: () => void;
}) {
  const [account, setAccount] = useState('');
  const [password, setPassword] = useState('');
  const { busy, error, setError, run } = useForm();

  const submit = () => {
    if (!isEmail(account) && !isValidPhone(account))
      return setError(`Enter your email, or your phone number as ${PHONE_FORMAT}.`);
    run(async () => onLogin(await login(account.trim(), password)));
  };

  return (
    <Form>
      <AccountField value={account} onChange={setAccount} />
      <PasswordField label="Password" value={password} onChange={setPassword} />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title="Log in" onPress={submit} busy={busy} disabled={!account.trim() || !password} />
      <Link title="Forgot password?" onPress={onForgot} disabled={busy} />
      <Link title="New here? Create an account" onPress={onRegister} disabled={busy} />
    </Form>
  );
}

/** Register: name, phone (+ optional email) -> SMS code -> password and USSD PIN. */
function RegisterScreen({ onDone, onCancel }: { onDone: (token: string) => void; onCancel: () => void }) {
  const [name, setNameText] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [codeSent, setCodeSent] = useState(false);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [pin, setPin] = useState('');
  const [confirmPin, setConfirmPin] = useState('');
  const { busy, error, setError, run } = useForm();

  const sendCode = () => {
    if (!name.trim()) return setError('Enter your name.');
    if (!isValidPhone(phone)) return setError(`Enter your phone number as ${PHONE_FORMAT}.`);
    if (email.trim() && !isEmail(email)) return setError('Enter a valid email address, or leave it empty.');
    run(async () => {
      await requestRegistration(phone, email.trim());
      setCodeSent(true);
    });
  };

  const finish = () => {
    if (password.length < PASSWORD_MIN) return setError(`Password must be at least ${PASSWORD_MIN} characters.`);
    if (password !== confirmPassword) return setError('Passwords do not match.');
    if (pin !== confirmPin) return setError('PINs do not match.');
    run(async () => onDone(await register({ phone, code, name: name.trim(), email: email.trim(), password, pin })));
  };

  return (
    <Form>
      <Text style={styles.heading}>Create an account</Text>
      {!codeSent ? (
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
          <PhoneField value={phone} onChange={setPhone} />
          <Text style={styles.hint}>We will text a 6-digit code to confirm it is yours.</Text>
          <Text style={styles.label}>Email (optional)</Text>
          <TextInput
            style={styles.input}
            value={email}
            onChangeText={setEmail}
            placeholder="you@example.com"
            keyboardType="email-address"
            autoCapitalize="none"
            autoComplete="email"
            autoCorrect={false}
          />
          <Text style={styles.hint}>Lets you log in with your email instead of your phone number.</Text>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button title="Send code" onPress={sendCode} busy={busy} disabled={!name.trim() || !phone} />
        </>
      ) : (
        <>
          <Text style={styles.hint}>Enter the code sent to {phone}.</Text>
          <CodeField value={code} onChange={setCode} />
          <PasswordField label={`Password (at least ${PASSWORD_MIN} characters)`} value={password} onChange={setPassword} isNew />
          <PasswordField label="Confirm password" value={confirmPassword} onChange={setConfirmPassword} isNew />
          <Text style={styles.hint}>For logging in to this app.</Text>
          <PinField label="4-digit USSD PIN" value={pin} onChange={setPin} />
          <PinField label="Confirm USSD PIN" value={confirmPin} onChange={setConfirmPin} />
          <Text style={styles.hint}>For dialling {USSD_CODE}, where you log in with your phone number and this PIN.</Text>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button
            title="Create account"
            onPress={finish}
            busy={busy}
            disabled={code.length !== 6 || !password || !confirmPassword || pin.length !== 4 || confirmPin.length !== 4}
          />
          <Link title="Send a new code" onPress={sendCode} disabled={busy} />
        </>
      )}
      <Link title="Back to log in" onPress={onCancel} disabled={busy} />
    </Form>
  );
}

/** Forgot password: email or phone -> SMS code to the account's phone -> new password. */
function ResetPasswordScreen({ onDone, onCancel }: { onDone: (token: string) => void; onCancel: () => void }) {
  const [account, setAccount] = useState('');
  const [sentTo, setSentTo] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const { busy, error, setError, run } = useForm();

  const sendCode = () => {
    if (!isEmail(account) && !isValidPhone(account))
      return setError(`Enter your email, or your phone number as ${PHONE_FORMAT}.`);
    run(async () => setSentTo((await requestPasswordReset(account.trim())).phone));
  };

  const finish = () => {
    if (password.length < PASSWORD_MIN) return setError(`Password must be at least ${PASSWORD_MIN} characters.`);
    if (password !== confirm) return setError('Passwords do not match.');
    run(async () => onDone(await resetPassword(account.trim(), code, password)));
  };

  return (
    <Form>
      <Text style={styles.heading}>Reset your password</Text>
      {!sentTo ? (
        <>
          <Text style={styles.hint}>We will text a 6-digit code to the phone number on your account.</Text>
          <AccountField value={account} onChange={setAccount} />
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button title="Send code" onPress={sendCode} busy={busy} disabled={!account.trim()} />
        </>
      ) : (
        <>
          <Text style={styles.hint}>Enter the code sent to {sentTo} and choose a new password.</Text>
          <CodeField value={code} onChange={setCode} />
          <PasswordField label={`New password (at least ${PASSWORD_MIN} characters)`} value={password} onChange={setPassword} isNew />
          <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} isNew />
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button title="Reset password" onPress={finish} busy={busy} disabled={code.length !== 6 || !password || !confirm} />
          <Link title="Send a new code" onPress={sendCode} disabled={busy} />
        </>
      )}
      <Link title="Back" onPress={onCancel} disabled={busy} />
    </Form>
  );
}

function ChangePasswordScreen({
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
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const { busy, error, setError, run } = useForm();

  const submit = () => {
    if (password.length < PASSWORD_MIN) return setError(`Password must be at least ${PASSWORD_MIN} characters.`);
    if (password !== confirm) return setError('New passwords do not match.');
    run(async () => {
      try {
        onDone(await changePassword(token, current, password));
      } catch (e) {
        if (e instanceof ApiError && e.message.startsWith('Please log in')) return onLogout();
        throw e;
      }
    });
  };

  return (
    <Form>
      <Text style={styles.heading}>Change password</Text>
      <PasswordField label="Current password" value={current} onChange={setCurrent} />
      <PasswordField label={`New password (at least ${PASSWORD_MIN} characters)`} value={password} onChange={setPassword} isNew />
      <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} isNew />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title="Change password" onPress={submit} busy={busy} disabled={!current || !password || !confirm} />
      <Text style={styles.hint}>Other devices will be logged out.</Text>
      <Link title="Cancel" onPress={onCancel} disabled={busy} />
    </Form>
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
  const [password, setPassword] = useState('');
  const [pin, setPin] = useState('');
  const [confirm, setConfirm] = useState('');
  const { busy, error, setError, run } = useForm();

  const submit = () => {
    if (pin !== confirm) return setError('PINs do not match.');
    run(async () => {
      try {
        onDone(await changePin(token, password, pin));
      } catch (e) {
        if (e instanceof ApiError && e.message.startsWith('Please log in')) return onLogout();
        throw e;
      }
    });
  };

  return (
    <Form>
      <Text style={styles.heading}>Change USSD PIN</Text>
      <Text style={styles.hint}>The PIN you use when dialling {USSD_CODE}. Forgot it? Just set a new one here.</Text>
      <PasswordField label="Your app password" value={password} onChange={setPassword} />
      <PinField label="New PIN" value={pin} onChange={setPin} />
      <PinField label="Confirm new PIN" value={confirm} onChange={setConfirm} />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title="Change PIN" onPress={submit} busy={busy} disabled={!password || pin.length !== 4 || confirm.length !== 4} />
      <Link title="Cancel" onPress={onCancel} disabled={busy} />
    </Form>
  );
}

/** Home: sync, the USSD visibility switch, and the synced phone book with priority stars. */
function SyncScreen({
  token,
  me,
  setMe,
  notice,
  onChangePin,
  onChangePassword,
  onLogout,
}: {
  token: string;
  me: Me | null;
  setMe: (me: Me) => void;
  notice: string;
  onChangePin: () => void;
  onChangePassword: () => void;
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
        {me?.email ? <Text style={styles.hint}>{me.email}</Text> : null}
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
          <Link title="Change password" onPress={onChangePassword} disabled={busy} />
          <Link title="Change USSD PIN" onPress={onChangePin} disabled={busy} />
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

function AccountField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <>
      <Text style={styles.label}>Email or phone number</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChange}
        placeholder="you@example.com or 0712 345 678"
        keyboardType="email-address"
        autoCapitalize="none"
        autoComplete="username"
        autoCorrect={false}
      />
    </>
  );
}

function PasswordField({
  label,
  value,
  onChange,
  isNew,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  isNew?: boolean;
}) {
  const [shown, setShown] = useState(false);
  return (
    <>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.nameRow}>
        <TextInput
          style={[styles.input, styles.nameInput]}
          value={value}
          onChangeText={onChange}
          secureTextEntry={!shown}
          autoCapitalize="none"
          autoCorrect={false}
          autoComplete={isNew ? 'new-password' : 'current-password'}
          textContentType={isNew ? 'newPassword' : 'password'}
          maxLength={128}
        />
        <Pressable onPress={() => setShown(!shown)} hitSlop={8}>
          <Text style={styles.showLink}>{shown ? 'Hide' : 'Show'}</Text>
        </Pressable>
      </View>
    </>
  );
}

function CodeField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <>
      <Text style={styles.label}>6-digit code</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={(v) => onChange(v.replace(/\D/g, '').slice(0, 6))}
        placeholder="123456"
        keyboardType="number-pad"
        autoComplete="sms-otp"
        textContentType="oneTimeCode"
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
  showLink: { color: COLORS.primary, fontSize: 15 },
  formScroll: { paddingBottom: 40 },
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
