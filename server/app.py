import threading
from collections import defaultdict, deque
from flask import Flask, request, jsonify
import jwt
import time
import logging
from flask_cors import CORS

SECRET_KEY = 'supersecretkey'  # Для генерации и проверки JWT

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

app = Flask(__name__)
CORS(app)

# school_id -> { sensor_id -> deque([measurements], maxlen=100) }
data_store = defaultdict(lambda: defaultdict(lambda: deque(maxlen=100)))
data_lock = threading.Lock()

# Пример генерации токена для школы (в реальности делайте отдельный сервис)
def generate_token(school_id):
    payload = {'school_id': school_id, 'exp': int(time.time()) + 60*60*24}
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')

# Декоратор для проверки JWT и получения school_id
def require_jwt(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', None)
        if not auth or not auth.startswith('Bearer '):
            logging.warning('Missing or invalid Authorization header')
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

@app.route('/health')
def health():
    return jsonify(status="all okay")

@app.route('/sensor-data', methods=['POST'])
@require_jwt
def receive_data(school_id):
    data = request.get_json(force=True)
    sensor_id = data.get('sensor_id')
    value = data.get('value')
    timestamp = data.get('timestamp', int(time.time()))
    if not sensor_id or value is None:
        logging.warning(f'Bad request from school {school_id}: {data}')
        return jsonify({'error': 'sensor_id and value required'}), 400
    with data_lock:
        data_store[school_id][sensor_id].append({'value': value, 'timestamp': timestamp})
    logging.info(f'Received data: school={school_id}, sensor={sensor_id}, value={value}, timestamp={timestamp}')
    return jsonify({'status': 'ok'})

@app.route('/sensor-data', methods=['GET'])
@require_jwt
def get_data(school_id):
    # ?sensor_id=... (опционально)
    sensor_id = request.args.get('sensor_id')
    with data_lock:
        if sensor_id:
            data = list(data_store[school_id][sensor_id])
        else:
            data = {sid: list(queue) for sid, queue in data_store[school_id].items()}
    logging.info(f'Data requested: school={school_id}, sensor={sensor_id}')
    return jsonify({'data': data})

if __name__ == '__main__':
    logging.info('Starting Flask server...')
    app.run(host='0.0.0.0', port=5000, debug=True)
