from pathlib import Path

from libemg.streamers import myo_streamer
from libemg.gui import GUI
from libemg.data_handler import OnlineDataHandler

# data/ 在 repo 根目錄，所以路徑錨在這個檔案的上一層，從哪個 cwd 跑都一樣
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

LABEL_PATH = f"{DATA_DIR / 'gestures_set'}/"
SAVE_PATH = "{}/".format(DATA_DIR / "grandma's_right_hand_emg")
gesture_ids = list(range(1, 38))
WINDOW_SCALE = 0.5

if __name__ == "__main__":
    streamer, sm = myo_streamer()
    odh = OnlineDataHandler(sm)
    gui = GUI(odh, 
                args={'media_folder': LABEL_PATH, 
                    'data_folder':SAVE_PATH, 
                    'num_reps': 3, 
                    'rep_time': 5, 
                    'rest_time': 10, 
                    'auto_advance': True}, 
                width=int(1920*WINDOW_SCALE), height=int(1080*WINDOW_SCALE), debug=False, gesture_width=int(500*WINDOW_SCALE), gesture_height=int(500*WINDOW_SCALE))
    gui.download_gestures(gesture_ids=gesture_ids, folder=LABEL_PATH, download_imgs=True)
    gui.start_gui()