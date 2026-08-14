import os
import hashlib
from flask import Flask, request, jsonify

app = Flask(__name__)

VERIFICATION_TOKEN = os.environ.get("EBAY_VERIFICATION_TOKEN", "")
ENDPOINT_URL = os.environ.get("EBAY_ENDPOINT_URL", "")


@app.route("/", methods=["GET"])
def home():
return "eBay notification endpoint is running", 200


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

# eBay account-deletion notification received.
# We intentionally do not log or persist the notification payload here.
return "", 204
