"""High-precision stills for the live shaders.

After Effects renders the blurred star at 8 bits, so along its gradient the same gray repeats for up to ~50 px; blown
up full-screen and pushed through a palette those plateaus show as bands. The true star is extremely smooth (AE blurs
it with Fast Box Blur 80), so a light Gaussian smoothing plus area-averaged downsampling in float recovers the
in-between values. They're stored as 16 bits in a lossless PNG: red = high byte, green = low byte (the shader decodes
it once on the GPU into a half-float texture)."""
import io
import numpy as np
from PIL import Image


def gauss(a, sigma):
    r = int(np.ceil(3 * sigma)); k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); k /= k.sum()
    p = np.pad(a, ((0, 0), (r, r)), 'edge'); a = sum(k[i] * p[:, i:i + a.shape[1]] for i in range(len(k)))
    p = np.pad(a, ((r, r), (0, 0)), 'edge'); return sum(k[i] * p[i:i + a.shape[0]] for i in range(len(k)))


def make(src_png, size, sigma=3.0, bits=12):
    """-> (float still at `size`, PNG bytes of the hi/lo encoding). `bits` of precision (low bits zeroed so the PNG
    compresses); 12 bits is 16x finer than 8-bit and, for these stills, the best quality per KB."""
    g = np.asarray(Image.open(src_png).convert('L'), np.float32) / 255
    g = gauss(g, sigma)
    small = np.asarray(Image.fromarray(g.astype(np.float32), 'F').resize(size, Image.BOX), np.float32)
    v = (np.clip(np.round(small * (2 ** bits - 1)), 0, 2 ** bits - 1).astype(np.uint32) << (16 - bits))
    rgb = np.stack([(v >> 8).astype(np.uint8), (v & 255).astype(np.uint8), np.zeros(v.shape, np.uint8)], -1)
    buf = io.BytesIO(); Image.fromarray(rgb, 'RGB').save(buf, 'PNG', optimize=True)
    return (v.astype(np.float32) / 65535), buf.getvalue()


def load(png_bytes_or_path):
    im = Image.open(io.BytesIO(png_bytes_or_path) if isinstance(png_bytes_or_path, bytes) else png_bytes_or_path).convert('RGB')
    a = np.asarray(im).astype(np.uint32)
    return ((a[..., 0] << 8) | a[..., 1]).astype(np.float32) / 65535
