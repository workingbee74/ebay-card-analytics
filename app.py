import os
import hashlib
import base64
import requests
import psycopg
from flask import Flask, request, jsonify

app = Flask(__name__)
VERIFICATION_TOKEN = os.environ.get("EBAY_VERIFICATION_TOKEN", "")
ENDPOINT_URL = os.environ.get("EBAY_ENDPOINT_URL", "")
EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
@app.route("/", methods=["GET"])
def home():
    return "eBay notification endpoint is running", 200
@app.route("/db-test", methods=["GET"])
def db_test():
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                result = cur.fetchone()
        return jsonify({
            "success": True,
            "database": "connected",
            "result": result[0]
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
@app.route("/ebay/account-deletion", methods=["GET", "POST"])
def ebay_account_deletion():
    if request.method == "GET":
        challenge_code = request.args.get("challenge_code")
        if not challenge_code:
            return jsonify({"error": "Missing challenge_code"}), 400
        challenge_response = hashlib.sha256(
            (
                challenge_code
                + VERIFICATION_TOKEN
                + ENDPOINT_URL
            ).encode("utf-8")
        ).hexdigest()
        return jsonify({"challengeResponse": challenge_response}), 200
    # eBay sends account-deletion notifications here by POST
    return "", 204

@app.route("/ebay/test-auth", methods=["GET"])

def ebay_test_auth():

    credentials = f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}"

    encoded_credentials = base64.b64encode(

        credentials.encode("utf-8")

    ).decode("utf-8")
    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=20,
    )
    if response.status_code == 200:
        return jsonify({
            "success": True,

            "message": "eBay authentication successful"
        }), 200
    return jsonify({
        "success": False,
        "status_code": response.status_code,
        "error": response.text
    }), response.status_code

@app.route("/ebay/search", methods=["GET"])
def ebay_search():
    credentials = f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(
        credentials.encode("utf-8")
    ).decode("utf-8")
    token_response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=20,
    )
    if token_response.status_code != 200:
        return jsonify({
            "success": False,
            "error": token_response.text
        }), token_response.status_code
    access_token = token_response.json()["access_token"]
    search_response = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        },
        params={
            "q": request.args.get("q", "Bowman Chrome baseball card"),
            "limit": 50,
        },
        timeout=20,
    )
    if search_response.status_code != 200:
        return jsonify({
            "success": False,
            "error": search_response.text
        }), search_response.status_code
    data = search_response.json()
    items = data.get("itemSummaries", [])
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ebay_listings (
                    id BIGSERIAL PRIMARY KEY,
                    ebay_item_id TEXT UNIQUE,
                    title TEXT,
                    asking_price NUMERIC(12,2),
                    seller_name TEXT,
                    listing_url TEXT,
                    listing_type TEXT,
                    date_collected TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
    cur.execute("""
    ALTER TABLE ebay_listings
        ADD COLUMN IF NOT EXISTS listing_type TEXT,
        ADD COLUMN IF NOT EXISTS condition TEXT,
        ADD COLUMN IF NOT EXISTS shipping_cost NUMERIC(12,2),
        ADD COLUMN IF NOT EXISTS seller_feedback_percentage NUMERIC(6,3),
        ADD COLUMN IF NOT EXISTS seller_feedback_score BIGINT,
        ADD COLUMN IF NOT EXISTS image_url TEXT,
        ADD COLUMN IF NOT EXISTS category_id TEXT,
        ADD COLUMN IF NOT EXISTS item_end_date TIMESTAMP,
        ADD COLUMN IF NOT EXISTS currency TEXT;
""")

    for item in items:

        listing_type = item.get("buyingOptions", ["UNKNOWN"])[0]

        price = item.get("price", {}).get("value")

        condition = item.get("condition")
        
        shipping_options = item.get("shippingOptions", [])
        shipping_cost = None
        if shipping_options:
            shipping_cost = shipping_options[0].get("shippingCost", {}).get("value")
        
        seller_info = item.get("seller", {})
        seller = seller_info.get("username")
        seller_feedback_percentage = seller_info.get("feedbackPercentage")
        seller_feedback_score = seller_info.get("feedbackScore")
        
        image_url = item.get("image", {}).get("imageUrl")
        
        category_ids = item.get("leafCategoryIds", [])
        category_id = category_ids[0] if category_ids else None
        
        item_end_date = item.get("itemEndDate")
        
        currency = item.get("price", {}).get("currency")



        cur.execute("""

              INSERT INTO ebay_listings (
                ebay_item_id,
                title,
                asking_price,
                seller_name,
                listing_url,
                listing_type,
                condition,
                shipping_cost,
                seller_feedback_percentage,
                seller_feedback_score,
                image_url,
                category_id,
                item_end_date,
                currency,
                date_collected
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                CURRENT_TIMESTAMP
            )

              ON CONFLICT (ebay_item_id)
            DO UPDATE SET
                title = EXCLUDED.title,
                asking_price = EXCLUDED.asking_price,
                seller_name = EXCLUDED.seller_name,
                listing_url = EXCLUDED.listing_url,
                listing_type = EXCLUDED.listing_type,
                condition = EXCLUDED.condition,
                shipping_cost = EXCLUDED.shipping_cost,
                seller_feedback_percentage = EXCLUDED.seller_feedback_percentage,
                seller_feedback_score = EXCLUDED.seller_feedback_score,
                image_url = EXCLUDED.image_url,
                category_id = EXCLUDED.category_id,
                item_end_date = EXCLUDED.item_end_date,
                currency = EXCLUDED.currency,
                date_collected = CURRENT_TIMESTAMP;
        """, (

    item.get("itemId"),
    item.get("title"),
    price,
    seller,
    item.get("itemWebUrl"),
    listing_type,
    condition,
    shipping_cost,
    seller_feedback_percentage,
    seller_feedback_score,
    image_url,
    category_id,
    item_end_date,
    currency,


        ))



    return jsonify({

        "success": True,

        "listings_received": len(items),

        "listings_saved": len(items)

    }), 200
