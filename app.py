# Final fix: encode lat/lon/radius into the QR URL to ensure QR works even after Render restarts
from flask import Flask, render_template, request, redirect, url_for, send_file, session
import pandas as pd
import qrcode
import uuid
import os
from datetime import datetime
from io import BytesIO
from pytz import timezone
import math

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join('static', 'qr'), exist_ok=True)

attendance = {}
used_devices = {}

BASE_URL = 'https://attendance-system-project.onrender.com/'

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['file']
    latitude = request.form.get('latitude')
    longitude = request.form.get('longitude')
    radius = request.form.get('radius')

    if not latitude or not longitude or not radius:
        return 'Location and radius are required.'

    try:
        latitude = float(latitude)
        longitude = float(longitude)
        radius = float(radius)
    except ValueError:
        return 'Invalid location or radius values.'

    if file:
        df = pd.read_csv(file)
        session_id = str(uuid.uuid4())
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], session_id + '.csv')
        df.to_csv(filepath, index=False)

        used_devices[session_id] = set()

        # Embed session data directly in QR URL
        url = f"{BASE_URL}scan/{session_id}?lat={latitude}&lon={longitude}&radius={radius}"
        qr = qrcode.make(url)
        qr_path = os.path.join('static', 'qr', session_id + '.png')
        qr.save(qr_path)

        return render_template('qr_display.html', qr_path='qr/' + session_id + '.png', session_id=session_id)
    else:
        return 'File not uploaded.'

@app.route('/scan/<session_id>')
def scan(session_id):
    # Retrieve lat/lon/radius from query params
    try:
        latitude = float(request.args.get('lat'))
        longitude = float(request.args.get('lon'))
        radius = float(request.args.get('radius'))
    except (TypeError, ValueError):
        return 'Invalid or missing session parameters.'

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], session_id + '.csv')
    if not os.path.exists(filepath):
        return 'Attendance data file is missing. Please contact the teacher.'

    df = pd.read_csv(filepath)
    students = df.to_dict(orient='records')
    roll_col = df.columns[0]
    name_col = df.columns[1]

    if 'user_token' not in session:
        session['user_token'] = str(uuid.uuid4())

    # Store session info temporarily in global map for validation during mark
    session_data = {
        'filename': filepath,
        'latitude': latitude,
        'longitude': longitude,
        'radius': radius
    }
    session['current_session'] = session_data

    return render_template('student_list.html', students=students, session_id=session_id, roll_col=roll_col, name_col=name_col)

@app.route('/mark', methods=['POST'])
def mark_attendance():
    session_id = request.form['session_id']
    name = request.form['name'].strip().lower()
    roll = request.form['roll_number'].strip().lower()
    lat = request.form.get('latitude')
    lon = request.form.get('longitude')
    ip_address = request.remote_addr
    device_token = session.get('user_token')

    if not lat or not lon:
        return 'Location access is required to mark attendance.'

    try:
        lat = float(lat)
        lon = float(lon)
    except ValueError:
        return 'Invalid latitude or longitude.'

    session_data = session.get('current_session')
    if not session_data:
        return 'Session metadata missing. Please re-scan the QR code.'

    dist = haversine(lat, lon, session_data['latitude'], session_data['longitude'])
    if dist > session_data['radius']:
        return f'You are outside the allowed area (Distance: {dist:.2f} m). Attendance not marked.'

    if session_id not in attendance:
        attendance[session_id] = []

    if session_id not in used_devices:
        used_devices[session_id] = set()

    device_key = f"{ip_address}_{device_token}"
    if device_key in used_devices[session_id]:
        return 'Attendance already submitted from this device.'

    filepath = session_data['filename']
    df = pd.read_csv(filepath)
    df.columns = df.columns.str.strip().str.lower()
    valid_pairs = {(str(row[df.columns[0]]).strip().lower(), str(row[df.columns[1]]).strip().lower()) for _, row in df.iterrows()}

    if (roll, name) not in valid_pairs:
        return 'Invalid roll number and name combination.'

    for record in attendance[session_id]:
        if record['roll'] == roll:
            return 'Attendance already marked for this roll number.'

    india_time = datetime.now(timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')

    attendance[session_id].append({
        'name': name,
        'roll': roll,
        'timestamp': india_time,
        'ip': ip_address
    })

    used_devices[session_id].add(device_key)

    return 'Attendance marked successfully!'

@app.route('/download/<session_id>')
def download(session_id):
    if session_id not in attendance:
        return 'No attendance data for this session.'

    df = pd.DataFrame(attendance[session_id])
    output = BytesIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return send_file(output, mimetype='text/csv', as_attachment=True, download_name=f'attendance_{session_id}.csv')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
