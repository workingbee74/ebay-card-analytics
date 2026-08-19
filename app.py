import os
import hashlib
import base64
import requests
import psycopg
import re
import unicodedata
import base64
from flask import Flask, request, jsonify
from cardsightai import CardSightAI

app = Flask(__name__)
VERIFICATION_TOKEN = os.environ.get("EBAY_VERIFICATION_TOKEN", "")
ENDPOINT_URL = os.environ.get("EBAY_ENDPOINT_URL", "")
EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SOLDCOMPS_API_KEY = os.environ.get("SOLDCOMPS_API_KEY", "")
CARDSIGHT_API_KEY = os.environ.get("CARDSIGHT_API_KEY", "")
XIMILAR_API_TOKEN = os.environ.get("XIMILAR_API_TOKEN", "")

def parse_card_title(title):
    title_upper = title.upper()

    # First Bowman
    first_bowman = bool(
        re.search(
            r"\b1ST\b.*\bBOWMAN\b|\bBOWMAN\b.*\b1ST\b",
            title_upper
        )
    )

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

    cleaned_title = re.sub(
        r"\b(?:"
        r"BOWMAN|TOPPS|CHROME|DRAFT|BASEBALL|CARD|CARDS|"
        r"ROOKIE|RC|PROSPECT|1ST|AUTO|AUTOGRAPH|AUTOGRAPHED|"
        r"PSA|BGS|SGC|CGC|BCCG|GEM|MINT|GRADED|"
        r"REFRACTOR|MOJO|SAPPHIRE|SPECKLE|LAVA|SHIMMER|"
        r"X-FRACTOR|X\s+FRACTOR|RAYWAVE|RAY\s+WAVE|"
        r"SILVER|GOLD|ROSE|ORANGE|RED|BLUE|GREEN|PURPLE|AQUA|"
        r"FUCHSIA|LIGHTNING|MEGA|HOBBY|BOX|INSERT|PARALLEL|"
        r"PARALLELS|SIGNED|RARE|HOT"
        r")\b",
        " ",
        title,
        flags=re.IGNORECASE
    )

    cleaned_title = re.sub(r"\b(19|20)\d{2}\b", " ", cleaned_title)
    cleaned_title = re.sub(r"#?[A-Z]{2,5}-[A-Z0-9]{1,6}", " ", cleaned_title)
    cleaned_title = re.sub(r"\b\d+\s*/\s*\d+\b", " ", cleaned_title)
    cleaned_title = re.sub(r"\$\d+(?:\.\d+)?", " ", cleaned_title)
    cleaned_title = re.sub(r"[^A-Za-z.' -]", " ", cleaned_title)
    cleaned_title = re.sub(r"\s+", " ", cleaned_title).strip()

    name_match = re.search(
        r"\b([A-Z][a-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,2})\b",
        cleaned_title
    )

    if name_match:
        candidate = name_match.group(1).strip()

        bad_name_words = {
            "PICK", "YOUR", "COMPLETE", "SET", "FREE", "SHIPPING",
            "MINIMUM", "PRESALE", "INVESTMENT", "MINT", "GEM",
            "SINGLES", "SEALED", "NEW", "QTY",
            "PSA", "BGS", "SGC", "CGC", "BCCG",
            "SAPPHIRE", "SPECKLE", "REFRACTOR", "MOJO",
            "LAVA", "GOLD", "ROSE", "AQUA", "ORANGE",
            "GREEN", "BLUE", "RED", "PURPLE"
        }

        candidate_words = set(candidate.upper().split())

        if not candidate_words.intersection(bad_name_words):
            player_name = candidate.title()

    # Parallel
    parallel = None

    parallel_patterns = [
        ("ORANGE SAPPHIRE", "Orange Sapphire"),
        ("AQUA X-FRACTOR", "Aqua X-Fractor"),
        ("AQUA X FRACTOR", "Aqua X-Fractor"),
        ("ROSE GOLD REFRACTOR", "Rose Gold Refractor"),
        ("ROSE GOLD", "Rose Gold"),
        ("LAVA REFRACTOR", "Lava Refractor"),
        ("MOJO REFRACTOR", "Mojo Refractor"),
        ("SPECKLE REFRACTOR", "Speckle Refractor"),
        ("SUPERFRACTOR", "Superfractor"),
        ("RED REFRACTOR", "Red Refractor"),
        ("ORANGE REFRACTOR", "Orange Refractor"),
        ("GOLD REFRACTOR", "Gold Refractor"),
        ("GREEN REFRACTOR", "Green Refractor"),
        ("BLUE REFRACTOR", "Blue Refractor"),
        ("PURPLE REFRACTOR", "Purple Refractor"),
        ("AQUA REFRACTOR", "Aqua Refractor"),
        ("SILVER REFRACTOR", "Silver Refractor"),
        ("X-FRACTOR", "X-Fractor"),
        ("X FRACTOR", "X-Fractor"),
        ("MOJO", "Mojo Refractor"),
        ("FUCHSIA", "Fuchsia"),
        ("LUNAR GLOW", "Lunar Glow"),
        ("RAYWAVE", "RayWave"),
        ("RAY WAVE", "RayWave"),
        ("REFRACTOR", "Refractor"),
        ("SHIMMER", "Shimmer"),
        ("SAPPHIRE", "Sapphire"),
        ("SPECKLE", "Speckle"),
    ]

    for pattern, name in parallel_patterns:
        if pattern in title_upper:
            parallel = name
            break

    # Card number
    card_number = None

    card_number_match = re.search(
        r"(?:#\s*)?\b([A-Z]{2,5}-[A-Z0-9]{1,6})\b",
        title_upper
    )

    if card_number_match:
        card_number = card_number_match.group(1)

    # Serial numbering
    serial_number = None
    serial_numbered_to = None

    # Serial numbering
    serial_number = None
    serial_numbered_to = None
    
    serial_title = re.sub(
        r"\b(?:PSA|BGS|SGC|CGC|BCCG)\s+\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?",
        " ",
        title_upper
    )
    
    serial_match = re.search(
        r"(?<![\d.])(\d{1,4})\s*/\s*(\d{1,4})(?![\d.])",
        serial_title
    )
    
    if serial_match:
        serial_number = int(serial_match.group(1))
        serial_numbered_to = int(serial_match.group(2))
    else:
        denominator_match = re.search(
            r"(?:#\s*)?/\s*(\d{1,4})\b",
            serial_title
        )

        if denominator_match:
            serial_numbered_to = int(denominator_match.group(1))

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
    for company in ["PSA", "BGS", "SGC", "CGC", "BCCG"]:
        if re.search(rf"\b{company}\b", title_upper):
            grade_company = company
            break

    # Grade
    grade = None

    if grade_company:
        grade_match = re.search(
            rf"\b{grade_company}\b.*?\b(\d+(?:\.\d+)?)\b",
            title_upper
        )
    
        if grade_match:
            grade = float(grade_match.group(1))

    # Identify listings that are not individual cards
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

    if re.search(
        r"\b\d+\s*CARD(?:S)?\s+(?:OR\s+\$?\d+(?:\.\d+)?\s+)?MINIMUM\b",
        title_upper
    ):
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
        "serial_number": serial_number,
        "serial_numbered_to": serial_numbered_to,
        "autograph": autograph,
        "rookie_card": rookie_card,
        "first_bowman": first_bowman,
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

@app.route("/parser-test", methods=["GET"])
def parser_test():
    title = request.args.get("title", "").strip()

    if not title:
        return jsonify({
            "success": False,
            "error": "Missing title parameter"
        }), 400

    card_data = parse_card_title(title)

    return jsonify({
        "success": True,
        "title": title,
        "parsed": card_data
    }), 200

def get_soldcomps_sales(query, count=100, days=90):
    response = requests.get(
        "https://api.sold-comps.com/v1/scrape",
        headers={
            "Authorization": f"Bearer {SOLDCOMPS_API_KEY}"
        },
        params={
            "keyword": query,
            "count": count,
            "daysToScrape": days,
        },
        timeout=30,
    )

    if response.status_code != 200:
        return []

    data = response.json()
    return data.get("items", [])

def get_cached_soldcomps_sales(
    search_key,
    query,
    count=100,
    days=90,
    cache_hours=24,
):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:

            # Check whether this exact card was refreshed recently
            cur.execute("""
                SELECT last_refreshed
                FROM sold_comp_cache
                WHERE search_key = %s
                  AND last_refreshed >
                      CURRENT_TIMESTAMP - (%s * INTERVAL '1 hour')
            """, (search_key, cache_hours))

            cache_is_fresh = cur.fetchone() is not None

            if not cache_is_fresh:
                # Call SoldComps only when cache is stale/missing
                sales = get_soldcomps_sales(
                    query,
                    count=count,
                    days=days,
                )

                for sale in sales:
                    sold_item_id = str(
                        sale.get("itemId")
                        or sale.get("epid")
                        or sale.get("url")
                        or ""
                    )

                    if not sold_item_id:
                        continue

                    cur.execute("""
                        INSERT INTO sold_comps (
                            sold_item_id,
                            search_key,
                            title,
                            sold_price,
                            shipping_price,
                            total_price,
                            sold_date,
                            buying_format,
                            bid_count,
                            best_offer_accepted,
                            listing_url,
                            collected_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            CURRENT_TIMESTAMP
                        )
                        ON CONFLICT (sold_item_id)
                        DO UPDATE SET
                            title = EXCLUDED.title,
                            sold_price = EXCLUDED.sold_price,
                            shipping_price = EXCLUDED.shipping_price,
                            total_price = EXCLUDED.total_price,
                            sold_date = EXCLUDED.sold_date,
                            buying_format = EXCLUDED.buying_format,
                            bid_count = EXCLUDED.bid_count,
                            best_offer_accepted =
                                EXCLUDED.best_offer_accepted,
                            listing_url = EXCLUDED.listing_url,
                            collected_at = CURRENT_TIMESTAMP
                    """, (
                        sold_item_id,
                        search_key,
                        sale.get("title"),
                        sale.get("soldPrice"),
                        sale.get("shippingPrice"),
                        sale.get("totalPrice"),
                        sale.get("endedAt"),
                        sale.get("buyingFormat"),
                        sale.get("bidCount"),
                        sale.get("bestOfferAccepted"),
                        sale.get("url"),
                    ))

                cur.execute("""
                    INSERT INTO sold_comp_cache (
                        search_key,
                        last_refreshed,
                        results_received
                    )
                    VALUES (%s, CURRENT_TIMESTAMP, %s)
                    ON CONFLICT (search_key)
                    DO UPDATE SET
                        last_refreshed = CURRENT_TIMESTAMP,
                        results_received = EXCLUDED.results_received
                """, (
                    search_key,
                    len(sales),
                ))

                conn.commit()

            # Return everything we've permanently accumulated
            # for this exact Bowman identity
            cur.execute("""
                SELECT
                    title,
                    sold_price,
                    shipping_price,
                    total_price,
                    sold_date,
                    buying_format,
                    bid_count,
                    best_offer_accepted,
                    listing_url
                FROM sold_comps
                WHERE search_key = %s
                ORDER BY sold_date DESC NULLS LAST
            """, (search_key,))

            rows = cur.fetchall()

    return [
        {
            "title": row[0],
            "soldPrice": float(row[1]) if row[1] is not None else None,
            "shippingPrice": float(row[2]) if row[2] is not None else None,
            "totalPrice": float(row[3]) if row[3] is not None else None,
            "endedAt": row[4].isoformat() if row[4] else None,
            "buyingFormat": row[5],
            "bidCount": row[6],
            "bestOfferAccepted": row[7],
            "url": row[8],
        }
        for row in rows
    ]

def get_sold_price_tiers(
    sales,
    player_name,
    card_year,
    product,
    card_number,
    parallel,
    grade_company=None,
    grade=None,
):
    exact_prices = []
    same_parallel_prices = []
    same_card_prices = []

    
    target_player = (player_name or "").casefold().strip()

    for sale in sales:
        title = sale.get("title", "")
        sold_price = sale.get("totalPrice") or sale.get("soldPrice")

        if sold_price is None:
            continue

        card_data = parse_card_title(title)

        sale_player = (
            card_data.get("player_name") or ""
        ).casefold().strip()

        player_match = sale_player == target_player
        year_match = card_data.get("card_year") == card_year
        product_match = card_data.get("product") == product
        card_number_match = (
            card_data.get("card_number") == card_number
        )
        parallel_match = (
            card_data.get("parallel") == parallel
        )

        if not (
            player_match
            and year_match
            and product_match
            and card_number_match
        ):
            continue

        try:
            price = float(sold_price)
        except (TypeError, ValueError):
            continue

        # Tier 3:
        # Same exact Bowman card number, regardless of parallel.
        same_card_prices.append({
            "price": price,
            "parallel": card_data.get("parallel"),
            "serial_numbered_to": card_data.get("serial_numbered_to"),
            "grade_company": card_data.get("grade_company"),
            "grade": card_data.get("grade"),
        })

        if not parallel_match:
            continue
    
        # Tier 2:
        # Same exact Bowman card and parallel,
        # regardless of grade.
        same_parallel_prices.append(price)

        grade_company_match = True
        grade_match = True

        if grade_company:
            grade_company_match = (
                card_data.get("grade_company")
                == grade_company
            )

        if grade is not None:
            grade_match = (
                card_data.get("grade") == grade
            )

        # Tier 1:
        # Same card + same parallel + same grade.
        if grade_company_match and grade_match:
            exact_prices.append(price)

    return {
    "exact_prices": exact_prices,
    "same_parallel_prices": same_parallel_prices,
    "same_card_prices": same_card_prices,
}
    
def calculate_auction_decision(
    exact_prices,
    current_bid=None
):
    exact_prices = sorted(
        price for price in exact_prices
        if price is not None
    )

    exact_comp_count = len(exact_prices)

    exact_active_median = None
    exact_lowest_ask = None
    exact_highest_ask = None

    if exact_prices:
        exact_lowest_ask = round(exact_prices[0], 2)
        exact_highest_ask = round(exact_prices[-1], 2)

        middle = exact_comp_count // 2

        if exact_comp_count % 2 == 1:
            exact_active_median = exact_prices[middle]
        else:
            exact_active_median = (
                exact_prices[middle - 1]
                + exact_prices[middle]
            ) / 2

        exact_active_median = round(
            exact_active_median,
            2
        )

    # Evidence confidence / active-ask haircut
    if exact_comp_count >= 8:
        evidence_confidence = 80
        valuation_haircut = 0.78

    elif exact_comp_count >= 5:
        evidence_confidence = 70
        valuation_haircut = 0.75

    elif exact_comp_count >= 3:
        evidence_confidence = 55
        valuation_haircut = 0.70

    elif exact_comp_count >= 1:
        evidence_confidence = 35
        valuation_haircut = 0.60

    else:
        evidence_confidence = 0
        valuation_haircut = 0.0

    conservative_value = None
    recommended_max_bid = None

    if exact_active_median is not None:
        conservative_value = round(
            exact_active_median * valuation_haircut,
            2
        )

        recommended_max_bid = conservative_value

    action = "NO BID"
    bid_headroom = None

    if (
        recommended_max_bid is not None
        and current_bid is not None
    ):
        bid_headroom = round(
            recommended_max_bid - current_bid,
            2
        )

        if evidence_confidence < 50:
            action = "NO BID"

        elif current_bid >= recommended_max_bid:
            action = "PASS"

        elif current_bid >= recommended_max_bid * 0.90:
            action = "WAIT"

        else:
            action = "BID"

    return {
        "exact_comp_count": exact_comp_count,
        "exact_active_median": exact_active_median,
        "exact_lowest_ask": exact_lowest_ask,
        "exact_highest_ask": exact_highest_ask,
        "evidence_confidence": evidence_confidence,
        "conservative_value": conservative_value,
        "recommended_max_bid": recommended_max_bid,
        "bid_headroom": bid_headroom,
        "action": action,
        "valuation_basis": "ACTIVE_ASKING_PRICES",
    }

@app.route("/ebay/exact-comp-search", methods=["GET"])
def ebay_exact_comp_search():

    player = request.args.get("player", "").strip()
    year = request.args.get("year", "").strip()
    card_number = request.args.get("card_number", "").strip()
    product = request.args.get("product", "").strip()
    current_bid_raw = request.args.get("current_bid", "").strip()

    try:
        current_bid = float(current_bid_raw) if current_bid_raw else None
    except ValueError:
        current_bid = None

    if not player:
        return jsonify({
            "success": False,
            "error": "Missing player"
        }), 400
        
    # Build a focused eBay query
    query_parts = []

    if year:
        query_parts.append(year)

    if product:
        query_parts.append(product)

    query_parts.append(player)

    if card_number:
        query_parts.append(card_number)

    query = " ".join(query_parts)

    # Get eBay access token
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

    # Search active fixed-price listings only
    search_response = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        },
        params={
            "q": query,
            "limit": 100,
            "filter": "buyingOptions:{FIXED_PRICE}",
        },
        timeout=20,
    )

    if search_response.status_code != 200:
        return jsonify({
            "success": False,
            "error": search_response.text
        }), search_response.status_code

    data = search_response.json()

    results = []

    for item in data.get("itemSummaries", []):

        title = item.get("title", "")
        card_data = parse_card_title(title)

        # Authoritative player matching
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:

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

        # Classify how closely this listing matches the requested card
        requested_year = int(year) if year.isdigit() else None

        def normalize_text(value):
            if not value:
                return ""

            return "".join(
                c
                for c in unicodedata.normalize("NFKD", value)
                if not unicodedata.combining(c)
            ).casefold().strip()

        player_match = (
            normalize_text(card_data["player_name"])
            == normalize_text(player)
        )

        year_match = (
            requested_year is None
            or card_data["card_year"] == requested_year
        )

        product_match = (
            not product
            or card_data["product"] == product
        )

        card_number_match = (
            not card_number
            or card_data["card_number"] == card_number.upper()
        )

        # V1 assumes Base when no requested parallel is supplied
        parallel_match = card_data["parallel"] is None

        if (
            player_match
            and year_match
            and product_match
            and card_number_match
            and parallel_match
        ):
            match_level = "EXACT"

        elif (
            player_match
            and year_match
            and product_match
            and card_number_match
        ):
            match_level = "RELATED"

        elif (
            player_match
            and year_match
            and product_match
        ):
            match_level = "RELATED"

        else:
            match_level = "REJECT"

        price = item.get("price", {}).get("value")

        shipping_options = item.get("shippingOptions", [])
        shipping_cost = None

        if shipping_options:
            shipping_cost = (
                shipping_options[0]
                .get("shippingCost", {})
                .get("value")
            )

        total_price = None

        if price is not None:
            total_price = float(price)

            if shipping_cost is not None:
                total_price += float(shipping_cost)

        results.append({
            "match_level": match_level,
            "title": title,
            "player_name": card_data["player_name"],
            "card_year": card_data["card_year"],
            "product": card_data["product"],
            "card_number": card_data["card_number"],
            "parallel": card_data["parallel"],
            "serial_numbered_to": card_data["serial_numbered_to"],
            "autograph": card_data["autograph"],
            "grade_company": card_data["grade_company"],
            "grade": card_data["grade"],
            "price": price,
            "shipping_cost": shipping_cost,
            "total_price": total_price,
            "url": item.get("itemWebUrl"),
        })

    # Aggregate EXACT comparable listings
    exact_prices = sorted(
        result["total_price"]
        for result in results
        if result["match_level"] == "EXACT"
        and result["total_price"] is not None
    )

    decision = calculate_auction_decision(
        exact_prices,
        current_bid
    )

    exact_comp_count = decision["exact_comp_count"]
    exact_active_median = decision["exact_active_median"]
    exact_lowest_ask = decision["exact_lowest_ask"]
    exact_highest_ask = decision["exact_highest_ask"]
    evidence_confidence = decision["evidence_confidence"]
    conservative_value = decision["conservative_value"]
    recommended_max_bid = decision["recommended_max_bid"]
    bid_headroom = decision["bid_headroom"]
    action = decision["action"]
    
    return jsonify({
        "success": True,
        "query": query,
        "ebay_total_matches": data.get("total", 0),
        "results_returned": len(results),
        "exact_comp_count": exact_comp_count,
        "exact_active_median": exact_active_median,
        "exact_lowest_ask": exact_lowest_ask,
        "exact_highest_ask": exact_highest_ask,
        "valuation_basis": "ACTIVE_ASKING_PRICES",
        "evidence_confidence": evidence_confidence,
        "conservative_value": conservative_value,
        "recommended_max_bid": recommended_max_bid,
        "current_bid": current_bid,
        "bid_headroom": bid_headroom,
        "action": action,
        "results": results
    }), 200
    
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
                    
                    cur.execute("""
                CREATE TABLE IF NOT EXISTS sold_comps (
                    id BIGSERIAL PRIMARY KEY,
                    sold_item_id TEXT UNIQUE,
                    search_key TEXT NOT NULL,
                    title TEXT,
                    sold_price NUMERIC(12,2),
                    shipping_price NUMERIC(12,2),
                    total_price NUMERIC(12,2),
                    sold_date TIMESTAMP,
                    buying_format TEXT,
                    bid_count INTEGER,
                    best_offer_accepted BOOLEAN,
                    listing_url TEXT,
                    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            
                CREATE INDEX IF NOT EXISTS idx_sold_comps_search_key
                ON sold_comps(search_key);
            
                CREATE TABLE IF NOT EXISTS sold_comp_cache (
                    search_key TEXT PRIMARY KEY,
                    last_refreshed TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    results_received INTEGER DEFAULT 0
                );
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


@app.route("/ebay/auction-check", methods=["GET"])
def ebay_auction_check():

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

    queries = [
        "Bowman Chrome baseball card",
        "Bowman Draft baseball card",
        "Bowman Sterling baseball card",
        "Topps Chrome baseball card",
    ]

    results = []

    for query in queries:

        search_response = requests.get(
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={
                "q": query,
                "limit": 10,
                "filter": "buyingOptions:{AUCTION}",
            },
            timeout=20,
        )

        if search_response.status_code != 200:
            results.append({
                "query": query,
                "error": search_response.text
            })
            continue

        data = search_response.json()

        sample_items = []

        for item in data.get("itemSummaries", []):
            if len(sample_items) == 0:
                sample_items.append({
                    "RAW_FIRST_ITEM": item
                })
                continue
            
            sample_items.append({
                "title": item.get("title"),
                "price": item.get("price", {}).get("value"),
                "buying_options": item.get("buyingOptions"),
                "item_end_date": item.get("itemEndDate"),
                "url": item.get("itemWebUrl"),
            })

        results.append({
            "query": query,
            "total_auctions": data.get("total", 0),
            "sample_items": sample_items,
        })

    return jsonify({
        "success": True,
        "results": results
    }), 200

@app.route("/ebay/auction-snapshot", methods=["GET"])
def ebay_auction_snapshot():

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

    queries = [
        "Bowman Chrome baseball card",
        "Bowman Draft baseball card",
        "Bowman Sterling baseball card",
    ]

    items = []

    for query in queries:
        search_response = requests.get(
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={
                "q": query,
                "limit": 50,
                "filter": "buyingOptions:{AUCTION}",
            },
            timeout=20,
        )

        if search_response.status_code != 200:
            continue

        data = search_response.json()
        items.extend(data.get("itemSummaries", []))

    # Prevent the same auction from being inserted twice
    # if it appears in more than one Bowman query.
    unique_items = {}

    for item in items:
        ebay_item_id = (
            item.get("legacyItemId")
            or item.get("itemId")
        )

        if ebay_item_id:
            unique_items[ebay_item_id] = item

    snapshots_saved = 0

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:

            for ebay_item_id, item in unique_items.items():

                title = item.get("title", "")
                card_data = parse_card_title(title)

                # Only keep true single-card Bowman listings
                if not card_data["is_single_card"]:
                    continue

                if card_data["manufacturer"] != "Bowman":
                    continue

                # Match player using the same players table logic
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

                current_bid = (
                    item.get("currentBidPrice", {})
                    .get("value")
                )

                bid_count = item.get("bidCount")

                shipping_options = item.get(
                    "shippingOptions", []
                )

                shipping_cost = None

                if shipping_options:
                    shipping_cost = (
                        shipping_options[0]
                        .get("shippingCost", {})
                        .get("value")
                    )

                seller_info = item.get("seller", {})

                cur.execute("""
                    INSERT INTO auction_history (
                        ebay_item_id,
                        title,
                        player_name,
                        card_year,
                        product,
                        card_number,
                        parallel,
                        serial_numbered_to,
                        autograph,
                        grade_company,
                        grade,
                        current_bid,
                        bid_count,
                        shipping_cost,
                        item_end_date,
                        listing_url,
                        seller_name
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                """, (
                    ebay_item_id,
                    title,
                    card_data["player_name"],
                    card_data["card_year"],
                    card_data["product"],
                    card_data["card_number"],
                    card_data["parallel"],
                    card_data["serial_numbered_to"],
                    card_data["autograph"],
                    card_data["grade_company"],
                    card_data["grade"],
                    current_bid,
                    bid_count,
                    shipping_cost,
                    item.get("itemEndDate"),
                    item.get("itemWebUrl"),
                    seller_info.get("username"),
                ))

                snapshots_saved += 1

        conn.commit()

    return jsonify({
        "success": True,
        "auctions_received": len(items),
        "unique_auctions": len(unique_items),
        "snapshots_saved": snapshots_saved
    }), 200

@app.route("/inventory/enrich", methods=["GET"])
def inventory_enrich():

    updated = 0
    skipped = 0

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    inventory_id,
                    original_title
                FROM inventory
                WHERE manufacturer = 'Bowman'
                ORDER BY inventory_id
            """)

            inventory_rows = cur.fetchall()

            for inventory_id, title in inventory_rows:

                card_data = parse_card_title(title)

                # Use our known-player table just like ebay_search()
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

                if card_data["manufacturer"] != "Bowman":
                    skipped += 1
                    continue

                cur.execute("""
                    UPDATE inventory
                    SET
                        player_name = %s,
                        manufacturer = %s,
                        product = %s,
                        card_number = %s,
                        parallel = %s,
                        serial_number = %s,
                        serial_numbered_to = %s,
                        autograph = %s,
                        rookie_card = %s,
                        first_bowman = %s,
                        grade_company = %s,
                        grade = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE inventory_id = %s
                """, (
                    card_data["player_name"],
                    card_data["manufacturer"],
                    card_data["product"],
                    card_data["card_number"],
                    card_data["parallel"],
                    card_data["serial_number"],
                    card_data["serial_numbered_to"],
                    card_data["autograph"],
                    card_data["rookie_card"],
                    card_data["first_bowman"],
                    card_data["grade_company"],
                    card_data["grade"],
                    inventory_id,
                ))

                updated += 1

        conn.commit()

    return jsonify({
        "success": True,
        "bowman_cards_updated": updated,
        "cards_skipped": skipped
    }), 200

@app.route("/ebay/re-enrich", methods=["GET"])
def ebay_re_enrich():

    updated = 0

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    title
                FROM ebay_listings
                WHERE is_single_card = TRUE
                  AND title IS NOT NULL
                ORDER BY id
            """)

            rows = cur.fetchall()

            for listing_id, title in rows:

                card_data = parse_card_title(title)

                # Use authoritative player table when possible
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

                cur.execute("""
                    UPDATE ebay_listings
                    SET
                        player_name = %s,
                        card_year = %s,
                        manufacturer = %s,
                        product = %s,
                        card_number = %s,
                        parallel = %s,
                        serial_numbered_to = %s,
                        autograph = %s,
                        rookie_card = %s,
                        grade_company = %s,
                        grade = %s,
                        is_single_card = %s
                    WHERE id = %s
                """, (
                    card_data["player_name"],
                    card_data["card_year"],
                    card_data["manufacturer"],
                    card_data["product"],
                    card_data["card_number"],
                    card_data["parallel"],
                    card_data["serial_numbered_to"],
                    card_data["autograph"],
                    card_data["rookie_card"],
                    card_data["grade_company"],
                    card_data["grade"],
                    card_data["is_single_card"],
                    listing_id,
                ))

                updated += 1

        conn.commit()

    return jsonify({
        "success": True,
        "listings_re_enriched": updated
    }), 200


@app.route("/auction-value-refresh", methods=["GET"])
def auction_value_refresh():

    valuations_saved = 0
    skipped = 0
    errors = []

    # Get eBay access token once for the whole refresh
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

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:

            # Pull latest observation for active auctions.
            # Require enough identity information for useful comp searching.
            cur.execute("""
                WITH latest AS (
                    SELECT DISTINCT ON (ebay_item_id)
                        ebay_item_id,
                        player_name,
                        card_year,
                        product,
                        card_number,
                        parallel,
                        grade_company,
                        grade,
                        current_bid,
                        bid_count,
                        item_end_date
                    FROM auction_history
                    WHERE item_end_date > CURRENT_TIMESTAMP
                    ORDER BY ebay_item_id, observed_at DESC
                )

                SELECT
                    ebay_item_id,
                    player_name,
                    card_year,
                    product,
                    card_number,
                    parallel,
                    grade_company,
                    grade,
                    current_bid,
                    bid_count,
                    item_end_date

                FROM latest

                WHERE
                    player_name IS NOT NULL
                    AND card_year IS NOT NULL
                    AND product IS NOT NULL
                    AND card_number IS NOT NULL

                ORDER BY item_end_date ASC

                LIMIT 10;
            """)

            auctions = cur.fetchall()

            for auction in auctions:

                (
                    ebay_item_id,
                    player_name,
                    card_year,
                    product,
                    card_number,
                    parallel,
                    grade_company,
                    grade,
                    current_bid,
                    bid_count,
                    item_end_date
                ) = auction

                try:
                    # Verify player identity against authoritative players table
                    cur.execute("""
                        SELECT player_name
                        FROM players
                        WHERE LOWER(player_name) = LOWER(%s)
                        LIMIT 1
                    """, (player_name,))
        
                    verified_player = cur.fetchone()
        
                    identity_verified = verified_player is not None
        
                    if identity_verified:
                        identity_confidence = 100
                    else:
                        identity_confidence = 25

                    
                    query = (
                        f"{card_year} "
                        f"{product} "
                        f"{player_name} "
                        f"{card_number}"
                    )

                    search_response = requests.get(
                        "https://api.ebay.com/buy/browse/v1/item_summary/search",
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                        },
                        params={
                            "q": query,
                            "limit": 100,
                            "filter": "buyingOptions:{FIXED_PRICE}",
                        },
                        timeout=20,
                    )

                    if search_response.status_code != 200:
                        skipped += 1
                        errors.append({
                            "ebay_item_id": ebay_item_id,
                            "error": search_response.text
                        })
                        continue

                    # Build a stable cache key for this exact Bowman card
                    search_key = "|".join([
                        str(player_name or "").casefold().strip(),
                        str(card_year or ""),
                        str(product or "").casefold().strip(),
                        str(card_number or "").casefold().strip(),
                        str(parallel or "").casefold().strip(),
                        str(grade_company or "").casefold().strip(),
                        str(grade or ""),
                    ])
                    
                    # Build a SoldComps search query
                    sold_query_parts = [
                        str(card_year or ""),
                        str(player_name or ""),
                        str(product or ""),
                        str(card_number or ""),
                        str(parallel or ""),
                        str(grade_company or ""),
                        str(grade or ""),
                    ]
                    
                    sold_query = " ".join(
                        part.strip()
                        for part in sold_query_parts
                        if part and part.strip()
                    )
                    
                    # Use cached SoldComps results when refreshed within 24 hours
                    sold_sales = get_cached_soldcomps_sales(
                        search_key=search_key,
                        query=sold_query,
                        count=100,
                        days=90,
                        cache_hours=24,
                    )
                    
                    # Strictly filter SoldComps results to the same Bowman identity
                    price_tiers = get_sold_price_tiers(
                        sold_sales,
                        player_name=player_name,
                        card_year=card_year,
                        product=product,
                        card_number=card_number,
                        parallel=parallel,
                        grade_company=grade_company,
                        grade=grade,
                    )

                    exact_prices = price_tiers["exact_prices"]
                
                    if not exact_prices:
                        exact_prices = price_tiers["same_parallel_prices"]
                    
                    if not exact_prices:
                        broad_query_parts = [
                            str(card_year or ""),
                            str(player_name or ""),
                            str(product or ""),
                            str(card_number or ""),
                        ]
                    
                        broad_query = " ".join(
                            part.strip()
                            for part in broad_query_parts
                            if part and part.strip()
                        )
                    
                        broad_search_key = search_key + "|ALL_PARALLELS"
                    
                        broad_sales = get_cached_soldcomps_sales(
                            search_key=broad_search_key,
                            query=broad_query,
                            count=100,
                            days=90,
                            cache_hours=24,
                        )
                    
                        broad_tiers = get_sold_price_tiers(
                            broad_sales,
                            player_name=player_name,
                            card_year=card_year,
                            product=product,
                            card_number=card_number,
                            parallel=parallel,
                            grade_company=grade_company,
                            grade=grade,
                        )
                    
                        exact_prices = [
                            comp["price"]
                            for comp in broad_tiers["same_card_prices"]
                            if comp.get("price") is not None
                        ]
                            
                    decision = calculate_auction_decision(
                        exact_prices,
                        float(current_bid)
                        if current_bid is not None
                        else None
                )

                    # Never issue an automated BID on uncertain identity
                    if not identity_verified:
                        decision["action"] = "REVIEW"
                    
                    cur.execute("""
                        INSERT INTO auction_valuations (
                            ebay_item_id,
                            player_name,
                            card_year,
                            product,
                            card_number,
                            parallel,
                            current_bid,
                            exact_comp_count,
                            exact_active_median,
                            exact_lowest_ask,
                            exact_highest_ask,
                            evidence_confidence,
                            conservative_value,
                            recommended_max_bid,
                            bid_headroom,
                            identity_confidence,
                            identity_verified,
                            action,
                            valuation_basis,
                            valued_at
                        )

                        VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            CURRENT_TIMESTAMP
                        )

                        ON CONFLICT (ebay_item_id)
                        DO UPDATE SET
                            player_name = EXCLUDED.player_name,
                            card_year = EXCLUDED.card_year,
                            product = EXCLUDED.product,
                            card_number = EXCLUDED.card_number,
                            parallel = EXCLUDED.parallel,
                            current_bid = EXCLUDED.current_bid,
                            exact_comp_count = EXCLUDED.exact_comp_count,
                            exact_active_median = EXCLUDED.exact_active_median,
                            exact_lowest_ask = EXCLUDED.exact_lowest_ask,
                            exact_highest_ask = EXCLUDED.exact_highest_ask,
                            evidence_confidence = EXCLUDED.evidence_confidence,
                            conservative_value = EXCLUDED.conservative_value,
                            recommended_max_bid =
                                EXCLUDED.recommended_max_bid,
                            bid_headroom = EXCLUDED.bid_headroom,
                            identity_confidence = EXCLUDED.identity_confidence,
                            identity_verified = EXCLUDED.identity_verified,
                            action = EXCLUDED.action,
                            valuation_basis = EXCLUDED.valuation_basis,
                            valued_at = CURRENT_TIMESTAMP
                    """, (
                        ebay_item_id,
                        player_name,
                        card_year,
                        product,
                        card_number,
                        parallel,
                        current_bid,
                        decision["exact_comp_count"],
                        decision["exact_active_median"],
                        decision["exact_lowest_ask"],
                        decision["exact_highest_ask"],
                        decision["evidence_confidence"],
                        decision["conservative_value"],
                        decision["recommended_max_bid"],
                        decision["bid_headroom"],
                        identity_confidence,
                        identity_verified,
                        decision["action"],
                        decision["valuation_basis"],
                    ))

                    valuations_saved += 1

                except Exception as e:
                    skipped += 1
                    errors.append({
                        "ebay_item_id": ebay_item_id,
                        "error": str(e)
                    })

            conn.commit()

    return jsonify({
        "success": True,
        "auctions_considered": len(auctions),
        "valuations_saved": valuations_saved,
        "skipped": skipped,
        "errors": errors
    }), 200

@app.route("/soldcomps-test", methods=["GET"])
def soldcomps_test():
    query = request.args.get(
        "q",
        "2024 Bowman Chrome Gold Refractor Auto PSA 10"
    )

    response = requests.get(
        "https://api.sold-comps.com/v1/scrape",
        headers={
            "Authorization": f"Bearer {SOLDCOMPS_API_KEY}"
        },
        params={
            "keyword": query,
            "count": 10,
            "daysToScrape": 90,
        },
        timeout=30,
    )

    if response.status_code != 200:
        return jsonify({
            "success": False,
            "status_code": response.status_code,
            "error": response.text
        }), response.status_code

    data = response.json()

    return jsonify({
        "success": True,
        "query": query,
        "results_found": data.get("totalItems", 0),
        "items": data.get("items", [])
    }), 200


@app.route("/auction-watch", methods=["GET"])
def auction_watch():

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:

            cur.execute("""
                WITH ranked AS (
                    SELECT
                        ebay_item_id,
                        player_name,
                        card_year,
                        product,
                        card_number,
                        parallel,
                        serial_numbered_to,
                        grade_company,
                        grade,
                        current_bid,
                        bid_count,
                        item_end_date,
                        listing_url,
                        observed_at,

                        ROW_NUMBER() OVER (
                            PARTITION BY ebay_item_id
                            ORDER BY observed_at ASC
                        ) AS first_rn,

                        ROW_NUMBER() OVER (
                            PARTITION BY ebay_item_id
                            ORDER BY observed_at DESC
                        ) AS last_rn,

                        COUNT(*) OVER (
                            PARTITION BY ebay_item_id
                        ) AS observations

                    FROM auction_history
                ),

                movement AS (
                    SELECT
                        ebay_item_id,

                        MAX(player_name) FILTER (WHERE last_rn = 1)
                            AS player_name,

                        MAX(card_year) FILTER (WHERE last_rn = 1)
                            AS card_year,

                        MAX(product) FILTER (WHERE last_rn = 1)
                            AS product,

                        MAX(card_number) FILTER (WHERE last_rn = 1)
                            AS card_number,

                        MAX(parallel) FILTER (WHERE last_rn = 1)
                            AS parallel,

                        MAX(serial_numbered_to) FILTER (WHERE last_rn = 1)
                            AS serial_numbered_to,

                        MAX(grade_company) FILTER (WHERE last_rn = 1)
                            AS grade_company,

                        MAX(grade) FILTER (WHERE last_rn = 1)
                            AS grade,

                        MAX(current_bid) FILTER (WHERE first_rn = 1)
                            AS first_bid,

                        MAX(current_bid) FILTER (WHERE last_rn = 1)
                            AS current_bid,

                        MAX(bid_count) FILTER (WHERE first_rn = 1)
                            AS first_bid_count,

                        MAX(bid_count) FILTER (WHERE last_rn = 1)
                            AS current_bid_count,

                        MAX(observed_at) FILTER (WHERE first_rn = 1)
                            AS first_observed,

                        MAX(observed_at) FILTER (WHERE last_rn = 1)
                            AS last_observed,

                        MAX(item_end_date) FILTER (WHERE last_rn = 1)
                            AS item_end_date,

                        MAX(listing_url) FILTER (WHERE last_rn = 1)
                            AS listing_url,

                        MAX(observations) AS observations

                    FROM ranked
                    GROUP BY ebay_item_id
                ),

                metrics AS (
                    SELECT
                        *,

                        GREATEST(
                            EXTRACT(
                                EPOCH FROM (
                                    last_observed - first_observed
                                )
                            ) / 3600.0,
                            0.25
                        ) AS hours_observed,

                        GREATEST(
                            EXTRACT(
                                EPOCH FROM (
                                    item_end_date - CURRENT_TIMESTAMP
                                )
                            ) / 3600.0,
                            0
                        ) AS hours_remaining,

                        current_bid_count - first_bid_count
                            AS new_bids,

                        current_bid - first_bid
                            AS price_change

                    FROM movement

                    WHERE
                        observations >= 2
                        AND item_end_date > CURRENT_TIMESTAMP
                )

                SELECT
                   m.ebay_item_id,
                    m.player_name,
                    m.card_year,
                    m.product,
                    m.card_number,
                    m.parallel,
                    m.serial_numbered_to,
                    m.grade_company,
                    m.grade,
                    m.current_bid,
                    m.current_bid_count,

                    -- Demand: 0-100
                    LEAST(
                        100,
                        current_bid_count * 4
                    ) AS demand_score,

                    -- Recent Momentum: 0-100
                    LEAST(
                        100,
                        GREATEST(
                            0,
                            (
                                (new_bids / hours_observed) * 20
                            )
                            +
                            CASE
                                WHEN first_bid > 0
                                THEN
                                    (
                                        (price_change / first_bid) * 100
                                        / hours_observed
                                    )
                                ELSE 0
                            END
                        )
                    ) AS momentum_score,

                    hours_remaining,

                    -- Urgency: 0-100
                    CASE
                        WHEN hours_remaining <= 1 THEN 100
                        WHEN hours_remaining <= 3 THEN 90
                        WHEN hours_remaining <= 6 THEN 80
                        WHEN hours_remaining <= 12 THEN 65
                        WHEN hours_remaining <= 24 THEN 50
                        WHEN hours_remaining <= 48 THEN 30
                        WHEN hours_remaining <= 72 THEN 15
                        ELSE 5
                    END AS urgency_score,

                    observations,
                    listing_url,
                
                    av.identity_verified,
                    av.evidence_confidence,
                    av.exact_comp_count,
                    av.exact_active_median,
                    av.recommended_max_bid,
                    av.bid_headroom,
                    av.action,
                    av.valued_at
            
            FROM metrics m

                LEFT JOIN auction_valuations av
                    ON av.ebay_item_id = m.ebay_item_id

                ORDER BY
                    urgency_score DESC,
                    momentum_score DESC,
                    demand_score DESC

                LIMIT 100;
            """)

            rows = cur.fetchall()

    html = """
    <html>
    <head>
        <title>Bowman Auction Watch</title>

        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f4f5f7;
                margin: 0;
                padding: 20px;
            }

            h1 {
                margin-bottom: 5px;
            }

            .subtitle {
                color: #555;
                margin-bottom: 25px;
            }

            table {
                width: 100%;
                border-collapse: collapse;
                background: white;
            }

            th {
                background: #222;
                color: white;
                text-align: left;
                padding: 11px;
            }

            td {
                padding: 10px 11px;
                border-bottom: 1px solid #ddd;
            }

            tr:hover {
                background: #f2f2f2;
            }

            .high {
                color: green;
                font-weight: bold;
            }

            .medium {
                color: #b36b00;
                font-weight: bold;
            }

            .low {
                color: #777;
            }

            .urgent {
                color: #c00000;
                font-weight: bold;
            }

            a {
                font-weight: bold;
            }
        </style>
    </head>

    <body>

        <h1>Bowman Auction Watch</h1>

        <div class="subtitle">
            Live auction behavior — Demand, Momentum and Urgency
        </div>

        <table>
            <tr>
                <th>Player</th>
                <th>Card</th>
                <th>Product</th>
                <th>Parallel</th>
                <th>Serial</th>
                <th>Grade</th>
                <th>Current Bid</th>
                <th>Max Bid</th>
                <th>Headroom</th>
                <th>Action</th>
                <th>Bids</th>
                <th>Demand</th>
                <th>Momentum</th>
                <th>Time Left</th>
                <th>Urgency</th>
                <th>Obs.</th>
                <th>eBay</th>
            </tr>
    """

    for row in rows:

        (
            ebay_item_id,
            player_name,
            card_year,
            product,
            card_number,
            parallel,
            serial_numbered_to,
            grade_company,
            grade,
            current_bid,
            current_bid_count,
            demand_score,
            momentum_score,
            hours_remaining,
            urgency_score,
            observations,
            listing_url,
              
            identity_verified,
            evidence_confidence,
            exact_comp_count,
            exact_active_median,
            recommended_max_bid,
            bid_headroom,
            action,
            valued_at
        ) = row

        if card_number:
            card_display = f"{card_year or ''} #{card_number}"
        else:
            card_display = str(card_year or "")

        serial_display = (
            f"/{serial_numbered_to}"
            if serial_numbered_to is not None
            else ""
        )

        if grade_company and grade is not None:
            grade_number = (
                str(int(grade))
                if float(grade).is_integer()
                else str(grade)
            )
            grade_display = f"{grade_company} {grade_number}"
        else:
            grade_display = "Raw"

        current_bid_display = (
            f"${float(current_bid):,.2f}"
            if current_bid is not None
            else ""
        )

        if action == "REVIEW":
            max_bid_display = "—"
            headroom_display = "—"
        
        elif recommended_max_bid is not None:
            max_bid_display = f"${float(recommended_max_bid):,.2f}"
        
            headroom_display = (
                f"${float(bid_headroom):,.2f}"
                if bid_headroom is not None
                else "—"
            )
        
        else:
            max_bid_display = "—"
            headroom_display = "—"

        if action == "BID":
            action_display = '<span class="high">BID</span>'
        
        elif action == "PASS":
            action_display = '<span class="urgent">PASS</span>'
        
        elif action == "WAIT":
            action_display = '<span class="medium">WAIT</span>'
        
        elif action == "REVIEW":
            action_display = '<span class="medium">REVIEW</span>'
        
        elif action == "NO BID":
            action_display = '<span class="low">NO BID</span>'

        else:
            action_display = '<span class="low">NOT VALUED</span>'
        
        demand = float(demand_score or 0)
        momentum = float(momentum_score or 0)
        urgency = float(urgency_score or 0)
        hours = float(hours_remaining or 0)

        def score_class(score):
            if score >= 70:
                return "high"
            elif score >= 35:
                return "medium"
            return "low"

        if hours < 1:
            time_display = f"{hours * 60:.0f} min"
        elif hours < 24:
            time_display = f"{hours:.1f} hr"
        else:
            time_display = f"{hours / 24:.1f} days"

        urgency_class = (
            "urgent"
            if urgency >= 80
            else score_class(urgency)
        )

        html += f"""
            <tr>
                <td>{player_name or ""}</td>
                <td>{card_display}</td>
                <td>{product or ""}</td>
                <td>{parallel or "Base"}</td>
                <td>{serial_display}</td>
                <td>{grade_display}</td>
                <td>{current_bid_display}</td>
                <td>{max_bid_display}</td>
                <td>{headroom_display}</td>
                <td>{action_display}</td>
                <td>{current_bid_count or 0}</td>

                <td class="{score_class(demand)}">
                    {demand:.0f}
                </td>

                <td class="{score_class(momentum)}">
                    {momentum:.0f}
                </td>

                <td class="{urgency_class}">
                    {time_display}
                </td>

                <td class="{urgency_class}">
                    {urgency:.0f}
                </td>

                <td>{observations}</td>

                <td>
                    <a href="{listing_url}" target="_blank">
                        View
                    </a>
                </td>
            </tr>
        """

    html += """
        </table>

    </body>
    </html>
    """

    return html

@app.route("/inventory-dashboard", methods=["GET"])
def inventory_dashboard():

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    inventory_id,
                    player_name,
                    card_year,
                    product,
                    card_number,
                    parallel,
                    serial_number,
                    serial_numbered_to,
                    grade_company,
                    grade,
                    autograph,
                    first_bowman,
                    purchase_price
                FROM inventory
                WHERE manufacturer = 'Bowman'
                ORDER BY purchase_price DESC NULLS LAST
            """)

            rows = cur.fetchall()

    total_cards = len(rows)

    total_cost = sum(
        float(row[12])
        for row in rows
        if row[12] is not None
    )

    html = f"""
    <html>
    <head>
        <title>Bowman Inventory</title>

        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f4f5f7;
                margin: 0;
                padding: 20px;
            }}

            h1 {{
                margin-bottom: 5px;
            }}

            .summary {{
                margin: 20px 0;
                font-size: 18px;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
            }}

            th {{
                background: #222;
                color: white;
                text-align: left;
                padding: 12px;
            }}

            td {{
                padding: 11px 12px;
                border-bottom: 1px solid #ddd;
            }}

            tr:hover {{
                background: #f2f2f2;
            }}

            .yes {{
                color: green;
                font-weight: bold;
            }}

            .no {{
                color: #777;
            }}
        </style>
    </head>

    <body>

        <h1>Bowman Inventory</h1>

        <div class="summary">
            <strong>{total_cards}</strong> Bowman cards
            &nbsp;&nbsp;|&nbsp;&nbsp;
            Cost Basis:
            <strong>${total_cost:,.2f}</strong>
        </div>

        <table>
            <tr>
                <th>Player</th>
                <th>Card</th>
                <th>Product</th>
                <th>Parallel</th>
                <th>Serial</th>
                <th>Grade</th>
                <th>Auto</th>
                <th>1st Bowman</th>
                <th>Cost Basis</th>
            </tr>
    """

    for row in rows:

        (
            inventory_id,
            player_name,
            card_year,
            product,
            card_number,
            parallel,
            serial_number,
            serial_numbered_to,
            grade_company,
            grade,
            autograph,
            first_bowman,
            purchase_price
        ) = row

        if card_number:
            card_display = f"{card_year} #{card_number}"
        else:
            card_display = str(card_year or "")

        if serial_number is not None and serial_numbered_to is not None:
            serial_display = f"{serial_number}/{serial_numbered_to}"
        elif serial_numbered_to is not None:
            serial_display = f"/{serial_numbered_to}"
        else:
            serial_display = ""

        if grade_company and grade is not None:
            grade_number = (
                str(int(grade))
                if float(grade).is_integer()
                else str(grade)
            )
            grade_display = f"{grade_company} {grade_number}"
        else:
            grade_display = "Raw"

        auto_display = (
            '<span class="yes">YES</span>'
            if autograph
            else '<span class="no">—</span>'
        )

        first_display = (
            '<span class="yes">YES</span>'
            if first_bowman
            else '<span class="no">—</span>'
        )

        cost_display = (
            f"${float(purchase_price):,.2f}"
            if purchase_price is not None
            else ""
        )

        html += f"""
            <tr>
                <td>{player_name or ""}</td>
                <td>{card_display}</td>
                <td>{product or ""}</td>
                <td>{parallel or "Base"}</td>
                <td>{serial_display}</td>
                <td>{grade_display}</td>
                <td>{auto_display}</td>
                <td>{first_display}</td>
                <td>{cost_display}</td>
            </tr>
        """

    html += """
        </table>

    </body>
    </html>
    """

    return html


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
                    HAVING COUNT(*) >= 2
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
                        CAST(
                            c.listing_count,
                            c.median_price,
                            (
                                (
                                    c.median_price -
                                    (e.asking_price + COALESCE(e.shipping_cost, 0))
                                )
                                / NULLIF(c.median_price, 0)
                            ) * 100 AS discount_percentage
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
        comparable_count = row[10]

        confidence_score = min(
            100,
            55 + max(0, comparable_count - 3) * 8
        )
        deal_quality_score = round(
    (discount_percentage * 0.60) +
    (confidence_score * 0.40),
    1
)
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
    "confidence_score": confidence_score,
    "deal_quality_score": deal_quality_score,
    "median_price": float(row[11]) if row[11] is not None else None,
    "discount_percentage": discount_percentage,
    "deal_rating": deal_rating,
})
    results.sort(
        key=lambda x: x["deal_quality_score"],
        reverse=True
    )
    return jsonify({
        "success": True,
        "deal_count": len(results),
        "deals": results
    }), 200

@app.route("/scan-card", methods=["GET", "POST"])
def scan_card():
    if request.method == "GET":
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Bowman Card Scanner</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {
                    font-family: Arial, sans-serif;
                    max-width: 600px;
                    margin: 40px auto;
                    padding: 20px;
                }

                h1 {
                    margin-bottom: 10px;
                }

                input, button {
                    font-size: 18px;
                    margin-top: 20px;
                    width: 100%;
                    padding: 14px;
                }

                button {
                    cursor: pointer;
                }
            </style>
        </head>

        <body>
            <h1>Bowman Card Scanner</h1>
            <p>Take a photo or choose a card image.</p>

            <form method="POST" enctype="multipart/form-data">
                <input
                    type="file"
                    name="card_image"
                    accept="image/*"
                    capture="environment"
                    required
                >

                <button type="submit">
                    Identify Card
                </button>
            </form>
        </body>
        </html>
        """

    image = request.files.get("card_image")

    if not image:
        return jsonify({
            "success": False,
            "error": "No image uploaded"
        }), 400

    image_bytes = image.read()

    cardsight_client = CardSightAI(
        api_key=CARDSIGHT_API_KEY
    )

    result = cardsight_client.identify.identify(
        image_bytes
    )

    if not result or not getattr(result, "detections", None):
        return jsonify({
            "success": False,
            "error": "No card identified"
        }), 404

    detection = result.detections[0]
    card = detection.card

    return jsonify({
        "success": True,
        "confidence": detection.confidence,
        "player": getattr(card, "name", None),
        "year": getattr(card, "year", None),
        "set_name": getattr(card, "set_name", None),
        "card_number": getattr(card, "card_number", None),
        "numbered_to": getattr(detection, "numbered_to", None),
    })

@app.route("/ximilar-test", methods=["GET", "POST"])
def ximilar_test():
    if request.method == "GET":
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Ximilar Bowman Test</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
        </head>
        <body style="font-family:Arial;max-width:600px;margin:40px auto;padding:20px;">
            <h1>Ximilar Bowman Test</h1>

            <form method="POST" enctype="multipart/form-data">
                <input
                    type="file"
                    name="card_image"
                    accept="image/*"
                    capture="environment"
                    required
                >
                <br><br>
                <button type="submit" style="font-size:18px;padding:14px;">
                    Identify Card
                </button>
            </form>
        </body>
        </html>
        """

    image = request.files.get("card_image")

    if not image:
        return jsonify({
            "success": False,
            "error": "No image uploaded"
        }), 400

    image_bytes = image.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    response = requests.post(
        "https://api.ximilar.com/collectibles/v2/sport_id",
        headers={
            "Authorization": f"Token {XIMILAR_API_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "records": [
                {
                    "_base64": image_base64
                }
            ],
            "magic_ai": True,
            "slab_id": True,
            "slab_grade": True,
            "price_stats": False,
        },
        timeout=60,
    )

    return jsonify({
        "http_status": response.status_code,
        "ximilar": response.json()
    }), response.status_code

@app.route("/deals-dashboard-v2", methods=["GET"])
def deals_dashboard_v2():
    player_filter = request.args.get("player", "").strip()
    rating_filter = request.args.get("rating", "").strip().upper()
    min_confidence = request.args.get("min_confidence", "").strip()
    min_comps = request.args.get("min_comps", "").strip()
    min_discount = request.args.get("min_discount", "").strip()
    parallel_filter = request.args.get("parallel", "").strip()
    grade_filter = request.args.get("grade", "").strip()
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
                        grade_company,
                        grade,
                        COUNT(*) AS listing_count,
                        PERCENTILE_CONT(0.5)
                            WITHIN GROUP (
                                ORDER BY asking_price + COALESCE(shipping_cost, 0)
                            ) AS median_price
                    FROM ebay_listings
                    WHERE
                        is_single_card = TRUE
                        AND player_name IS NOT NULL
                        AND asking_price IS NOT NULL
                    GROUP BY
                        player_name,
                        card_year,
                        product,
                        parallel,
                        card_number,
                        autograph,
                        grade_company,
                        grade
                    HAVING COUNT(*) >= 2
                )
                SELECT
                    e.title,
                    e.player_name,
                    e.card_year,
                    e.product,
                    e.parallel,
                    e.card_number,
                    e.grade_company,
                    e.grade,
                    e.asking_price,
                    e.shipping_cost,
                    e.listing_url,
                    c.listing_count,
                    c.median_price,
                    (
                        (
                            c.median_price -
                            (e.asking_price + COALESCE(e.shipping_cost, 0))
                        )
                        / NULLIF(c.median_price, 0)
                    ) * 100 AS discount_percentage
                   FROM ebay_listings e
                   JOIN comparable_stats c
                       ON e.player_name = c.player_name
                       AND e.card_year IS NOT DISTINCT FROM c.card_year
                       AND e.product IS NOT DISTINCT FROM c.product
                       AND e.parallel IS NOT DISTINCT FROM c.parallel
                       AND e.card_number IS NOT DISTINCT FROM c.card_number
                       AND e.autograph IS NOT DISTINCT FROM c.autograph
                       AND e.grade_company IS NOT DISTINCT FROM c.grade_company
                        AND e.grade IS NOT DISTINCT FROM c.grade
                   WHERE
                    e.is_single_card = TRUE
                    AND e.asking_price IS NOT NULL
                    AND (e.asking_price + COALESCE(e.shipping_cost, 0)) < c.median_price
                LIMIT 50;
            """)

            rows = cur.fetchall()

    deals = []

    for row in rows:
        discount = float(row[13])
        comparable_count = row[11]

        if comparable_count == 2:
            confidence = 40
        else:
            confidence = min(
            100,
            55 + (comparable_count - 3) * 8
            )
        scoring_discount = min(discount, 60)
        quality = round(
            (scoring_discount * 0.60) +
            (confidence * 0.40),
            1
        )
        if discount >= 20:
            rating = "BUY"
        elif discount >= 10:
            rating = "FAIR"
        else:
            rating = "HIGH"

        total_cost = (
            float(row[8]) +
            (float(row[9]) if row[9] is not None else 0)
        )
        deals.append({
            "title": row[0],
            "player_name": row[1],
            "card_year": row[2],
            "product": row[3],
            "parallel": row[4],
            "card_number": row[5],
            "grade_company": row[6],
            "grade": row[7],
            "asking_price": float(row[8]),
            "shipping_cost": float(row[9]) if row[9] is not None else 0,
            "total_cost": total_cost,
            "listing_url": row[10],
            "comparable_count": comparable_count,
            "median_price": float(row[12]),
            "discount_percentage": discount,
            "confidence_score": confidence,
            "deal_quality_score": quality,
            "deal_rating": rating,
        })
    parallel_options = sorted(set(
        deal["parallel"] or "Base"
        for deal in deals
    ))
    grade_options = sorted(set(
    (
        deal["grade_company"] + " " + str(deal["grade"])
        if deal["grade_company"] and deal["grade"] is not None
        else "Raw"
    )
    for deal in deals
))
    if player_filter:
        deals = [
            deal for deal in deals
            if player_filter.lower() in deal["player_name"].lower()
        ]
    if rating_filter:
        deals = [
            deal for deal in deals
            if deal["deal_rating"] == rating_filter
        ]
    if parallel_filter:
        deals = [
            deal for deal in deals
            if (deal["parallel"] or "Base") == parallel_filter
        ]
    if grade_filter:
        deals = [
            deal for deal in deals
            if (
                (
                    deal["grade_company"] + " " + str(deal["grade"])
                    if deal["grade_company"] and deal["grade"] is not None
                    else "Raw"
                )
                == grade_filter
            )
    ]
    if min_confidence:
        deals = [
            deal for deal in deals
            if deal["confidence_score"] >= int(min_confidence)
        ]
    if min_comps:
        deals = [
            deal for deal in deals
            if deal["comparable_count"] >= int(min_comps)
        ]
    if min_discount:
        deals = [
            deal for deal in deals
            if deal["discount_percentage"] >= float(min_discount)
        ]
    deals.sort(
        key=lambda x: x["deal_quality_score"],
        reverse=True
    )
    html = f"""
    <html>
    <head>
        <title>Baseball Card Deals</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 30px;
                background: #f5f6f8;
            }}
            h1 {{
                margin-bottom: 5px;
            }}
            .subtitle {{
                color: #666;
                margin-bottom: 25px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
            }}
            th, td {{
                padding: 10px;
                border-bottom: 1px solid #ddd;
                text-align: left;
                font-size: 14px;
            }}
            th {{
                background: #222;
                color: white;
                position: sticky;
                top: 0;
            }}
            .buy {{
                font-weight: bold;
                color: #087a2f;
            }}
            .fair {{
                font-weight: bold;
                color: #b36b00;
            }}
            .high {{
                font-weight: bold;
                color: #b42318;
            }}
            a {{
                text-decoration: none;
                font-weight: bold;
            }}
        </style>
    </head>

    <body>

        <h1>Baseball Card Deal Finder</h1>
        <div class="subtitle">
            Ranked by Deal Quality Score
        </div>
<form method="GET" action="/deals-dashboard-v2" style="margin-bottom: 20px;">
    <input
        type="text"
        name="player"
        placeholder="Search player"
        value="{player_filter}"
        style="padding: 8px; margin-right: 10px;"
    >

   <select
    name="rating"
    onchange="this.form.submit()"
    style="padding: 8px; margin-right: 10px;"
>
    <option value="" {"selected" if rating_filter == "" else ""}>All Ratings</option>
    <option value="BUY" {"selected" if rating_filter == "BUY" else ""}>BUY</option>
    <option value="FAIR" {"selected" if rating_filter == "FAIR" else ""}>FAIR</option>
    <option value="HIGH" {"selected" if rating_filter == "HIGH" else ""}>HIGH</option>
</select>
<label style="margin-right: 10px;">
    Parallel
    <select
        name="parallel"
        onchange="this.form.submit()"
        style="padding: 8px;"
    >
        <option value="">All Parallels</option>
        {''.join(
            f'<option value="{p}" {"selected" if p == parallel_filter else ""}>{p}</option>'
            for p in parallel_options
        )}
    </select>
</label>
<label style="margin-right: 10px;">
    Grade
    <select
        name="grade"
        onchange="this.form.submit()"
        style="padding: 8px;"
    >
        <option value="">All Grades</option>
        {''.join(
            f'<option value="{g}" {"selected" if g == grade_filter else ""}>{g}</option>'
            for g in grade_options
        )}
    </select>
</label>
<label style="margin-right: 10px;">
    Min Confidence
    <input
        type="number"
        name="min_confidence"
        value="{min_confidence}"
        min="0"
        max="100"
        style="padding: 8px; width: 80px;"
    >
</label>

<label style="margin-right: 10px;">
    Min Comps
    <input
        type="number"
        name="min_comps"
        value="{min_comps}"
        min="2"
        style="padding: 8px; width: 70px;"
    >
</label>

<label style="margin-right: 10px;">
    Min Discount %
    <input
        type="number"
        name="min_discount"
        value="{min_discount}"
        min="0"
        max="100"
        step="0.1"
        style="padding: 8px; width: 80px;"
    >
</label>
<button
    type="submit"
    style="padding: 8px 14px;"
>
    Apply
    </button>
<a
    href="/deals-dashboard-v2"
    style="
        display: inline-block;
        padding: 8px 14px;
        margin-left: 8px;
        border: 1px solid #999;
        text-decoration: none;
        color: #222;
        background: white;
    "
>
    Reset Filters
</a>
</form>
        <table>
            <tr>
                <th>Player</th>
                <th>Card</th>
                <th>Product</th>
                <th>Parallel</th>
                <th>Grade</th>
                <th>Total Cost</th>
                <th>Median</th>
                <th>Discount</th>
                <th>Comps</th>
                <th>Confidence</th>
                <th>Quality</th>
                <th>Rating</th>
                <th>eBay</th>
            </tr>
    """

    for deal in deals:
        rating_class = deal["deal_rating"].lower()

        html += f"""
            <tr>
                <td>{deal["player_name"]}</td>
                <td>{deal["card_year"]} #{deal["card_number"]}</td>
                <td>{deal["product"] or ""}</td>
                <td>{deal["parallel"] or "Base"}</td>
                <td>{(deal["grade_company"] + " " + str(deal["grade"])) if deal["grade_company"] and deal["grade"] is not None else "Raw"}</td>
                <td>${deal["total_cost"]:.2f}</td>
                <td>${deal["median_price"]:.2f}</td>
                <td>{deal["discount_percentage"]:.1f}%</td>
                <td>{deal["comparable_count"]}</td>
                <td>{deal["confidence_score"]}</td>
                <td>{deal["deal_quality_score"]:.1f}</td>
                <td class="{rating_class}">{deal["deal_rating"]}</td>
                <td>
                    <a href="{deal["listing_url"]}" target="_blank">
                        View
                    </a>
                </td>
            </tr>
        """

    html += """
        </table>
    </body>
    </html>
    """

    return html

   
