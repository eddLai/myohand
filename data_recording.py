from libemg.streamers import myo_streamer
from libemg.gui import GUI
from libemg.data_handler import OnlineDataHandler

LABEL_PATH = "data/gestures_set/"
SAVE_PATH = "data/grandma's_right_hand_emg/"
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