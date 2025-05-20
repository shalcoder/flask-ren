from flask import Flask, request, jsonify, render_template_string, send_file
import requests
import io
from urllib.parse import quote_plus
import re
from geopy.distance import geodesic
import os

app = Flask(__name__)

# --- Configuration ---
Maps_API_KEY = os.getenv("Maps_API_KEY") # Use environment variable for security
MAP_WIDTH = 320
MAP_HEIGHT = 240
MIN_REROUTE_METERS = 50 # Minimum distance moved to trigger a route recalculation

# Global state for the current navigation route
current_route = {
    'origin': None,
    'last_routed_origin': None, # Stores the origin used for the last successful route calculation
    'destination': None,
    'steps': [],
    'step_index': 0,
    'polyline': ''
}

# --- Helper Functions ---
def clean_html(raw_html):
    """Removes HTML tags from a string."""
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)

def update_route(origin, destination):
    """
    Fetches directions from Google Maps API and updates the current_route.
    Returns True on success, False otherwise.
    """
    directions_url = 'https://maps.googleapis.com/maps/api/directions/json'
    params = {
        'origin': origin,
        'destination': destination,
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
        current_route['step_index'] = 0
        
        # Update last_routed_origin only if the new origin is valid
        if origin and ',' in origin:
            try:
                lat, lng = map(float, origin.split(','))
                current_route['last_routed_origin'] = (lat, lng)
            except ValueError:
                print(f"Warning: Could not parse origin '{origin}' for last_routed_origin.")
        else:
            current_route['last_routed_origin'] = None # Reset if origin is invalid/None
        
        print(f"Route updated: {len(steps)} steps from {origin}.")
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

        if not current_route['origin']:
            return "Origin not set yet from GPS. Please wait for GPS fix.", 400

        success = update_route(current_route['origin'], destination)
        if not success:
            return "Could not calculate route. Check destination and try again.", 500

        return render_template_string('''
            <!doctype html>
            <html>
            <head>
                <title>Navigation Started</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
            </head>
            <body>
                <h2>Navigation Started</h2>
                <p><b>Origin:</b> {{ origin }}</p>
                <p><b>Destination:</b> {{ destination }}</p>
                <p><b>Total steps:</b> {{ steps_count }}</p>
                <img src="/map/0" alt="Step 1 Map" style="max-width:100%; height:auto;"><br><br>
                <a href="/">Plan another route</a>
            </body>
            </html>
        ''',
        origin=current_route['origin'],
        destination=current_route['destination'],
        steps_count=len(current_route['steps']))

    # Initial page to get user location and destination
    return render_template_string('''
        <!doctype html>
        <html>
        <head>
            <title>Live Navigation</title>
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

                        // Decide whether to send this location to the server
                        // 1. If it's the first location received
                        // 2. If it's significantly more accurate than the last known accurate location
                        // 3. If we've moved significantly AND the current accuracy is reasonable (e.g., < 500m)
                        const MIN_MOVE_DISTANCE_FOR_UPDATE = 50; // meters
                        const MAX_ACCEPTABLE_ACCURACY_FOR_MOVE_UPDATE = 500; // meters

                        let shouldSend = false;
                        if (!lastKnownAccurateLocation) {
                            shouldSend = true;
                        } else if (currentAccuracy < lastKnownAccurateLocation.accuracy) {
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
                        // Don't rely solely on browser GPS if it's poor.
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
                        timeout: 15000, // Give GPS more time to get a fix
                        maximumAge: 0   // Get a fresh reading every time
                    }
                );

                // Periodically try fallback as a safety net, in case watchPosition doesn't trigger frequently
                // enough or fails silently for a long time.
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
            <h2>Start Navigation</h2>
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
    # We still update current_route['origin'] to reflect the very latest known position
    current_route['origin'] = new_origin_str
    print(f"[{method.upper()}] Location: {new_origin_str} (Accuracy: {accuracy}m)")

    # --- Auto-advance Navigation Step ---
    if current_route['steps']:
        current_step_target = current_route['steps'][current_route['step_index']]
        step_coords = (current_step_target['lat'], current_step_target['lng'])
        user_coords = (lat, lng)

        distance_to_step = geodesic(step_coords, user_coords).meters
        THRESHOLD_METERS_TO_ADVANCE_STEP = 15 # Advance step if within this distance

        if distance_to_step < THRESHOLD_METERS_TO_ADVANCE_STEP:
            if current_route['step_index'] < len(current_route['steps']) - 1:
                current_route['step_index'] += 1
                print(f"Automatically advanced to step {current_route['step_index']}")
                # If we advance a step, consider re-routing from current location
                # to get the most accurate next segment.
                if current_route['destination']:
                    print("Re-routing after advancing step...")
                    update_route(new_origin_str, current_route['destination'])

    # --- Dynamic Route Re-calculation ---
    # Re-route if we have a destination AND:
    # 1. This is the first route calculation for this destination (no last_routed_origin set initially)
    # 2. We have moved a significant distance since the last route calculation
    if current_route['destination']:
        # Ensure last_routed_origin is set for comparison, or initialize it
        if current_route['last_routed_origin'] is None:
            # If no route was calculated yet, or last_routed_origin was reset, force a re-route.
            moved_distance = MIN_REROUTE_METERS + 1 # Force re-route
        else:
            last_lat, last_lng = current_route['last_routed_origin']
            moved_distance = geodesic((last_lat, last_lng), (lat, lng)).meters
        
        if moved_distance > MIN_REROUTE_METERS:
            print(f"Checking for re-route. Moved: {moved_distance:.2f}m from last route origin.")
            if update_route(new_origin_str, current_route['destination']):
                print("Route updated dynamically with new origin.")
                # The update_route function now sets last_routed_origin
            else:
                print("Failed to update route dynamically.")
        else:
            print(f"Not re-routing. Moved only {moved_distance:.2f}m (min {MIN_REROUTE_METERS}m needed).")

    return jsonify({'status': 'Location updated', 'method': method}), 200

@app.route('/get_fallback_location')
def get_fallback_location():
    """
    Endpoint for client to request location from Google's Geolocation API.
    Used as a fallback if browser GPS is unavailable or inaccurate.
    """
    geo_api_url = f"https://www.googleapis.com/geolocation/v1/geolocate?key={Maps_API_KEY}"
    try:
        # Using considerIp=True as a last resort, as it can be very inaccurate
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

@app.route('/map/<int:step>')
def step_map(step):
    """
    Generates and serves a static map image for a specific step in the route.
    """
    if not current_route['steps'] or step < 0 or step >= len(current_route['steps']):
        return "No such step or route not defined", 404

    location = current_route['steps'][step]
    lat = location['lat']
    lng = location['lng']
    polyline = current_route['polyline']

    base_url = "https://maps.googleapis.com/maps/api/staticmap"
    params = {
        'size': f'{MAP_WIDTH}x{MAP_HEIGHT}',
        'zoom': '18', # Adjust zoom for better visibility of the current step
        'center': f'{lat},{lng}',
        'path': f'color:0xff0000ff|weight:5|enc:{polyline}', # Red polyline for the route
        'format': 'jpg-baseline',
        'key': Maps_API_KEY
    }

    # Add markers for the current step and the destination
    markers = [
        f'markers=color:blue|label:{step+1}|{lat},{lng}', # Current step marker (blue)
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
    if i < 0 or i >= len(current_route['steps']):
        return jsonify({'error': 'No current step or route not defined'}), 404

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
    return "Route reset successfully."

# The if __name__ == "__main__": block is typically removed or commented out for Gunicorn deployment
# if __name__ == "__main__":
#     app.run(debug=True)
