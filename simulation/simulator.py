import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import requests
import jwt
import logging

SERVER_URL = 'http://localhost:5000/sensor-data'
SECRET_KEY = 'supersecretkey'

NUM_SENSORS = 5
sensor_values = [20 for _ in range(NUM_SENSORS)]

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Глобальные переменные для токена
current_token = None
current_headers = {}

def generate_token(school_id):
    """Генерация JWT токена для указанной школы"""
    global current_token, current_headers
    payload = {'school_id': school_id, 'exp': int(time.time()) + 60*60*24}
    current_token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')
    current_headers = {'Authorization': f'Bearer {current_token}'}
    logging.info(f'Токен сгенерирован для школы: {school_id}')
    return current_token

def send_data_loop():
    """Функция отправки данных на сервер"""
    while True:
        if not current_headers:
            time.sleep(1)
            continue
            
        for i in range(NUM_SENSORS):
            data = {
                'sensor_id': f'sensor_{i+1}',
                'temperature': sensor_values[i],  # Исправлено: было 'value', нужно 'temperature'
                'timestamp': int(time.time())
            }
            try:
                resp = requests.post(SERVER_URL, json=data, headers=current_headers, timeout=2)
                logging.info(f'Отправка sensor_{i+1}: {data["temperature"]}°C, ответ: {resp.status_code}')
            except Exception as e:
                logging.error(f'Ошибка отправки sensor_{i+1}: {e}')
        time.sleep(1)

# GUI
root = tk.Tk()
root.title('Температурные датчики (симуляция)')
root.geometry('500x450')

# Фрейм для выбора школы
school_frame = ttk.LabelFrame(root, text='Настройки школы', padding=10)
school_frame.pack(fill='x', padx=10, pady=5)

ttk.Label(school_frame, text='ID школы:').grid(row=0, column=0, sticky='w', padx=5)
school_id_var = tk.StringVar(value='school_1')
school_id_entry = ttk.Entry(school_frame, textvariable=school_id_var, width=30)
school_id_entry.grid(row=0, column=1, padx=5, pady=5)

token_status_label = ttk.Label(school_frame, text='Токен: не создан', foreground='red')
token_status_label.grid(row=1, column=0, columnspan=2, sticky='w', padx=5, pady=5)

def update_token():
    """Обновить токен для указанной школы"""
    school_id = school_id_var.get().strip()
    if not school_id:
        messagebox.showerror('Ошибка', 'Введите ID школы')
        return
    generate_token(school_id)
    token_status_label.config(text=f'Токен: создан для "{school_id}"', foreground='green')
    messagebox.showinfo('Успех', f'Токен создан для школы: {school_id}')

ttk.Button(school_frame, text='Применить', command=update_token).grid(row=0, column=2, padx=5)

# Фрейм для датчиков
sensors_frame = ttk.LabelFrame(root, text='Датчики температуры', padding=10)
sensors_frame.pack(fill='both', expand=True, padx=10, pady=5)

frames = []
sliders = []
labels = []

def update_label(idx, val):
    sensor_values[idx] = float(val)
    labels[idx]['text'] = f'Датчик {idx+1}: {val}°C'

for i in range(NUM_SENSORS):
    frame = tk.Frame(sensors_frame)
    frame.pack(pady=5, fill='x')
    lbl = tk.Label(frame, text=f'Датчик {i+1}: {sensor_values[i]}°C', width=20, anchor='w')
    lbl.pack(side=tk.LEFT)
    slider = tk.Scale(frame, from_=0, to=50, orient=tk.HORIZONTAL, resolution=0.1,
                      length=280, command=lambda val, idx=i: update_label(idx, val))
    slider.set(sensor_values[i])
    slider.pack(side=tk.LEFT, fill='x', expand=True)
    labels.append(lbl)
    sliders.append(slider)
    frames.append(frame)

# Статус отправки
status_frame = ttk.Frame(root, padding=5)
status_frame.pack(fill='x', padx=10, pady=5)
ttk.Label(status_frame, text='Статус: Данные отправляются каждую секунду после применения токена').pack()

# Генерируем токен по умолчанию
generate_token('school_1')
token_status_label.config(text='Токен: создан для "school_1"', foreground='green')

# Запуск потока отправки данных
threading.Thread(target=send_data_loop, daemon=True).start()

root.mainloop()
