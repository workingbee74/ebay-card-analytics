import os
import hashlib
import base64
import requests
import psycopg
import re
from flask import Flask, request, jsonify

app = Flask(__name__)
VERIFICATION_TOKEN = os.environ.get("EBAY_VERIFICATION_TOKEN", "")
ENDPOINT_URL = os.environ.get("EBAY_ENDPOINT_URL", "")
EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
def parse_card_title(title):
    title_upper = title.upper()

    # Year
    year_match = re.search(r"\b(19|20)\d{2}\b", title)
    card_year = int(year_match.group()) if year_match else None
    # Manufacturer
    manufacturer = None
    if "BOWMAN" in title_upper:
        manufacturer = "Bowman"
    elif "TOPPS" in title_upper:
        manufacturer = "Topps"
    elif "PANINI" in title_upper:
        manufacturer = "Panini"
    # Product
    product = None
    if "BOWMAN STERLING" in title_upper:
     product = "Bowman Sterling"
    elif "BOWMAN CHROME" in title_upper:
     product = "Bowman Chrome"
    elif "BOWMAN DRAFT" in title_upper:
     product = "Bowman Draft"
    elif "TOPPS CHROME" in title_upper:
     product = "Topps Chrome"
    elif "BOWMAN" in title_upper:
     product = "Bowman"
    elif "TOPPS" in title_upper:
     product = "Topps"
       # Player name
    player_name = None

    # Common pattern:
    # "2026 Bowman Chrome Kevin McGonigle Rookie..."
      # Player name
    player_name = None

    # Remove obvious non-name phrases first
    cleaned_title = re.sub(
        r"\b(?:BOWMAN|TOPPS|CHROME|DRAFT|BASEBALL|CARD|CARDS|"
        r"ROOKIE|RC|PROSPECT|1ST|AUTO|AUTOGRAPH|REFRACTOR|MOJO|"
        r"SILVER|GOLD|ORANGE|RED|BLUE|GREEN|PURPLE|AQUA|LIGHTNING|"
        r"MEGA|HOBBY|BOX|INSERT|PARALLEL|PARALLELS|SIGNED|RARE|HOT)\b",
        " ",
        title,
        flags=re.IGNORECASE
    )

    # Remove years, card numbers, serial-like fragments, prices, and punctuation noise
    cleaned_title = re.sub(r"\b(?:19|20)\d{2}\b", " ", cleaned_title)
    cleaned_title = re.sub(r"#[A-Za-z0-9-]+", " ", cleaned_title)
    cleaned_title = re.sub(r"\b\d+\s*/\s*\d+\b", " ", cleaned_title)
    cleaned_title = re.sub(r"\$\d+(?:\.\d+)?", " ", cleaned_title)
    cleaned_title = re.sub(r"[^A-Za-z.' -]", " ", cleaned_title)
    cleaned_title = re.sub(r"\s+", " ", cleaned_title).strip()

    # Look for plausible 2- or 3-word person names
    name_match = re.search(
        r"\b([A-Za-z][A-Za-z.'-]+(?:\s+[A-Za-z][A-Za-z.'-]+){1,2})\b",
        cleaned_title
    )

    if name_match:
        candidate = name_match.group(1).strip()

        bad_name_words = {
            "PICK", "YOUR", "COMPLETE", "SET", "FREE", "SHIPPING",
            "MINIMUM", "PRESALE", "INVESTMENT", "MINT", "GEM",
            "SINGLES", "SEALED", "NEW", "QTY"
        }

        candidate_words = set(candidate.upper().split())

        if not candidate_words.intersection(bad_name_words):
            player_name = candidate.title()
    # Parallel
    parallel = None

    parallel_patterns = [
        ("SUPERFRACTOR", "Superfractor"),
        ("RED REFRACTOR", "Red Refractor"),
        ("ORANGE REFRACTOR", "Orange Refractor"),
        ("GOLD REFRACTOR", "Gold Refractor"),
        ("GREEN REFRACTOR", "Green Refractor"),
        ("BLUE REFRACTOR", "Blue Refractor"),
        ("PURPLE REFRACTOR", "Purple Refractor"),
        ("AQUA REFRACTOR", "Aqua Refractor"),
        ("SILVER REFRACTOR", "Silver Refractor"),
        ("MOJO REFRACTOR", "Mojo Refractor"),
        ("MOJO", "Mojo Refractor"),
        ("ROSE GOLD", "Rose Gold"),
        ("FUCHSIA", "Fuchsia"),
        ("LUNAR GLOW", "Lunar Glow"),
        ("RAYWAVE", "RayWave"),
        ("RAY WAVE", "RayWave"),
        ("REFRACTOR", "Refractor"),
        ("SHIMMER", "Shimmer"),
        ("WAVE", "Wave"),
        ("SAPPHIRE", "Sapphire"),
        ("SPECKLE", "Speckle"),
    ]

    for pattern, name in parallel_patterns:
        if pattern in title_upper:
            parallel = name
            break
    # Card number, e.g. #BCP-164, #BD-76, #BST-1
    card_number = None

    card_number_match = re.search(
        r"#([A-Z]{1,5}-?\d{1,4})\b",
        title_upper
    )

    if card_number_match:
        card_number = card_number_match.group(1)
    # Serial numbering, e.g. /50, /25, /5, 1/1
    serial_numbered_to = None

    serial_match = re.search(r"(?<!\d)/(\d{1,4})\b", title_upper)
    if serial_match:
        serial_numbered_to = int(serial_match.group(1))

    if re.search(r"\b1\s*/\s*1\b", title_upper):
        serial_numbered_to = 1
    # Autograph
    autograph = bool(
        re.search(r"\b(AUTO|AUTOGRAPH|AUTOGRAPHED)\b", title_upper)
    )

    # Rookie card
    rookie_card = bool(
        re.search(r"\b(RC|ROOKIE)\b", title_upper)
    )

    # Grading company
    grade_company = None
    for company in ["PSA", "BGS", "SGC", "CGC"]:
        if re.search(rf"\b{company}\b", title_upper):
            grade_company = company
            break

    # Grade
    grade = None
    if grade_company:
        grade_match = re.search(
            rf"\b{grade_company}\s*(\d+(?:\.\d+)?)\b",
            title_upper
        )
        if grade_match:
            grade = float(grade_match.group(1))

    # Identify listings that are NOT individual cards
    exclusion_terms = [
    "YOU PICK",
    "PICK YOUR CARD",
    "CHOOSE YOUR CARD",
    "HOBBY BOX",
    "HOBBY CASE",
    "BLASTER BOX",
    "MEGA BOX",
    "SEALED BOX",
    "2 CARD MIN",
    "2 CARD MINIMUM",
    "CARD MIN",
    "MINIMUM ORDER",
    "CARD LOT",
    "LOT OF",
]

    is_single_card = not any(
        term in title_upper for term in exclusion_terms
    )
    # Catch variable minimum-order wording:
    # "6 card minimum", "2 CARD or $1.50 MINIMUM", "$2 Minimum Order"
    if re.search(r"\b\d+\s*CARD(?:S)?\s+(?:OR\s+\$?\d+(?:\.\d+)?\s+)?MINIMUM\b", title_upper):
        is_single_card = False

    if re.search(r"\$\d+(?:\.\d+)?\s+MINIMUM\b", title_upper):
        is_single_card = False
    if not is_single_card:
        player_name = None
    return {
        "card_year": card_year,
        "player_name": player_name,
        "manufacturer": manufacturer,
        "product": product,
        "parallel": parallel,
        "card_number": card_number,
        "serial_numbered_to": serial_numbered_to,
        "autograph": autograph,
        "rookie_card": rookie_card,
        "grade_company": grade_company,
        "grade": grade,
        "is_single_card": is_single_card,
    }

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
    items = []

    queries = [
        "Bowman Chrome baseball card",
        "Bowman Draft baseball card",
        "Bowman Sterling baseball card",
        "Topps Chrome baseball card",
    ]

    for query in queries:
        for offset in (0, 200, 400):
            search_response = requests.get(
                "https://api.ebay.com/buy/browse/v1/item_summary/search",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                },
                params={
                    "q": query,
                    "limit": 200,
                    "offset": offset,
                },
                timeout=20,
            )

            if search_response.status_code != 200:
                return jsonify({
                    "success": False,
                    "error": search_response.text
                }), search_response.status_code

            data = search_response.json()
            page_items = data.get("itemSummaries", [])
            items.extend(page_items)

            if len(page_items) < 200:
                break
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
                        title = item.get("title", "")
                        card_data = parse_card_title(title)
                                # Match title against known players in the players table
                        cur.execute("""
                            SELECT player_name
                            FROM players
                            WHERE %s ILIKE '%%' || player_name || '%%'
                            ORDER BY LENGTH(player_name) DESC
                            LIMIT 1
                        """, (title,))
                
                        player_row = cur.fetchone()
        
                        if player_row:
                            card_data["player_name"] = player_row[0]
                        else:
                            card_data["player_name"] = None
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
                        card_year,
                        player_name,
                        manufacturer,
                        product,
                        parallel,
                        card_number,
                        serial_numbered_to,
                        autograph,
                        rookie_card,
                        grade_company,
                        grade,
                        is_single_card,
                        date_collected
                    )
                    VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
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
                                card_year = EXCLUDED.card_year,
                                player_name = EXCLUDED.player_name,
                                manufacturer = EXCLUDED.manufacturer,
                                product = EXCLUDED.product,
                                parallel = EXCLUDED.parallel,
                                card_number = EXCLUDED.card_number,
                                serial_numbered_to = EXCLUDED.serial_numbered_to,
                                autograph = EXCLUDED.autograph,
                                rookie_card = EXCLUDED.rookie_card,
                                grade_company = EXCLUDED.grade_company,
                                grade = EXCLUDED.grade,
                                is_single_card = EXCLUDED.is_single_card,
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
                    card_data["card_year"],
                    card_data["player_name"],
                    card_data["manufacturer"],
                    card_data["product"],
                    card_data["parallel"],
                    card_data["card_number"],
                    card_data["serial_numbered_to"],
                    card_data["autograph"],
                    card_data["rookie_card"],
                    card_data["grade_company"],
                    card_data["grade"],
                    card_data["is_single_card"],
        
                ))
    return jsonify({

        "success": True,

        "listings_received": len(items),

        "listings_saved": len(items)

    }), 200
@app.route("/valuation", methods=["GET"])
def valuation():
    player = request.args.get("player", "").strip()

    if not player:
        return jsonify({
            "success": False,
            "error": "Missing player parameter"
        }), 400

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    player_name,
                    card_year,
                    product,
                    parallel,
                    COUNT(*) AS listing_count,
                    ROUND(MIN(asking_price), 2) AS low_price,
                    ROUND(
                        PERCENTILE_CONT(0.5)
                        WITHIN GROUP (ORDER BY asking_price)::numeric,
                        2
                    ) AS median_price,
                    ROUND(AVG(asking_price), 2) AS average_price,
                    ROUND(MAX(asking_price), 2) AS high_price
                FROM ebay_listings
                WHERE
                    is_single_card = TRUE
                    AND player_name ILIKE %s
                    AND asking_price IS NOT NULL
                GROUP BY
                    player_name,
                    card_year,
                    product,
                    parallel
                ORDER BY
                    listing_count DESC,
                    card_year DESC,
                    product,
                    parallel;
            """, (player,))

            rows = cur.fetchall()

    results = []

    for row in rows:
        low_price = float(row[5]) if row[5] is not None else None
        median_price = float(row[6]) if row[6] is not None else None

        spread_percentage = None

        if low_price is not None and median_price and median_price > 0:
            spread_percentage = round(
                ((median_price - low_price) / median_price) * 100,
                1
            )

        deal_rating = None

        if row[4] >= 2 and spread_percentage is not None:
            if spread_percentage >= 20:
                deal_rating = "BUY"
            elif spread_percentage >= 10:
                deal_rating = "FAIR"
            else:
                deal_rating = "HIGH"
        if low_price is not None and median_price and median_price > 0:
            spread_percentage = round(
                ((median_price - low_price) / median_price) * 100,
                1
            )
        results.append({
            "player_name": row[0],
            "card_year": row[1],
            "product": row[2],
            "parallel": row[3],
            "listing_count": row[4],
            "low_price": low_price,
            "median_price": median_price,
            "spread_percentage": spread_percentage,
            "deal_rating": deal_rating,
            "average_price": float(row[7]) if row[7] is not None else None,
            "high_price": float(row[8]) if row[8] is not None else None,
        })

    return jsonify({
        "success": True,
        "player": player,
        "comparables": results
    }), 200
@app.route("/deals", methods=["GET"])
def deals():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH comparable_stats AS (
                    SELECT
                        player_name,
                        card_year,
                        product,
                        parallel,
                        card_number,
                        autograph,
                        COUNT(*) AS listing_count,
                        PERCENTILE_CONT(0.5)
                            WITHIN GROUP (
                                ORDER BY asking_price + COALESCE(shipping_cost, 0)
                            ) AS median_price
                    FROM ebay_listings
                    WHERE
                        is_single_card = TRUE
                        AND player_name IS NOT NULL
                        AND card_number IS NOT NULL
                        AND asking_price IS NOT NULL
                    GROUP BY
                        player_name,
                        card_year,
                        product,
                        parallel,
                        card_number,
                        autograph
                    HAVING COUNT(*) >= 3
                )
                SELECT
                    e.title,
                    e.player_name,
                    e.card_year,
                    e.product,
                    e.parallel,
                    e.card_number,
                    e.asking_price,
                    e.shipping_cost,
                    e.listing_url,
                    e.seller_name,
                    c.listing_count,
                    c.median_price,
                    ROUND(
                        (
                            (
                                (c.median_price - (e.asking_price + COALESCE(e.shipping_cost, 0)))
                                / NULLIF(c.median_price, 0)
                            ) * 100
                        )::numeric,
                        1
                    ) AS discount_percentage
                FROM ebay_listings e
                JOIN comparable_stats c
                    ON e.player_name = c.player_name
                    AND e.card_year IS NOT DISTINCT FROM c.card_year
                    AND e.product IS NOT DISTINCT FROM c.product
                    AND e.parallel IS NOT DISTINCT FROM c.parallel
                    AND e.card_number IS NOT DISTINCT FROM c.card_number
                    AND e.autograph IS NOT DISTINCT FROM c.autograph
                WHERE
                    e.is_single_card = TRUE
                    AND e.asking_price IS NOT NULL
                    AND (e.asking_price + COALESCE(e.shipping_cost, 0)) < c.median_price
                ORDER BY
                    discount_percentage DESC,
                    c.listing_count DESC
                LIMIT 50;
            """)

            rows = cur.fetchall()

    results = []

    for row in rows:
        discount_percentage = float(row[12])

        if discount_percentage >= 20:
            deal_rating = "BUY"
        elif discount_percentage >= 10:
            deal_rating = "FAIR"
        else:
            deal_rating = "HIGH"

        results.append({
    "title": row[0],
    "player_name": row[1],
    "card_year": row[2],
    "product": row[3],
    "parallel": row[4],
    "card_number": row[5],
    "asking_price": float(row[6]) if row[6] is not None else None,
    "shipping_cost": float(row[7]) if row[7] is not None else None,
    "total_cost": (
        float(row[6]) + (float(row[7]) if row[7] is not None else 0)
    ),
    "listing_url": row[8],
    "seller_name": row[9],
    "comparable_count": row[10],
    "median_price": float(row[11]) if row[11] is not None else None,
    "discount_percentage": discount_percentage,
    "deal_rating": deal_rating,
})

    return jsonify({
        "success": True,
        "deal_count": len(results),
        "deals": results
    }), 200
