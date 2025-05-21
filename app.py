from flask import Flask, request, jsonify, render_template_string, send_file
import requests
import io
from urllib.parse import quote_plus
import re
from geopy.distance import geodesic

app = Flask(__name__)

# --- Configuration ---
# Hardcoded Google Maps API key (for simplicity)
GOOGLE_MAPS_API_KEY = 'AIzaSyDZuZ1sMCSJSyC_u-rbzHC8BvbIyzAgL3M'
MAP_WIDTH = 480
MAP_HEIGHT = 360
MIN_REROUTE_METERS = 50
THRESHOLD_METERS_TO_ADVANCE_STEP = 30

current_route = {
    'origin': None,
    'last_routed_origin': None,
    'destination': None,
    'steps': [],
    'step_index': 0,
    'polyline': '',
    'destination_reached': False
}

def clean_html(raw_html):
    """Removes HTML tags from a string."""
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)

def update_route(origin_str, destination_str):
    directions_url = 'https://maps.googleapis.com/maps/api/directions/json '
    params = {
        'origin': origin_str,
        'destination': destination_str,
        'mode': 'driving',
        'key': GOOGLE_MAPS_API_KEY
    }

    try:
        response = requests.get(directions_url, params=params).json()
        if response['status'] != 'OK':
            print(f"Failed to fetch directions: {response['status']}")
            return False

        steps = []
        for leg in response['routes'][0]['legs']:
            for step in leg['steps']:
                start_loc = step['start_location']
                end_loc = step['end_location']
                instruction = clean_html(step['html_instructions'])
                steps.append({
                    'lat': start_loc['lat'],
                    'lng': start_loc['lng'],
                    'instruction': instruction,
                    'end_lat': end_loc['lat'],
                    'end_lng': end_loc['lng']
                })

        current_user_lat, current_user_lng = None, None
        if ',' in origin_str:
            try:
                current_user_lat, current_user_lng = map(float, origin_str.split(','))
            except ValueError:
                pass

        min_dist = float('inf')
        closest_step_index = 0

        if current_user_lat is not None and current_user_lng is not None:
            for i, step in enumerate(steps):
                step_coords = (step['lat'], step['lng'])
                dist = geodesic(step_coords, (current_user_lat, current_user_lng)).meters
                if dist < min_dist:
                    min_dist = dist
                    closest_step_index = i

        current_route.update({
            'steps': steps,
            'polyline': response['routes'][0]['overview_polyline']['points'],
            'step_index': closest_step_index,
            'destination_reached': False
        })

        if ',' in origin_str:
            try:
                lat_orig, lng_orig = map(float, origin_str.split(','))
                current_route['last_routed_origin'] = (lat_orig, lng_orig)
            except ValueError:
                current_route['last_routed_origin'] = None

        print(f"Route updated: {len(steps)} steps. Starting at step {closest_step_index}.")
        return True
    except Exception as e:
        print(f"Error updating route: {e}")
        return False

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        dest = request.form.get('destination')
        if not dest:
            return "Destination required", 400

        current_route['destination'] = dest
        if not current_route['origin']:
            return "Origin not set yet. Please wait for GPS fix.", 400

        success = update_route(current_route['origin'], dest)
        if not success:
            return "Failed to calculate route. Check destination or try again later.", 500

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
                    let currentStepIndex = 0;
                    let totalSteps = 0;
                    let routeActive = true;

                    function updateNavigationUI(stepIndex, instruction, totalStepsFromAPI, status) {
                        if (status === 'Destination reached') {
                            document.getElementById('current-step-instruction').innerText = "You have arrived!";
                            document.getElementById('step-counter').innerText = "";
                            document.getElementById('map-image').src = "";
                            routeActive = false;
                            return;
                        }
                        document.getElementById('current-step-instruction').innerText = instruction;
                        document.getElementById('step-counter').innerText = `Step ${stepIndex + 1} of ${totalStepsFromAPI}`;
                        document.getElementById('map-image').src = `/map/${stepIndex}?_=${new Date().getTime()}`;
                    }

                    function pollCurrentStep() {
                        if (!routeActive) return;
                        fetch('/current_step')
                            .then(res => {
                                if (res.status === 404) {
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
                                    updateNavigationUI(data.step_index, data.instruction, data.total_steps, data.status);
                                }
                            })
                            .catch(err => console.error("Error polling current step:", err));
                    }

                    window.onload = function() {
                        pollCurrentStep();
                        setInterval(pollCurrentStep, 2000);
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

    return render_template_string('''
        <!doctype html>
        <html>
        <head>
            <title>Start Live Navigation</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <script>
                let lastKnownAccurateLocation = null;

                function sendLocation(lat, lng, accuracy, method) {
                    fetch('/update_location', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ lat: lat, lng: lng, accuracy: accuracy, method: method })
                    }).then(res => res.json())
                      .then(data => console.log("Server response:", data))
                      .catch(err => console.error("Send error:", err));
                }

                function getFallbackLocation() {
                    fetch('/get_fallback_location')
                        .then(res => res.json())
                        .then(data => {
                            if (data.lat && data.lng) {
                                const MIN_ACCURACY = 1000;
                                if (data.accuracy < MIN_ACCURACY) {
                                    if (!lastKnownAccurateLocation || data.accuracy < lastKnownAccurateLocation.accuracy) {
                                        sendLocation(data.lat, data.lng, data.accuracy, "google_api");
                                        lastKnownAccurateLocation = { lat: data.lat, lng: data.lng, accuracy: data.accuracy };
                                    }
                                }
                            }
                        });
                }

                function startGettingLocation() {
                    if (!navigator.geolocation) {
                        alert("Geolocation not supported.");
                        getFallbackLocation();
                        return;
                    }

                    navigator.geolocation.watchPosition(pos => {
                        const lat = pos.coords.latitude;
                        const lng = pos.coords.longitude;
                        const accuracy = pos.coords.accuracy;

                        const MIN_MOVE_DISTANCE = 10;
                        const MAX_ACCURACY = 500;

                        let shouldSend = false;

                        if (!lastKnownAccurateLocation) {
                            shouldSend = true;
                        } else {
                            const distanceMoved = geodesic([lastKnownAccurateLocation.lat, lastKnownAccurateLocation.lng], [lat, lng]).meters;
                            if (accuracy < lastKnownAccurateLocation.accuracy * 0.8) {
                                shouldSend = true;
                            } else if (distanceMoved > MIN_MOVE_DISTANCE && accuracy < MAX_ACCURACY) {
                                shouldSend = true;
                            }
                        }

                        if (shouldSend) {
                            sendLocation(lat, lng, accuracy, "browser_gps_watch");
                            lastKnownAccurateLocation = { lat, lng, accuracy };
                        }

                        if (accuracy > 50) getFallbackLocation();

                    }, err => {
                        console.error("GPS error:", err);
                        getFallbackLocation();
                    }, {
                        enableHighAccuracy: true,
                        timeout: 15000,
                        maximumAge: 0
                    });

                    setInterval(getFallbackLocation, 30000);
                }

                function geodesic(coords1, coords2) {
                    const R = 6371e3;
                    const φ1 = coords1[0] * Math.PI / 180;
                    const φ2 = coords2[0] * Math.PI / 180;
                    const Δφ = (coords2[0]-coords1[0]) * Math.PI / 180;
                    const Δλ = (coords2[1]-coords1[1]) * Math.PI / 180;
                    const a = Math.sin(Δφ/2)*Math.sin(Δφ/2) +
                              Math.cos(φ1)*Math.cos(φ2)*Math.sin(Δλ/2)*Math.sin(Δλ/2);
                    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
                    return { meters: R * c };
                }

                window.onload = startGettingLocation;
            </script>
        </head>
        <body>
            <h2>Start Live Navigation</h2>
            <form method="POST">
              <p>Your location will be detected automatically.</p>
              Destination: <input name="destination" required><br><br>
              <input type="submit" value="Start Navigation">
            </form>
        </body>
        </html>
    ''')

@app.route('/update_location', methods=['POST'])
def update_location():
    data = request.get_json()
    lat = data.get('lat')
    lng = data.get('lng')
    accuracy = data.get('accuracy')
    method = data.get('method')

    if lat is None or lng is None:
        return jsonify({'error': 'Invalid data'}), 400

    new_origin_str = f"{lat},{lng}"
    current_route['origin'] = new_origin_str
    print(f"[{method.upper()}] Location: {new_origin_str} (Accuracy: {accuracy}m)")

    if current_route['steps'] and current_route['step_index'] < len(current_route['steps']):
        current_step = current_route['steps'][current_route['step_index']]
        end_coords = (current_step['end_lat'], current_step['end_lng'])
        user_coords = (lat, lng)
        distance = geodesic(end_coords, user_coords).meters

        if distance < THRESHOLD_METERS_TO_ADVANCE_STEP:
            if current_route['step_index'] < len(current_route['steps']) - 1:
                current_route['step_index'] += 1
                print(f"Advanced to step {current_route['step_index']} | Distance: {distance:.2f}m")
            else:
                current_route['destination_reached'] = True
                print("Destination reached!")

    if current_route['destination'] and not current_route['destination_reached']:
        moved_distance = 0
        should_reroute = False

        if current_route['last_routed_origin'] is None:
            should_reroute = True
        else:
            last_lat, last_lng = current_route['last_routed_origin']
            moved_distance = geodesic((last_lat, last_lng), (lat, lng)).meters
            if moved_distance > MIN_REROUTE_METERS:
                should_reroute = True

        if should_reroute:
            if update_route(new_origin_str, current_route['destination']):
                print("Route updated dynamically.")
            else:
                print("Route update failed.")

    return jsonify({'status': 'Location updated'}), 200

@app.route('/get_fallback_location')
def get_fallback_location():
    url = f"https://www.googleapis.com/geolocation/v1/geolocate?key= {GOOGLE_MAPS_API_KEY}"
    try:
        res = requests.post(url, json={"considerIp": True})
        if res.status_code == 200:
            data = res.json()
            return jsonify({
                'lat': data['location']['lat'],
                'lng': data['location']['lng'],
                'accuracy': data['accuracy']
            })
        return jsonify({'error': 'Google API error'}), 500
    except Exception as e:
        print(e)
        return jsonify({'error': 'Network error'}), 500

@app.route('/map/<int:step_index>')
def step_map(step_index):
    if current_route.get('destination_reached'):
        return send_file(io.BytesIO(b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x00\x44\x01\x00\x3b'), mimetype='image/gif')

    if not current_route['steps'] or step_index < 0 or step_index >= len(current_route['steps']):
        return "No active route", 404

    loc = current_route['steps'][step_index]
    lat, lng = loc['lat'], loc['lng']
    polyline = current_route['polyline']

    base_url = "https://maps.googleapis.com/maps/api/staticmap "
    params = {
        'size': f'{MAP_WIDTH}x{MAP_HEIGHT}',
        'zoom': '17',
        'center': f'{lat},{lng}',
        'path': f'color:0xff0000ff|weight:5|enc:{polyline}',
        'format': 'jpg-baseline',
        'key': GOOGLE_MAPS_API_KEY
    }

    markers = [f'markers=color:blue|label:{step_index+1}|{lat},{lng}']
    if current_route['destination']:
        markers.append(f'markers=color:red|label:E|{current_route["destination"]}')

    query = '&'.join([f'{k}={quote_plus(str(v))}' for k, v in params.items()])
    marker_query = '&'.join(markers)
    full_url = f"{base_url}?{query}&{marker_query}"

    try:
        res = requests.get(full_url)
        if res.status_code == 200:
            return send_file(io.BytesIO(res.content), mimetype='image/jpeg')
        return "Map fetch failed", 500
    except Exception as e:
        print(e)
        return "Map fetch error", 500

@app.route('/current_step')
def current_step():
    if current_route.get('destination_reached'):
        return jsonify({
            'status': 'Destination reached',
            'instruction': 'You have arrived!',
            'step_index': len(current_route['steps']) - 1,
            'total_steps': len(current_route['steps'])
        })

    i = current_route['step_index']
    if not current_route['steps'] or i < 0 or i >= len(current_route['steps']):
        return jsonify({'error': 'No active route'}), 404

    step_data = current_route['steps'][i]
    return jsonify({
        'step_index': i,
        'lat': step_data['lat'],
        'lng': step_data['lng'],
        'instruction': step_data['instruction'],
        'total_steps': len(current_route['steps']),
        'status': 'Navigating'
    })

@app.route('/reset')
def reset():
    current_route.update({
        'origin': None,
        'last_routed_origin': None,
        'destination': None,
        'steps': [],
        'step_index': 0,
        'polyline': '',
        'destination_reached': False
    })
    return "Route reset. <a href='/'>New route</a>"

if __name__ == "__main__":
    app.run(debug=True)
