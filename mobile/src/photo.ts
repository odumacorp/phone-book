import * as ImagePicker from 'expo-image-picker';
import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';

const SIZE = 320; // pixels: sharp on any phone, small to upload (about 20-60 KB)

/** Let the person choose (or take) a photo; returns it as a small square JPEG data URL, or null if they cancelled. */
export async function pickPhoto(source: 'library' | 'camera'): Promise<string | null> {
  if (source === 'camera') {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) throw new Error('Allow camera access in your phone settings to take a photo.');
  }
  const options: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], allowsEditing: true, aspect: [1, 1], quality: 1 };
  const result =
    source === 'camera' ? await ImagePicker.launchCameraAsync(options) : await ImagePicker.launchImageLibraryAsync(options);
  if (result.canceled || !result.assets?.length) return null;
  const asset = result.assets[0];
  const context = ImageManipulator.manipulate(asset.uri);
  const { width, height } = asset;
  if (width && height && width !== height) {
    // the website can't crop while choosing: keep the middle square
    const side = Math.min(width, height);
    context.crop({ originX: Math.floor((width - side) / 2), originY: Math.floor((height - side) / 2), width: side, height: side });
  }
  context.resize({ width: SIZE, height: SIZE });
  const image = await context.renderAsync();
  const saved = await image.saveAsync({ format: SaveFormat.JPEG, compress: 0.75, base64: true });
  if (!saved.base64) throw new Error("That picture couldn't be read. Try another one.");
  return `data:image/jpeg;base64,${saved.base64}`;
}
