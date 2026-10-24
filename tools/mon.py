"""Stream the board serial log. usage: mon.py [seconds] [--reset]"""
from esp_port import open_port
import serial,time,sys
dur=float(sys.argv[1]) if len(sys.argv)>1 else 40
s=open_port(0.2)
if '--reset' in sys.argv: s.rts=True; time.sleep(0.2); s.rts=False
t=time.time()
while time.time()-t<dur:
    try:
        b=s.read(4096)
        if b: sys.stdout.write(b.decode(errors='replace')); sys.stdout.flush()
    except Exception:
        time.sleep(0.5)
        try: s.close(); s.open()
        except Exception: pass
