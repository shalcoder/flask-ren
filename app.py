from flask import Flask, request, jsonify, render_template_string, send_file
import requests
import io
from urllib.parse import quote_plus
import socket
import re
from geopy.distance import geodesic
import logging
import os # For environment variables

app = Flask(__name__)

# Load API key from environment variable for security and flexibility
GOOGLE_MAPS_API_KEY = 'AIzaSyDZuZ1sMCSJSyC_u-rbzHC8BvbIyzAgL3M'
MAP_WIDTH = 800  
MAP_HEIGHT = 400

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app.logger.setLevel(logging.INFO)


current_route = {
    'origin': None,
    'destination': None,
    'steps': [],
    'step_index': 0,
    'polyline': '',
    'total_distance_text': 'N/A',
    'total_duration_text': 'N/A',
   
    'map_type': 'roadmap'     # New: 'roadmap', 'satellite', 'hybrid', 'terrain'
}

def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)

def update_route(origin, destination):
    directions_url = 'https://maps.googleapis.com/maps/api/directions/json'
    params = {
        'origin': origin,
        'destination': destination,
        'mode': 'driving', # Hardcode to driving
        'key': GOOGLE_MAPS_API_KEY,
        'alternatives': 'true' # Request alternative routes
    }
    app.logger.info(f"Requesting directions from {origin} to {destination} with mode: {params['mode']}")
    response = requests.get(directions_url, params=params).json()
    
    if response.get('status') != 'OK':
        current_route['last_directions_error_status'] = response.get('status') # Store error status
        app.logger.error(f"Failed to fetch directions. Status: {response.get('status')}. Mode: {params['mode']}. Response: {response}")
        return False

    steps = []
    # The API returns a list of routes if alternatives=true. We'll use the first one.
    # Future enhancement: Allow user to select from response['routes']
    if not response.get('routes') or not response['routes']:
        app.logger.error(f"No routes array found in Directions API response: {response}")
        return False
    if not response.get('routes') or not response['routes'][0].get('legs'):
        app.logger.error(f"No routes or legs found in Directions API response: {response}")
        return False
        
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

    # Feature: Total Route Distance & Estimated Duration
    if response.get('routes') and response['routes'][0].get('legs'):
        leg = response['routes'][0]['legs'][0]
        current_route['total_distance_text'] = leg.get('distance', {}).get('text', 'N/A')
        current_route['total_duration_text'] = leg.get('duration', {}).get('text', 'N/A')
    else:
        current_route['total_distance_text'] = "N/A"
        current_route['total_duration_text'] = "N/A"

    app.logger.info(f"Route updated: {len(steps)} steps from {origin} to {destination}.")
    return True

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        destination = request.form.get('destination')
        map_type_preference = request.form.get('map_type', 'roadmap') # Get map type from form

        if not destination:
            return "Destination is required.", 400
        
        current_route['destination'] = destination
        current_route['map_type'] = map_type_preference # Store map type

        if not current_route['origin']:
            return "Origin not set yet from GPS. Please wait for GPS fix.", 400

        success = update_route(current_route['origin'], destination)
        if not success:
            # Provide more specific feedback if possible
            error_status = current_route.get('last_directions_error_status', 'UNKNOWN')
            user_message = "Could not calculate route. Please check the destination and try again."
            if error_status == 'NOT_FOUND':
                user_message = "The destination address could not be found by Google Maps. Please check the address."
            elif error_status == 'ZERO_RESULTS':
                user_message = "No routes could be found for the destination." # Removed travel mode from message
            
            return user_message, 500

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
                    /* Feature: Live Tracking Indicator */
                    .live-update-indicator {
                        position: absolute;
                        top: 10px;
                        right: 10px;
                        background: rgba(13, 110, 253, 0.9); /* Bootstrap primary with alpha */
                        color: white;
                        padding: 5px 10px;
                        border-radius: 15px;
                        font-size: 0.8em;
                        display: flex;
                        align-items: center;
                        gap: 5px;
                    }
                    .live-update-indicator .pulse {
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
                        <!-- Feature: Clearer Reset Flow -->
                        <button onclick="resetRoute()" class="btn btn-light ms-auto">Plan New Route</button>
                    </div>
                </nav>
                
                <div class="container">
                    <div class="row">
                        <div class="col-lg-8">
                            <!-- Feature: Live Tracking Indicator -->
                            <div class="map-container">
                                <div class="live-update-indicator"><i class="fas fa-satellite-dish pulse"></i> Live Tracking</div>
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
                            
                            <div class="d-flex justify-content-around mb-3"> <!-- Changed to justify-content-around for better spacing -->
                                <button id="prevStep" class="btn btn-outline-primary" onclick="prevStep()">
                                    <i class="fas fa-arrow-left"></i> Previous
                                </button>
                                <button id="recenterMapBtn" class="btn btn-secondary" onclick="recenterMap()">
                                    <i class="fas fa-location-arrow"></i> Re-center
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
                                    <!-- Feature: Dynamic Origin Display -->
                                    <p><strong>Origin:</strong> <span id="originDisplay">{{ origin }}</span></p> 
                                    <p><strong>Destination:</strong> {{ destination }}</p>
                                    <!-- Feature: Total Route Distance & Duration -->
                                    <p><strong>Total Distance:</strong> <span id="totalDistance">{{ total_distance }}</span></p>
                                    <p><strong>Est. Duration:</strong> <span id="totalDuration">{{ total_duration }}</span></p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
                <script>
                    let currentStep = 0;
                    const totalSteps = {{ steps_count }};
                    let trackingInterval; // For setInterval
                    let watchId; // To store the ID from watchPosition
                    
                    // Start GPS tracking and UI updates
                    function startTracking() {
                        if (!navigator.geolocation) {
                            alert("Geolocation not supported.");
                            return;
                        }
                        
                        // Watch position for sending updates to server
                        watchId = navigator.geolocation.watchPosition(pos => {
                            fetch('/update_location', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    lat: pos.coords.latitude,
                                    lng: pos.coords.longitude,
                                    accuracy: pos.coords.accuracy,
                                    method: "browser_gps_watch" // Indicate method
                                })
                            });
                        }, err => {
                            console.error("GPS tracking error:", err);
                        }, {
                            enableHighAccuracy: true,
                            timeout: 10000,
                            maximumAge: 0
                        });
                        
                        // Periodically fetch current step and update UI
                        trackingInterval = setInterval(() => {
                            fetch('/current_step')
                                .then(response => response.json())
                                .then(data => {
                                    if (!data.error) {
                                        currentStep = data.step_index;
                                        document.getElementById('originDisplay').textContent = data.current_origin || 'Updating...';
                                        document.getElementById('currentStepNum').textContent = data.step_index + 1;
                                        
                                        // Feature: "You Have Arrived" Message
                                        if (data.step_index === totalSteps - 1 && totalSteps > 0) {
                                            document.getElementById('stepInstruction').innerHTML = 
                                                `<strong>You have arrived at your destination!</strong><br>${data.instruction}`;
                                            // Optionally, stop further updates or disable next button more permanently
                                            if (trackingInterval) clearInterval(trackingInterval); 
                                            if (watchId && navigator.geolocation) navigator.geolocation.clearWatch(watchId);
                                        } else {
                                            document.getElementById('stepInstruction').textContent = data.instruction;
                                        }

                                        document.getElementById('mapImage').src = `/map/${data.step_index}?t=${new Date().getTime()}`;
                                        
                                        updateProgress();
                                        
                                        // Enable/disable navigation buttons
                                        document.getElementById('prevStep').disabled = data.step_index === 0;
                                        document.getElementById('nextStep').disabled = data.step_index === totalSteps - 1;
                                        
                                        fetchStepDetails(data.step_index);
                                    }
                                })
                                .catch(err => console.error('Error fetching step:', err));
                        }, 3000); // Update UI every 3 seconds
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
                            }).catch(err => console.error('Error fetching step details:', err));
                    }
                    
                    function updateProgress() {
                        const progress = ((currentStep + 1) / totalSteps) * 100;
                        document.getElementById('progressBar').style.width = `${progress}%`;
                    }
                    
                    function nextStep() {
                        if (currentStep < totalSteps - 1) {
                            currentStep++;
                            // Manually trigger a UI update for responsiveness, 
                            // server will catch up via /update_location and /current_step
                            updateUIForStep(currentStep); 
                        }
                    }
                    
                    function prevStep() {
                        if (currentStep > 0) {
                            currentStep--;
                            updateUIForStep(currentStep);
                        }
                    }
                    
                    // Function to optimistically update UI and then fetch server state
                    function updateUIForStep(stepIdx) {
                        fetch(`/current_step_set/${stepIdx}`) // A new endpoint to set step on server
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
                            })
                            .catch(err => console.error('Error setting/getting step:', err));
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
                    
                    function recenterMap() {
                        const mapImage = document.getElementById('mapImage');
                        // currentStep is updated by the polling mechanism
                        if (typeof currentStep === 'undefined' || currentStep < 0) {
                            console.warn("Current step not defined for recentering.");
                            return;
                        }
                        fetch(`/map_recenter/${currentStep}?t=${new Date().getTime()}`) // Add timestamp
                            .then(response => {
                                if (!response.ok) {
                                    throw new Error(`HTTP error! status: ${response.status}`);
                                }
                                return response.blob();
                            })
                            .then(blob => {
                                const imageUrl = URL.createObjectURL(blob);
                                mapImage.src = imageUrl;
                            })
                            .catch(err => console.error('Error recentering map:', err));
                    }
                    // Feature: Clearer Reset Flow
                    function resetRoute() {
                        if (trackingInterval) {
                            clearInterval(trackingInterval);
                        }
                        if (watchId && navigator.geolocation) {
                            navigator.geolocation.clearWatch(watchId);
                        }
                        fetch('/reset').then(() => {
                            window.location.href = '/';
                        });
                    }
                    
                    window.addEventListener('load', () => {
                        startTracking();
                        updateUIForStep(currentStep); // Initial UI update
                        updateProgress(); // Initialize progress bar
                    });
                </script>
            </body>
            </html>
        ''',
        origin=current_route['origin'],
        destination=current_route['destination'],
        steps_count=len(current_route['steps']),
        steps=current_route['steps'],
        total_distance=current_route['total_distance_text'], # Pass total distance
        total_duration=current_route['total_duration_text']) # Pass total duration

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
                                <div class="mb-3">
                                    <label for="map_type" class="form-label">Map Type</label>
                                    <select class="form-select" id="map_type" name="map_type">
                                        <option value="roadmap" selected>Roadmap</option>
                                        <option value="satellite">Satellite</option>
                                        <option value="hybrid">Hybrid</option>
                                        <option value="terrain">Terrain</option>
                                    </select>
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
            <!-- IMPORTANT: In a production environment, manage your API key more securely. 
                 Consider passing it from the backend or using API key restrictions. -->
            <script async defer src="https://maps.googleapis.com/maps/api/js?key={{ GOOGLE_MAPS_API_KEY }}&libraries=places&callback=initAutocomplete"></script>

            <script>
                const statusEl = document.getElementById('locationStatus');
                const statusText = document.getElementById('locationStatusText');

                function sendLocationToServer(lat, lng, accuracy, method) {
                    fetch('/update_location', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            lat: lat,
                            lng: lng,
                            accuracy: accuracy,
                            method: method
                        })
                    }).then(res => res.json()).then(data => {
                        // Do not update status for 'browser_gps_watch' to avoid overwriting initial fix message
                        if (method === 'browser_gps_watch') return; 
                        
                        statusEl.classList.add('active');
                        statusEl.innerHTML = `<i class="fas fa-check-circle location-icon"></i> 
                            Location detected via ${method.replace(/_/g, ' ')} (Accuracy: ${accuracy ? accuracy.toFixed(0) : 'N/A'}m)`;
                        console.log(`Location sent to server: ${lat},${lng}, Accuracy: ${accuracy}m, Method: ${method}`);
                    }).catch(err => {
                        statusText.textContent = 'Failed to send location to server.';
                        console.error('Error sending location to server:', err);
                    });
                }

                function fetchFallbackLocation() {
                    statusText.textContent = 'GPS accuracy low or unavailable. Trying network location...';
                    fetch('/get_fallback_location')
                        .then(res => res.json())
                        .then(data => {
                            if (data.lat && data.lng) {
                                sendLocationToServer(data.lat, data.lng, data.accuracy, "google_api_fallback");
                            } else {
                                statusText.textContent = 
                                    'Could not detect precise location using fallback. Navigation may be less accurate.';
                                console.warn('Fallback location did not return coordinates:', data.error);
                            }
                        })
                        .catch(err => {
                            statusText.textContent = 'Error fetching fallback location.';
                            console.error('Error fetching fallback location:', err);
                        });
                }

                function startGettingLocation() {
                    if (navigator.geolocation) {
                        navigator.geolocation.getCurrentPosition(
                            pos => {
                                const accuracy = pos.coords.accuracy;
                                // If GPS accuracy is poor (e.g., > 75 meters), try fallback.
                                if (accuracy > 75) {
                                    console.log(`Initial GPS accuracy poor (${accuracy}m), attempting fallback.`);
                                    fetchFallbackLocation();
                                } else {
                                    sendLocationToServer(pos.coords.latitude, pos.coords.longitude, 
                                                accuracy, "browser_gps_initial");
                                }
                            },
                            err => {
                                console.error("Initial GPS failed:", err);
                                fetchFallbackLocation(); // GPS failed, try fallback
                            },
                            { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
                        );
                    } else {
                        console.warn('Geolocation not supported by browser, attempting fallback.');
                        fetchFallbackLocation(); // Geolocation not supported, try fallback
                    }
                }

                // --- Destination Autocomplete Feature ---
                let autocomplete;
                function initAutocomplete() {
                    const destinationInput = document.getElementById('destination');
                    if (destinationInput) {
                        autocomplete = new google.maps.places.Autocomplete(destinationInput, {
                            types: ['geocode'] // You can customize types: 'address', 'establishment', '(regions)', '(cities)'
                        });
                        autocomplete.setFields(['address_components', 'formatted_address', 'geometry', 'name', 'place_id']);
                        
                        // Optional: If you want to do something when a place is selected
                        autocomplete.addListener('place_changed', onPlaceChanged);
                    } else {
                        console.error("Destination input field not found for autocomplete.");
                    }
                }

                function onPlaceChanged() {
                    const place = autocomplete.getPlace();
                    const destinationInput = document.getElementById('destination'); // Get the input element

                    if (!place.geometry) {
                        // User entered the name of a Place that was not suggested,
                        // or pressed the Enter key, or the Place Details request failed.
                        // Or the input was cleared after a selection.
                        console.warn("Autocomplete: No geometry available for input: '" + (place.name || destinationInput.value) + "'. User's raw input will be used.");
                        // The input field will retain what the user typed or cleared.
                    } else {
                        // Place was selected from suggestions. Update the input field's value.
                        destinationInput.value = place.formatted_address;
                        console.log("Autocomplete: Place selected and input updated to - ", place.formatted_address);
                        // You could also store place.place_id in a hidden field if your backend needs it.
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
    accuracy = data.get('accuracy', 'unknown') # Accept accuracy
    method = data.get('method', 'unknown')     # Accept method

    if lat is None or lng is None:
        return jsonify({'error': 'Invalid data, lat and lng required'}), 400

    new_origin = f"{lat},{lng}"
    origin_changed = (current_route['origin'] != new_origin)
    current_route['origin'] = new_origin

    app.logger.info(f"[{method.upper()}] Location: {new_origin} (Accuracy: {accuracy}m)")

   
    if current_route['steps']:
        current_step = current_route['steps'][current_route['step_index']]
        step_coords = (current_step['lat'], current_step['lng'])
        user_coords = (lat, lng)

        distance_to_next_step_point = geodesic(step_coords, user_coords).meters
        THRESHOLD_METERS_TO_ADVANCE = 15 # Threshold for auto-advancing to the next step

        if distance_to_next_step_point < THRESHOLD_METERS_TO_ADVANCE:
            if current_route['step_index'] < len(current_route['steps']) - 1:
                current_route['step_index'] += 1
                app.logger.info(f"Automatically advanced to step {current_route['step_index']}")

    # Re-calculate route if origin changed significantly,
    # but not for every minor update from 'browser_gps_watch' to avoid excessive API calls.
    if current_route['destination'] and origin_changed and method != "browser_gps_watch":
        success = update_route(new_origin, current_route['destination'])
        if success:
            app.logger.info("Route updated dynamically with new origin.")
        else:
            app.logger.error("Failed to update route dynamically.")

    return jsonify({'status': 'Location updated', 'method': method}), 200

@app.route('/get_fallback_location')
def get_fallback_location():
    geo_api_url = f"https://www.googleapis.com/geolocation/v1/geolocate?key={GOOGLE_MAPS_API_KEY}"
    try:
        response = requests.post(geo_api_url, json={"considerIp": True})
        if response.status_code == 200:
            data = response.json()
            app.logger.info(f"Fallback location success: {data.get('location')}, Accuracy: {data.get('accuracy')}")
            return jsonify({
                'lat': data['location']['lat'],
                'lng': data['location']['lng'],
                'accuracy': data['accuracy']
            })
        else:
            app.logger.error(f"Google Geolocation API error. Status: {response.status_code}. Response: {response.text}")
            return jsonify({'error': 'Google API error', 'details': response.text}), response.status_code
    except Exception as e:
        app.logger.error(f"Exception in geolocation fallback: {e}", exc_info=True)
        return jsonify({'error': 'Exception occurred during fallback'}), 500

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
        'maptype': current_route.get('map_type', 'roadmap'), # Use stored map type
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
        app.logger.error(f"Failed to fetch map image for step {step}. Status: {response.status_code}. Response: {response.content}")
        return f"Failed to fetch map image", 500

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
        'maptype': current_route.get('map_type', 'roadmap'), # Use stored map type
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
        app.logger.error(f"Failed to fetch panned map image for step {step}. Status: {response.status_code}. Response: {response.content}")
        return f"Failed to fetch map image", 500

    return send_file(io.BytesIO(response.content), mimetype='image/jpeg')

@app.route('/map_recenter/<int:step_index>')
def map_recenter(step_index):
    if not current_route['origin']:
        app.logger.warn("Re-center map called but no origin is set.")
        return "User origin not available to re-center.", 404

    # It's good to have route context, but re-centering should primarily focus on user's location.
    # If steps are not available, we might still want to show the user on the map.
    # For now, we'll assume steps exist to draw the polyline and step markers.
    if not current_route['steps'] or step_index < 0 or step_index >= len(current_route['steps']):
        app.logger.warn(f"Re-center map called for step {step_index}, but route/step context is invalid or unavailable.")
        # Fallback: could generate a map with just user location if no route, but for now require route.
        return "Route context not available for recentering.", 404

    user_current_location_str = current_route['origin'] # This is "lat,lng"
    polyline = current_route['polyline']
    current_step_details = current_route['steps'][step_index]

    base_url = "https://maps.googleapis.com/maps/api/staticmap"
    params = {
        'size': f'{MAP_WIDTH}x{MAP_HEIGHT}',
        'zoom': '16', # A good zoom level for "you are here"
        'center': user_current_location_str, # Center on the user's current location
        'path': f'color:0xff0000ff|weight:5|enc:{polyline}',
        'format': 'jpg-baseline',
        'key': GOOGLE_MAPS_API_KEY,
        'maptype': current_route.get('map_type', 'roadmap'),
    }

    markers = [
        f'color:blue|label:{step_index+1}|{current_step_details["lat"]},{current_step_details["lng"]}', # Current navigation step
        f'color:red|label:E|{current_route["destination"]}', # Destination
        # Add a distinct marker for the user's actual current location (map center)
        f'icon:https://maps.google.com/mapfiles/ms/icons/blue-dot.png|{user_current_location_str}'
    ]
    params['markers'] = markers

    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status() 
        return send_file(io.BytesIO(response.content), mimetype='image/jpeg')
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Failed to fetch recentered map image. Error: {e}. Params: {params}")
        return f"Failed to fetch map image: {e}", 500

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
        'total_steps': len(current_route['steps']),
        'current_origin': current_route.get('origin', 'N/A') # Added for dynamic origin display
    })

@app.route('/current_step_set/<int:step_index>', methods=['GET']) # New endpoint for manual step changes
def set_current_step(step_index):
    if 0 <= step_index < len(current_route['steps']):
        current_route['step_index'] = step_index
        app.logger.info(f"User manually set step to {step_index}")
        # Return the new current step data, similar to /current_step
        step_data = current_route['steps'][step_index]
        return jsonify({
            'step_index': step_index,
            'instruction': step_data['instruction'],
            'current_origin': current_route.get('origin', 'N/A')
            # Add other fields like lat, lng, total_steps if your JS in updateUIForStep needs them
        })
    return jsonify({'error': 'Invalid step index'}), 400

@app.route('/reset')
def reset():
    current_route['origin'] = None
    current_route['destination'] = None
    current_route['steps'] = []
    current_route['step_index'] = 0
    current_route['polyline'] = ''
    current_route['map_type'] = 'roadmap'   # Reset to default
    return "Route reset."

if __name__ == '__main__':
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = '127.0.0.1'
    app.logger.info(f"Flask server running at http://{local_ip}:5000")
    app.run(host='0.0.0.0', port=5000)
