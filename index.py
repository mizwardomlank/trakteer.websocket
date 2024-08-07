import os
import json
import logging
import time
import threading
import random
import requests
import pyautogui
import base64
from websocket import WebSocketApp
from dotenv import load_dotenv
from queue import Queue
from playsound import playsound

# Load environment variables from .env file
load_dotenv()

# PUSHER_CHANNEL = os.getenv("PUSHER_CHANNEL")
# PUSHER_CHANNEL_TEST = os.getenv("PUSHER_CHANNEL_TEST")
# WS_URL = os.getenv("WS_URL")
# CONFIG_URL = os.getenv("CONFIG_URL")

WS_URL="wss://socket.trakteer.id/app/2ae25d102cc6cd41100a?protocol=7&client=python&version=5.1.1&flash=false"
PUSHER_CHANNEL_TEST="creator-stream-test."
PUSHER_CHANNEL="creator-stream."
CONFIG_URL="https://api.trakteer.id/v1/public/jsondb/trakteercharity2024" # custom by creator

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
logger = logging.getLogger()

# Main connection variables
ws = None
ws_thread = None

# Reconnect variables
is_connection_established = False
reconnect_attempts = 0
max_reconnect_attempts = 120
base_reconnect_delay = 5  # 5 seconds
max_reconnect_delay = 60  # 1 minute
shutdown_flag = threading.Event()

# Keyboard actions and configuration from config
keyboard_actions = {}
quantity_threshold = 0  # Default value
repeat_settings = {}
my_channel_id = ""

# Action queue
action_queue = Queue()
queue_lock = threading.Lock()

# Path to the sound file
sound_file_path = 'notification.wav'

def fetch_config():
    global keyboard_actions, quantity_threshold, repeat_settings, my_channel_id
    try:
        response = requests.get(CONFIG_URL)
        response.raise_for_status()
        config = response.json()
        # logger.error(f"Config from API: {config}")
        # Access the `keyboard_actions`, `quantity_threshold`, and `repeat_settings` inside `record`
        keyboard_actions = config.get("keyboard_actions", {})
        quantity_threshold = config.get("quantity_threshold", 0)
        repeat_settings = config.get("repeat_settings", {})
        my_channel_id = config.get("my_channel_id", "")
        logger.info("Config fetched and parsed successfully.")
    except requests.RequestException as e:
        logger.error(f"Failed to fetch config: {e}")
    except json.JSONDecodeError:
        logger.error("Config file is not a valid JSON")

def perform_keyboard_action(action_name):
    if action_name in keyboard_actions:
        action = keyboard_actions[action_name]
        repeat_setting = repeat_settings.get(action_name, {})
        repeat_count = repeat_setting.get("count", 1)
        repeat_interval = repeat_setting.get("interval", 0)
        # for playing note.wav file
        if os.path.exists(sound_file_path):
            playsound(sound_file_path)  # Play the sound here
        else:
            logger.error(f"Sound file not found: {sound_file_path}")

        for _ in range(repeat_count):
            for key in action:
                pyautogui.press(key)
            logger.info(f"Performed keyboard action: {action_name}")
            time.sleep(repeat_interval)
    else:
        logger.warning(f"No action found for: {action_name}")

def action_processor():
    while not shutdown_flag.is_set():
        try:
            action_name = action_queue.get(timeout=1)
            with queue_lock:
                perform_keyboard_action(action_name)
            action_queue.task_done()
        except Exception as e:
            if not shutdown_flag.is_set():
                logger.error(f"Waiting the action (e): {e}")

def extract_commands(supporter_message):
    words = supporter_message.split()
    commands = [word for word in words if word.startswith('!')]
    return commands

def get_current_timestamp():
    return time.strftime("%d:%m:%Y %H:%M:%S", time.localtime())

def send_ping(ws):
    ws.send(json.dumps({'event': 'ping'}))

def handle_websocket_message(ws, message):
    try:
        response = json.loads(message)
        logger.info(f"Received message.")
        if response.get('event') == 'Illuminate\\Notifications\\Events\\BroadcastNotificationCreated':
            data = json.loads(response.get('data'))
            logger.info(f"Data: {data}")

            supporter_message = data.get("supporter_message", "")
            if supporter_message is None:
                logger.warning("No supporter_message found in the data.")
                return

            commands = extract_commands(supporter_message)
            
            # Check if quantity is above the threshold
            quantity = data.get("quantity", 0)
            if quantity >= quantity_threshold and commands:
                first_command = commands[0]
                with queue_lock:
                    action_queue.put(first_command)
    except json.JSONDecodeError:
        logger.error("Received message is not a valid JSON")


def on_open(ws):
    global is_connection_established, reconnect_attempts

    logger.info("WebSocket connection opened.")
    is_connection_established = True
    reconnect_attempts = 0

    # Fetch the config on opening the websocket connection
    fetch_config()

    # Decode my_channel_id and append to PUSHER_CHANNEL and PUSHER_CHANNEL_TEST
    decoded_channel_id = base64.b64decode(my_channel_id).decode('utf-8')
    channels = [PUSHER_CHANNEL + decoded_channel_id, PUSHER_CHANNEL_TEST + decoded_channel_id]

    # Subscribe to the specified channels
    for channel in channels:
        subscription_message = json.dumps({
            'event': 'pusher:subscribe',
            'data': {'channel': channel}
        })
        ws.send(subscription_message)
        logger.info(f"Subscription message sent.")

    # Send a ping every 30 seconds to keep the connection alive
    def ping():
        while is_connection_established:
            send_ping(ws)
            time.sleep(30)

    threading.Thread(target=ping, daemon=True).start()

def on_close(ws, close_status_code, close_msg):
    global is_connection_established
    logger.error(f"Connection closed with code {close_status_code}: {close_msg}")
    is_connection_established = False
    handle_reconnect()

def on_error(ws, error):
    global is_connection_established
    logger.error(f"Connection error: {error}")
    is_connection_established = False
    handle_reconnect()

def on_message(ws, message):
    handle_websocket_message(ws, message)

def handle_reconnect():
    global reconnect_attempts

    if reconnect_attempts >= max_reconnect_attempts:
        logger.error("Max reconnect attempts reached. Giving up.")
        return

    reconnect_attempts += 1
    delay = min(base_reconnect_delay * (2 ** reconnect_attempts), max_reconnect_delay)
    jitter = delay * 0.1 * (2 * (random.random() - 0.5))  # Add jitter
    delay_with_jitter = delay + jitter
    logger.info(f"Attempt ({reconnect_attempts}). Reconnecting in {delay_with_jitter:.2f} seconds...")

    time.sleep(delay_with_jitter)
    connect_websocket()

def connect_websocket():
    global is_connection_established, ws, ws_thread

    logger.info("Connecting to WebSocket...")
    ws = WebSocketApp(WS_URL,
                      on_open=on_open,
                      on_message=on_message,
                      on_error=on_error,
                      on_close=on_close)
    
    ws_thread = threading.Thread(target=ws.run_forever)
    ws_thread.daemon = True
    ws_thread.start()

    # Wait to see if the connection was successful
    time.sleep(10)
    if not is_connection_established:
        logger.error("Connection attempt timed out")
        ws.close()
        handle_reconnect()

def signal_handler(sig, frame):
    global ws, ws_thread
    logger.info('Shutting down...')
    shutdown_flag.set()
    if ws:
        ws.close()
    if ws_thread:
        ws_thread.join(timeout=10)  # Wait for the WebSocket thread to finish with a timeout
    # Wait for the action queue to be processed
    action_queue.join()
    logger.info('Shutdown complete.')
    exit(0)

if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start the action processor thread
    threading.Thread(target=action_processor, daemon=True).start()
    
    connect_websocket()
    while not shutdown_flag.is_set():
        time.sleep(1)
