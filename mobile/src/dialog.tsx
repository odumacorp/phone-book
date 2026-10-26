import { createContext, useCallback, useContext, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

/**
 * Confirmation dialogs that work in the phone app and on the website (React Native's Alert does
 * nothing in a browser). Usage: const ask = useDialog(); ask(title, message, buttons).
 */

export type DialogButton = { text: string; style?: 'cancel' | 'destructive'; onPress?: () => void };
type Dialog = { title: string; message?: string; buttons: DialogButton[] };
type Ask = (title: string, message?: string, buttons?: DialogButton[]) => void;

const DialogContext = createContext<Ask>(() => {});

export const useDialog = () => useContext(DialogContext);

export function DialogProvider({ children }: { children: React.ReactNode }) {
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const ask = useCallback<Ask>(
    (title, message, buttons = [{ text: 'OK' }]) => setDialog({ title, message, buttons }),
    [],
  );
  const press = (button: DialogButton) => {
    setDialog(null);
    button.onPress?.();
  };

  return (
    <DialogContext.Provider value={ask}>
      {children}
      <Modal visible={!!dialog} transparent animationType="fade" onRequestClose={() => setDialog(null)}>
        <View style={styles.backdrop}>
          <View style={styles.box} accessibilityRole="alert">
            <Text style={styles.title}>{dialog?.title}</Text>
            {dialog?.message ? <Text style={styles.message}>{dialog.message}</Text> : null}
            <View style={styles.buttons}>
              {dialog?.buttons.map((b) => (
                <Pressable key={b.text} onPress={() => press(b)} style={styles.button} accessibilityRole="button">
                  <Text style={[styles.buttonText, b.style === 'destructive' && styles.destructive, b.style === 'cancel' && styles.cancel]}>
                    {b.text}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>
        </View>
      </Modal>
    </DialogContext.Provider>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', alignItems: 'center', justifyContent: 'center', padding: 24 },
  box: { backgroundColor: '#fff', borderRadius: 14, padding: 20, width: '100%', maxWidth: 400, gap: 8 },
  title: { fontSize: 18, fontWeight: '700', color: '#1A1A1A' },
  message: { fontSize: 15, color: '#444' },
  buttons: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'flex-end', gap: 8, marginTop: 12 },
  button: { paddingVertical: 10, paddingHorizontal: 12 },
  buttonText: { fontSize: 16, fontWeight: '600', color: '#0B6E4F' },
  destructive: { color: '#B3261E' },
  cancel: { color: '#6B6B6B', fontWeight: '500' },
});
