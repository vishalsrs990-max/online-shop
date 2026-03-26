import os
import uuid
import time
import json
from decimal import Decimal
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError

from flask import Flask, request, jsonify
from flask_cors import CORS
import boto3
from botocore.exceptions import ClientError

application = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(application)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1").strip()
PRODUCTS_TABLE = os.environ.get("PRODUCTS_TABLE", "products").strip()
ORDERS_TABLE = os.environ.get("ORDERS_TABLE", "Orders").strip()
QUEUE_URL = os.environ.get("QUEUE_URL", "").strip()
QUEUE_NAME = os.environ.get("QUEUE_NAME", "").strip()
ASSETS_BUCKET = os.environ.get("ASSETS_BUCKET", "").strip()

# NEW: SNS topic ARN for email notifications
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "").strip()

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
sqs = boto3.client("sqs", region_name=AWS_REGION)
sns = boto3.client("sns", region_name=AWS_REGION)   # NEW

products_tbl = dynamodb.Table(PRODUCTS_TABLE)
orders_tbl = dynamodb.Table(ORDERS_TABLE)

print("AWS_REGION =", AWS_REGION)
print("PRODUCTS_TABLE =", PRODUCTS_TABLE)
print("ORDERS_TABLE =", ORDERS_TABLE)
print("QUEUE_URL =", QUEUE_URL if QUEUE_URL else "(empty)")
print("QUEUE_NAME =", QUEUE_NAME if QUEUE_NAME else "(empty)")
print("SNS_TOPIC_ARN =", SNS_TOPIC_ARN if SNS_TOPIC_ARN else "(empty)")


def to_json_safe(obj):
    if isinstance(obj, list):
        return [to_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def is_valid_sqs_queue_url(url: str) -> bool:
    if not url:
        return False
    parts = url.split("/")
    return (
        url.startswith("https://sqs.")
        and ".amazonaws.com/" in url
        and len(parts) >= 5
        and parts[3] != ""
        and parts[4] != ""
    )


def is_valid_sns_topic_arn(arn: str) -> bool:
    return arn.startswith("arn:aws:sns:") and len(arn.split(":")) >= 6


def resolve_queue_url():
    global QUEUE_URL

    if is_valid_sqs_queue_url(QUEUE_URL):
        return QUEUE_URL

    if QUEUE_URL and not is_valid_sqs_queue_url(QUEUE_URL):
        print("⚠️ QUEUE_URL looks invalid:", QUEUE_URL)

    if QUEUE_NAME:
        try:
            resp = sqs.get_queue_url(QueueName=QUEUE_NAME)
            QUEUE_URL = resp["QueueUrl"]
            print("✅ Resolved QUEUE_URL from QUEUE_NAME:", QUEUE_URL)
            return QUEUE_URL
        except Exception as e:
            print("❌ Failed to resolve QUEUE_NAME to QueueUrl:", str(e))
            return ""

    return ""


def send_order_to_sqs(order):
    queue_url = resolve_queue_url()

    if not queue_url:
        print("⚠️ No valid QUEUE_URL available → skipping SQS send")
        return False

    try:
        message_body = {
            "orderId": order["orderId"],
            "items": order.get("items", []),
            "email": order.get("email", ""),
            "paymentStatus": order.get("paymentStatus", ""),
            "paymentRef": order.get("paymentRef", ""),
            "createdAt": order.get("createdAt")
        }

        params = {
            "QueueUrl": queue_url,
            "MessageBody": json.dumps(message_body)
        }

        if queue_url.endswith(".fifo"):
            params["MessageGroupId"] = "orders"
            params["MessageDeduplicationId"] = order["orderId"]

        resp = sqs.send_message(**params)
        print("✅ SQS message sent:", resp.get("MessageId"))
        return True

    except ClientError as e:
        print("❌ SQS send failed:", e.response["Error"]["Message"])
        return False
    except Exception as e:
        print("❌ SQS send failed:", str(e))
        return False


# NEW: SNS email notification helper
def send_order_email_notification(order):
    if not SNS_TOPIC_ARN:
        print("⚠️ SNS_TOPIC_ARN not configured → skipping SNS notification")
        return False

    if not is_valid_sns_topic_arn(SNS_TOPIC_ARN):
        print("⚠️ SNS_TOPIC_ARN looks invalid:", SNS_TOPIC_ARN)
        return False

    try:
        message = {
            "event": "ORDER_CREATED",
            "orderId": order["orderId"],
            "status": order.get("status", ""),
            "email": order.get("email", ""),
            "paymentStatus": order.get("paymentStatus", ""),
            "paymentRef": order.get("paymentRef", ""),
            "items": order.get("items", []),
            "createdAt": order.get("createdAt", 0)
        }

        resp = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"New Order Created - {order['orderId'][:8]}",
            Message=json.dumps(message, indent=2),
            MessageAttributes={
                "eventType": {
                    "DataType": "String",
                    "StringValue": "ORDER_CREATED"
                }
            }
        )

        print("✅ SNS notification sent:", resp.get("MessageId"))
        return True

    except ClientError as e:
        print("❌ SNS publish failed:", e.response["Error"]["Message"])
        return False
    except Exception as e:
        print("❌ SNS publish failed:", str(e))
        return False


@application.get("/")
def home():
    return application.send_static_file("index.html")


@application.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "region": AWS_REGION,
        "productsTable": PRODUCTS_TABLE,
        "ordersTable": ORDERS_TABLE,
        "queueConfigured": bool(resolve_queue_url()),
        "snsConfigured": bool(is_valid_sns_topic_arn(SNS_TOPIC_ARN))
    })


# -------------------------------
# Public API: Open-Meteo Weather
# -------------------------------
@application.get("/weather")
def weather():
    """
    Example:
    /weather
    /weather?latitude=53.3498&longitude=-6.2603
    """

    latitude = request.args.get("latitude", "53.3498").strip()   # Dublin
    longitude = request.args.get("longitude", "-6.2603").strip() # Dublin

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,wind_speed_10m,weather_code",
        "timezone": "auto"
    }

    url = f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}"

    try:
        with urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return jsonify(data), 200
    except HTTPError as e:
        return jsonify({"message": f"Weather API HTTP error: {e.code}"}), 502
    except URLError as e:
        return jsonify({"message": f"Weather API URL error: {e.reason}"}), 502
    except Exception as e:
        return jsonify({"message": f"Weather API failed: {str(e)}"}), 500


@application.get("/products")
def list_products():
    resp = products_tbl.scan()
    return jsonify(to_json_safe(resp.get("Items", [])))


@application.get("/products/<product_id>")
def get_product(product_id):
    resp = products_tbl.get_item(Key={"productId": product_id})
    item = resp.get("Item")
    if not item:
        return jsonify({"message": "Product not found"}), 404
    return jsonify(to_json_safe(item))


@application.post("/products")
def create_product():
    data = request.get_json(silent=True) or {}

    required = ["name", "category", "price"]
    for r in required:
        if r not in data:
            return jsonify({"message": f"Missing field: {r}"}), 400

    product_id = str(uuid.uuid4())
    item = {
        "productId": product_id,
        "name": data["name"],
        "category": data["category"],
        "price": Decimal(str(data["price"])),
        "sizes": data.get("sizes", []),
        "stock": int(data.get("stock", 0)),
        "imageUrl": data.get("imageUrl", ""),
        "createdAt": int(time.time())
    }

    products_tbl.put_item(Item=item)
    return jsonify(to_json_safe(item)), 201


@application.put("/products/<product_id>")
def update_product(product_id):
    data = request.get_json(silent=True) or {}

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


@application.delete("/products/<product_id>")
def delete_product(product_id):
    products_tbl.delete_item(Key={"productId": product_id})
    return jsonify({"message": "Deleted"}), 200


@application.post("/orders")
def create_order():
    data = request.get_json(silent=True) or {}

    items = data.get("items", [])
    customer_email = data.get("email", "")
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

    sqs_sent = send_order_to_sqs(order)

    # NEW: publish order details to SNS topic for email notification
    sns_sent = send_order_email_notification(order)
    print("SNS notification status:", sns_sent)

    # Keeping your response format same so existing frontend will not break
    return jsonify({
        "orderId": order_id,
        "status": "PENDING",
        "sqsSent": sqs_sent
    }), 201


@application.get("/orders/<order_id>")
def get_order(order_id):
    resp = orders_tbl.get_item(Key={"orderId": order_id})
    item = resp.get("Item")
    if not item:
        return jsonify({"message": "Order not found"}), 404
    return jsonify(to_json_safe(item)), 200


@application.errorhandler(404)
def not_found(e):
    return jsonify({"message": "Route not found"}), 404


if __name__ == "__main__":
    application.run(
        host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        debug=os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    )