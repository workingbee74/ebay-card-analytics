import os
import hashlib
import base64
import requests
import psycopg
import zipfile
import re
import time
import json
import unicodedata
import base64
import urllib.parse
from flask import Flask, request, jsonify, redirect
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
CARDHEDGE_API_KEY = os.environ.get("CARDHEDGE_API_KEY", "")

def ensure_ebay_oauth_table():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ebay_oauth_tokens (
                    id INTEGER PRIMARY KEY,
                    refresh_token TEXT NOT NULL,
                    scope TEXT,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.commit()

NAV_HTML = """
<nav class="app-nav">
    <a href="/inventory">Inventory</a>
    <a href="/inventory/actions">Actions</a>
    <a href="/scan-card">Scan</a>
    <a href="/auction-watch">Auction Watch</a>
    <a href="/deals-dashboard-v2">Bowman Deals</a>
</nav>
"""

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

def normalize_ximilar_sport_result(ximilar_data):
    evidence = {
        "player_name": None,
        "card_year": None,
        "manufacturer": None,
        "product": None,
        "card_number": None,
        "parallel": None,
        "serial_number": None,
        "serial_numbered_to": None,
        "autograph": None,
        "grade_company": None,
        "grade": None,
        "best_match": None,
        "alternatives": [],
    }

    records = ximilar_data.get("records", [])

    if not records:
        return evidence

    front_record = records[0]
    front_objects = front_record.get("_objects", [])
    
    record = records[0]

    objects = record.get("_objects", [])
    
    if not objects:
        return evidence
    
    identification = objects[0].get("_identification", {})

    best_match = identification.get("best_match")
    alternatives = identification.get("alternatives", [])

    evidence["best_match"] = best_match
    evidence["alternatives"] = alternatives

    if best_match:
        evidence["player_name"] = best_match.get("name")

        year_value = (
            best_match.get("year")
            or best_match.get("season")
        )

        if year_value:
            try:
                evidence["card_year"] = int(year_value)
            except (TypeError, ValueError):
                pass

        company = best_match.get("company")

        if company:
            if "BOWMAN" in company.upper():
                evidence["manufacturer"] = "Bowman"
            elif "TOPPS" in company.upper():
                evidence["manufacturer"] = "Topps"
            else:
                evidence["manufacturer"] = company

        set_name = best_match.get("set_name")
        sub_set = best_match.get("sub_set")

        combined_product = " ".join(
            value
            for value in [set_name, sub_set]
            if value
        )

        parsed_product = parse_card_title(
            combined_product
        )

        evidence["product"] = (
            parsed_product.get("product")
            or set_name
        )

        evidence["parallel"] = (
            parsed_product.get("parallel")
            or sub_set
        )

        evidence["card_number"] = (
            best_match.get("card_number")
            or best_match.get("number")
        )

        serial_text = best_match.get("serial_number")

        if serial_text:
            serial_match = re.search(
                r"(\d{1,4})\s*/\s*(\d{1,4})",
                str(serial_text)
            )

            if serial_match:
                evidence["serial_number"] = int(
                    serial_match.group(1)
                )

                evidence["serial_numbered_to"] = int(
                    serial_match.group(2)
                )

    tags = (
        front_objects[0].get("_tags", {})
        if front_objects
        else {}
    )

    autograph_tags = tags.get("Autograph", [])

    if autograph_tags:
        strongest = max(
            autograph_tags,
            key=lambda item: item.get("prob", 0)
        )

        name = (
            strongest.get("name") or ""
        ).casefold()

        if name == "signed":
            evidence["autograph"] = True
        elif name == "not signed":
            evidence["autograph"] = False

    return evidence

def resolve_with_cardhedge(evidence):
    player_name = evidence.get("player_name")
    card_year = evidence.get("card_year")
    product = evidence.get("product")
    card_number = evidence.get("card_number")
    parallel = evidence.get("parallel")
    serial_numbered_to = evidence.get("serial_numbered_to")

    # Normalize Bowman card numbers such as BCP59 -> BCP-59
    normalized_card_number = card_number

    if normalized_card_number:
        normalized_card_number = re.sub(
            r"^([A-Z]{2,5})(\d+)$",
            r"\1-\2",
            normalized_card_number.upper()
        )

    query_parts = [
        str(card_year or ""),
        str(player_name or ""),
        str(product or ""),
        str(normalized_card_number or ""),
        str(parallel or ""),
    ]

    if serial_numbered_to:
        query_parts.append(
            f"/{serial_numbered_to}"
        )

    query = " ".join(
        part.strip()
        for part in query_parts
        if part and part.strip()
    )

    response = requests.post(
        "https://api.cardhedger.com/v1/cards/card-search",
        headers={
            "X-API-Key": CARDHEDGE_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "search": query,
            "category": "Baseball",
            "page": 1,
            "page_size": 20,
        },
        timeout=30,
    )

    if not response.ok:
        return {
            "success": False,
            "query": query,
            "http_status": response.status_code,
            "candidates": [],
        }

    data = response.json()

    candidates = data.get("cards", [])

    scored_candidates = []

    for card in candidates:
        score = 0

        candidate_player = (
            card.get("player") or ""
        ).casefold()

        candidate_number = (
            card.get("number") or ""
        ).upper()

        candidate_set = (
            card.get("set") or ""
        ).casefold()

        candidate_variant = (
            card.get("variant") or ""
        ).casefold()

        candidate_description = (
            card.get("description") or ""
        ).casefold()

        # Player is essential
        if player_name and (
            player_name.casefold()
            == candidate_player
        ):
            score += 35

        # Card number is extremely strong evidence
        if (
            normalized_card_number
            and normalized_card_number
            == candidate_number
        ):
            score += 30

        # Correct year
        if card_year and str(card_year) in candidate_description:
            score += 15

        # Product/set family
        if product and product.casefold() in candidate_set:
            score += 10

        # Exact parallel
        if parallel and parallel.casefold() == candidate_variant:
            score += 30
        elif parallel and parallel.casefold() in candidate_description:
            score += 20

        scored_candidates.append({
            "score": score,
            "card": card,
        })

    scored_candidates.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    best = (
        scored_candidates[0]
        if scored_candidates
        else None
    )

    # Do not auto-resolve weak catalog matches.
    # Weak matches should go to manual review instead.
    
    minimum_resolver_score = 70
    
    if best and best["score"] < minimum_resolver_score:
        best = None
        
    return {
        "success": True,
        "query": query,
        "best": best,
        "candidates": scored_candidates[:5],
    }

    if best:
        expected_player = (
            evidence.get("player_name") or ""
        ).casefold().strip()
    
        resolved_player = (
            best["card"].get("player") or ""
        ).casefold().strip()
    
        if (
            expected_player
            and resolved_player
            and expected_player != resolved_player
        ):
            best = None

    identification = objects[0].get("_identification", {})

    best_match = identification.get("best_match")
    alternatives = identification.get("alternatives", [])

    evidence["best_match"] = best_match
    evidence["alternatives"] = alternatives

    if best_match:
        evidence["player_name"] = best_match.get("name")

        year_value = (
            best_match.get("year")
            or best_match.get("season")
        )

        if year_value:
            try:
                evidence["card_year"] = int(year_value)
            except (TypeError, ValueError):
                pass

        company = best_match.get("company")

        if company:
            if "BOWMAN" in company.upper():
                evidence["manufacturer"] = "Bowman"
            elif "TOPPS" in company.upper():
                evidence["manufacturer"] = "Topps"
            else:
                evidence["manufacturer"] = company

        set_name = best_match.get("set_name")
        sub_set = best_match.get("sub_set")

        combined_product = " ".join(
            value
            for value in [set_name, sub_set]
            if value
        )

        parsed_product = parse_card_title(
            combined_product
        )

        evidence["product"] = (
            parsed_product.get("product")
            or set_name
        )

        evidence["parallel"] = (
            parsed_product.get("parallel")
            or sub_set
        )

        evidence["card_number"] = (
            best_match.get("card_number")
            or best_match.get("number")
        )

        serial_text = best_match.get("serial_number")

        if serial_text:
            serial_match = re.search(
                r"(\d{1,4})\s*/\s*(\d{1,4})",
                str(serial_text)
            )

            if serial_match:
                evidence["serial_number"] = int(
                    serial_match.group(1)
                )

                evidence["serial_numbered_to"] = int(
                    serial_match.group(2)
                )

    tags = objects[0].get("_tags", {})

    autograph_tags = tags.get("Autograph", [])

    if autograph_tags:
        strongest = max(
            autograph_tags,
            key=lambda item: item.get("prob", 0)
        )

        name = (
            strongest.get("name") or ""
        ).casefold()

        if name == "signed":
            evidence["autograph"] = True

        elif name == "not signed":
            evidence["autograph"] = False

    return evidence

def get_inventory_market_data(
    cardhedge_id,
    grade_company=None,
    grade=None
):
    if not cardhedge_id:
        return {
            "market_value": None,
            "sales_7day": 0,
            "sales_30day": 0,
            "market_gain": None,
            "grade_used": None,
        }

    response = requests.post(
        "https://api.cardhedger.com/v1/cards/card-details",
        headers={
            "X-API-Key": CARDHEDGE_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "card_id": cardhedge_id
        },
        timeout=30,
    )

    if not response.ok:
        return {
            "market_value": None,
            "sales_7day": 0,
            "sales_30day": 0,
            "market_gain": None,
            "grade_used": None,
        }

    data = response.json()
    cards = data.get("cards", [])

    if not cards:
        return {
            "market_value": None,
            "sales_7day": 0,
            "sales_30day": 0,
            "market_gain": None,
            "grade_used": None,
        }

    card = cards[0]
    prices = card.get("prices", [])

    if grade_company and grade is not None:
        grade_value = float(grade)

        if grade_value.is_integer():
            grade_text = str(int(grade_value))
        else:
            grade_text = str(grade_value)

        target_grade = (
            f"{grade_company.upper()} {grade_text}"
        )
    else:
        target_grade = "Raw"

    market_value = None
    grade_used = None

    for price_record in prices:
        record_grade = (
            price_record.get("grade") or ""
        ).strip()

        if record_grade.casefold() == target_grade.casefold():
            try:
                market_value = float(
                    price_record.get("price")
                )
                grade_used = record_grade
            except (TypeError, ValueError):
                pass

            break

    return {
        "market_value": market_value,
        "sales_7day": card.get("7 Day Sales", 0) or 0,
        "sales_30day": card.get("30 Day Sales", 0) or 0,
        "market_gain": card.get("gain"),
        "grade_used": grade_used,
    }

def get_cardhedge_price_trend(
    cardhedge_id,
    grade_company=None,
    grade=None
):
    if not cardhedge_id:
        return {
            "trend": "UNKNOWN",
            "trend_pct": None,
            "trend_confidence": "LOW",
            "history_points": 0,
        }

    if grade_company and grade is not None:
        grade_value = float(grade)

        if grade_value.is_integer():
            grade_text = str(int(grade_value))
        else:
            grade_text = str(grade_value)

        target_grade = (
            f"{grade_company.upper()} {grade_text}"
        )
    else:
        target_grade = "Raw"

    response = requests.post(
        "https://api.cardhedger.com/v1/cards/prices-by-card",
        headers={
            "X-API-Key": CARDHEDGE_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "card_id": cardhedge_id,
            "grade": target_grade,
            "days": 30,
        },
        timeout=30,
    )

    if not response.ok:
        return {
            "trend": "UNKNOWN",
            "trend_pct": None,
            "trend_confidence": "LOW",
            "history_points": 0,
        }

    data = response.json()
    prices = data.get("prices", [])

    valid_points = []

    for item in prices:
        try:
            price = float(item.get("price"))
            date = item.get("closing_date")

            if date:
                valid_points.append({
                    "date": date,
                    "price": price,
                })

        except (TypeError, ValueError):
            continue

    valid_points.sort(
        key=lambda item: item["date"]
    )

    if len(valid_points) < 2:
        return {
            "trend": "UNKNOWN",
            "trend_pct": None,
            "trend_confidence": "LOW",
            "history_points": len(valid_points),
        }

    start_price = valid_points[0]["price"]
    end_price = valid_points[-1]["price"]

    if start_price <= 0:
        trend_pct = None
    else:
        trend_pct = (
            (end_price - start_price)
            / start_price
            * 100
        )

    if trend_pct is None:
        trend = "UNKNOWN"

    elif trend_pct >= 5:
        trend = "RISING"

    elif trend_pct <= -5:
        trend = "FALLING"

    else:
        trend = "FLAT"

    if len(valid_points) >= 8:
        trend_confidence = "HIGH"

    elif len(valid_points) >= 4:
        trend_confidence = "MEDIUM"

    else:
        trend_confidence = "LOW"

    return {
        "trend": trend,
        "trend_pct": trend_pct,
        "trend_confidence": trend_confidence,
        "history_points": len(valid_points),
        "start_price": start_price,
        "end_price": end_price,
    }

def calculate_disposition(
    market_value,
    purchase_price,
    sales_7day,
    sales_30day,
    prospect_card,
    first_bowman,
    autograph,
    grade_company,
    grade,
    serial_numbered_to,
    trend_pct
):
    score = 0
    reasons = []

    # Scarcity
    if serial_numbered_to:
        if serial_numbered_to <= 25:
            score += 3
            reasons.append("Very scarce numbered parallel")

        elif serial_numbered_to <= 75:
            score += 2
            reasons.append("Low-print numbered parallel")

        elif serial_numbered_to <= 150:
            score += 1
            reasons.append("Numbered parallel")

    # Prospect upside
    if prospect_card:
        score += 2
        reasons.append("Prospect upside")

    # 1st Bowman
    if first_bowman:
        score += 2
        reasons.append("1st Bowman")

    # Autograph
    if autograph:
        score += 2
        reasons.append("Autograph")

    # Premium grade
    if (
        grade_company
        and grade is not None
        and float(grade) >= 10
    ):
        score += 1
        reasons.append("Gem-mint grade")

    # Liquidity
    if sales_30day >= 10:
        liquidity = "HIGH"
        score -= 2
        reasons.append("Strong recent liquidity")

    elif sales_30day >= 4:
        liquidity = "MODERATE"
        score -= 1
        reasons.append("Moderate recent liquidity")

    else:
        liquidity = "LOW"
        score += 1
        reasons.append("Thin recent market")

    # Position vs current market
    gain_loss_pct = None

    if (
        market_value is not None
        and purchase_price is not None
        and float(purchase_price) > 0
    ):
        gain_loss_pct = (
            (
                float(market_value)
                - float(purchase_price)
            )
            / float(purchase_price)
            * 100
        )

    # Decide
    # Decide
    if score >= 4:
        action = "HOLD"
    
    elif (
        gain_loss_pct is not None
        and gain_loss_pct < 0
        and trend_pct is not None
        and trend_pct >= 0
    ):
        action = "HOLD"
        reasons.append("Below cost, but market trend is stable or improving")
    
    elif liquidity == "HIGH":
        action = "SELL - AUCTION"
    
    elif liquidity == "MODERATE":
        action = "SELL - BIN + BEST OFFER"
    
    else:
        action = "HOLD"
        return {
            "action": action,
            "score": score,
            "liquidity": liquidity,
            "reasons": reasons,
            "gain_loss_pct": gain_loss_pct,
        }




@app.route("/ebay/oauth/db-scope", methods=["GET"])
def ebay_oauth_db_scope():
    ensure_ebay_oauth_table()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT scope, updated_at
                FROM ebay_oauth_tokens
                WHERE id = 1
            """)

            row = cur.fetchone()

    return jsonify({
        "scope": row[0] if row else None,
        "updated_at": row[1].isoformat() if row and row[1] else None,
    })

@app.route("/ebay/whoami", methods=["GET"])
def ebay_whoami():
    ebay_token = get_ebay_user_access_token()

    response = requests.get(
        "https://apiz.ebay.com/commerce/identity/v1/user/",
        headers={
            "Authorization": f"Bearer {ebay_token}",
            "Accept": "application/json",
        },
        timeout=30,
    )

    return jsonify({
        "status_code": response.status_code,
        "user": (
            response.json()
            if response.content
            else {}
        ),
    })

@app.route("/ebay/add-location-to-offer/<offer_id>", methods=["GET"])
def ebay_add_location_to_offer(offer_id):
    ebay_token = get_ebay_user_access_token()

    headers = {
        "Authorization": f"Bearer {ebay_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Content-Language": "en-US",
    }

    # Get the complete existing offer first
    get_response = requests.get(
        f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}",
        headers=headers,
        timeout=30,
    )

    if get_response.status_code != 200:
        return jsonify({
            "success": False,
            "stage": "get_offer",
            "status_code": get_response.status_code,
            "response": get_response.json() if get_response.content else {},
        }), 400

    offer_update = get_response.json()

    # Remove read-only response fields
    offer_update.pop("offerId", None)
    offer_update.pop("status", None)

    # Attach our inventory location
    offer_update["merchantLocationKey"] = "jackstation-main"

    update_response = requests.put(
        f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}",
        headers=headers,
        json=offer_update,
        timeout=30,
    )

    return jsonify({
        "success": update_response.status_code in (200, 204),
        "offer_id": offer_id,
        "status_code": update_response.status_code,
        "response": (
            update_response.json()
            if update_response.content
            else {}
        ),
    })


@app.route("/ebay/create-test-location", methods=["GET"])
def ebay_create_test_location():
    ebay_token = get_ebay_user_access_token()

    merchant_location_key = "jackstation-main"

    response = requests.post(
        f"https://api.ebay.com/sell/inventory/v1/location/{merchant_location_key}",
        headers={
            "Authorization": f"Bearer {ebay_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Content-Language": "en-US",
        },
        json={
            "name": "JackStation Main Inventory",
            "location": {
                "address": {
                    "postalCode": "76092",
                    "country": "US",
                }
            },
            "merchantLocationStatus": "ENABLED",
        },
        timeout=30,
    )

    return jsonify({
        "success": response.status_code in (200, 201, 204),
        "status_code": response.status_code,
        "merchant_location_key": merchant_location_key,
        "response": response.json() if response.content else {},
    })

@app.route("/ebay/publish-offer/<offer_id>", methods=["GET"])
def ebay_publish_offer(offer_id):
    ebay_token = get_ebay_user_access_token()

    response = requests.post(
        f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}/publish",
        headers={
            "Authorization": f"Bearer {ebay_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=30,
    )

    response_json = (
        response.json()
        if response.content
        else {}
    )

    return jsonify({
        "success": response.status_code in (200, 201),
        "offer_id": offer_id,
        "status_code": response.status_code,
        "response": response_json,
    })

@app.route("/ebay/withdraw-offer/<offer_id>", methods=["GET"])
def ebay_withdraw_offer(offer_id):
    ebay_token = get_ebay_user_access_token()

    response = requests.post(
        f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}/withdraw",
        headers={
            "Authorization": f"Bearer {ebay_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=30,
    )

    response_json = (
        response.json()
        if response.content
        else {}
    )

    return jsonify({
        "success": response.status_code in (200, 204),
        "offer_id": offer_id,
        "status_code": response.status_code,
        "response": response_json,
    })


@app.route("/ebay/oauth/introspect-test")
def ebay_oauth_introspect_test():
    token = get_ebay_user_access_token()

    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")

    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(
        credentials.encode("utf-8")
    ).decode("utf-8")

    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token/introspect",
        headers={
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "token": token,
            "token_type_hint": "access_token",
        },
        timeout=30,
    )

    data = response.json() if response.content else {}

    return jsonify({
        "status_code": response.status_code,
        "active": data.get("active"),
        "scope": data.get("scope"),
        "token_type": data.get("token_type"),
    })


@app.route("/ebay/oauth/introspect-refresh")
def ebay_oauth_introspect_refresh():
    ensure_ebay_oauth_table()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT refresh_token
                FROM ebay_oauth_tokens
                WHERE id = 1
            """)
            row = cur.fetchone()

    if not row or not row[0]:
        return jsonify({
            "success": False,
            "error": "No saved refresh token"
        }), 400

    refresh_token = row[0]

    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")

    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(
        credentials.encode("utf-8")
    ).decode("utf-8")

    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token/introspect",
        headers={
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "token": refresh_token,
            "token_type_hint": "refresh_token",
        },
        timeout=30,
    )

    data = response.json() if response.content else {}

    return jsonify({
        "status_code": response.status_code,
        "active": data.get("active"),
        "scope": data.get("scope"),
        "token_type": data.get("token_type"),
    })


@app.route("/ebay/oauth/manual")
def ebay_oauth_manual():
    return """
    <h2>Complete eBay Authorization</h2>
    <form method="POST" action="/ebay/oauth/exchange-code">
        <label>Authorization code:</label><br><br>
        <input
            type="password"
            name="code"
            style="width:700px;"
            autocomplete="off"
        ><br><br>
        <button type="submit">Exchange Code</button>
    </form>
    """

@app.route("/ebay/oauth/exchange-code", methods=["GET", "POST"])
def ebay_exchange_code():
    auth_code = urllib.parse.unquote(
        request.values.get("code", "").strip()
    )
    if not auth_code:
        return jsonify({
            "success": False,
            "error": "Missing authorization code"
        }), 400

    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")
    runame = os.environ.get("EBAY_RUNAME")

    credentials = f"{client_id}:{client_secret}"
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
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": runame,
        },
        timeout=30,
    )

    token_json = token_response.json() if token_response.content else {}


    access_token = token_json.get("access_token")
    
    introspect_data = {}
    
    if access_token:
        introspect_response = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token/introspect",
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "token": access_token,
                "token_type_hint": "access_token",
            },
            timeout=30,
        )
    
        introspect_data = (
            introspect_response.json()
            if introspect_response.content
            else {}
        )
    
    if token_response.ok and token_json.get("refresh_token"):
        ensure_ebay_oauth_table()
    
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO ebay_oauth_tokens (
                        id,
                        refresh_token,
                        scope,
                        updated_at
                    )
                    VALUES (
                        1,
                        %s,
                        %s,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (id)
                    DO UPDATE SET
                        refresh_token = EXCLUDED.refresh_token,
                        scope = EXCLUDED.scope,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    token_json["refresh_token"],
                    token_json.get("scope"),
                ))
    
                conn.commit()
            
        
                return jsonify({
                    "success": token_response.ok,
                    "status_code": token_response.status_code,
                    "has_access_token": bool(token_json.get("access_token")),
                    "has_refresh_token": bool(token_json.get("refresh_token")),
                    "original_access_token_scope": introspect_data.get("scope"),
                    "expires_in": token_json.get("expires_in"),
                    "refresh_token_expires_in": token_json.get("refresh_token_expires_in"),
                    "error": token_json.get("error"),
                    "error_description": token_json.get("error_description"),
                })

@app.route("/ebay/create-return-policy", methods=["GET"])
def ebay_create_return_policy():
    token = get_ebay_user_access_token()
    response = requests.post(
        "https://api.ebay.com/sell/account/v1/return_policy",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "name": "JackStation No Returns",
            "description": "No returns accepted for JackStation listings",
            "marketplaceId": "EBAY_US",
            "merchantLocationKey": "jackstation-main",
            "categoryTypes": [
                {
                    "name": "ALL_EXCLUDING_MOTORS_VEHICLES"
                }
            ],
            "returnsAccepted": False
        },
                    
                timeout=30,
            )
        
    return jsonify({
        "status_code": response.status_code,
        "response": response.json() if response.content else {}
    })

@app.route("/ebay/create-payment-policy", methods=["GET"])
def ebay_create_payment_policy():
    token = os.environ.get("EBAY_USER_ACCESS_TOKEN")

    response = requests.post(
        "https://api.ebay.com/sell/account/v1/payment_policy",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "name": "JackStation Payment Policy",
            "description": "Standard payment policy for JackStation listings",
            "marketplaceId": "EBAY_US",
            "categoryTypes": [
                {
                    "name": "ALL_EXCLUDING_MOTORS_VEHICLES"
                }
            ]
        },
        timeout=30,
    )

    return jsonify({
        "status_code": response.status_code,
        "response": response.json() if response.content else {}
    })

@app.route("/ebay/shipping-services", methods=["GET"])
def ebay_shipping_services():
    token = os.environ.get("EBAY_USER_ACCESS_TOKEN")

    xml_body = """<?xml version="1.0" encoding="utf-8"?>
<GeteBayDetailsRequest xmlns="urn:ebay:apis:eBLBaseComponents">
    <RequesterCredentials>
        <eBayAuthToken>{}</eBayAuthToken>
    </RequesterCredentials>
    <DetailName>ShippingServiceDetails</DetailName>
</GeteBayDetailsRequest>
""".format(token)

    response = requests.post(
        "https://api.ebay.com/ws/api.dll",
        headers={
            "X-EBAY-API-CALL-NAME": "GeteBayDetails",
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-COMPATIBILITY-LEVEL": "1475",
            "Content-Type": "text/xml",
        },
        data=xml_body,
        timeout=30,
    )

    return response.text, response.status_code, {
        "Content-Type": "text/xml"
    }

@app.route("/ebay/create-fulfillment-policy", methods=["GET"])
def ebay_create_fulfillment_policy():
    token = os.environ.get("EBAY_USER_ACCESS_TOKEN")

    response = requests.post(
        "https://api.ebay.com/sell/account/v1/fulfillment_policy",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "name": "JackStation Graded Card Shipping",
            "description": "Buyer-paid shipping for graded trading cards",
            "marketplaceId": "EBAY_US",
            "categoryTypes": [
                {
                    "name": "ALL_EXCLUDING_MOTORS_VEHICLES"
                }
            ],
            "handlingTime": {
                "value": 1,
                "unit": "DAY"
            },
            "shippingOptions": [
                {
                    "optionType": "DOMESTIC",
                    "costType": "FLAT_RATE",
                    "shippingServices": [
                        {
                            "shippingCarrierCode": "USPS",
                            "shippingServiceCode": "USPSParcel",
                            "shippingCost": {
                                "value": "5.50",
                                "currency": "USD"
                            },
                            "sortOrder": 1
                        }
                    ]
                }
            ]
        },
        timeout=30,
    )

    return jsonify({
        "status_code": response.status_code,
        "response": response.json() if response.content else {}
    })

@app.route("/privacy-policy.html", methods=["GET"])
def privacy_policy_html():
    return privacy_policy()

@app.route("/ebay/opt-in-business-policies", methods=["GET"])
def ebay_opt_in_business_policies():
    token = os.environ.get("EBAY_USER_ACCESS_TOKEN")

    response = requests.post(
        "https://api.ebay.com/sell/account/v1/program/opt_in",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "programType": "SELLING_POLICY_MANAGEMENT"
        },
        timeout=30,
    )

    return jsonify({
        "status_code": response.status_code,
        "response": response.json() if response.content else {}
    })

@app.route("/cardhedge-history-test", methods=["GET"])
def calculate_action_priority(
    disposition_action,
    disposition_liquidity,
    gain_loss_pct,
    price_trend,
    price_trend_confidence
):
    action_priority = 0

    if disposition_action and "AUCTION" in disposition_action:
        action_priority += 90

    elif disposition_action and "BIN" in disposition_action:
        action_priority += 80

    elif disposition_action == "REVIEW":
        action_priority += 70

    elif disposition_action == "HOLD":
        action_priority += 30

    # Strong liquidity makes an action more executable.
    if disposition_liquidity == "HIGH":
        action_priority += 15

    elif disposition_liquidity == "MODERATE":
        action_priority += 8

    # Large market movement versus cost deserves attention.
    if gain_loss_pct is not None:
        if gain_loss_pct >= 25:
            action_priority += 15

        elif gain_loss_pct >= 10:
            action_priority += 8

        elif gain_loss_pct <= -25:
            action_priority += 10

    # Trend strength adds urgency.
    if price_trend == "FALLING":
        if price_trend_confidence == "HIGH":
            action_priority += 20

        elif price_trend_confidence == "MEDIUM":
            action_priority += 12

        else:
            action_priority += 6

    elif price_trend == "RISING":
        if price_trend_confidence == "HIGH":
            action_priority += 15

        elif price_trend_confidence == "MEDIUM":
            action_priority += 10

        else:
            action_priority += 5

    return min(action_priority, 100)


def calculate_auction_start_price(
    market_value,
    purchase_price=None,
    serial_numbered_to=None,
    grade_company=None,
    grade=None,
    price_trend=None,
    trend_confidence=None
):
    if market_value is None:
        return None

    market_value = float(market_value)

    if market_value <= 0:
        return None

    # Base protection level.
    start_pct = 0.70

    # Scarcer cards deserve stronger downside protection.
    if serial_numbered_to:
        if serial_numbered_to <= 25:
            start_pct += 0.10
        elif serial_numbered_to <= 50:
            start_pct += 0.07
        elif serial_numbered_to <= 99:
            start_pct += 0.05
        elif serial_numbered_to <= 150:
            start_pct += 0.03

    # High-grade cards generally deserve a stronger opening floor.
    if grade_company and grade is not None:
        try:
            numeric_grade = float(grade)

            if numeric_grade >= 10:
                start_pct += 0.05
            elif numeric_grade >= 9:
                start_pct += 0.03
        except (TypeError, ValueError):
            pass

    # Protect more aggressively when the market is falling.
    if price_trend == "FALLING":
        if trend_confidence == "HIGH":
            start_pct += 0.07
        elif trend_confidence == "MEDIUM":
            start_pct += 0.04
        else:
            start_pct += 0.02

    # Strong rising markets can tolerate a slightly lower opening bid
    # to encourage bidding activity.
    elif price_trend == "RISING":
        if trend_confidence == "HIGH":
            start_pct -= 0.05
        elif trend_confidence == "MEDIUM":
            start_pct -= 0.03

    # Keep the model within sensible boundaries.
    start_pct = max(0.60, min(start_pct, 0.90))

    recommended_start = market_value * start_pct

    

    # eBay-friendly pricing.
    recommended_start = round(recommended_start, 2)

    return max(0.99, recommended_start)


@app.route("/ebay/policies", methods=["GET"])
def ebay_policies():
    token = get_ebay_user_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    base_url = "https://api.ebay.com/sell/account/v1"

    fulfillment = requests.get(
        f"{base_url}/fulfillment_policy",
        headers=headers,
        params={"marketplace_id": "EBAY_US"},
        timeout=30,
    )

    payment = requests.get(
        f"{base_url}/payment_policy",
        headers=headers,
        params={"marketplace_id": "EBAY_US"},
        timeout=30,
    )

    returns = requests.get(
        f"{base_url}/return_policy",
        headers=headers,
        params={"marketplace_id": "EBAY_US"},
        timeout=30,
    )

    return jsonify({
        "fulfillment_status": fulfillment.status_code,
        "fulfillment": fulfillment.json() if fulfillment.content else {},
        "payment_status": payment.status_code,
        "payment": payment.json() if payment.content else {},
        "return_status": returns.status_code,
        "returns": returns.json() if returns.content else {},
    })

@app.route("/cardhedge-history-test", methods=["GET"])
def cardhedge_history_test():
    card_id = request.args.get("card_id")
    grade = request.args.get("grade", "PSA 10")

    if not card_id:
        return jsonify({
            "success": False,
            "error": "card_id is required"
        }), 400

    response = requests.post(
        "https://api.cardhedger.com/v1/cards/prices-by-card",
        headers={
            "X-API-Key": CARDHEDGE_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "card_id": card_id,
            "grade": grade,
            "days": 30,
        },
        timeout=30,
    )

    try:
        data = response.json()
    except ValueError:
        data = {
            "error": response.text
        }

    return jsonify({
        "success": response.ok,
        "http_status": response.status_code,
        "card_id": card_id,
        "grade": grade,
        "cardhedge": data,
    }), response.status_code

@app.route("/cardhedge-scan-test", methods=["GET", "POST"])
def cardhedge_scan_test():
    if request.method == "GET":
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Card Hedge Scanner Test</title>
            <meta
                name="viewport"
                content="width=device-width, initial-scale=1, viewport-fit=cover"
            >

            <style>
                body {
                    margin: 0;
                    background: #111;
                    color: white;
                    font-family: Arial, sans-serif;
                    text-align: center;
                }

                h1 {
                    margin: 16px 0 10px;
                    font-size: 26px;
                }

                #camera-wrap {
                    position: relative;
                    width: 100%;
                    max-width: 650px;
                    margin: 0 auto;
                    background: black;
                }

                video {
                    display: block;
                    width: 100%;
                }

                #guide {
                    position: absolute;
                    top: 8%;
                    left: 15%;
                    width: 70%;
                    height: 84%;
                    border: 3px solid white;
                    border-radius: 14px;
                    box-sizing: border-box;
                    pointer-events: none;
                }

                #capture-button {
                    width: calc(100% - 40px);
                    max-width: 610px;
                    margin: 18px auto 8px;
                    padding: 18px;
                    border: 0;
                    border-radius: 12px;
                    background: #2563eb;
                    color: white;
                    font-size: 22px;
                    font-weight: bold;
                }

                #status {
                    padding: 10px 20px 24px;
                    font-size: 16px;
                }

                canvas {
                    display: none;
                }

                pre {
                    text-align: left;
                    white-space: pre-wrap;
                    word-break: break-word;
                    background: white;
                    color: black;
                    padding: 20px;
                    margin: 0;
                    min-height: 100vh;
                }
            </style>
        </head>

        <body>
            <h1>Card Hedge Scanner Test</h1>

            <div id="camera-wrap">
                <video
                    id="video"
                    autoplay
                    playsinline
                    muted
                ></video>

                <div id="guide"></div>
            </div>

            <button id="capture-button">
                Capture Card
            </button>

            <div id="status">
                Center the card inside the frame.
            </div>

            <hr style="margin:28px 0;">
            
            <div style="max-width:500px;margin:0 auto;text-align:left;">
                <h2>Upload Card Photos</h2>
            
                <form method="POST"
                      action="/scan-card"
                      enctype="multipart/form-data">
            
                    <label><strong>Front Photo</strong></label><br>
                    <input
                        type="file"
                        name="front_image"
                        accept="image/*"
                        required
                    >
            
                    <br><br>
            
                    <label><strong>Back Photo</strong></label><br>
                    <input
                        type="file"
                        name="back_image"
                        accept="image/*"
                        required
                    >
            
                    <br><br>
            
                    <button type="submit">
                        Identify Uploaded Card
                    </button>
            
                </form>
            </div>

            <canvas id="canvas"></canvas>

            <script>
                const video = document.getElementById("video");
                const canvas = document.getElementById("canvas");
                const button = document.getElementById("capture-button");
                const status = document.getElementById("status");

                async function startCamera() {
                    try {
                        const stream =
                            await navigator.mediaDevices.getUserMedia({
                                video: {
                                    facingMode: {
                                        ideal: "environment"
                                    }
                                },
                                audio: false
                            });

                        video.srcObject = stream;
                    } catch (error) {
                        status.textContent =
                            "Camera unavailable: " + error.message;
                    }
                }

                button.addEventListener("click", async () => {
                    if (!video.videoWidth) {
                        status.textContent =
                            "Camera is not ready yet.";
                        return;
                    }

                    button.disabled = true;
                    status.textContent =
                        "Identifying card with Card Hedge...";

                    canvas.width = video.videoWidth;
                    canvas.height = video.videoHeight;

                    const ctx = canvas.getContext("2d");

                    ctx.drawImage(
                        video,
                        0,
                        0,
                        canvas.width,
                        canvas.height
                    );

                    canvas.toBlob(
                        async (blob) => {
                            const formData = new FormData();

                            formData.append(
                                "card_image",
                                blob,
                                "card.jpg"
                            );

                            const response =
                                await fetch(
                                    "/cardhedge-scan-test",
                                    {
                                        method: "POST",
                                        body: formData
                                    }
                                );

                            const html =
                                await response.text();
                            
                            document.open();
                            document.write(html);
                            document.close();
                        },
                        "image/jpeg",
                        0.92
                    );
                });

                startCamera();
            </script>
        </body>
        </html>
        """

    image = request.files.get("card_image")

    if not image:
        return jsonify({
            "success": False,
            "error": "No image received"
        }), 400

    image_bytes = image.read()

    image_base64 = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    response = requests.post(
        "https://api.cardhedger.com/v1/cards/image-match",
        headers={
            "X-API-Key": CARDHEDGE_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "image_base64": image_base64,
            "k": 10,
        },
        timeout=60,
    )

    try:
        data = response.json()
    except ValueError:
        data = {
            "error": response.text
        }

    return jsonify({
        "success": response.ok,
        "http_status": response.status_code,
        "cardhedge": data,
    }), response.status_code


@app.route("/cardhedge-test", methods=["GET"])
def cardhedge_test():
    query = request.args.get(
        "q",
        "2025 Leo De Vries Bowman Chrome ECP59 Aqua X-Fractor"
    )

    response = requests.post(
        "https://api.cardhedger.com/v1/cards/card-search",
        headers={
            "X-API-Key": CARDHEDGE_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "search": query,
            "category": "Baseball",
            "page": 1,
            "page_size": 20,
        },
        timeout=30,
    )

    try:
        data = response.json()
    except ValueError:
        data = {
            "error": response.text
        }

    return jsonify({
        "success": response.ok,
        "http_status": response.status_code,
        "query": query,
        "cardhedge": data,
    }), response.status_code

@app.route("/cardhedge-image-test", methods=["POST"])
def cardhedge_image_test():
    import base64

    if "image" not in request.files:
        return {"success": False, "error": "No image uploaded"}, 400

    image = request.files["image"]
    image_bytes = image.read()
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    response = requests.post(
        "https://api.cardhedger.com/v1/cards/image-match",
        headers={
            "X-API-Key": CARDHEDGE_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "image_base64": image_base64,
            "k": 10
        },
        timeout=60,
    )

    return response.json(), response.status_code


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
            "scope": (
                "https://api.ebay.com/oauth/api_scope "
                "https://api.ebay.com/oauth/api_scope/sell.account"
            ),
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

                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS inventory_cards (
                            id BIGSERIAL PRIMARY KEY,
                    
                            player_name TEXT,
                            card_year INTEGER,
                            manufacturer TEXT DEFAULT 'Bowman',
                            product TEXT,
                            card_number TEXT,
                    
                            first_bowman BOOLEAN DEFAULT FALSE,
                            prospect_card BOOLEAN DEFAULT FALSE,
                    
                            parallel TEXT,
                            serial_numbered_to INTEGER,
                            serial_number INTEGER,
                    
                            autograph BOOLEAN DEFAULT FALSE,
                            rookie_card BOOLEAN DEFAULT FALSE,
                    
                            grade_company TEXT,
                            grade NUMERIC(3,1),
                    
                            purchase_price NUMERIC(12,2),
                            purchase_date DATE,
                            purchase_source TEXT,
                    
                            image_url TEXT,
                    
                            notes TEXT,
                    
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                    """)

                    cur.execute("""
                        ALTER TABLE inventory_cards
                        ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1,
                        ADD COLUMN IF NOT EXISTS condition TEXT,
                        ADD COLUMN IF NOT EXISTS storage_location TEXT,
                        ADD COLUMN IF NOT EXISTS scanner_source TEXT,
                        ADD COLUMN IF NOT EXISTS scanner_confidence NUMERIC(5,2),
                        ADD COLUMN IF NOT EXISTS external_card_id TEXT,
                        ADD COLUMN IF NOT EXISTS cdp_sku TEXT,
                        ADD COLUMN IF NOT EXISTS front_image_url TEXT,
                        ADD COLUMN IF NOT EXISTS back_image_url TEXT
                        ADD COLUMN IF NOT EXISTS market_value NUMERIC(12,2),
                        ADD COLUMN IF NOT EXISTS price_trend TEXT,
                        ADD COLUMN IF NOT EXISTS trend_pct NUMERIC(8,2),
                        ADD COLUMN IF NOT EXISTS trend_confidence TEXT,
                        ADD COLUMN IF NOT EXISTS disposition_action TEXT,
                        ADD COLUMN IF NOT EXISTS action_priority INTEGER,
                        ADD COLUMN IF NOT EXISTS market_updated_at TIMESTAMP;
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

        .app-nav {
            position: sticky;
            top: 0;
            z-index: 1000;
            display: flex;
            gap: 6px;
            padding: 10px 16px;
            background: white;
            border-bottom: 1px solid #e5e7eb;
            overflow-x: auto;
            white-space: nowrap;
        }
        
        .app-nav a {
            display: inline-block;
            padding: 9px 12px;
            color: #374151;
            text-decoration: none;
            font-size: 14px;
            font-weight: bold;
            border-radius: 8px;
        }
        
        .app-nav a:hover {
            background: #f3f4f6;
            color: #111;
        }
        
            body {
                font-family: Arial, sans-serif;
                background: #f4f5f7;
                margin: 0;
                padding: 10px;
            }

            h1 {
                font-size: 28px;
                margin-bottom: 5px;
            }

            .subtitle {
                color: #555;
                margin-bottom: 10px;
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
    {NAV_HTML}
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
    html = html.replace("{NAV_HTML}", NAV_HTML)
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

        <h1>Action Queue</h1>

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
            <meta
                name="viewport"
                content="width=device-width, initial-scale=1, viewport-fit=cover"
            >

            <style>

            .app-nav {
                position: sticky;
                top: 0;
                z-index: 1000;
                display: flex;
                gap: 6px;
                padding: 10px 16px;
                background: white;
                border-bottom: 1px solid #e5e7eb;
                overflow-x: auto;
                white-space: nowrap;
            }
            
            .app-nav a {
                display: inline-block;
                padding: 9px 12px;
                color: #374151;
                text-decoration: none;
                font-size: 14px;
                font-weight: bold;
                border-radius: 8px;
            }
            
            .app-nav a:hover {
                background: #f3f4f6;
                color: #111;
            }
    
                body {
                    margin: 0;
                    background: #111;
                    color: white;
                    font-family: Arial, sans-serif;
                    text-align: center;
                }

                h1 {
                    margin: 16px 0 10px;
                    font-size: 26px;
                }

                #camera-wrap {
                    position: relative;
                    width: 100%;
                    max-width: 650px;
                    margin: 0 auto;
                    background: black;
                }

                video {
                    display: block;
                    width: 100%;
                }

                #guide {
                    position: absolute;
                    top: 8%;
                    left: 15%;
                    width: 70%;
                    height: 84%;
                    border: 3px solid white;
                    border-radius: 14px;
                    box-sizing: border-box;
                    pointer-events: none;
                }

                #capture-button {
                    width: calc(100% - 40px);
                    max-width: 610px;
                    margin: 18px auto 8px;
                    padding: 18px;
                    border: 0;
                    border-radius: 12px;
                    background: #2563eb;
                    color: white;
                    font-size: 22px;
                    font-weight: bold;
                }

                #status {
                    padding: 10px 20px 24px;
                    font-size: 16px;
                }

                canvas {
                    display: none;
                }

                pre {
                    text-align: left;
                    white-space: pre-wrap;
                    word-break: break-word;
                    background: white;
                    color: black;
                    padding: 20px;
                    margin: 0;
                    min-height: 100vh;
                }
            </style>
        </head>

        <body>
        {NAV_HTML}
            <h1>Bowman Card Scanner</h1>

            <div id="camera-wrap">
                <video
                    id="video"
                    autoplay
                    playsinline
                    muted
                ></video>

                <div id="guide"></div>
            </div>

            <button id="capture-button">
                Capture Card
            </button>


            <hr style="margin:28px 0;">
            
            <div style="max-width:500px;margin:0 auto;text-align:left;">
                <h2>Upload Card Photos</h2>
            
                <form method="POST"
                      action="/scan-card"
                      enctype="multipart/form-data">
            
                    <label><strong>Front Photo</strong></label><br>
                    <input
                        type="file"
                        name="front_image"
                        accept="image/*"
                        required
                    >
            
                    <br><br>
            
                    <label><strong>Back Photo</strong></label><br>
                    <input
                        type="file"
                        name="back_image"
                        accept="image/*"
                        required
                    >
            
                    <br><br>
            
                    <button type="submit">
                        Identify Uploaded Card
                    </button>
            
                </form>
            </div>

            <div id="status">
                Center the card inside the frame.
            </div>

            </div>
            <canvas id="canvas"></canvas>

            <script>
                const video = document.getElementById("video");
                const canvas = document.getElementById("canvas");
                const button = document.getElementById("capture-button");
                const status = document.getElementById("status");

                let frontBlob = null;

                async function startCamera() {
                    try {
                        const stream =
                            await navigator.mediaDevices.getUserMedia({
                                video: {
                                    facingMode: {
                                        ideal: "environment"
                                    }
                                },
                                audio: false
                            });

                        video.srcObject = stream;
                    } catch (error) {
                        status.textContent =
                            "Camera unavailable: " + error.message;
                    }
                }

                button.addEventListener("click", async () => {
                    if (!video.videoWidth) {
                        status.textContent =
                            "Camera is not ready yet.";
                        return;
                    }

                    button.disabled = true;
                    status.textContent =
                        "Identifying card...";

                    canvas.width = video.videoWidth;
                    canvas.height = video.videoHeight;

                    const ctx = canvas.getContext("2d");

                    ctx.drawImage(
                        video,
                        0,
                        0,
                        canvas.width,
                        canvas.height
                    );

                    canvas.toBlob(
                        async (blob) => {
                            if (!frontBlob) {
                                frontBlob = blob;
                                status.textContent = "Front captured. Flip card and capture back.";
                                button.textContent = "Capture Back";
                                button.disabled = false;
                                return;
                            }
                            
                            const formData = new FormData();
                            
                            formData.append(
                                "front_image",
                                frontBlob,
                                "front.jpg"
                            );
                            
                            formData.append(
                                "back_image",
                                blob,
                                "back.jpg"
                            );

                            try {
                                const response =
                                    await fetch("/scan-card", {
                                        method: "POST",
                                        body: formData
                                    });

                                const html =
                                    await response.text();
                                
                                document.open();
                                document.write(html);
                                document.close();
                                    
                            } catch (error) {
                                status.textContent =
                                    "Scan failed: " +
                                    error.message;

                                button.disabled = false;
                            }
                        },
                        "image/jpeg",
                        0.92
                    );
                });

                startCamera();
            </script>
        </body>
        </html>
        """.replace("{NAV_HTML}", NAV_HTML)

    front_image = request.files.get("front_image")
    back_image = request.files.get("back_image")
    
    if not front_image or not back_image:
        return jsonify({
            "success": False,
            "error": "Front and back images are required"
        }), 400
    
    front_base64 = base64.b64encode(
        front_image.read()
    ).decode("utf-8")
    
    back_base64 = base64.b64encode(
        back_image.read()
    ).decode("utf-8")
    
    headers = {
        "Authorization": f"Token {XIMILAR_API_TOKEN}",
        "Content-Type": "application/json",
    }
    
    front_record = {
        "_base64": front_base64,
        "Side": "front"
    }
    
    back_record = {
        "_base64": back_base64,
        "Side": "back"
    }
        
    sport_response = requests.post(
        "https://api.ximilar.com/collectibles/v2/sport_id",
        headers=headers,
        json={
            "records": [front_record, back_record],
            "magic_ai": True,
            "slab_id": True,
            "slab_grade": True,
            "price_stats": False,
        },
        timeout=60,
    )
    
    ocr_response = requests.post(
        "https://api.ximilar.com/collectibles/v2/card_ocr_id",
        headers=headers,
        json={
            "records": [front_record, back_record],
        },
        timeout=60,
    )
    
    try:
        sport_data = sport_response.json()


        for i, record in enumerate(sport_data.get("records", [])):
            objects = record.get("_objects", [])
            identification = objects[0].get("_identification", {}) if objects else {}
        
            print(
                f"XIMILAR_RECORD_{i}:",
                "SIDE=", record.get("Side"),
                "BEST=", identification.get("best_match"),
                "TAGS=", objects[0].get("_tags", {}) if objects else {}
            )


    except ValueError:
        sport_data = {
            "error": sport_response.text
        }

    normalized_evidence = normalize_ximilar_sport_result(
        sport_data
    )

    cardhedge_resolution = resolve_with_cardhedge(
    normalized_evidence
    )
    
    try:
        ocr_data = ocr_response.json()
        for i, record in enumerate(ocr_data.get("records", [])):
            print(
                f"OCR_RECORD_{i}:",
                "SIDE=", record.get("Side"),
                "FULL_TEXT=", record.get("full_text")
            )
    except ValueError:
        ocr_data = {
            "error": ocr_response.text
        }
    
    overall_success = (
        sport_response.ok
        or ocr_response.ok
    )
    needs_review = True
    best_resolution = cardhedge_resolution.get("best")

    if best_resolution:
        resolved_card = best_resolution.get("card", {})
        resolver_score = best_resolution.get("score", 0)
    else:
        resolved_card = {}
        resolver_score = 0
        needs_review = best_resolution is None

    if needs_review:
        resolved_card = {}
    
    player = (
        resolved_card.get("player")
        or normalized_evidence.get("player_name")
        or ""
    )
    
    year = (
        normalized_evidence.get("card_year")
        or ""
    )
    
    product = (
        resolved_card.get("set")
        or normalized_evidence.get("product")
        or ""
    )
    
    card_number = (
        resolved_card.get("number")
        or normalized_evidence.get("card_number")
        or ""
    )
    
    parallel = (
        resolved_card.get("variant")
        or normalized_evidence.get("parallel")
        or ""
    )


    autograph = normalized_evidence.get("autograph")
    
    serial_number = (
        normalized_evidence.get("serial_number")
    )
    
    serial_to = (
        normalized_evidence.get("serial_numbered_to")
    )
    
    serial_display = ""
    
    if serial_to:
        if serial_number is not None:
            serial_display = f"{serial_number:03d}/{serial_to}"
        else:
            serial_display = f"/{serial_to}"
    
    confidence_label = "REVIEW"
    
    if not needs_review:
        if resolver_score >= 85:
            confidence_label = "HIGH"
        elif resolver_score >= 70:
            confidence_label = "MEDIUM"
    
    cardhedge_id = resolved_card.get("card_id") or ""
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Card Identified</title>
    
        <meta
            name="viewport"
            content="width=device-width, initial-scale=1"
        >
    
        <style>
        .app-nav {{
            position: sticky;
            top: 0;
            z-index: 1000;
            display: flex;
            gap: 6px;
            padding: 10px 16px;
            background: white;
            border-bottom: 1px solid #e5e7eb;
            overflow-x: auto;
            white-space: nowrap;
        }}
        
        .app-nav a {{
            display: inline-block;
            padding: 9px 12px;
            color: #374151;
            text-decoration: none;
            font-size: 14px;
            font-weight: bold;
            border-radius: 8px;
        }}
        
        .app-nav a:hover {{
            background: #f3f4f6;
            color: #111;
        }}


        .app-nav {{
        position: sticky;
        top: 0;
        z-index: 1000;
        display: flex;
        gap: 6px;
        padding: 10px 16px;
        background: white;
        border-bottom: 1px solid #e5e7eb;
        overflow-x: auto;
        white-space: nowrap;
    }}
    
    .app-nav a {{
        display: inline-block;
        padding: 10px 14px;
        color: #222;
        text-decoration: none;
        font-weight: 700;
    }}
    
    .app-nav a:hover {{
        background: #f3f4f6;
        border-radius: 8px;
    }}
            body {{
                font-family: Arial, sans-serif;
                max-width: 650px;
                margin: 30px auto;
                padding: 20px;
                background: #f5f5f5;
            }}
    
            .card {{
                background: white;
                padding: 12px;
                border-radius: 14px;
            }}
    
            h1 {{
                margin-top: 0;
            }}
    
            .field {{
                margin: 6px 0;
                font-size: 14px;
            }}
    
            .label {{
                color: #666;
                font-size: 14px;
            }}
    
            .confidence {{
                font-size: 16px;
                font-weight: bold;
                margin: 8px 0;
            }}
    
            button, a {{
                display: block;
                box-sizing: border-box;
                width: 100%;
                padding: 9px;
                margin-top: 12px;
                border-radius: 10px;
                text-align: center;
                font-size: 18px;
                text-decoration: none;
            }}
    
            .confirm {{
                background: #2563eb;
                color: white;
                border: none;
            }}
    
            .secondary {{
                background: #e5e7eb;
                color: #111;
            }}
        </style>
    </head>
    
    <body>
    {NAV_HTML}
    <div class="card">

    
        <h1>Card Identified</h1>
        
        <div class="confidence">
            Resolver Confidence: {confidence_label}
            ({resolver_score})
        </div>

        {"""
        <div style="
            margin: 12px 0 18px;
            padding: 12px;
            border-radius: 10px;
            background: #fff3cd;
            color: #664d03;
            font-weight: bold;
        ">
            Manual Review Required — scanner sources did not agree strongly enough.
        </div>
        """ if needs_review else ""}
        
        <form method="POST" action="/inventory-add">
        
        <div class="field">
        <div class="label">Player</div>
    
            <input
                type="text"
                name="player_name"
                value="{player}"
                style="
                    width:260px;
                    padding:8px;
                    font-size:18px;
                "
            >
        </div>
    
        <div class="field">
        <div class="label">Year</div>
    
            <input
                type="number"
                name="card_year"
                value="{year}"
                min="1900"
                max="2100"
                style="
                    width:120px;
                    padding:8px;
                    font-size:18px;
                "
            >
        </div>
    
        <div class="field">
        <div class="label">Product</div>
    
            <input
                type="text"
                name="product"
                value="{product}"
                style="
                    width:300px;
                    padding:8px;
                    font-size:18px;
                "
            >
        </div>
    
        <div class="field">
        <div class="label">Card Number</div>
    
            <input
                type="text"
                name="card_number"
                value="{card_number}"
                style="
                    width:140px;
                    padding:8px;
                    font-size:18px;
                    "
                >
            </div>
    
        <div class="field">
        <div class="label">Parallel</div>
    
            <input
                type="text"
                name="parallel"
                value="{parallel}"
                style="
                    width:220px;
                    padding:8px;
                    font-size:18px;
                "
            >
        </div>
    
        <div class="field">
        <div class="label">Serial Number</div>
    
        <input
            type="number"
            name="serial_number"
            value="{serial_number if serial_number is not None else ''}"
            min="1"
            style="
                width:90px;
                padding:8px;
                font-size:18px;
            "
        >
    
        <span style="font-size:18px;">
            /
        </span>
    
        <input
            type="number"
            name="serial_numbered_to"
            value="{serial_to if serial_to is not None else ''}"
            min="1"
            style="
                width:90px;
                padding:8px;
                font-size:18px;
            "
        >
    
        <div style="
            margin-top:6px;
            font-size:12px;
            color:#777;
        ">
            Verify the serial number before saving.
        </div>
    </div>

    <div class="field">
        <div class="label">Quantity</div>
    
        <input
            type="number"
            name="quantity"
            value="1"
            min="1"
            max="100"
            style="
                width:90px;
                padding:8px;
                font-size:18px;
            "
        >
    </div>

    <div class="field">
        <div class="label">1st Bowman</div>
        <select name="first_bowman">
            <option value="false">No</option>
            <option value="true">Yes</option>
        </select>
    </div>
    
    <div class="field">
        <div class="label">Prospect Card</div>
        <select name="prospect_card">
            <option value="false">No</option>
            <option value="true">Yes</option>
        </select>
    </div>
    
    <div class="field">
        <div class="label">Autograph</div>
        <select name="autograph">
            <option value="false" {"selected" if autograph is not True else ""}>No</option>
            <option value="true" {"selected" if autograph is True else ""}>Yes</option>
        </select>
    </div>
    
    <div class="field">
        <div class="label">Rookie Card</div>
        <select name="rookie_card">
            <option value="false">No</option>
            <option value="true">Yes</option>
        </select>
    </div>
    
    <div class="field">
        <div class="label">Grade Company</div>
        <select name="grade_company">
            <option value="">Raw</option>
            <option value="PSA">PSA</option>
            <option value="BGS">BGS</option>
            <option value="SGC">SGC</option>
            <option value="CGC">CGC</option>
        </select>
    </div>
    
    <div class="field">
        <div class="label">Grade</div>
        <input
            type="number"
            name="grade"
            min="1"
            max="10"
            step="0.5"
        >
    </div>
    
    <div class="field">
        <div class="label">Purchase Price</div>
        <input
            type="number"
            name="purchase_price"
            min="0"
            step="0.01"
            placeholder="0.00"
        >
    </div>
    
    <div class="field">
        <div class="label">Purchase Date</div>
        <input
            type="date"
            name="purchase_date"
        >
    </div>
    
    <div class="field">
        <div class="label">Purchase Source</div>
        <select name="purchase_source">
            <option value=""></option>
            <option value="eBay">eBay</option>
            <option value="Whatnot">Whatnot</option>
            <option value="Card Show">Card Show</option>
            <option value="Local Shop">Local Shop</option>
            <option value="Private Sale">Private Sale</option>
            <option value="Other">Other</option>
        </select>
    </div>
    
    <div class="field">
        <div class="label">Card Hedge ID</div>
        {cardhedge_id}
    </div>
     
    <input type="hidden" name="cardhedge_id" value="{cardhedge_id}">
    <input type="hidden" name="scanner_source" value="Ximilar + Card Hedge">
    <input type="hidden" name="resolver_score" value="{resolver_score}">
    
    <button class="confirm" type="submit">
        Confirm & Add to Inventory
    </button>
    
    </form>
    
        <a class="secondary" href="/scan-card">
            Scan Again
        </a>
    
    </div>
    
    </body>
    </html>
    """

@app.route("/inventory-add", methods=["POST"])
def inventory_add():
    player_name = request.form.get("player_name")
    card_year = request.form.get("card_year")
    product = request.form.get("product")
    card_number = request.form.get("card_number")
    parallel = request.form.get("parallel")

    serial_number = request.form.get("serial_number")
    serial_numbered_to = request.form.get("serial_numbered_to")

    scanner_source = request.form.get("scanner_source")
    resolver_score = request.form.get("resolver_score")
    cardhedge_id = request.form.get("cardhedge_id")
    
    first_bowman = request.form.get("first_bowman") == "true"
    prospect_card = request.form.get("prospect_card") == "true"
    autograph = request.form.get("autograph") == "true"
    rookie_card = request.form.get("rookie_card") == "true"
    
    grade_company = request.form.get("grade_company") or None
    grade = request.form.get("grade")
    purchase_price = request.form.get("purchase_price")
    purchase_date = request.form.get("purchase_date") or None
    purchase_source = request.form.get("purchase_source") or None
    
    grade = float(grade) if grade else None
    purchase_price = float(purchase_price) if purchase_price else None

    quantity = request.form.get("quantity", "1")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(1, min(quantity, 100))
    
    card_year = int(card_year) if card_year else None
    serial_number = int(serial_number) if serial_number else None
    
    serial_numbered_to = (
        int(serial_numbered_to)
        if serial_numbered_to
        else None
    )

    scanner_confidence = (
        float(resolver_score)
        if resolver_score
        else None
    )

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            inventory_ids = []
            for _ in range(quantity):
            
                cur.execute(
                    """
                    INSERT INTO inventory_cards (
                        player_name,
                        card_year,
                        manufacturer,
                        product,
                        card_number,
                        parallel,
                        serial_number,
                        serial_numbered_to,
                        scanner_source,
                        scanner_confidence,
                        external_card_id,
                        first_bowman,
                        prospect_card,
                        autograph,
                        rookie_card,
                        grade_company,
                        grade,
                        purchase_price,
                        purchase_date,
                        purchase_source
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    RETURNING id
                    """,
                    (
                        player_name,
                        card_year,
                        "Bowman",
                        product,
                        card_number,
                        parallel,
                        serial_number,
                        serial_numbered_to,
                        scanner_source,
                        scanner_confidence,
                        cardhedge_id,
                        first_bowman,
                        prospect_card,
                        autograph,
                        rookie_card,
                        grade_company,
                        grade,
                        purchase_price,
                        purchase_date,
                        purchase_source,
                    ),
                )

            inventory_id = cur.fetchone()[0]

        conn.commit()

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Card Added</title>
        <meta
            name="viewport"
            content="width=device-width, initial-scale=1"
        >
    </head>

    <body style="
        font-family:Arial;
        margin:0;
        padding:0;
        background:#f5f6f8;
    ">
    
    <div class="app-nav">
        <a href="/inventory">Inventory</a>
        <a href="/inventory/actions">Actions</a>
        <a href="/scan-card">Scan</a>
        <a href="/auction-watch">Auction Watch</a>
        <a href="/deals">Bowman Deals</a>
    </div>
    
    <div style="
        max-width:600px;
        margin:40px auto;
        padding:20px;
    ">

        <h1>Card Added ✓</h1>

        <h2>{player_name}</h2>

        <p>
            {card_year} {product}<br>
            #{card_number}<br>
            {parallel}
        </p>

        <p>
            Serial:
            {serial_number if serial_number is not None else ''}
            /
            {serial_numbered_to if serial_numbered_to is not None else ''}
        </p>

        <p>
            Added {quantity} card{"s" if quantity != 1 else ""} to inventory.
        </p>
        
        <p>
            Inventory IDs: {", ".join(str(i) for i in inventory_ids)}
        </p>

        <a
            href="/scan-card"
            style="
                display:block;
                padding:10px;
                background:#2563eb;
                color:white;
                text-decoration:none;
                text-align:center;
                border-radius:10px;
                font-size:18px;
            "
        >
            Scan Next Card
        </a>
    </div>
    </body>
    </html>
    """

@app.route("/inventory/actions", methods=["GET"])
def inventory_actions_page():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    player_name,
                    card_year,
                    product,
                    card_number,
                    first_bowman,
                    prospect_card,
                    parallel,
                    serial_number,
                    serial_numbered_to,
                    autograph,
                    rookie_card,
                    grade_company,
                    grade,
                    purchase_price,
                    purchase_date,
                    purchase_source,
                    quantity,
                    external_card_id,
                    market_value,
                    price_trend,
                    trend_pct,
                    trend_confidence,
                    disposition_action,
                    action_priority
                    FROM inventory_cards
                WHERE player_name IS NOT NULL
                ORDER BY created_at DESC
            """)

            rows = cur.fetchall()

    inventory_items = []

    for row in rows:
        (
            inventory_id,
            player_name,
            card_year,
            product,
            card_number,
            first_bowman,
            prospect_card,
            parallel,
            serial_number,
            serial_numbered_to,
            autograph,
            rookie_card,
            grade_company,
            grade,
            purchase_price,
            purchase_date,
            purchase_source,
            quantity,
            cardhedge_id,
            saved_market_value,
    saved_price_trend,
    saved_trend_pct,
    saved_trend_confidence,
    saved_disposition_action,
    saved_action_priority,
    ) = row

        market_value = saved_market_value
        price_trend = saved_price_trend or "UNKNOWN"
        price_trend_pct = saved_trend_pct
        price_trend_confidence = saved_trend_confidence or "LOW"
        history_points = 0
        sales_7day = 0
        sales_30day = 0
        market_gain = None
        
        gain_loss = None
        gain_loss_pct = None
        
        if (
            market_value is not None
            and purchase_price is not None
        ):
            gain_loss = (
                float(market_value) - float(purchase_price)
            )    
        
            if float(purchase_price) > 0:
                gain_loss_pct = (
                    gain_loss
                    / float(purchase_price)
                    * 100
                )
        
        market_value_display = (
            f"${market_value:,.2f}"
            if market_value is not None
            else "—"
        )
        
        gain_loss_display = "—"
        
        if gain_loss is not None:
            gain_loss_display = (
                f"${gain_loss:+,.2f}"
            )
        
            if gain_loss_pct is not None:
                gain_loss_display += (
                    f" ({gain_loss_pct:+.1f}%)"
                )

        action_priority = saved_action_priority or 0
        disposition_action = saved_disposition_action or "HOLD"
        disposition_liquidity = "UNKNOWN"
        disposition_reasons = []
                
        reasons_html = "".join(
            f"<li>{reason}</li>"
            for reason in disposition_reasons
        )

        trend_display = price_trend
        
        if price_trend_pct is not None:
            trend_display += f" ({price_trend_pct:+.1f}%)"
        
        trend_display += (
            f" · {price_trend_confidence} confidence"
        )
        
        serial_display = ""

        if serial_numbered_to:
            if serial_number is not None:
                serial_display = (
                    f"{serial_number:03d}/{serial_numbered_to}"
                )
            else:
                serial_display = f"/{serial_numbered_to}"

        grade_display = "Raw"

        if grade_company:
            grade_display = (
                f"{grade_company} {grade}"
                if grade is not None
                else grade_company
            )

        price_display = (
            f"${float(purchase_price):,.2f}"
            if purchase_price is not None
            else "—"
        )

        date_display = (
            purchase_date.strftime("%b %d, %Y")
            if purchase_date
            else "—"
        )

        badges = []

        if first_bowman:
            badges.append("1st Bowman")

        if prospect_card:
            badges.append("Prospect")

        if autograph:
            badges.append("Auto")

        if rookie_card:
            badges.append("RC")

        badges_html = " ".join(
            f'<span class="badge">{badge}</span>'
            for badge in badges
        )

        inventory_items.append({
            "priority": action_priority,
            "action": disposition_action,
            "trend": price_trend,
            "card_year": card_year,
            "product": product,
            "parallel": parallel,
            "grade_display": grade_display,
            "html": f"""
                
            <div class="inventory-card">
    
                <div class="player">
                    {player_name}
                </div>
    
                <div class="identity">
                    {card_year or ""} {product or ""}
                </div>
    
                <div class="identity">
                    #{card_number or ""}
                </div>
    
                <div class="parallel">
                    {parallel or "Base"}
                    {serial_display}
                </div>
    
                <div class="badges">
                    {badges_html}
                </div>
    
                <div class="details">
                    <div>
                        <span>Grade</span>
                        <strong>{grade_display}</strong>
                    </div>
    
                    <div>
                        <span>Cost</span>
                        <strong>{price_display}</strong>
                    </div>
    
                    <div>
                        <span>Purchased</span>
                        <strong>{date_display}</strong>
                    </div>
    
                    <div>
                        <span>Source</span>
                        <strong>{purchase_source or "—"}</strong>
                    </div>
                </div>
    
                <div class="market-placeholder">
                <div>
                    <span>Market Value</span>
                    <strong>{market_value_display}</strong>
                </div>
            
                <div>
                    <span>Gain / Loss</span>
                    <strong>{gain_loss_display}</strong>
                </div>
            </div>
            
            <div class="market-placeholder">
                <div>
                    <span>7-Day Sales</span>
                    <strong>{sales_7day}</strong>
                </div>
            
                <div>
                    <span>30-Day Sales</span>
                    <strong>{sales_30day}</strong>
                </div>
            </div>
    
            <div class="market-placeholder">
                <div>
                    <span>30-Day Price Trend</span>
                    <strong>{trend_display}</strong>
                </div>
            
                <div>
                    <span>History Points</span>
                    <strong>{history_points}</strong>
                </div>
            </div>
    
                <div class="decision-placeholder">
                <div style="
                    font-size:20px;
                    font-weight:bold;
                    margin-bottom:8px;
                ">
                    {disposition_action}
                </div>
    
        <div style="
            font-size:13px;
            color:#666;
            margin-bottom:8px;
        ">
            Liquidity: {disposition_liquidity}
        </div>
    
        <ul style="
            text-align:left;
            margin:0;
            padding-left:20px;
            font-weight:normal;
        ">
            {reasons_html}
        </ul>
    </div>
    
            </div>
            """,
    
       "compact_html": f"""
        <tr>
            <td>{player_name}</td>
        
            <td>
                <strong>{card_year or ""} #{card_number or ""}</strong><br>
                {product or ""}<br>
                {parallel or "Base"} {serial_display}<br>
                {grade_display}
            </td>
        
            <td>{market_value_display}</td>
        
            <td>{gain_loss_display}</td>
        
            <td>{trend_display}</td>
        
            <td>{price_trend_confidence}</td>
        
            <td>
                {
                    f'<a href="/inventory/action/{inventory_id}" class="action-link">{disposition_action}</a>'
                    if disposition_action == "SELL — AUCTION"
                    else disposition_action
                }
            </td>
        </tr>
        """
        })
    inventory_items.sort(
        key=lambda item: item["priority"],
        reverse=True
    )

    action_items = [
        item
        for item in inventory_items
        if item["priority"] >= 35
    ]

    action_years = sorted({
        str(item["card_year"])
        for item in action_items
        if item.get("card_year")
    }, reverse=True)
    
    action_products = sorted({
        str(item["product"])
        for item in action_items
        if item.get("product")
    })
    
    action_parallels = sorted({
        str(item["parallel"])
        for item in action_items
        if item.get("parallel")
    })
    
    action_grades = sorted({
        str(item["grade_display"])
        for item in action_items
        if item.get("grade_display")
    })
    
    action_actions = sorted({
        str(item["action"])
        for item in action_items
        if item.get("action")
    })
    
    action_queue_html = "".join(
        item["compact_html"]
        for item in action_items
    )

    if not action_queue_html:
        action_queue_html = """
        <div class="empty">
            No cards currently require attention.
        </div>
        """
    
    cards_html = "".join(
        item["compact_html"]
        for item in inventory_items
    )
    if not cards_html:
        cards_html = """
        <div class="empty">
            No inventory cards yet.
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>

    <head>
        <title>Bowman Inventory</title>

        <meta
            name="viewport"
            content="width=device-width, initial-scale=1"
        >

        <style>
            body {{
                margin: 0;
                background: #f3f4f6;
                font-family: Arial, sans-serif;
                color: #111;
            }}

            .app-nav {{
                position: sticky;
                top: 0;
                z-index: 1000;
                display: flex;
                gap: 6px;
                padding: 10px 16px;
                background: white;
                border-bottom: 1px solid #e5e7eb;
                overflow-x: auto;
                white-space: nowrap;
            }}
            
            .app-nav a {{
                display: inline-block;
                padding: 9px 12px;
                color: #374151;
                text-decoration: none;
                font-size: 14px;
                font-weight: bold;
                border-radius: 8px;
            }}
            
            .app-nav a:hover {{
                background: #f3f4f6;
                color: #111;
            }}

            .page {{
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
            }}

            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }}

            h1 {{
                margin: 0;
                font-size: 30px;
            }}

            .scan {{
                background: #2563eb;
                color: white;
                text-decoration: none;
                padding: 12px 16px;
                border-radius: 9px;
                font-weight: bold;
            }}

            .inventory-card {{
                background: white;
                border-radius: 14px;
                padding: 20px;
                margin-bottom: 16px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            .compact-card {{
                padding: 14px 18px;
                margin-bottom: 10px;
            }}
            
            .compact-top {{
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                gap: 15px;
            }}
            
            .compact-card .player {{
                font-size: 19px;
                margin-bottom: 3px;
            }}
            
            .compact-identity {{
                font-size: 13px;
                line-height: 1.35;
            }}
            
            .compact-action {{
                font-size: 15px;
                font-weight: bold;
                white-space: nowrap;
            }}
            
            .compact-metrics {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px 18px;
                margin-top: 10px;
                padding-top: 9px;
                border-top: 1px solid #e5e7eb;
                font-size: 13px;
            }}
            
            .compact-metrics span {{
                white-space: nowrap;
            }}
            
            @media (max-width: 600px) {{
                .compact-card {{
                    padding: 12px 14px;
                }}
            
                .compact-metrics {{
                    gap: 6px 12px;
                }}
            }}

            .player {{
                font-size: 24px;
                font-weight: bold;
                margin-bottom: 5px;
            }}

            .identity {{
                font-size: 16px;
                margin-top: 3px;
            }}


            .action-link {{
                display: inline-block;
                padding: 7px 10px;
                border-radius: 6px;
                background: #2563eb;
                color: white;
                text-decoration: none;
                font-weight: 700;
                white-space: nowrap;
            }}
            
            .action-link:hover {{
                background: #1d4ed8;
            }}
            
            .parallel {{
                font-size: 18px;
                font-weight: bold;
                margin-top: 8px;
            }}

            .badges {{
                margin-top: 10px;
            }}

            .badge {{
                display: inline-block;
                background: #e5e7eb;
                padding: 5px 9px;
                margin-right: 5px;
                border-radius: 7px;
                font-size: 12px;
                font-weight: bold;
            }}

            .details {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px;
                margin-top: 18px;
                border-top: 1px solid #ddd;
                padding-top: 16px;
            }}

            .details div {{
                display: flex;
                flex-direction: column;
            }}

            .details span,
            .market-placeholder span {{
                color: #666;
                font-size: 12px;
            }}

            .details strong {{
                margin-top: 3px;
            }}

            .actions-table {{
                width: 100%;
                border-collapse: collapse;
                table-layout: fixed;
                font-size: 12px;
            }}
            
            .actions-table th {{
                text-align: left;
                padding: 5px 6px;
                border-bottom: 2px solid #d1d5db;
                font-size: 11px;
                white-space: nowrap;
            }}
            
            .actions-table td {{
                padding: 5px 6px;
                border-bottom: 1px solid #e5e7eb;
                vertical-align: middle;
                line-height: 1.2;
            }}
            
            .actions-table th:nth-child(1) {{ width: 12%; }}
            .actions-table th:nth-child(2) {{ width: 30%; }}
            .actions-table th:nth-child(3) {{ width: 10%; }}
            .actions-table th:nth-child(4) {{ width: 11%; }}
            .actions-table th:nth-child(5) {{ width: 15%; }}
            .actions-table th:nth-child(6) {{ width: 9%; }}
            .actions-table th:nth-child(7) {{ width: 13%; }}
            
            .signal-bad {{
                color: #c62828;
                font-weight: 700;
            }}
            
            .signal-so-so {{
                color: #b77900;
                font-weight: 700;
            }}
            
            .signal-good {{
                color: #16803a;
                font-weight: 700;
            }}

            .market-placeholder {{
                margin-top: 18px;
                padding: 14px;
                background: #f9fafb;
                border-radius: 9px;
                display: flex;
                justify-content: space-between;
            }}

            .action-filters {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                margin: 14px 0 18px;
            }}
            
            .action-filters input,
            .action-filters select,
            .action-filters button {{
                font-size: 12px;
                padding: 7px 9px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background: white;
            }}
            
            .action-filters input {{
                min-width: 180px;
            }}
            
            .action-filters button {{
                cursor: pointer;
                font-weight: 700;
            }}
            .refresh-button {{
                padding: 10px 14px;
                border: 1px solid #2563eb;
                border-radius: 7px;
                background: white;
                color: #2563eb;
                font-weight: 700;
                cursor: pointer;
            }}
            
            .refresh-button:hover {{
                background: #eff6ff;
            }}
            .decision-placeholder {{
                margin-top: 10px;
                padding: 14px;
                background: #f3f4f6;
                border-radius: 9px;
                text-align: center;
                font-weight: bold;
                color: #666;
            }}

            .empty {{
                background: white;
                padding: 30px;
                text-align: center;
                border-radius: 12px;
            }}

            @media (max-width: 600px) {{
                .page {{
                    padding: 14px;
                }}

                h1 {{
                    font-size: 24px;
                }}
            }}
        </style>
    </head>

    <body>

        {NAV_HTML}

        <div class="page">

            <div class="header">
                <h1>Action Queue</h1>
                <form method="POST" action="/inventory/actions/refresh" style="margin:0;">
                    <button type="submit" class="refresh-button">
                        Refresh Intelligence
                    </button>
                </form>
                <a class="scan" href="/scan-card">
                    + Scan Card
                </a>
            </div>

           
            
            <div style="
                margin-bottom:20px;
                color:#666;
                font-size:14px;
            ">
                Cards with the highest current attention priority.
            </div>

            <div class="action-filters">
            
                <input
                    type="text"
                    id="actionPlayerSearch"
                    placeholder="Search player..."
                >
            
                <select id="actionYearFilter">
                    <option value="">All Years</option>
                    {"".join(
                        f'<option value="{year.lower()}">{year}</option>'
                        for year in action_years
                    )}
                </select>
            
                <select id="actionGradeFilter">
                    <option value="">All Grades</option>
                    {"".join(
                        f'<option value="{grade.lower()}">{grade}</option>'
                        for grade in action_grades
                    )}
                </select>
            
                <select id="actionActionFilter">
                    <option value="">All Actions</option>
                    {"".join(
                        f'<option value="{action.lower()}">{action}</option>'
                        for action in action_actions
                    )}
                </select>
            
                <button type="button" id="actionClearFilters">
                    Clear
                </button>
            
            </div>
            
            <div>
                <table class="actions-table">
                    <thead>
                        <tr>
                            <th>Player</th>
                            <th>Card</th>
                            <th>Market</th>
                            <th>P/L</th>
                            <th>Trend</th>
                            <th>Confidence</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {action_queue_html}
                    </tbody>
                </table>
            </div>
           </div>
        <script>
            const actionTable = document.querySelector(".actions-table");
        
            if (actionTable) {{
                const actionRows = Array.from(
                    actionTable.querySelectorAll("tbody tr")
                );
        
                const playerSearch =
                    document.getElementById("actionPlayerSearch");
        
                const yearFilter =
                    document.getElementById("actionYearFilter");
        
                const gradeFilter =
                    document.getElementById("actionGradeFilter");
        
                const actionFilter =
                    document.getElementById("actionActionFilter");
        
                const clearFilters =
                    document.getElementById("actionClearFilters");
        
        
                function applyActionFilters() {{
                    const playerValue =
                        playerSearch.value.trim().toLowerCase();
        
                    const yearValue =
                        yearFilter.value.trim().toLowerCase();
        
                    const gradeValue =
                        gradeFilter.value.trim().toLowerCase();
        
                    const actionValue =
                        actionFilter.value.trim().toLowerCase();
        
        
                    actionRows.forEach(row => {{
                        const cells = row.querySelectorAll("td");
        
                        const player =
                            cells[0]?.textContent.trim().toLowerCase() || "";
        
                        const card =
                            cells[1]?.textContent.trim().toLowerCase() || "";
        
                        const action =
                            cells[6]?.textContent.trim().toLowerCase() || "";
        
        
                        const playerMatch =
                            !playerValue ||
                            player.includes(playerValue);
        
                        const yearMatch =
                            !yearValue ||
                            card.includes(yearValue);
        
                        const gradeMatch =
                            !gradeValue ||
                            card.includes(gradeValue);
        
                        const actionMatch =
                            !actionValue ||
                            action.includes(actionValue);
        
        
                        const showRow =
                            playerMatch &&
                            yearMatch &&
                            gradeMatch &&
                            actionMatch;
        
                        row.style.display =
                            showRow ? "" : "none";
                    }});
                }}
        
        
                playerSearch.addEventListener(
                    "input",
                    applyActionFilters
                );
        
                yearFilter.addEventListener(
                    "change",
                    applyActionFilters
                );
        
                gradeFilter.addEventListener(
                    "change",
                    applyActionFilters
                );
        
                actionFilter.addEventListener(
                    "change",
                    applyActionFilters
                );
        
        
                clearFilters.addEventListener("click", () => {{
                    playerSearch.value = "";
                    yearFilter.value = "";
                    gradeFilter.value = "";
                    actionFilter.value = "";
        
                    applyActionFilters();
                }});
            }}
        </script>
    </body>
    </html>
    """


    
@app.route("/inventory/actions/refresh", methods=["POST"])
def refresh_inventory_actions():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    grade_company,
                    grade,
                    external_card_id,
                    purchase_price,
                    prospect_card,
                    first_bowman,
                    autograph,
                    serial_numbered_to
                FROM inventory_cards
                WHERE external_card_id IS NOT NULL
            """)

            rows = cur.fetchall()

            for (
                inventory_id,
                grade_company,
                grade,
                cardhedge_id,
                purchase_price,
                prospect_card,
                first_bowman,
                autograph,
                serial_numbered_to
            ) in rows:
                market = get_inventory_market_data(
                    cardhedge_id,
                    grade_company,
                    grade
                )

                trend_data = get_cardhedge_price_trend(
                    cardhedge_id,
                    grade_company,
                    grade
                )

                market_value = market["market_value"]
                price_trend = trend_data["trend"]
                trend_pct = trend_data["trend_pct"]
                trend_confidence = trend_data["trend_confidence"]

                sales_7day = market["sales_7day"]
                sales_30day = market["sales_30day"]


                disposition = calculate_disposition(
                    market_value,
                    purchase_price,
                    sales_7day,
                    sales_30day,
                    prospect_card,
                    first_bowman,
                    autograph,
                    grade_company,
                    grade,
                    serial_numbered_to,
                    trend_pct
                )

                disposition_action = disposition["action"]
                disposition_liquidity = disposition["liquidity"]
                gain_loss_pct = disposition["gain_loss_pct"]
            
            
                action_priority = calculate_action_priority(
                disposition_action,
                disposition_liquidity,
                gain_loss_pct,
                price_trend,
                trend_confidence
                )

                cur.execute("""
                    UPDATE inventory_cards
                    SET
                        market_value = %s,
                        price_trend = %s,
                        trend_pct = %s,
                        trend_confidence = %s,
                        disposition_action = %s,
                        action_priority = %s,
                        market_updated_at = NOW()
                    WHERE id = %s
                """, 
                (
                
                    market_value,
                    price_trend,
                    trend_pct,
                    trend_confidence,
                    disposition_action,
                    action_priority,
                    inventory_id
            
                ))

        conn.commit()

    return redirect("/inventory/actions")
                
@app.route("/inventory/action/<int:inventory_id>", methods=["GET"])
def inventory_action_detail(inventory_id):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    player_name,
                    card_year,
                    product,
                    card_number,
                    parallel,
                    serial_number,
                    serial_numbered_to,
                    grade_company,
                    grade,
                    purchase_price,
                    external_card_id
                FROM inventory_cards
                WHERE id = %s
            """, (inventory_id,))

            card = cur.fetchone()

    if not card:
        return "Inventory card not found", 404

    cardhedge_id = card[11]
    
    market = get_inventory_market_data(
        cardhedge_id,
        card[8],
        card[9]
    )
    
    trend_data = get_cardhedge_price_trend(
        cardhedge_id,
        card[8],
        card[9]
    )
    
    market_value = market["market_value"]
    price_trend = trend_data["trend"]
    price_trend_pct = trend_data["trend_pct"]
    price_trend_confidence = trend_data["trend_confidence"]
    
    purchase_price = card[10]
    
    gain_loss = None
    gain_loss_pct = None
    
    if market_value is not None and purchase_price is not None:
        gain_loss = float(market_value) - float(purchase_price)
    
        if float(purchase_price) != 0:
            gain_loss_pct = (
                gain_loss / float(purchase_price)
            ) * 100
    
    market_value_display = (
        f"${float(market_value):,.2f}"
        if market_value is not None
        else "-"
    )
    
    gain_loss_display = "-"
    
    if gain_loss is not None:
        gain_loss_display = f"${gain_loss:+,.2f}"
    
        if gain_loss_pct is not None:
            gain_loss_display += f" ({gain_loss_pct:+.1f}%)"
    
    trend_display = price_trend or "UNKNOWN"

    recommended_start_price = None
    expected_low = None
    expected_high = None
    minimum_outcome = None
    
    if market_value is not None:
        mv = float(market_value)
    
        recommended_start_price = max(0.99, mv * 0.50)
        expected_low = mv * 0.90
        expected_high = mv * 1.10
        minimum_outcome = mv * 0.80
    
    recommended_start_display = (
        f"${recommended_start_price:,.2f}"
        if recommended_start_price is not None
        else "-"
    )
    
    expected_sale_display = (
        f"${expected_low:,.2f} - ${expected_high:,.2f}"
        if expected_low is not None and expected_high is not None
        else "-"
    )
    
    minimum_outcome_display = (
        f"${minimum_outcome:,.2f}"
        if minimum_outcome is not None
        else "-"
    )

    recommended_duration = "7 days"
    recommended_ending_window = "Sunday 7:00 PM - 10:00 PM"
    
    if price_trend_pct is not None:
        trend_display += f" ({price_trend_pct:+.1f}%)"

    return f"""
    <html>
    <head>
        <title>Sell - Auction</title>
    
        <meta
            name="viewport"
            content="width=device-width, initial-scale=1"
        >
    
        <style>
            * {{
                box-sizing: border-box;
            }}
    
            body {{
                margin: 0;
                font-family: Arial, sans-serif;
                background: #f5f6f8;
                color: #111;
            }}
    
            .app-nav {{
                position: sticky;
                top: 0;
                z-index: 1000;
                display: flex;
                gap: 6px;
                padding: 10px 16px;
                background: white;
                border-bottom: 1px solid #e5e7eb;
                overflow-x: auto;
                white-space: nowrap;
            }}
    
            .app-nav a {{
                display: inline-block;
                padding: 10px 14px;
                color: #222;
                text-decoration: none;
                font-weight: 700;
            }}
    
            .app-nav a:hover {{
                background: #f3f4f6;
                border-radius: 8px;
            }}
    
            .page {{
                max-width: 1400px;
                margin: 0 auto;
                padding: 16px 24px 24px;
            }}
        
            .header {{
                display: flex;
                justify-content: space-between;
                gap: 20px;
                align-items: flex-start;
                margin-bottom: 24px;
            }}
    
            h1 {{
                margin: 0 0 8px;
                font-size: 34px;
            }}
    
            .player {{
                font-size: 24px;
                font-weight: 700;
                margin-bottom: 6px;
            }}
    
            .identity {{
                font-size: 15px;
                color: #555;
                line-height: 1.5;
            }}
    
            .summary {{
                display: grid;
                grid-template-columns: repeat(5, 1fr);
                gap: 10px;
                margin: 22px 0 28px;
            }}
    
            .summary-item {{
                background: white;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                padding: 14px;
            }}
    
            .summary-label {{
                font-size: 12px;
                color: #666;
                margin-bottom: 6px;
            }}
    
            .summary-value {{
                font-size: 18px;
                font-weight: 700;
            }}
    
            .section {{
                background: white;
                border: 1px solid #e5e7eb;
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 18px;
            }}
    
            .section h2 {{
                margin: 0 0 16px;
                font-size: 20px;
            }}
    
            .plan-grid {{
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 14px 28px;
            }}
    
            .plan-label {{
                color: #666;
                font-size: 12px;
                margin-bottom: 4px;
            }}
    
            .plan-value {{
                font-weight: 700;
                font-size: 16px;
            }}
    
            .actions {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
            }}
    
            .button {{
                display: inline-block;
                padding: 10px 14px;
                border-radius: 7px;
                text-decoration: none;
                font-weight: 700;
                border: 1px solid #d1d5db;
                background: white;
                color: #222;
            }}
    
            .button-primary {{
                background: #2563eb;
                color: white;
                border-color: #2563eb;
            }}
    
            .button:hover {{
                opacity: .9;
            }}
    
            .status {{
                display: inline-block;
                padding: 7px 10px;
                border-radius: 7px;
                background: #2563eb;
                color: white;
                font-weight: 700;
                white-space: nowrap;
            }}
    
            @media (max-width: 800px) {{
                .summary {{
                    grid-template-columns: repeat(2, 1fr);
                }}
    
                .plan-grid {{
                    grid-template-columns: 1fr;
                }}
    
                .header {{
                    flex-direction: column;
                }}
            }}
        </style>
    </head>
    
    <body>
    
        <div class="app-nav">
            <a href="/inventory">Inventory</a>
            <a href="/inventory/actions">Actions</a>
            <a href="/scan-card">Scan</a>
            <a href="/auction-watch">Auction Watch</a>
            <a href="/deals">Bowman Deals</a>
        </div>
    
        <div class="page">
    
            <div class="header">
                <div>
                    <h1>Sell - Auction</h1>
    
                    <div class="player">
                        {card[1]}
                    </div>
    
                    <div class="identity">
                        {card[2] or ""} {card[3] or ""}<br>
                        #{card[4] or ""} · {card[5] or "Base"}
                        {f" · {card[6]}/{card[7]}" if card[7] else ""}
                        <br>
                        {card[8] or "Raw"} {card[9] if card[9] is not None else ""}
                    </div>
                </div>
    
                <div class="status">
                    SELL - AUCTION
                </div>
            </div>
    
    
            <div class="summary">
    
                <div class="summary-item">
                    <div class="summary-label">Market Value</div>
                    <div class="summary-value">{market_value_display}</div>
                </div>
    
                <div class="summary-item">
                    <div class="summary-label">Your Cost</div>
                    <div class="summary-value">
                        {f"${float(card[10]):,.2f}" if card[10] is not None else "-"}
                    </div>
                </div>
    
                <div class="summary-item">
                    <div class="summary-label">P/L</div>
                    <div class="summary-value">{gain_loss_display}</div>
                </div>
    
                <div class="summary-item">
                    <div class="summary-label">Trend</div>
                    <div class="summary-value">{trend_display}</div>
                </div>
    
                <div class="summary-item">
                    <div class="summary-label">Confidence</div>
                    <div class="summary-value">—</div>
                </div>
    
            </div>
    
    
            <div class="section">
                <h2>Auction Plan</h2>
    
                <div class="plan-grid">
    
                    <div>
                        <div class="plan-label">Recommended Starting Price</div>
                        <div class="plan-value">{recommended_start_display}</div>
                    </div>
    
                    <div>
                        <div class="plan-label">Expected Sale Range</div>
                        <div class="plan-value">{expected_sale_display}</div>
                    </div>
    
                    <div>
                        <div class="plan-label">Minimum Acceptable Outcome</div>
                        <div class="plan-value">Coming next</div>
                    </div>
    
                    <div>
                        <div class="plan-label">Recommended Duration</div>
                        <div class="plan-value">{recommended_duration}</div>
                    </div>
    
                    <div>
                        <div class="plan-label">Recommended Ending Window</div>
                        <div class="plan-value">{recommended_ending_window}</div>
                    </div>
    
                    <div>
                        <div class="plan-label">Selling Method</div>
                        <div class="plan-value">eBay Auction</div>
                    </div>
    
                </div>
            </div>
    
    
            <div class="section">
                <h2>Why This Action?</h2>
    
                <p>
                    Current market value is approximately {market_value_display}
                    versus your cost of
                    {f"${float(purchase_price):,.2f}" if purchase_price is not None else "-"}.
                </p>
                
                <p>
                    The current price trend is {trend_display}
                    with {price_trend_confidence} confidence.
                    The portfolio engine is recommending an auction exit
                    rather than continuing to hold this position.
                </p>
            </div>
    
    
            <div class="section">
                <h2>Action Items</h2>
    
                <div class="actions">
    
                    <a
                        class="button button-primary"
                        href="/inventory/action/{inventory_id}/ebay-draft"
                    >
                        Create eBay Auction Draft
                    </a>
    
                    <a
                        class="button"
                        href="/inventory/action/{inventory_id}/bin"
                    >
                        Compare Buy It Now
                    </a>
    
                    <a
                        class="button"
                        href="/inventory/action/{inventory_id}/hold"
                    >
                        Hold Instead
                    </a>
    
                    <a
                        class="button"
                        href="/inventory/actions"
                    >
                        Back to Actions
                    </a>
    
                </div>
            </div>
    
        </div>
    
    </body>
    </html>
    """

@app.route("/privacy", methods=["GET"])
def privacy_policy():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>JacksJunkbox Privacy Policy</title>
    </head>
    <body style="font-family:Arial,sans-serif;max-width:800px;margin:40px auto;padding:20px;">
        <h1>JacksJunkbox Privacy Policy</h1>

        <p>
            JacksJunkbox uses eBay account authorization only to provide
            inventory management and listing functionality requested by
            the account owner.
        </p>

        <p>
            eBay account information and authorization credentials are
            used only to communicate with eBay services on behalf of the
            authorized user.
        </p>

        <p>
            JacksJunkbox does not sell personal information or eBay
            account information to third parties.
        </p>

        <p>
            Authorization may be revoked through the user's eBay account
            or application authorization settings.
        </p>
    </body>
    </html>
    """

@app.route("/ebay/user-token-check")
def ebay_user_token_check():
    token = os.environ.get("EBAY_USER_ACCESS_TOKEN", "")

    return jsonify({
        "configured": bool(token),
        "length": len(token),
        "starts_with_bearer": token.lower().startswith("bearer "),
        "has_leading_space": token != token.lstrip(),
        "has_trailing_space": token != token.rstrip(),
        "has_quotes": token.startswith(("'", '"')) or token.endswith(("'", '"')),
    })

@app.route("/ebay/oauth/callback", methods=["GET"])
def ebay_oauth_callback():
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return f"eBay authorization declined or failed: {error}", 400

    if not code:
        return "No eBay authorization code received.", 400

    return f"eBay authorization code received successfully."


def get_ebay_user_access_token():
    ensure_ebay_oauth_table()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT refresh_token
                FROM ebay_oauth_tokens
                WHERE id = 1
            """)
            row = cur.fetchone()

    if not row or not row[0]:
        raise RuntimeError("No saved eBay refresh token")

    refresh_token = row[0]

    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")

    credentials = f"{client_id}:{client_secret}"
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
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )

    token_json = response.json() if response.content else {}

    if not response.ok or not token_json.get("access_token"):
        raise RuntimeError(
            token_json.get("error_description")
            or token_json.get("error")
            or "Unable to refresh eBay access token"
        )

    return token_json["access_token"]

@app.route("/ebay/oauth/start")
def ebay_oauth_start():
    scopes = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
])

    params = {
        "client_id": EBAY_CLIENT_ID,
        "redirect_uri": os.environ["EBAY_RUNAME"],
        "response_type": "code",
        "scope": scopes,
    }

    auth_url = (
        "https://auth.ebay.com/oauth2/authorize?"
        + urllib.parse.urlencode(params)
    )

    return redirect(auth_url)


@app.route("/ebay/oauth/scope-test")
def ebay_oauth_scope_test():
    scopes = (
        "https://api.ebay.com/oauth/api_scope "
        "https://api.ebay.com/oauth/api_scope/sell.account"
    )

    return jsonify({
        "scope": scopes,
        "has_sell_account": "sell.account" in scopes,
    })


@app.route("/ebay/oauth/start-debug")
def ebay_oauth_start_debug():
    scopes = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
])

    params = {
        "client_id": EBAY_CLIENT_ID,
        "redirect_uri": os.environ["EBAY_RUNAME"],
        "response_type": "code",
        "scope": scopes,
    }

    auth_url = (
        "https://auth.ebay.com/oauth2/authorize?"
        + urllib.parse.urlencode(params)
    )

    parsed = urllib.parse.urlparse(auth_url)
    query = urllib.parse.parse_qs(parsed.query)

    return jsonify({
        "scope_received": query.get("scope", []),
        "has_sell_account": "sell.account" in query.get("scope", [""])[0],
    })

@app.route("/ebay/oauth/status")
def ebay_oauth_status():
    ensure_ebay_oauth_table()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT refresh_token, scope, updated_at
                FROM ebay_oauth_tokens
                WHERE id = 1
            """)
            row = cur.fetchone()

    return jsonify({
        "saved": bool(row),
        "has_refresh_token": bool(row and row[0]),
        "scope": row[1] if row else None,
        "updated_at": row[2].isoformat() if row and row[2] else None,
    })

@app.route("/ebay/oauth/refresh-test")
def ebay_oauth_refresh_test():
    ensure_ebay_oauth_table()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT refresh_token
                FROM ebay_oauth_tokens
                WHERE id = 1
            """)
            row = cur.fetchone()

    if not row or not row[0]:
        return jsonify({
            "success": False,
            "error": "No saved refresh token"
        }), 400

    refresh_token = row[0]

    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")

    credentials = f"{client_id}:{client_secret}"
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
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=30,
    )

    token_json = response.json() if response.content else {}

    return jsonify({
        "success": response.ok,
        "status_code": response.status_code,
        "has_access_token": bool(token_json.get("access_token")),
        "expires_in": token_json.get("expires_in"),
        "error": token_json.get("error"),
        "error_description": token_json.get("error_description"),
    })



@app.route("/ebay/account/privileges-test")
def ebay_account_privileges_test():
    token = get_ebay_user_access_token()

    response = requests.get(
        "https://api.ebay.com/sell/account/v1/privilege",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=30,
    )

    data = response.json() if response.content else {}

    return jsonify({
        "status_code": response.status_code,
        "response": data,
    })

@app.route("/ebay/create-bin-test/<int:inventory_id>", methods=["GET"])
def ebay_create_bin_test(inventory_id):
    ebay_token = get_ebay_user_access_token()

    sku = f"inventory-{inventory_id}"

    # Confirm the inventory item exists
    item_response = requests.get(
        f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
        headers={
            "Authorization": f"Bearer {ebay_token}",
            "Accept": "application/json",
        },
        timeout=30,
    )

    item_json = (
        item_response.json()
        if item_response.content
        else {}
    )

    product = item_json.get("product", {})
    listing_description = product.get("description", "")

    offer_payload = {
        "sku": sku,
        "marketplaceId": "EBAY_US",
        "merchantLocationKey": "jackstation-main",
        "format": "FIXED_PRICE",
        "categoryId": "261328",
        "listingDescription": listing_description,
        "listingPolicies": {
            "fulfillmentPolicyId": "294947209015",
            "paymentPolicyId": "294947292015",
            "returnPolicyId": "294956239015",
        },
        "pricingSummary": {
            "price": {
                "value": "400.00",
                "currency": "USD",
            }
        },
        "listingDuration": "GTC",
    }

    offer_response = requests.post(
        "https://api.ebay.com/sell/inventory/v1/offer",
        headers={
            "Authorization": f"Bearer {ebay_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Content-Language": "en-US",
        },
        json=offer_payload,
        timeout=30,
    )

    return jsonify({
        "success": offer_response.status_code in (200, 201),
        "inventory_id": inventory_id,
        "sku": sku,
        "status_code": offer_response.status_code,
        "offer_response": (
            offer_response.json()
            if offer_response.content
            else {}
        ),
    })

@app.route(
    "/inventory/action/<int:inventory_id>/ebay-draft",
    methods=["GET", "POST"]
)
def inventory_action_ebay_draft(inventory_id):
    if request.method == "POST":
        listing_title = request.form.get("listing_title", "").strip()
        listing_description = request.form.get("listing_description", "").strip()
        starting_bid = request.form.get("starting_bid", "").strip()
        auction_duration = request.form.get("auction_duration", "7").strip()
        condition_value = request.form.get("condition", "graded").strip()
    
        photos = request.files.getlist("listing_photos")
    
        upload_dir = f"/tmp/ebay_draft_{inventory_id}"
        os.makedirs(upload_dir, exist_ok=True)
    
        saved_photos = []
        ebay_upload_results = []
        ebay_image_urls = []


        ebay_listing_payload = {
            "inventory_id": inventory_id,
            "category_id": "261328",
            "condition": "LIKE_NEW",
            "condition_descriptors": [
                {
                    "name": "27501",
                    "values": ["275010"],
                },
                {
                    "name": "27502",
                    "values": ["275020"],
                },
            ],
            "title": listing_title,
            "description": listing_description,
            "starting_bid": starting_bid,
            "auction_duration_days": auction_duration,
            "condition": condition_value,
            "image_urls": ebay_image_urls,
            "quantity": 1,
            "format": "AUCTION",
            "marketplace": "EBAY_US",
        }
    
        for photo in photos:
            if photo and photo.filename:
                safe_name = os.path.basename(photo.filename)
                save_path = os.path.join(upload_dir, safe_name)
    
                photo.save(save_path)
                saved_photos.append(save_path)
    
        ebay_token = get_ebay_user_access_token()
        

        if ebay_token and saved_photos:
            for photo_path in saved_photos:
                with open(photo_path, "rb") as image_file:
                    upload_response = requests.post(
                        "https://apim.ebay.com/commerce/media/v1_beta/image/create_image_from_file",
                        headers={
                            "Authorization": f"Bearer {ebay_token}",
                            "Accept": "application/json",
                        },
                        files={
                            "image": (
                                os.path.basename(photo_path),
                                image_file,
                            )
                        },
                        timeout=30,
                    )
        
                upload_json = upload_response.json() if upload_response.content else {}
        
                ebay_upload_results.append({
                    "status_code": upload_response.status_code,
                    "location": upload_response.headers.get("Location"),
                    "image_url": upload_json.get("imageUrl"),
                    "max_dimension_image_url": upload_json.get("maxDimensionImageUrl"),
                })


                ebay_image_urls = [
                    result["image_url"]
                    for result in ebay_upload_results
                    if result.get("status_code") == 201
                    and result.get("image_url")
                ]

                ebay_category_id = None
        
                with psycopg.connect(DATABASE_URL) as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT player_name
                            FROM inventory_cards
                            WHERE id = %s
                        """, (inventory_id,))
        
                        inventory_row = cur.fetchone()
        
                        if inventory_row and inventory_row[0]:
                            player_name_for_category = inventory_row[0]
        
                            cur.execute("""
                                SELECT category_id, COUNT(*) AS category_count
                                FROM ebay_listings
                                WHERE category_id IS NOT NULL
                                GROUP BY category_id
                                ORDER BY category_count DESC
                                LIMIT 1
                            """)
        
                            category_row = cur.fetchone()
        
                            if category_row:
                                ebay_category_id = category_row[0]

            
                             
        sku = f"inventory-{inventory_id}"
        
        inventory_payload = {
            "availability": {
                "shipToLocationAvailability": {
                    "quantity": 1
                }
            },
            "condition": "LIKE_NEW",
            "conditionDescriptors": [
                {
                    "name": "27501",
                    "values": ["275010"],
                },
                {
                    "name": "27502",
                    "values": ["275020"],
                },
            ],
            "product": {
                "title": listing_title,
                "description": listing_description,
                "imageUrls": ebay_image_urls,
                "aspects": {
                    "Sport": ["Baseball"]
                },
            }
        }
    
        inventory_response = requests.put(
            f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
            headers={
                "Authorization": f"Bearer {ebay_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Language": "en-US",
            },
            json=inventory_payload,
            timeout=30,
        )
        
        inventory_response_json = (
            inventory_response.json()
            if inventory_response.content
            else {}
        )

        offer_payload = {
            "sku": sku,
            "marketplaceId": "EBAY_US",
            "format": "AUCTION",
            "categoryId": "261328",
            "listingDescription": listing_description,
            "listingPolicies": {
                "fulfillmentPolicyId": "294947209015",
                "paymentPolicyId": "294947292015",
                "returnPolicyId": "294956239015",
            },
            "pricingSummary": {
                "auctionStartPrice": {
                    "value": str(starting_bid),
                    "currency": "USD",
                }
            },
            "listingDuration": f"DAYS_{auction_duration}",
        }
        
        offer_response = requests.post(
            "https://api.ebay.com/sell/inventory/v1/offer",
            headers={
                "Authorization": f"Bearer {ebay_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Language": "en-US",
            },
            json=offer_payload,
            timeout=30,
        )
        
        offer_response_json = (
            offer_response.json()
            if offer_response.content
            else {}
        )

                   
        return jsonify({
            "success": True,
            "inventory_id": inventory_id,
            "ebay_inventory_status": inventory_response.status_code,
            "ebay_inventory_response": inventory_response_json,
            "category_id": "261328",
            "photos_saved": len(saved_photos),
            "upload_dir": upload_dir,
            "ebay_upload_results": ebay_upload_results,
            "ebay_image_urls": ebay_image_urls,
            "listing_title": listing_title,
            "listing_description": listing_description,
            "starting_bid": starting_bid,
            "auction_duration": auction_duration,
            "condition": condition_value,
            "ebay_listing_payload": ebay_listing_payload,
            "ebay_offer_status": offer_response.status_code,
            "ebay_offer_response": offer_response_json,
        })   
        
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    player_name,
                    card_year,
                    product,
                    card_number,
                    parallel,
                    serial_number,
                    serial_numbered_to,
                    grade_company,
                    grade,
                    purchase_price,
                    market_value,
                    price_trend,
                    trend_confidence
                    FROM inventory_cards
                WHERE id = %s
            """, (inventory_id,))

            card = cur.fetchone()

        if not card:
            return "Inventory card not found", 404
        
        listing_title = ""
        
        market_value = card[11]
        
        recommended_start_price = calculate_auction_start_price(
            market_value,
            card[10],
            card[7],
            card[8],
            card[9],
            card[12],
            card[13]
        )
        
        recommended_start_display = (
            f"{recommended_start_price:.2f}"
            if recommended_start_price is not None
            else ""
        )
        
        purchase_price = card[10]
        
        market_value_display = (
            f"${float(market_value):,.2f}"
            if market_value is not None
            else "-"
        )
        
        purchase_price_display = (
            f"${float(purchase_price):,.2f}"
            if purchase_price is not None
            else "-"
        )
        
        start_market_pct = None
        gain_loss = None
        gain_loss_pct = None
        
        if market_value is not None and recommended_start_price is not None:
            if float(market_value) != 0:
                start_market_pct = (
                    float(recommended_start_price)
                    / float(market_value)
                ) * 100
        
        if market_value is not None and purchase_price is not None:
            gain_loss = float(market_value) - float(purchase_price)
        
            if float(purchase_price) != 0:
                gain_loss_pct = (
                    gain_loss / float(purchase_price)
                ) * 100
        
        start_market_pct_display = (
            f"{start_market_pct:.1f}%"
            if start_market_pct is not None
            else "-"
        )
        
        gain_loss_display = (
            f"${gain_loss:+,.2f}"
            if gain_loss is not None
            else "-"
        )
        
        gain_loss_pct_display = (
            f"{gain_loss_pct:+.1f}%"
            if gain_loss_pct is not None
            else "-"
        )
        
        price_trend = card[12] or "UNKNOWN"
        trend_confidence = card[13] or "LOW"


        player_name = card[1] or ""
        card_year = str(card[2] or "")
        product = card[3] or ""
        card_number = card[4] or ""
        parallel = card[5] or "Base"
        serial_number = card[6]
        serial_numbered_to = card[7]
        grade_company = card[8] or ""
        grade = card[9]

        condition_value = "graded" if grade_company else "raw"
        listing_title = ""


        title_product = product
        
        # Remove duplicate year from product
        if card_year:
            title_product = re.sub(
                rf"^\s*{re.escape(card_year)}\s+",
                "",
                title_product,
                flags=re.IGNORECASE
            )
        
        # "Baseball" is unnecessary in an 80-character eBay title
        title_product = re.sub(
            r"\bBASEBALL\b",
            "",
            title_product,
            flags=re.IGNORECASE
        )
        
        title_product = re.sub(r"\s+", " ", title_product).strip()
        
        title_parts = [
            card_year,
            player_name,
            title_product,
        ]

        if parallel and parallel.lower() != "base":
            title_parallel = parallel
        
            if (
                title_product.lower().endswith("chrome")
                and title_parallel.lower().startswith("chrome ")
            ):
                title_parallel = title_parallel[7:].strip()
        
            if title_parallel.lower() not in title_product.lower():
                title_parts.append(title_parallel)
        
        if card_number:
            title_parts.append(f"#{card_number}")
        
        if serial_numbered_to:
            title_parts.append(f"/{serial_numbered_to}")
        
        if grade_company:
            if grade is not None:
                grade_title = (
                    str(int(grade))
                    if float(grade).is_integer()
                    else str(grade)
                )
        
                title_parts.append(
                    f"{grade_company} {grade_title}"
                )
            else:
                title_parts.append(grade_company)
        
        listing_title = " ".join(
            part.strip()
            for part in title_parts
            if str(part).strip()
        )
        
        listing_title = listing_title[:80]
        listing_description = (
            f"{card_year} {player_name} {product}\n"
            f"Card #{card_number}\n"
            f"Parallel: {parallel}\n"
        )
        
        if serial_numbered_to:
            listing_description += (
                f"Serial Numbered: {serial_number or ''}/{serial_numbered_to}\n"
            )
        
        if grade_company:
            listing_description += (
                f"Grade: {grade_company} {grade or ''}\n"
            )
        
        listing_description += (
            "\nPlease review photos for exact card condition. "
            "Card pictured is the card you will receive."
        )
    return f"""
    <html>
    <head>
        <title>eBay Auction Draft</title>

        <style>
            * {{
                box-sizing: border-box;
            }}

            body {{
                margin: 0;
                background: #f4f5f7;
                color: #111;
                font-family: Arial, sans-serif;
                font-size: 12px;
            }}

            .page {{
                max-width: 1400px;
                margin: 0 auto;
                padding: 18px 24px 28px;
            }}

            .top-grid {{
                display: grid;
                grid-template-columns: 0.65fr 1.6fr;
                gap: 18px;
                align-items: start;
                margin-bottom: 14px;
            }}

            h1 {{
                margin: 0 0 10px;
                font-size: 16px;
                line-height: 1.1;
            }}

            h2 {{
                margin: 0 0 14px;
                font-size: 20px;
            }}

            .card-summary {{
                margin: 0;
                line-height: 1.35;
                font-size: 12px;
            }}

            .panel {{
                background: white;
                border: 1px solid #dfe3e8;
                border-radius: 10px;
                padding: 12px;
            }}

            .label {{
                color: #666;
                font-size: 12px;
                margin-bottom: 6px;
            }}

            .market-layout {{
                display: grid;
                grid-template-columns: minmax(360px, 2fr) minmax(180px, 0.8fr);
                gap: 16px;
                align-items: start;
            }}

            .market-metrics {{
                display: grid;
                grid-template-columns: 130px 100px 130px 110px;
                gap: 8px 16px;
                align-items: start;
                line-height: 1.35;
                font-size: 15px;
            }}

            .market-metrics strong {{
                font-weight: 700;
            }}

            .timing {{
                border-left: 1px solid #dfe3e8;
                padding-left: 16px;
            }}

            .timing-block {{
                margin-bottom: 12px;
            }}

            .timing-value {{
                font-size: 17px;
                font-weight: 700;
                line-height: 1.3;
            }}

            .title-row {{
                display: grid;
                grid-template-columns: minmax(0, 1fr) 240px;
                gap: 12px;
                margin-bottom: 12px;
            }}

            .bottom-grid {{
                display: grid;
                grid-template-columns: minmax(0, 3fr) minmax(260px, 1fr);
                gap: 12px;
                align-items: start;
            }}

            input,
            select,
            textarea {{
                width: 100%;
                font: inherit;
                border: 1px solid #cfd5dc;
                border-radius: 6px;
                background: white;
                padding: 8px;
            }}

            .title-input {{
                font-size: 16px;
                font-weight: 700;
            }}

            textarea {{
                line-height: 1.3;
                resize: vertical;
            }}

            .status-row {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 12px;
                margin-top: 10px;
            }}

            .status {{
                color: #555;
            }}

            @media (max-width: 900px) {{
                .top-grid,
                .market-layout,
                .title-row,
                .bottom-grid {{
                    grid-template-columns: 1fr;
                }}

                .market-metrics {{
                    grid-template-columns: 1fr 100px;
                }}

                .timing {{
                    border-left: 0;
                    border-top: 1px solid #dfe3e8;
                    padding-left: 0;
                    padding-top: 12px;
                }}
            }}
        </style>
    </head>

    <body>
        <div class="page">
        <form method="POST" enctype="multipart/form-data">
            <div class="top-grid">

                <div>
                    <h1>eBay Auction Draft</h1>
                    <h2>{player_name}</h2>

                    <p class="card-summary">
                        {card_year} {product}<br>
                        #{card_number}<br>
                        {parallel or "Base"}
                    </p>
                </div>

                <div class="panel">
                    <div class="label">Market Intelligence</div>

                    <div class="market-layout">

                        <div class="market-metrics">
                            <div>Market Value</div>
                            <div><strong>{market_value_display}</strong></div>

                            <div>Recommended Start</div>
                            <div><strong>${recommended_start_display}</strong></div>

                            <div>Start / Market</div>
                            <div><strong>{start_market_pct_display}</strong></div>

                            <div>Purchase Cost</div>
                            <div><strong>{purchase_price_display}</strong></div>

                            <div>Unrealized P/L</div>
                            <div>
                                <strong>
                                    {gain_loss_display}
                                    ({gain_loss_pct_display})
                                </strong>
                            </div>

                            <div>Trend</div>
                            <div><strong>{price_trend}</strong></div>

                            <div>Confidence</div>
                            <div><strong>{trend_confidence}</strong></div>

                            <div></div>
                            <div></div>
                        </div>

                        <div class="timing">

                            <div class="timing-block">
                                <div class="label">Auction Duration</div>

                                <select name="auction_duration">
                                    <option value="3">3 days</option>
                                    <option value="5">5 days</option>
                                    <option value="7" selected>7 days</option>
                                    <option value="10">10 days</option>
                                </select>
                            </div>

                            <div class="timing-block">
                                <div class="label">
                                    Recommended Ending Window
                                </div>

                                <div class="timing-value">
                                    Sunday 7:00 PM - 10:00 PM
                                </div>
                            </div>

                            <div>
                                <div class="label">Condition</div>

                                <select name="condition">
                                    <option value="graded"
                                        {"selected" if condition_value == "graded" else ""}>
                                        Graded
                                    </option>

                                    <option value="raw"
                                        {"selected" if condition_value == "raw" else ""}>
                                        Raw / Ungraded
                                    </option>
                                </select>
                            </div>

                        </div>
                    </div>
                </div>
            </div>

            <div class="title-row">

                <div class="panel">
                    <div class="label">Proposed eBay Title</div>

                    <input
                        class="title-input"
                        type="text"
                        name="listing_title"
                        value="{listing_title}"
                        maxlength="80"
                    >
                </div>

                <div class="panel">
                    <div class="label">Starting Bid</div>

                    <input
                        type="number"
                        step="0.01"
                        name="starting_bid"
                        value="{recommended_start_display}"
                        style="font-size:16px;font-weight:700;"
                    >
                </div>

            </div>

            <div class="bottom-grid">

                <div class="panel">
                    <div class="label">Listing Description</div>

                    <textarea
                        name="listing_description"
                        rows="7"
                    >{listing_description}</textarea>
                </div>

                <div class="panel">
                    <div class="label">Listing Photos</div>

                    <input
                        type="file"
                        name="listing_photos"
                        id="listing_photos"
                        accept="image/*"
                        multiple
                    >

                    <div id="photo_preview"></div>
                </div>

            </div>

            <div class="status-row">
                <div class="status">
                    Draft workflow is connected.
                </div>

                <a href="/inventory/action/{inventory_id}">
                    ← Back to Action Detail
                </a>
            </div>

        </div>
        <button type="submit">
            Save Draft & Upload Photos
        </button>
        </form>
    
        <script>
            const photoInput = document.getElementById("listing_photos");
            const preview = document.getElementById("photo_preview");
        
            photoInput.addEventListener("change", function () {{
                preview.innerHTML = "";
        
                Array.from(this.files).forEach(function (file) {{
                    const img = document.createElement("img");
    
                img.src = URL.createObjectURL(file);
    
                img.style.width = "120px";
                img.style.height = "120px";
                img.style.objectFit = "contain";
                img.style.margin = "8px 8px 0 0";
                img.style.border = "1px solid #dfe3e8";
                img.style.borderRadius = "6px";
                img.style.background = "#fff";
    
                preview.appendChild(img);
            }});
        }});
    </script>
    
    </body>
    </html>
    """

@app.route("/ebay/offer/<offer_id>", methods=["GET"])
def ebay_offer_detail(offer_id):
    token = get_ebay_user_access_token()

    response = requests.get(
        f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=30,
    )

    data = response.json() if response.content else {}

    return jsonify({
        "status_code": response.status_code,
        "offer": data,
    })


@app.route("/inventory/import-cdp", methods=["POST"])
def import_cdp_csv():
    import csv
    import io

    uploaded_file = request.files.get("cdp_csv")
    filename = (uploaded_file.filename or "").lower()


    if not uploaded_file:
        return "No CSV uploaded", 400


    if filename.endswith(".zip"):
        zip_bytes = uploaded_file.read()
    
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            csv_files = [
                name for name in z.namelist()
                if name.lower().endswith(".csv")
            ]
    
            if not csv_files:
                return jsonify({
                    "success": False,
                    "message": "No CSV found inside ZIP."
                }), 400
    
            csv_name = csv_files[0]
            text = z.read(csv_name).decode("utf-8-sig")
    else:
        text = uploaded_file.read().decode("utf-8-sig")

   
    reader = csv.DictReader(io.StringIO(text))

    rows = list(reader)

    normalized_rows = []
    
    for first in rows:
        attributes = first.get("attributes") or ""
        title = first.get("title") or ""
    
        serial_number = None
        serial_numbered_to = None
    
        serial_match = re.search(
            r"(?:#\s*)?(?:(\d{1,4})\s*/\s*(\d{1,4})|/\s*(\d{1,4}))",
            title
        )
    
        if serial_match:
            if serial_match.group(1) and serial_match.group(2):
                serial_number = int(serial_match.group(1))
                serial_numbered_to = int(serial_match.group(2))
            elif serial_match.group(3):
                serial_numbered_to = int(serial_match.group(3))
    
        graded_text = (first.get("graded") or "").strip().lower()
    
        if graded_text == "yes":
            grade_company = first.get("grader") or None
            grade = first.get("grade_number") or first.get("grade_name") or None
        else:
            grade_company = "Raw"
            grade = None
    
        first_bowman = (
            "bowman" in title.lower()
            and re.search(r"\b1st\b", title.lower()) is not None
        )
        prospect_card = "prospect" in title.lower()
        autograph = (
            "auto" in title.lower()
            or "autograph" in title.lower()
        )

        purchase_price_raw = (first.get("purchase_price") or "").strip()
        
        if purchase_price_raw and purchase_price_raw not in ("0", "0.00"):
            purchase_price = float(purchase_price_raw)
        else:
            purchase_price = None
    
        normalized_rows.append({
            "title": title,
            "player_name": first.get("player"),
            "card_year": first.get("year"),
            "product": (
                "Bowman Draft"
                if "Bowman" in title and "Draft" in title
                else "Bowman Chrome"
                if "Bowman Chrome" in title
                else "Topps Chrome"
                if "Topps Chrome" in title
                else first.get("set")
            ),
            "card_number": first.get("card_number"),
            "parallel": first.get("subset"),
            "serial_number": serial_number,
            "serial_numbered_to": serial_numbered_to,
            "first_bowman": first_bowman,
            "prospect_card": prospect_card,
            "autograph": autograph,
            "grade_company": grade_company,
            "grade": grade,
            "quantity": 1,
            "purchase_price": purchase_price,
            "team": first.get("team"),
            "sku": first.get("sku"),
            "front_image": first.get("front_image"),
            "back_image": first.get("back_image"),
        })


        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    ALTER TABLE inventory_cards
                    ADD COLUMN IF NOT EXISTS cdp_sku TEXT
                """)
                cur.execute("""
                    SELECT cdp_sku
                    FROM inventory_cards
                    WHERE cdp_sku IS NOT NULL
                """)
        
                existing_cdp_skus = {
                    str(row[0])
                    for row in cur.fetchall()
                }
                
        imported = 0
        skipped = 0
        ready_to_import = []

        for item in normalized_rows:
            cdp_sku = str(item.get("sku") or "").strip()
        
            if not cdp_sku or cdp_sku in existing_cdp_skus:
                skipped += 1
                continue

            ready_to_import.append({
                "sku": cdp_sku,
                "player": item.get("player_name"),
            })
                
    return jsonify({
        "success": True,
        "row_count": len(rows),
        "columns": reader.fieldnames,
        "preview": normalized_rows,
        "existing_cdp_skus": sorted(existing_cdp_skus),
        "ready_to_import": ready_to_import,
    })

@app.route("/inventory", methods=["GET"])
def inventory_cards_dashboard():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    player_name,
                    card_year,
                    product,
                    card_number,
                    first_bowman,
                    prospect_card,
                    parallel,
                    serial_number,
                    serial_numbered_to,
                    autograph,
                    rookie_card,
                    grade_company,
                    grade,
                    quantity,
                    purchase_price,
                    purchase_date,
                    purchase_source,
                    external_card_id,
                    disposition_action,
                    action_priority
                    FROM inventory_cards
                WHERE player_name IS NOT NULL
                ORDER BY created_at DESC
            """)

            rows = cur.fetchall()

            inventory_items = []
            years = set()
            products = set()
            parallels = set()
            grades = set()
            actions = set()
    
    for row in rows:
        (
            inventory_id,
            player_name,
            card_year,
            product,
            card_number,
            first_bowman,
            prospect_card,
            parallel,
            serial_number,
            serial_numbered_to,
            autograph,
            rookie_card,
            grade_company,
            grade,
            quantity,
            purchase_price,
            purchase_date,
            purchase_source,
            cardhedge_id,
            saved_disposition_action,
            saved_action_priority,
            ) = row

        market = get_inventory_market_data(
            cardhedge_id,
            grade_company,
            grade
        )

        trend_data = get_cardhedge_price_trend(
            cardhedge_id,
            grade_company,
            grade
        )

        
        price_trend = trend_data["trend"]
        price_trend_pct = trend_data["trend_pct"]
        price_trend_confidence = trend_data["trend_confidence"]
        history_points = trend_data["history_points"]
        
        market_value = market["market_value"]
        sales_7day = market["sales_7day"]
        sales_30day = market["sales_30day"]
        market_gain = market["market_gain"]
        
        gain_loss = None
        gain_loss_pct = None
        
        if (
            market_value is not None
            and purchase_price is not None
        ):
            gain_loss = (
                market_value - float(purchase_price)
            )
        
            if float(purchase_price) > 0:
                gain_loss_pct = (
                    gain_loss
                    / float(purchase_price)
                    * 100
                )
        
        market_value_display = (
            f"${market_value:,.2f}"
            if market_value is not None
            else "—"
        )
        
        gain_loss_display = "—"
        
        if gain_loss is not None:
            gain_loss_display = (
                f"${gain_loss:+,.2f}"
            )
        
            if gain_loss_pct is not None:
                gain_loss_display += (
                    f" ({gain_loss_pct:+.1f}%)"
                )

       
        disposition_action = saved_disposition_action or "HOLD"
        action_priority = saved_action_priority or 0
        
        disposition_score = None
        disposition_liquidity = "UNKNOWN"
        disposition_reasons = []

  
                
        reasons_html = "".join(
            f"<li>{reason}</li>"
            for reason in disposition_reasons
        )

        trend_display = price_trend
        
        if price_trend_pct is not None:
            trend_display += f" ({price_trend_pct:+.1f}%)"
        
        trend_display += (
            f" · {price_trend_confidence} confidence"
        )
        
        serial_display = ""

        if serial_numbered_to:
            if serial_number is not None:
                serial_display = (
                    f"{serial_number:03d}/{serial_numbered_to}"
                )
            else:
                serial_display = f"/{serial_numbered_to}"

        grade_display = "Raw"

        if grade_company:
            grade_display = (
                f"{grade_company} {grade}"
                if grade is not None
                else grade_company
            )

        price_display = (
            f"${float(purchase_price):,.2f}"
            if purchase_price is not None
            else "—"
        )

        date_display = (
            purchase_date.strftime("%b %d, %Y")
            if purchase_date
            else "—"
        )

        badges = []

        if first_bowman:
            badges.append("1st Bowman")

        if prospect_card:
            badges.append("Prospect")

        if autograph:
            badges.append("Auto")

        if rookie_card:
            badges.append("RC")

        badges_html = " ".join(
            f'<span class="badge">{badge}</span>'
            for badge in badges
        )

        if card_year:
            years.add(str(card_year))

        if product:
            products.add(str(product))
        
        if parallel:
            parallels.add(str(parallel))

        if grade_display:
            grades.add(str(grade_display))
        
        if disposition_action:
            actions.add(str(disposition_action))

        if gain_loss_pct is None:
            gain_loss_class = ""
        elif gain_loss_pct < -10:
            gain_loss_class = "signal-bad"
        elif gain_loss_pct < 0:
            gain_loss_class = "signal-so-so"
        else:
            gain_loss_class = "signal-good"
        
        if price_trend_pct is None:
            trend_class = ""
        elif price_trend_pct < -10:
            trend_class = "signal-bad"
        elif price_trend_pct < 0:
            trend_class = "signal-so-so"
        else:
            trend_class = "signal-good"
            
        inventory_items.append({
            "priority": action_priority,
            "action": disposition_action,
            "trend": price_trend,
            "html": f"""
                
            <div class="inventory-card">
    
                <div class="player">
                    {player_name}
                </div>
    
                <div class="identity">
                    {card_year or ""} {product or ""}
                </div>
    
                <div class="identity">
                    #{card_number or ""}
                </div>
    
                <div class="parallel">
                    {parallel or "Base"}
                    {serial_display}
                </div>
    
                <div class="badges">
                    {badges_html}
                </div>
    
                <div class="details">
                    <div>
                        <span>Grade</span>
                        <strong>{grade_display}</strong>
                    </div>
    
                    <div>
                        <span>Cost</span>
                        <strong>{price_display}</strong>
                    </div>
    
                    <div>
                        <span>Purchased</span>
                        <strong>{date_display}</strong>
                    </div>
    
                    <div>
                        <span>Source</span>
                        <strong>{purchase_source or "—"}</strong>
                    </div>
                </div>
    
                <div class="market-placeholder">
                <div>
                    <span>Market Value</span>
                    <strong>{market_value_display}</strong>
                </div>
            
                <div>
                    <span>Gain / Loss</span>
                    <strong>{gain_loss_display}</strong>
                </div>
            </div>
            
            <div class="market-placeholder">
                <div>
                    <span>7-Day Sales</span>
                    <strong>{sales_7day}</strong>
                </div>
            
                <div>
                    <span>30-Day Sales</span>
                    <strong>{sales_30day}</strong>
                </div>
            </div>
    
            <div class="market-placeholder">
                <div>
                    <span>30-Day Price Trend</span>
                    <strong>{trend_display}</strong>
                </div>
            
                <div>
                    <span>History Points</span>
                    <strong>{history_points}</strong>
                </div>
            </div>
    
                <div class="decision-placeholder">
                <div style="
                    font-size:20px;
                    font-weight:bold;
                    margin-bottom:8px;
                ">
                    {disposition_action}
                </div>
    
        <div style="
            font-size:13px;
            color:#666;
            margin-bottom:8px;
        ">
            Liquidity: {disposition_liquidity}
        </div>
    
        <ul style="
            text-align:left;
            margin:0;
            padding-left:20px;
            font-weight:normal;
        ">
            {reasons_html}
        </ul>
    </div>
    
            </div>
            """,
    
            "compact_html": f"""
            <tr
                data-action="{disposition_action}"
                data-parallel="{parallel or "Base"}"
            >
                <td>{player_name}</td>
                <td>{card_year or ""}</td>
                <td>#{card_number or ""}</td>
                <td>{product or ""}</td>
                <td>{parallel or "Base"} {serial_display}</td>
                <td>{grade_display}</td>
                <td>{quantity or 1}</td>
                <td>{price_display}</td>
                <td>{market_value_display}</td>
                <td class="{gain_loss_class}">
                    {gain_loss_display}
                </td>
                
                <td class="{trend_class}">
                    {trend_display}
                </td>
                <td>{disposition_action}</td>
            </tr>
            """
        })

    years = sorted(years, reverse=True)
    products = sorted(products)
    parallels = sorted(parallels)
    grades = sorted(grades)
    actions = sorted(actions)

    inventory_items.sort(
        key=lambda item: item["priority"],
        reverse=True
    )

    action_items = [
        item
        for item in inventory_items
        if item["priority"] >= 35
    ]
    
    action_queue_html = "".join(
        item["html"]
        for item in action_items
    )

    if not action_queue_html:
        action_queue_html = """
        <div class="empty">
            No cards currently require attention.
        </div>
        """
    
    cards_html = "".join(
        item["compact_html"]
        for item in inventory_items
    )
    if not cards_html:
        cards_html = """
        <div class="empty">
            No inventory cards yet.
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>

    <head>
        <title>Bowman Inventory</title>

        <meta
            name="viewport"
            content="width=device-width, initial-scale=1"
        >

        <style>
            body {{
                margin: 0;
                background: #f3f4f6;
                font-family: Arial, sans-serif;
                color: #111;
            }}

            .app-nav {{
                position: sticky;
                top: 0;
                z-index: 1000;
                display: flex;
                gap: 6px;
                padding: 10px 16px;
                background: white;
                border-bottom: 1px solid #e5e7eb;
                overflow-x: auto;
                white-space: nowrap;
            }}
            
            .app-nav a {{
                display: inline-block;
                padding: 9px 12px;
                color: #374151;
                text-decoration: none;
                font-size: 14px;
                font-weight: bold;
                border-radius: 8px;
            }}
            
            .app-nav a:hover {{
                background: #f3f4f6;
                color: #111;
            }}

            .page {{
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
            }}

            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }}

            h1 {{
                margin: 0;
                font-size: 30px;
            }}

            .scan {{
                background: #2563eb;
                color: white;
                text-decoration: none;
                padding: 12px 16px;
                border-radius: 9px;
                font-weight: bold;
            }}

            .inventory-card {{
                background: white;
                border-radius: 14px;
                padding: 20px;
                margin-bottom: 16px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            .compact-card {{
                padding: 14px 18px;
                margin-bottom: 10px;
            }}
            
            .compact-top {{
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                gap: 15px;
            }}
            
            .compact-card .player {{
                font-size: 19px;
                margin-bottom: 3px;
            }}

           .inventory-table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                font-size: 14px;
                table-layout: fixed;
            }}

            .inventory-table th {{
                text-align: left;
                padding: 10px 8px;
                background: #f3f4f6;
                border-bottom: 2px solid #d1d5db;
                white-space: nowrap;
                position: sticky;
                top: 0;
                z-index: 10;
            }}

            .inventory-filters {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                margin: 18px 0;
            }}
            
            .inventory-filters input,
            .inventory-filters select,
            .inventory-filters button {{
                padding: 8px 10px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background: white;
                font-size: 14px;
            }}
            
            .inventory-filters input {{
                min-width: 190px;
            }}
            
            .inventory-filters button {{
                cursor: pointer;
            }}
            
            .inventory-table td {{
                padding: 8px 6px;
                border-bottom: 1px solid #e5e7eb;
                vertical-align: middle;
                white-space: normal;
                overflow-wrap: anywhere;
            }}
            
            .inventory-table tbody tr:hover {{
                background: #f8fafc;
            }}
            
            .inventory-table td:first-child {{
                font-weight: 700;
            }}
            
            .inventory-table th:nth-child(7),
            .inventory-table th:nth-child(8),
            .inventory-table th:nth-child(9),
            .inventory-table td:nth-child(7),
            .inventory-table td:nth-child(8),
            .inventory-table td:nth-child(9) {{
                text-align: right;
            }}
            
            .compact-identity {{
                font-size: 13px;
                line-height: 1.35;
            }}
            
            .compact-action {{
                font-size: 15px;
                font-weight: bold;
                white-space: nowrap;
            }}
            
            .compact-metrics {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px 18px;
                margin-top: 10px;
                padding-top: 9px;
                border-top: 1px solid #e5e7eb;
                font-size: 13px;
            }}
            
            .compact-metrics span {{
                white-space: nowrap;
            }}
            
            @media (max-width: 600px) {{
                .compact-card {{
                    padding: 12px 14px;
                }}
            
                .compact-metrics {{
                    gap: 6px 12px;
                }}
            }}

            .player {{
                font-size: 24px;
                font-weight: bold;
                margin-bottom: 5px;
            }}

            .identity {{
                font-size: 16px;
                margin-top: 3px;
            }}

            .parallel {{
                font-size: 18px;
                font-weight: bold;
                margin-top: 8px;
            }}

            .badges {{
                margin-top: 10px;
            }}

            .badge {{
                display: inline-block;
                background: #e5e7eb;
                padding: 5px 9px;
                margin-right: 5px;
                border-radius: 7px;
                font-size: 12px;
                font-weight: bold;
            }}

            .details {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px;
                margin-top: 18px;
                border-top: 1px solid #ddd;
                padding-top: 16px;
            }}

            .details div {{
                display: flex;
                flex-direction: column;
            }}

            .details span,
            .market-placeholder span {{
                color: #666;
                font-size: 12px;
            }}

            .details strong {{
                margin-top: 3px;
            }}

            .market-placeholder {{
                margin-top: 18px;
                padding: 14px;
                background: #f9fafb;
                border-radius: 9px;
                display: flex;
                justify-content: space-between;
            }}

            .decision-placeholder {{
                margin-top: 10px;
                padding: 14px;
                background: #f3f4f6;
                border-radius: 9px;
                text-align: center;
                font-weight: bold;
                color: #666;
            }}

            .empty {{
                background: white;
                padding: 30px;
                text-align: center;
                border-radius: 12px;
            }}

            @media (max-width: 600px) {{
                .page {{
                    padding: 14px;
                }}

                h1 {{
                    font-size: 24px;
                }}
            }}
        </style>
    </head>

    <body>

        {NAV_HTML}

        <div class="page">

            <div class="header">
                <h1>Inventory</h1>

                <a class="scan" href="/scan-card">
                    + Scan Card
                </a>
            </div>

            <form
                action="/inventory/import-cdp"
                method="POST"
                enctype="multipart/form-data"
                style="margin:0 0 14px 0;"
            >
                <strong>Import Card Dealer Pro:</strong>
            
                <input
                    type="file"
                    name="cdp_csv"
                    accept=".csv,.zip,text/csv,application/zip"
                    required
                >
            
                <button type="submit">
                    Import
                </button>
            </form>
            
            <div class="inventory-filters">
                <input
                    type="text"
                    id="playerFilter"
                    placeholder="Search player..."
                >
            
                <select id="yearFilter">
                    <option value="">All Years</option>
                </select>
                
                <select id="productFilter">
                    <option value="">All Products</option>
                </select>
                
                <select id="parallelFilter">
                    <option value="">All Parallels</option>
                </select>
                
                <select id="gradeFilter">
                    <option value="">All Grades</option>
                </select>
                
               <select id="actionFilter">
                    <option value="">All Actions</option>
                </select>
            
                <select id="marketFilter">
                    <option value="">All Market Data</option>
                    <option value="has">Has Market Value</option>
                    <option value="missing">Missing Market Value</option>
                </select>
            
                <button type="button" id="clearFilters">Clear</button>
            </div>
            
                       
            <div>
                <table class="inventory-table">
                    <thead>
                        <tr>
                            <th>Player</th>
                            <th>Year</th>
                            <th>Card #</th>
                            <th>Product</th>
                            <th>Parallel</th>
                            <th>Grade</th>
                            <th>Qty</th>
                            <th>Cost</th>
                            <th>Market</th>
                            <th>P/L</th>
                            <th>Trend</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {cards_html}
                    </tbody>
                </table>
            </div>      

        </div>

        <script>
        const table = document.querySelector(".inventory-table");
        
        if (table) {{
            const rows = Array.from(table.querySelectorAll("tbody tr"));
        
            const playerFilter = document.getElementById("playerFilter");
            const yearFilter = document.getElementById("yearFilter");
            const productFilter = document.getElementById("productFilter");
            const parallelFilter = document.getElementById("parallelFilter");
            const gradeFilter = document.getElementById("gradeFilter");
            const actionFilter = document.getElementById("actionFilter");
            const marketFilter = document.getElementById("marketFilter");
            const clearFilters = document.getElementById("clearFilters");
        
            function uniqueValues(columnIndex) {{
                return [...new Set(
                    rows
                        .map(row => row.cells[columnIndex]?.textContent.trim())
                        .filter(Boolean)
                )].sort();
            }}
             function fillSelect(select, values) {{
                values.forEach(value => {{
                    const option = document.createElement("option");
                    option.value = value.toLowerCase();
                    option.textContent = value;
                    select.appendChild(option);
                }});
            }}
            function applyFilters() {{
                const player = playerFilter.value.trim().toLowerCase();
                const year = yearFilter.value.toLowerCase();
                const product = productFilter.value.toLowerCase();
                const parallel = parallelFilter.value.toLowerCase();
                const grade = gradeFilter.value.toLowerCase();
                const action = actionFilter.value.toLowerCase();
                const market = marketFilter.value;
        
                rows.forEach(row => {{
                    const cells = Array.from(row.cells).map(
                        cell => cell.textContent.trim().toLowerCase()
                    );
        
                    const playerMatch = !player || cells[0].includes(player);
                    const yearMatch = !year || cells[1] === year;
                    const productMatch = !product || cells[3] === product;
                    const parallelText =
                        (row.dataset.parallel || "").trim().toLowerCase();
                    
                    const parallelMatch =
                        !parallel || parallelText === parallel;
                    const gradeMatch = !grade || cells[5] === grade;
                    const actionText =
                        (row.dataset.action || "").trim().toLowerCase();
                    
                    const actionMatch =
                        !action || actionText === action;
        
                    const marketText = cells[8] || "";
        
                    const hasMarket =
                        marketText !== "" &&
                        marketText !== "—" &&
                        marketText !== "-";
        
                    const marketMatch =
                        !market ||
                        (market === "has" && hasMarket) ||
                        (market === "missing" && !hasMarket);
        
                    row.style.display =
                        playerMatch &&
                        yearMatch &&
                        productMatch &&
                        parallelMatch &&
                        gradeMatch &&
                        actionMatch &&
                        marketMatch
                            ? ""
                            : "none";
                }});
            }}
            function resetAndFillSelect(select, values) {{
                const firstOption = select.options[0];
            
                select.innerHTML = "";
                select.appendChild(firstOption);
            
                values.forEach(value => {{
                    const option = document.createElement("option");
                    option.value = value.toLowerCase();
                    option.textContent = value;
                    select.appendChild(option);
                }});
            }}
            
            resetAndFillSelect(yearFilter, uniqueValues(1));
            resetAndFillSelect(productFilter, uniqueValues(3));
            
                        
            resetAndFillSelect(gradeFilter, uniqueValues(5));
            resetAndFillSelect(
                actionFilter,
                [...new Set(
                    rows
                        .map(row => row.dataset.action)
                        .filter(Boolean)
                )].sort()
            );
            
            console.log(
                "action options:",
                [...actionFilter.options].map(option => option.textContent)
            );
            playerFilter.addEventListener("input", applyFilters);
        
            [
                yearFilter,
                productFilter,
                parallelFilter,
                gradeFilter,
                actionFilter,
                marketFilter
            ].forEach(select => {{
                select.addEventListener("change", applyFilters);
            }});
        
            clearFilters.addEventListener("click", function () {{
                playerFilter.value = "";
                yearFilter.value = "";
                productFilter.value = "";
                parallelFilter.value = "";
                gradeFilter.value = "";
                actionFilter.value = "";
                marketFilter.value = "";
        
                applyFilters();
            }});
        }}
        </script>
    </body>
    </html>
    """


@app.route("/ebay/inventory-item/<sku>", methods=["GET"])
def ebay_inventory_item_detail(sku):
    token = get_ebay_user_access_token()

    response = requests.get(
        f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=30,
    )

    data = response.json() if response.content else {}

    return jsonify({
        "status_code": response.status_code,
        "inventory_item": data,
    })


@app.route("/ebay/draft-review/<offer_id>", methods=["GET", "POST"])
def ebay_draft_review(offer_id):
    token = get_ebay_user_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    offer_response = requests.get(
        f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}",
        headers=headers,
        timeout=30,
    )

    offer = offer_response.json() if offer_response.content else {}

    sku = offer.get("sku")

    if request.method == "POST":
        print("DRAFT SAVE FORM:", request.form.to_dict())
        
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        starting_bid = request.form.get("starting_bid", "").strip()
        auction_duration = request.form.get("auction_duration", "DAYS_7").strip()
    
        photo_order = json.loads(
            request.form.get("photo_order", "[]") or "[]"
        )

        removed_photos = set(
            json.loads(
                request.form.get("removed_photos", "[]") or "[]"
            )
        )
        new_photo_keys = json.loads(
            request.form.get("new_photo_keys", "[]") or "[]"
        )
    
        uploaded_files = request.files.getlist("listing_photos")
    
        item_response = requests.get(
            f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
            headers=headers,
            timeout=30,
        )
    
        item = item_response.json() if item_response.content else {}
    
        uploaded_url_by_key = {}
    
        for key, photo in zip(new_photo_keys, uploaded_files):
            if not photo or not photo.filename:
                continue
    
            upload_response = requests.post(
                "https://apim.ebay.com/commerce/media/v1_beta/image/create_image_from_file",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                files={
                    "image": (
                        photo.filename,
                        photo.stream,
                    )
                },
                timeout=30,
            )
    
            upload_json = (
                upload_response.json()
                if upload_response.content
                else {}
            )
    
            image_url = upload_json.get("imageUrl")
    
            if image_url:
                uploaded_url_by_key[key] = image_url
    
        final_image_urls = []
    
        for photo in photo_order:
            if photo.get("kind") == "existing":
                existing_url = photo.get("value")
            
                if existing_url and existing_url not in removed_photos:
                    final_image_urls.append(existing_url)
    
            elif photo.get("kind") == "new":
                image_url = uploaded_url_by_key.get(photo.get("value"))
    
                if image_url:
                    final_image_urls.append(image_url)
    
        if not final_image_urls:
            return "At least one listing photo is required.", 400
    
        product = item.get("product", {})
        product["title"] = title
        product["description"] = description
        product["imageUrls"] = final_image_urls
        aspects = product.get("aspects", {})
        aspects["Sport"] = ["Baseball"]
        product["aspects"] = aspects
    
        item["product"] = product

        app.logger.warning("DRAFT SAVE: about to update inventory item")

        app.logger.warning(
            "INVENTORY UPDATE PRODUCT: %s",
            item.get("product", {})
        )
    
        update_item_response = requests.put(
            f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Language": "en-US",
            },
            json=item,
            timeout=30,
        )

        app.logger.warning(
            "INVENTORY UPDATE RESPONSE: status=%s body=%s",
            update_item_response.status_code,
            update_item_response.text
        )
    
        if update_item_response.status_code not in (200, 204):
            return jsonify({
                "success": False,
                "stage": "inventory_item_update",
                "status_code": update_item_response.status_code,
                "response": (
                    update_item_response.json()
                    if update_item_response.content
                    else {}
                ),
            }), 400

        app.logger.warning("DRAFT SAVE: inventory item succeeded, updating offer")
    
        offer_update = dict(offer)

        # Remove read-only fields returned by eBay
        offer_update.pop("offerId", None)
        offer_update.pop("status", None)
        
        # Apply the user's draft edits
        offer_update["listingDescription"] = description
        offer_update["listingDuration"] = auction_duration
        
        offer_update["pricingSummary"] = {
            "auctionStartPrice": {
                "value": starting_bid,
                "currency": "USD",
            }
        }
    
        update_offer_response = requests.put(
            f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Language": "en-US",
            },
            json=offer_update,
            timeout=30,
        )


        app.logger.warning(
            "EBAY OFFER UPDATE: status=%s body=%s",
            update_offer_response.status_code,
            update_offer_response.text
        )
    
        if update_offer_response.status_code not in (200, 204):
            return jsonify({
                "success": False,
                "stage": "offer_update",
                "status_code": update_offer_response.status_code,
                "response": (
                    update_offer_response.json()
                    if update_offer_response.content
                    else {}
                ),
            }), 400

        time.sleep(1)

        return (
            "",
            303,
            {
                "Location": f"/ebay/draft-review/{offer_id}?saved=1"
            }
        )


    inventory_id = None
    
    if sku and sku.startswith("inventory-"):
        inventory_id = int(sku.replace("inventory-", ""))
    
    market_data = {}
    
    if inventory_id:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        market_value,
                        purchase_price,
                        price_trend,
                        trend_pct,
                        trend_confidence,
                        disposition_action,
                        action_priority
                    FROM inventory_cards
                    WHERE id = %s
                """, (inventory_id,))
    
                row = cur.fetchone()
    
                if row:
                    market_data = {
                        "market_value": row[0],
                        "purchase_price": row[1],
                        "price_trend": row[2],
                        "trend_pct": row[3],
                        "trend_confidence": row[4],
                        "disposition_action": row[5],
                        "action_priority": row[6],
                    }

    mv = float(market_data.get("market_value") or 0)

    recommended_start_price = max(0.99, mv * 0.50)
    expected_low = mv * 0.90
    expected_high = mv * 1.10
    minimum_outcome = mv * 0.80

    purchase_price_value = float(
        market_data.get("purchase_price") or 0
    )

    gain_loss = mv - purchase_price_value

    gain_loss_pct = (
        (gain_loss / purchase_price_value) * 100
        if purchase_price_value > 0
        else None
    )


    item_response = requests.get(
        f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
        headers=headers,
        timeout=30,
    )

    item = item_response.json() if item_response.content else {}

    product = item.get("product", {})
    pricing = offer.get("pricingSummary", {})
    auction_price = pricing.get("auctionStartPrice", {})
    policies = offer.get("listingPolicies", {})

    image_urls = product.get("imageUrls", [])
    
    image_html = "".join(
        f"""
        <div
            class="photo-card"
            data-kind="existing"
            data-url="{url}"
            style="
                width:220px;
                padding:10px;
                border:1px solid #ddd;
                border-radius:8px;
                background:#fff;
            "
        >
            <img
                src="{url}"
                style="
                    width:100%;
                    height:280px;
                    object-fit:contain;
                    display:block;
                    margin-bottom:10px;
                "
            >
    
            <div class="photo-status" style="font-weight:bold; margin-bottom:8px;"></div>
    
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
                <button type="button" class="make-primary">Primary</button>
                <button type="button" class="move-left">←</button>
                <button type="button" class="move-right">→</button>
                <button type="button" class="remove-photo">Remove</button>
            </div>
        </div>
        """
        for url in image_urls
    )    
    return f"""
    <html>
    <head>
        <title>Review eBay Draft</title>
    </head>
    
    <body style="font-family:Arial; max-width:1000px; margin:40px auto;">
    
        <h1>Review eBay Draft - EDIT TEST</h1>
    
        <p><strong>Status:</strong> {offer.get("status")}</p>
    
        <form method="POST" enctype="multipart/form-data">
    
            <label><strong>Title</strong></label><br>
            <input
                type="text"
                name="title"
                value="{product.get("title", "")}"
                style="width:100%; padding:10px; margin:8px 0 20px 0;"
            >
    
            

            <h3>Listing Photos</h3>
            
            <div id="photo-manager">
            
                <div
                    id="photo-list"
                    style="
                        display:flex;
                        gap:16px;
                        flex-wrap:wrap;
                        margin-bottom:18px;
                    "
                >
                    {image_html}
                </div>
            
                <div style="
                    border:2px dashed #ccc;
                    border-radius:8px;
                    padding:18px;
                    background:#fafafa;
                    margin-bottom:20px;
                ">
                    <strong>Add Photos</strong>
            
                    <p style="margin:8px 0 12px 0; color:#666;">
                        Add front, back, or detail images. New photos will appear above immediately.
                    </p>
            
                    <input
                        id="listing-photo-input"
                        type="file"
                        name="listing_photos"
                        accept="image/*"
                        multiple
                    >
                </div>
            
                <input
                    type="hidden"
                    id="photo_order"
                    name="photo_order"
                >
            
                <input
                    type="hidden"
                    id="removed_photos"
                    name="removed_photos"
                >
                <input
                    type="hidden"
                    id="new_photo_keys"
                    name="new_photo_keys"
                > 
            </div>
    
            <br>
    
            <label><strong>Description</strong></label><br>
            <textarea
                name="description"
                rows="8"
                style="width:100%; padding:10px; margin:8px 0 20px 0;"
            >{offer.get("listingDescription", "")}</textarea>
    
 <div style="
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:30px;
    align-items:start;
    margin-top:20px;
">

    <div>

        <label><strong>Listing Type</strong></label><br>
        
        <select
            name="listing_format"
            id="listing-format"
            style="padding:10px; margin:8px 0 20px 0;"
        >
            <option
                value="AUCTION"
                {"selected" if offer.get("format") == "AUCTION" else ""}
            >
                Auction
            </option>
        
            <option
                value="FIXED_PRICE"
                {"selected" if offer.get("format") == "FIXED_PRICE" else ""}
            >
                Buy It Now
            </option>
        </select>
        
        <br>
    
        <label><strong>Starting Bid</strong></label><br>

        <input
            type="text"
            name="starting_bid"
            value="{auction_price.get("value", "")}"
            style="width:200px; padding:10px; margin:8px 0 20px 0;"
        >

        <br>

        <label><strong>Auction Duration</strong></label><br>

        <select
            name="auction_duration"
            style="padding:10px; margin:8px 0 20px 0;"
        >
            <option value="DAYS_3" {"selected" if offer.get("listingDuration") == "DAYS_3" else ""}>3 days</option>
            <option value="DAYS_5" {"selected" if offer.get("listingDuration") == "DAYS_5" else ""}>5 days</option>
            <option value="DAYS_7" {"selected" if offer.get("listingDuration") == "DAYS_7" else ""}>7 days</option>
            <option value="DAYS_10" {"selected" if offer.get("listingDuration") == "DAYS_10" else ""}>10 days</option>
        </select>
    </div>

    <div style="
        border:1px solid #ddd;
        border-radius:8px;
        padding:18px;
        background:#fafafa;
    ">
        <h3 style="margin-top:0;">Market Analytics</h3>

        <p>
            <strong>Market Value:</strong>
            ${float(market_data.get("market_value") or 0):,.2f}
        </p>

        <p>
            <strong>Your Cost:</strong>
            ${float(market_data.get("purchase_price") or 0):,.2f}
        </p>

        <p>
            <strong>P/L:</strong>
            ${gain_loss:+,.2f}
            {f'({gain_loss_pct:+.1f}%)' if gain_loss_pct is not None else ""}
        </p>
        
        <p>
            <strong>Recommended Start:</strong>
            ${recommended_start_price:,.2f}
        </p>
        
        <p>
            <strong>Expected Sale Range:</strong>
            ${expected_low:,.2f} - ${expected_high:,.2f}
        </p>
        
        <p>
            <strong>Minimum Outcome:</strong>
            ${minimum_outcome:,.2f}
        </p>

        <p>
            <strong>Trend:</strong>
            {market_data.get("price_trend") or "UNKNOWN"}
            {f'({float(market_data.get("trend_pct")):+.1f}%)' if market_data.get("trend_pct") is not None else ""}
        </p>

        <p>
            <strong>Confidence:</strong>
            {market_data.get("trend_confidence") or "UNKNOWN"}
        </p>

        <p>
            <strong>Recommendation:</strong>
            {market_data.get("disposition_action") or "HOLD"}
        </p>
    </div>

</div>
    
            <h3>Policies</h3>
    
            <p><strong>Shipping:</strong> JackStation Graded Card Shipping</p>
            <p><strong>Payment:</strong> JackStation Payment Policy</p>
            <p><strong>Returns:</strong> No Returns</p>
    
            <hr>
    
            <p><strong>Offer ID:</strong> {offer_id}</p>
            <p><strong>SKU:</strong> {sku}</p>
    
            <button
                type="submit"
                style="padding:12px 20px; font-size:16px;"
            >
                Save Draft Changes
            </button>
    
        </form>


        <script>
        (function() {{
            const manager = document.getElementById("photo-manager");
            const list = document.getElementById("photo-list");
            const fileInput = document.getElementById("listing-photo-input");
            const orderInput = document.getElementById("photo_order");
            const removedInput = document.getElementById("removed_photos");
            const newKeysInput = document.getElementById("new_photo_keys");
        
            if (!manager || !list || !fileInput) return;
        
            let pendingFiles = [];
            const removedUrls = new Set();
        
            function rebuildFileInput() {{
                const transfer = new DataTransfer();
        
                pendingFiles.forEach(item => {{
                    transfer.items.add(item.file);
                }});
        
                fileInput.files = transfer.files;
            }}
        
            function updateState() {{
                const cards = Array.from(
                    list.querySelectorAll(".photo-card")
                );
        
                cards.forEach((card, index) => {{
                    const status = card.querySelector(".photo-status");
                    const primaryButton = card.querySelector(".make-primary");
        
                    if (index === 0) {{
                        status.textContent = "Primary Photo";
                        primaryButton.disabled = true;
                    }} else {{
                        status.textContent = `Photo ${{index + 1}}`;
                        primaryButton.disabled = false;
                    }}
                }});
        
                const order = cards.map(card => {{
                    if (card.dataset.kind === "existing") {{
                        return {{
                            kind: "existing",
                            value: card.dataset.url
                        }};
                    }}
        
                    return {{
                        kind: "new",
                        value: card.dataset.fileKey
                    }};
                }});
        
                orderInput.value = JSON.stringify(order);
                removedInput.value = JSON.stringify(Array.from(removedUrls));
                newKeysInput.value = JSON.stringify(
                    pendingFiles.map(item => item.key)
                );
            }}
        
            function wireCard(card) {{
                const primary = card.querySelector(".make-primary");
                const left = card.querySelector(".move-left");
                const right = card.querySelector(".move-right");
                const remove = card.querySelector(".remove-photo");
        
                primary.addEventListener("click", () => {{
                    list.insertBefore(card, list.firstChild);
                    updateState();
                }});
        
                left.addEventListener("click", () => {{
                    const previous = card.previousElementSibling;
        
                    if (previous) {{
                        list.insertBefore(card, previous);
                        updateState();
                    }}
                }});
        
                right.addEventListener("click", () => {{
                    const next = card.nextElementSibling;
        
                    if (next) {{
                        list.insertBefore(next, card);
                        updateState();
                    }}
                }});
        
                remove.addEventListener("click", () => {{
                    if (card.dataset.kind === "existing") {{
                        removedUrls.add(card.dataset.url);
                    }} else {{
                        pendingFiles = pendingFiles.filter(
                            item => item.key !== card.dataset.fileKey
                        );
        
                        rebuildFileInput();
                    }}
        
                    card.remove();

                    const remainingCards = Array.from(
                        list.querySelectorAll(".photo-card")
                    );
                    
                    if (remainingCards.length > 0) {{
                        list.insertBefore(remainingCards[0], list.firstChild);
                    }}
                    
                    updateState();
                }});
            }}
        
            Array.from(
                list.querySelectorAll(".photo-card")
            ).forEach(wireCard);
        
            fileInput.addEventListener("change", () => {{
                const selectedFiles = Array.from(fileInput.files);
        
                selectedFiles.forEach(file => {{
                    const key =
                        `${{Date.now()}}-${{Math.random()}}-${{file.name}}`;
        
                    pendingFiles.push({{
                        key: key,
                        file: file
                    }});
        
                    const previewUrl = URL.createObjectURL(file);
        
                    const card = document.createElement("div");
                    card.className = "photo-card";
                    card.dataset.kind = "new";
                    card.dataset.fileKey = key;
        
                    card.style.width = "220px";
                    card.style.padding = "10px";
                    card.style.border = "1px solid #ddd";
                    card.style.borderRadius = "8px";
                    card.style.background = "#fff";
        
                    card.innerHTML = `
                        <img
                            src="${{previewUrl}}"
                            style="
                                width:100%;
                                height:280px;
                                object-fit:contain;
                                display:block;
                                margin-bottom:10px;
                            "
                        >
        
                        <div
                            class="photo-status"
                            style="font-weight:bold; margin-bottom:8px;"
                        ></div>
        
                        <div style="display:flex; gap:6px; flex-wrap:wrap;">
                            <button type="button" class="make-primary">Primary</button>
                            <button type="button" class="move-left">←</button>
                            <button type="button" class="move-right">→</button>
                            <button type="button" class="remove-photo">Remove</button>
                        </div>
                    `;
        
                    list.appendChild(card);
                    wireCard(card);
                }});
        
                rebuildFileInput();
                updateState();
            }});
        
            updateState();
        }})();
        </script>
    
    </body>
    </html>
    """
    
 
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

        .app-nav {{
            position: sticky;
            top: 0;
            z-index: 1000;
            display: flex;
            gap: 6px;
            padding: 10px 16px;
            background: white;
            border-bottom: 1px solid #e5e7eb;
            overflow-x: auto;
            white-space: nowrap;
        }}
        
        .app-nav a {{
            display: inline-block;
            padding: 9px 12px;
            color: #374151;
            text-decoration: none;
            font-size: 14px;
            font-weight: bold;
            border-radius: 8px;
        }}
        
        .app-nav a:hover {{
            background: #f3f4f6;
            color: #111;
        }}
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
    {NAV_HTML}
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

    html = html.replace("{NAV_HTML}", NAV_HTML)
    return html

   
