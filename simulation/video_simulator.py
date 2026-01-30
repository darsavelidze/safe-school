"""
Симулятор видеокамер для системы мониторинга школы.
Позволяет загружать видеофайлы и потоково отправлять кадры на сервер.
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
import requests
import jwt
import cv2
import base64
import logging
import os

# Конфигурация
SERVER_URL = 'http://localhost:5000'
SECRET_KEY = 'supersecretkey'
DEFAULT_SCHOOL_ID = 'school_1'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

class VideoSimulator:
    def __init__(self, root):
        self.root = root
        self.root.title('Симулятор видеокамер')
        self.root.geometry('700x600')
        
        self.school_id = DEFAULT_SCHOOL_ID
        self.token = None
        self.cameras = {}  # camera_id -> { path, cap, thread, running, fps }
        self.camera_counter = 0
        
        self.setup_ui()
        self.get_token()
    
    def setup_ui(self):
        # --- Настройки подключения ---
        conn_frame = ttk.LabelFrame(self.root, text='Подключение к серверу', padding=10)
        conn_frame.pack(fill='x', padx=10, pady=5)
        
        ttk.Label(conn_frame, text='ID Школы:').grid(row=0, column=0, sticky='w')
        self.school_id_var = tk.StringVar(value=DEFAULT_SCHOOL_ID)
        self.school_id_entry = ttk.Entry(conn_frame, textvariable=self.school_id_var, width=30)
        self.school_id_entry.grid(row=0, column=1, padx=5)
        
        self.connect_btn = ttk.Button(conn_frame, text='Подключиться', command=self.get_token)
        self.connect_btn.grid(row=0, column=2, padx=5)
        
        self.status_label = ttk.Label(conn_frame, text='Не подключено', foreground='red')
        self.status_label.grid(row=0, column=3, padx=10)
        
        # --- Добавление камер ---
        add_frame = ttk.LabelFrame(self.root, text='Добавить видеокамеру', padding=10)
        add_frame.pack(fill='x', padx=10, pady=5)
        
        ttk.Label(add_frame, text='ID Камеры:').grid(row=0, column=0, sticky='w')
        self.camera_id_var = tk.StringVar(value='camera_1')
        self.camera_id_entry = ttk.Entry(add_frame, textvariable=self.camera_id_var, width=20)
        self.camera_id_entry.grid(row=0, column=1, padx=5)
        
        ttk.Label(add_frame, text='FPS:').grid(row=0, column=2, sticky='w')
        self.fps_var = tk.StringVar(value='2')
        self.fps_entry = ttk.Entry(add_frame, textvariable=self.fps_var, width=5)
        self.fps_entry.grid(row=0, column=3, padx=5)
        
        self.select_video_btn = ttk.Button(add_frame, text='Выбрать видео...', command=self.add_camera)
        self.select_video_btn.grid(row=0, column=4, padx=10)
        
        # --- Список камер ---
        list_frame = ttk.LabelFrame(self.root, text='Активные камеры', padding=10)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        # Создаём Treeview для списка камер
        columns = ('camera_id', 'video_file', 'fps', 'status', 'people')
        self.cameras_tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=10)
        self.cameras_tree.heading('camera_id', text='ID Камеры')
        self.cameras_tree.heading('video_file', text='Видеофайл')
        self.cameras_tree.heading('fps', text='FPS')
        self.cameras_tree.heading('status', text='Статус')
        self.cameras_tree.heading('people', text='Людей')
        
        self.cameras_tree.column('camera_id', width=100)
        self.cameras_tree.column('video_file', width=250)
        self.cameras_tree.column('fps', width=50)
        self.cameras_tree.column('status', width=100)
        self.cameras_tree.column('people', width=80)
        
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.cameras_tree.yview)
        self.cameras_tree.configure(yscrollcommand=scrollbar.set)
        
        self.cameras_tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # --- Кнопки управления ---
        btn_frame = ttk.Frame(self.root, padding=10)
        btn_frame.pack(fill='x', padx=10, pady=5)
        
        self.start_btn = ttk.Button(btn_frame, text='▶ Запустить выбранную', command=self.start_selected)
        self.start_btn.pack(side='left', padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text='⏹ Остановить выбранную', command=self.stop_selected)
        self.stop_btn.pack(side='left', padx=5)
        
        self.start_all_btn = ttk.Button(btn_frame, text='▶▶ Запустить все', command=self.start_all)
        self.start_all_btn.pack(side='left', padx=5)
        
        self.stop_all_btn = ttk.Button(btn_frame, text='⏹⏹ Остановить все', command=self.stop_all)
        self.stop_all_btn.pack(side='left', padx=5)
        
        self.remove_btn = ttk.Button(btn_frame, text='✕ Удалить выбранную', command=self.remove_selected)
        self.remove_btn.pack(side='right', padx=5)
        
        # --- Лог ---
        log_frame = ttk.LabelFrame(self.root, text='Лог', padding=5)
        log_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        self.log_text = tk.Text(log_frame, height=8, state='disabled')
        self.log_text.pack(fill='both', expand=True)
        
        log_scrollbar = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
    
    def log(self, message):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', f'{time.strftime("%H:%M:%S")} {message}\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')
    
    def get_token(self):
        self.school_id = self.school_id_var.get().strip()
        if not self.school_id:
            messagebox.showerror('Ошибка', 'Введите ID школы')
            return
        
        try:
            resp = requests.get(f'{SERVER_URL}/get-token/{self.school_id}', timeout=5)
            if resp.ok:
                data = resp.json()
                self.token = data['token']
                self.status_label.config(text=f'Подключено: {self.school_id}', foreground='green')
                self.log(f'Подключено к серверу как {self.school_id}')
            else:
                raise Exception(resp.text)
        except Exception as e:
            self.status_label.config(text='Ошибка подключения', foreground='red')
            self.log(f'Ошибка: {e}')
            messagebox.showerror('Ошибка', f'Не удалось подключиться к серверу:\n{e}')
    
    def add_camera(self):
        if not self.token:
            messagebox.showerror('Ошибка', 'Сначала подключитесь к серверу')
            return
        
        camera_id = self.camera_id_var.get().strip()
        if not camera_id:
            messagebox.showerror('Ошибка', 'Введите ID камеры')
            return
        
        if camera_id in self.cameras:
            messagebox.showerror('Ошибка', f'Камера {camera_id} уже добавлена')
            return
        
        try:
            fps = float(self.fps_var.get())
            if fps <= 0 or fps > 30:
                raise ValueError()
        except:
            messagebox.showerror('Ошибка', 'FPS должен быть числом от 0.1 до 30')
            return
        
        # Выбор видеофайла
        video_path = filedialog.askopenfilename(
            title='Выберите видеофайл',
            filetypes=[
                ('Видео файлы', '*.mp4 *.avi *.mkv *.mov *.wmv'),
                ('Все файлы', '*.*')
            ]
        )
        
        if not video_path:
            return
        
        # Проверяем что видео открывается
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            messagebox.showerror('Ошибка', 'Не удалось открыть видеофайл')
            return
        cap.release()
        
        # Добавляем камеру
        self.cameras[camera_id] = {
            'path': video_path,
            'fps': fps,
            'running': False,
            'thread': None,
            'cap': None,
            'people_count': 0
        }
        
        # Добавляем в дерево
        filename = os.path.basename(video_path)
        self.cameras_tree.insert('', 'end', iid=camera_id, values=(camera_id, filename, fps, 'Остановлена', '—'))
        
        self.log(f'Добавлена камера {camera_id}: {filename}')
        
        # Увеличиваем счётчик для следующей камеры
        self.camera_counter += 1
        self.camera_id_var.set(f'camera_{self.camera_counter + 1}')
    
    def start_camera(self, camera_id):
        if camera_id not in self.cameras:
            return
        
        cam = self.cameras[camera_id]
        if cam['running']:
            return
        
        cam['running'] = True
        cam['thread'] = threading.Thread(target=self.camera_loop, args=(camera_id,), daemon=True)
        cam['thread'].start()
        
        self.cameras_tree.set(camera_id, 'status', 'Работает')
        self.log(f'Камера {camera_id} запущена')
    
    def stop_camera(self, camera_id):
        if camera_id not in self.cameras:
            return
        
        cam = self.cameras[camera_id]
        cam['running'] = False
        
        self.cameras_tree.set(camera_id, 'status', 'Остановлена')
        self.log(f'Камера {camera_id} остановлена')
    
    def camera_loop(self, camera_id):
        cam = self.cameras[camera_id]
        cap = cv2.VideoCapture(cam['path'])
        
        if not cap.isOpened():
            self.log(f'Ошибка открытия видео для {camera_id}')
            cam['running'] = False
            return
        
        interval = 1.0 / cam['fps']
        headers = {'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}
        
        while cam['running']:
            ret, frame = cap.read()
            
            if not ret:
                # Перематываем видео на начало
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
                if not ret:
                    break
            
            try:
                # Уменьшаем размер для быстрой передачи
                frame_small = cv2.resize(frame, (640, 480))
                
                # Кодируем в JPEG
                _, buffer = cv2.imencode('.jpg', frame_small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                frame_b64 = base64.b64encode(buffer).decode('utf-8')
                
                # Отправляем на сервер (используем annotated эндпоинт для поддержки просмотра)
                data = {
                    'camera_id': camera_id,
                    'frame': frame_b64
                }
                
                # Используем annotated эндпоинт для поддержки просмотра с bounding boxes
                resp = requests.post(f'{SERVER_URL}/video-frame-annotated', json=data, headers=headers, timeout=10)
                
                if resp.ok:
                    result = resp.json()
                    people_count = result.get('people_count', 0)
                    cam['people_count'] = people_count
                    
                    # Обновляем UI в главном потоке
                    self.root.after(0, lambda cid=camera_id, cnt=people_count: 
                                   self.cameras_tree.set(cid, 'people', str(cnt)))
                else:
                    self.log(f'Ошибка отправки кадра {camera_id}: {resp.status_code}')
                    
            except Exception as e:
                self.log(f'Ошибка камеры {camera_id}: {e}')
            
            time.sleep(interval)
        
        cap.release()
        self.root.after(0, lambda: self.cameras_tree.set(camera_id, 'status', 'Остановлена'))
    
    def start_selected(self):
        selection = self.cameras_tree.selection()
        if not selection:
            messagebox.showwarning('Внимание', 'Выберите камеру')
            return
        for camera_id in selection:
            self.start_camera(camera_id)
    
    def stop_selected(self):
        selection = self.cameras_tree.selection()
        if not selection:
            messagebox.showwarning('Внимание', 'Выберите камеру')
            return
        for camera_id in selection:
            self.stop_camera(camera_id)
    
    def start_all(self):
        for camera_id in self.cameras:
            self.start_camera(camera_id)
    
    def stop_all(self):
        for camera_id in self.cameras:
            self.stop_camera(camera_id)
    
    def remove_selected(self):
        selection = self.cameras_tree.selection()
        if not selection:
            messagebox.showwarning('Внимание', 'Выберите камеру')
            return
        
        for camera_id in selection:
            self.stop_camera(camera_id)
            del self.cameras[camera_id]
            self.cameras_tree.delete(camera_id)
            self.log(f'Камера {camera_id} удалена')

def main():
    root = tk.Tk()
    app = VideoSimulator(root)
    root.mainloop()

if __name__ == '__main__':
    main()
