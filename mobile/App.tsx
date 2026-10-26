import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Image,
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
  SyncContact,
  AppSession,
  UssdLogin,
  UssdSession,
  PHONE_FORMAT,
  addContact,
  changePassword,
  changePin,
  clearToken,
  deleteContact,
  getContacts,
  getLogins,
  getSessions,
  getMe,
  isValidPhone,
  loadToken,
  login,
  logoutApp,
  logoutAppSession,
  logoutUssdSession,
  normalizePhone,
  register,
  requestPasswordReset,
  requestRegistration,
  resetPassword,
  setName,
  setPriority,
  setShowOthers,
  syncContacts,
} from './src/api';
import { contactSource, readDeviceContacts } from './src/deviceContacts';
import { DialogProvider, useDialog } from './src/dialog';
import { isWeb } from './src/platform';

const USSD_CODE = '*384*88041#';
const BATCH_SIZE = 1000;

type View_ =
  | 'login'
  | 'register'
  | 'reset'
  | 'sync'
  | 'changePin'
  | 'changePassword'
  | 'logins'
  | 'sessions'
  | 'addContact'
  | 'pickContacts';

export default function App() {
  return (
    <DialogProvider>
      <Main />
    </DialogProvider>
  );
}

function Main() {
  const ask = useDialog();
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
    await clearToken(); // local only: used when the server already rejected the token
    setToken(null);
    setMe(null);
    setNotice('');
    setView('login');
  };

  const back = () => setView(token ? 'sync' : 'login');

  const confirmLogout = () => {
    const out = async (everywhere: boolean) => {
      if (token) await logoutApp(token, everywhere).catch(() => null); // offline: still log out here
      await logout();
    };
    ask('Log out?', 'Log out everywhere also ends your app login on other devices and any USSD session.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Log out everywhere', style: 'destructive', onPress: () => out(true) },
      { text: 'Log out', onPress: () => out(false) },
    ]);
  };

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
  else if (view === 'logins') content = <LoginHistoryScreen token={token} onBack={back} onLogout={logout} />;
  else if (view === 'sessions') content = <SessionsScreen token={token} onBack={back} onLogout={logout} />;
  else if (view === 'addContact')
    content = <AddContactScreen token={token} onDone={(msg) => loggedIn(token, msg)} onCancel={back} onLogout={logout} />;
  else if (view === 'pickContacts')
    content = <PickContactsScreen token={token} onDone={(msg) => loggedIn(token, msg)} onCancel={back} onLogout={logout} />;
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
        onAddContact={() => {
          setNotice('');
          setView('addContact');
        }}
        onPickContacts={() => {
          setNotice('');
          setView('pickContacts');
        }}
        onLoginHistory={() => {
          setNotice('');
          setView('logins');
        }}
        onSessions={() => {
          setNotice('');
          setView('sessions');
        }}
        onLogout={logout}
      />
    );

  return (
    <View style={styles.screen}>
      <StatusBar style="dark" />
      <View style={styles.column}>
      <View style={styles.titleRow}>
        <View style={styles.brand}>
          <Image source={require('./assets/logo.png')} style={styles.logo} accessibilityLabel="Oduma Corp" />
          <Text style={styles.title}>Remote Phonebook</Text>
        </View>
        {token && !booting && view !== 'register' && view !== 'reset' ? (
          <Pressable onPress={confirmLogout} hitSlop={10} accessibilityRole="button">
            <Text style={styles.logout}>Log out</Text>
          </Pressable>
        ) : null}
      </View>
        {content}
      </View>
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

const LOGIN_RESULT: Record<UssdLogin['reason'], string> = {
  ok: 'Logged in',
  resumed: 'Continued (within 1 min)',
  wrong_pin: 'Wrong PIN',
  locked: 'Blocked: too many wrong PINs',
};

/** "45 s", "12 min", "1 h 5 min", "3 d 2 h" */
function formatDuration(seconds: number) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d} d ${h % 24} h` : `${d} d`;
}

const formatTime = (unix: number) => new Date(unix * 1000).toLocaleString();

const END_REASON: Record<string, string> = {
  'logged out': 'Logged out',
  'logged out from another device': 'Logged out from another device',
  'logged out everywhere': 'Logged out everywhere',
  'logged out from the app': 'Logged out from the app',
  'logged out on USSD': 'Logged out on USSD',
  'password or PIN changed': 'Password or PIN changed',
  'PIN changed': 'PIN changed',
  'timed out': 'Timed out (idle)',
  'session ended': 'Session ended',
  'new login': 'Replaced by a new login',
  expired: 'Expired',
};

type SessionItem = { kind: 'app'; s: AppSession } | { kind: 'ussd'; s: UssdSession };

/** Every session on the account (app devices and USSD phones): login, logout, duration; log out each one. */
function SessionsScreen({ token, onBack, onLogout }: { token: string; onBack: () => void; onLogout: () => void }) {
  const ask = useDialog();
  const [data, setData] = useState<{ app: AppSession[]; ussd: UssdSession[]; timeout: number; days: number } | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(Date.now() / 1000);

  const load = useCallback(async () => {
    setError('');
    try {
      const res = await getSessions(token);
      setData({ app: res.app, ussd: res.ussd, timeout: res.ussd_timeout_seconds, days: res.history_days });
      setNow(Date.now() / 1000);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onLogout();
      setError(e instanceof Error ? e.message : 'Could not load your sessions.');
    }
  }, [token]);

  useEffect(() => {
    load();
    const timer = setInterval(() => setNow(Date.now() / 1000), 15000); // keep active durations ticking
    return () => clearInterval(timer);
  }, [load]);

  const endSession = (item: SessionItem) => {
    const label = item.kind === 'app' ? item.s.device : `USSD on ${item.s.phone}`;
    const thisDevice = item.kind === 'app' && item.s.current;
    const message = thisDevice
      ? 'You will be logged out of this app.'
      : item.kind === 'app'
        ? 'That device will be logged out of the app.'
        : 'The USSD session ends on its next screen, and the next dial needs the phone number and PIN.';
    ask(`Log out ${label}?`, message, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Log out',
        style: 'destructive',
        onPress: async () => {
          try {
            if (item.kind === 'app') await logoutAppSession(token, item.s.id);
            else await logoutUssdSession(token, item.s.id);
            if (thisDevice) return onLogout();
            setNotice(`${label} logged out.`);
            await load();
          } catch (e) {
            if (e instanceof ApiError && e.status === 401) return onLogout();
            setError(e instanceof Error ? e.message : 'Could not log out that session.');
            await load();
          }
        },
      },
    ]);
  };

  const sections: { title: string; items: SessionItem[] }[] = data
    ? [
        { title: 'This app', items: data.app.map((s) => ({ kind: 'app' as const, s })) },
        { title: `USSD (${USSD_CODE})`, items: data.ussd.map((s) => ({ kind: 'ussd' as const, s })) },
      ]
    : [];
  const rows = sections.flatMap((sec) => [
    { key: `h-${sec.title}`, header: sec.title, empty: sec.items.length === 0 },
    ...sec.items.map((item) => ({ key: `${item.kind}-${item.s.id}`, item })),
  ]);

  return (
    <FlatList
      style={styles.list}
      data={rows}
      keyExtractor={(r) => r.key}
      refreshing={refreshing}
      onRefresh={async () => {
        setRefreshing(true);
        await load();
        setRefreshing(false);
      }}
      ListHeaderComponent={
        <View style={styles.card}>
          <Text style={styles.heading}>Sessions</Text>
          <Text style={styles.hint}>
            Where your account is logged in, with login and logout times and how long each lasted. Active sessions come
            first; ended ones are kept for {data?.days ?? 30} days. Pull down to refresh.
          </Text>
          {data ? (
            <Text style={styles.hint}>
              USSD sessions end after {Math.round(data.timeout / 60)} minute{Math.round(data.timeout / 60) === 1 ? '' : 's'}{' '}
              without activity.
            </Text>
          ) : null}
          {notice ? <Text style={styles.status}>{notice}</Text> : null}
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Link title="Back" onPress={onBack} />
        </View>
      }
      ListEmptyComponent={<ActivityIndicator color={COLORS.primary} />}
      renderItem={({ item: row }) => {
        if ('header' in row) {
          return (
            <>
              <Text style={styles.sectionTitle}>{row.header}</Text>
              {row.empty ? <Text style={styles.hint}>No sessions.</Text> : null}
            </>
          );
        }
        const { item } = row;
        const s = item.s;
        const title =
          item.kind === 'app'
            ? `${item.s.device}${item.s.current ? '  (this device)' : ''}`
            : `${item.s.phone}  (${item.s.own_phone ? 'your phone' : 'another phone'}) · ${item.s.network}`;
        const idleEnd = item.kind === 'ussd' ? item.s.expires_at : null;
        const duration = s.active ? now - s.logged_in : s.duration_seconds;
        return (
          <View style={[styles.sessionRow, s.active && styles.sessionActive]}>
            <View style={styles.rowText}>
              <Text style={styles.rowName} numberOfLines={2}>
                {title}
              </Text>
              <Text style={styles.rowPhone}>Logged in: {formatTime(s.logged_in)}</Text>
              {s.active ? (
                <Text style={[styles.rowPhone, styles.activeText]}>
                  Active now · last active {new Date(s.last_active * 1000).toLocaleTimeString()}
                  {idleEnd ? ` · ends ${new Date(idleEnd * 1000).toLocaleTimeString()} if idle` : ''}
                </Text>
              ) : (
                <Text style={styles.rowPhone}>
                  Logged out: {formatTime(s.logged_out as number)} · {END_REASON[s.end_reason ?? ''] ?? s.end_reason ?? 'Ended'}
                </Text>
              )}
              <Text style={styles.rowPhone}>Duration: {formatDuration(duration)}{s.active ? ' so far' : ''}</Text>
            </View>
            {s.active ? (
              <Pressable onPress={() => endSession(item)} hitSlop={8} accessibilityRole="button">
                <Text style={styles.logout}>Log out</Text>
              </Pressable>
            ) : null}
          </View>
        );
      }}
    />
  );
}

/** USSD login history: which phones logged in to this phone book over USSD, and failed attempts. */
function LoginHistoryScreen({ token, onBack, onLogout }: { token: string; onBack: () => void; onLogout: () => void }) {
  const [logins, setLogins] = useState<UssdLogin[] | null>(null);
  const [keepDays, setKeepDays] = useState(90);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError('');
    try {
      const res = await getLogins(token);
      setLogins(res.logins);
      setKeepDays(res.keep_days);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onLogout();
      setError(e instanceof Error ? e.message : 'Could not load your login history.');
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const failures = logins?.filter((l) => !l.success).length ?? 0;
  const otherPhones = new Set(logins?.filter((l) => l.success && !l.own_phone).map((l) => l.phone)).size;

  return (
    <FlatList
      style={styles.list}
      data={logins ?? []}
      keyExtractor={(l, i) => `${l.at}-${i}`}
      refreshing={refreshing}
      onRefresh={async () => {
        setRefreshing(true);
        await load();
        setRefreshing(false);
      }}
      ListHeaderComponent={
        <>
          <View style={styles.card}>
            <Text style={styles.heading}>USSD login attempts</Text>
            <Text style={styles.hint}>
              Every time someone logs in to your phone book by dialling {USSD_CODE}, or tries to. Kept for {keepDays} days.
              Pull down to refresh.
            </Text>
            {logins && logins.length ? (
              <Text style={[styles.status, failures ? styles.warn : null]}>
                {failures ? `${failures} failed attempt${failures === 1 ? '' : 's'}. ` : 'No failed attempts. '}
                {otherPhones ? `Logged in from ${otherPhones} other phone${otherPhones === 1 ? '' : 's'}.` : ''}
              </Text>
            ) : null}
            {failures ? (
              <Text style={styles.hint}>Not you? Change your USSD PIN from the home screen.</Text>
            ) : null}
            {error ? <Text style={styles.error}>{error}</Text> : null}
            <Link title="Back" onPress={onBack} />
          </View>
          <View style={styles.spacer} />
        </>
      }
      ListEmptyComponent={
        logins === null ? (
          <ActivityIndicator color={COLORS.primary} />
        ) : (
          <Text style={styles.hint}>No USSD logins yet.</Text>
        )
      }
      renderItem={({ item }) => (
        <View style={styles.row}>
          <View style={[styles.dot, item.success ? styles.dotOk : styles.dotBad]} />
          <View style={styles.rowText}>
            <Text style={styles.rowName}>
              {item.phone}
              {item.own_phone ? '  (your phone)' : '  (another phone)'}
            </Text>
            <Text style={styles.rowPhone}>
              {new Date(item.at * 1000).toLocaleString()} · {item.network}
            </Text>
          </View>
          <Text style={[styles.result, item.success ? styles.resultOk : styles.resultBad]}>{LOGIN_RESULT[item.reason]}</Text>
        </View>
      )}
    />
  );
}

function AddContactScreen({
  token,
  onDone,
  onCancel,
  onLogout,
}: {
  token: string;
  onDone: (message: string) => void;
  onCancel: () => void;
  onLogout: () => void;
}) {
  const [name, setNameText] = useState('');
  const [phone, setPhone] = useState('');
  const { busy, error, setError, run } = useForm();

  const submit = () => {
    if (!name.trim()) return setError('Enter a name.');
    if (!normalizePhone(phone)) return setError('Enter a valid phone number, e.g. 0712 345 678 or +965 6674 4067.');
    run(async () => {
      try {
        const saved = await addContact(token, name.trim(), phone);
        onDone(`${saved.name} added.`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return onLogout();
        throw e;
      }
    });
  };

  return (
    <Form>
      <Text style={styles.heading}>Add contact</Text>
      <Text style={styles.label}>Name</Text>
      <TextInput style={styles.input} value={name} onChangeText={setNameText} placeholder="e.g. Mama Mboga" maxLength={60} />
      <Text style={styles.label}>Phone number</Text>
      <TextInput
        style={styles.input}
        value={phone}
        onChangeText={setPhone}
        placeholder="0712 345 678"
        keyboardType="phone-pad"
      />
      <Text style={styles.hint}>Kenyan numbers can start with 0; other countries need the + code.</Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title="Add contact" onPress={submit} busy={busy} disabled={!name.trim() || !phone.trim()} />
      <Link title="Cancel" onPress={onCancel} disabled={busy} />
    </Form>
  );
}

type DeviceEntry = SyncContact & { key: string; saved: boolean };

/** Choose which of the phone's contacts go into the Remote Phonebook (instead of uploading them all). */
function PickContactsScreen({
  token,
  onDone,
  onCancel,
  onLogout,
}: {
  token: string;
  onDone: (message: string) => void;
  onCancel: () => void;
  onLogout: () => void;
}) {
  const [entries, setEntries] = useState<DeviceEntry[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const { busy, error, setError, run } = useForm();
  // On the website, the phonebook picker / file chooser can only open from a tap
  const [waitingForTap, setWaitingForTap] = useState(isWeb);

  const load = async (devicePromise: Promise<SyncContact[] | null>) => {
    try {
      const [device, saved] = await Promise.all([devicePromise, getContacts(token)]);
      if (device === null) {
        setEntries([]);
        return setError(
          isWeb ? 'No contacts were chosen.' : 'Contacts permission is needed. Allow it in Settings → Apps → Remote Phonebook.',
        );
      }
      setError('');
      const savedPhones = new Set(saved.contacts.map((c) => c.phone));
      const seen = new Set<string>();
      const list: DeviceEntry[] = [];
      for (const c of device) {
        const key = normalizePhone(c.phone);
        if (!key || seen.has(key)) continue; // skip invalid numbers and duplicates
        seen.add(key);
        list.push({ ...c, key, saved: savedPhones.has(key) });
      }
      list.sort((a, b) => a.name.localeCompare(b.name));
      setEntries(list);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onLogout();
      setEntries([]);
      setError(e instanceof Error ? e.message : 'Could not read your contacts.');
    }
  };

  useEffect(() => {
    if (!isWeb) load(readDeviceContacts());
  }, [token]);

  const openOnWeb = () => {
    setWaitingForTap(false);
    setEntries(null);
    setSelected(new Set());
    load(readDeviceContacts()); // called straight from the tap, so the browser allows the picker
  };

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = entries ?? [];
    return q ? list.filter((c) => c.name.toLowerCase().includes(q) || c.phone.includes(q)) : list;
  }, [entries, query]);
  const addable = shown.filter((c) => !c.saved);

  const toggle = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const upload = () =>
    run(async () => {
      const chosen = (entries ?? []).filter((c) => selected.has(c.key));
      let added = 0;
      for (let i = 0; i < chosen.length; i += BATCH_SIZE) {
        setStatus(`Adding ${Math.min(i + BATCH_SIZE, chosen.length)} of ${chosen.length}…`);
        const batch = chosen.slice(i, i + BATCH_SIZE).map(({ name, phone }) => ({ name, phone }));
        added += (await syncContacts(token, batch)).added;
      }
      onDone(`${added} contact${added === 1 ? '' : 's'} added.`);
    });

  return (
    <View style={styles.list}>
      <FlatList
        style={styles.list}
        data={shown}
        keyExtractor={(c) => c.key}
        keyboardShouldPersistTaps="handled"
        ListHeaderComponent={
          <>
            <View style={styles.card}>
              <Text style={styles.heading}>Choose contacts from your phone</Text>
              <Text style={styles.hint}>
                Tick the contacts to add to your Remote Phonebook. Ones already added are marked.
              </Text>
              {isWeb ? (
                <>
                  <Text style={styles.hint}>
                    {contactSource === 'phone'
                      ? 'Your browser will open your phonebook so you can choose contacts.'
                      : 'This browser cannot open your phonebook. Export your contacts as a .vcf file (Contacts app → Share / Export) and choose it here.'}
                  </Text>
                  <Button
                    title={contactSource === 'phone' ? 'Open my phonebook' : 'Choose a contacts file (.vcf)'}
                    onPress={openOnWeb}
                    disabled={busy}
                  />
                </>
              ) : null}
              {error ? <Text style={styles.error}>{error}</Text> : null}
              <Link title="Cancel" onPress={onCancel} disabled={busy} />
            </View>
            <TextInput
              style={[styles.input, styles.search]}
              value={query}
              onChangeText={setQuery}
              placeholder="Search your phone's contacts"
              autoCorrect={false}
            />
            {addable.length ? (
              <View style={styles.linkRow}>
                <Link
                  title={`Select all${query ? ' shown' : ''} (${addable.length})`}
                  onPress={() => setSelected((prev) => new Set([...prev, ...addable.map((c) => c.key)]))}
                />
                <Link title="Clear" onPress={() => setSelected(new Set())} />
              </View>
            ) : null}
          </>
        }
        ListEmptyComponent={
          waitingForTap ? undefined : entries === null ? (
            <ActivityIndicator color={COLORS.primary} />
          ) : (
            <Text style={styles.hint}>
              {query ? 'No contacts match.' : isWeb ? 'No contacts to show yet.' : 'No contacts with phone numbers on this phone.'}
            </Text>
          )
        }
        renderItem={({ item }) => {
          const on = selected.has(item.key);
          return (
            <Pressable
              style={styles.row}
              onPress={() => !item.saved && toggle(item.key)}
              disabled={item.saved || busy}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: item.saved || on, disabled: item.saved }}
            >
              <View style={styles.rowText}>
                <Text style={[styles.rowName, item.saved && styles.muted]} numberOfLines={1}>
                  {item.name}
                </Text>
                <Text style={styles.rowPhone}>{item.phone}</Text>
              </View>
              {item.saved ? (
                <Text style={styles.savedTag}>Added</Text>
              ) : (
                <View style={[styles.checkbox, on && styles.checkboxOn]}>{on ? <Text style={styles.checkmark}>✓</Text> : null}</View>
              )}
            </Pressable>
          );
        }}
      />
      <View style={styles.footer}>
        {status ? <Text style={styles.status}>{status}</Text> : null}
        <Button
          title={selected.size ? `Add ${selected.size} contact${selected.size === 1 ? '' : 's'}` : 'Select contacts to add'}
          onPress={upload}
          busy={busy}
          disabled={!selected.size}
        />
      </View>
    </View>
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
  onAddContact,
  onPickContacts,
  onLoginHistory,
  onSessions,
  onLogout,
}: {
  token: string;
  me: Me | null;
  setMe: (me: Me) => void;
  notice: string;
  onChangePin: () => void;
  onChangePassword: () => void;
  onAddContact: () => void;
  onPickContacts: () => void;
  onLoginHistory: () => void;
  onSessions: () => void;
  onLogout: () => void;
}) {
  const ask = useDialog();
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
        setError(isWeb ? 'No contacts were chosen.' : 'Contacts permission is needed. Allow it in Settings → Apps → Remote Phonebook.');
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

  const confirmRemove = (contact: Contact) =>
    ask(contact.name, `${contact.phone}\n\nRemove from your Remote Phonebook? It stays on your phone.`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Remove',
        style: 'destructive',
        onPress: async () => {
          setError('');
          try {
            await deleteContact(token, contact.id);
            setContacts((list) => list && list.filter((c) => c.id !== contact.id));
            setStatus(`${contact.name} removed.`);
          } catch (e) {
            failed(e, 'Could not remove the contact.');
          }
        },
      },
    ]);

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
        <Button title="Choose contacts from phone" onPress={onPickContacts} disabled={busy} />
        <View style={styles.buttonRow}>
          <SecondaryButton title="Add contact" onPress={onAddContact} disabled={busy} />
          <SecondaryButton title="Sync all" onPress={sync} busy={busy} />
        </View>
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
          <Link title="Sessions" onPress={onSessions} disabled={busy} />
          <Link title="Login attempts" onPress={onLoginHistory} disabled={busy} />
          <Link title="Change password" onPress={onChangePassword} disabled={busy} />
          <Link title="Change USSD PIN" onPress={onChangePin} disabled={busy} />
        </View>
      </View>
      <Text style={styles.sectionTitle}>
        Your contacts{contacts ? ` (${contacts.length})` : ''} · ★ {priorityCount}/{maxPriority} priority
      </Text>
      <Text style={styles.hint}>Tap ☆ to pick up to {maxPriority} priority contacts. Tap a contact to remove it.</Text>
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
            {query ? 'No contacts match.' : 'No contacts yet. Choose some from your phone, or add one.'}
          </Text>
        )
      }
      renderItem={({ item }) => (
        <View style={styles.row}>
          <Pressable style={styles.rowText} onPress={() => confirmRemove(item)} accessibilityHint="Remove this contact">
            <Text style={styles.rowName} numberOfLines={1}>
              {item.name}
            </Text>
            <Text style={styles.rowPhone}>{item.phone}</Text>
          </Pressable>
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

function SecondaryButton({
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
    <Pressable style={[styles.secondary, off && styles.buttonOff]} onPress={onPress} disabled={off}>
      {busy ? <ActivityIndicator color={COLORS.primary} /> : <Text style={styles.secondaryText}>{title}</Text>}
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
  screen: { flex: 1, backgroundColor: '#F6F7F6', paddingHorizontal: 16, paddingTop: isWeb ? 24 : 80 },
  column: { flex: 1, width: '100%', maxWidth: 640, alignSelf: 'center' }, // centred on tablets and computers
  title: { fontSize: 26, fontWeight: '700', color: COLORS.text, flexShrink: 1 },
  titleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24, gap: 12 },
  brand: { flexDirection: 'row', alignItems: 'center', gap: 10, flexShrink: 1 },
  logo: { width: 40, height: 40 },
  logout: { color: COLORS.error, fontSize: 16, fontWeight: '600' },
  activeCard: { borderWidth: 2, borderColor: COLORS.primary },
  sessionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    padding: 14,
    marginTop: 8,
    borderRadius: 10,
    gap: 12,
  },
  sessionActive: { borderLeftWidth: 4, borderLeftColor: COLORS.primary },
  activeText: { color: COLORS.primary },
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
  linkRow: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-around', columnGap: 16 },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  nameInput: { flex: 1 },
  showLink: { color: COLORS.primary, fontSize: 15 },
  spacer: { height: 16 },
  buttonRow: { flexDirection: 'row', gap: 12 },
  secondary: {
    flex: 1,
    borderWidth: 1,
    borderColor: COLORS.primary,
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: 'center',
    marginTop: 12,
  },
  secondaryText: { color: COLORS.primary, fontSize: 16, fontWeight: '600' },
  muted: { color: COLORS.muted },
  savedTag: { fontSize: 13, color: COLORS.muted },
  checkbox: {
    width: 26,
    height: 26,
    borderRadius: 6,
    borderWidth: 2,
    borderColor: COLORS.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkboxOn: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  checkmark: { color: '#fff', fontSize: 16, fontWeight: '700' },
  footer: { paddingTop: 8, paddingBottom: 24, backgroundColor: '#F6F7F6' },
  warn: { color: COLORS.error },
  dot: { width: 10, height: 10, borderRadius: 5, marginRight: 12 },
  dotOk: { backgroundColor: COLORS.primary },
  dotBad: { backgroundColor: COLORS.error },
  result: { fontSize: 13, maxWidth: 110, textAlign: 'right' },
  resultOk: { color: COLORS.primary },
  resultBad: { color: COLORS.error, fontWeight: '600' },
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
