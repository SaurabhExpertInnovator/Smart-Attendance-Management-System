# app.py – QR + Face-recognition attendance system (complete fixed version)
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
import pandas as pd
import qrcode
import uuid
import os
import json
import base64
import face_recognition
import numpy as np
import cv2
from datetime import datetime
from pytz import timezone
from io import BytesIO
import math

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PHOTO_FOLDER'] = 'static/photos'
app.config['DATABASE_FILE'] = 'students.json'

# Create directories if they don't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PHOTO_FOLDER'], exist_ok=True)
os.makedirs('static/qr', exist_ok=True)

# In-memory storage
sessions = {}          # session_id → {filename, latitude, longitude, radius}
attendance = {}        # session_id → {ip: [rolls]}
BASE_URL = 'https://0985-103-151-209-183.ngrok-free.app/'  # Change this to your actual URL

def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance between two GPS coordinates in meters"""
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    
    a = (math.sin(d_phi/2)**2 + 
         math.cos(phi1) * math.cos(phi2) * 
         math.sin(d_lambda/2)**2)
    
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

def load_students():
    """Load student data from JSON file"""
    if os.path.exists(app.config['DATABASE_FILE']):
        with open(app.config['DATABASE_FILE'], 'r') as f:
            return json.load(f)
    return {}

def save_students(data):
    """Save student data to JSON file"""
    with open(app.config['DATABASE_FILE'], 'w') as f:
        json.dump(data, f, indent=4)

def enhance_image(image):
    """Improve image quality for better face recognition"""
    # Convert to YUV color space and equalize histogram
    yuv = cv2.cvtColor(image, cv2.COLOR_BGR2YUV)
    yuv[:,:,0] = cv2.equalizeHist(yuv[:,:,0])
    enhanced = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR)
    
    # Apply sharpening filter
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    return cv2.filter2D(enhanced, -1, kernel)

def is_blurry(image, threshold=100):
    """Check if image is blurry using Laplacian variance"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    fm = cv2.Laplacian(gray, cv2.CV_64F).var()
    return fm < threshold

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            name = request.form['name'].strip()
            roll = request.form['roll'].strip()
            photo_data = request.form['photo']

            # Handle base64 image data
            if ',' in photo_data:
                photo_data = photo_data.split(',')[1]
            
            # Fix base64 padding if needed
            missing_padding = len(photo_data) % 4
            if missing_padding:
                photo_data += '=' * (4 - missing_padding)

            # Decode image
            img_bytes = base64.b64decode(photo_data)
            nparr = np.frombuffer(img_bytes, np.uint8)
            
            # Read image with OpenCV (try different flags)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if image is None:
                # Try alternative decoding if first attempt fails
                image = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
                if image is None:
                    return jsonify({'status':'error', 'message':'Failed to decode image'})

            # Convert to RGB format if needed
            if len(image.shape) == 2:  # Grayscale
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            elif image.shape[2] == 4:   # RGBA
                image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
            elif image.shape[2] == 3:   # RGB
                pass  # Already in correct format
            else:
                return jsonify({'status':'error', 'message':'Unsupported image format'})

            # Enhance image quality
            enhanced = enhance_image(image)
            
            # Check for blur
            if is_blurry(enhanced):
                return jsonify({'status':'error', 'message':'Image too blurry - hold still or improve lighting'})

            # Convert to RGB for face_recognition
            rgb_image = cv2.cvtColor(enhanced, cv2.COLOR_BGR2RGB)
            
            # Save the image
            filepath = os.path.join(app.config['PHOTO_FOLDER'], f'{roll}.jpg')
            cv2.imwrite(filepath, cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR))

            # Face encoding
            try:
                encodings = face_recognition.face_encodings(rgb_image)
                if not encodings:
                    return jsonify({'status':'error', 'message':'No face detected - ensure full face is visible'})
                
                enc = encodings[0]
            except Exception as e:
                return jsonify({'status':'error', 'message':f'Face encoding failed: {str(e)}'})

            # Save student data
            students = load_students()
            students[roll] = {
                'name': name,
                'photo_path': filepath,
                'encoding': enc.tolist(),
                'attendance': []
            }
            save_students(students)

            return jsonify({'status':'success', 'message':'Student registered successfully!'})

        except Exception as e:
            return jsonify({'status':'error', 'message':f'Registration failed: {str(e)}'})

    return render_template('register.html')

@app.route('/upload', methods=['POST'])
def upload():
    try:
        file = request.files['file']
        radius = request.form.get('radius')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')

        # Validate inputs
        if not all([latitude, longitude, radius]):
            return 'Location and radius are required', 400
        
        if not file:
            return 'CSV file not uploaded', 400

        try:
            latitude = float(latitude)
            longitude = float(longitude)
            radius = float(radius)
        except ValueError:
            return 'Invalid location or radius values', 400

        # Process CSV file
        try:
            df = pd.read_csv(file)
            today = datetime.now(timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
            
            if today not in df.columns:
                df[today] = 0
            
            # Create session
            session_id = str(uuid.uuid4())
            csv_path = os.path.join(app.config['UPLOAD_FOLDER'], f'{session_id}.csv')
            df.to_csv(csv_path, index=False)

            sessions[session_id] = {
                'filename': csv_path,
                'latitude': latitude,
                'longitude': longitude,
                'radius': radius
            }

            # Generate QR code
            url = BASE_URL + 'scan/' + session_id
            qr_img = qrcode.make(url)
            qr_path = os.path.join('static/qr', f'{session_id}.png')
            qr_img.save(qr_path)

            return render_template('qr_display.html', 
                                 qr_path=f'qr/{session_id}.png', 
                                 session_id=session_id)

        except Exception as e:
            return f'Error processing CSV file: {str(e)}', 500

    except Exception as e:
        return f'Error: {str(e)}', 500

@app.route('/scan/<session_id>')
def scan(session_id):
    if session_id not in sessions:
        return 'Invalid session ID', 404
    
    sess = sessions[session_id]
    try:
        df = pd.read_csv(sess['filename'])
        students = df.to_dict(orient='records')
        roll_col, name_col = df.columns[:2]
        
        return render_template('student_list.html', 
                            students=students, 
                            session_id=session_id, 
                            roll_col=roll_col, 
                            name_col=name_col)
    except Exception as e:
        return f'Error loading session: {str(e)}', 500

@app.route('/mark', methods=['POST'])
def mark_attendance():
    try:
        session_id = request.form['session_id']
        roll = request.form['roll_number']
        lat = float(request.form['latitude'])
        lon = float(request.form['longitude'])
        photo_data = request.form['photo']

        # Validate session
        if session_id not in sessions:
            return jsonify({'status': 'error', 'message': 'Invalid session ID'}), 400
        
        sess = sessions[session_id]
        user_ip = request.remote_addr

        # Check location
        distance = haversine(lat, lon, sess['latitude'], sess['longitude'])
        if distance > sess['radius']:
            return jsonify({
                'status': 'error',
                'message': f'You are {int(distance)}m away (allowed: {int(sess["radius"])}m)'
            }), 400

        # Load student data
        students = load_students()
        if roll not in students:
            return jsonify({'status': 'error', 'message': 'Student not registered'}), 404

        stored_enc = np.array(students[roll]['encoding'])
        if stored_enc.shape != (128,):
            return jsonify({'status': 'error', 'message': 'Invalid face encoding'}), 400

        # Process image
        if ',' in photo_data:
            photo_data = photo_data.split(',')[1]
        
        missing_padding = len(photo_data) % 4
        if missing_padding:
            photo_data += '=' * (4 - missing_padding)

        try:
            img_bytes = base64.b64decode(photo_data)
            nparr = np.frombuffer(img_bytes, np.uint8)
            new_img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
            
            if new_img is None:
                return jsonify({'status': 'error', 'message': 'Invalid image data'}), 400
            
            # Convert image to RGB if needed
            if len(new_img.shape) == 2:  # Grayscale
                new_img = cv2.cvtColor(new_img, cv2.COLOR_GRAY2RGB)
            elif new_img.shape[2] == 4:   # RGBA
                new_img = cv2.cvtColor(new_img, cv2.COLOR_RGBA2RGB)
            elif new_img.shape[2] == 3:   # RGB
                pass
            else:
                return jsonify({'status': 'error', 'message': 'Unsupported image format'}), 400

            enhanced = enhance_image(new_img)
            if is_blurry(enhanced):
                return jsonify({'status': 'error', 'message': 'Image too blurry'}), 400

            # Save temporary image for debugging
            temp_path = os.path.join(app.config['PHOTO_FOLDER'], f'temp_{roll}.jpg')
            cv2.imwrite(temp_path, cv2.cvtColor(enhanced, cv2.COLOR_RGB2BGR))

            # Face recognition
            rgb_image = cv2.cvtColor(enhanced, cv2.COLOR_BGR2RGB)
            face_encodings = face_recognition.face_encodings(rgb_image)
            
            if not face_encodings:
                return jsonify({'status': 'error', 'message': 'No face detected'}), 400
            
            new_enc = face_encodings[0]
            distance = face_recognition.face_distance([stored_enc], new_enc)[0]

            if distance >= 0.55:
                return jsonify({
                    'status': 'error',
                    'message': f'Face mismatch (distance: {distance:.2f})'
                }), 400

            # Prevent duplicate attendance
            if roll in attendance.setdefault(session_id, {}).setdefault(user_ip, []):
                return jsonify({
                    'status': 'error',
                    'message': 'Attendance already marked from this device'
                }), 400

            # Update attendance record
            df = pd.read_csv(sess['filename'])
            today = datetime.now(timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
            
            if today not in df.columns:
                df[today] = 0
            
            df.loc[df[df.columns[0]] == roll, today] = 1
            df['Attendance %'] = df.iloc[:, 2:].mean(axis=1) * 100
            df.to_csv(sess['filename'], index=False)

            attendance[session_id][user_ip].append(roll)

            return jsonify({
                'status': 'success',
                'message': f'Attendance marked for {students[roll]["name"]}'
            })

        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': f'Face recognition error: {str(e)}'
            }), 500

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Attendance marking failed: {str(e)}'
        }), 500

@app.route('/download/<session_id>')
def download(session_id):
    if session_id not in sessions:
        return 'Invalid session ID', 404
    
    try:
        sess = sessions[session_id]
        df = pd.read_csv(sess['filename'])
        buff = BytesIO()
        df.to_csv(buff, index=False)
        buff.seek(0)
        
        return send_file(
            buff,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'attendance_{session_id}.csv'
        )
    except Exception as e:
        return f'Error generating download: {str(e)}', 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
