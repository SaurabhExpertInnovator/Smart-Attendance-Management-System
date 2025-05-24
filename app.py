# app.py
from flask import Flask, render_template, request, redirect, url_for, send_file, abort
import pandas as pd
import qrcode
import uuid
import os
import json
from datetime import datetime
from io import BytesIO
from pytz import timezone
import math

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
SESSIONS_FILE = 'sessions.json'

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

qr_folder = os.path.join('static', 'qr')
os.makedirs(qr_folder, exist_ok=True)

attendance = {}  # session_id -> {ips: {ip: roll}, rolls: set()}
BASE_URL = 'https://smart-attendance-management-system-tavt.onrender.com/'

# Load sessions from file
if os.path.exists(SESSIONS_FILE):
    with open(SESSIONS_FILE, 'r') as f:
        sessions = json.load(f)
else:
    sessions = {}

def save_sessions():
    with open(SESSIONS_FILE, 'w') as f:
        json.dump(sessions, f)

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
    radius = request.form.get('radius')
    latitude = request.form.get('latitude')
    longitude = request.form.get('longitude')

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

        sessions[session_id] = {
            'filename': filepath,
            'latitude': latitude,
            'longitude': longitude,
            'radius': radius
        }
        save_sessions()

        url = BASE_URL + 'scan/' + session_id
        qr = qrcode.make(url)
        qr_path = os.path.join(qr_folder, session_id + '.png')
        qr.save(qr_path)

        return render_template('qr_display.html', qr_path='qr/' + session_id + '.png', session_id=session_id)
    else:
        return 'File not uploaded.'

@app.route('/scan/<session_id>')
def scan(session_id):
    session = sessions.get(session_id)
    if not session:
        return 'Invalid session ID.'

    df = pd.read_csv(session['filename'])
    students = df.to_dict(orient='records')
    roll_col = df.columns[0]
    name_col = df.columns[1]

    return render_template('student_list.html', students=students, session_id=session_id, roll_col=roll_col, name_col=name_col)

@app.route('/mark', methods=['POST'])
def mark_attendance():
    try:
        session_id = request.form['session_id']
        roll = request.form['roll_number']
        lat = request.form.get('latitude')
        lon = request.form.get('longitude')
        user_ip = request.remote_addr
    except Exception as e:
        return 'Invalid form data: {}'.format(str(e)), 400

    if not lat or not lon or not roll or not session_id:
        return 'Missing required fields.', 400

    try:
        lat = float(lat)
        lon = float(lon)
    except ValueError:
        return 'Invalid latitude or longitude.', 400

    session = sessions.get(session_id)
    if not session:
        return 'Invalid session.', 400

    dist = haversine(lat, lon, session['latitude'], session['longitude'])
    if dist > session['radius']:
        return f'You are outside the allowed area (Distance: {dist:.2f} m). Attendance not marked.', 400

    if session_id not in attendance:
        attendance[session_id] = {'ips': {}, 'rolls': set()}

    ip_map = attendance[session_id]['ips']
    roll_set = attendance[session_id]['rolls']

    # Check if this student has already marked attendance
    if roll in roll_set:
        return 'Attendance already marked for this student.', 400

    # Check if the same IP was used for a different roll
    if user_ip in ip_map and ip_map[user_ip] != roll:
        return 'This device has already been used to mark attendance for another student.', 400

    df = pd.read_csv(session['filename'])
    today = datetime.now(timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
    if today not in df.columns:
        df[today] = 0

    row_mask = df[df[df.columns[0]].astype(str) == roll]
    if not row_mask.empty:
        df.loc[df[df.columns[0]].astype(str) == roll, today] = 1
        roll_set.add(roll)
        ip_map[user_ip] = roll

        df.to_csv(session['filename'], index=False)
        return 'Attendance marked successfully!'
    else:
        return 'Student not found in list.', 400

@app.route('/download/<session_id>')
def download(session_id):
    session = sessions.get(session_id)
    if not session:
        return 'Invalid session ID.'

    df = pd.read_csv(session['filename'])
    today = datetime.now(timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
    if today not in df.columns:
        df[today] = 0

    if session_id in attendance:
        marked_rolls = attendance[session_id]['rolls']
        for idx in df.index:
            roll = str(df.loc[idx, df.columns[0]])
            if roll not in marked_rolls:
                df.at[idx, today] = 0

    df.to_csv(session['filename'], index=False)

    output = BytesIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return send_file(output, mimetype='text/csv', as_attachment=True, download_name=f'attendance_{session_id}.csv')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
