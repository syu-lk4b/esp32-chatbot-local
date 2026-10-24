"""Send serial commands to the board, then capture a frame as PNG.
usage: snap.py out.png [cmds-before-capture] [--wait N]"""
from esp_port import open_port
import serial, time, sys
from PIL import Image
out = sys.argv[1]; cmds = sys.argv[2] if len(sys.argv) > 2 else ""
wait = float(sys.argv[sys.argv.index('--wait')+1]) if '--wait' in sys.argv else 0
s = open_port(0.5)
time.sleep(wait)
for c in cmds: s.write(c.encode()); time.sleep(0.4)
s.reset_input_buffer(); s.write(b'c')
buf = b''; t = time.time()
while b'SNAP ' not in buf and time.time() - t < 5: buf += s.read(4096)
i = buf.index(b'SNAP '); j = buf.index(b'\n', i)
w, h, n = map(int, buf[i+5:j].split()); data = buf[j+1:]
while len(data) < n and time.time() - t < 15: data += s.read(n - len(data))
assert len(data) >= n, f"short frame {len(data)}/{n}"
img = Image.frombytes('RGB', (w, h), bytes(
    b for k in range(0, n, 2) for b in (lambda v: ((v >> 11 & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v & 31) * 255 // 31))(data[k] << 8 | data[k+1])))
img.resize((w*2, h*2), Image.NEAREST).save(out); print("saved", out)
