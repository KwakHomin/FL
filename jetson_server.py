import cv2
import Jetson.GPIO as GPIO
from ultralytics import YOLO
import time
import threading
import socket
import json
import base64
import logging
import signal
import sys
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

PORT = 8888; CAMERA_ID = 0; SAVE_DIR = "/home/solwith/Desktop/FDD/Record"
os.makedirs(SAVE_DIR, exist_ok=True)
FRAME_WIDTH = 640; FRAME_HEIGHT = 480
MAX_WORKERS = 20
POST_RECORD_BUFFER_SECONDS = 10; MAX_RECORD_FOLDER_SIZE_MB = 1024
CLASS_NAME = ['driver', 'forklift', 'person']; TARGET_CLASS = {'forklift': 1, 'person': 2}
FORKLIFT_GPIO = 7; PERSON_GPIO = 29; BOTH_GPIO = 31; ALL_GPIO = [FORKLIFT_GPIO, PERSON_GPIO, BOTH_GPIO]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler('jetson_safety_system.log'), logging.StreamHandler()])
GPIO.setwarnings(False); GPIO.setmode(GPIO.BOARD)
for pin in ALL_GPIO: GPIO.setup(pin, GPIO.OUT); GPIO.output(pin, GPIO.LOW)

tracking_active = False; latest_frame = None; terminate = False
recording = False; recording_stop_time = 0; video_writer = None

frame_lock = threading.Lock()
gpio_lock = threading.Lock()
state_lock = threading.Lock()

model = YOLO("model.engine")

def update_gpio(forklift_in, person_in):
    with gpio_lock:
        if forklift_in and person_in: GPIO.output(BOTH_GPIO, GPIO.HIGH); GPIO.output(FORKLIFT_GPIO, GPIO.LOW); GPIO.output(PERSON_GPIO, GPIO.LOW)
        elif forklift_in: GPIO.output(BOTH_GPIO, GPIO.LOW); GPIO.output(FORKLIFT_GPIO, GPIO.HIGH); GPIO.output(PERSON_GPIO, GPIO.LOW)
        elif person_in: GPIO.output(BOTH_GPIO, GPIO.LOW); GPIO.output(FORKLIFT_GPIO, GPIO.LOW); GPIO.output(PERSON_GPIO, GPIO.HIGH)
        else:
            for pin in ALL_GPIO: GPIO.output(pin, GPIO.LOW)

def process_frame(frame):
    global recording, recording_stop_time, video_writer
    start_time = time.perf_counter()
    results = model(frame, verbose=False)[0]
    latency_ms = (time.perf_counter() - start_time) * 1000
    logging.info(f"Model Inference Latency: {latency_ms:.2f} ms")
    forklift_in, person_in = False, False
    if results.boxes is not None:
        for box in results.boxes:
            cls_id = int(box.cls[0]); class_name = CLASS_NAME[cls_id]
            if class_name not in TARGET_CLASS: continue
            if class_name == 'forklift': forklift_in = True
            elif class_name == 'person': person_in = True
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            color = (0, 255, 0) if class_name == 'person' else (0, 165, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, class_name, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    update_gpio(forklift_in, person_in)
    is_danger = GPIO.input(BOTH_GPIO) == GPIO.HIGH
    if is_danger:
        if not recording:
            now = datetime.now().strftime("%Y_%m_%d_%H_%M_%S"); save_path = os.path.join(SAVE_DIR, f"{now}.mp4")
            fourcc = cv2.VideoWriter_fourcc(*'avc1'); video_writer = cv2.VideoWriter(save_path, fourcc, 15, (frame.shape[1], frame.shape[0]))
            recording = True; recording_stop_time = 0; logging.info(f"Recording started: {save_path}")
        if video_writer: video_writer.write(frame)
    elif recording:
        if recording_stop_time == 0: recording_stop_time = time.time() + POST_RECORD_BUFFER_SECONDS
        if time.time() < recording_stop_time:
            if video_writer: video_writer.write(frame)
        else:
            if video_writer: video_writer.release(); video_writer = None
            recording = False; recording_stop_time = 0; logging.info("Recording finished.")
    return frame

def camera_thread():
    global latest_frame, video_writer, recording, tracking_active, terminate
    cap = cv2.VideoCapture(CAMERA_ID)
    if not cap.isOpened(): logging.error("Camera open failed!"); terminate = True; return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    
    while not terminate:
        try:
            ret, raw_frame = cap.read()
            if not ret: logging.warning("Failed to grab frame."); time.sleep(0.1); continue
            
            with state_lock: is_tracking = tracking_active
            
            frame_to_be_sent = raw_frame
            if is_tracking:
                frame_to_be_sent = process_frame(raw_frame.copy())
            
            with frame_lock:
                latest_frame = frame_to_be_sent
            
            time.sleep(0.01)
        except Exception as e:
            logging.error(f"Exception in camera loop: {e}"); time.sleep(1)
            
    if recording and video_writer:
        video_writer.release()
        logging.info("Recording file has been saved safely on exit.")
            
    cap.release()
    with gpio_lock: GPIO.cleanup()
    logging.info("Camera thread terminated.")


def handle_client(conn, addr):
    global tracking_active
    logging.info(f"Client connected: {addr}")
    try:
        with conn:
            buffer = b""
            while not terminate:
                data = conn.recv(1024)
                if not data: break
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    command = json.loads(line.decode())
                    command_type = command.get("type"); response = {}
                    if command_type == "start_tracking":
                        with state_lock: tracking_active = True
                        response = {"status": "success"}
                    elif command_type == "stop_tracking":
                        with state_lock: tracking_active = False
                        update_gpio(False, False)
                        response = {"status": "success"}
                    elif command_type == "status":
                        with state_lock: is_tracking = tracking_active
                        response = {"status": "success", "tracking_status": is_tracking}
                    elif command_type == "get_frame":
                        with frame_lock:
                            if latest_frame is not None:
                                small_frame = cv2.resize(latest_frame, (200, 150))
                                _, img_encoded = cv2.imencode(".jpg", small_frame)
                                img_bytes = base64.b64encode(img_encoded).decode('ascii')
                                response = {"status": "success", "frame": img_bytes}
                            else: response = {"status": "error", "message": "No frame"}
                    elif command_type == "list_recordings":
                        try:
                            files = sorted([f for f in os.listdir(SAVE_DIR) if f.endswith('.mp4')], reverse=True)
                            response = {"status": "success", "files": files}
                        except Exception as e: response = {"status": "error", "message": str(e)}
                    else:
                        response = {"status": "error", "message": "Unknown command"}
                    
                    conn.sendall((json.dumps(response) + "\n").encode())
    except (ConnectionResetError, BrokenPipeError):
        logging.warning(f"Client {addr} disconnected unexpectedly.")
    except Exception as e:
        logging.error(f"Error with client {addr}: {e}")
    finally:
        logging.info(f"Connection from {addr} closed.")

def start_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", PORT))
        s.listen(128)
        logging.info(f"Threaded server listening on port {PORT}")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            while not terminate:
                try:
                    s.settimeout(1.0)
                    conn, addr = s.accept()
                    executor.submit(handle_client, conn, addr)
                except socket.timeout: continue
                except Exception as e:
                    if not terminate: logging.error(f"Server accept error: {e}")
    logging.info("Server shutdown.")

def signal_handler(sig, frame):
    global terminate
    if not terminate: logging.info("Shutdown signal received..."); terminate = True

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler); signal.signal(signal.SIGTERM, signal_handler)
    threading.Thread(target=camera_thread, daemon=True).start()
    start_server()
    logging.info("Application terminated.")