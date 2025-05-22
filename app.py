from flask import Flask, request, jsonify, render_template_string, send_file
import requests
import io
from urllib.parse import quote_plus
import re
from geopy.distance import geodesic
import os

app = Flask(__name__)

# --- Configuration ---
# IMPORTANT: Set this environment variable if deploying (e.g., on Render)!
# For local testing, you can leave it hardcoded, but it's bad practice for production.
Maps_API_KEY = 'AIzaSyDZuZ1sMCSJSyC_u-rbzHC8BvbIyzAgL3M' # Replace with your actual API key if needed
MAP_WIDTH = 480 # Increased for better visibility
MAP_HEIGHT = 360 # Increased for better visibility
MIN_REROUTE_METERS = 50 # Minimum distance moved from last_routed_origin to trigger a route recalculation
THRESHOLD_METERS_TO_ADVANCE_STEP = 15 # Advance step if user is within this distance of the step's start

# Global state for the current navigation route
current_route = {
    'origin': None, # Stores the most recent origin string ("lat,lng")
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
            if 'error_message' in response:
                print(f"Error message: {response['error_message']}")
            return False

        if not response.get('routes') or not response['routes'][0].get('legs'):
            print(f"No routes or legs found in API response for {origin_str} to {destination_str}.")
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
        current_route['step_index'] = 0 # CRITICAL RESET POINT FOR NEW ROUTES
        
        # Update last_routed_origin only if the new origin string is valid
        if origin_str and ',' in origin_str:
            try:
                lat_orig, lng_orig = map(float, origin_str.split(','))
                current_route['last_routed_origin'] = (lat_orig, lng_orig)
            except ValueError:
                print(f"Warning: Could not parse origin '{origin_str}' for last_routed_origin.")
                current_route['last_routed_origin'] = None # Or handle as error
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

        if not current_route['origin']:
            return "Origin not set yet from GPS. Please wait for GPS fix and resubmit.", 400

        success = update_route(current_route['origin'], destination)
        if not success:
            return "Could not calculate route. Check destination and try again. Ensure API key is valid and has Directions API enabled.", 500

        # After a successful route calculation, render the navigation view
        return render_template_string('''
            <!doctype html>
            <html>
            <head>
                <title>Navigation</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body { font-family: sans-serif; margin: 10px; background-color: #f4f4f4; color: #333; }
                    .container { max-width: 600px; margin: auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
                    h2 { color: #333; text-align: center; }
                    #map-container img { max-width: 100%; height: auto; display: block; margin: 20px auto; border: 1px solid #ddd; border-radius: 4px;}
                    .nav-info { margin-top: 15px; border-top: 1px solid #eee; padding-top: 15px; }
                    .nav-info p { margin: 8px 0; }
                    .instruction { font-size: 1.2em; font-weight: bold; margin-bottom: 10px; color: #007bff; }
                    #current-origin-display { font-weight: normal; color: #555;}
                    a { color: #007bff; text-decoration: none; }
                    a:hover { text-decoration: underline; }
                    .button-link { display: inline-block; padding: 10px 15px; background-color: #dc3545; color: white; border-radius: 4px; text-align: center; margin-top:20px;}
                </style>
                <script>
                    let routeActive = true; 

                    function updateNavigationUI(stepIndex, instruction, totalStepsFromAPI, currentOriginFromServer) {
                        document.getElementById('current-step-instruction').innerText = instruction || "Loading instruction...";
                        document.getElementById('step-counter').innerText = `Step ${stepIndex + 1} of ${totalStepsFromAPI}`;
                        
                        if (currentOriginFromServer) {
                            document.getElementById('current-origin-display').innerText = currentOriginFromServer;
                        }
                        
                        document.getElementById('map-image').src = `/map/${stepIndex}?_=${new Date().getTime()}`;
                    }

                    function pollCurrentStep() {
                        if (!routeActive) return;

                        fetch('/current_step')
                            .then(res => {
                                if (res.status === 404) {
                                    routeActive = false; 
                                    document.getElementById('current-step-instruction').innerText = "Navigation finished or route cleared.";
                                    document.getElementById('step-counter').innerText = "Route inactive";
                                    // document.getElementById('map-image').src = ""; // Optionally clear map
                                    return null;
                                }
                                if (!res.ok) {
                                    throw new Error(`HTTP error! status: ${res.status}`);
                                }
                                return res.json();
                            })
                            .then(data => {
                                if (data && data.step_index !== undefined) {
                                    updateNavigationUI(data.step_index, data.instruction, data.total_steps, data.current_origin);
                                } else if (data && data.error) {
                                     console.warn("Server responded with error for current_step:", data.error);
                                     // Optionally update UI to show this error
                                }
                            })
                            .catch(err => {
                                console.error("Error polling current step:", err);
                                // document.getElementById('current-step-instruction').innerText = "Error fetching updates. Check console.";
                            });
                    }

                    window.onload = function() {
                        pollCurrentStep(); // Initial update
                        setInterval(pollCurrentStep, 3000); // Poll for updates (e.g., every 3 seconds)
                    };
                </script>
            </head>
            <body>
                <div class="container">
                    <h2>Live Navigation</h2>
                    <div class="nav-info">
                        <p><b>Origin:</b> <span id="current-origin-display">{{ origin }}</span></p>
                        <p><b>Destination:</b> {{ destination }}</p>
                        <p id="step-counter">Step {{ current_step_index + 1 }} of {{ steps_count }}</p>
                        <p class="instruction" id="current-step-instruction">Loading first instruction...</p>
                    </div>
                    <div id="map-container">
                        <img id="map-image" src="/map/{{ current_step_index }}" alt="Navigation Map"> 
                    </div>
                    <p style="text-align:center;"><a href="/reset" class="button-link">Plan a New Route</a></p>
                </div>
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
            <style>
                body { font-family: sans-serif; margin: 20px; background-color: #f4f4f4; color: #333; display: flex; justify-content: center; align-items: center; min-height: 90vh; }
                .container { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 0 15px rgba(0,0,0,0.1); text-align: center; }
                h2 { color: #333; margin-bottom: 20px; }
                p { margin-bottom: 15px; color: #555; }
                input[type="text"] { padding: 10px; margin-bottom: 20px; border: 1px solid #ddd; border-radius: 4px; width: calc(100% - 22px); }
                input[type="submit"] { padding: 10px 20px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }
                input[type="submit"]:hover { background-color: #0056b3; }
                #status { margin-top:15px; font-style:italic; color: #6c757d; }
            </style>
            <script>
            let lastKnownAccurateLocation = null; 

            function sendLocation(lat, lng, accuracy, method) {
              document.getElementById('status').innerText = `Location: ${lat.toFixed(5)}, ${lng.toFixed(5)} (Acc: ${accuracy.toFixed(1)}m, Method: ${method})`;
              fetch('/update_location', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ lat: lat, lng: lng, accuracy: accuracy, method: method })
              })
              .then(res => res.json())
              .then(data => console.log("Location update acknowledged by server:", data))
              .catch(err => console.error("Error sending location:", err));
            }

            function getFallbackLocation() {
              console.log("Attempting Google Geolocation API fallback...");
              document.getElementById('status').innerText = "Attempting IP-based location...";
              fetch('/get_fallback_location')
                .then(res => res.json())
                .then(data => {
                  if (data.lat && data.lng) {
                    console.log(`Fallback location (Google API): ${data.lat},${data.lng} (Accuracy: ${data.accuracy}m)`);
                    const MIN_ACCEPTABLE_ACCURACY_FALLBACK = 1000; 

                    if (data.accuracy < MIN_ACCEPTABLE_ACCURACY_FALLBACK) {
                      if (!lastKnownAccurateLocation || data.accuracy < lastKnownAccurateLocation.accuracy) {
                        sendLocation(data.lat, data.lng, data.accuracy, "google_api");
                        lastKnownAccurateLocation = {lat: data.lat, lng: data.lng, accuracy: data.accuracy};
                      }
                    } else {
                      console.warn(`Discarding Google API fallback due to low accuracy: ${data.accuracy}m`);
                      document.getElementById('status').innerText = `IP-based location too inaccurate (${data.accuracy.toFixed(0)}m). Waiting for GPS.`;
                    }
                  } else {
                    console.error("Google fallback failed to return coordinates.");
                    document.getElementById('status').innerText = "IP-based location failed.";
                  }
                })
                .catch(err => {
                    console.error("Error with Google Geolocation fallback:", err);
                    document.getElementById('status').innerText = "Error getting IP-based location.";
                });
            }

            function startGettingLocation() {
              document.getElementById('status').innerText = "Requesting GPS location...";
              if (!navigator.geolocation) {
                alert("Geolocation not supported by your browser. Navigation might not work reliably.");
                document.getElementById('status').innerText = "Geolocation not supported. Trying IP-based location.";
                getFallbackLocation(); 
                return;
              }

              navigator.geolocation.watchPosition(
                pos => {
                  const currentAccuracy = pos.coords.accuracy;
                  const currentLat = pos.coords.latitude;
                  const currentLng = pos.coords.longitude;

                  console.log(`Browser GPS: ${currentLat},${currentLng} (Accuracy: ${currentAccuracy}m)`);
                  document.getElementById('status').innerText = `GPS: ${currentLat.toFixed(5)}, ${currentLng.toFixed(5)} (Acc: ${currentAccuracy.toFixed(1)}m)`;


                  const MIN_MOVE_DISTANCE_FOR_UPDATE = 10; // meters - send more frequent updates if good accuracy
                  const MAX_ACCEPTABLE_ACCURACY_FOR_MOVE_UPDATE = 100; // meters - don't send if very inaccurate and not moved much

                  let shouldSend = false;
                  if (!lastKnownAccurateLocation) { // First good fix
                    if (currentAccuracy < MAX_ACCEPTABLE_ACCURACY_FOR_MOVE_UPDATE) shouldSend = true;
                  } else if (currentAccuracy < lastKnownAccurateLocation.accuracy) { // Better accuracy
                    shouldSend = true;
                  } else if (geodesicDistance( // Moved significantly
                      [lastKnownAccurateLocation.lat, lastKnownAccurateLocation.lng],
                      [currentLat, currentLng]
                    ).meters > MIN_MOVE_DISTANCE_FOR_UPDATE && currentAccuracy < MAX_ACCEPTABLE_ACCURACY_FOR_MOVE_UPDATE) {
                    shouldSend = true;
                  }

                  if (shouldSend) {
                    sendLocation(currentLat, currentLng, currentAccuracy, "browser_gps_watch");
                    lastKnownAccurateLocation = {lat: currentLat, lng: currentLng, accuracy: currentAccuracy};
                  }

                  if (currentAccuracy > 75) { // If GPS accuracy degrades, try fallback
                    getFallbackLocation();
                  }
                },
                err => {
                  console.error("Browser GPS error:", err);
                  let errorMsg = "GPS error. ";
                  if (err.code == 1) errorMsg += "Permission denied. ";
                  else if (err.code == 2) errorMsg += "Position unavailable. ";
                  else if (err.code == 3) errorMsg += "Timeout. ";
                  document.getElementById('status').innerText = errorMsg + "Trying IP-based location.";
                  getFallbackLocation();
                },
                { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
              );
              // Backup periodic fallback check
              // setInterval(getFallbackLocation, 45000); // Check fallback less frequently if watchPosition is active
            }

            // Simple Haversine formula for geodesic distance (client-side)
            function geodesicDistance(coords1, coords2) {
                const R = 6371e3; // metres (Earth's radius)
                const φ1 = coords1[0] * Math.PI/180;
                const φ2 = coords2[0] * Math.PI/180;
                const Δφ = (coords2[0]-coords1[0]) * Math.PI/180;
                const Δλ = (coords2[1]-coords1[1]) * Math.PI/180;
                const a = Math.sin(Δφ/2) * Math.sin(Δφ/2) +
                          Math.cos(φ1) * Math.cos(φ2) *
                          Math.sin(Δλ/2) * Math.sin(Δλ/2);
                const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
                return {meters: R * c};
            }

            window.onload = startGettingLocation;
            </script>
        </head>
        <body>
            <div class="container">
                <h2>Start Live Navigation</h2>
                <p>Your current location will be set from your GPS automatically.</p>
                <p id="status">Initializing...</p>
                <form method="POST">
                    Destination: <input name="destination" type="text" placeholder="e.g., Eiffel Tower, Paris" required><br><br>
                    <input type="submit" value="Start Navigation">
                </form>
            </div>
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
    
    current_route['origin'] = new_origin_str # Always update to the latest known origin string
    print(f"[{method.upper()}] Location: {new_origin_str} (Accuracy: {accuracy}m)")

    # --- Auto-advance Navigation Step ---
    if current_route['steps'] and current_route['step_index'] < len(current_route['steps']):
        current_step_target = current_route['steps'][current_route['step_index']]
        step_coords = (current_step_target['lat'], current_step_target['lng'])
        user_coords = (lat, lng)

        distance_to_step = geodesic(user_coords, step_coords).meters # Note: geopy geodesic takes (point1, point2)
        
        if distance_to_step < THRESHOLD_METERS_TO_ADVANCE_STEP:
            if current_route['step_index'] < len(current_route['steps']) - 1:
                current_route['step_index'] += 1
                print(f"Automatically advanced to step {current_route['step_index'] + 1} / {len(current_route['steps'])}")
            else:
                print("Reached final destination step!")
                # Optionally, could add logic here to "finish" the route
    
    # --- Dynamic Route Re-calculation ---
    if current_route['destination']: 
        current_lat, current_lng = lat, lng
        moved_distance = 0 
        
        should_reroute = False
        if current_route['last_routed_origin'] is None:
            # This case is typically handled by the initial POST to '/', but as a fallback:
            # If a destination is set, but no route has ever been calculated with a specific origin,
            # this implies we should try to route now.
            print("No 'last_routed_origin' set, considering re-route if destination exists.")
            should_reroute = True 
        else:
            last_routed_lat, last_routed_lng = current_route['last_routed_origin']
            moved_distance = geodesic((current_lat, current_lng), (last_routed_lat, last_routed_lng)).meters
            if moved_distance > MIN_REROUTE_METERS:
                should_reroute = True

        if should_reroute:
            print(f"Re-route check. Moved: {moved_distance:.2f}m from last route origin (threshold {MIN_REROUTE_METERS}m). Attempting re-route.")
            if update_route(new_origin_str, current_route['destination']):
                print(f"Route updated dynamically. New origin: {new_origin_str}. New step index: {current_route['step_index'] +1 }")
            else:
                print(f"Failed to update route dynamically from {new_origin_str}.")
        # else: # Commented out for brevity in logs, can be enabled for debugging
            # print(f"Not re-routing. Moved only {moved_distance:.2f}m (min {MIN_REROUTE_METERS}m needed) or no significant change.")

    return jsonify({'status': 'Location updated', 'method': method, 'current_origin_set': new_origin_str}), 200

@app.route('/get_fallback_location')
def get_fallback_location():
    geo_api_url = f"https://www.googleapis.com/geolocation/v1/geolocate?key={Maps_API_KEY}"
    try:
        response = requests.post(geo_api_url, json={"considerIp": True})
        response.raise_for_status() # Raises an HTTPError for bad responses (4XX or 5XX)
        data = response.json()
        return jsonify({
            'lat': data['location']['lat'],
            'lng': data['location']['lng'],
            'accuracy': data['accuracy']
        })
    except requests.exceptions.HTTPError as e:
        print(f"Google Geolocation API HTTP error: {e} - {response.text}")
        return jsonify({'error': f'Google API error: {e.response.status_code}'}), e.response.status_code
    except requests.exceptions.RequestException as e:
        print(f"Network error in geolocation fallback: {e}")
        return jsonify({'error': 'Network error occurred'}), 500
    except Exception as e: # Catch other potential errors like JSONDecodeError
        print(f"An unexpected error occurred in geolocation fallback: {e}")
        return jsonify({'error': 'Exception occurred processing fallback response'}), 500

@app.route('/map/<int:step_index>') 
def step_map(step_index):
    if not current_route['steps'] or step_index < 0 or step_index >= len(current_route['steps']):
        if not current_route['destination'] and not current_route['origin']:
             return "No route active. Please start navigation.", 404
        # Fallback: if there's an origin, show a map centered there, or a placeholder.
        # For simplicity, returning 404 if specific step is invalid for an active route.
        print(f"Map request for invalid step_index: {step_index}. Total steps: {len(current_route['steps'])}")
        # Could attempt to serve map for step 0 if route exists but index is bad.
        # For now, strict 404 if step_index is out of bounds for current_route.steps
        if current_route['steps'] and len(current_route['steps']) > 0 : # if there are steps, but index is bad try step 0
             step_index = 0 # default to first step if index is bad but route exists
        else: # no steps at all, so cannot display map
            return "Invalid step for current route or route not fully defined.", 404


    location = current_route['steps'][step_index]
    lat = location['lat']
    lng = location['lng']
    polyline = current_route['polyline'] # This is the polyline for the WHOLE route

    base_url = "https://maps.googleapis.com/maps/api/staticmap"
    params = {
        'size': f'{MAP_WIDTH}x{MAP_HEIGHT}',
        'zoom': '17', 
        'center': f'{lat},{lng}', # Center on the start of the current step
        'path': f'color:0xff0000ff|weight:5|enc:{polyline}', 
        'format': 'jpg-baseline',
        'key': Maps_API_KEY
    }

    markers = [
        f'markers=color:blue|label:S|{lat},{lng}', # Current step's start location
    ]
    # Add marker for user's current location if available and different from step start
    if current_route['origin']:
        try:
            user_lat, user_lng = map(float, current_route['origin'].split(','))
            # Only add user marker if it's reasonably different from the step marker
            if geodesic((user_lat, user_lng), (lat,lng)).meters > 5: # e.g. > 5 meters
                 markers.append(f'markers=color:green|label:U|{current_route["origin"]}')
        except ValueError:
            pass # Origin not parsable

    if current_route["destination"]:
         markers.append(f'markers=color:red|label:E|{current_route["destination"]}')


    query = '&'.join([f'{k}={quote_plus(str(v))}' for k, v in params.items()])
    marker_query = '&'.join(markers)
    full_url = f"{base_url}?{query}&{marker_query}"

    try:
        response = requests.get(full_url)
        response.raise_for_status()
        return send_file(io.BytesIO(response.content), mimetype='image/jpeg')
    except requests.exceptions.HTTPError as e:
        print(f"Failed to fetch map image (HTTPError): {e.response.status_code} - {e.response.text}")
        return f"Failed to fetch map image: {e.response.status_code}", 500
    except requests.exceptions.RequestException as e:
        print(f"Network error fetching map image: {e}")
        return "Network error fetching map image", 500


@app.route('/current_step')
def current_step():
    i = current_route['step_index']
    
    if not current_route['steps'] or i < 0 or i >= len(current_route['steps']):
        return jsonify({
            'error': 'No active route or navigation finished',
            'current_origin': current_route['origin'] or "Not set" # Still provide origin
            }), 404

    step_data = current_route['steps'][i]
    return jsonify({
        'step_index': i,
        'lat': step_data['lat'],
        'lng': step_data['lng'],
        'instruction': step_data['instruction'],
        'total_steps': len(current_route['steps']),
        'current_origin': current_route['origin'] # Ensure this is sent
    })

@app.route('/reset')
def reset():
    current_route['origin'] = None
    current_route['last_routed_origin'] = None
    current_route['destination'] = None
    current_route['steps'] = []
    current_route['step_index'] = 0
    current_route['polyline'] = ''
    print("Route has been reset.")
    return "Route reset successfully. <a href='/'>Start a new one</a>"

if __name__ == "__main__":
    # For production, use a WSGI server like Gunicorn or Waitress
    # Example: gunicorn --bind 0.0.0.0:8000 app:app (where app.py is your filename)
    app.run(debug=True, host='0.0.0.0', port=5000)
