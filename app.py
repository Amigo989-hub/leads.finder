from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from dotenv import load_dotenv
import openai
import os
import time
import requests


load_dotenv()

app = Flask(__name__)
CORS(app)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
NEARBY_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
SEARCH_RADIUS_METERS = 15000

CATEGORIES = {
    "barbershop": {"label": "Barbershop", "type": "barber_shop", "keyword": "Barbershop"},
    "restaurant": {"label": "Restaurant", "type": "restaurant", "keyword": "Restaurant"},
    "friseur": {"label": "Friseur", "type": "hair_care", "keyword": "Friseur"},
    "kosmetikstudio": {"label": "Kosmetikstudio", "type": "beauty_salon", "keyword": "Kosmetikstudio"},
    "doner": {"label": "Döner", "type": "restaurant", "keyword": "Döner"},
    "handwerker": {"label": "Handwerker", "type": "general_contractor", "keyword": "Handwerker"},
    "immobilienmakler": {"label": "Immobilienmakler", "type": "real_estate_agency", "keyword": "Immobilienmakler"},
    "physiotherapeut": {"label": "Physiotherapeut", "type": "physiotherapist", "keyword": "Physiotherapeut"},
}


def geocode_city(city_name: str, api_key: str):
    params = {
        "address": f"{city_name}, Germany",
        "key": api_key,
        "language": "de",
    }
    response = requests.get(GEOCODE_URL, params=params, timeout=20)
    data = response.json()

    if data.get("status") != "OK" or not data.get("results"):
        raise ValueError(f"Не удалось найти город. Geocoding status: {data.get('status')}")

    location = data["results"][0]["geometry"]["location"]
    return location["lat"], location["lng"]


def fetch_nearby_places(lat: float, lng: float, category: dict, api_key: str):
    params = {
        "location": f"{lat},{lng}",
        "radius": SEARCH_RADIUS_METERS,
        "keyword": category["keyword"],
        "type": category["type"],
        "key": api_key,
        "language": "de",
    }

    results = []
    while True:
        response = requests.get(NEARBY_SEARCH_URL, params=params, timeout=25)
        data = response.json()
        status = data.get("status")

        if status not in ("OK", "ZERO_RESULTS"):
            raise ValueError(f"Nearby Search ошибка: {status}")

        results.extend(data.get("results", []))

        next_page_token = data.get("next_page_token")
        if not next_page_token:
            break

        time.sleep(2)
        params = {"pagetoken": next_page_token, "key": api_key}

    return results


def fetch_place_details(place_id: str, api_key: str):
    params = {
        "place_id": place_id,
        "fields": "name,formatted_address,formatted_phone_number,website,url,rating,user_ratings_total",
        "key": api_key,
        "language": "de",
    }

    response = requests.get(PLACE_DETAILS_URL, params=params, timeout=25)
    data = response.json()
    if data.get("status") == "OK":
        return data.get("result", {})
    return {}


@app.get("/")
def index():
    return render_template("index.html", categories=CATEGORIES)


@app.post("/search")
def search():
    payload = request.get_json(silent=True) or {}
    city = (payload.get("city") or "").strip()
    category_id = (payload.get("category") or "").strip().lower()
    api_key_input = (payload.get("api_key") or "").strip()
    api_key = api_key_input or os.getenv("GOOGLE_API_KEY", "").strip()

    if not city:
        return jsonify({"error": "Укажи город"}), 400
    if category_id not in CATEGORIES:
        return jsonify({"error": "Выбери корректную категорию"}), 400
    if not api_key:
        return jsonify({"error": "Google API ключ не указан (форма или .env)"}), 400

    try:
        lat, lng = geocode_city(city, api_key)
        places = fetch_nearby_places(lat, lng, CATEGORIES[category_id], api_key)

        leads = []
        seen = set()
        for place in places:
            place_id = place.get("place_id")
            if not place_id or place_id in seen:
                continue
            seen.add(place_id)

            details = fetch_place_details(place_id, api_key)
            website = details.get("website")
            if website:
                continue

            leads.append(
                {
                    "name": details.get("name") or place.get("name", ""),
                    "address": details.get("formatted_address") or place.get("vicinity", ""),
                    "phone": details.get("formatted_phone_number", ""),
                    "rating": details.get("rating", ""),
                    "reviews_count": details.get("user_ratings_total", ""),
                    "google_maps_url": details.get("url")
                    or f"https://www.google.com/maps/place/?q=place_id:{place_id}",
                }
            )

        return jsonify({"city": city, "category": category_id, "count": len(leads), "leads": leads})
    except requests.RequestException:
        return jsonify({"error": "Ошибка сети при запросе к Google API"}), 502
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        return jsonify({"error": "Внутренняя ошибка сервера"}), 500


@app.post("/generate-email")
def generate_email():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    address = (payload.get("address") or "").strip()
    category = (payload.get("category") or "").strip()
    language = (payload.get("language") or "de").strip().lower()

    if not name:
        return jsonify({"error": "Название бизнеса не указано"}), 400
    if language not in ("de", "en"):
        return jsonify({"error": "Неверный язык. Используй de или en"}), 400

    if language == "de":
        prompt = (
            "Du bist ein professioneller Webdesigner. Schreibe eine kurze, "
            f"persoenliche Akquise-E-Mail auf Deutsch an {name} in {address}. "
            f"Das Unternehmen ist ein {category} und hat noch keine Website. "
            "Erklaere in 3-4 Saetzen warum eine Website wichtig ist und biete "
            "deine Hilfe an. Kein Spam-Ton, kein generischer Text. "
            "Nur der E-Mail-Text, ohne Betreff."
        )
    else:
        prompt = (
            "You are a professional web designer. Write a short personal "
            f"outreach email in English to {name} located at {address}. "
            f"They are a {category} business with no website. "
            "Explain in 3-4 sentences why a website matters and offer help. "
            "Keep it casual and genuine, not salesy. "
            "Just the email body, no subject line."
        )

    try:
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        email_text = response.choices[0].message.content.strip()
        return jsonify({"email_text": email_text})
    except Exception:
        return jsonify({"error": "Не удалось сгенерировать письмо"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
