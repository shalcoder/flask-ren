# WORKING PRINCIPLE:
# This Flask app fetches live GPS coordinates from the client's browser, queries the Google Directions API
# every 5 seconds, and updates route details like origin, steps, instructions, and a dynamic map image.
# Users can enter a destination, initiate navigation, and replan routes. ESP32 will pull the latest map
# image from a specified static URL endpoint.

# CLOUD PLATFORM USED: Google Maps API for Directions and Static Maps

from flask import Flask, request, render_template, jsonify
import requests
import os
import threading
import time

app = Flask(__name__)

# Store route info
data_store = {
    "origin": None,
    "destination": None,
    "steps": [],
    "instructions": [],
    "map_url": None
}

GOOGLE_MAPS_API_KEY = 'AIzaSyDZuZ1sMCSJSyC_u-rbzHC8BvbIyzAgL3M' 


def fetch_route():
    while True:
        if data_store["origin"] and data_store["destination"]:
            origin = data_store["origin"]
            destination = data_store["destination"]

            directions_url = (
                f"https://maps.googleapis.com/maps/api/directions/json?origin={origin}"
                f"&destination={destination}&key={GOOGLE_MAPS_API_KEY}"
            )
            response = requests.get(directions_url)
            if response.status_code == 200:
                directions = response.json()
                try:
                    leg = directions['routes'][0]['legs'][0]
                    steps = leg['steps']
                    data_store['steps'] = [step['html_instructions'] for step in steps]
                    data_store['instructions'] = [
                        f"From {step['start_location']['lat']},{step['start_location']['lng']} to "
                        f"{step['end_location']['lat']},{step['end_location']['lng']}"
                        for step in steps
                    ]
                    # Static map URL
                    data_store['map_url'] = (
                        f"https://maps.googleapis.com/maps/api/staticmap?size=600x400&path=color:red|weight:5|"
                        f"{origin}|{destination}&key={GOOGLE_MAPS_API_KEY}"
                    )
                except Exception as e:
                    print("Failed parsing directions:", e)
        time.sleep(5)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/start_navigation', methods=['POST'])
def start_navigation():
    content = request.json
    data_store['origin'] = content.get('origin')
    data_store['destination'] = content.get('destination')
    return jsonify({"status": "started"})


@app.route('/current_route')
def current_route():
    return jsonify({
        "origin": data_store['origin'],
        "destination": data_store['destination'],
        "steps": data_store['steps'],
        "instructions": data_store['instructions'],
        "map_url": data_store['map_url']
    })


@app.route('/esp32_map')
def esp32_map():
    # This returns only the static map image URL to ESP32
    return jsonify({"map_url": data_store['map_url']})


if __name__ == '__main__':
    # Start background route updater thread
    threading.Thread(target=fetch_route, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True)
