import threading
import queue
from collections import defaultdict, deque
from flask import Flask, request, jsonify, Response
from flask_socketio import SocketIO, emit
import jwt
import time
import logging
import json
import os
import hashlib
import base64
import cv2
import numpy as np
from flask_cors import CORS
from ultralytics import YOLO

SECRET_KEY = 'supersecretkey'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- Хранилища данных ---
# Зарегистрированные школы: { school_id: { name, password_hash, created_at } }
schools_store = {}
schools_lock = threading.Lock()

# school_id -> { sensor_id -> deque([measurements], maxlen=100) }
data_store = defaultdict(lambda: defaultdict(lambda: deque(maxlen=100)))
data_lock = threading.Lock()

# Координаты датчиков: school_id -> { floor_idx -> { sensor_id -> {x, y} } }
sensor_positions_store = defaultdict(lambda: defaultdict(dict))
sensor_positions_lock = threading.Lock()

# Координаты камер: school_id -> { floor_idx -> { camera_id -> {x, y} } }
camera_positions_store = defaultdict(lambda: defaultdict(dict))
camera_positions_lock = threading.Lock()

# Данные с камер (количество людей): school_id -> { camera_id -> { count, timestamp } }
camera_data_store = defaultdict(dict)
camera_data_lock = threading.Lock()

# Схемы этажей: school_id -> [floor_points, ...]
floors_store = defaultdict(list)
floors_lock = threading.Lock()

# Файл для персистентного хранения
DATA_FILE = 'school_data.json'

# --- YOLO модель для детекции людей ---
yolo_model = None
yolo_lock = threading.Lock()

def load_yolo():
    global yolo_model
    try:
        # Удаляем повреждённый файл если он существует и меньше ожидаемого размера
        model_path = 'yolov8n.pt'
        if os.path.exists(model_path):
            if os.path.getsize(model_path) < 6_000_000:  # Меньше 6MB - повреждён
                logging.warning(f'Removing corrupted YOLO model file')
                os.remove(model_path)
        
        # Используем YOLOv8n (nano) для быстрой работы
        yolo_model = YOLO(model_path)
        logging.info('YOLO model loaded successfully')
    except Exception as e:
        logging.error(f'Failed to load YOLO model: {e}')
        yolo_model = None

# Загружаем YOLO в отдельном потоке чтобы не блокировать старт сервера
threading.Thread(target=load_yolo, daemon=True).start()

def detect_people(frame):
    """Детектирует людей на кадре, возвращает количество"""
    global yolo_model
    if yolo_model is None:
        logging.warning('YOLO model not loaded, returning 0')
        return 0
    
    with yolo_lock:
        try:
            # Детекция с пониженным порогом уверенности для лучшего обнаружения
            results = yolo_model(frame, verbose=False, conf=0.25)
            # Класс 0 в COCO - это 'person'
            people_count = 0
            for r in results:
                for box in r.boxes:
                    if int(box.cls[0]) == 0:  # person class
                        people_count += 1
            return people_count
        except Exception as e:
            logging.error(f'Detection error: {e}')
            return 0

def detect_people_with_boxes(frame):
    """Детектирует людей и возвращает кадр с bounding boxes и количество"""
    global yolo_model
    if yolo_model is None:
        return frame, 0, []
    
    with yolo_lock:
        try:
            results = yolo_model(frame, verbose=False, conf=0.25)
            people_count = 0
            boxes_list = []
            
            for r in results:
                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    if cls_id == 0:  # person class
                        people_count += 1
                        # Получаем координаты bounding box
                        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                        conf = float(box.conf[0])
                        boxes_list.append({'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'conf': conf})
                        
                        # Рисуем bounding box на кадре
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        label = f'Person {conf:.2f}'
                        cv2.putText(frame, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            
            return frame, people_count, boxes_list
        except Exception as e:
            logging.error(f'Detection with boxes error: {e}')
            return frame, 0, []

# --- Функции загрузки/сохранения данных ---
def load_data():
    global sensor_positions_store, floors_store, schools_store, camera_positions_store
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Загрузка школ
                schools_store = data.get('schools', {})
                
                # Загрузка позиций датчиков
                for school_id, school_data in data.get('sensor_positions', {}).items():
                    for floor_idx, sensors in school_data.items():
                        sensor_positions_store[school_id][int(floor_idx)] = sensors
                
                # Загрузка позиций камер
                for school_id, school_data in data.get('camera_positions', {}).items():
                    for floor_idx, cameras in school_data.items():
                        camera_positions_store[school_id][int(floor_idx)] = cameras
                
                # Загрузка этажей
                for school_id, floors in data.get('floors', {}).items():
                    floors_store[school_id] = floors
                    
                logging.info('Data loaded from file')
        except Exception as e:
            logging.error(f'Error loading data: {e}')

def save_data():
    try:
        data = {
            'schools': schools_store,
            'sensor_positions': {k: {str(fk): fv for fk, fv in v.items()} for k, v in sensor_positions_store.items()},
            'camera_positions': {k: {str(fk): fv for fk, fv in v.items()} for k, v in camera_positions_store.items()},
            'floors': dict(floors_store)
        }
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info('Data saved to file')
    except Exception as e:
        logging.error(f'Error saving data: {e}')

load_data()

# --- Утилиты ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token(school_id):
    payload = {'school_id': school_id, 'exp': int(time.time()) + 60*60*24}
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')

def require_jwt(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', None)
        if not auth or not auth.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid Authorization header'}), 401
        token = auth.split(' ', 1)[1]
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
            school_id = payload['school_id']
        except Exception as e:
            logging.error(f'JWT error: {e}')
            return jsonify({'error': 'Invalid token', 'details': str(e)}), 401
        return f(school_id, *args, **kwargs)
    return wrapper

# --- API: Регистрация и авторизация школ ---
@app.route('/health')
def health():
    return jsonify(status="all okay", yolo_loaded=yolo_model is not None)

@app.route('/register', methods=['POST'])
def register_school():
    """Регистрация новой школы"""
    data = request.get_json(force=True)
    school_id = data.get('school_id', '').strip()
    school_name = data.get('name', '').strip()
    password = data.get('password', '')
    
    if not school_id or not password:
        return jsonify({'error': 'school_id and password required'}), 400
    
    if len(school_id) < 3:
        return jsonify({'error': 'school_id must be at least 3 characters'}), 400
    
    if len(password) < 4:
        return jsonify({'error': 'password must be at least 4 characters'}), 400
    
    with schools_lock:
        if school_id in schools_store:
            return jsonify({'error': 'School already exists'}), 409
        
        schools_store[school_id] = {
            'name': school_name or school_id,
            'password_hash': hash_password(password),
            'created_at': int(time.time())
        }
    
    save_data()
    token = generate_token(school_id)
    logging.info(f'School registered: {school_id}')
    return jsonify({'status': 'ok', 'token': token, 'school_id': school_id})

@app.route('/login', methods=['POST'])
def login_school():
    """Авторизация школы"""
    data = request.get_json(force=True)
    school_id = data.get('school_id', '').strip()
    password = data.get('password', '')
    
    if not school_id or not password:
        return jsonify({'error': 'school_id and password required'}), 400
    
    with schools_lock:
        school = schools_store.get(school_id)
        if not school:
            return jsonify({'error': 'School not found'}), 404
        
        if school['password_hash'] != hash_password(password):
            return jsonify({'error': 'Invalid password'}), 401
    
    token = generate_token(school_id)
    logging.info(f'School logged in: {school_id}')
    return jsonify({'status': 'ok', 'token': token, 'school_id': school_id, 'name': school['name']})

@app.route('/get-token/<school_id>')
def get_token(school_id):
    """Получить токен для школы (для тестирования/симулятора)"""
    token = generate_token(school_id)
    return jsonify({'token': token, 'school_id': school_id})

# --- API: Данные датчиков температуры ---
@app.route('/sensor-data', methods=['POST'])
@require_jwt
def receive_data(school_id):
    data = request.get_json(force=True)
    sensor_id = data.get('sensor_id')
    # Поддержка обоих ключей: 'value' и 'temperature'
    value = data.get('temperature') or data.get('value')
    timestamp = data.get('timestamp', int(time.time()))
    if not sensor_id or value is None:
        return jsonify({'error': 'sensor_id and value/temperature required'}), 400
    with data_lock:
        data_store[school_id][sensor_id].append({'value': value, 'timestamp': timestamp})
    return jsonify({'status': 'ok'})

@app.route('/sensor-data', methods=['GET'])
@require_jwt
def get_data(school_id):
    sensor_id = request.args.get('sensor_id')
    with data_lock:
        if sensor_id:
            data = list(data_store[school_id][sensor_id])
        else:
            data = {sid: list(queue) for sid, queue in data_store[school_id].items()}
    return jsonify({'data': data})

# --- API: Схемы этажей ---
@app.route('/floors', methods=['GET'])
@require_jwt
def get_floors(school_id):
    with floors_lock:
        floors = floors_store.get(school_id, [])
    return jsonify({'floors': floors})

@app.route('/floors', methods=['POST'])
@require_jwt
def save_floors(school_id):
    data = request.get_json(force=True)
    floors = data.get('floors', [])
    with floors_lock:
        floors_store[school_id] = floors
    save_data()
    return jsonify({'status': 'ok'})

# --- API: Позиции датчиков ---
@app.route('/sensor-positions', methods=['GET'])
@require_jwt
def get_sensor_positions(school_id):
    floor_idx = request.args.get('floor_idx')
    with sensor_positions_lock:
        if floor_idx is not None:
            positions = sensor_positions_store[school_id].get(int(floor_idx), {})
        else:
            positions = dict(sensor_positions_store[school_id])
    return jsonify({'positions': positions})

@app.route('/sensor-positions', methods=['POST'])
@require_jwt
def save_sensor_positions(school_id):
    data = request.get_json(force=True)
    floor_idx = data.get('floor_idx')
    positions = data.get('positions', {})
    if floor_idx is None:
        return jsonify({'error': 'floor_idx required'}), 400
    with sensor_positions_lock:
        sensor_positions_store[school_id][int(floor_idx)] = positions
    save_data()
    return jsonify({'status': 'ok'})

# --- API: Позиции камер ---
@app.route('/camera-positions', methods=['GET'])
@require_jwt
def get_camera_positions(school_id):
    floor_idx = request.args.get('floor_idx')
    with camera_positions_lock:
        if floor_idx is not None:
            positions = camera_positions_store[school_id].get(int(floor_idx), {})
        else:
            positions = dict(camera_positions_store[school_id])
    return jsonify({'positions': positions})

@app.route('/camera-positions', methods=['POST'])
@require_jwt
def save_camera_positions(school_id):
    data = request.get_json(force=True)
    floor_idx = data.get('floor_idx')
    positions = data.get('positions', {})
    if floor_idx is None:
        return jsonify({'error': 'floor_idx required'}), 400
    with camera_positions_lock:
        camera_positions_store[school_id][int(floor_idx)] = positions
    save_data()
    return jsonify({'status': 'ok'})

# --- API: Данные камер (количество людей) ---
@app.route('/camera-data', methods=['GET'])
@require_jwt
def get_camera_data(school_id):
    with camera_data_lock:
        data = dict(camera_data_store.get(school_id, {}))
    return jsonify({'data': data})

# --- API: Загрузка видео кадров (для симулятора) ---
@app.route('/video-frame', methods=['POST'])
@require_jwt
def receive_video_frame(school_id):
    """Получение кадра видео, детекция людей"""
    data = request.get_json(force=True)
    camera_id = data.get('camera_id')
    frame_b64 = data.get('frame')  # base64 encoded JPEG
    
    if not camera_id or not frame_b64:
        return jsonify({'error': 'camera_id and frame required'}), 400
    
    try:
        # Декодируем изображение
        frame_bytes = base64.b64decode(frame_b64)
        nparr = np.frombuffer(frame_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            logging.error(f'Failed to decode frame from {camera_id}, base64 length: {len(frame_b64)}')
            return jsonify({'error': 'Invalid frame data'}), 400
        
        # Логируем размер кадра
        h, w = frame.shape[:2]
        logging.debug(f'Frame from {camera_id}: {w}x{h}')
        
        # Детектируем людей
        people_count = detect_people(frame)
        
        # Сохраняем результат
        with camera_data_lock:
            camera_data_store[school_id][camera_id] = {
                'count': people_count,
                'timestamp': int(time.time())
            }
        
        # Отправляем обновление через WebSocket
        socketio.emit('camera_update', {
            'school_id': school_id,
            'camera_id': camera_id,
            'count': people_count,
            'timestamp': int(time.time())
        }, namespace='/')
        
        logging.info(f'Frame from {camera_id}: detected {people_count} people')
        return jsonify({'status': 'ok', 'people_count': people_count})
        
    except Exception as e:
        logging.error(f'Error processing frame: {e}')
        return jsonify({'error': str(e)}), 500

# Хранилище последних кадров с аннотациями: school_id -> camera_id -> { frame_b64, count, timestamp }
annotated_frames_store = defaultdict(dict)
annotated_frames_lock = threading.Lock()

@app.route('/video-frame-annotated', methods=['POST'])
@require_jwt
def receive_video_frame_annotated(school_id):
    """Получение кадра, детекция людей с bounding boxes, возврат аннотированного кадра"""
    data = request.get_json(force=True)
    camera_id = data.get('camera_id')
    frame_b64 = data.get('frame')
    
    if not camera_id or not frame_b64:
        return jsonify({'error': 'camera_id and frame required'}), 400
    
    try:
        # Декодируем изображение
        frame_bytes = base64.b64decode(frame_b64)
        nparr = np.frombuffer(frame_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            return jsonify({'error': 'Invalid frame data'}), 400
        
        # Детектируем с bounding boxes
        annotated_frame, people_count, boxes = detect_people_with_boxes(frame)
        
        # Кодируем обратно в JPEG
        _, buffer = cv2.imencode('.jpg', annotated_frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        annotated_b64 = base64.b64encode(buffer).decode('utf-8')
        
        # Сохраняем для просмотра
        with annotated_frames_lock:
            annotated_frames_store[school_id][camera_id] = {
                'frame': annotated_b64,
                'count': people_count,
                'boxes': boxes,
                'timestamp': int(time.time())
            }
        
        # Сохраняем результат в общий store
        with camera_data_lock:
            camera_data_store[school_id][camera_id] = {
                'count': people_count,
                'timestamp': int(time.time())
            }
        
        # WebSocket уведомление с кадром
        socketio.emit('camera_frame', {
            'school_id': school_id,
            'camera_id': camera_id,
            'frame': annotated_b64,
            'count': people_count,
            'boxes': boxes,
            'timestamp': int(time.time())
        }, namespace='/')
        
        return jsonify({
            'status': 'ok',
            'people_count': people_count,
            'annotated_frame': annotated_b64,
            'boxes': boxes
        })
        
    except Exception as e:
        logging.error(f'Error processing annotated frame: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/camera-stream/<camera_id>', methods=['GET'])
@require_jwt
def get_camera_stream(school_id, camera_id):
    """Получить последний аннотированный кадр с камеры"""
    with annotated_frames_lock:
        cam_data = annotated_frames_store.get(school_id, {}).get(camera_id)
    
    if not cam_data:
        return jsonify({'error': 'No frame available'}), 404
    
    return jsonify(cam_data)

# --- WebSocket для реального времени ---
@socketio.on('connect')
def handle_connect():
    logging.info(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    logging.info(f'Client disconnected: {request.sid}')

@socketio.on('subscribe')
def handle_subscribe(data):
    school_id = data.get('school_id')
    logging.info(f'Client {request.sid} subscribed to {school_id}')

if __name__ == '__main__':
    logging.info('Starting Flask server with SocketIO...')
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
