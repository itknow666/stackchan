import ctypes, logging, socket, subprocess, sys, time
from pathlib import Path

BASE=Path(__file__).resolve().parent
handle=ctypes.windll.kernel32.CreateMutexW(None,False,'Local\\StackChanHomeAssistantLauncher')
if ctypes.windll.kernel32.GetLastError()==183: sys.exit(0)
logging.basicConfig(filename=BASE/'launcher.log',level=logging.INFO,format='%(asctime)s %(message)s')
flags=subprocess.CREATE_NO_WINDOW
with (BASE/'wsl.log').open('a',encoding='utf-8') as log:
    keeper=subprocess.Popen(['wsl','-d','Ubuntu-22.04','-u','root','--','bash','-lc','systemctl start docker mosquitto; docker start stackchan-homeassistant; exec sleep infinity'],stdout=log,stderr=log,creationflags=flags)
    try:
        for _ in range(90):
            try:
                with socket.create_connection(('127.0.0.1',1883),timeout=1): break
            except OSError: time.sleep(2)
        else: raise RuntimeError('本机 MQTT 未启动，请查看 wsl.log')
        while True:
            with (BASE/'bridge-process.log').open('a',encoding='utf-8') as out:
                code=subprocess.call([sys.executable.replace('pythonw.exe','python.exe'),'-X','utf8',str(BASE/'bridge.py')],stdout=out,stderr=out,creationflags=flags)
            logging.warning('Bridge exited %s; restarting',code)
            time.sleep(10)
    finally: keeper.terminate()
