import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import socket
import json
import threading
import time
import base64
import numpy as np
from PIL import Image, ImageTk
import configparser
import os

class JetsonController:
    def __init__(self, root):
        self.root = root
        self.root.title("Jetson AI Safety Controller - Final Version")
        self.root.geometry("1100x750")
        
        self.jetson_ips = self.load_jetson_config()
        self.jetson_connections = {}
        self.camera_threads = {}
        self.running = True
        self.connections_lock = threading.Lock()
        
        # 재연결 시도 중인 Jetson을 추적하기 위한 집합(set)
        self.reconnecting_jetsons = set()

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.auto_update_status()

    def _send_command(self, name, command):
        # ... (이전과 동일)
        with self.connections_lock:
            conn_info = self.jetson_connections.get(name)
        if not conn_info or not conn_info.get('connected'): return None
        with conn_info['lock']:
            try:
                sock = conn_info['socket']
                sock.sendall((json.dumps(command) + "\n").encode('utf-8'))
                buffer = conn_info['buffer']
                while b"\n" not in buffer:
                    sock.settimeout(10.0) 
                    chunk = sock.recv(4096)
                    if not chunk: raise ConnectionError("Server closed connection")
                    buffer += chunk
                response_data, buffer = buffer.split(b'\n', 1)
                conn_info['buffer'] = buffer
                return json.loads(response_data)
            except Exception as e:
                self.log(f"Connection error for {name}: {e}. Disconnecting.")
                self.cleanup_connection_async(name)
                return None

    def connect_jetson(self, name, ip_config):
        with self.connections_lock:
            if name in self.jetson_connections: return

        self.log(f"Connecting to {name} ({ip_config})...")
        try:
            try:
                ip, port_str = ip_config.split(':')
                port = int(port_str)
            except ValueError:
                ip, port = ip_config, 8888

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((ip, port))
            self.log(f"Successfully connected to {name}.")
            
            with self.connections_lock:
                self.jetson_connections[name] = {
                    'socket': sock, 'ip': ip_config, 'connected': True,
                    'buffer': b'', 'lock': threading.Lock()
                }
            self.start_camera_stream(name)
        except Exception as e:
            self.log(f"Failed to connect to {name}: {e}")
        finally:
            # 연결 시도가 끝났으므로, 재연결 목록에서 제거
            if name in self.reconnecting_jetsons:
                self.reconnecting_jetsons.remove(name)
        
        self.root.after(0, self.update_status_display)

    def cleanup_connection(self, name):
        with self.connections_lock:
            conn_info = self.jetson_connections.pop(name, None)
        if conn_info:
            if conn_info.get('socket'):
                try: conn_info['socket'].close()
                except: pass
        self.update_disconnected_view(name)
        
    def cleanup_connection_async(self, name):
        self.root.after(0, self.cleanup_connection, name)

    def on_closing(self):
        self.running = False
        with self.connections_lock:
            connection_names = list(self.jetson_connections.keys())
        for name in connection_names:
            self.cleanup_connection(name)
        self.root.destroy()
        
    def auto_update_status(self):
        """(수정됨) 상태 표시 및 모든 연결(최초+재연결)을 관리하는 유일한 함수"""
        self.update_status_display()
        
        with self.connections_lock:
            connected_jetson_names = list(self.jetson_connections.keys())

        for name, ip_config in self.jetson_ips.items():
            is_connected = name in connected_jetson_names
            is_reconnecting = name in self.reconnecting_jetsons
            
            # 연결되어 있지도 않고, 재연결 시도 중도 아니라면 -> 새로운 연결 시도
            if not is_connected and not is_reconnecting:
                self.log(f"{name} is disconnected. Attempting to connect...")
                self.reconnecting_jetsons.add(name)
                threading.Thread(target=self.connect_jetson, args=(name, ip_config), daemon=True).start()
        
        if self.running:
            self.root.after(5000, self.auto_update_status)

    def start_camera_stream(self, name):
        if name not in self.camera_threads or not self.camera_threads[name].is_alive():
            thread = threading.Thread(target=self.camera_stream_worker, args=(name,), daemon=True)
            self.camera_threads[name] = thread; thread.start()
    def camera_stream_worker(self, name):
        while self.running:
            with self.connections_lock:
                if not self.jetson_connections.get(name): break
            response = self._send_command(name, {'type': 'get_frame'})
            if response and response.get('status') == 'success' and response.get('frame'):
                try:
                    img_data = base64.b64decode(response['frame']); nparr = np.frombuffer(img_data, np.uint8)
                    import cv2
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                        photo = ImageTk.PhotoImage(pil_image)
                        self.root.after(0, self.update_camera_view, name, photo)
                except Exception as e: self.log(f"Frame decoding error for {name}: {e}")
            elif response is None: break
            time.sleep(0.1)
        self.log(f"Camera stream for {name} has stopped.")
    def download_worker(self, name, filename, save_path):
        self.log(f"Starting download of {filename} from {name}...")
        response = self._send_command(name, {'type': 'download_recording', 'filename': filename})
        if response and response.get('status') == 'success' and response.get('content'):
            try:
                file_content = base64.b64decode(response['content'])
                with open(save_path, 'wb') as f: f.write(file_content)
                self.log(f"Successfully downloaded {filename}")
                messagebox.showinfo("Success", f"File '{filename}' downloaded successfully.")
            except Exception as e: self.log(f"Failed to save downloaded file {filename}: {e}")
        else: self.log(f"Download command failed for {filename}. Response: {response}")
    def generic_command_individual(self, command_type):
        name = self.jetson_var.get()
        if name: threading.Thread(target=self._send_command, args=(name, {'type': command_type}), daemon=True).start()
    def generic_command_all(self, command_type):
        with self.connections_lock: names = list(self.jetson_connections.keys())
        for name in names: threading.Thread(target=self._send_command, args=(name, {'type': command_type}), daemon=True).start()
    def power_control(self, name, command):
        with self.connections_lock:
            if not name or not self.jetson_connections.get(name):
                messagebox.showwarning("Warning", f"Please select a connected Jetson."); return
        if messagebox.askyesno("Confirm", f"Are you sure you want to {command} {name}?", icon='warning'):
            self.log(f"Sending {command} command to {name}...")
            threading.Thread(target=self._send_command, args=(name, {'type': command}), daemon=True).start()
    def power_control_all(self, command):
        if messagebox.askyesno("Confirm", f"Are you sure you want to {command} ALL Jetsons?", icon='warning'):
            with self.connections_lock: names = list(self.jetson_connections.keys())
            for name in names:
                def task(n, c):
                    self.log(f"Sending {c} command to {n}...")
                    self._send_command(n, {'type': c})
                threading.Thread(target=task, args=(name, command), daemon=True).start()
    def refresh_file_list(self):
        name = self.jetson_var.get()
        with self.connections_lock:
            if not name or not self.jetson_connections.get(name):
                messagebox.showwarning("Warning", "Please select a connected Jetson."); return
        def task():
            self.log(f"Fetching file list from {name}...")
            response = self._send_command(name, {'type': 'list_recordings'})
            def update_ui():
                self.file_listbox.delete(0, tk.END)
                if response and response.get('status') == 'success':
                    for f in response.get('files', []): self.file_listbox.insert(tk.END, f)
                    self.log(f"Found {self.file_listbox.size()} files on {name}.")
                else: self.log(f"Failed to get file list from {name}.")
            self.root.after(0, update_ui)
        threading.Thread(target=task, daemon=True).start()
    def download_selected_file(self):
        name = self.jetson_var.get()
        selected_indices = self.file_listbox.curselection()
        if not name or not selected_indices:
            messagebox.showwarning("Warning", "Please select a Jetson and a file to download."); return
        filename = self.file_listbox.get(selected_indices[0])
        save_path = filedialog.asksaveasfilename(initialfile=filename, defaultextension=".mp4")
        if save_path:
            threading.Thread(target=self.download_worker, args=(name, filename, save_path), daemon=True).start()
    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding=10); main_frame.pack(fill=tk.BOTH, expand=True)
        control_panel = self._create_control_panel(main_frame); control_panel.pack(fill=tk.X, pady=(0, 10))
        bottom_frame = ttk.Frame(main_frame); bottom_frame.pack(fill=tk.BOTH, expand=True)
        info_panel = self._create_info_panel(bottom_frame); info_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        camera_frame = ttk.LabelFrame(bottom_frame, text="Camera Views", padding=5); camera_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.camera_container = ttk.Frame(camera_frame); self.camera_container.pack(fill=tk.BOTH, expand=True)
        self.camera_views = {}; self._setup_camera_views()
    def _create_control_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Control Panel", padding=10)
        all_control_frame = ttk.Frame(frame); all_control_frame.pack(fill=tk.X, pady=2)
        ttk.Button(all_control_frame, text="Start All Tracking", command=lambda: self.generic_command_all('start_tracking')).pack(side=tk.LEFT, padx=2)
        ttk.Button(all_control_frame, text="Stop All Tracking", command=lambda: self.generic_command_all('stop_tracking')).pack(side=tk.LEFT, padx=2)
        ttk.Button(all_control_frame, text="Reboot All", command=lambda: self.power_control_all('reboot')).pack(side=tk.LEFT, padx=10)
        tk.Button(all_control_frame, text="⚠ Power Off All", command=lambda: self.power_control_all('power_off'), bg='#ff6b6b', fg='white').pack(side=tk.LEFT, padx=2)
        ind_control_frame = ttk.Frame(frame); ind_control_frame.pack(fill=tk.X, pady=5)
        ttk.Label(ind_control_frame, text="Select Jetson:").pack(side=tk.LEFT)
        self.jetson_var = tk.StringVar(); self.jetson_combo = ttk.Combobox(ind_control_frame, textvariable=self.jetson_var, values=list(self.jetson_ips.keys()))
        if self.jetson_ips: self.jetson_combo.set(list(self.jetson_ips.keys())[0])
        self.jetson_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(ind_control_frame, text="Start", command=lambda: self.generic_command_individual('start_tracking')).pack(side=tk.LEFT, padx=2)
        ttk.Button(ind_control_frame, text="Stop", command=lambda: self.generic_command_individual('stop_tracking')).pack(side=tk.LEFT, padx=2)
        ttk.Button(ind_control_frame, text="Reboot", command=lambda: self.power_control(self.jetson_var.get(), 'reboot')).pack(side=tk.LEFT, padx=10)
        tk.Button(ind_control_frame, text="⚠ Power Off", command=lambda: self.power_control(self.jetson_var.get(), 'power_off'), bg='#ff9999').pack(side=tk.LEFT, padx=2)
        return frame
    def _create_info_panel(self, parent):
        frame = ttk.Frame(parent, width=350); frame.pack_propagate(False)
        status_frame = ttk.LabelFrame(frame, text="Status", padding=5); status_frame.pack(fill=tk.X, pady=(0, 10))
        self.status_text = tk.Text(status_frame, height=5); self.status_text.pack(fill=tk.X, expand=True, pady=2)
        file_frame = ttk.LabelFrame(frame, text="File Management", padding=5); file_frame.pack(fill=tk.X, pady=(0, 10))
        self.file_listbox = tk.Listbox(file_frame, height=8); self.file_listbox.pack(fill=tk.X, expand=True)
        file_btn_frame = ttk.Frame(file_frame); file_btn_frame.pack(fill=tk.X)
        ttk.Button(file_btn_frame, text="Refresh List", command=self.refresh_file_list).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(file_btn_frame, text="Download Selected", command=self.download_selected_file).pack(side=tk.LEFT, expand=True, fill=tk.X)
        log_frame = ttk.LabelFrame(frame, text="Logs", padding=5); log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10); self.log_text.pack(fill=tk.BOTH, expand=True)
        return frame
    def _setup_camera_views(self):
        rows, cols = 2, (len(self.jetson_ips) + 1) // 2
        for i, jetson_name in enumerate(self.jetson_ips.keys()):
            r, c = i // cols, i % cols; frame = ttk.LabelFrame(self.camera_container, text=jetson_name, padding=5);
            frame.grid(row=r, column=c, padx=5, pady=5, sticky="nsew"); img_label = ttk.Label(frame, text="Disconnected", background='gray');
            img_label.pack(fill=tk.BOTH, expand=True); self.camera_views[jetson_name] = {'label': img_label}
            self.camera_container.rowconfigure(r, weight=1); self.camera_container.columnconfigure(c, weight=1)
    def load_jetson_config(self):
        config = configparser.ConfigParser(); config_file = "jetson_config.ini"
        if not os.path.exists(config_file):
            config['JETSONS'] = {'jetson1': '192.168.0.9:8888'};
            with open(config_file, 'w') as f: config.write(f)
        config.read(config_file)
        return dict(config['JETSONS'])
    def log(self, message): self.root.after(0, lambda: self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n") or self.log_text.see(tk.END))
    def update_camera_view(self, name, photo):
        label = self.camera_views.get(name, {}).get('label')
        if label: label.configure(image=photo, text=""); label.image = photo
    def update_disconnected_view(self, name):
        label = self.camera_views.get(name, {}).get('label')
        if label: label.configure(image='', text="Disconnected", background='gray'); label.image = None
    def update_status_display(self):
        status_info = ""
        with self.connections_lock:
            all_jetsons = self.jetson_ips.copy(); connected_jetsons = self.jetson_connections.copy()
        for name, ip in all_jetsons.items():
            status = "Connected" if name in connected_jetsons else "Disconnected"
            status_info += f"{name} ({ip}): {status}\n"
        self.status_text.delete(1.0, tk.END); self.status_text.insert(tk.END, status_info)
            
if __name__ == "__main__":
    root = tk.Tk()
    app = JetsonController(root)
    root.mainloop()