// Generates the extension icons (16/48/128 px PNG) without any external dependency.
import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "public", "icons");
mkdirSync(outDir, { recursive: true });

const crcTable = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};
const chunk = (type, data) => {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const typeBuf = Buffer.from(type, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])));
  return Buffer.concat([len, typeBuf, data, crc]);
};

function png(size, pixel) {
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0;
    for (let x = 0; x < size; x++) {
      const [r, g, b, a] = pixel(x, y, size);
      const o = y * (size * 4 + 1) + 1 + x * 4;
      raw[o] = r; raw[o + 1] = g; raw[o + 2] = b; raw[o + 3] = a;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0); ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

// A gem (diamond) on a rounded dark tile.
function gemPixel(x, y, size) {
  const cx = (x + 0.5) / size - 0.5;
  const cy = (y + 0.5) / size - 0.5;
  const r = 0.46;
  const inTile = Math.abs(cx) <= r && Math.abs(cy) <= r && (Math.abs(cx) <= r - 0.1 || Math.abs(cy) <= r - 0.1 || Math.hypot(Math.abs(cx) - (r - 0.1), Math.abs(cy) - (r - 0.1)) <= 0.1);
  if (!inTile) return [0, 0, 0, 0];
  const top = cy < -0.08;
  const inGem = top ? Math.abs(cx) <= 0.34 && cy >= -0.3 : Math.abs(cx) <= 0.34 * (1 - (cy + 0.08) / 0.42) && cy <= 0.34;
  if (inGem) {
    const facet = top ? (Math.abs(cx) < 0.12 ? 1 : 0.8) : cx < 0 ? 0.9 : 0.7;
    return [Math.round(72 * facet), Math.round(214 * facet), Math.round(255 * facet), 255];
  }
  return [16, 20, 31, 255];
}

for (const size of [16, 48, 128]) {
  writeFileSync(join(outDir, `icon${size}.png`), png(size, gemPixel));
  console.log(`wrote icon${size}.png`);
}
