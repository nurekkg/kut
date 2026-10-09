// Сжатие снимка на телефоне до отправки: длинная сторона не больше 2560 px, JPEG. Снимок с камеры
// весит 4-10 МБ, после сжатия 0,3-1,5 МБ: загрузка по слабой связи проходит, а лимит сервера
// (15 МБ) не нужен как запас. Вместе с файлом строится маленькая картинка для списка снимков.
export const MAX_SIDE = 2560;
export const THUMB_SIDE = 192;
export const JPEG_QUALITY = 0.82;

export class UnsupportedImageError extends Error {}

export type Compressed = { blob: Blob; thumb: string };

function drawScaled(bitmap: ImageBitmap, side: number): HTMLCanvasElement {
  const scale = Math.min(1, side / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new UnsupportedImageError("canvas");
  // У JPEG нет прозрачности: прозрачные места png не должны стать чёрными.
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, width, height);
  context.drawImage(bitmap, 0, 0, width, height);
  return canvas;
}

export async function compressPhoto(file: File): Promise<Compressed> {
  let bitmap: ImageBitmap;
  try {
    // Ориентация из EXIF применяется при разборе: снимок не окажется боком.
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    throw new UnsupportedImageError(file.type);
  }
  try {
    const canvas = drawScaled(bitmap, MAX_SIDE);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY),
    );
    if (!blob) throw new UnsupportedImageError("encode");
    const thumb = drawScaled(bitmap, THUMB_SIDE).toDataURL("image/jpeg", 0.7);
    return { blob, thumb };
  } finally {
    bitmap.close();
  }
}
