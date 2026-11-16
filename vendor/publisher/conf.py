from pathlib import Path
import os
BASE_DIR = Path(__file__).resolve().parent / 'vendor'
LOCAL_CHROME_PATH = os.environ.get('DREAM_ROOM_CHROME', '')
LOCAL_CHROME_HEADLESS = True
DEBUG_MODE = False
YT_PROXY = None
XHS_SERVER = ''
