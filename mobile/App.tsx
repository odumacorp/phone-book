import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  AppState,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Linking,
  useWindowDimensions,
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
  API_URL,
  ApiError,
  AppNotification,
  Complaint,
  Contact,
  NotificationPrefs,
  EmailAddress,
  EmergencyNumber,
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
  addEmail,
  clearNotifications,
  clearToken,
  getNotifications,
  markNotificationsRead,
  setNotificationPrefs,
  deleteEmail,
  confirmEmailCode,
  getComplaints,
  requestEmailCode,
  sendComplaint,
  deleteContact,
  deleteContacts,
  editContact,
  getContacts,
  getEmergencyNumbers,
  getLogins,
  getSessions,
  getMe,
  getPublicOrgs,
  type PublicOrg,
  getOrg,
  getOrgs,
  type OrgContact,
  type OrgDetail,
  type OrgSummary,
  isValidPhone,
  loadConfig,
  loadToken,
  loginOtp,
  type OtpChallenge,
  ussdCode,
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
  setSmsAll,
  syncContacts,
} from './src/api';
import {
  askContactAccess,
  chooseSharedContacts,
  type ContactAccess,
  contactSource,
  getContactAccess,
  openPhoneSettings,
  readDeviceContacts,
} from './src/deviceContacts';
import { DialogProvider, useDialog } from './src/dialog';
import { isWeb } from './src/platform';

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
  | 'editContact'
  | 'emergency'
  | 'pickContacts'
  | 'verifyEmail'
  | 'addEmail'
  | 'notifications'
  | 'report'
  | 'orgs';

export default function App() {
  return (
    <DialogProvider>
      <Main />
    </DialogProvider>
  );
}

function Main() {
  const ask = useDialog();
  const narrow = useWindowDimensions().width < 480; // phones: logo only, so the header buttons fit
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [view, setView] = useState<View_>('login');
  const [notice, setNotice] = useState('');
  const [booting, setBooting] = useState(true);
  const [editing, setEditing] = useState<Contact | null>(null);
  const [verifying, setVerifying] = useState<EmailAddress | null>(null);

  useEffect(() => {
    (async () => {
      const [saved] = await Promise.all([loadToken(), loadConfig()]);
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
    content = (
      <RegisterScreen onDone={(t, message) => loggedIn(t, message || 'Account created. Tap Sync contacts to upload them.')} onCancel={back} />
    );
  else if (view === 'reset')
    content = <ResetPasswordScreen onDone={(t) => loggedIn(t, 'Password reset. You are logged in.')} onCancel={back} />;
  else if (view === 'report') content = <ReportScreen token={token} onBack={back} onLogout={logout} />;
  else if (!token)
    content = (
      <LoginScreen
        onLogin={loggedIn}
        onRegister={() => setView('register')}
        onForgot={() => setView('reset')}
        onReport={() => setView('report')}
      />
    );
  else if (view === 'verifyEmail' && verifying)
    content = (
      <VerifyEmailScreen token={token} entry={verifying} onDone={() => loggedIn(token, 'Email verified.')} onCancel={back} onLogout={logout} />
    );
  else if (view === 'notifications')
    content = (
      <NotificationsScreen
        token={token}
        onBack={() => {
          back();
          getMe(token).then(setMe).catch(() => null); // refresh the unread count
        }}
        onLogout={logout}
      />
    );
  else if (view === 'addEmail')
    content = (
      <AddEmailScreen
        token={token}
        onDone={(entry) => {
          if (me?.features?.email_verification) {
            setVerifying(entry);
            setView('verifyEmail');
            getMe(token).then(setMe).catch(() => null);
          } else loggedIn(token, `${entry.email} added. You can now log in with it.`);
        }}
        onCancel={back}
        onLogout={logout}
      />
    );
  else if (view === 'changePassword')
    content = <ChangePasswordScreen token={token} onDone={(t) => loggedIn(t, 'Password changed.')} onCancel={back} onLogout={logout} />;
  else if (view === 'logins') content = <LoginHistoryScreen token={token} onBack={back} onLogout={logout} />;
  else if (view === 'sessions') content = <SessionsScreen token={token} onBack={back} onLogout={logout} />;
  else if (view === 'emergency') content = <EmergencyScreen token={token} onBack={back} onLogout={logout} />;
  else if (view === 'orgs') content = <OrganisationsScreen token={token} onBack={back} onLogout={logout} />;
  else if (view === 'addContact')
    content = <AddContactScreen token={token} onDone={(msg) => loggedIn(token, msg)} onCancel={back} onLogout={logout} />;
  else if (view === 'editContact' && editing)
    content = (
      <AddContactScreen token={token} contact={editing} onDone={(msg) => loggedIn(token, msg)} onCancel={back} onLogout={logout} />
    );
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
        onEmergency={() => {
          setNotice('');
          setView('emergency');
        }}
        onOrgs={() => {
          setNotice('');
          setView('orgs');
        }}
        onAddContact={() => {
          setNotice('');
          setView('addContact');
        }}
        onEditContact={(contact) => {
          setNotice('');
          setEditing(contact);
          setView('editContact');
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
        onVerifyEmail={(entry) => {
          setNotice('');
          setVerifying(entry);
          setView('verifyEmail');
        }}
        onAddEmail={() => {
          setNotice('');
          setView('addEmail');
        }}
        onNotifications={() => {
          setNotice('');
          setView('notifications');
        }}
        onReport={() => {
          setNotice('');
          setView('report');
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
          {narrow && token ? null : <Text style={styles.title}>Remote Phonebook</Text>}
        </View>
        {token && !booting && view !== 'register' && view !== 'reset' ? (
          <View style={styles.headerActions}>
            {me?.platform_access ? (
              <Pressable onPress={() => openPlatform(token)} hitSlop={6} accessibilityRole="link">
                <Text style={[styles.headerButton, styles.headerButtonMain]}>Platform</Text>
              </Pressable>
            ) : null}
            <Pressable onPress={() => openSite('/help')} hitSlop={6} accessibilityRole="link">
              <Text style={styles.headerButton}>Help</Text>
            </Pressable>
            <Pressable onPress={confirmLogout} hitSlop={6} accessibilityRole="button">
              <Text style={styles.logout}>Log out</Text>
            </Pressable>
          </View>
        ) : null}
      </View>
        {token && !booting && view === 'sync' ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.quickNav} contentContainerStyle={styles.quickNavIn}>
            {me?.organisations ? (
              <Pressable style={styles.navChip} onPress={() => setView('orgs')} accessibilityRole="button">
                <Text style={styles.navChipText}>🏢 My organisations</Text>
              </Pressable>
            ) : null}
            <Pressable style={styles.navChip} onPress={() => setView('notifications')} accessibilityRole="button">
              <Text style={styles.navChipText}>
                🔔 Notifications{me?.unread_notifications ? ` (${me.unread_notifications})` : ''}
              </Text>
            </Pressable>
            <Pressable style={styles.navChip} onPress={() => setView('emergency')} accessibilityRole="button">
              <Text style={styles.navChipText}>🚨 Emergency</Text>
            </Pressable>
          </ScrollView>
        ) : null}
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
  onReport,
}: {
  onLogin: (token: string) => Promise<void>;
  onRegister: () => void;
  onForgot: () => void;
  onReport: () => void;
}) {
  const [account, setAccount] = useState('');
  const [password, setPassword] = useState('');
  const [challenge, setChallenge] = useState<OtpChallenge | null>(null);
  const [code, setCode] = useState('');
  const { busy, error, setError, run } = useForm();

  const submit = () => {
    if (!isEmail(account) && !isValidPhone(account))
      return setError(`Enter your email, or your phone number as ${PHONE_FORMAT}.`);
    run(async () => {
      const result = await login(account.trim(), password);
      if (typeof result === 'string') return onLogin(result);
      setCode('');
      setChallenge(result); // OTP is on: ask for the code texted to the phone
    });
  };

  if (challenge) {
    return (
      <Form>
        <Text style={styles.hint}>We texted a 6-digit code to {challenge.phone_hint}. Enter it to finish logging in.</Text>
        <CodeField value={code} onChange={setCode} />
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Button
          title="Log in"
          onPress={() => run(async () => onLogin(await loginOtp(challenge.ticket, code.trim())))}
          busy={busy}
          disabled={code.trim().length !== 6}
        />
        <Link title="Start again" onPress={() => { setChallenge(null); setError(''); }} disabled={busy} />
      </Form>
    );
  }

  return (
    <Form>
      <AccountField value={account} onChange={setAccount} />
      <PasswordField label="Password" value={password} onChange={setPassword} />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title="Log in" onPress={submit} busy={busy} disabled={!account.trim() || !password} />
      <Link title="Forgot password?" onPress={onForgot} disabled={busy} />
      <Link title="New here? Create an account" onPress={onRegister} disabled={busy} />
      <Link title="Report a problem" onPress={onReport} disabled={busy} />
      <Link title="Help and support" onPress={() => openSite('/help')} disabled={busy} />
    </Form>
  );
}

/** Register: name, phone (+ optional email) -> SMS code -> password and USSD PIN. */
function RegisterScreen({ onDone, onCancel }: { onDone: (token: string, message?: string) => void; onCancel: () => void }) {
  const [name, setNameText] = useState('');
  const [joinOrg, setJoinOrg] = useState(false); // false = "Just me" (an individual account)
  const [orgs, setOrgs] = useState<PublicOrg[]>([]);
  const [orgQuery, setOrgQuery] = useState('');
  const [org, setOrg] = useState<PublicOrg | null>(null);
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

  useEffect(() => {
    if (joinOrg && !orgs.length) getPublicOrgs().then((r) => setOrgs(r.orgs)).catch(() => null);
  }, [joinOrg]);

  const orgCode = /^\d{3,6}$/.test(orgQuery.trim()) ? orgQuery.trim() : '';
  const matches = orgQuery.trim()
    ? orgs.filter((o) => o.name.toLowerCase().includes(orgQuery.trim().toLowerCase())).slice(0, 6)
    : orgs.slice(0, 6);

  const sendCodeChecked = () => {
    if (joinOrg && !org && !orgCode) return setError('Choose your organisation, type its code, or choose "Just me".');
    sendCode();
  };

  const finish = () => {
    if (password.length < PASSWORD_MIN) return setError(`Password must be at least ${PASSWORD_MIN} characters.`);
    if (password !== confirmPassword) return setError('Passwords do not match.');
    if (pin !== confirmPin) return setError('PINs do not match.');
    run(async () => {
      const res = await register({
        phone, code, name: name.trim(), email: email.trim(), password, pin,
        ...(joinOrg && org ? { org_id: org.id } : joinOrg && orgCode ? { org_code: orgCode } : {}),
      });
      const o = res.organisation;
      const message = !o
        ? undefined
        : o.status === 'pending'
          ? `Account created. Your request to join ${o.name} was sent: you'll see its notices and contacts once it's approved.`
          : `Account created. You're a member of ${o.name}: see its notices and contacts under My organisations.`;
      onDone(res.token, message);
    });
  };

  return (
    <Form>
      <Text style={styles.heading}>Create an account</Text>
      {!codeSent ? (
        <>
          <Text style={styles.label}>Your name</Text>
          <TextInput placeholderTextColor={COLORS.placeholder}
            style={styles.input}
            value={name}
            onChangeText={setNameText}
            placeholder="e.g. Austin"
            autoComplete="name"
            maxLength={30}
          />
          <Text style={styles.hint}>Added to the end of every SMS you send from {ussdCode()}.</Text>
          <PhoneField value={phone} onChange={setPhone} />
          <Text style={styles.hint}>We will text a 6-digit code to confirm it is yours.</Text>
          <Text style={styles.label}>Email (optional)</Text>
          <TextInput placeholderTextColor={COLORS.placeholder}
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
          <Text style={styles.label}>Are you joining an organisation?</Text>
          <View style={styles.choiceRow}>
            <Pressable style={[styles.choice, !joinOrg && styles.choiceOn]} onPress={() => { setJoinOrg(false); setOrg(null); }} accessibilityRole="radio" accessibilityState={{ checked: !joinOrg }}>
              <Text style={[styles.choiceTitle, !joinOrg && styles.choiceTitleOn]}>Just me</Text>
              <Text style={styles.hintTight}>A personal account</Text>
            </Pressable>
            <Pressable style={[styles.choice, joinOrg && styles.choiceOn]} onPress={() => setJoinOrg(true)} accessibilityRole="radio" accessibilityState={{ checked: joinOrg }}>
              <Text style={[styles.choiceTitle, joinOrg && styles.choiceTitleOn]}>An organisation</Text>
              <Text style={styles.hintTight}>School, SACCO, employer…</Text>
            </Pressable>
          </View>
          {joinOrg ? (
            org ? (
              <View style={styles.pickedOrg}>
                <Text style={styles.rowName}>✓ {org.name}</Text>
                {org.kind ? <Text style={styles.rowPhone}>{org.kind}</Text> : null}
                <Link title="Choose another" onPress={() => setOrg(null)} />
              </View>
            ) : (
              <>
                <TextInput placeholderTextColor={COLORS.placeholder}
                  style={styles.input}
                  value={orgQuery}
                  onChangeText={setOrgQuery}
                  placeholder="Search by name, or type its code"
                  autoCorrect={false}
                />
                {matches.map((o) => (
                  <Pressable key={o.id} style={styles.orgOption} onPress={() => setOrg(o)} accessibilityRole="button">
                    <Text style={styles.rowName}>{o.name}</Text>
                    {o.kind ? <Text style={styles.rowPhone}>{o.kind}</Text> : null}
                  </Pressable>
                ))}
                {orgCode && !matches.length ? (
                  <Text style={styles.hint}>We'll use organisation code {orgCode}.</Text>
                ) : orgQuery.trim() && !matches.length ? (
                  <Text style={styles.hint}>No organisation by that name. Ask your organisation for its code.</Text>
                ) : null}
                <Text style={styles.hint}>Your organisation may need to approve you before you see its notices and contacts.</Text>
              </>
            )
          ) : null}
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button title="Send code" onPress={sendCodeChecked} busy={busy} disabled={!name.trim() || !phone} />
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
          <Text style={styles.hint}>For dialling {ussdCode()}, where you log in with your phone number and this PIN.</Text>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button
            title="Create account"
            onPress={finish}
            busy={busy}
            disabled={code.length !== 6 || !password || !confirmPassword || pin.length !== 4 || confirmPin.length !== 4}
          />
          {joinOrg && (org || orgCode) ? (
            <Text style={styles.hint}>Joining: {org ? org.name : `organisation code ${orgCode}`}</Text>
          ) : null}
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

/** Email verification (when the admin has turned it on): send a code to the account's email, then enter it. */
/** The in-app inbox, and where the user gets notifications (or stops them). */
function NotificationsScreen({ token, onBack, onLogout }: { token: string; onBack: () => void; onLogout: () => void }) {
  const ask = useDialog();
  const [items, setItems] = useState<AppNotification[] | null>(null);
  const [prefs, setPrefs] = useState<NotificationPrefs | null>(null);
  const [keepDays, setKeepDays] = useState(90);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const failed = (e: unknown, fallback: string) => {
    if (e instanceof ApiError && e.status === 401) return onLogout();
    setError(e instanceof Error ? e.message : fallback);
  };

  const load = useCallback(async () => {
    setError('');
    try {
      const inbox = await getNotifications(token);
      setItems(inbox.notifications);
      setPrefs(inbox.prefs);
      setKeepDays(inbox.keep_days);
      if (inbox.unread) markNotificationsRead(token).catch(() => null); // seen now; still shown as new this time
    } catch (e) {
      failed(e, 'Could not load your notifications.');
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const change = async (c: Parameters<typeof setNotificationPrefs>[1]) => {
    setError('');
    try {
      setPrefs(await setNotificationPrefs(token, c));
    } catch (e) {
      failed(e, 'Could not save your choice.');
    }
  };

  const clear = () =>
    ask('Clear notifications?', 'This removes every notification from this list.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Clear',
        style: 'destructive',
        onPress: async () => {
          try {
            await clearNotifications(token);
            setItems([]);
          } catch (e) {
            failed(e, 'Could not clear them.');
          }
        },
      },
    ]);

  const anyOn = prefs ? prefs.inapp || prefs.emails.some((e) => e.notify) : true;
  const allOff = prefs ? !prefs.security && !prefs.messages : false;

  const toggle = (label: string, hint: string, value: boolean, onChange: (v: boolean) => void, key: string) => (
    <View style={styles.switchRow} key={key}>
      <View style={styles.switchText}>
        <Text style={styles.switchLabel}>{label}</Text>
        {hint ? <Text style={styles.hint}>{hint}</Text> : null}
      </View>
      <Switch value={value} onValueChange={onChange} trackColor={{ true: COLORS.primary, false: '#CBD3DD' }} thumbColor="#fff" {...SWITCH_WEB} />
    </View>
  );

  return (
    <FlatList
      style={styles.list}
      data={items ?? []}
      keyExtractor={(n) => String(n.id)}
      refreshing={refreshing}
      onRefresh={async () => {
        setRefreshing(true);
        await load();
        setRefreshing(false);
      }}
      ListHeaderComponent={
        <>
          <View style={styles.card}>
            <Text style={styles.heading}>Notifications</Text>
            <Text style={styles.hint}>Security alerts about your account, replies to your reports and messages from the team.</Text>
            {error ? <Text style={styles.error}>{error}</Text> : null}
            {prefs ? (
              <>
                <Text style={[styles.label, { marginTop: 12 }]}>What to get</Text>
                {toggle('Security alerts', 'New logins, password or PIN changes, lockouts', prefs.security, (v) => change({ security: v }), 'security')}
                {toggle('Messages from the team', 'Replies to your reports and announcements', prefs.messages, (v) => change({ messages: v }), 'messages')}
                <Text style={[styles.label, { marginTop: 12 }]}>Where to get them</Text>
                {toggle('In the app', 'This list', prefs.inapp, (v) => change({ inapp: v }), 'inapp')}
                {prefs.emails.map((e) =>
                  toggle(
                    e.email,
                    !prefs.email_available
                      ? 'Email is not available yet'
                      : prefs.verified_only && !e.verified
                        ? 'Verify this email to get notifications here'
                        : 'Email',
                    !!e.notify,
                    (v) => change({ email_id: e.id, notify: v }),
                    `email-${e.id}`,
                  ),
                )}
                {!prefs.emails.length ? <Text style={styles.hint}>Add an email on the home screen to get notifications by email.</Text> : null}
                {allOff || !anyOn ? (
                  <Text style={[styles.status, styles.warn]}>Notifications are off. You won't hear about logins or replies.</Text>
                ) : null}
                <View style={styles.linkRow}>
                  {allOff && !anyOn ? (
                    <Link title="Turn all notifications on" onPress={() => change({ all: true })} />
                  ) : (
                    <Link title="Stop all notifications" onPress={() => change({ all: false })} />
                  )}
                  {items && items.length ? <Link title="Clear list" onPress={clear} /> : null}
                  <Link title="Back" onPress={onBack} />
                </View>
              </>
            ) : (
              <Link title="Back" onPress={onBack} />
            )}
          </View>
          <View style={styles.spacer} />
        </>
      }
      ListEmptyComponent={
        items === null ? (
          <ActivityIndicator color={COLORS.primary} />
        ) : (
          <Text style={styles.hint}>No notifications yet. They are kept for {keepDays} days.</Text>
        )
      }
      renderItem={({ item }) => (
        <View style={styles.row}>
          <View style={[styles.dot, item.read ? styles.dotRead : item.kind === 'security' ? styles.dotBad : styles.dotOk]} />
          <View style={styles.rowText}>
            <Text style={[styles.rowName, !item.read && { fontWeight: '700' }]}>{item.title}</Text>
            <Text style={styles.rowPhone}>{new Date(item.created_at * 1000).toLocaleString()}</Text>
            <Text style={styles.noteBody}>{item.body}</Text>
          </View>
        </View>
      )}
    />
  );
}

/** Add one more email to the account (up to the server's limit). */
function AddEmailScreen({
  token,
  onDone,
  onCancel,
  onLogout,
}: {
  token: string;
  onDone: (entry: EmailAddress) => void;
  onCancel: () => void;
  onLogout: () => void;
}) {
  const [email, setEmail] = useState('');
  const { busy, error, setError, run } = useForm();

  const submit = () => {
    if (!isEmail(email)) return setError('Enter a valid email address.');
    run(async () => {
      try {
        onDone(await addEmail(token, email.trim()));
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return onLogout();
        throw e;
      }
    });
  };

  return (
    <Form>
      <Text style={styles.heading}>Add email</Text>
      <Text style={styles.hint}>You can add up to 3 emails. Any of them can be used to log in.</Text>
      <Text style={styles.label}>Email</Text>
      <TextInput placeholderTextColor={COLORS.placeholder}
        style={styles.input}
        value={email}
        onChangeText={setEmail}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="email-address"
        autoComplete="email"
        placeholder="you@example.com"
        onSubmitEditing={submit}
      />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title="Add email" onPress={submit} busy={busy} disabled={!email.trim()} />
      <Link title="Cancel" onPress={onCancel} disabled={busy} />
    </Form>
  );
}

function VerifyEmailScreen({
  token,
  entry,
  onDone,
  onCancel,
  onLogout,
}: {
  token: string;
  entry: EmailAddress;
  onDone: () => void;
  onCancel: () => void;
  onLogout: () => void;
}) {
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState('');
  const { busy, error, run } = useForm();

  const guard = async (action: () => Promise<void>) =>
    run(async () => {
      try {
        await action();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401 && e.message.startsWith('Please log in')) return onLogout();
        throw e;
      }
    });

  return (
    <Form>
      <Text style={styles.heading}>Verify your email</Text>
      <Text style={styles.hint}>
        We'll email a 6-digit code to {entry.email}. Verified emails can be used to log in and get security alerts.
      </Text>
      {sent ? (
        <>
          <Text style={styles.status}>Code sent. Check your inbox (and spam folder).</Text>
          <CodeField value={code} onChange={setCode} />
        </>
      ) : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {sent ? (
        <>
          <Button title="Verify" onPress={() => guard(async () => { await confirmEmailCode(token, entry.id, code); onDone(); })} busy={busy} disabled={code.length !== 6} />
          <Link title="Send a new code" onPress={() => guard(async () => { await requestEmailCode(token, entry.id); })} disabled={busy} />
        </>
      ) : (
        <Button title="Send code" onPress={() => guard(async () => { await requestEmailCode(token, entry.id); setSent(true); })} busy={busy} />
      )}
      <Link title="Cancel" onPress={onCancel} disabled={busy} />
    </Form>
  );
}

/** Report a problem to the admin. Logged-in users also see their earlier reports and the replies. */
function ReportScreen({ token, onBack, onLogout }: { token: string | null; onBack: () => void; onLogout: () => void }) {
  const [message, setMessage] = useState('');
  const [contact, setContact] = useState('');
  const [sentNote, setSentNote] = useState('');
  const [reports, setReports] = useState<Complaint[] | null>(null);
  const { busy, error, setError, run } = useForm();

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setReports((await getComplaints(token)).complaints);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onLogout();
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const submit = () => {
    if (message.trim().length < 5) return setError('Describe the problem in a few words.');
    if (!token && !isEmail(contact) && !isValidPhone(contact))
      return setError(`Enter an email, or a phone number as ${PHONE_FORMAT}, so we can reply.`);
    run(async () => {
      await sendComplaint(message.trim(), contact.trim(), token);
      setMessage('');
      setSentNote(token ? 'Thanks, your report was sent. The reply will appear below.' : 'Thanks, your report was sent. We will contact you.');
      await load();
    });
  };

  return (
    <Form>
      <Text style={styles.heading}>Report a problem</Text>
      <Text style={styles.hint}>Tell us what went wrong or what you'd like changed. The team reads every report.</Text>
      <Text style={styles.label}>What happened?</Text>
      <TextInput placeholderTextColor={COLORS.placeholder}
        style={[styles.input, { minHeight: 96, textAlignVertical: 'top' }]}
        value={message}
        onChangeText={setMessage}
        multiline
        maxLength={1000}
        placeholder="e.g. USSD search doesn't find my contact"
      />
      {!token ? (
        <>
          <Text style={styles.label}>Your phone number or email (so we can reply)</Text>
          <TextInput placeholderTextColor={COLORS.placeholder}
            style={styles.input}
            value={contact}
            onChangeText={setContact}
            autoCapitalize="none"
            keyboardType="email-address"
            placeholder="07XX XXX XXX or you@example.com"
          />
        </>
      ) : null}
      {sentNote ? <Text style={styles.status}>{sentNote}</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title="Send report" onPress={submit} busy={busy} disabled={!message.trim()} />
      <Link title="Back" onPress={onBack} disabled={busy} />
      {token && reports && reports.length ? (
        <>
          <Text style={[styles.label, { marginTop: 16 }]}>Your reports</Text>
          {reports.map((r) => (
            <View key={r.id} style={{ paddingVertical: 8, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: COLORS.border }}>
              <Text style={styles.hint}>
                {new Date(r.created_at * 1000).toLocaleString()} · {r.status === 'resolved' ? 'Resolved' : 'Open'}
              </Text>
              <Text style={styles.rowName}>{r.message}</Text>
              {r.reply ? <Text style={styles.status}>Reply: {r.reply}</Text> : null}
            </View>
          ))}
        </>
      ) : null}
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
      <Text style={styles.hint}>The PIN you use when dialling {ussdCode()}. Forgot it? Just set a new one here.</Text>
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
  resumed: 'Continued (no PIN)',
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

/** 40 -> "40 seconds", 60 -> "1 minute", 180 -> "3 minutes" */
function timeoutText(seconds: number) {
  if (seconds % 60) return `${seconds} second${seconds === 1 ? '' : 's'}`;
  const m = seconds / 60;
  return `${m} minute${m === 1 ? '' : 's'}`;
}

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
        { title: `USSD (${ussdCode()})`, items: data.ussd.map((s) => ({ kind: 'ussd' as const, s })) },
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
              USSD sessions end after {timeoutText(data.timeout)}{' '}
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
              Every time someone logs in to your phone book by dialling {ussdCode()}, or tries to. Kept for {keepDays} days.
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

/** Adds a contact, or edits one when `contact` is given (and offers to remove it). */
function AddContactScreen({
  token,
  contact,
  onDone,
  onCancel,
  onLogout,
}: {
  token: string;
  contact?: Contact;
  onDone: (message: string) => void;
  onCancel: () => void;
  onLogout: () => void;
}) {
  const ask = useDialog();
  const [name, setNameText] = useState(contact?.name ?? '');
  const [phone, setPhone] = useState(contact?.phone ?? '');
  const { busy, error, setError, run } = useForm();
  const unchanged = !!contact && name.trim() === contact.name && phone.trim() === contact.phone;

  const submit = () => {
    if (!name.trim()) return setError('Enter a name.');
    if (!normalizePhone(phone)) return setError('Enter a valid phone number, e.g. 0712 345 678 or +965 6674 4067.');
    run(async () => {
      try {
        if (contact) {
          const saved = await editContact(token, contact.id, name.trim(), phone);
          onDone(`${saved.name} saved.`);
        } else {
          const saved = await addContact(token, name.trim(), phone);
          onDone(`${saved.name} added.`);
        }
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return onLogout();
        throw e;
      }
    });
  };

  const confirmRemove = () =>
    contact &&
    ask(contact.name, `${contact.phone}\n\nRemove from your Remote Phonebook? It stays on your phone.`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Remove',
        style: 'destructive',
        onPress: () =>
          run(async () => {
            try {
              await deleteContact(token, contact.id);
              onDone(`${contact.name} removed.`);
            } catch (e) {
              if (e instanceof ApiError && e.status === 401) return onLogout();
              throw e;
            }
          }),
      },
    ]);

  return (
    <Form>
      <Text style={styles.heading}>{contact ? 'Edit contact' : 'Add contact'}</Text>
      <Text style={styles.label}>Name</Text>
      <TextInput placeholderTextColor={COLORS.placeholder} style={styles.input} value={name} onChangeText={setNameText} placeholder="e.g. Mama Mboga" maxLength={60} />
      <Text style={styles.label}>Phone number</Text>
      <TextInput placeholderTextColor={COLORS.placeholder}
        style={styles.input}
        value={phone}
        onChangeText={setPhone}
        placeholder="0712 345 678"
        keyboardType="phone-pad"
      />
      <Text style={styles.hint}>Kenyan numbers can start with 0; other countries need the + code. Service numbers like 999 or 1199 work too.</Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button
        title={contact ? 'Save changes' : 'Add contact'}
        onPress={submit}
        busy={busy}
        disabled={!name.trim() || !phone.trim() || unchanged}
      />
      {contact ? (
        <Pressable style={styles.removeButton} onPress={confirmRemove} disabled={busy} accessibilityRole="button">
          <Text style={styles.removeButtonText}>Remove contact</Text>
        </Pressable>
      ) : null}
      <Link title="Cancel" onPress={onCancel} disabled={busy} />
      {contact ? null : <ContactAccessSettings />}
    </Form>
  );
}

const ACCESS_TEXT: Record<ContactAccess, string> = {
  all: 'Remote Phonebook can read all the contacts on this phone. Sync all uploads every one; Choose contacts from phone lets you pick.',
  limited: 'Remote Phonebook can only see the contacts you selected. Change the selection, or allow all contacts in phone settings.',
  denied: 'Contacts access is off, so the app cannot read your phonebook. Turn it on in phone settings.',
  unasked: 'Allow access to your phonebook so you can sync or choose contacts.',
};

/**
 * Shows what the phone lets the app read from the phonebook, with buttons to change it:
 * allow all contacts (for Sync all) or keep only selected ones. onChange runs after a change.
 */
function ContactAccessSettings({ onChange }: { onChange?: () => void }) {
  const [access, setAccess] = useState<ContactAccess | null>(null);

  const refresh = useCallback(async () => {
    const next = await getContactAccess().catch(() => null);
    setAccess((prev) => {
      if (prev && next && prev !== next) onChange?.();
      return next;
    });
  }, [onChange]);

  useEffect(() => {
    refresh();
    // Coming back from the phone's Settings app: show the new setting
    const sub = AppState.addEventListener('change', (state) => state === 'active' && refresh());
    return () => sub.remove();
  }, [refresh]);

  if (isWeb) {
    return (
      <Text style={styles.hint}>
        The website asks for contacts each time you choose them. To let Remote Phonebook read all your phone's contacts or only selected
        ones, use the Remote Phonebook app.
      </Text>
    );
  }
  if (!access) return null;

  const settingsHint =
    Platform.OS === 'ios' ? 'In Settings → Remote Phonebook → Contacts, choose Full Access or Limited Access.' : 'In Settings → Permissions → Contacts, choose Allow or Don\'t allow.';

  return (
    <View style={styles.accessBox}>
      <Text style={styles.switchLabel}>Phone contacts access</Text>
      <Text style={styles.hint}>{ACCESS_TEXT[access]}</Text>
      {access === 'unasked' ? (
        <SecondaryButton title="Allow contacts access" onPress={() => askContactAccess().then(refresh)} />
      ) : (
        <>
          {access === 'limited' ? (
            <SecondaryButton title="Choose which contacts" onPress={() => chooseSharedContacts().then(() => onChange?.(), () => {})} />
          ) : null}
          <SecondaryButton title="Change in phone settings" onPress={() => openPhoneSettings().catch(() => {})} />
          <Text style={styles.hint}>{settingsHint}</Text>
        </>
      )}
    </View>
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

  // Contacts access changed (e.g. more contacts shared in phone settings): read the phonebook again
  const reload = useCallback(() => {
    setEntries(null);
    load(readDeviceContacts());
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
              {isWeb ? null : <ContactAccessSettings onChange={reload} />}
              <Link title="Cancel" onPress={onCancel} disabled={busy} />
            </View>
            <TextInput placeholderTextColor={COLORS.placeholder}
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

/** Opens the phone's dialler with the number (the user presses call). Works in the app and the website. */
/** "24 Nov 2026, 15:10" (today: "today, 15:10"). */
function formatWhen(seconds: number) {
  const d = new Date(seconds * 1000);
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === new Date().toDateString()) return `today, ${time}`;
  return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}, ${time}`;
}

/** One labelled fact on the profile card ("Title: Bursar"). */
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.fact}>
      <Text style={styles.factLabel}>{label}</Text>
      <Text style={styles.factValue}>{value}</Text>
    </View>
  );
}

/** Open a page of the Remote Phonebook website (/platform, /help): same tab on the website, the browser in the app. */
function openSite(path: string) {
  if (isWeb) {
    window.location.href = path;
    return;
  }
  Linking.openURL(API_URL + path).catch(() => {});
}

/** "Manage organisation": on the website, reuse this login for /platform; in the app, open it in the browser. */
async function openPlatform(token: string) {
  if (isWeb) {
    await fetch('/platform/app-login', { method: 'POST', headers: { Authorization: `Bearer ${token}` } }).catch(() => null);
    window.location.href = '/platform';
    return;
  }
  Linking.openURL(API_URL + '/platform').catch(() => {});
}

function callNumber(phone: string) {
  const url = `tel:${phone.replace(/[^\d+]/g, '')}`;
  if (isWeb) {
    window.location.href = url; // same page: mobile browsers go straight to the dialler (a new tab can stay blank)
    return;
  }
  Linking.openURL(url).catch(() => {});
}

/** Free emergency and help lines: call straight from the phone, or add to the contacts. */
/** The user's organisations (school, SACCO, employer...): their notices, emergency contacts and contacts. */
function OrganisationsScreen({ token, onBack, onLogout }: { token: string; onBack: () => void; onLogout: () => void }) {
  const [orgs, setOrgs] = useState<OrgSummary[] | null>(null);
  const [detail, setDetail] = useState<OrgDetail | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [saved, setSaved] = useState<Set<string>>(new Set());

  const fail = (e: unknown, fallback: string) => {
    if (e instanceof ApiError && e.status === 401) return onLogout();
    setError(e instanceof Error ? e.message : fallback);
  };

  const open = useCallback(
    async (id: number) => {
      setError('');
      setNotice('');
      try {
        setDetail(await getOrg(token, id));
      } catch (e) {
        fail(e, 'Could not load this organisation.');
      }
    },
    [token],
  );

  useEffect(() => {
    (async () => {
      try {
        const list = (await getOrgs(token)).orgs;
        setOrgs(list);
        if (list.length === 1) open(list[0].id); // only one: open it straight away
      } catch (e) {
        fail(e, 'Could not load your organisations.');
      }
    })();
  }, [token, open]);

  const save = async (c: OrgContact) => {
    setError('');
    try {
      await addContact(token, c.name, c.phone);
      setNotice(`${c.name} added to your contacts.`);
      setSaved((s) => new Set(s).add(c.phone));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setSaved((s) => new Set(s).add(c.phone));
        return setNotice(e.message);
      }
      fail(e, 'Could not add the contact.');
    }
  };

  const contactRow = (c: OrgContact, urgent = false) => (
    <View key={c.id} style={styles.sessionRow}>
      <View style={styles.rowText}>
        <Text style={styles.rowName}>{c.name}</Text>
        <Text style={styles.rowPhone}>
          {c.title ? `${c.title} · ` : ''}
          {c.phone.startsWith('+254') ? '0' + c.phone.slice(4) : c.phone}
        </Text>
      </View>
      {saved.has(c.phone) ? (
        <Text style={styles.savedTag}>Saved</Text>
      ) : (
        <Pressable onPress={() => save(c)} hitSlop={8} accessibilityRole="button" accessibilityLabel={`Save ${c.name} to my contacts`}>
          <Text style={styles.showLink}>Save</Text>
        </Pressable>
      )}
      <Pressable
        style={[styles.callButton, !urgent && styles.callButtonCalm]}
        onPress={() => callNumber(c.phone)}
        accessibilityRole="button"
        accessibilityLabel={`Call ${c.name}`}
      >
        <Text style={styles.callButtonText}>Call</Text>
      </Pressable>
    </View>
  );

  const header = (
    <View style={styles.card}>
      <Text style={styles.heading}>{detail ? detail.org.name : 'My organisations'}</Text>
      <Text style={styles.hint}>
        {detail
          ? `Notices and contacts from ${detail.org.name}. Also on ${ussdCode()}: choose 7. My organisations.`
          : 'Organisations you belong to, such as your school, SACCO or employer.'}
      </Text>
      {notice ? <Text style={styles.status}>{notice}</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Link
        title={detail && orgs && orgs.length > 1 ? 'All organisations' : 'Back'}
        onPress={() => (detail && orgs && orgs.length > 1 ? setDetail(null) : onBack())}
      />
    </View>
  );

  if (!detail) {
    return (
      <ScrollView style={styles.list} contentContainerStyle={styles.formScroll}>
        {header}
        {orgs === null ? <ActivityIndicator color={COLORS.primary} /> : null}
        {orgs?.length === 0 ? <Text style={styles.hint}>You are not in any organisation yet.</Text> : null}
        {orgs && orgs.length > 1
          ? orgs.map((o) => (
              <Pressable key={o.id} style={styles.sessionRow} onPress={() => open(o.id)} accessibilityRole="button">
                <View style={styles.rowText}>
                  <Text style={styles.rowName}>{o.name}</Text>
                  <Text style={styles.rowPhone}>
                    {o.notices ? `${o.notices} notice${o.notices === 1 ? '' : 's'}` : 'No notices'}
                    {o.urgent ? ` · ${o.urgent} urgent` : ''}
                  </Text>
                </View>
                <Text style={styles.showLink}>Open</Text>
              </Pressable>
            ))
          : null}
      </ScrollView>
    );
  }

  const groups = new Map<string, OrgContact[]>();
  for (const c of detail.contacts) {
    const g = c.group || 'Contacts';
    groups.set(g, [...(groups.get(g) ?? []), c]);
  }

  return (
    <ScrollView style={styles.list} contentContainerStyle={styles.formScroll}>
      {header}
      <Text style={styles.sectionTitle}>Notices</Text>
      {detail.notices.length === 0 ? <Text style={styles.hint}>No notices right now.</Text> : null}
      {detail.notices.map((n) => (
        <View key={n.id} style={[styles.card, n.urgent && styles.urgentCard]}>
          <Text style={styles.noticeTitle}>
            {n.urgent ? 'URGENT: ' : ''}
            {n.title}
          </Text>
          <Text style={styles.noticeBody}>{n.body}</Text>
          <Text style={styles.rowPhone}>
            {new Date(n.at * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}
          </Text>
        </View>
      ))}
      {detail.emergency.length ? <Text style={styles.sectionTitle}>Emergency contacts</Text> : null}
      {detail.emergency.map((c) => contactRow(c, true))}
      {[...groups.entries()].map(([g, list]) => (
        <View key={g}>
          <Text style={styles.sectionTitle}>{g}</Text>
          {list.map((c) => contactRow(c))}
        </View>
      ))}
    </ScrollView>
  );
}

function EmergencyScreen({ token, onBack, onLogout }: { token: string; onBack: () => void; onLogout: () => void }) {
  const [numbers, setNumbers] = useState<EmergencyNumber[] | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    try {
      setNumbers((await getEmergencyNumbers(token)).numbers);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onLogout();
      setError(e instanceof Error ? e.message : 'Could not load the emergency numbers.');
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const add = async (n: EmergencyNumber) => {
    setError('');
    try {
      await addContact(token, n.label, n.number);
      setNotice(`${n.label} (${n.number}) added to your contacts.`);
      setNumbers((list) => list && list.map((x) => (x.number === n.number ? { ...x, saved: true } : x)));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onLogout();
      if (e instanceof ApiError && e.status === 409) {
        setNumbers((list) => list && list.map((x) => (x.number === n.number ? { ...x, saved: true } : x)));
        return setNotice(e.message);
      }
      setError(e instanceof Error ? e.message : 'Could not add the number.');
    }
  };

  return (
    <FlatList
      style={styles.list}
      data={numbers ?? []}
      keyExtractor={(n) => n.number}
      ListHeaderComponent={
        <View style={styles.card}>
          <Text style={styles.heading}>Emergency numbers</Text>
          <Text style={styles.hint}>
            Free to call. Call opens your phone&apos;s dialler, so the call goes straight from your phone. On USSD, choose 2 on
            the main menu.
          </Text>
          {notice ? <Text style={styles.status}>{notice}</Text> : null}
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Link title="Back" onPress={onBack} />
        </View>
      }
      ListEmptyComponent={numbers === null ? <ActivityIndicator color={COLORS.primary} /> : undefined}
      renderItem={({ item }) => (
        <View style={styles.sessionRow}>
          <View style={styles.rowText}>
            <Text style={styles.emergencyNumber}>{item.number}</Text>
            <Text style={styles.rowPhone}>{item.label}</Text>
          </View>
          {item.saved ? (
            <Text style={styles.savedTag}>In contacts</Text>
          ) : (
            <Pressable onPress={() => add(item)} hitSlop={8} accessibilityRole="button">
              <Text style={styles.showLink}>Add</Text>
            </Pressable>
          )}
          <Pressable
            style={styles.callButton}
            onPress={() => callNumber(item.number)}
            accessibilityRole="button"
            accessibilityLabel={`Call ${item.number}, ${item.label}`}
          >
            <Text style={styles.callButtonText}>Call</Text>
          </Pressable>
        </View>
      )}
    />
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
  onEmergency,
  onOrgs,
  onAddContact,
  onEditContact,
  onPickContacts,
  onLoginHistory,
  onSessions,
  onVerifyEmail,
  onAddEmail,
  onNotifications,
  onReport,
  onLogout,
}: {
  token: string;
  me: Me | null;
  setMe: (me: Me) => void;
  notice: string;
  onChangePin: () => void;
  onChangePassword: () => void;
  onEmergency: () => void;
  onOrgs: () => void;
  onAddContact: () => void;
  onEditContact: (contact: Contact) => void;
  onPickContacts: () => void;
  onLoginHistory: () => void;
  onSessions: () => void;
  onVerifyEmail: (entry: EmailAddress) => void;
  onAddEmail: () => void;
  onNotifications: () => void;
  onReport: () => void;
  onLogout: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(notice);
  const [error, setError] = useState('');
  const ask = useDialog();
  // Bulk select: tick several contacts, then delete them together
  const [selecting, setSelecting] = useState(false);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [contacts, setContacts] = useState<Contact[] | null>(null);
  const [maxPriority, setMaxPriority] = useState(5);
  const [showOthers, setShowOthersState] = useState(true);
  const [smsAll, setSmsAllState] = useState(true);
  const [query, setQuery] = useState('');
  const [name, setNameText] = useState(me?.name ?? '');
  const [savedName, setSavedName] = useState(me?.name ?? '');

  useEffect(() => {
    setNameText(me?.name ?? '');
    setSavedName(me?.name ?? '');
  }, [me?.name]);

  const removeEmail = (entry: EmailAddress) =>
    ask('Remove email?', `${entry.email} will no longer log in to this account or get alerts.`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Remove',
        style: 'destructive',
        onPress: async () => {
          setError('');
          try {
            await deleteEmail(token, entry.id);
            setMe(await getMe(token));
            setStatus(`${entry.email} removed.`);
          } catch (e) {
            failed(e, 'Could not remove the email.');
          }
        },
      },
    ]);

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
      setSmsAllState(list.sms_all ?? list.show_others);
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

  const stopSelecting = () => {
    setSelecting(false);
    setChosen(new Set());
  };

  const toggleChosen = (id: number) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const confirmDeleteChosen = () => {
    const ids = [...chosen];
    const label = `${ids.length} contact${ids.length === 1 ? '' : 's'}`;
    ask(`Delete ${label}?`, 'They are removed from your Remote Phonebook and USSD. They stay on your phone.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          setBusy(true);
          setError('');
          try {
            const { deleted } = await deleteContacts(token, ids);
            const gone = new Set(deleted);
            setContacts((list) => list && list.filter((c) => !gone.has(c.id)));
            stopSelecting();
            setStatus(`${deleted.length} contact${deleted.length === 1 ? '' : 's'} deleted.`);
          } catch (e) {
            failed(e, 'Could not delete the contacts.');
          } finally {
            setBusy(false);
          }
        },
      },
    ]);
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

  const toggleSmsAll = async (value: boolean) => {
    setSmsAllState(value);
    setError('');
    try {
      await setSmsAll(token, value);
    } catch (e) {
      setSmsAllState(!value);
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

  const initials =
    (savedName || '')
      .split(' ')
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0].toUpperCase())
      .join('') || '👤';
  const localPhone = me?.phone?.startsWith('+254') ? '0' + me.phone.slice(4) : me?.phone;

  const managedBy = me?.contacts_managed_by ?? null; // the organisation provides this person's contacts

  const header = (
    <>
      <View style={[styles.card, styles.profileCard]}>
        <View style={styles.profileTop}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{initials}</Text>
          </View>
          <View style={styles.rowText}>
            <Text style={styles.greeting}>{savedName ? `Hi, ${savedName.split(' ')[0]}` : 'Welcome'}</Text>
            <Text style={styles.rowPhone}>{localPhone ?? '…'}</Text>
            {me?.last_login ? (
              <Text style={styles.lastLogin}>
                Last login {formatWhen(me.last_login.at)} · {me.last_login.how === 'USSD' ? 'USSD from ' : ''}
                {me.last_login.where}
              </Text>
            ) : me ? (
              <Text style={styles.lastLogin}>First login on this account</Text>
            ) : null}
          </View>
        </View>
        {managedBy ? (
          <Text style={styles.managedNote}>📇 Your contacts are provided by {managedBy}.</Text>
        ) : null}
        {(me?.memberships ?? []).map((m) => (
          <View key={m.id} style={styles.membership}>
            <Text style={styles.membershipOrg}>🏢 {m.name}</Text>
            <View style={styles.facts}>
              <Fact label="Role" value={m.roles.join(', ')} />
              {m.title ? <Fact label="Title" value={m.title} /> : null}
              {m.groups.length ? <Fact label={m.groups.length > 1 ? 'Departments and groups' : 'Department or group'} value={m.groups.join(', ')} /> : null}
              {m.member_no ? <Fact label="Number" value={m.member_no} /> : null}
              {m.joined_at ? <Fact label="Member since" value={new Date(m.joined_at * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })} /> : null}
            </View>
          </View>
        ))}
        <Text style={styles.label}>Your name (signs the SMS you send)</Text>
        <View style={styles.nameRow}>
          <TextInput placeholderTextColor={COLORS.placeholder}
            style={[styles.input, styles.nameInput]}
            value={name}
            onChangeText={setNameText}
            placeholder="Add your name"
            autoComplete="name"
            maxLength={30}
          />
          {name.trim() && name.trim() !== savedName ? <Link title="Save" onPress={saveName} /> : null}
        </View>
        <Text style={styles.label}>Emails (log in with any of them)</Text>
        {(me?.emails ?? []).map((e) => (
          <View key={e.id} style={styles.emailRow}>
            <Text style={styles.emailText} numberOfLines={1}>
              {e.email}
              {me?.features?.email_verification ? (e.verified ? '  ✓ verified' : '  (not verified)') : ''}
            </Text>
            {me?.features?.email_verification && !e.verified ? (
              <Pressable onPress={() => onVerifyEmail(e)} hitSlop={8} accessibilityRole="button">
                <Text style={styles.callLink}>Verify</Text>
              </Pressable>
            ) : null}
            <Pressable onPress={() => removeEmail(e)} hitSlop={8} accessibilityRole="button">
              <Text style={styles.removeLink}>Remove</Text>
            </Pressable>
          </View>
        ))}
        {(me?.emails?.length ?? 0) < (me?.max_emails ?? 3) ? (
          <View style={styles.buttonRow}>
            <SecondaryButton title="Add email" onPress={onAddEmail} disabled={busy} />
          </View>
        ) : (
          <Text style={styles.hint}>You've added the most emails allowed ({me?.max_emails ?? 3}). Remove one to add another.</Text>
        )}
      </View>

      <View style={styles.tiles}>
        <Pressable style={[styles.tile, styles.tileDanger]} onPress={onEmergency} accessibilityRole="button">
          <Text style={styles.tileIcon}>🚨</Text>
          <Text style={[styles.tileTitle, styles.tileDangerText]}>Emergency numbers</Text>
          <Text style={styles.tileHint}>999, 112, Red Cross…</Text>
        </Pressable>
        {me?.organisations ? (
          <Pressable style={[styles.tile, styles.tileAccent]} onPress={onOrgs} accessibilityRole="button">
            <Text style={styles.tileIcon}>🏢</Text>
            <Text style={styles.tileTitle}>My organisations{me.organisations > 1 ? ` (${me.organisations})` : ''}</Text>
            <Text style={styles.tileHint}>Notices and contacts</Text>
          </Pressable>
        ) : null}
        {me?.platform_access ? (
          <Pressable style={[styles.tile, styles.tileAccent]} onPress={() => openPlatform(token)} accessibilityRole="link">
            <Text style={styles.tileIcon}>🛠️</Text>
            <Text style={styles.tileTitle}>Manage organisation</Text>
            <Text style={styles.tileHint}>Post notices, add contacts and members</Text>
          </Pressable>
        ) : null}
        {managedBy ? null : (
        <>
        <Pressable style={[styles.tile, busy && styles.buttonOff]} onPress={onPickContacts} disabled={busy} accessibilityRole="button">
          <Text style={styles.tileIcon}>📲</Text>
          <Text style={styles.tileTitle}>Choose from phone</Text>
          <Text style={styles.tileHint}>Back up the contacts you pick</Text>
        </Pressable>
        <Pressable style={[styles.tile, busy && styles.buttonOff]} onPress={onAddContact} disabled={busy} accessibilityRole="button">
          <Text style={styles.tileIcon}>➕</Text>
          <Text style={styles.tileTitle}>Add contact</Text>
          <Text style={styles.tileHint}>Type a name and number</Text>
        </Pressable>
        <Pressable style={[styles.tile, busy && styles.buttonOff]} onPress={sync} disabled={busy} accessibilityRole="button">
          {busy ? <ActivityIndicator color={COLORS.primary} /> : <Text style={styles.tileIcon}>🔄</Text>}
          <Text style={styles.tileTitle}>Sync all</Text>
          <Text style={styles.tileHint}>Back up every phone contact</Text>
        </Pressable>
        </>
        )}
      </View>
      {status ? <Text style={styles.status}>{status}</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Text style={styles.sectionTitle}>On USSD ({ussdCode()})</Text>
      <View style={styles.card}>
        <View style={[styles.switchRow, styles.switchFirst]}>
          <View style={styles.switchText}>
            <Text style={styles.switchLabel}>Show other contacts</Text>
            <Text style={styles.hintTight}>
              {showOthers ? 'All your contacts, priority ones first.' : `Only your ${maxPriority} priority contacts.`}
            </Text>
          </View>
          <Switch value={showOthers} onValueChange={toggleShowOthers} trackColor={{ true: COLORS.primary, false: '#CBD3DD' }} thumbColor="#fff" {...SWITCH_WEB} />
        </View>
        <View style={styles.switchRow}>
          <View style={styles.switchText}>
            <Text style={styles.switchLabel}>"SMS me my contacts" sends all</Text>
            <Text style={styles.hintTight}>
              {smsAll
                ? 'Texts all your contacts to the phone you dialled from.'
                : `Texts only your ${maxPriority} priority contacts.`}
            </Text>
          </View>
          <Switch value={smsAll} onValueChange={toggleSmsAll} trackColor={{ true: COLORS.primary, false: '#CBD3DD' }} thumbColor="#fff" {...SWITCH_WEB} />
        </View>
      </View>

      <Text style={styles.sectionTitle}>Account and security</Text>
      <View style={[styles.card, styles.menuCard]}>
        {[
          {
            title: 'Notifications',
            badge: me?.unread_notifications ? `${me.unread_notifications} new` : '',
            onPress: onNotifications,
          },
          { title: 'Where you are logged in', badge: '', onPress: onSessions },
          { title: 'Login attempts', badge: '', onPress: onLoginHistory },
          { title: 'Change password', badge: '', onPress: onChangePassword },
          { title: 'Change USSD PIN', badge: '', onPress: onChangePin },
          { title: 'Report a problem', badge: '', onPress: onReport },
          { title: 'Help and support', badge: '', onPress: () => openSite('/help') },
        ].map((item, i) => (
          <Pressable
            key={item.title}
            style={[styles.menuRow, i === 0 && styles.menuRowFirst]}
            onPress={item.onPress}
            disabled={busy}
            accessibilityRole="button"
          >
            <Text style={styles.menuText}>{item.title}</Text>
            {item.badge ? <Text style={styles.badge}>{item.badge}</Text> : null}
            <Text style={styles.chevron}>›</Text>
          </Pressable>
        ))}
      </View>

      <Text style={styles.sectionTitle}>
        Your contacts{contacts ? ` (${contacts.length})` : ''} · ★ {priorityCount}/{maxPriority} priority
      </Text>
      <Text style={styles.hint}>
        {managedBy
          ? `Your contacts are provided by ${managedBy}. Ask ${managedBy} to add the numbers you need. Tap ☆ to pick up to ${maxPriority} priority contacts.`
          : selecting
            ? 'Tick the contacts to delete.'
            : `Tap ☆ to pick up to ${maxPriority} priority contacts. Tap a contact to edit or remove it. To add several at once, use Choose contacts from phone.`}
      </Text>
      <TextInput placeholderTextColor={COLORS.placeholder}
        style={[styles.input, styles.search]}
        value={query}
        onChangeText={setQuery}
        placeholder="Search name or number"
        autoCorrect={false}
      />
      {contacts?.length && !managedBy ? (
        <View style={styles.linkRow}>
          {selecting ? (
            <>
              <Link
                title={`Select all${query ? ' shown' : ''} (${visible.length})`}
                onPress={() => setChosen((prev) => new Set([...prev, ...visible.map((c) => c.id)]))}
              />
              <Link title="Clear" onPress={() => setChosen(new Set())} />
              <Link title="Done" onPress={stopSelecting} />
            </>
          ) : (
            <>
              <Link title="Select to delete" onPress={() => setSelecting(true)} disabled={busy} />
              <Link title="Add from phone" onPress={onPickContacts} disabled={busy} />
            </>
          )}
        </View>
      ) : null}
    </>
  );

  return (
    <View style={styles.list}>
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
              {query ? 'No contacts match.' : managedBy ? `No contacts yet. ${managedBy} will add the numbers you need.` : 'No contacts yet. Choose some from your phone, or add one.'}
            </Text>
          )
        }
        renderItem={({ item }) => {
          if (selecting) {
            const on = chosen.has(item.id);
            return (
              <Pressable
                style={styles.row}
                onPress={() => toggleChosen(item.id)}
                disabled={busy}
                accessibilityRole="checkbox"
                accessibilityState={{ checked: on }}
              >
                <View style={styles.rowText}>
                  <Text style={styles.rowName} numberOfLines={1}>
                    {item.name}
                  </Text>
                  <Text style={styles.rowPhone}>{item.phone}</Text>
                </View>
                <View style={[styles.checkbox, on && styles.checkboxOn]}>{on ? <Text style={styles.checkmark}>✓</Text> : null}</View>
              </Pressable>
            );
          }
          return (
            <View style={styles.row}>
              <Pressable style={styles.rowText} onPress={() => !managedBy && onEditContact(item)} disabled={!!managedBy} accessibilityHint="Edit or remove this contact">
                <Text style={styles.rowName} numberOfLines={1}>
                  {item.name}
                </Text>
                <Text style={styles.rowPhone}>{item.phone}</Text>
              </Pressable>
              <Pressable onPress={() => callNumber(item.phone)} hitSlop={8} accessibilityRole="button" accessibilityLabel={`Call ${item.name}`}>
                <Text style={styles.callLink}>Call</Text>
              </Pressable>
              {managedBy ? null : (
                <Pressable onPress={() => onEditContact(item)} hitSlop={8} accessibilityRole="button" accessibilityLabel={`Edit ${item.name}`}>
                  <Text style={styles.callLink}>Edit</Text>
                </Pressable>
              )}
              <Pressable
                onPress={() => togglePriority(item)}
                hitSlop={12}
                accessibilityRole="button"
                accessibilityLabel={item.priority ? `Remove ${item.name} from priority` : `Make ${item.name} a priority`}
              >
                <Text style={[styles.star, item.priority && styles.starOn]}>{item.priority ? '★' : '☆'}</Text>
              </Pressable>
            </View>
          );
        }}
      />
      {selecting ? (
        <View style={styles.footer}>
          <Pressable
            style={[styles.removeButton, (!chosen.size || busy) && styles.buttonOff]}
            onPress={confirmDeleteChosen}
            disabled={!chosen.size || busy}
            accessibilityRole="button"
          >
            <Text style={styles.removeButtonText}>
              {chosen.size ? `Delete ${chosen.size} contact${chosen.size === 1 ? '' : 's'}` : 'Select contacts to delete'}
            </Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

function PhoneField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <>
      <Text style={styles.label}>Phone number</Text>
      <TextInput placeholderTextColor={COLORS.placeholder}
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
      <TextInput placeholderTextColor={COLORS.placeholder}
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
        <TextInput placeholderTextColor={COLORS.placeholder}
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
      <TextInput placeholderTextColor={COLORS.placeholder}
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
      <TextInput placeholderTextColor={COLORS.placeholder}
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

// Light and calm, in the blue of the Oduma Corp logo (the same palette as the /platform portal)
const COLORS = {
  primary: '#1F6FEB',
  primarySoft: '#EAF2FF',
  text: '#1F2A37',
  muted: '#6B7785',
  border: '#E3E8EF',
  error: '#C0362C',
  errorSoft: '#FDECEB',
  bg: '#F6F8FB',
  placeholder: '#9AA5B1',
};
// react-native-web colours the "on" knob teal unless told otherwise
const SWITCH_WEB = (isWeb ? { activeThumbColor: '#fff' } : {}) as object;
const SHADOW = isWeb
  ? ({ boxShadow: '0 1px 2px rgba(16,24,40,.04), 0 2px 8px rgba(16,24,40,.05)' } as object)
  : { shadowColor: '#101828', shadowOpacity: 0.06, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 1 };

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: COLORS.bg, paddingHorizontal: 16, paddingTop: isWeb ? 28 : 80 },
  column: { flex: 1, width: '100%', maxWidth: 640, alignSelf: 'center' }, // centred on tablets and computers
  title: { fontSize: 22, fontWeight: '700', color: COLORS.text, flexShrink: 1 },
  titleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24, gap: 12 },
  brand: { flexDirection: 'row', alignItems: 'center', gap: 10, flexShrink: 1 },
  logo: { width: 36, height: 36 },
  logout: { color: COLORS.muted, fontSize: 15, fontWeight: '600', paddingVertical: 6, paddingHorizontal: 12, borderRadius: 10, borderWidth: 1, borderColor: COLORS.border, backgroundColor: '#fff' },
  activeCard: { borderWidth: 2, borderColor: COLORS.primary },
  sessionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    padding: 14,
    marginTop: 8,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: COLORS.border,
    gap: 12,
  },
  sessionActive: { borderLeftWidth: 4, borderLeftColor: COLORS.primary },
  emergencyButton: {
    backgroundColor: COLORS.error,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: 'center',
    marginTop: 8,
  },
  emergencyButtonText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  orgButton: { backgroundColor: COLORS.primarySoft, borderRadius: 12, paddingVertical: 12, alignItems: 'center' },
  orgButtonText: { color: COLORS.primary, fontSize: 16, fontWeight: '700' },
  urgentCard: { borderLeftWidth: 4, borderLeftColor: COLORS.error },
  noticeTitle: { fontSize: 17, fontWeight: '700', color: COLORS.text },
  noticeBody: { fontSize: 15, color: COLORS.text, lineHeight: 22 },
  callButtonCalm: { backgroundColor: COLORS.primary },
  emergencyNumber: { fontSize: 22, fontWeight: '700', color: COLORS.text },
  callButton: { backgroundColor: COLORS.error, borderRadius: 10, paddingVertical: 9, paddingHorizontal: 18 },
  callButtonText: { color: '#fff', fontSize: 15, fontWeight: '700' },
  callLink: { color: COLORS.primary, fontSize: 15, fontWeight: '600', marginRight: 16 },
  activeText: { color: COLORS.primary },
  heading: { fontSize: 20, fontWeight: '700', color: COLORS.text },
  card: { backgroundColor: '#fff', borderRadius: 16, padding: 20, gap: 8, borderWidth: 1, borderColor: COLORS.border, ...SHADOW },
  label: { fontSize: 14, color: COLORS.text, fontWeight: '600', marginTop: 8 },
  value: { fontSize: 20, fontWeight: '600', color: COLORS.text, marginBottom: 8 },
  input: {
    borderWidth: 1,
    borderColor: '#D6DCE4',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
    color: COLORS.text,
    backgroundColor: '#fff',
  },
  button: {
    backgroundColor: COLORS.primary,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 12,
  },
  buttonOff: { opacity: 0.5 },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  status: { color: '#17803D', backgroundColor: '#E8F6EE', borderRadius: 12, padding: 12, marginTop: 10, overflow: 'hidden' },
  error: { color: COLORS.error, backgroundColor: COLORS.errorSoft, borderRadius: 12, padding: 12, marginTop: 10, overflow: 'hidden' },
  hint: { color: COLORS.muted, fontSize: 13, marginTop: 12 },
  removeButton: { borderWidth: 1, borderColor: COLORS.error, borderRadius: 10, paddingVertical: 12, alignItems: 'center', marginTop: 4 },
  removeButtonText: { color: COLORS.error, fontSize: 16, fontWeight: '600' },
  accessBox: { borderTopWidth: 1, borderTopColor: COLORS.border, marginTop: 16, paddingTop: 12, gap: 8 },
  link: { color: COLORS.primary, fontSize: 15, fontWeight: '600', marginTop: 16, textAlign: 'center' },
  linkRow: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-around', columnGap: 16 },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  emailRow: { flexDirection: 'row', alignItems: 'center', gap: 16, paddingVertical: 6 },
  dotRead: { backgroundColor: COLORS.border },
  noteBody: { fontSize: 14, color: COLORS.text, marginTop: 4 },
  emailText: { flex: 1, fontSize: 15, color: COLORS.text },
  removeLink: { color: COLORS.error, fontSize: 15, fontWeight: '600' },
  nameInput: { flex: 1 },
  showLink: { color: COLORS.primary, fontSize: 15 },
  spacer: { height: 16 },
  buttonRow: { flexDirection: 'row', gap: 12 },
  secondary: {
    flex: 1,
    borderWidth: 1,
    borderColor: COLORS.border,
    backgroundColor: '#fff',
    borderRadius: 12,
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
  footer: { paddingTop: 8, paddingBottom: 24, backgroundColor: COLORS.bg },
  warn: { color: COLORS.error },
  dot: { width: 10, height: 10, borderRadius: 5, marginRight: 12 },
  dotOk: { backgroundColor: COLORS.primary },
  dotBad: { backgroundColor: COLORS.error },
  result: { fontSize: 13, maxWidth: 110, textAlign: 'right' },
  resultOk: { color: COLORS.primary },
  resultBad: { color: COLORS.error, fontWeight: '600' },
  formScroll: { paddingBottom: 40 },
  list: { flex: 1 },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingTop: 14, marginTop: 6, borderTopWidth: 1, borderTopColor: COLORS.border },
  switchFirst: { borderTopWidth: 0, paddingTop: 0, marginTop: 0 },
  hintTight: { color: COLORS.muted, fontSize: 13, marginTop: 2 },
  switchText: { flex: 1 },
  switchLabel: { fontSize: 16, color: COLORS.text, fontWeight: '600' },
  sectionTitle: { fontSize: 13, fontWeight: '700', color: COLORS.muted, marginTop: 26, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.6 },
  search: { marginTop: 12, marginBottom: 10, backgroundColor: '#fff' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    paddingHorizontal: 16,
    paddingVertical: 13,
    marginBottom: 6,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  rowText: { flex: 1, marginRight: 12 },
  rowName: { fontSize: 16, color: COLORS.text, fontWeight: '600' },
  rowPhone: { fontSize: 14, color: COLORS.muted, marginTop: 2 },
  star: { fontSize: 26, color: COLORS.muted },
  starOn: { color: '#E0A100' },
  profileCard: { gap: 6 },
  managedNote: { fontSize: 14, color: COLORS.primary, backgroundColor: COLORS.primarySoft, borderRadius: 10, padding: 10, overflow: 'hidden' },
  choiceRow: { flexDirection: 'row', gap: 10 },
  choice: { flex: 1, borderWidth: 1, borderColor: COLORS.border, borderRadius: 12, padding: 12, backgroundColor: '#fff' },
  choiceOn: { borderColor: COLORS.primary, backgroundColor: COLORS.primarySoft },
  choiceTitle: { fontSize: 15, fontWeight: '700', color: COLORS.text },
  choiceTitleOn: { color: COLORS.primary },
  orgOption: { borderWidth: 1, borderColor: COLORS.border, borderRadius: 12, paddingVertical: 10, paddingHorizontal: 14, backgroundColor: '#fff' },
  pickedOrg: { borderWidth: 1, borderColor: COLORS.primary, backgroundColor: COLORS.primarySoft, borderRadius: 12, padding: 12 },
  lastLogin: { fontSize: 13, color: COLORS.muted, marginTop: 2 },
  membership: { backgroundColor: COLORS.bg, borderRadius: 12, padding: 12, marginTop: 6, gap: 6 },
  membershipOrg: { fontSize: 15, fontWeight: '700', color: COLORS.text },
  facts: { flexDirection: 'row', flexWrap: 'wrap', columnGap: 18, rowGap: 6 },
  fact: { minWidth: 110 },
  factLabel: { fontSize: 12, color: COLORS.muted, textTransform: 'uppercase', letterSpacing: 0.4 },
  factValue: { fontSize: 14, color: COLORS.text, fontWeight: '600' },
  headerActions: { flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 0 },
  headerButton: { color: COLORS.primary, fontSize: 15, fontWeight: '600', paddingVertical: 6, paddingHorizontal: 12, borderRadius: 10, borderWidth: 1, borderColor: COLORS.border, backgroundColor: '#fff', overflow: 'hidden' },
  headerButtonMain: { color: '#fff', backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  quickNav: { flexGrow: 0, marginTop: -10, marginBottom: 14 },
  quickNavIn: { gap: 8, paddingRight: 8 },
  navChip: { borderWidth: 1, borderColor: COLORS.border, backgroundColor: '#fff', borderRadius: 99, paddingVertical: 8, paddingHorizontal: 14 },
  navChipMain: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  navChipText: { fontSize: 14, fontWeight: '600', color: COLORS.text },
  navChipMainText: { color: '#fff' },
  profileTop: { flexDirection: 'row', alignItems: 'center', gap: 14, marginBottom: 4 },
  avatar: { width: 52, height: 52, borderRadius: 26, backgroundColor: COLORS.primarySoft, alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: COLORS.primary, fontSize: 20, fontWeight: '700' },
  greeting: { fontSize: 20, fontWeight: '700', color: COLORS.text },
  tiles: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 14 },
  tile: {
    flexGrow: 1,
    flexBasis: '30%',
    minWidth: 140,
    backgroundColor: '#fff',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: COLORS.border,
    padding: 16,
    gap: 4,
    ...SHADOW,
  },
  tileIcon: { fontSize: 24 },
  tileTitle: { fontSize: 16, fontWeight: '700', color: COLORS.text, marginTop: 4 },
  tileHint: { fontSize: 13, color: COLORS.muted },
  tileDanger: { backgroundColor: COLORS.errorSoft, borderColor: '#F6CFCB' },
  tileDangerText: { color: COLORS.error },
  tileAccent: { backgroundColor: COLORS.primarySoft, borderColor: '#CFE0FB' },
  menuCard: { paddingVertical: 4, paddingHorizontal: 0, gap: 0 },
  menuRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 14, paddingHorizontal: 20, borderTopWidth: 1, borderTopColor: COLORS.border, gap: 10 },
  menuRowFirst: { borderTopWidth: 0 },
  menuText: { flex: 1, fontSize: 16, color: COLORS.text },
  badge: { backgroundColor: COLORS.primary, color: '#fff', fontSize: 12, fontWeight: '700', paddingHorizontal: 8, paddingVertical: 2, borderRadius: 99, overflow: 'hidden' },
  chevron: { fontSize: 22, color: COLORS.muted, lineHeight: 22 },
});
