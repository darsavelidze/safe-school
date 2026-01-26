import tkinter as tk
import threading
import time
import requests
import jwt
import logging

SERVER_URL = 'http://localhost:5000/sensor-data'
SECRET_KEY = 'supersecretkey'
SCHOOL_ID = 'school_1'

# Генерация токена для школы
payload = {'school_id': SCHOOL_ID, 'exp': int(time.time()) + 60*60*24}
token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')
headers = {'Authorization': f'Bearer {token}'}

NUM_SENSORS = 5
sensor_values = [20 for _ in range(NUM_SENSORS)]

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Функция отправки данных на сервер
def send_data_loop():
    while True:
        for i in range(NUM_SENSORS):
            data = {
                'sensor_id': f'sensor_{i+1}',
                'value': sensor_values[i],
                'timestamp': int(time.time())
            }
            try:
                resp = requests.post(SERVER_URL, json=data, headers=headers, timeout=2)
                logging.info(f'Отправка sensor_{i+1}: {data}, ответ: {resp.status_code} {resp.text}')
            except Exception as e:
                logging.error(f'Ошибка отправки sensor_{i+1}: {e}')
        time.sleep(1)

# GUI
root = tk.Tk()
root.title('Температурные датчики (симуляция)')
frames = []
sliders = []
labels = []

def update_label(idx, val):
    sensor_values[idx] = float(val)
    labels[idx]['text'] = f'Датчик {idx+1}: {val}°C'
    logging.info(f'Изменение sensor_{idx+1}: {val}°C')

for i in range(NUM_SENSORS):
    frame = tk.Frame(root)
    frame.pack(pady=8)
    lbl = tk.Label(frame, text=f'Датчик {i+1}: {sensor_values[i]}°C', width=20)
    lbl.pack(side=tk.LEFT)
    slider = tk.Scale(frame, from_=0, to=50, orient=tk.HORIZONTAL, resolution=0.1,
                      length=300, command=lambda val, idx=i: update_label(idx, val))
    slider.set(sensor_values[i])
    slider.pack(side=tk.LEFT)
    labels.append(lbl)
    sliders.append(slider)
    frames.append(frame)

# Запуск потока отправки данных
threading.Thread(target=send_data_loop, daemon=True).start()

root.mainloop()
