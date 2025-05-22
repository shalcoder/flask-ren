from flask import Flask, request, jsonify, render_template_string, send_file
import requests
import io
from urllib.parse import quote_plus
import re
from geopy.distance import geodesic
import os

app = Flask(__name__)

# --- Configuration ---
# IMPORTANT: Set this environment variable on Render!
Maps_API_KEY = 'AIzaSyDZuZ1sMCSJSyC_u-rbzHC8BvbIyzAgL3M'
MAP_WIDTH = 480 # Increased for better visibility
MAP_HEIGHT = 360 # Increased for better visibility
MIN_REROUTE_METERS = 50 # Minimum distance moved to trigger a route recalculation
THRESHOLD_METERS_TO_ADVANCE_STEP = 15 # Advance step if user is within this distance of the step's start

# Global state for the current navigation route
current_route = {
    'origin': None,
    'last_routed_origin': None, # Stores the origin (lat, lng tuple) used for the last successful route calculation
    'destination': None,
    'steps': [],
    'step_index': 0, # Current step being displayed/followed
    'polyline': ''
}

# --- Helper Functions ---
def clean_html(raw_html):
    """Removes HTML tags from a string."""
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)

def update_route(origin_str, destination_str):
    """
    Fetches directions from Google Maps API and updates the current_route.
    Returns True on success, False otherwise.
    NOTE: This function resets current_route['step_index'] to 0 upon successful route update.
    """
    directions_url = 'https://maps.googleapis.com/maps/api/directions/json'
    params = {
        'origin': origin_str,
        'destination': destination_str,
        'mode': 'driving',
        'key': Maps_API_KEY
    }
    try:
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
                    'instruction': instruction
                })

        current_route['steps'] = steps
        current_route['polyline'] = response['routes'][0]['overview_polyline']['points']
        current_route['step_index'] = 0 # THIS IS THE CRITICAL RESET POINT FOR NEW ROUTES
        
        # Update last_routed_origin only if the new origin string is valid
        if origin_str and ',' in origin_str:
            try:
                lat_orig, lng_orig = map(float, origin_str.split(','))
                current_route['last_routed_origin'] = (lat_orig, lng_orig)
            except ValueError:
                print(f"Warning: Could not parse origin '{origin_str}' for last_routed_origin.")
        else:
            current_route['last_routed_origin'] = None # Reset if origin is invalid/None
        
        print(f"Route updated: {len(steps)} steps from {origin_str} to {destination_str}.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Network error fetching directions: {e}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred while updating route: {e}")
        return False

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    """Handles the main page, route planning, and initial setup."""
    if request.method == 'POST':
        destination = request.form.get('destination')
        if not destination:
            return "Destination is required.", 400
        current_route['destination'] = destination

        # Ensure origin is set before trying to calculate route
        if not current_route['origin']:
            return "Origin not set yet from GPS. Please wait for GPS fix and resubmit.", 400

        success = update_route(current_route['origin'], destination)
        if not success:
            return "Could not calculate route. Check destination and try again. Ensure API key is valid.", 500

        # After a successful route calculation, render the navigation view
        return render_template_string('''
            <!doctype html>
            <html>
            <head>
                <title>Navigation</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body { font-family: sans-serif; margin: 10px; }
                    #map-container img { max-width: 100%; height: auto; display: block; margin: 0 auto; }
                    .nav-info { margin-top: 15px; border-top: 1px solid #eee; padding-top: 15px; }
                    .instruction { font-size: 1.2em; font-weight: bold; margin-bottom: 10px; }
                </style>
                <script>
                    // Client-side state variables
                    let currentStepIndex = 0; 
                    let totalSteps = 0;
                    let routeActive = true; 

                    // Function to update map and instruction on the client
                    function updateNavigationUI(stepIndex, instruction, totalStepsFromAPI) {
                        // Update text elements
                        document.getElementById('current-step-instruction').innerText = instruction;
                        document.getElementById('step-counter').innerText = `Step ${stepIndex + 1} of ${totalStepsFromAPI}`;
                        
                        // Update map image with cache-buster
                        document.getElementById('map-image').src = `/map/${stepIndex}?_=${new Date().getTime()}`;
                    }

                    // Polling for current step data
                    function pollCurrentStep() {
                        if (!routeActive) return; // Stop polling if navigation is complete or reset

                        fetch('/current_step')
                            .then(res => {
                                if (res.status === 404) {
                                    routeActive = false; // Route might be finished or cleared
                                    document.getElementById('current-step-instruction').innerText = "Navigation finished or route cleared.";
                                    document.getElementById('step-counter').innerText = "";
                                    document.getElementById('map-image').src = ""; // Clear map
                                    return null;
                                }
                                return res.json();
                            })
                            .then(data => {
                                if (data && data.step_index !== undefined) {
                                    // Always call updateNavigationUI if valid data is received.
                                    // The cache-buster on the image source handles re-fetching the image.
                                    // Text elements will update only if their content actually changes.
                                    updateNavigationUI(data.step_index, data.instruction, data.total_steps);
                                }
                            })
                            .catch(err => console.error("Error polling current step:", err));
                    }

                    // Start polling when page loads
                    window.onload = function() {
                        pollCurrentStep(); // Initial update when page loads
                        setInterval(pollCurrentStep, 2000); // Poll every 2 seconds for updates
                    };
                </script>
            </head>
            <body>
                <h2>Live Navigation</h2>
                <div class="nav-info">
                    <p><b>Origin:</b> {{ origin }}</p>
                    <p><b>Destination:</b> {{ destination }}</p>
                    <p id="step-counter">Step {{ current_step_index + 1 }} of {{ steps_count }}</p>
                    <p class="instruction" id="current-step-instruction">Loading next instruction...</p>
                </div>
                <div id="map-container">
                    <img id="map-image" src="/map/{{ current_step_index }}" alt="Navigation Map"> 
                </div>
                <p><a href="/reset">Plan a new route</a></p>
            </body>
            </html>
        ''',
        origin=current_route['origin'],
        destination=current_route['destination'],
        current_step_index=current_route['step_index'],
        steps_count=len(current_route['steps']))

    # Initial page to get user location and destination
    return render_template_string('''
        <!doctype html>
        <html>
        <head>
            <title>Start Live Navigation</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <script>
            let lastKnownAccurateLocation = null; // Stores the best location found so far

            // Function to send location to Flask server
            function sendLocation(lat, lng, accuracy, method) {
                fetch('/update_location', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        lat: lat,
                        lng: lng,
                        accuracy: accuracy,
                        method: method
                    })
                })
                .then(res => res.json())
                .then(data => {
                    console.log("Location update acknowledged by server:", data);
                })
                .catch(err => console.error("Error sending location:", err));
            }

            // Function to get location from Google Geolocation API (fallback)
            function getFallbackLocation() {
                console.log("Attempting Google Geolocation API fallback...");
                fetch('/get_fallback_location')
                    .then(res => res.json())
                    .then(data => {
                        if (data.lat && data.lng) {
                            console.log(`Fallback location (Google API): ${data.lat},${data.lng} (Accuracy: ${data.accuracy}m)`);
                            const MIN_ACCEPTABLE_ACCURACY_FALLBACK = 1000; // e.g., 1km, discard very poor IP-based accuracy

                            if (data.accuracy < MIN_ACCEPTABLE_ACCURACY_FALLBACK) {
                                if (!lastKnownAccurateLocation || data.accuracy < lastKnownAccurateLocation.accuracy) {
                                    sendLocation(data.lat, data.lng, data.accuracy, "google_api");
                                    lastKnownAccurateLocation = {lat: data.lat, lng: data.lng, accuracy: data.accuracy};
                                }
                            } else {
                                console.warn(`Discarding Google API fallback due to very low accuracy: ${data.accuracy}m`);
                            }
                        } else {
                            console.error("Google fallback failed to return coordinates.");
                        }
                    })
                    .catch(err => console.error("Error with Google Geolocation fallback:", err));
            }

            // Function to start getting location (using browser GPS primarily)
            function startGettingLocation() {
                if (!navigator.geolocation) {
                    alert("Geolocation not supported by your browser. Navigation might not work reliably.");
                    getFallbackLocation(); // Try fallback even if browser GPS is absent
                    return;
                }

                // Use watchPosition for continuous updates
                navigator.geolocation.watchPosition(
                    pos => {
                        const currentAccuracy = pos.coords.accuracy;
                        const currentLat = pos.coords.latitude;
                        const currentLng = pos.coords.longitude;

                        console.log(`Browser GPS: ${currentLat},${currentLng} (Accuracy: ${currentAccuracy}m)`);

                        const MIN_MOVE_DISTANCE_FOR_UPDATE = 50; // meters
                        const MAX_ACCEPTABLE_ACCURACY_FOR_MOVE_UPDATE = 500; // meters

                        let shouldSend = false;
                        if (!lastKnownAccurateLocation) {
                            shouldSend = true;
                        } else if (currentAccuracy < lastKnownAccateLocation.accuracy) {
                            shouldSend = true;
                        } else if (geodesic(
                            [lastKnownAccurateLocation.lat, lastKnownAccurateLocation.lng],
                            [currentLat, currentLng]
                        ).meters > MIN_MOVE_DISTANCE_FOR_UPDATE && currentAccuracy < MAX_ACCEPTABLE_ACCURACY_FOR_MOVE_UPDATE) {
                            shouldSend = true;
                        }

                        if (shouldSend) {
                            sendLocation(currentLat, currentLng, currentAccuracy, "browser_gps_watch");
                            lastKnownAccurateLocation = {lat: currentLat, lng: currentLng, accuracy: currentAccuracy};
                        }

                        // If browser GPS is low accuracy (e.g., > 50m), *also* try fallback in parallel.
                        if (currentAccuracy > 50) {
                            getFallbackLocation();
                        }
                    },
                    err => {
                        console.error("Browser GPS error:", err);
                        // On any browser GPS error (permission denied, position unavailable), try fallback
                        getFallbackLocation();
                    },
                    {
                        enableHighAccuracy: true,
                        timeout: 15000,
                        maximumAge: 0
                    }
                );

                // Periodically try fallback as a safety net
                setInterval(getFallbackLocation, 30000); // Every 30 seconds
            }

            // Simple Haversine formula for geodesic distance (approximate, but fine for this use case)
            function geodesic(coords1, coords2) {
                const R = 6371e3; // metres (Earth's radius)
                const φ1 = coords1[0] * Math.PI/180;
                const φ2 = coords2[0] * Math.PI/180;
                const Δφ = (coords2[0]-coords1[0]) * Math.PI/180;
                const Δλ = (coords2[1]-coords1[1]) * Math.PI/180;

                const a = Math.sin(Δφ/2) * Math.sin(Δφ/2) +
                          Math.cos(φ1) * Math.cos(φ2) *
                          Math.sin(Δλ/2) * Math.sin(Δλ/2);
                const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));

                const d = R * c; // in metres
                return {meters: d};
            }

            window.onload = startGettingLocation;
            </script>
        </head>
        <body>
            <h2>Start Live Navigation</h2>
            <form method="POST">
              <p>Your current location will be set from your GPS automatically.</p>
              Destination: <input name="destination" required><br><br>
              <input type="submit" value="Start Navigation">
            </form>
        </body>
        </html>
    ''')

@app.route('/update_location', methods=['POST'])
def update_location():
    """
    Receives location updates from the client, sets the origin,
    and handles route re-calculation and step advancement.
    """
    data = request.get_json()
    lat = data.get('lat')
    lng = data.get('lng')
    accuracy = data.get('accuracy', 'unknown')
    method = data.get('method', 'unknown')

    if lat is None or lng is None:
        return jsonify({'error': 'Invalid data, lat and lng required'}), 400

    new_origin_str = f"{lat},{lng}"
    
    # Store the most recent, reasonably accurate origin
    current_route['origin'] = new_origin_str
    print(f"[{method.upper()}] Location: {new_origin_str} (Accuracy: {accuracy}m)")

    # --- Auto-advance Navigation Step ---
    # Only try to advance if there's an active route and we haven't reached the end
    if current_route['steps'] and current_route['step_index'] < len(current_route['steps']):
        current_step_target = current_route['steps'][current_route['step_index']]
        step_coords = (current_step_target['lat'], current_step_target['lng'])
        user_coords = (lat, lng)

        distance_to_step = geodesic(step_coords, user_coords).meters
        
        if distance_to_step < THRESHOLD_METERS_TO_ADVANCE_STEP:
            # Check if it's not the very last step before incrementing
            if current_route['step_index'] < len(current_route['steps']) - 1:
                current_route['step_index'] += 1
                print(f"Automatically advanced to step {current_route['step_index']}")
            else:
                print("Reached final destination!")
                # Optionally, clear the route to indicate completion
                # current_route['destination'] = None
                # current_route['steps'] = []
                # current_route['polyline'] = ''
                # current_route['step_index'] = 0 # Or set to -1 to indicate completion
    
    # --- Dynamic Route Re-calculation ---
    # This logic determines if a *new* route should be fetched from Google,
    # typically due to significant deviation or as an initial calculation.
    if current_route['destination']: # Only attempt if a destination is set
        current_lat, current_lng = lat, lng

        # Determine distance from the origin that was used for the LAST route calculation
        moved_distance = 0 # Initialize to 0, will be calculated if last_routed_origin exists
        if current_route['last_routed_origin'] is None:
            # Force a re-route if no route has been established (e.g., initial POST or after reset)
            moved_distance = MIN_REROUTE_METERS + 1 # Ensures should_reroute is True
        else:
            last_routed_lat, last_routed_lng = current_route['last_routed_origin']
            moved_distance = geodesic(
                (last_routed_lat, last_routed_lng),
                (current_lat, current_lng)
            ).meters
        
        should_reroute = False
        if current_route['last_routed_origin'] is None:
            should_reroute = True # Always re-route if no prior route origin
        elif moved_distance > MIN_REROUTE_METERS:
            should_reroute = True

        if should_reroute:
            print(f"Checking for re-route. Moved: {moved_distance:.2f}m from last route origin (min {MIN_REROUTE_METERS}m needed).")
            # If update_route is successful, it will reset current_route['step_index'] to 0
            # and update current_route['last_routed_origin'].
            if update_route(new_origin_str, current_route['destination']):
                print("Route updated dynamically with new origin.")
            else:
                print("Failed to update route dynamically.")
        else:
            print(f"Not re-routing. Moved only {moved_distance:.2f}m (min {MIN_REROUTE_METERS}m needed) or no route set.")

    return jsonify({'status': 'Location updated', 'method': method}), 200

@app.route('/get_fallback_location')
def get_fallback_location():
    """
    Endpoint for client to request location from Google's Geolocation API.
    Used as a fallback if browser GPS is unavailable or inaccurate.
    """
    geo_api_url = f"https://www.googleapis.com/geolocation/v1/geolocate?key={Maps_API_KEY}"
    try:
        response = requests.post(geo_api_url, json={"considerIp": True})
        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'lat': data['location']['lat'],
                'lng': data['location']['lng'],
                'accuracy': data['accuracy']
            })
        else:
            print(f"Google Geolocation API error: {response.status_code} - {response.text}")
            return jsonify({'error': f'Google API error: {response.status_code}'}), 500
    except requests.exceptions.RequestException as e:
        print(f"Network error in geolocation fallback: {e}")
        return jsonify({'error': 'Network error occurred'}), 500
    except Exception as e:
        print(f"An unexpected error occurred in geolocation fallback: {e}")
        return jsonify({'error': 'Exception occurred'}), 500

@app.route('/map/<int:step_index>') 
def step_map(step_index):
    """
    Generates and serves a static map image for a specific step in the route.
    """
    if not current_route['steps'] or step_index < 0 or step_index >= len(current_route['steps']):
        # If no route, or step_index out of bounds, try to return a default map or error gracefully
        if not current_route['destination']:
            # No destination set, so can't draw anything meaningful without user input
            return "No route active. Please start navigation.", 404
        else:
            # Route active but step_index is out of bounds, maybe display first step or an error
            print(f"Map request for invalid step_index: {step_index}. Total steps: {len(current_route['steps'])}")
            return "Invalid step for current route.", 404

    location = current_route['steps'][step_index]
    lat = location['lat']
    lng = location['lng']
    polyline = current_route['polyline']

    base_url = "https://maps.googleapis.com/maps/api/staticmap"
    params = {
        'size': f'{MAP_WIDTH}x{MAP_HEIGHT}',
        'zoom': '17', # Adjusted zoom for better context
        'center': f'{lat},{lng}',
        'path': f'color:0xff0000ff|weight:5|enc:{polyline}', # Red polyline for the route
        'format': 'jpg-baseline',
        'key': Maps_API_KEY
    }

    # Add markers for the current step and the destination
    markers = [
        f'markers=color:blue|label:{step_index+1}|{lat},{lng}', # Current step marker (blue)
        f'markers=color:red|label:E|{current_route["destination"]}' # Destination marker (red, 'E' for End)
    ]

    query = '&'.join([f'{k}={quote_plus(str(v))}' for k, v in params.items()])
    marker_query = '&'.join(markers)
    full_url = f"{base_url}?{query}&{marker_query}"

    try:
        response = requests.get(full_url)
        if response.status_code != 200:
            print(f"Failed to fetch map image: {response.status_code} - {response.content.decode()}")
            return f"Failed to fetch map image: {response.status_code}", 500
        return send_file(io.BytesIO(response.content), mimetype='image/jpeg')
    except requests.exceptions.RequestException as e:
        print(f"Network error fetching map image: {e}")
        return "Network error fetching map image", 500
    except Exception as e:
        print(f"An unexpected error occurred while fetching map: {e}")
        return "An unexpected error occurred", 500


@app.route('/current_step')
def current_step():
    """Returns JSON data for the current navigation step."""
    i = current_route['step_index']
    
    if not current_route['steps'] or i < 0 or i >= len(current_route['steps']):
        # No active route or beyond last step
        return jsonify({'error': 'No active route or navigation finished'}), 404

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
    """Resets the current navigation route."""
    current_route['origin'] = None
    current_route['last_routed_origin'] = None
    current_route['destination'] = None
    current_route['steps'] = []
    current_route['step_index'] = 0
    current_route['polyline'] = ''
    return "Route reset successfully. <a href='/'>Start a new one</a>"

# The if __name__ == "__main__": block is typically removed or commented out for Gunicorn deployment
# if __name__ == "__main__":
#     app.run(debug=True)
