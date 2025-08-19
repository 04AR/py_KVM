import asyncio
import aiohttp
from aiohttp import web
import pyautogui
import json
import logging
from pynput import keyboard

# Set up logging to file and console
logging.basicConfig(
    level=logging.DEBUG,  # Debug level for detailed tracing
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('inputs.log'), logging.StreamHandler()]
)

screen_width, screen_height = pyautogui.size()
# Store active WebSocket clients
clients = set()
# Get the main event loop - it's better to manage this implicitly or through aiohttp's app context.
# However, for run_coroutine_threadsafe, we'll keep it as a practical workaround for now.
# This variable might not be strictly necessary if using app.loop within startup/cleanup hooks.
loop = asyncio.get_event_loop() 

# Buffer for collecting server text
text_buffer = []

# Create an asyncio Queue for inter-thread communication
message_queue = asyncio.Queue()

async def handle(request):
    logging.debug("Handling HTTP request for index.html")
    return web.FileResponse('index.html')

async def broadcast_message(msg):
    """Broadcast message to all connected clients."""
    logging.debug(f"Starting broadcast_message with message: {msg}")
    if not clients:
        logging.debug("No clients connected to broadcast to")
        return
    closed_clients = []
    for client in clients:
        if client.closed:
            logging.debug(f"Found closed client: {client}")
            closed_clients.append(client)
            continue
        try:
            await client.send_str(msg)
            logging.debug(f"Successfully sent message to client: {msg}")
        except Exception as e:
            logging.error(f"Error sending to client {client}: {e}")
            closed_clients.append(client)
    # Clean up closed clients
    for client in closed_clients:
        clients.discard(client)
        logging.info(f"Removed closed client: {client}")
    logging.debug(f"Broadcast completed. Active clients: {len(clients)}")

# Define the on_key_event function globally or within a class
# to avoid recreating it with each websocket connection
def on_key_event(key, action):
    try:
        key_str = str(key).replace("Key.", "").replace("'", "")
        if action == 'down':
            if key == keyboard.Key.enter:
                # Send buffered text as a paragraph
                if text_buffer:
                    text = ''.join(text_buffer)
                    msg = json.dumps({'type': 'server_text', 'text': text})
                    # Put message into the queue instead of direct broadcast
                    asyncio.run_coroutine_threadsafe(message_queue.put(msg), loop)
                    logging.info(f"Server text queued: {text}")
                    text_buffer.clear()
            elif hasattr(key, 'char') and key.char:
                # Add printable characters to buffer
                text_buffer.append(key.char)
                logging.debug(f"Added to buffer: {key.char}")
                # Send if buffer exceeds limit (e.g., 1000 chars)
                if len(text_buffer) >= 1000:
                    text = ''.join(text_buffer)
                    msg = json.dumps({'type': 'server_text', 'text': text})
                    # Put message into the queue
                    asyncio.run_coroutine_threadsafe(message_queue.put(msg), loop)
                    logging.info(f"Server text queued (buffer limit): {text}")
                    text_buffer.clear()
            else:
                # Send non-printable keys as single key events
                msg = json.dumps({'type': 'server_key', 'action': action, 'key': key_str})
                # Put message into the queue
                asyncio.run_coroutine_threadsafe(message_queue.put(msg), loop)
                logging.info(f"Server key {action} queued: {key_str}")
    except Exception as e:
        logging.error(f"Error processing key event: {e}")

def on_press(key):
    on_key_event(key, 'down')

def on_release(key):
    on_key_event(key, 'up')

# Global variable for the keyboard listener
keyboard_listener = None

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    clients.add(ws)  # Add client to set
    logging.info(f"New WebSocket client connected. Total clients: {len(clients)}")
    
    # Start keyboard listener only once for the application, not per connection
    # This check ensures it's started only when the first client connects
    # or you could start it in an app startup hook if you want it always active.
    global keyboard_listener
    if keyboard_listener is None or not keyboard_listener.is_alive():
        keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        keyboard_listener.start()
        logging.debug("Keyboard listener started for the first time")

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    event_type = data['type']
                    logging.debug(f"Received WebSocket message: {data}")

                    if event_type == 'mouse':
                        action = data['action']
                        if action == 'move':
                            frac_x = data['x']
                            frac_y = data['y']
                            pos_x = frac_x * screen_width
                            pos_y = frac_y * screen_height
                            pyautogui.moveTo(pos_x, pos_y)
                            logging.info(f"Mouse moved to ({pos_x:.2f}, {pos_y:.2f})")
                        elif action == 'down':
                            button = 'left' if data['button'] == 0 else 'right' if data['button'] == 2 else 'middle'
                            pyautogui.mouseDown(button=button)
                            logging.info(f"Mouse button down: {button}")
                        elif action == 'up':
                            button = 'left' if data['button'] == 0 else 'right' if data['button'] == 2 else 'middle'
                            pyautogui.mouseUp(button=button)
                            logging.info(f"Mouse button up: {button}")
                        elif action == 'wheel':
                            delta = data['delta']
                            pyautogui.scroll(delta)
                            logging.info(f"Mouse scroll: {delta}")

                    elif event_type == 'key':
                        action = data['action']
                        key = data['key'].lower() if len(data['key']) == 1 else data['key']
                        if action == 'down':
                            pyautogui.keyDown(key)
                            logging.info(f"Client key down: {key}")
                        elif action == 'up':
                            pyautogui.keyUp(key)
                            logging.info(f"Client key up: {key}")
                        # Broadcast client key event
                        await broadcast_message(json.dumps({'type': 'client_key', 'action': action, 'key': key}))

                    elif event_type == 'client_text':
                        text = data['text']
                        for char in text:
                            pyautogui.write(char)
                        pyautogui.press('enter')
                        logging.info(f"Client text typed: {text}")
                        # Broadcast client text event
                        await broadcast_message(json.dumps({'type': 'client_text', 'text': text}))
                except Exception as e:
                    logging.error(f"Error processing WebSocket message: {e}")
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logging.error(f"WebSocket connection closed with exception: {ws.exception()}")
    finally: # Use finally to ensure cleanup even if exceptions occur
        clients.discard(ws)  # Remove client on disconnect
        logging.info(f"WebSocket client disconnected. Total clients: {len(clients)}")
    return ws

# New async task to consume messages from the queue and broadcast them
async def queue_consumer(app): # app argument allows access to app-specific resources if needed
    logging.info("Starting message queue consumer...")
    while True:
        try:
            msg = await message_queue.get()
            await broadcast_message(msg)
            message_queue.task_done()
            logging.debug(f"Message consumed from queue and broadcasted: {msg}")
        except asyncio.CancelledError:
            logging.info("Queue consumer task cancelled.")
            break
        except Exception as e:
            logging.error(f"Error in queue_consumer: {e}")
            await asyncio.sleep(1) # Prevent busy loop on continuous errors


async def start_background_tasks(app):
    app['queue_consumer_task'] = asyncio.create_task(queue_consumer(app))
    # You could also start the keyboard listener here if you want it to run
    # independently of client connections.
    # global keyboard_listener
    # if keyboard_listener is None or not keyboard_listener.is_alive():
    #     keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    #     keyboard_listener.start()
    #     logging.debug("Keyboard listener started on app startup")


async def cleanup_background_tasks(app):
    app['queue_consumer_task'].cancel()
    await app['queue_consumer_task']
    # If the keyboard listener was started globally on app startup, stop it here
    global keyboard_listener
    if keyboard_listener is not None and keyboard_listener.is_alive():
        keyboard_listener.stop()
        keyboard_listener.join()
        logging.debug("Keyboard listener stopped on app shutdown")


app = web.Application()
app.on_startup.append(start_background_tasks)
app.on_cleanup.append(cleanup_background_tasks)

app.add_routes([
    web.get('/', handle),
    web.get('/ws', websocket_handler)
])

if __name__ == '__main__':
    logging.info("Starting server on port 8000")
    web.run_app(app, port=8000)