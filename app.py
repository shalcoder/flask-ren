from flask import Flask, request, jsonify, render_template_string, send_file
import requests
import io
from urllib.parse import quote_plus
import socket
import re
from geopy.distance import geodesic

app = Flask(__name__)

GOOGLE_MAPS_API_KEY = 'AIzaSyDZuZ1sMCSJSyC_u-rbzHC8BvbIyzAgL3M'
MAP_WIDTH = 800  
MAP_HEIGHT = 400

current_route = {
    'origin': None,
    'destination': None,
    'steps': [],
    'step_index': 0,
    'polyline': ''
}

def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)

def update_route(origin, destination):
    directions_url = 'https://maps.googleapis.com/maps/api/directions/json'
    params = {
        'origin': origin,
        'destination': destination,
        'mode': 'driving',
        'key': GOOGLE_MAPS_API_KEY
    }
    response = requests.get(directions_url, params=params).json()
    if response['status'] != 'OK':
        print(f"Failed to fetch directions: {response['status']}")
        return False

    steps = []
    for leg in response['routes'][0]['legs']:
        for step in leg['steps']:
            loc = step['start_location']
            instruction = clean_html(step['html_instructions'])
            steps.append({
                'lat': loc['lat'],
                'lng': loc['lng'],
                'instruction': instruction,
                'distance': step['distance']['text'],
                'duration': step['duration']['text']
            })

    current_route['steps'] = steps
    current_route['polyline'] = response['routes'][0]['overview_polyline']['points']
    current_route['step_index'] = 0
    print(f"Route updated: {len(steps)} steps.")
    return True

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        destination = request.form.get('destination')
        if not destination:
            return "Destination is required.", 400
        current_route['destination'] = destination

        if not current_route['origin']:
            return "Origin not set yet from GPS. Please wait for GPS fix.", 400

        success = update_route(current_route['origin'], destination)
        if not success:
            return "Could not calculate route. Check destination and try again.", 500

        return render_template_string('''
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Navigation Started</title>
                <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
                <style>
                    body {
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        background-color: #f8f9fa;
                        color: #333;
                    }
                    .navbar-brand {
                        font-weight: bold;
                    }
                    .map-container {
                        position: relative;
                        margin-bottom: 20px;
                        border-radius: 8px;
                        overflow: hidden;
                        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                    }
                    .map-img {
                        width: 100%;
                        height: auto;
                        cursor: grab;
                    }
                    .map-img:active {
                        cursor: grabbing;
                    }
                    .instruction-card {
                        background: white;
                        border-radius: 8px;
                        padding: 15px;
                        margin-bottom: 15px;
                        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
                    }
                    .progress-container {
                        height: 8px;
                        background: #e9ecef;
                        border-radius: 4px;
                        margin: 15px 0;
                    }
                    .progress-bar {
                        background: #0d6efd;
                        border-radius: 4px;
                        transition: width 0.3s ease;
                    }
                    .step-info {
                        display: flex;
                        justify-content: space-between;
                        margin-bottom: 10px;
                    }
                    .step-distance, .step-duration {
                        font-size: 0.9em;
                        color: #6c757d;
                    }
                    .live-update-indicator {
                        position: absolute;
                        top: 10px;
                        right: 10px;
                        background: rgba(13, 110, 253, 0.9);
                        color: white;
                        padding: 5px 10px;
                        border-radius: 15px;
                        font-size: 0.8em;
                        display: flex;
                        align-items: center;
                        gap: 5px;
                    }
                    .pulse {
                        animation: pulse 2s infinite;
                    }
                    @keyframes pulse {
                        0% { opacity: 1; }
                        50% { opacity: 0.5; }
                        100% { opacity: 1; }
                    }
                    @media (max-width: 768px) {
                        .container {
                            padding: 0 15px;
                        }
                        .map-container {
                            margin-left: -15px;
                            margin-right: -15px;
                            border-radius: 0;
                        }
                    }
                </style>
            </head>
            <body>
                <nav class="navbar navbar-expand-lg navbar-dark bg-primary mb-4">
                    <div class="container">
                        <a class="navbar-brand" href="/">Route Navigator</a>
                        <a href="/" class="btn btn-light ms-auto">Plan New Route</a>
                    </div>
                </nav>
                
                <div class="container">
                    <div class="row">
                        <div class="col-lg-8">
                            <div class="map-container">
                                <div class="live-update-indicator">
                                    <i class="fas fa-satellite-dish pulse"></i>
                                    Live Tracking
                                </div>
                                <img id="mapImage" src="/map/0" alt="Step 1 Map" class="map-img" 
                                     onclick="handleMapClick(event)">
                            </div>
                        </div>
                        <div class="col-lg-4">
                            <div class="d-flex justify-content-between align-items-center mb-3">
                                <h4>Navigation</h4>
                                <span class="badge bg-primary">Step <span id="currentStepNum">1</span>/{{ steps_count }}</span>
                            </div>
                            
                            <div class="instruction-card">
                                <div class="step-info">
                                    <span class="step-distance"><i class="fas fa-road"></i> <span id="stepDistance">{{ steps[0].distance }}</span></span>
                                    <span class="step-duration"><i class="fas fa-clock"></i> <span id="stepDuration">{{ steps[0].duration }}</span></span>
                                </div>
                                <p id="stepInstruction">{{ steps[0].instruction }}</p>
                            </div>
                            
                            <div class="progress-container">
                                <div id="progressBar" class="progress-bar" style="width: 0%"></div>
                            </div>
                            
                            <div class="d-flex justify-content-between mb-3">
                                <button id="prevStep" class="btn btn-outline-primary" onclick="prevStep()">
                                    <i class="fas fa-arrow-left"></i> Previous
                                </button>
                                <button id="nextStep" class="btn btn-primary" onclick="nextStep()">
                                    Next <i class="fas fa-arrow-right"></i>
                                </button>
                            </div>
                            
                            <div class="card mb-3">
                                <div class="card-header bg-light">
                                    <h5 class="mb-0">Route Summary</h5>
                                </div>
                                <div class="card-body">
                                    <p><strong>Origin:</strong> <span id="originDisplay">{{ origin }}</span></p>
                                    <p><strong>Destination:</strong> {{ destination }}</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
                <script>
                    let currentStep = 0;
                    const totalSteps = {{ steps_count }};
                    let trackingInterval;
                    
                    // Initialize progress bar
                    updateProgress();
                    
                    // Start GPS tracking like flaskapp.py
                    function startTracking() {
                        if (!navigator.geolocation) {
                            alert("Geolocation not supported.");
                            return;
                        }
                        
                        // Watch position for real-time updates
                        navigator.geolocation.watchPosition(pos => {
                            fetch('/update_location', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    lat: pos.coords.latitude,
                                    lng: pos.coords.longitude
                                })
                            });
                        }, err => {
                            console.error("GPS tracking error:", err);
                        }, {
                            enableHighAccuracy: true,
                            timeout: 10000,
                            maximumAge: 0
                        });
                        
                        // Update display every 5 seconds like flaskapp.py
                        trackingInterval = setInterval(() => {
                            fetch('/current_step')
                                .then(response => response.json())
                                .then(data => {
                                    if (!data.error) {
                                        currentStep = data.step_index;
                                        document.getElementById('currentStepNum').textContent = data.step_index + 1;
                                        document.getElementById('stepInstruction').textContent = data.instruction;
                                        
                                        // Update map image with timestamp to force refresh like flaskapp.py
                                        document.getElementById('mapImage').src = `/map/${data.step_index}?t=${new Date().getTime()}`;
                                        
                                        // Update origin display with current coordinates
                                        document.getElementById('originDisplay').textContent = `(${data.lat.toFixed(5)}, ${data.lng.toFixed(5)})`;
                                        
                                        updateProgress();
                                        
                                        // Enable/disable navigation buttons
                                        document.getElementById('prevStep').disabled = data.step_index === 0;
                                        document.getElementById('nextStep').disabled = data.step_index === totalSteps - 1;
                                        
                                        // Fetch step details for distance/duration
                                        fetchStepDetails(data.step_index);
                                    }
                                })
                                .catch(err => console.error('Error fetching step:', err));
                        }, 5000); // 5 second updates like flaskapp.py
                    }
                    
                    function fetchStepDetails(stepIndex) {
                        fetch('/step_details/' + stepIndex)
                            .then(response => response.json())
                            .then(data => {
                                if (data.distance) {
                                    document.getElementById('stepDistance').textContent = data.distance;
                                }
                                if (data.duration) {
                                    document.getElementById('stepDuration').textContent = data.duration;
                                }
                            })
                            .catch(err => console.error('Error fetching step details:', err));
                    }
                    
                    function updateProgress() {
                        const progress = ((currentStep + 1) / totalSteps) * 100;
                        document.getElementById('progressBar').style.width = `${progress}%`;
                    }
                    
                    function nextStep() {
                        if (currentStep < totalSteps - 1) {
                            currentStep++;
                            updateStepDisplay();
                        }
                    }
                    
                    function prevStep() {
                        if (currentStep > 0) {
                            currentStep--;
                            updateStepDisplay();
                        }
                    }
                    
                    function updateStepDisplay() {
                        fetch('/current_step')
                            .then(response => response.json())
                            .then(data => {
                                if (!data.error) {
                                    document.getElementById('currentStepNum').textContent = data.step_index + 1;
                                    document.getElementById('stepInstruction').textContent = data.instruction;
                                    document.getElementById('mapImage').src = `/map/${data.step_index}?t=${new Date().getTime()}`;
                                    updateProgress();
                                    
                                    // Enable/disable navigation buttons
                                    document.getElementById('prevStep').disabled = data.step_index === 0;
                                    document.getElementById('nextStep').disabled = data.step_index === totalSteps - 1;
                                    
                                    fetchStepDetails(data.step_index);
                                }
                            });
                    }
                    
                    function handleMapClick(event) {
                        const img = document.getElementById('mapImage');
                        const rect = img.getBoundingClientRect();
                        const x = event.clientX - rect.left;
                        const y = event.clientY - rect.top;
                        
                        // Calculate percentage position
                        const percentX = (x / rect.width) * 100;
                        const percentY = (y / rect.height) * 100;
                        
                        // Pan the map by requesting a new centered image
                        fetch(`/pan_map/${currentStep}?x=${percentX}&y=${percentY}`)
                            .then(response => response.blob())
                            .then(blob => {
                                const url = URL.createObjectURL(blob);
                                img.src = url;
                            })
                            .catch(err => console.error('Error panning map:', err));
                    }
                    
                    function resetRoute() {
                        if (trackingInterval) {
                            clearInterval(trackingInterval);
                        }
                        fetch('/reset').then(() => {
                            window.location.href = '/';
                        });
                    }
                    
                    // Start tracking when page loads
                    window.addEventListener('load', startTracking);
                    
                    // Add reset button functionality
                    document.querySelector('a[href="/"]').addEventListener('click', function(e) {
                        e.preventDefault();
                        resetRoute();
                    });
                </script>
            </body>
            </html>
        ''',
        origin=current_route['origin'],
        destination=current_route['destination'],
        steps_count=len(current_route['steps']),
        steps=current_route['steps'])

    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Route Navigator</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
            <style>
                body {
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background-color: #f8f9fa;
                }
                .hero-section {
                    background: linear-gradient(135deg, #0d6efd 0%, #0b5ed7 100%);
                    color: white;
                    padding: 3rem 0;
                    margin-bottom: 2rem;
                    border-radius: 0 0 10px 10px;
                }
                .form-container {
                    background: white;
                    padding: 2rem;
                    border-radius: 8px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }
                .location-status {
                    padding: 1rem;
                    background: #e9ecef;
                    border-radius: 4px;
                    margin-bottom: 1rem;
                }
                .location-status.active {
                    background: #d1e7dd;
                    color: #0f5132;
                }
                .location-icon {
                    font-size: 1.5rem;
                    margin-right: 0.5rem;
                }
            </style>
        </head>
        <body>
            <div class="hero-section">
                <div class="container text-center">
                    <h1><i class="fas fa-route"></i> Route Navigator</h1>
                    <p class="lead">Get turn-by-turn navigation with real-time updates</p>
                </div>
            </div>
            
            <div class="container">
                <div class="row justify-content-center">
                    <div class="col-md-8 col-lg-6">
                        <div class="form-container">
                            <h2 class="mb-4">Start Navigation</h2>
                            <div id="locationStatus" class="location-status">
                                <i class="fas fa-spinner fa-spin location-icon"></i>
                                <span id="locationStatusText">Detecting your location...</span>
                            </div>
                            <form method="POST">
                                <div class="mb-3">
                                    <label for="destination" class="form-label">Destination Address</label>
                                    <input type="text" class="form-control" id="destination" name="destination" required 
                                           placeholder="Enter destination address">
                                </div>
                                <button type="submit" class="btn btn-primary btn-lg w-100">
                                    <i class="fas fa-play"></i> Start Navigation
                                </button>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
            
            <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
            <script>
                function startGettingLocation() {
                    const statusEl = document.getElementById('locationStatus');
                    const statusText = document.getElementById('locationStatusText');
                    
                    if (navigator.geolocation) {
                        navigator.geolocation.getCurrentPosition(
                            pos => {
                                fetch('/update_location', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({
                                        lat: pos.coords.latitude,
                                        lng: pos.coords.longitude
                                    })
                                }).then(res => res.json()).then(data => {
                                    statusEl.classList.add('active');
                                    statusEl.innerHTML = `<i class="fas fa-check-circle location-icon"></i> 
                                        Location detected (Accuracy: ${pos.coords.accuracy.toFixed(0)}m)`;
                                }).catch(err => {
                                    statusText.textContent = 'Location detection failed. Please try again.';
                                });
                            },
                            err => {
                                console.error("GPS failed:", err);
                                statusText.textContent = 'Could not detect location. Please ensure GPS is enabled.';
                            },
                            {
                                enableHighAccuracy: true,
                                timeout: 10000,
                                maximumAge: 0
                            }
                        );
                    } else {
                        statusText.textContent = 'Geolocation not supported by your browser.';
                    }
                }
                
                // Start location detection when page loads
                window.addEventListener('load', startGettingLocation);
            </script>
        </body>
        </html>
    ''')

@app.route('/update_location', methods=['POST'])
def update_location():
    data = request.get_json()
    lat = data.get('lat')
    lng = data.get('lng')
    if lat is None or lng is None:
        return jsonify({'error': 'Invalid data, lat and lng required'}), 400

    new_origin = f"{lat},{lng}"
    origin_changed = (current_route['origin'] != new_origin)
    current_route['origin'] = new_origin

    print(f"Location updated: {new_origin}")

   
    if current_route['steps']:
        current_step = current_route['steps'][current_route['step_index']]
        step_coords = (current_step['lat'], current_step['lng'])
        user_coords = (lat, lng)

        distance = geodesic(step_coords, user_coords).meters
        if distance < 15:  # 15 meter threshold like flaskapp.py
            if current_route['step_index'] < len(current_route['steps']) - 1:
                current_route['step_index'] += 1
                print(f"Automatically advanced to step {current_route['step_index']}")

    
    if current_route['destination'] and origin_changed:
        success = update_route(new_origin, current_route['destination'])
        if success:
            print("Route updated dynamically with new origin.")
        else:
            print("Failed to update route dynamically.")

    return jsonify({'status': 'Location updated'}), 200

@app.route('/map/<int:step>')
def step_map(step):
    if step < 0 or step >= len(current_route['steps']):
        return "No such step", 404

    location = current_route['steps'][step]
    lat = location['lat']
    lng = location['lng']
    polyline = current_route['polyline']

    base_url = "https://maps.googleapis.com/maps/api/staticmap"
    params = {
        'size': f'{MAP_WIDTH}x{MAP_HEIGHT}',
        'zoom': '16',
        'center': f'{lat},{lng}',
        'path': f'color:0xff0000ff|weight:5|enc:{polyline}',
        'format': 'jpg-baseline',
        'key': GOOGLE_MAPS_API_KEY,
        'maptype': 'roadmap',
    }
    
    # Add markers for the current step, destination, and user's current location
    markers = [
        f'color:blue|label:{step+1}|{lat},{lng}',  # Current step
        f'color:red|label:E|{current_route["destination"]}'  # Destination
    ]
    if current_route['origin']:
        markers.append(f'color:green|label:U|{current_route["origin"]}') # User's current location
    params['markers'] = markers

    response = requests.get(base_url, params=params)
    if response.status_code != 200:
        return f"Failed to fetch map image: {response.content}", 500

    return send_file(io.BytesIO(response.content), mimetype='image/jpeg')

@app.route('/pan_map/<int:step>')
def pan_map(step):
    if step < 0 or step >= len(current_route['steps']):
        return "No such step", 404

    # Get click position percentages
    x_percent = float(request.args.get('x', 50))
    y_percent = float(request.args.get('y', 50))
    
    # Calculate the offset from center (50%, 50%)
    x_offset = (x_percent - 50) / 50  # -1 to 1
    y_offset = (y_percent - 50) / 50  # -1 to 1
    
    # Adjust the center based on click position
    location = current_route['steps'][step]
    lat = location['lat']
    lng = location['lng']
    
    # Simple approximation for panning
    lat_adjust = 0.02 * y_offset
    lng_adjust = 0.02 * x_offset
    
    new_lat = lat - lat_adjust
    new_lng = lng + lng_adjust

    base_url = "https://maps.googleapis.com/maps/api/staticmap"
    params = {
        'size': f'{MAP_WIDTH}x{MAP_HEIGHT}',
        'zoom': '16',
        'center': f'{new_lat},{new_lng}',
        'path': f'color:0xff0000ff|weight:5|enc:{current_route["polyline"]}',
        'format': 'jpg-baseline',
        'key': GOOGLE_MAPS_API_KEY,
        'maptype': 'roadmap',
    }

    # Add markers for the current step, destination, and user's current location
    markers = [
        f'color:blue|label:{step+1}|{lat},{lng}',  # Current step
        f'color:red|label:E|{current_route["destination"]}'  # Destination
    ]
    if current_route['origin']:
        markers.append(f'color:green|label:U|{current_route["origin"]}') # User's current location
    params['markers'] = markers

    response = requests.get(base_url, params=params)
    if response.status_code != 200:
        return f"Failed to fetch map image: {response.content}", 500

    return send_file(io.BytesIO(response.content), mimetype='image/jpeg')

@app.route('/step_details/<int:step>')
def step_details(step):
    if step < 0 or step >= len(current_route['steps']):
        return jsonify({'error': 'Invalid step'}), 404
    
    step_data = current_route['steps'][step]
    return jsonify({
        'distance': step_data.get('distance', ''),
        'duration': step_data.get('duration', '')
    })

@app.route('/current_step')
def current_step():
    i = current_route['step_index']
    if i < 0 or i >= len(current_route['steps']):
        return jsonify({'error': 'No current step'}), 404

    step_data = current_route['steps'][i]
    return jsonify({
        'step_index': i,
        'lat': step_data['lat'],
        'lng': step_data['lng'],
        'instruction': step_data['instruction'],
        'total_steps': len(current_route['steps'])
    })

@app.route('/reset')
def reset():
    current_route['origin'] = None
    current_route['destination'] = None
    current_route['steps'] = []
    current_route['step_index'] = 0
    current_route['polyline'] = ''
    return "Route reset."

if __name__ == '__main__':
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = '127.0.0.1'
    print(f"Flask server running at http://{local_ip}:5000")
    app.run(host='0.0.0.0', port=5000)
