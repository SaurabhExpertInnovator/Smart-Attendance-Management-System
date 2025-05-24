from flask import Flask, render_template, request, redirect, send_file, session, url_for
from werkzeug.utils import secure_filename
import os
import pandas as pd
import qrcode
import uuid
import json
from datetime import datetime
import io
from pytz import timezone
from math import radians, sin, cos, sqrt, atan2

app = Flask(__name__)
app.secret_key = 'secret_key'
UPLOAD_FOLDER = 'uploads'
QR_FOLDER = 'static/qr'
SESSIONS_FILE = 'sessions.json'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['QR_FOLDER'] = QR_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
if not os.path.exists(QR_FOLDER):
    os.makedirs(QR_FOLDER)

# Load existing sessions
if os.path.exists(SESSIONS_FILE):
    with open(SESSIONS_FILE, 'r') as f:
        attendance_sessions = json.load(f)
else:
    attendance_sessions = {}

def save_sessions():
    with open(SESSIONS_FILE, 'w') as f:
        json.dump(attendance_sessions, f)

def is_within_radius(lat1, lon1, lat2, lon2, radius):
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    distance = R * c
    return distance <= radius

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files['file']
    radius = request.form['radius']
    latitude = request.form['latitude']
    longitude = request.form['longitude']

    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        df = pd.read_csv(filepath)
        if 'Name' not in df.columns or 'Roll' not in df.columns:
            return 'CSV must have Name and Roll columns'

        session_id = str(uuid.uuid4())
        qr_data = url_for('scan_qr', session_id=session_id, _external=True)

        img = qrcode.make(qr_data)
        qr_path = os.path.join(app.config['QR_FOLDER'], f'{session_id}.png')
        img.save(qr_path)

        attendance_sessions[session_id] = {
            'filepath': filepath,
            'latitude': float(latitude),
            'longitude': float(longitude),
            'radius': float(radius),
            'marked_devices': {},  # device_id -> roll
            'marked_rolls': [],    # list of roll numbers
            'timestamp': datetime.now(timezone('Asia/Kolkata')).isoformat()
        }
        save_sessions()

        return render_template('qr_display.html', qr_path=qr_path, session_id=session_id)
    return 'No file uploaded'

@app.route('/scan/<session_id>')
def scan_qr(session_id):
    session_data = attendance_sessions.get(session_id)
    if not session_data:
        return 'Invalid session ID'

    df = pd.read_csv(session_data['filepath'])
    return render_template('student_list.html', students=df.to_dict('records'),
                           roll_col='Roll', name_col='Name', session_id=session_id)

@app.route('/mark', methods=['POST'])
def mark_attendance():
    session_id = request.form['session_id']
    roll_number = request.form['roll_number']
    latitude = float(request.form['latitude'])
    longitude = float(request.form['longitude'])
    device_id = request.form['device_id']

    session_data = attendance_sessions.get(session_id)
    if not session_data:
        return 'Invalid session ID'

    if not is_within_radius(latitude, longitude, session_data['latitude'], session_data['longitude'], session_data['radius']):
        return 'You are outside the allowed location radius.'

    # Check device and roll logic
    if device_id in session_data['marked_devices']:
        return 'This device has already marked attendance.'

    if roll_number in session_data['marked_rolls']:
        return 'This student has already marked attendance.'

    # Mark attendance
    session_data['marked_devices'][device_id] = roll_number
    session_data['marked_rolls'].append(roll_number)

    df = pd.read_csv(session_data['filepath'])
    date_col = datetime.now(timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
    if date_col not in df.columns:
        df[date_col] = 0

    df.loc[df['Roll'] == roll_number, date_col] = 1
    df.to_csv(session_data['filepath'], index=False)
    save_sessions()

    return 'Attendance marked successfully!'

@app.route('/download/<session_id>')
def download_file(session_id):
    session_data = attendance_sessions.get(session_id)
    if not session_data:
        return 'Invalid session ID'

    df = pd.read_csv(session_data['filepath'])
    date_col = datetime.now(timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
    if date_col not in df.columns:
        df[date_col] = 0

    marked_rolls = set(session_data['marked_rolls'])
    for idx in df.index:
        if df.at[idx, 'Roll'] not in marked_rolls:
            df.at[idx, date_col] = 0

    output = io.BytesIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return send_file(output, mimetype='text/csv', as_attachment=True, download_name=f'attendance_{session_id}.csv')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
