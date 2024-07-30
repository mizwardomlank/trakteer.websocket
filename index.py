import os
import json
import logging
import time
import threading
import random
from websocket import WebSocketApp
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

PUSHER_CHANNEL = os.getenv("PUSHER_CHANNEL")
PUSHER_CHANNEL_TEST = os.getenv("PUSHER_CHANNEL_TEST")
WS_URL = os.getenv("WS_URL")

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

def get_current_timestamp():
    return time.strftime("%d:%m:%Y %H:%M:%S", time.localtime())

def send_ping(ws):
    ws.send(json.dumps({'event': 'ping'}))

def handle_websocket_message(ws, message):
    try:
        response = json.loads(message)
        logger.info(f"Received message: {response}")
        if response.get('event') == 'Illuminate\\Notifications\\Events\\BroadcastNotificationCreated':
            data = json.loads(response.get('data'))
            logger.info(f"Notification received with id: {data['id']}")
    except json.JSONDecodeError:
        logger.error("Received message is not a valid JSON")

def on_open(ws):
    global is_connection_established, reconnect_attempts

    logger.info("WebSocket connection opened.")
    is_connection_established = True
    reconnect_attempts = 0

    # Subscribe to the specified channels
    channels = [PUSHER_CHANNEL, PUSHER_CHANNEL_TEST]
    for channel in channels:
        subscription_message = json.dumps({
            'event': 'pusher:subscribe',
            'data': {'channel': channel}
        })
        ws.send(subscription_message)
        logger.info(f"Subscription message sent: {subscription_message}")

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
        ws_thread.join()
    logger.info('Shutdown complete.')
    exit(0)

if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    connect_websocket()
    while not shutdown_flag.is_set():
        time.sleep(1)
