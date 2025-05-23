from flask import Flask, request, jsonify, render_template_string, send_file
import requests
import io
from urllib.parse import quote_plus
import re
from geopy.distance import geodesic
import os # Import os module to access environment variables

app = Flask(__name__)

# --- Configuration ---
# IMPORTANT: Set this environment variable on Render!
# For local testing, you can uncomment the line below and set your key directly,
# but for deployment on Render, it's safer to use os.getenv()
Maps_API_KEY = os.getenv("Maps_API_KEY") # Ensure this matches the env var name on Render
# Maps_API_KEY = "YOUR_Maps_API_KEY_HERE" # Uncomment for local testing if not using env var

MAP_WIDTH = 320 # Increased for better visibility
MAP_HEIGHT = 240 # Increased for better visibility
# --- ADJUST THESE VALUES FOR TESTING ---
MIN_REROUTE_METERS = 20 # *** Increased! *** Minimum distance moved to trigger a route recalculation
# Advance step if user is within this distance of the step's END location
THRESHOLD_METERS_TO_ADVANCE_STEP = 10 # Adjusted for better reliability. Can be fine-tuned.

# Global state for the current navigation route
current_route = {
    'origin': None,
    'last_routed_origin': None, # Stores the origin (lat, lng tuple) used for the last successful route calculation
    'destination': None,
    'steps': [],
    'step_index': 0, # Current step being displayed/followed
    'polyline': '',
    'destination_reached': False # NEW FLAG: True when navigation is complete
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
    This function intelligently updates current_route['step_index'] based on user's current location.
    """
    if not Maps_API_KEY:
        print("Maps API Key is not set. Cannot fetch directions.")
        return False

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
            # Check for specific error messages from Google
            if 'error_message' in response:
                print(f"Google API Error: {response['error_message']}")
            return False

        steps = []
        for leg in response['routes'][0]['legs']:
            for step in leg['steps']:
                start_loc = step['start_location']
                end_loc = step['end_location'] # Store end_location for step advancement
                instruction = clean_html(step['html_instructions'])
                steps.append({
                    'lat': start_loc['lat'],
                    'lng': start_loc['lng'],
                    'instruction': instruction,
                    'end_lat': end_loc['lat'], # Added
                    'end_lng': end_loc['lng']  # Added
                })

        # Preserve current step_index or find closest step in new route
        current_user_lat, current_user_lng = None, None
        if origin_str and ',' in origin_str:
            try:
                current_user_lat, current_user_lng = map(float, origin_str.split(','))
            except ValueError:
                pass # Invalid origin string, can't align step_index

        current_route['steps'] = steps
        current_route['polyline'] = response['routes'][0]['overview_polyline']['points']
        current_route['destination_reached'] = False # Reset this if a new route is calculated

        if current_user_lat is not None and current_user_lng is not None and steps:
            min_dist = float('inf')
            closest_step_index = 0 # Default to first step

            # Find the step in the NEW route that is closest to the user's current location
            for i, step in enumerate(current_route['steps']):
                step_start_coords = (step['lat'], step['lng'])
                dist = geodesic(step_start_coords, (current_user_lat, current_user_lng)).meters
                if dist < min_dist:
                    min_dist = dist
                    closest_step_index = i
            
            # If the new route is very short (e.g., 1 step) and we're already beyond the first step,
            # ensure we don't try to access an invalid index
            if closest_step_index >= len(current_route['steps']):
                closest_step_index = max(0, len(current_route['steps']) - 1)

            current_route['step_index'] = closest_step_index
            print(f"Re-routing: Aligned step_index to {closest_step_index} (closest step was {min_dist:.2f}m away).")

        else:
            current_route['step_index'] = 0 # Fallback if user origin not available or no steps
            print("Re-routing: User origin not available or no steps, resetting step_index to 0.")

        # Update last_routed_origin
        if origin_str and ',' in origin_str:
            try:
                lat_orig, lng_orig = map(float, origin_str.split(','))
                current_route['last_routed_origin'] = (lat_orig, lng_orig)
            except ValueError:
                print(f"Warning: Could not parse origin '{origin_str}' for last_routed_origin.")
                current_route['last_routed_origin'] = None
        else:
            current_route['last_routed_origin'] = None # Reset if origin is invalid/None
        
        print(f"Route updated: {len(steps)} steps from {origin_str} to {destination_str}. Current step_index: {current_route['step_index']}.")
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
                    function updateNavigationUI(stepIndex, instruction, totalStepsFromAPI, status) {
                        if (status === 'Destination reached') {
                            document.getElementById('current-step-instruction').innerText = "You have arrived!";
                            document.getElementById('step-counter').innerText = "Destination Reached!"; // Clear step counter
                            document.getElementById('map-image').src = ""; // Clear map
                            routeActive = false; // Stop polling
                            return;
                        }

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
                                if (res.status === 404) { // Handle case where no route is active
                                    routeActive = false;
                                    document.getElementById('current-step-instruction').innerText = "Navigation finished or route cleared.";
                                    document.getElementById('step-counter').innerText = "";
                                    document.getElementById('map-image').src = "";
                                    return null;
                                }
                                return res.json();
                            })
                            .then(data => {
                                if (data) {
                                    updateNavigationUI(data.step_index, data.instruction, data.total_steps, data.status); // Pass status
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
                // Note: This endpoint '/get_fallback_location' needs to be implemented on the server
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

                        const MIN_MOVE_DISTANCE_FOR_UPDATE = 10; // meters (reduce for more frequent updates if needed)
                        const MAX_ACCEPTABLE_ACCURACY_FOR_MOVE_UPDATE = 500; // meters

                        let shouldSend = false;
                        if (!lastKnownAccurateLocation) {
                            shouldSend = true; // Always send the first location
                        } else {
                            // Simple Haversine formula for geodesic distance (approximate, but fine for this use case)
                            const R = 6371e3; // metres (Earth's radius)
                            const φ1 = lastKnownAccurateLocation.lat * Math.PI/180;
                            const φ2 = currentLat * Math.PI/180;
                            const Δφ = (currentLat - lastKnownAccurateLocation.lat) * Math.PI/180;
                            const Δλ = (currentLng - lastKnownAccurateLocation.lng) * Math.PI/180;

                            const a = Math.sin(Δφ/2) * Math.sin(Δφ/2) +
                                      Math.cos(φ1) * Math.cos(φ2) *
                                      Math.sin(Δλ/2) * Math.sin(Δλ/2);
                            const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));

                            const distanceMoved = R * c; // in metres


                            // Send if accuracy is significantly better OR if moved a minimum distance AND accuracy is acceptable
                            if (currentAccuracy < lastKnownAccurateLocation.accuracy * 0.8) { // 20% better accuracy
                                shouldSend = true;
                            } else if (distanceMoved > MIN_MOVE_DISTANCE_FOR_UPDATE && currentAccuracy < MAX_ACCEPTABLE_ACCURACY_FOR_MOVE_UPDATE) {
                                shouldSend = true;
                            }
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
    accuracy = data.get('accuracy', 'unknown') # Now properly receiving accuracy
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
        current_step_target_end = current_route['steps'][current_route['step_index']]
        # Use end_lat and end_lng for checking if we passed the step
        # Make sure 'end_lat' and 'end_lng' are stored in steps (see update_route)
        if 'end_lat' in current_step_target_end and 'end_lng' in current_step_target_end:
            end_step_coords = (current_step_target_end['end_lat'], current_step_target_end['end_lng'])
            user_coords = (lat, lng)

            distance_to_end_of_step = geodesic(end_step_coords, user_coords).meters
            
            # If user is within THRESHOLD_METERS_TO_ADVANCE_STEP of the END of the current step
            if distance_to_end_of_step < THRESHOLD_METERS_TO_ADVANCE_STEP:
                # Check if it's not the very last step before incrementing
                if current_route['step_index'] < len(current_route['steps']) - 1:
                    current_route['step_index'] += 1
                    print(f"Automatically advanced to step {current_route['step_index']}. Distance to end of previous step: {distance_to_end_of_step:.2f}m")
                else:
                    # Reached the last step, and now close to its end (which is destination)
                    print(f"Reached final destination! Distance to destination: {distance_to_end_of_step:.2f}m")
                    current_route['destination_reached'] = True 
                    # Optionally, clear route for new planning (commented out for now to allow 'You arrived' message to persist)
                    # current_route['origin'] = None
                    # current_route['destination'] = None
                    # current_route['steps'] = []
                    # current_route['polyline'] = ''
                    # current_route['step_index'] = 0 # Reset for future use, or -1 to signal finished.
            else:
                print(f"Not advancing step {current_route['step_index']}. Distance to end of step: {distance_to_end_of_step:.2f}m (needed < {THRESHOLD_METERS_TO_ADVANCE_STEP}m)")
        else:
            print("Warning: 'end_lat' or 'end_lng' not found in current step data for advancement check.")
    else:
        # This block is reached if no steps are loaded yet, or if navigation finished/reset.
        if current_route.get('destination_reached'):
            print("Navigation is complete.")
        # No 'else' here, to avoid printing "No active steps for navigation" repeatedly if
        # a route is just starting or hasn't been calculated yet.


    # --- Dynamic Route Re-calculation ---
    # This logic determines if a *new* route should be fetched from Google,
    # typically due to significant deviation or as an initial calculation.
    # This should ONLY happen if there is an active destination AND destination has NOT been reached
    if current_route['destination'] and not current_route['destination_reached']:
        current_lat, current_lng = lat, lng

        # Determine distance from the origin that was used for the LAST route calculation
        moved_distance = 0
        should_reroute = False

        if current_route['last_routed_origin'] is None:
            # Initial route calculation or after a full reset/new destination
            print("No last routed origin. Forcing initial route calculation.")
            should_reroute = True
        else:
            last_routed_lat, last_routed_lng = current_route['last_routed_origin']
            moved_distance = geodesic(
                (last_routed_lat, last_routed_lng),
                (current_lat, current_lng)
            ).meters
            
            # Re-route if moved significantly OR if current step is out of sync/invalid
            if moved_distance > MIN_REROUTE_METERS:
                should_reroute = True
                print(f"Moved {moved_distance:.2f}m from last route origin. Re-routing.")
            elif current_route['step_index'] >= len(current_route['steps']):
                # This could happen if current_route['steps'] was cleared or became empty
                # while step_index was high, or if a very short route became invalid.
                print("Current step index out of bounds for existing steps. Re-routing to re-align.")
                should_reroute = True

        if should_reroute:
            # If update_route is successful, it will reset current_route['step_index']
            # to the closest step in the *new* route.
            if update_route(new_origin_str, current_route['destination']):
                print("Route updated dynamically with new origin.")
            else:
                print("Failed to update route dynamically during re-route attempt.")
        else:
            print(f"Not re-routing. Moved only {moved_distance:.2f}m (min {MIN_REROUTE_METERS}m needed) or route is currently stable.")

    return jsonify({'status': 'Location updated', 'method': method}), 200

@app.route('/get_fallback_location')
def get_fallback_location():
    """
    Endpoint for client to request location from Google's Geolocation API.
    Used as a fallback if browser GPS is unavailable or inaccurate.
    """
    # Replace Maps_API_KEY with Maps_API_KEY if that's what you used
    if not Maps_API_KEY:
        print("Maps API Key is not set. Cannot use Geolocation API.")
        return jsonify({'error': 'Maps API Key not configured'}), 500

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


@app.route('/map/<int:step>') # Changed 'step' to 'step_index' for consistency with client and current_route['step_index']
def step_map(step):
    """
    Generates and serves a static map image for a specific step in the route.
    """
    # If destination reached, clear map or show a static arrival map
    if current_route.get('destination_reached'):
        # You could return a specific 'destination reached' map here,
        # or just an empty response if the client clears the image.
        print("Map requested for destination reached state. Returning blank or arrival map.")
        # Example: return a transparent GIF or a custom "arrived" image
        return send_file(io.BytesIO(b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x00\x44\x01\x00\x3b'), mimetype='image/gif')

    if not current_route['steps'] or step < 0 or step >= len(current_route['steps']):
        print(f"Map request for invalid step_index: {step}. Total steps: {len(current_route['steps'])}. No active route or invalid step.")
        # Return a blank map or error
        return "No active route or invalid step for map.", 404

    location = current_route['steps'][step]
    lat = location['lat']
    lng = location['lng']
    polyline = current_route['polyline']

    base_url = "https://maps.googleapis.com/maps/api/staticmap"
    params = {
        'size': f'{MAP_WIDTH}x{MAP_HEIGHT}',
        'zoom': '17', # Adjusted zoom for better context
        'center': f'{lat},{lng}', # Center on the start of the current step
        'path': f'color:0xff0000ff|weight:5|enc:{polyline}', # Red polyline for the route
        'format': 'jpg-baseline',
        'key': Maps_API_KEY
    }

    # Add markers for the current step and the destination
    markers = [
        f'markers=color:blue|label:{step+1}|{lat},{lng}', # Current step marker (blue)
    ]
    # Add destination marker if it's not the current step's end location
    if current_route['destination']:
        # This will be the string like "lat,lng" for the destination directly
        markers.append(f'markers=color:red|label:E|{current_route["destination"]}')


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
    # Check if destination reached first
    if current_route.get('destination_reached'):
        return jsonify({
            'status': 'Destination reached',
            'instruction': 'You have arrived!',
            'step_index': len(current_route['steps']) -1 if current_route['steps'] else 0, # Point to the last step's index or 0 if no steps
            'total_steps': len(current_route['steps'])
        }), 200

    i = current_route['step_index']
    
    if not current_route['steps'] or i < 0 or i >= len(current_route['steps']):
        # No active route or beyond last valid step
        return jsonify({'error': 'No active route or navigation finished'}), 404

    step_data = current_route['steps'][i]
    return jsonify({
        'step_index': i,
        'lat': step_data['lat'],
        'lng': step_data['lng'],
        'instruction': step_data['instruction'],
        'total_steps': len(current_route['steps']),
        'status': 'Navigating' # Indicate normal navigation state
    })

@app.route('/reset')
def reset():
    """Resets the current navigation route."""
    current_route['origin'] = None
    current_route['last_routed_origin'] = None # Reset this as well
    current_route['destination'] = None
    current_route['steps'] = []
    current_route['step_index'] = 0
    current_route['polyline'] = ''
    current_route['destination_reached'] = False # Reset this flag
    return "Route reset successfully. <a href='/'>Start a new one</a>"

# This __name__ == "__main__" block is for local development.
# For deployment on Render, Gunicorn (or similar WSGI server) handles running the app.
# You can keep it for local testing if needed, but it won't be used by Render.
if __name__ == '__main__':
    # You might want to get the IP differently for local testing if 'socket' is not working
    # For Render deployment, this block is typically ignored.
    try:
        import socket
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = '127.0.0.1' # Fallback for local testing
    
    print(f"Flask server running at http://{local_ip}:5000")
    app.run(host='0.0.0.0', port=5000, debug=True) # debug=True for local testing verbose logs
