import os, uuid, time, json
from decimal import Decimal

from flask import Flask, request, jsonify
from flask_cors import CORS
import boto3

# -------------------------------------------------------
# App setup
# -------------------------------------------------------
# Serve static files from: backend/static/
# and make them available at root path /
app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# -------------------------------------------------------
# AWS clients/resources (force region to avoid surprises)
# -------------------------------------------------------
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
sqs = boto3.client("sqs", region_name=AWS_REGION)

# -------------------------------------------------------
# Config (match your actual table names)
# Your AWS CLI shows: tables = ["Orders", "products"]
# -------------------------------------------------------
PRODUCTS_TABLE = os.environ.get("PRODUCTS_TABLE", "products")  # ✅ lowercase
ORDERS_TABLE = os.environ.get("ORDERS_TABLE", "Orders")        # ✅ capital O
QUEUE_URL = os.environ.get("QUEUE_URL", "")
ASSETS_BUCKET = os.environ.get("ASSETS_BUCKET", "")  # optional

products_tbl = dynamodb.Table(PRODUCTS_TABLE)
orders_tbl = dynamodb.Table(ORDERS_TABLE)

# Helpful logs in Cloud9 terminal
print("AWS_REGION =", AWS_REGION)
print("PRODUCTS_TABLE =", PRODUCTS_TABLE)
print("ORDERS_TABLE =", ORDERS_TABLE)
print("QUEUE_URL =", QUEUE_URL)

# ✅ Convert Decimal -> float so jsonify works
def to_json_safe(obj):
    if isinstance(obj, list):
        return [to_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return float(obj)
    return obj

# -------------------------------------------------------
# Home page (serves backend/static/index.html)
# -------------------------------------------------------
@app.get("/")
def home():
    return app.send_static_file("index.html")

# -------------------------------------------------------
# Health check
# -------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({"status": "ok"})

# -------------------------------------------------------
# PRODUCTS CRUD
# -------------------------------------------------------
@app.get("/products")
def list_products():
    resp = products_tbl.scan()
    return jsonify(to_json_safe(resp.get("Items", [])))

@app.get("/products/<product_id>")
def get_product(product_id):
    resp = products_tbl.get_item(Key={"productId": product_id})
    item = resp.get("Item")
    if not item:
        return jsonify({"message": "Product not found"}), 404
    return jsonify(to_json_safe(item))

@app.post("/products")
def create_product():
    data = request.json or {}
    required = ["name", "category", "price"]
    for r in required:
        if r not in data:
            return jsonify({"message": f"Missing field: {r}"}), 400

    product_id = str(uuid.uuid4())
    item = {
        "productId": product_id,
        "name": data["name"],
        "category": data["category"],
        "price": Decimal(str(data["price"])),  # DynamoDB numeric
        "sizes": data.get("sizes", []),
        "stock": int(data.get("stock", 0)),
        "imageUrl": data.get("imageUrl", ""),
        "createdAt": int(time.time())
    }

    products_tbl.put_item(Item=item)
    return jsonify(to_json_safe(item)), 201

@app.put("/products/<product_id>")
def update_product(product_id):
    data = request.json or {}

    resp = products_tbl.get_item(Key={"productId": product_id})
    item = resp.get("Item")
    if not item:
        return jsonify({"message": "Product not found"}), 404

    for k in ["name", "category", "price", "sizes", "stock", "imageUrl"]:
        if k in data:
            if k == "price":
                item[k] = Decimal(str(data[k]))
            elif k == "stock":
                item[k] = int(data[k])
            else:
                item[k] = data[k]

    products_tbl.put_item(Item=item)
    return jsonify(to_json_safe(item))

@app.delete("/products/<product_id>")
def delete_product(product_id):
    products_tbl.delete_item(Key={"productId": product_id})
    return jsonify({"message": "Deleted"}), 200

# -------------------------------------------------------
# ORDERS
# -------------------------------------------------------
@app.post("/orders")
def create_order():
    data = request.json or {}
    items = data.get("items", [])
    customer_email = data.get("email", "")

    # Optional payment fields from frontend
    payment_status = data.get("paymentStatus", "")
    payment_ref = data.get("paymentRef", "")

    if not items:
        return jsonify({"message": "items required"}), 400

    order_id = str(uuid.uuid4())
    order = {
        "orderId": order_id,
        "status": "PENDING",
        "items": items,
        "email": customer_email,
        "paymentStatus": payment_status,
        "paymentRef": payment_ref,
        "createdAt": int(time.time())
    }

    orders_tbl.put_item(Item=order)

    # Push job to SQS (worker will confirm later)
    if QUEUE_URL:
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps({"orderId": order_id})
        )

    # ✅ return the SAME status you stored
    return jsonify({"orderId": order_id, "status": "PENDING"}), 201

@app.get("/orders/<order_id>")
def get_order(order_id):
    resp = orders_tbl.get_item(Key={"orderId": order_id})
    item = resp.get("Item")
    if not item:
        return jsonify({"message": "Order not found"}), 404
    return jsonify(to_json_safe(item)), 200

# -------------------------------------------------------
# Optional: nice error if route not found
# -------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return jsonify({"message": "Route not found"}), 404

# -------------------------------------------------------
# Local run
# -------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=True)